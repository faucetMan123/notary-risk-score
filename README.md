# Notary Risk Score v1

Counterparty risk ratings for every nation in Politics & War, built from
public API data. **This is a risk score, not a credit score** — it contains
no repayment history. Say so every time you publish it; overclaiming is how
the community kills it.

## The model

Five pillars, each 0–100, weighted into a 0–1000 score:

| Pillar    | Weight | What it measures | Why it predicts default |
|-----------|--------|------------------|-------------------------|
| Activity  | 40%    | Days since login (exp. decay), gray color, VM | Going inactive is the #1 way money vanishes |
| Tenure    | 20%    | Nation age (log), full member vs applicant vs none | Old, embedded nations have sunk costs; drifters don't |
| Stability | 15%    | Their alliance's rank | A rank-5 alliance enforces norms and repays for members; a rank-200 one can't |
| Capacity  | 15%    | City count (log) + nation score | Can they even generate the money to repay? |
| Exposure  | 10%    | Current defensive wars, beige, lifetime loss rate | Being farmed = income is being stolen mid-loan |

**Hard caps** override the weighted total for acute conditions:
vacation mode → max 250, inactive 14+ days → max 350, in beige or 2+
defensive wars → max 500. Rationale: averaging lets a rich old nation
score B while being actively dismantled; a lender funding that gets burned.

**Bands** (publish these, keep precise scores semi-private for political
safety): A ≥800, B ≥650, C ≥500, D ≥350, E <350.

## Honest limitations (v1)

- Weights are priors from domain reasoning, NOT fitted. Do not publish
  before backtesting shows a monotonic gradient and AUC ≥ ~0.70.
- No income model: capacity uses cities/score as a proxy, not build quality.
- No repayment data: that layer only accumulates through notarised deals.
- Gameable: login-farming inflates Activity. Acceptable at launch; iterate.
- Alliance rank is a crude stability proxy; a rank-15 alliance mid-civil-war
  scores the same as a stable one.

## Files

- `notary_score.py` — pulls all ~13.5k nations (paginated GraphQL), scores
  them, writes `scores_DATE.csv` and `snapshots/snapshot_DATE.json`.
- `backtest.py` — scores an old snapshot with today's model, checks what
  actually happened to those nations since, reports bad-outcome rate per
  band + AUC.

## Setup

```
pip install requests
export PNW_API_KEY=your_key      # in-game: Account -> API Key
python3 notary_score.py
```

If the API rejects a field name (schema drift), open the Playground at
https://api.politicsandwar.com/graphql?api_key=YOUR_KEY, check the nations
schema in the Docs tab, and fix the name at the top of `notary_score.py`.

## The launch protocol — do not skip steps

1. **Today:** run `notary_score.py`. Fix any field names. You now have
   snapshot #1. Schedule it daily (same GitHub Actions pattern as your
   Discord poster — snapshots are the asset, each one is irreplaceable).
2. **Days 1–30:** collect snapshots. Build nothing public. Optionally show
   the CSV privately to 1–2 experienced players for face-validity ("does
   your gut agree with these bands?").
3. **Day ~30:** run `backtest.py snapshots/snapshot_<day1>.json`.
   - Gradient wrong or AUC < 0.65 → adjust weights/caps, re-test on a
     DIFFERENT snapshot (never tune and validate on the same one).
   - AUC ≥ 0.70 with clean gradient → you have a defensible claim:
     "E-band nations went bad at X%, A-band at Y%, measured over 30 days."
4. **Launch:** publish bands only, with the methodology summary and the
   backtest numbers. The number you can defend is the product.

## Compliance

Read-only API access, no in-game automation — fully within game rules.
Everything stays in-game currency. Nation data used is public.
