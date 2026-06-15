---
strategy: VOYAGER (Voyager — long-horizon LONG)
baseline_tag: VOYAGER_PAPER
code_path: strategies/voyager.py
scorecard: docs/scorecards/voyager_scorecard.md
last_updated: 2026-04-25
---

# Voyager Promotion Criteria

This document is the pre-declared promotion path for the active VOYAGER paper
sleeve. It binds before evidence matures so that promotion decisions are
evidence-based, not retroactive.

References that take precedence:
- `STRATEGY_DOCTRINE.md` — verdict hierarchy and Pre-Backtest Gate
- `CURRENT_READINESS.md` — current operational truth
- `MASTER_PLAN.md §Change Discipline` — pre-declared reason / rollback
- `voyager_scorecard.md` — Paper-Cycle Success Criteria (canonical thresholds)

If this file conflicts with the scorecard on a numerical threshold, the
scorecard wins. This document only sequences the staged path.

---

## Promotion Path

The path is `Paper → Pilot Candidate → Live Candidate`. No stage may be
skipped. Each stage has its own entry conditions and disqualifiers.

```
Paper                 (current)
  └── Pilot Candidate (real capital, capped, supervised)
        └── Live Candidate (scaled real capital, autonomous within sleeve cap)
```

---

## Stage 1 — Paper (Current)

### Required sample
- **≥ 30 logged paper signals** in `voyager_paper_signals` with `signal_status='open'`
  or any non-stopped state at logging time.
- **≥ 30 signals with 30d window closed** (i.e. `outcome_30d` populated by the
  resolver wired into `scripts/run_paper_evidence.py`).

### Required outcome windows
- Primary horizon: **30d** (entry-quality measurement).
- Secondary horizon: **90d** (early thesis confirmation; required where n ≥ 10
  measured).
- Confirming horizon: **180d** (used qualitatively only at this stage; full
  6–18 month exit P&L is out of scope for paper).

### Quality thresholds
| # | Criterion | Threshold |
|---|-----------|-----------|
| 1 | 30d avg return | **> 0%** |
| 2 | MA200 hold rate at 30d | **≥ 70%** |
| 3 | 90d win rate (n ≥ 10) | **≥ 50%** |
| 4 | No archetype with 90d WR < 30% (n ≥ 5) | none failing |
| 5 | 13F avg pts contribution | **≥ −2** (overlay must not be actively harmful) |

### Concentration / governance sanity
- No single ticker accounts for more than **25%** of paper signals.
- No single sector accounts for more than **35%** of paper signals.
- Same-ticker dedup remains enforced via `core/voyager_paper_logger.py`
  (forward-only).
- Frozen-sleeve guard remains green (no REMORA / CONTRARIAN / SHORT_B /
  PATHFINDER rows appear in active opportunity views).

### Disqualifying conditions (stop the paper cycle, fall back to research)
- Any archetype reaches **90d WR < 30% with n ≥ 5** and the loss path is
  not an isolated regime artifact.
- 30d **stop-loss-hit rate > 35%** across the cohort (entry-quality gate
  failed; thesis is breaking too early).
- Paper supply collapses: scanner produces **0 setups** for **20 consecutive
  trading days** with the dominant rejection unchanged. Treat as a scanner
  starvation issue, not promotion evidence.
- 13F average contribution **< −5** (overlay actively damaging).

### Exit conditions for Stage 1
All five quality thresholds passed AND all four concentration checks passed
AND zero disqualifiers fired. Mark paper-valid in scorecard, open Stage 2.

---

## Stage 2 — Pilot Candidate

### Capital geometry
- **Per-position notional cap: $25,000**.
- **Max concurrent pilot positions: 2**.
- Sleeve runs **alongside continued paper logging**, not in place of it.
- Pilot positions are tracked in the live `trades` table with
  `notes='VOYAGER_PILOT'` so they are auditable and excluded from broader
  live-strategy promotion logic until Stage 3.

### Required sample
- **≥ 6 measured 90d outcomes** from the pilot cohort.
- Pilot cohort runs for **a minimum of 6 months** before any review,
  regardless of sample size, to capture at least one quarterly market beat.

### Quality thresholds
- Pilot **90d avg return ≥ paper 90d avg return − 1σ** (paper expectancy
  must transfer to real execution within one standard deviation; otherwise
  there is a slippage / adverse-selection / borrow problem).
- Pilot **MA200 hold rate at 30d ≥ 70%** (entry quality unchanged from paper).
- Pilot **stop-hit rate at 30d < 35%**.
- **At most 2** of the three archetypes (BASE / TREND_PULLBACK / EARLY)
  underperform paper expectations by more than 1σ.

### Risk circuit breakers (cut off new pilot entries)
- **Per-trade circuit:** any closed pilot loss exceeds **−10%** unrealised
  before the 30d window. Stop opening new pilots until cause review.
- **Cohort circuit:** rolling pilot drawdown across all open positions
  reaches **−8% of pilot-allocated capital**. Stop and review.

### Disqualifying conditions (fall back to Stage 1 or research)
- Pilot 90d avg return underperforms paper by more than **2σ**.
- Pilot MA200 hold rate at 30d **< 60%** (structural failure).
- Borrow / margin / fill cost reveals an execution problem that breaks paper
  assumptions (e.g. consistent slippage > 0.5% on entries, or borrow rate
  spikes invalidating geometry).

### Exit conditions for Stage 2
≥ 6 measured 90d pilot outcomes, all three thresholds met, zero
disqualifiers, and zero unresolved circuit breakers across the most recent
60 days. Mark pilot-valid in scorecard, open Stage 3.

---

## Stage 3 — Live Candidate

### Capital geometry
- Per-position notional cap **starts at 1% of portfolio**, may scale to **3%
  over 6 months** on continued positive evidence.
- **Max concurrent live VOYAGER positions: 3** (matches `MAX_POSITIONS_PER_STRATEGY`).
- Sleeve-level cap: live VOYAGER exposure ≤ **6% of portfolio NAV**.
- Pilot lane closes when Stage 3 begins; further evidence accrues at live
  size only.

### Required quality during live
- 90d avg return remains > 0% on rolling 6-month windows.
- MA200 hold rate at 30d remains ≥ 70%.
- Council profile alignment: must be running on a VOYAGER-specific profile
  (not generic VetoCouncil) — see `voyager_scorecard.md §Council Profile Gap`.

### Disqualifying conditions (revoke live status, fall back to Stage 2)
- 90d avg return turns negative on a rolling 6-month window.
- 30d stop-hit rate breaches **35%** for two consecutive months.
- Sleeve-level monthly drawdown exceeds **−5%** twice in any 12-month window.
- Doctrine drift: any signal logged with `direction != 'LONG'`. Hard fail.

---

## Out of Scope (do not let scope creep)

- Options overlay routing for VOYAGER long bias is downstream and not part
  of this promotion path.
- 13F sponsorship-driven entries (PATHFINDER) are a separate paused sleeve.
- Universe expansion (more than 90 dynamic Voyager candidates) is a separate
  Change Discipline ticket and must be evaluated against rule #9 (one
  structural hypothesis at a time).

---

## Stage Tracking

Stage transitions are recorded in `docs/scorecards/voyager_scorecard.md`
under Validation Status. Each promotion or demotion event must include:

- date
- evidence (sample size, threshold values measured)
- pre-declared metric expected to improve
- rollback condition

per `MASTER_PLAN.md §Change Discipline`.
