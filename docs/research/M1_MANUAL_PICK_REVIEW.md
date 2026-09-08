# M1 Manual Pick Review — session 2026-09-04

*Human review of the 75-name M1 shadow pool. Cohort frozen 2026-09-08. Zero provider calls.*

> **RESEARCH ONLY — this is a FALSIFICATION TEST, not a shortlist.** The M1 pool publishes
> membership unordered on purpose. Ten names were picked from it anyway, and this document
> records the rule, the reasoning and the forward test that can prove the selection worthless.
> No trade signal, no entry, no size, no live verdict. Must never be pooled with the Phase 4B
> forward ledger, program verdicts, HC/EO routing, Alpha Focus, MCP or the dashboard.

## 1. Why the prior is "this will not work"

The shadow pool refuses to rank for a documented reason: **M1's ticker-clustered confidence
interval is negative at every horizon tested.** The basket beats the tape while the typical
name inside it does not. Thirteen conviction overlays, an earnings-surprise dataset and a
complete analyst-action study each failed to rank names inside the quintile.

So picking ten names out of seventy-five starts with the evidence against it. The only way
the exercise is worth anything is if it is set up to *fail visibly*, which is what the control
arm below does.

## 2. The rule, pre-registered

Applied mechanically, in order, to all 75 names. **No name was added or removed by hand
afterwards.**

| # | Rule | Rationale |
|---|---|---|
| R1 | median 20-session dollar volume ≥ $20M | tradability — the checklist's "could a real position be entered and exited" |
| R2 | close above a **rising** MA50 *and* a **rising** MA200 | trend intact on both timeframes, not just a 12-month artifact |
| R3 | no more than +25% above MA50 | the checklist's "extended or exhausted" |
| R4 | no worse than −15% from the 52-week high | a momentum name far off its high is a failing one |
| R5 | no `below_ma200` / `deep_drawdown_from_52w_high` tag | the pool's own risk flags |
| R6 | `quality_pass` on the pool's fundamental screen | the only fundamental filter available cache-side |

**Result: 19 of 75 passed R1–R5; 10 survived R6.** Those 10 are the cohort.

**What the rule deliberately ignores: 12-1 momentum rank.** Sorting inside M1 by momentum is
precisely the operation the ticker-clustered evidence says carries no information, so the rule
never looks at it.

## 3. The ten picks

| ticker | sector | $vol/day | vs MA50 | vs MA200 | from 52wh | ATR% | 3m | 6m |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| CORT | Healthcare | 89M | +7.2% | +67.1% | −9.7% | 3.7 | +53.7% | +229.6% |
| ILMN | Healthcare | 379M | +10.1% | +43.7% | −4.2% | 3.4 | +34.4% | +75.1% |
| KRYS | Healthcare | 69M | +2.4% | +23.4% | −5.3% | 2.6 | +19.0% | +41.2% |
| LFST | Healthcare | 44M | +11.0% | +54.6% | −1.2% | 3.3 | +71.8% | +83.7% |
| CDNA | Healthcare | 52M | +25.0% | +103.0% | −2.8% | 4.2 | +128.7% | +184.4% |
| DELL | Technology | 2,407M | +19.2% | +106.7% | 0.0% | 4.6 | +32.9% | +257.7% |
| TWLO | Technology | 440M | +8.1% | +42.7% | −9.0% | 4.3 | +3.1% | +82.0% |
| MPC | Energy | 847M | +20.8% | +60.9% | 0.0% | 2.6 | +48.4% | +75.8% |
| TECK | Basic Materials | 169M | +10.1% | +22.0% | −3.5% | 2.5 | +12.0% | +36.7% |
| COKE | Consumer Defensive | 77M | +1.1% | +5.5% | −12.7% | 3.2 | +5.2% | −7.4% |

Per-name thesis and invalidation are recorded in
`research/backtests/m1_manual_picks_2026_09_04.json` and frozen into the cohort. In short:

- **CORT** — the strongest sustained advance that is *not* extended: +229% over six months yet
  only 7.2% above MA50. The move has been absorbed rather than spiked.
- **ILMN** — cleanest structure in the pool. Large-cap turnaround, nothing stretched, $379M/day.
- **KRYS** — the low-variance leg (ATR 2.6%), steady and non-parabolic.
- **LFST** — near highs with +71.8% over three months; thinnest liquidity of the ten, so it is
  the speculative leg.
- **CDNA** — the most extended member (+25% over MA50). Retained only because the rule was
  pre-registered; flagged as the likeliest mean-reverter.
- **DELL** — AI-infrastructure demand through a quality balance sheet rather than a story stock.
- **TWLO** — the only pick *re-accelerating from a base* (+20.6% 1m vs +3.1% 3m) rather than
  extending a move.
- **MPC** — orderly advance at the 52-week high; +30% in a month on ATR of just 2.6%.
- **TECK** — copper-levered ballast against five healthcare legs.
- **COKE** — the weakest trend of the ten (−7.4% over six months). Retained for the same
  no-post-hoc-trimming reason; flagged as the likeliest to prove R1–R6 too loose.

## 4. Known weaknesses, stated before the outcome

1. **Healthcare is 5 of 10 legs.** The rule has no sector cap, so this is materially one sector
   bet plus five diversifiers.
2. **CDNA and COKE were knowingly retained.** If the cohort fails, check first whether those two
   caused it — that is a rule-calibration finding, not a selection finding.
3. **R6 is silently biased.** The quality tag is `UNKNOWN` or `no_fundamental_data` for 20 of the
   75 names, so R6 favours names that happen to have cached fundamentals.
4. **One session, ten names.** Even a clean win is a single cohort and promotes nothing.

## 5. How this gets judged

The control arm is **the 65 names not picked**, not SPY/QQQ.

Beating SPY/QQQ would prove nothing about selection: the pool already beats the tape by
construction, so an index comparison credits the selector for M1's basket property. Only the
not-picked arm isolates the claim *"choosing inside M1 adds information"*.

- **Horizons:** 20 / 45 / 60 / 90 sessions. Decisive: **45d and 60d** (20d is the noisiest and
  most likely to hand back a false positive).
- **Hypothesis:** human selection inside M1 adds nothing.
- **Falsification:** the hypothesis survives *unless* the picks beat the not-picked arm on
  **both mean and median at both 45d and 60d**. A mean-only win — one outlier carrying the
  cohort — is explicitly not enough.
- **Default verdict is `NO_EDGE_FROM_SELECTION`.** A win returns
  `SELECTION_ADDS_VALUE_PROVISIONAL` with `may_conclude: false`.

Current state: **`NOT_MATURE`** — two sessions elapsed of the twenty needed for the first
horizon. On a plain business-day count (holidays will push these slightly later): 20d matures
**~2026-10-02**, the first decisive read at 45d **~2026-11-06**, the second at 60d
**~2026-11-27**, and 90d **~2027-01-08**.

## 6. Reproducing it

```bash
# freeze is write-once; it refuses to overwrite a cohort outcomes have been read from
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.m1_manual_pick_tracker freeze
# resolve is cache-only and safe to run any time; immature horizons stay null
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.m1_manual_pick_tracker resolve
```

Artifacts: `cache/research/m1_manual_picks_2026-09-04.json` (frozen cohort),
`cache/research/m1_manual_picks_resolution_latest.json`, `logs/m1_manual_picks_latest.txt`.
