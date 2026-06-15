# Power-Trend Extension Study — Phase 1G.14

> RESEARCH-ONLY power-trend extension study. Forward MEASUREMENT + classification only — NOT buy/sell/trade signals, NOT paper signals, NOT trade proposals, and NO label is tradeable. Does NOT change the Gatekeeper, Veto Council, core/universe.py, strategy gates, the strategy registry, execution, governance, or live capital, and makes no provider calls.

_Generated 2026-06-07T03:48:44.266871+00:00._

## Cohort

- Too-extended candidates: **116** by source `{'gatekeeper_too_extended': 105, 'rs_theme_too_extended': 5, 'alpha_board_extended': 6}`
- Flag as-of range: `['2026-05-05', '2026-06-07']`
- Label distribution: `{'EXTENDED_BUT_WAIT_FOR_RESET': 63, 'LOW_QUALITY_EXTENSION': 35, 'CLIMAX_CHASE_EXTENSION': 2, 'POWER_TREND_EXTENSION': 16}`
- Matured POWER_TREND at 5d: **12** (floor 15)
- Names that were missed winners: **63**

## Forward rel-SPY by extension label and horizon

| Horizon | POWER_TREND | CLIMAX_CHASE | WAIT_FOR_RESET | LOW_QUALITY | ALL too_ext | Random |
|---|---|---|---|---|---|---|
| 1d | +0.0254 | +0.0607 | +0.0116 | -0.0027 | +0.0103 | -0.0020 |
| 3d | +0.0469 | +0.1134 | +0.0216 | +0.0033 | +0.0218 | -0.0071 |
| 5d | +0.0521 | +0.1383 | +0.0183 | -0.0108 | +0.0176 | -0.0062 |
| 10d | +0.0692 | +0.2509 | +0.0432 | +0.0016 | +0.0388 | — |
| 20d | +0.0613 | — | -0.0191 | — | +0.0077 | — |

## Label detail (5d)

| Label | n | matured | rel-SPY | win% | MFE | MAE | time→pullback | continued% | reset-better% |
|---|---|---|---|---|---|---|---|---|---|
| POWER_TREND_EXTENSION | 16 | 12 | +0.0521 | 75.0% | +0.0875 | -0.0042 | — | 0.0% | 0.0% |
| CLIMAX_CHASE_EXTENSION | 2 | 2 | +0.1383 | 100.0% | +0.1590 | -0.0255 | — | 0.0% | 0.0% |
| EXTENDED_BUT_WAIT_FOR_RESET | 63 | 38 | +0.0183 | 39.5% | +0.0600 | -0.0215 | 3 | 13.2% | 5.3% |
| LOW_QUALITY_EXTENSION | 35 | 24 | -0.0108 | 37.5% | +0.0335 | -0.0248 | 1 | 20.8% | 4.2% |
| ALL_TOO_EXTENDED | 116 | 76 | +0.0176 | 46.1% | +0.0586 | -0.0199 | 2 | 13.2% | 3.9% |

## Theme-specific continuation audit

| Theme | n | power? | rel-SPY 5d | rel-SPY 10d | continued% | call-chase n | mean IV skew | verdict |
|---|---|---|---|---|---|---|---|---|
| other | 68 | — | +0.0086 | +0.0040 | 14.6% | 27 | +1.1900 | **REWARDS_CHASING_STRENGTH** |
| semiconductors | 15 | Y | +0.0380 | +0.0422 | 0.0% | 4 | +0.9370 | **REWARDS_CHASING_STRENGTH** |
| hardware | 13 | Y | +0.0599 | +0.1514 | 12.5% | 6 | +1.1780 | **REWARDS_CHASING_STRENGTH** |
| biotech_healthcare | 11 | — | -0.0247 | +0.0078 | 25.0% | 5 | +1.1890 | **EXTENSION_NOT_REWARDED** |
| space_aerospace | 7 | Y | +0.0483 | +0.2509 | 20.0% | 4 | +1.2780 | **REWARDS_CHASING_STRENGTH** |
| crypto_blockchain | 1 | Y | -0.0185 | +0.0070 | 0.0% | 1 | +0.9650 | **NEED_MORE_DATA** |
| nuclear_energy | 1 | Y | +0.0269 | — | 0.0% | 1 | +1.6000 | **NEED_MORE_DATA** |

## Verdict

### NEED_MORE_DATA → recommendation D

**Wait for more data.**

Only 12 matured POWER_TREND name(s) at 5d (< 15 floor) — preliminary POWER_TREND 5d rel-SPY=+0.0521 (win 75.0%) vs random -0.0062 / low-quality -0.0108. Signal is encouraging but below the verdict floor; keep measuring. too_extended stays a HARD BLOCK in production.

## Proposed future Gatekeeper fields (PROPOSED ONLY — not implemented)

- **extension_block_type**: enum HARD_BLOCK_EXTENSION | SOFT_WARNING_POWER_TREND | WAIT_FOR_RESET | LOW_QUALITY
- **extension_context**: theme / RS / volume / breadth summary at flag time
- **theme_power_score**: 0-100 composite of power-theme membership + RS + cohort breadth
- **extension_risk_score**: 0-100 climax/chase risk (parabolic move, blow-off volume, call chase)
- **reset_required**: bool — wait for EMA20/VWAP pullback-reclaim before any research entry
- **continuation_allowed_for_research**: bool — research-only continuation tracking permitted

> caveat: Alpha-board / lens / options annotations use CURRENT artifacts, not point-in-time snapshots; per-ticker option skew comes from the latest lens only. Random controls anchor at the median flag date (single as-of). Treat as best-effort context.
