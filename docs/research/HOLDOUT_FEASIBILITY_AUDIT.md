# Holdout Feasibility Audit (Phase 1G.17)

Generated: 2026-06-11T23:42:29Z · research-only · the V1 covenant
(`docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md`) and all in-window evidence are **unmodified**.

## Question

Can the pre-registered 2026H2 holdout still meet its own first acceptance
criterion — **≥30 closed trades inside
[2026-06-01, 2026-12-01)** — at observed emission rates?

## Observed (day 10 of 183)

| Metric | Value |
|---|---|
| In-window raw signals (active sleeves) | 1 |
| In-window decisions | 0 |
| In-window closed trades | 0 |
| Decisions, last 30 days | 0 |
| Paper signals, last 30 days | 3 |

## Projections

| Basis | Rate | Projected total by deadline | P(≥30) | Notes |
|---|---|---|---|---|
| `decision_basis` | 0.0/day | 0.0 | 0.0000 | actual decisions (real closed-trade pipeline), last 30d |
| `paper_signal_basis` | 0.1/day | 17.3 | 0.0035 | active-sleeve paper signals, last 30d; assumes 100% conversion to closed trades (strictly generous) |
| `replay_basis` | 0.1651/day | 28.6 | 0.4191 | UPPER BOUND ONLY: SNIPER replay expectation + observed VOYAGER trickle; counterfactual replay emissions have NEVER converted to a decision in-window, so this is not a planning basis |

## Covenant design flags

- VOYAGER hold horizon is 6-18 months; trades opened mid-window cannot close inside the window except via stop — the '>=30 closed' bar implicitly assumed tactical cadence
- SHORT_A was frozen 2026-05-24 (after registration on 2026-05-07); the covenant's third active sleeve no longer emits, removing its expected contribution

## Verdict

**statistically_viable = False** (planning-basis
P = 0.0035; replay upper bound
P = 0.4191, excluded from the verdict because it
stacks a never-observed conversion assumption).

**Recommendation: RESTATE_AFTER_REPAIR.** The holdout cannot plausibly reach its own n>=30 bar at observed emission rates. Recommended sequence: (1) land the cache-layer data-correctness fix, (2) run the emission-calibration study and pick gate sets at a 1-3/week research flow with non-negative forward quality, (3) restate the holdout as V2 with a fresh pre-registration date and realistic sample bar. The V1 covenant and all its evidence remain untouched, designated STARVED_HOLDOUT_V1 by reference.

Designation: the original covenant is referred to as
**STARVED_HOLDOUT_V1** going forward. The file itself is not renamed,
edited, or deleted — pre-registered history is immutable. A draft V2
restatement proposal (operator must ratify; not active) is at
`docs/research/HOLDOUT_2026H2_V2_RESTATEMENT_PROPOSAL.md`.
