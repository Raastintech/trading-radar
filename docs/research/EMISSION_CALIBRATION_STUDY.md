# Emission Calibration Study (Phase 1G.17)

Generated: 2026-06-11T23:42:33Z · research-only counterfactual replay.
**No production threshold was modified by this study.** Promotion of a
variant is an operator decision tied to the holdout V2 restatement proposal.

Target research flow: **1.0–3.0
candidates/week/sleeve**; a variant qualifies only with n≥15
mature 10d outcomes AND non-negative 10d rel-SPY. If nothing qualifies, the
honest answer is to extend bar depth / collect history — not to force flow.


## SNIPER (46 tickers, 3026 ticker-days)

| Variant | Emissions | /week | rel-SPY 5d | 10d | 20d | win10 | FP10 | recall | n10 | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| `S0_baseline` | 6 | 0.46 | -0.03% | +0.06% | -5.74% | 33.3 | 0.667 | 0.000 | 6 | NEED_MORE_DATA |
| `S1_no_atr_contraction` | 43 | 3.27 | -0.29% | +3.24% | +4.75% | 57.9 | 0.474 | 0.135 | 38 | — |
| `S2_vol_1_2` | 8 | 0.61 | +4.91% | +5.30% | +3.92% | 50.0 | 0.500 | 0.027 | 8 | NEED_MORE_DATA |
| `S3_score_60` | 6 | 0.46 | -0.03% | +0.06% | -5.74% | 33.3 | 0.667 | 0.000 | 6 | NEED_MORE_DATA |
| `S4_no_first_bar` | 6 | 0.46 | -0.03% | +0.06% | -5.74% | 33.3 | 0.667 | 0.000 | 6 | NEED_MORE_DATA |
| `S5_no_atr_vol_1_2` | 67 | 5.09 | +1.04% | +4.77% | +6.99% | 63.2 | 0.421 | 0.189 | 57 | — |
| `S6_no_slope` | 8 | 0.61 | +0.67% | +2.84% | +5.48% | 37.5 | 0.625 | 0.027 | 8 | NEED_MORE_DATA |

## VOYAGER (74 tickers, 6985 ticker-days)

| Variant | Emissions | /week | rel-SPY 5d | 10d | 20d | win10 | FP10 | recall | n10 | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| `Y0_baseline` | 289 | 15.31 | -0.39% | -0.59% | -2.23% | 47.2 | 0.556 | 0.176 | 288 | — |
| `Y1_ext_20pct` | 291 | 15.41 | -0.39% | -0.58% | -2.25% | 47.4 | 0.554 | 0.176 | 289 | — |
| `Y2_no_extension_gate` | 291 | 15.41 | -0.39% | -0.58% | -2.25% | 47.4 | 0.554 | 0.176 | 289 | — |
| `Y3_rs_minus2pct` | 353 | 18.7 | -0.63% | -0.93% | -2.53% | 43.4 | 0.594 | 0.189 | 350 | — |
| `Y4_floor_088` | 294 | 15.57 | -0.44% | -0.67% | -2.39% | 46.8 | 0.563 | 0.176 | 293 | — |
| `Y5_no_archetype` | 1122 | 59.43 | -0.30% | -0.87% | -2.59% | 45.7 | 0.546 | 0.270 | 995 | — |
| `Y6_ext20_rs0_no_arch` | 1428 | 75.64 | -0.33% | -0.89% | -2.58% | 45.0 | 0.548 | 0.284 | 1187 | — |

## Verdict

SNIPER qualifying: **NONE** ·
VOYAGER qualifying: **NONE**

a variant qualifies only if it lands in the 1-3/week band AND clears the n>=15 maturity floor AND has non-negative 10d rel-SPY; near_band = same quality bar within [0.8x, 1.25x] of the band (banding artifact tolerance); if none qualify the answer is 'collect more depth/history', NOT 'force the target'

## Fidelity caveats

- Replay depth is the cached bar depth; short histories ⇒ wide error bars.
- VOYAGER earnings/fundamental/13F gates are NOT_RETAINED (treated as pass)
  ⇒ VOYAGER emission rates are upper bounds.
- SNIPER score assumes neutral VIX band (no VIX history cached).
- Forward windows past available bars are excluded, never imputed.

*Sidecar:* `cache/research/emission_calibration_study_latest.json` ·
*Runner:* `./scripts/run_research_cycle.sh emission-calibration`
