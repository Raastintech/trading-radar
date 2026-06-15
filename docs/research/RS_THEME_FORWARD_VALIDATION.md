# RS/Theme Forward Validation — cohort 1G10 (Phase 1G.11)

> RESEARCH-ONLY forward-validation gate. Routing/measurement only — NOT buy/sell/trade signals, NOT paper signals, NOT trade proposals. Does NOT promote any sleeve, register a strategy, modify core/universe.py, strategy gates, execution, governance, or live capital, and makes no provider calls.

_Generated 2026-06-07T03:49:00.689715+00:00; cohort as-of 2026-05-26 (frozen 2026-05-27T16:59:32.369429+00:00)._

## Status

- Forward sessions elapsed: **8**
- Matured horizons: **['1d', '3d', '5d']**
- Matured LENS_READY at 5d: **20** (verdict floor 20)
- Label distribution: `{'BLOCKED': 8, 'LENS_READY': 20, 'NEEDS_LENS': 1, 'LOW_QUALITY_NOISE': 1}`

## Forward returns by horizon (mean excess vs SPY)

| Horizon | Matured | A LENS_READY | B BLOCKED | C Alpha board | D Random | E RS-top |
|---|---|---|---|---|---|---|
| 1d | Y | -0.0014 | +0.0069 | +0.0024 | +0.0002 | +0.0153 |
| 3d | Y | +0.0026 | +0.0422 | +0.0312 | -0.0047 | -0.0128 |
| 5d | Y | +0.0043 | +0.0293 | +0.0487 | -0.0097 | -0.0250 |
| 10d | — | — | — | — | — | — |
| 20d | — | — | — | — | — | — |

## Candidate-quality comparison (primary horizon)

1. **LENS_READY vs BLOCKED:** underperform
2. **LENS_READY vs Alpha board:** underperform
3. **LENS_READY vs random control:** outperform
4. **Blocked correctly filtered?** BLOCKS_MAY_REJECT_WINNERS
5. **Became too extended:** 0.0%
6. **Offered pullback/reclaim:** 60.0%

## Gatekeeper precision audit

- Verdict: **BLOCKS_MAY_REJECT_WINNERS**
- Blocked names (8): `['KALV', 'RAL', 'CYTK', 'TEAM', 'OWL', 'LTH', 'AMAT', 'HEI']`
- Root-cause buckets: `{'real_quality_rejection': 6, 'gate_design_mismatch': 2}`
- Blocked names are outrunning LENS_READY and beating SPY — early sign the gates may be rejecting future winners; keep watching.

## Options-quality audit

- Candidates with an options label: 29
- Options P/L modeled: False (no point-in-time chain history)
- By label: `{'unusable': {'n_matured': 19, 'mean_fwd': 0.0284}, 'poor': {'n_matured': 6, 'mean_fwd': 0.021}, 'ok': {'n_matured': 2, 'mean_fwd': 0.0342}, 'unknown': {'n_matured': 2, 'mean_fwd': 0.0088}, 'none': {'n_matured': 1, 'mean_fwd': 0.0}}`
- Comparing mean forward return by options-quality label (see by_label). No options P/L is modeled — labels are evaluated only as a quality filter on the underlying's forward move.

## Daily maintenance recommendation (proposed only — not implemented)

**Primary: option B.** Positive but not decisive — continue nightly cache-only triage (B) to confirm; defer C/D until the edge holds over more matured windows.

| Opt | Action | Provider cost | False-positive risk | Overfitting risk | Expected benefit |
|---|---|---|---|---|---|
| A | Keep manual/adhoc RS-theme triage only | none (operator-invoked, cache-first triage) | low — nothing is automated | low — no recurring fit to noise | low — surface stays available but unmonitored between runs |
| B | Run RS/theme triage nightly (cache-only) | negligible — triage is cache-only; only the existing nightly lens/gatekeeper cadence touches providers | low — labels only, no routing into any board | low — measurement only | medium — accumulates the matured sample that every verdict needs |
| C | Run RS/theme triage + Lens refresh for top 20 nightly | ~20 stock-lens builds/night (provider calls per ticker) — non-trivial | medium — fresh constructive lenses may over-suggest names | medium — daily re-selection can chase recent movers | medium-high IF a forward edge is later confirmed |
| D | Route RS/theme LENS_READY into the Alpha board as a separate research strip | low incremental (reuses board enrichment) | high — a visible board strip invites action on unproven names | medium-high — couples a research surface to the operator board | high only after FORWARD_EDGE_DETECTED is sustained |
| E | Keep production unchanged | none | none | none | baseline safety |

## Verdict

### PROMISING_BUT_UNPROVEN

LENS_READY excess +0.0043 beats random but not the Alpha board — a positive but not yet decisive forward edge; keep accumulating sample.

> caveat: Alpha-board / options-quality annotations use CURRENT artifacts, not point-in-time snapshots (no board history exists at cohort as-of) — treat as best-effort context, not as-of truth.
