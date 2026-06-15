---
strategy: SHORT_A (Event Continuation Short — Sleeve A of SHORT family)
baseline_tag: SHORT_A_PAPER
code_path: strategies/short_sleeve.py
scorecard: docs/scorecards/short_sleeve_scorecard.md
last_updated: 2026-04-25
---

# SHORT_A Promotion Criteria

This document is the pre-declared promotion path for the active SHORT_A paper
sleeve. It binds before evidence matures so that promotion decisions are
evidence-based, not retroactive.

References that take precedence:
- `STRATEGY_DOCTRINE.md §Direction Mandate` — SHORT is the sole short-direction family
- `SHORT_DOCTRINE.md` — sleeve identities and what each sleeve is/isn't
- `CURRENT_READINESS.md` — current operational truth
- `MASTER_PLAN.md §Change Discipline` — pre-declared reason / rollback
- `short_sleeve_scorecard.md` — Paper-Cycle Success Criteria (canonical thresholds)

If this file conflicts with the scorecard on a numerical threshold, the
scorecard wins. This document only sequences the staged path.

This file covers **Sleeve A only** (Event Continuation). Sleeve B (Broken
Leader) is research-only — see `short_sleeve_scorecard.md §Sleeve B` for its
v4 calibration blockers. Sleeve B has no promotion path until backtest-valid.

---

## Promotion Path

The path is `Paper → Pilot Candidate → Live Candidate`. No stage may be
skipped. Each stage has its own entry conditions and disqualifiers.

```
Paper                 (current, post-AMC-fix baseline)
  └── Pilot Candidate (real capital, capped, supervised, borrow-verified)
        └── Live Candidate (scaled real capital, autonomous within sleeve cap)
```

---

## Stage 1 — Paper (Current)

### Baseline reset
The AMC/BMO timing fix (2026-04-25) materially changes the signal generator.
**Paper sample counting starts from the first signal logged after the AMC fix
went live.** Pre-AMC-fix paper signals are research-only and do not count
toward Stage-1 sample size — they were measured under a generator that missed
~50% of the opportunity set.

### Required sample
- **≥ 30 logged paper signals** in tactical paper-trades (`signal_status='open'`
  or any non-stopped state at logging time), all post-AMC-fix.
- **≥ 30 signals with 10d window closed** (`outcome_10d` populated by the
  resolver wired into `scripts/run_paper_evidence.py`).
- Of those 30, **≥ 20 must have 20d window closed** (longer-horizon read).

### Required outcome windows
- Primary horizon: **10d** (matches backtest scorecard primary).
- Secondary horizon: **5d** (early validation; backtest showed +1.1% adj / 56.5% WR).
- Confirming horizon: **20d** (late repricing; backtest showed +0.9% adj at n=23).

### Quality thresholds
| # | Criterion | Threshold |
|---|-----------|-----------|
| 1 | 10d raw win rate | **≥ 50%** |
| 2 | 10d avg adjusted return (with friction) | **> 0%** |
| 3 | 10d stop-hit rate | **< 35%** |
| 4 | 5d adjusted avg return (n ≥ 15) | **> 0%** (consistency check vs 10d) |
| 5 | Gap bucket `−7% to −10%` win rate (n ≥ 5) | **≥ 50%** (the backtest sweet spot must hold) |
| 6 | Gap bucket `> −7%` (marginal) win rate (n ≥ 5) | **≥ 40%** (continuation gate must rescue marginal gaps) |

### Concentration / governance sanity
- No single ticker accounts for more than **30%** of paper signals.
- No single sector accounts for more than **40%** (consumer/retail dominance
  is expected from backtest data — wider tolerance than VOYAGER's 35%).
- AMC mix: **≥ 25%** of post-AMC-fix signals must be `event_time='amc'`
  (proves the AMC detection branch is firing in production, not just BMO).
- Same-ticker dedup remains enforced via `MAX_SAME_TICKER_POSITIONS=1`.
- Frozen-sleeve guard: SHORT_B does **not** appear in active opportunities.

### Disqualifying conditions (stop the paper cycle, fall back to research)
- 10d win rate **< 40%** with n ≥ 30 (clearly negative expectancy, not noise).
- 10d stop-hit rate **> 45%** (entry geometry / 1.5×ATR stop is structurally
  too tight; this is the same class of failure that killed Sleeve B).
- AMC mix below **15%** with n ≥ 30 (AMC detection branch is not firing —
  treat as a code regression and fix before promotion talk).
- Paper supply collapses: scanner produces **0 setups for 25 consecutive
  trading days** with the dominant rejection unchanged. Treat as a
  scanner/calendar starvation issue, not promotion evidence.
- Doctrine drift: any signal logged with `direction != 'SHORT'`. Hard fail
  (mirrors VOYAGER's LONG-only check).

### Exit conditions for Stage 1
All six quality thresholds passed AND all four concentration / governance
checks passed AND zero disqualifiers fired AND **borrow availability has been
spot-checked** for the top 10 tickers by signal count (if any name is
hard-to-borrow, flag it — execution will be different from paper). Mark
paper-valid in scorecard, open Stage 2.

---

## Stage 2 — Pilot Candidate

### Capital geometry
- **Per-position notional cap: $15,000** (smaller than VOYAGER's $25k —
  short asymmetric tail risk and borrow drag warrant tighter sizing).
- **Max concurrent pilot positions: 2** (matches `MAX_POSITIONS_PER_STRATEGY['SHORT']`).
- Sleeve runs **alongside continued paper logging**, not in place of it.
- Pilot positions are tracked in the live `trades` table with
  `notes='SHORT_A_PILOT'` so they are auditable and excluded from broader
  live-strategy promotion logic until Stage 3.
- **Borrow must be confirmed available** at pilot entry. If borrow is
  unavailable or rate exceeds 5% annualized, skip the signal (do not pay
  through borrow cost — geometry was sized for ~2% borrow assumption).

### Required sample
- **≥ 10 measured 10d outcomes** from the pilot cohort.
- Pilot cohort runs for **a minimum of 3 months** before any review,
  regardless of sample size, to span at least one earnings cycle batch.
  (Shorter than VOYAGER's 6 months because SHORT_A holds are 5–10d, so
  outcomes accrue ~3× faster.)

### Quality thresholds
- Pilot **10d adj avg return ≥ paper 10d adj avg − 1σ** (paper expectancy
  must transfer to real execution within one standard deviation; otherwise
  there is a slippage / borrow / locate problem).
- Pilot **10d win rate ≥ 45%** (slight tolerance below paper's 50% to
  absorb borrow drag and locate friction; still must be positive expectancy).
- Pilot **10d stop-hit rate < 35%** (entry quality unchanged from paper).
- **Borrow execution observed**: average realized borrow cost ≤ 3%
  annualized across the cohort. Single-name spikes acceptable; cohort
  average is the gate.

### Risk circuit breakers (cut off new pilot entries)
- **Per-trade circuit:** any closed pilot loss exceeds **−12% unrealised**
  before the 10d window. Stop opening new pilots until cause review.
  (Tighter than VOYAGER's −10% because shorts can squeeze faster than
  longs can crash.)
- **Cohort circuit:** rolling pilot drawdown across all open positions
  reaches **−6% of pilot-allocated capital**. Stop and review.
- **Squeeze circuit:** any single pilot position experiences a 1-day
  adverse move > 8% with elevated short interest (≥ 15% SI/float).
  Stop opening new pilots in similar names until the cause is understood
  (could be a forced-buy-in cascade signal).

### Disqualifying conditions (fall back to Stage 1 or research)
- Pilot 10d adj avg underperforms paper by more than **2σ**.
- Pilot 10d stop-hit rate **≥ 45%** (matches paper-stage disqualifier;
  structural failure in stop geometry).
- Borrow / locate issue: ≥ 30% of qualifying paper signals could not be
  shorted in pilot due to no borrow available. Re-evaluate universe.
- Slippage > 0.3% on entries averaged across cohort (worse than backtest's
  0.05% friction model — execution is meaningfully different from paper).

### Exit conditions for Stage 2
≥ 10 measured 10d pilot outcomes, all four thresholds met, zero
disqualifiers, and zero unresolved circuit breakers across the most recent
45 days. Mark pilot-valid in scorecard, open Stage 3.

---

## Stage 3 — Live Candidate

### Capital geometry
- Per-position notional cap **starts at 0.5% of portfolio**, may scale to
  **2% over 6 months** on continued positive evidence. (Both bounds smaller
  than VOYAGER's 1%/3% — short asymmetric risk warrants permanent
  tighter sizing.)
- **Max concurrent live SHORT positions: 2** (matches
  `MAX_POSITIONS_PER_STRATEGY['SHORT']`).
- Sleeve-level cap: live SHORT_A exposure ≤ **4% of portfolio NAV**.
  (Tighter than VOYAGER's 6% — short crowding risk and squeeze risk.)
- Pilot lane closes when Stage 3 begins; further evidence accrues at live
  size only.

### Required quality during live
- 10d adj avg return remains > 0% on rolling 3-month windows.
- 10d win rate remains ≥ 45%.
- 10d stop-hit rate remains < 35%.
- Council profile alignment: must be running on a SHORT-specific profile
  (not generic VetoCouncil) — see `short_sleeve_scorecard.md §Council
  Profile Gap`. The generic profile under-weights MomentumAgent for shorts
  and over-weights FlowAgent.

### Disqualifying conditions (revoke live status, fall back to Stage 2)
- 10d adj avg turns negative on a rolling 3-month window.
- 10d stop-hit rate breaches **35%** for two consecutive months.
- Sleeve-level monthly drawdown exceeds **−4%** twice in any 12-month window.
  (Tighter than VOYAGER's −5% — see capital-geometry note above.)
- Doctrine drift: any signal logged with `direction != 'SHORT'`. Hard fail.
- Regime drift: VIX > 40 sustained for ≥ 5 sessions while sleeve is open
  (entire SHORT family doctrine assumes non-crisis regimes — bear-crisis
  vol invalidates the gap-continuation thesis).

---

## Out of Scope (do not let scope creep)

- Sleeve B (Broken Leader) is research-only. Do not paper-validate Sleeve B
  off Sleeve A's pilot infrastructure — Sleeve B requires its own v4
  calibration before any promotion talk.
- Sleeve C (Hype / Exhaustion) is explicitly deferred per
  `SHORT_DOCTRINE.md §5` until both Sleeves A and B are paper-validated.
- Options-based short overlays (puts, put spreads) are downstream and not
  part of this promotion path.
- Universe expansion beyond the current 65-ticker SHORT_A universe is a
  separate Change Discipline ticket.

---

## Stage Tracking

Stage transitions are recorded in `docs/scorecards/short_sleeve_scorecard.md`
under Validation Status. Each promotion or demotion event must include:

- date
- evidence (sample size, threshold values measured)
- pre-declared metric expected to improve
- rollback condition

per `MASTER_PLAN.md §Change Discipline`.

The AMC-fix baseline reset (2026-04-25) is recorded as the Stage-1 sample
start. Any pre-fix paper signals remain in the database for archival /
research purposes but do not count toward Stage-1 thresholds.
