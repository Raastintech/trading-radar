# Strategy Research Lab Results

Generated: 2026-06-12T15:09:07.553166+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

Run scope: EXACT_2026_YTD · mode: `exact_ytd` · sampled: `False` · dates: 111 · ticker-days: 15540 · skipped dates: 0

## Data Reliability

- Price bars: TRUE_POINT_IN_TIME when sliced to the as-of date.
- Features: RECONSTRUCTED_FROM_PRICE_ONLY.
- Sector/theme/profile metadata: CURRENT_METADATA_APPROXIMATION for old dates.
- Stock Lens, Gatekeeper, Alpha board, fundamentals, earnings, 13F, options, social, and short-interest labels are not used as historical decision inputs unless dated history exists.

## Results Table

| Variant | Verdict | Trades | Avg Exp | Rel SPY | Rel QQQ | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| SNIPER_NO_ATR_CONTRACTION | REJECT | 73 | +1.92% | +1.79% | +1.55% | -22.75% |
| PROD_SNIPER_CURRENT | NEED_MORE_DATA | 8 | +1.10% | +0.66% | +0.55% | -13.57% |
| PROD_VOYAGER_CURRENT | REJECT | 225 | +1.23% | +0.87% | +0.84% | -68.01% |
| POWER_TREND_EXTENSION | REJECT | 332 | +1.25% | +0.82% | +0.45% | -87.98% |
| RECALL_SHADOW_RS_MOMENTUM | REJECT | 508 | +0.59% | +0.37% | +0.15% | -97.74% |
| RECALL_SHADOW_PULLBACK | REJECT | 313 | -0.05% | -0.11% | -0.33% | -91.53% |
| SIMPLE_SECTOR_RS | REJECT | 510 | -0.08% | -0.39% | -0.64% | -98.70% |
| RANDOM_LIQUID | REJECT | 506 | -0.12% | -0.58% | -1.11% | -99.34% |
| CORRECTION_LEADER_RECLAIM | REJECT | 196 | -0.75% | -0.38% | -0.48% | -90.61% |
| SIMPLE_MOM_20_60 | REJECT | 509 | -0.43% | -0.69% | -0.91% | -99.03% |
| QQQ_TECH_TACTICAL_SHORT | REJECT | 164 | -2.69% | -2.57% | -2.35% | -99.41% |

## Window Details

### 2026_ytd

- Dates: 2026-01-02 to 2026-06-11
- Signal dates: 111 (sampled: False, ticker-days: 15540, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 8 | +37.50% | +1.10% | +0.66% | +0.55% | -13.57% | +37.50% | +37.50% | +1.35% | +0.70% |
| SNIPER_NO_ATR_CONTRACTION | 73 | +58.90% | +1.92% | +1.79% | +1.55% | -22.75% | +27.40% | +27.40% | +2.17% | +1.52% |
| PROD_VOYAGER_CURRENT | 225 | +57.78% | +1.23% | +0.87% | +0.84% | -68.01% | +17.78% | +16.89% | +1.48% | +0.83% |
| CORRECTION_LEADER_RECLAIM | 196 | +41.33% | -0.75% | -0.38% | -0.48% | -90.61% | +37.76% | +17.86% | -0.50% | -1.15% |
| RECALL_SHADOW_RS_MOMENTUM | 508 | +42.91% | +0.59% | +0.37% | +0.15% | -97.74% | +54.13% | +41.14% | +0.84% | +0.19% |
| RECALL_SHADOW_PULLBACK | 313 | +43.77% | -0.05% | -0.11% | -0.33% | -91.53% | +42.17% | +20.45% | +0.20% | -0.45% |
| POWER_TREND_EXTENSION | 332 | +47.89% | +1.25% | +0.82% | +0.45% | -87.98% | +51.20% | +43.67% | +1.50% | +0.85% |
| QQQ_TECH_TACTICAL_SHORT | 164 | +22.56% | -2.69% | -2.57% | -2.35% | -99.41% | +71.95% | +17.68% | -2.41% | -3.15% |
| SIMPLE_SECTOR_RS | 510 | +39.41% | -0.08% | -0.39% | -0.64% | -98.70% | +59.22% | +36.86% | +0.17% | -0.48% |
| SIMPLE_MOM_20_60 | 509 | +37.13% | -0.43% | -0.69% | -0.91% | -99.03% | +60.71% | +34.58% | -0.18% | -0.83% |
| RANDOM_LIQUID | 506 | +43.87% | -0.12% | -0.58% | -1.11% | -99.34% | +43.28% | +22.73% | +0.13% | -0.52% |
| SPY_BUY_HOLD | 1 | +100.00% | +7.51% | n/a | n/a | +0.00% | +0.00% | +0.00% | +7.76% | +7.11% |
| QQQ_BUY_HOLD | 1 | +100.00% | +15.69% | n/a | n/a | +0.00% | +0.00% | +0.00% | +15.94% | +15.29% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2026-01:5, 2026-05:3] · regimes [spy_above_ma200=8@+1.10%] · themes [other:4, biotech_healthcare:2, semiconductors:1, unknown:1]
- SNIPER_NO_ATR_CONTRACTION: months [2026-01:31, 2026-02:14, 2026-03:4, 2026-04:4, 2026-05:16, 2026-06:4] · regimes [spy_above_ma200=73@+1.92%] · themes [unknown:28, other:26, semiconductors:17, biotech_healthcare:2]
- PROD_VOYAGER_CURRENT: months [2026-01:16, 2026-02:75, 2026-03:71, 2026-04:63] · regimes [spy_above_ma200=174@+0.65%, spy_below_ma200=51@+3.21%] · themes [other:82, unknown:82, biotech_healthcare:21, space_aerospace:16, semiconductors:14]
- CORRECTION_LEADER_RECLAIM: months [2026-01:54, 2026-02:37, 2026-03:83, 2026-04:22] · regimes [spy_above_ma200=186@-0.83%, spy_below_ma200=10@+0.83%] · themes [other:105, unknown:25, semiconductors:22, biotech_healthcare:18, space_aerospace:11]
- RECALL_SHADOW_RS_MOMENTUM: months [2026-01:89, 2026-02:84, 2026-03:125, 2026-04:95, 2026-05:100, 2026-06:15] · regimes [spy_above_ma200=448@+0.80%, spy_below_ma200=60@-0.98%] · themes [other:162, semiconductors:124, memory_storage:77, hardware:54, space_aerospace:46]
- RECALL_SHADOW_PULLBACK: months [2026-01:45, 2026-02:73, 2026-03:108, 2026-04:35, 2026-05:30, 2026-06:22] · regimes [spy_above_ma200=278@-0.31%, spy_below_ma200=35@+1.99%] · themes [other:156, semiconductors:39, unknown:38, space_aerospace:30, biotech_healthcare:22]
- POWER_TREND_EXTENSION: months [2026-01:69, 2026-02:47, 2026-03:39, 2026-04:75, 2026-05:86, 2026-06:16] · regimes [spy_above_ma200=306@+1.62%, spy_below_ma200=26@-3.17%] · themes [semiconductors:120, other:91, space_aerospace:56, memory_storage:33, hardware:30]
- QQQ_TECH_TACTICAL_SHORT: months [2026-01:10, 2026-02:65, 2026-03:65, 2026-04:24] · regimes [spy_above_ma200=134@-2.50%, spy_below_ma200=30@-3.53%] · themes [space_aerospace:50, other:45, semiconductors:35, hardware:17, memory_storage:17]
- SIMPLE_SECTOR_RS: months [2026-01:93, 2026-02:82, 2026-03:123, 2026-04:99, 2026-05:100, 2026-06:13] · regimes [spy_above_ma200=450@+0.01%, spy_below_ma200=60@-0.78%] · themes [other:185, semiconductors:123, memory_storage:60, biotech_healthcare:41, space_aerospace:39]
- SIMPLE_MOM_20_60: months [2026-01:92, 2026-02:83, 2026-03:123, 2026-04:98, 2026-05:102, 2026-06:11] · regimes [spy_above_ma200=449@-0.35%, spy_below_ma200=60@-1.04%] · themes [semiconductors:156, other:153, memory_storage:75, unknown:38, biotech_healthcare:37]
- RANDOM_LIQUID: months [2026-01:75, 2026-02:83, 2026-03:116, 2026-04:104, 2026-05:98, 2026-06:30] · regimes [spy_above_ma200=446@-0.50%, spy_below_ma200=60@+2.69%] · themes [other:282, semiconductors:64, unknown:46, biotech_healthcare:37, space_aerospace:32]

## Comparison Answers

- is_production_sniper_worse_than_simple_baselines: NO
- does_sniper_no_atr_improve_flow_and_returns: YES
- does_recall_shadow_have_backtested_edge: YES
- does_pullback_improve_recall_shadow_entry_quality: NO
- does_power_trend_extension_work_beyond_recent_regime: NO
- does_qqq_tactical_short_produce_usable_edge: NO
- is_voyager_worth_preserving_unchanged: YES
- variant_deserving_paper_shadow_proposal: NONE_BACKTEST_ONLY_REQUIRES_WALK_FORWARD

## Paper-Shadow Decision

NO_VARIANT_READY_FOR_PAPER_SHADOW

No paper signals, trade proposals, strategy registry edits, execution edits, Gatekeeper edits, Veto Council edits, live-capital edits, or historical evidence mutation were made.
