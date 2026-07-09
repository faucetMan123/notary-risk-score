"""
Notary Risk Score v1 — Politics & War counterparty risk ratings.

Pulls every nation from the PnW GraphQL API, computes a 0-1000 risk score
from public data, and saves:
  - snapshots/snapshot_YYYY-MM-DD.json  (raw data, needed for backtesting)
  - scores_YYYY-MM-DD.csv               (scored output)

Usage:
  export PNW_API_KEY=your_key_here     (or set in a .env-style shell)
  python3 notary_score.py

IMPORTANT: This is a *risk* score built from public data (activity, tenure,
stability, capacity, war exposure). It is NOT a credit score — it contains
no repayment history. Do not market it as one.

Weights below are PRIORS based on domain reasoning. They must be calibrated
via backtest.py before you publish anything. See README.md.
"""

import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://api.politicsandwar.com/graphql"
PAGE_SIZE = 500  # max nations per page

# ── The GraphQL query ─────────────────────────────────────────────────────
# If the API rejects a field name, check the exact name in the Playground
# (https://api.politicsandwar.com/graphql?api_key=KEY -> Docs tab) and fix
# it here. Field names are the single most likely thing to need a tweak.
NATIONS_QUERY = """
query($page: Int!) {
  nations(first: %d, page: $page, vmode: false) {
    paginatorInfo { hasMorePages currentPage }
    data {
      id
      nation_name
      leader_name
      date
      last_active
      color
      alliance_id
      alliance_position
      alliance { id name rank score }
      num_cities
      score
      soldiers
      tanks
      aircraft
      ships
      beige_turns
      vacation_mode_turns
      wars_won
      wars_lost
      defensive_wars_count
      offensive_wars_count
    }
  }
}
""" % PAGE_SIZE


def fetch_all_nations(api_key: str) -> list[dict]:
    nations, page = [], 1
    session = requests.Session()
    while True:
        resp = session.post(
            f"{API_URL}?api_key={api_key}",
            json={"query": NATIONS_QUERY, "variables": {"page": page}},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "errors" in payload:
            sys.exit(f"API error (fix the field name it names, see query comment):\n"
                     f"{json.dumps(payload['errors'], indent=2)}")
        block = payload["data"]["nations"]
        nations.extend(block["data"])
        print(f"  page {page}: {len(nations)} nations so far")
        if not block["paginatorInfo"]["hasMorePages"]:
            break
        page += 1
        time.sleep(0.5)  # stay well inside rate limits
    return nations


# ── Scoring model v1 ──────────────────────────────────────────────────────
# Four pillars, each scored 0-100, combined by weight into a 0-1000 score.
# Weights are priors — calibrate with backtest.py before publishing.

WEIGHTS = {
    "activity":  0.40,  # inactivity is the #1 way money vanishes
    "tenure":    0.20,  # nation age + alliance standing
    "stability": 0.15,  # quality/stability of their alliance
    "capacity":  0.15,  # economic base (cities, score)
    "exposure":  0.10,  # current + historical war losses
}

DAY = 86400.0


def _parse_ts(s: str) -> float:
    """PnW timestamps look like '2026-07-09 14:03:22' or ISO8601."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    return 0.0


def score_activity(n: dict, now: float) -> float:
    """Exponential decay on days since last login. Gray color = hard penalty."""
    days_idle = max(0.0, (now - _parse_ts(n.get("last_active") or "")) / DAY)
    # 100 at 0 days idle, ~61 at 3 days, ~37 at 6 days, ~5 at 18 days
    s = 100.0 * math.exp(-days_idle / 6.0)
    if (n.get("color") or "").lower() == "gray":
        s *= 0.3  # game itself flags them inactive
    if int(n.get("vacation_mode_turns") or 0) > 0:
        s = min(s, 10.0)  # unreachable = uncollectable
    return s


def score_tenure(n: dict, now: float) -> float:
    """Nation age (log curve) + full alliance membership vs applicant/none."""
    age_days = max(0.0, (now - _parse_ts(n.get("date") or "")) / DAY)
    # 0 days -> 0, 30d -> ~49, 180d -> ~75, 2y -> ~95, caps at 100
    age_component = min(100.0, 28.0 * math.log1p(age_days / 30.0) + (
        21.0 if age_days >= 30 else age_days * 0.7))
    pos = (n.get("alliance_position") or "").upper()
    if pos in ("MEMBER", "OFFICER", "HEIR", "LEADER"):
        member_component = 100.0
    elif pos == "APPLICANT":
        member_component = 40.0
    else:  # NOALLIANCE / none
        member_component = 15.0
    return 0.55 * age_component + 0.45 * member_component


def score_stability(n: dict) -> float:
    """Is their alliance a real institution or a rank-400 ghost town?"""
    a = n.get("alliance")
    if not a:
        return 10.0
    rank = int(a.get("rank") or 999)
    # rank 1 -> ~100, rank 25 -> ~78, rank 50 -> ~65, rank 150 -> ~40, floor 15
    return max(15.0, 100.0 - 22.0 * math.log1p(rank / 8.0))


def score_capacity(n: dict) -> float:
    """Economic base: city count (log) + score as a rough size proxy."""
    cities = int(n.get("num_cities") or 0)
    city_component = min(100.0, 33.0 * math.log1p(cities))     # 3c->46, 10c->79, 20c->100
    ns = float(n.get("score") or 0.0)
    score_component = min(100.0, 20.0 * math.log1p(ns / 100.0))
    return 0.7 * city_component + 0.3 * score_component


def score_exposure(n: dict) -> float:
    """Current defensive wars are immediate risk; lifetime loss ratio is habit."""
    s = 100.0
    s -= 22.0 * min(3, int(n.get("defensive_wars_count") or 0))  # being attacked NOW
    if int(n.get("beige_turns") or 0) > 0:
        s -= 25.0                                                # just lost a war
    won, lost = int(n.get("wars_won") or 0), int(n.get("wars_lost") or 0)
    total = won + lost
    if total >= 5:
        loss_rate = lost / total
        s -= 30.0 * max(0.0, loss_rate - 0.5) * 2               # penalise >50% loss rate
    return max(0.0, s)


def band(score: float) -> str:
    """Coarse public bands. Precise scores stay private (political safety)."""
    if score >= 800: return "A"
    if score >= 650: return "B"
    if score >= 500: return "C"
    if score >= 350: return "D"
    return "E"


def compute(n: dict, now: float) -> dict:
    pillars = {
        "activity":  score_activity(n, now),
        "tenure":    score_tenure(n, now),
        "stability": score_stability(n),
        "capacity":  score_capacity(n),
        "exposure":  score_exposure(n),
    }
    total = 10.0 * sum(WEIGHTS[k] * v for k, v in pillars.items())

    # Hard caps: acute conditions that weighted averaging must not paper
    # over. A rich, old, well-allied nation that is unreachable or being
    # dismantled RIGHT NOW is still a bad counterparty TODAY.
    flags = []
    if int(n.get("vacation_mode_turns") or 0) > 0:
        total = min(total, 250.0); flags.append("VACATION_MODE")
    days_idle = max(0.0, (now - _parse_ts(n.get("last_active") or "")) / DAY)
    if days_idle >= 14:
        total = min(total, 350.0); flags.append("INACTIVE_14D")
    if int(n.get("beige_turns") or 0) > 0 or int(n.get("defensive_wars_count") or 0) >= 2:
        total = min(total, 500.0); flags.append("UNDER_ATTACK")

    return {
        "id": n["id"],
        "nation": n.get("nation_name", ""),
        "leader": n.get("leader_name", ""),
        "alliance": (n.get("alliance") or {}).get("name", "None") if n.get("alliance") else "None",
        "cities": n.get("num_cities", 0),
        **{f"p_{k}": round(v, 1) for k, v in pillars.items()},
        "score": round(total),
        "band": band(total),
        "flags": "|".join(flags),
    }


def main() -> None:
    api_key = os.environ.get("PNW_API_KEY")
    if not api_key:
        sys.exit("Set your key first:  export PNW_API_KEY=xxxx  (find it in-game: Account -> API Key)")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = time.time()

    print("Fetching all nations (takes ~1-2 min)...")
    nations = fetch_all_nations(api_key)
    print(f"Fetched {len(nations)} nations.")

    # Raw snapshot — this is your backtesting gold, never skip it
    snap_dir = Path("snapshots"); snap_dir.mkdir(exist_ok=True)
    snap_path = snap_dir / f"snapshot_{today}.json"
    snap_path.write_text(json.dumps({"taken_at": now, "nations": nations}))
    print(f"Snapshot saved: {snap_path}")

    rows = [compute(n, now) for n in nations]
    rows.sort(key=lambda r: -r["score"])

    csv_path = f"scores_{today}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"Scores saved: {csv_path}")

    dist = {}
    for r in rows:
        dist[r["band"]] = dist.get(r["band"], 0) + 1
    print("\nBand distribution:")
    for b in "ABCDE":
        n_count = dist.get(b, 0)
        print(f"  {b}: {n_count:6d}  ({100*n_count/len(rows):.1f}%)")


if __name__ == "__main__":
    main()
