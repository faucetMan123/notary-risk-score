"""
Backtest for Notary Risk Score v1.

Takes an OLD snapshot (>= 30 days old ideally) and the CURRENT state of the
game, and answers: did nations we rated badly actually go bad?

"Went bad" (proxy for uncollectable counterparty) means, at evaluation time:
  - nation no longer exists (deleted), OR
  - inactive 14+ days, OR
  - sitting on gray color, OR
  - in vacation mode

Usage:
  export PNW_API_KEY=xxxx
  python3 backtest.py snapshots/snapshot_2026-07-09.json

Output: bad-outcome rate per rating band. A working model shows a clean
monotonic gradient (E band much worse than A band). Also prints AUC-style
separation so you have one number to quote.
"""

import json
import sys
import time
from datetime import datetime, timezone

# Reuse the fetch + scoring logic from the main script
from notary_score import fetch_all_nations, compute, _parse_ts, DAY
import os


def went_bad(nation_now: dict | None, now: float) -> bool:
    if nation_now is None:
        return True  # deleted / vanished
    if int(nation_now.get("vacation_mode_turns") or 0) > 0:
        return True
    if (nation_now.get("color") or "").lower() == "gray":
        return True
    idle_days = (now - _parse_ts(nation_now.get("last_active") or "")) / DAY
    return idle_days >= 14


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 backtest.py snapshots/snapshot_YYYY-MM-DD.json")

    api_key = os.environ.get("PNW_API_KEY")
    if not api_key:
        sys.exit("Set your key first:  export PNW_API_KEY=xxxx")

    snap = json.loads(open(sys.argv[1]).read())
    taken_at = snap["taken_at"]
    age_days = (time.time() - taken_at) / DAY
    print(f"Snapshot age: {age_days:.0f} days "
          f"({'OK' if age_days >= 21 else 'WARNING: too fresh, results weak'})")

    # Score every nation AS OF the snapshot
    old_scores = {n["id"]: compute(n, taken_at) for n in snap["nations"]}

    print("Fetching current state...")
    current = {n["id"]: n for n in fetch_all_nations(api_key)}
    now = time.time()

    # Evaluate outcomes per band
    bands: dict[str, list[bool]] = {}
    for nid, scored in old_scores.items():
        outcome = went_bad(current.get(nid), now)
        bands.setdefault(scored["band"], []).append(outcome)

    print(f"\n{'Band':<6}{'N':>8}{'Went bad':>10}{'Rate':>8}")
    rates = {}
    for b in "ABCDE":
        outcomes = bands.get(b, [])
        if not outcomes:
            continue
        rate = sum(outcomes) / len(outcomes)
        rates[b] = rate
        print(f"{b:<6}{len(outcomes):>8}{sum(outcomes):>10}{rate:>7.1%}")

    # One-number separation: probability a random bad nation scored lower
    # than a random good one (concordance / AUC).
    pairs_checked = pairs_concordant = 0
    goods = [(s["score"]) for nid, s in old_scores.items() if not went_bad(current.get(nid), now)]
    bads = [(s["score"]) for nid, s in old_scores.items() if went_bad(current.get(nid), now)]
    import random
    random.seed(42)
    for _ in range(min(200_000, len(goods) * len(bads))):
        g, b_ = random.choice(goods), random.choice(bads)
        pairs_checked += 1
        if g > b_:
            pairs_concordant += 1
        elif g == b_:
            pairs_concordant += 0.5
    if pairs_checked:
        auc = pairs_concordant / pairs_checked
        print(f"\nSeparation (AUC): {auc:.3f}")
        print("  0.50 = useless coin-flip | 0.70 = decent | 0.80+ = publishable")

    if rates and ("A" in rates and "E" in rates) and rates["A"] < rates["E"]:
        print("\nGradient direction: CORRECT (worse bands went bad more often).")
    else:
        print("\nGradient direction: WRONG or incomplete — recalibrate weights "
              "before publishing anything.")


if __name__ == "__main__":
    main()
