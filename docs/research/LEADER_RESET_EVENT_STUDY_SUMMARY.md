# LEADER_RESET — Event Study Summary (research-only)

**Generated:** 2026-06-16T05:50:04  
**Source:** `data/state/stock_lens_forward_log.jsonl` (1547 historical lens snapshots)  
**Friction:** 0.3% round-trip  
**Status:** Research only. No paper sleeve, no signals, no registry change.

> This study reuses the Stock Lens forward log, which already carries each
> snapshot's entry/leadership/options layers plus resolved forward outcomes.
> Each snapshot is classified into a LEADER_RESET candidate state using the v0
> gates as research filters, then forward cohorts are compared. It is a fast,
> artifact-based event study — NOT the rigorous point-in-time backtest in
> `LEADER_RESET_VALIDATION_PLAN.md`, which remains a prerequisite for activation.

## Cohort forward metrics (net of friction)

| state | n | n(5d resolved) | exp 5d net | exp 10d net | win 5d | rel SPY 5d | mean MAE 5d |
|---|---|---|---|---|---|---|---|
| RESEARCH_READY | 1 | 1 | -1.3326 | -1.9102 | 0.0 | 1.3508 | -5.3193 |
| WATCH_RECLAIM | 260 | 217 | -0.3436 | -0.04 | 0.4424 | -0.4585 | -4.8616 |
| LATE_EXTENDED | 275 | 223 | 3.2349 | 5.4632 | 0.5874 | 3.0252 | -4.4967 |
| BLOCKED | 658 | 518 | 0.804 | 2.1453 | 0.5637 | 0.4821 | -4.2717 |
| NO_EDGE | 353 | 276 | -0.4505 | -0.4106 | 0.471 | -0.693 | -3.984 |

Pooled control (all lens names): n=1547, exp5d_net=0.7592, rel_spy_5d=0.5141.

## Key findings

- RESEARCH_READY = 1: the Stock Lens entry validator reported actionable_now=True in only 1/1547 snapshots. The existing entry layer never emits an actionable reclaim — so LEADER_RESET's trigger must be built fresh; the validator alone will not produce entries. This is the structural reason the system opens almost nothing.
- In this bull-tape sample, LATE_EXTENDED forward returns (exp5d_net=3.2349, rel_spy_5d=3.0252) BEAT WATCH_RECLAIM (exp5d_net=-0.3436, rel_spy_5d=-0.4585). Momentum outran reset in-sample — a real thesis risk. The reset premise must be tested across regimes (incl. risk-off) in the formal backtest before activation.
- Closest existing reclaim proxies in entry.view: 'Pullback Forming' n=11, 'Watch Reclaim' n=545.

## Verdict: **NEED_MORE_DATA**

Not enough resolved RESEARCH_READY events to evaluate edge. The system rarely produces an actionable bullish-leader entry, so the sleeve cannot yet be accepted or rejected. Accumulate more clean-epoch evidence and let forward outcomes mature (next maturity 2026-05-28).

**Blockers:**
- RESEARCH_READY resolved-5d sample 1 < required 40

## Activation gates (all must pass before a paper sleeve is spec'd)

- [ ] **min_sample** — need >= 40 resolved RESEARCH_READY 5d, got `1`
- [ ] **net_5d_expectancy_positive** — need > 0, got `-1.3326`
- [ ] **net_10d_expectancy_positive** — need > 0, got `-1.9102`
- [x] **beats_spy_5d** — need rel_spy_5d > 0, got `1.3508`
- [x] **mae_acceptable** — need mean MAE > -8.0, got `-5.3193`

## Doctrine reminder

- minimum sample: >= 40 resolved RESEARCH_READY 5d events
- positive net 5d AND 10d expectancy (after round-trip friction)
- beats SPY baseline (rel_spy_5d > 0) and pooled random-liquid control
- acceptable MAE (mean 5d MAE > -8.0%)
- clean_epoch remains ready; forward resolver healthy (see forward_resolution_health)
- no concentration / slippage red flags

LEADER_RESET stays research-only until this study (and the formal validation-plan backtest) clears every gate. Phase 2C (Trade Proposal Generator) remains not started.
