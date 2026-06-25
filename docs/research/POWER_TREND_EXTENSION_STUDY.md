# Power-Trend Extension Study — Phase 1G.14

> RESEARCH-ONLY power-trend extension study. Forward MEASUREMENT + classification only — NOT buy/sell/trade signals, NOT paper signals, NOT trade proposals, and NO label is tradeable. Does NOT change the Gatekeeper, Veto Council, core/universe.py, strategy gates, the strategy registry, execution, governance, or live capital, and makes no provider calls.

_Generated 2026-06-25T13:00:39.300330+00:00._

## Cohort

- Too-extended candidates: **138** by source `{'gatekeeper_too_extended': 130, 'rs_theme_too_extended': 5, 'alpha_board_extended': 3}`
- Flag as-of range: `['2026-05-05', '2026-06-25']`
- Label distribution: `{'EXTENDED_BUT_WAIT_FOR_RESET': 76, 'LOW_QUALITY_EXTENSION': 47, 'CLIMAX_CHASE_EXTENSION': 2, 'POWER_TREND_EXTENSION': 13}`
- Matured POWER_TREND at 5d: **6** (floor 15)
- Names that were missed winners: **77**

## Forward rel-SPY by extension label and horizon

| Horizon | POWER_TREND | CLIMAX_CHASE | WAIT_FOR_RESET | LOW_QUALITY | ALL too_ext | Random |
|---|---|---|---|---|---|---|
| 1d | +0.0100 | -0.0008 | +0.0009 | -0.0014 | +0.0009 | -0.0070 |
| 3d | +0.0080 | +0.0656 | +0.0069 | +0.0119 | +0.0097 | +0.0103 |
| 5d | +0.0268 | +0.0520 | -0.0000 | -0.0061 | +0.0025 | — |
| 10d | +0.0471 | +0.0006 | -0.0065 | +0.0212 | +0.0088 | — |
| 20d | +0.0768 | +0.0725 | +0.0255 | +0.0563 | +0.0439 | — |

## Label detail (5d)

| Label | n | matured | rel-SPY | win% | MFE | MAE | time→pullback | continued% | reset-better% |
|---|---|---|---|---|---|---|---|---|---|
| POWER_TREND_EXTENSION | 13 | 6 | +0.0268 | 50.0% | +0.0467 | -0.0034 | — | 0.0% | 0.0% |
| CLIMAX_CHASE_EXTENSION | 2 | 2 | +0.0520 | 100.0% | +0.1097 | -0.0405 | — | 0.0% | 0.0% |
| EXTENDED_BUT_WAIT_FOR_RESET | 76 | 39 | -0.0000 | 30.8% | +0.0344 | -0.0239 | 3 | 12.8% | 2.6% |
| LOW_QUALITY_EXTENSION | 47 | 17 | -0.0061 | 47.1% | +0.0284 | -0.0268 | 1 | 11.8% | 0.0% |
| ALL_TOO_EXTENDED | 138 | 64 | +0.0025 | 39.1% | +0.0363 | -0.0232 | 2 | 10.9% | 1.6% |

## Theme-specific continuation audit

| Theme | n | power? | rel-SPY 5d | rel-SPY 10d | continued% | call-chase n | mean IV skew | verdict |
|---|---|---|---|---|---|---|---|---|
| other | 82 | — | -0.0062 | -0.0007 | 7.9% | 30 | +1.1400 | **EXTENSION_NOT_REWARDED** |
| semiconductors | 16 | Y | +0.0411 | +0.0540 | 12.5% | 8 | +1.0050 | **REWARDS_CHASING_STRENGTH** |
| hardware | 14 | Y | +0.0041 | -0.0516 | 25.0% | 8 | +1.1550 | **NEED_MORE_DATA** |
| biotech_healthcare | 13 | — | -0.0055 | -0.0027 | 14.3% | 6 | +1.1130 | **EXTENSION_NOT_REWARDED** |
| space_aerospace | 10 | Y | +0.0104 | +0.0396 | 16.7% | 4 | +1.4580 | **REWARDS_CHASING_STRENGTH** |
| crypto_blockchain | 1 | Y | — | — | — | 1 | +0.9650 | **NEED_MORE_DATA** |
| memory_storage | 1 | Y | — | — | — | 0 | +1.0130 | **NEED_MORE_DATA** |
| nuclear_energy | 1 | Y | +0.0269 | +0.0116 | 0.0% | 1 | +1.6000 | **NEED_MORE_DATA** |

## Verdict

### NEED_MORE_DATA → recommendation D

**Wait for more data.**

Only 6 matured POWER_TREND name(s) at 5d (< 15 floor) — preliminary POWER_TREND 5d rel-SPY=+0.0268 (win 50.0%) vs random — / low-quality -0.0061. Signal is encouraging but below the verdict floor; keep measuring. too_extended stays a HARD BLOCK in production.

## Proposed future Gatekeeper fields (PROPOSED ONLY — not implemented)

- **extension_block_type**: enum HARD_BLOCK_EXTENSION | SOFT_WARNING_POWER_TREND | WAIT_FOR_RESET | LOW_QUALITY
- **extension_context**: theme / RS / volume / breadth summary at flag time
- **theme_power_score**: 0-100 composite of power-theme membership + RS + cohort breadth
- **extension_risk_score**: 0-100 climax/chase risk (parabolic move, blow-off volume, call chase)
- **reset_required**: bool — wait for EMA20/VWAP pullback-reclaim before any research entry
- **continuation_allowed_for_research**: bool — research-only continuation tracking permitted

> caveat: Alpha-board / lens / options annotations use CURRENT artifacts, not point-in-time snapshots; per-ticker option skew comes from the latest lens only. Random controls anchor at the median flag date (single as-of). Treat as best-effort context.
