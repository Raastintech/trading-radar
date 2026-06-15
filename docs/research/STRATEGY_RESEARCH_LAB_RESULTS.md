# Strategy Research Lab Results

Generated: 2026-06-12T15:10:58.612205+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

Run scope: EXACT_FULL_WINDOWS · mode: `exact_full` · sampled: `False` · dates: 1286 · ticker-days: 174668 · skipped dates: 0

## Data Reliability

- Price bars: TRUE_POINT_IN_TIME when sliced to the as-of date.
- Features: RECONSTRUCTED_FROM_PRICE_ONLY.
- Sector/theme/profile metadata: CURRENT_METADATA_APPROXIMATION for old dates.
- Stock Lens, Gatekeeper, Alpha board, fundamentals, earnings, 13F, options, social, and short-interest labels are not used as historical decision inputs unless dated history exists.

## Results Table

| Variant | Verdict | Trades | Avg Exp | Rel SPY | Rel QQQ | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | PROMISING_BUT_OVERFIT_RISK | 85 | +1.68% | +1.48% | +1.29% | -13.57% |
| PROD_VOYAGER_CURRENT | PROMISING_BUT_OVERFIT_RISK | 3012 | +0.58% | +0.20% | -0.14% | -99.96% |
| SNIPER_NO_ATR_CONTRACTION | PROMISING_BUT_OVERFIT_RISK | 832 | +0.70% | +0.16% | +0.01% | -68.27% |
| CORRECTION_LEADER_RECLAIM | PROMISING_BUT_OVERFIT_RISK | 2237 | +0.84% | +0.00% | -0.30% | -90.61% |
| POWER_TREND_EXTENSION | PROMISING_BUT_OVERFIT_RISK | 1784 | +0.99% | -0.01% | -0.20% | -87.98% |
| RECALL_SHADOW_PULLBACK | PROMISING_BUT_OVERFIT_RISK | 3933 | +0.61% | +0.04% | -0.14% | -98.65% |
| RECALL_SHADOW_RS_MOMENTUM | PROMISING_BUT_OVERFIT_RISK | 5853 | +0.40% | +0.02% | -0.08% | -99.91% |
| RANDOM_LIQUID | PROMISING_BUT_OVERFIT_RISK | 6248 | +0.37% | -0.28% | -0.52% | -99.34% |
| SIMPLE_SECTOR_RS | PROMISING_BUT_OVERFIT_RISK | 6291 | +0.03% | -0.35% | -0.46% | -100.00% |
| SIMPLE_MOM_20_60 | PROMISING_BUT_OVERFIT_RISK | 5848 | -0.06% | -0.56% | -0.68% | -99.99% |
| QQQ_TECH_TACTICAL_SHORT | PROMISING_BUT_OVERFIT_RISK | 1567 | -0.74% | -0.51% | -0.16% | -99.41% |

## Window Details

### 2024_available

- Dates: 2024-01-02 to 2024-12-31
- Signal dates: 252 (sampled: False, ticker-days: 33264, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 23 | +69.57% | +1.91% | +0.94% | +0.85% | -8.84% | +4.35% | +8.70% | +2.16% | +1.51% |
| SNIPER_NO_ATR_CONTRACTION | 197 | +52.28% | +0.69% | -0.12% | -0.02% | -39.04% | +20.30% | +11.68% | +0.94% | +0.29% |
| PROD_VOYAGER_CURRENT | 656 | +49.39% | +0.40% | -0.54% | -0.82% | -86.84% | +16.31% | +9.60% | +0.65% | -0.00% |
| CORRECTION_LEADER_RECLAIM | 466 | +57.08% | +1.38% | +0.60% | +0.50% | -77.52% | +21.03% | +18.03% | +1.63% | +0.98% |
| RECALL_SHADOW_RS_MOMENTUM | 1236 | +45.06% | +0.14% | -0.36% | -0.34% | -99.91% | +44.17% | +25.32% | +0.39% | -0.26% |
| RECALL_SHADOW_PULLBACK | 875 | +51.31% | +0.68% | -0.05% | -0.18% | -98.65% | +27.31% | +17.83% | +0.93% | +0.28% |
| POWER_TREND_EXTENSION | 168 | +46.43% | +0.68% | +0.27% | +0.09% | -72.70% | +45.83% | +30.95% | +0.93% | +0.28% |
| QQQ_TECH_TACTICAL_SHORT | 215 | +35.35% | -1.21% | -0.61% | -0.40% | -97.19% | +60.93% | +22.79% | -0.92% | -1.70% |
| SIMPLE_SECTOR_RS | 1258 | +41.89% | -0.13% | -0.55% | -0.53% | -99.97% | +50.24% | +27.50% | +0.12% | -0.53% |
| SIMPLE_MOM_20_60 | 1206 | +43.45% | +0.13% | -0.25% | -0.24% | -99.96% | +48.34% | +28.77% | +0.38% | -0.27% |
| RANDOM_LIQUID | 1260 | +49.44% | +0.36% | -0.28% | -0.32% | -97.85% | +30.08% | +17.38% | +0.61% | -0.04% |
| SPY_BUY_HOLD | 1 | +100.00% | +25.93% | n/a | n/a | +0.00% | +0.00% | +0.00% | +26.18% | +25.53% |
| QQQ_BUY_HOLD | 1 | +100.00% | +28.34% | n/a | n/a | +0.00% | +0.00% | +0.00% | +28.59% | +27.94% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2024-01:2, 2024-02:1, 2024-03:2, 2024-05:1, 2024-06:2, 2024-07:1, 2024-09:7, 2024-10:4, 2024-11:2, 2024-12:1] · regimes [spy_above_ma200=23@+1.91%] · themes [unknown:11, other:8, semiconductors:4]
- SNIPER_NO_ATR_CONTRACTION: months [2024-01:19, 2024-02:26, 2024-03:26, 2024-04:6, 2024-05:9, 2024-06:12, 2024-07:18, 2024-08:10, 2024-09:9, 2024-10:11, 2024-11:28, 2024-12:20, 2025-01:3] · regimes [spy_above_ma200=197@+0.69%] · themes [other:83, unknown:56, semiconductors:50, biotech_healthcare:8]
- PROD_VOYAGER_CURRENT: months [2024-01:45, 2024-02:48, 2024-03:31, 2024-04:59, 2024-05:51, 2024-06:35, 2024-07:28, 2024-08:44, 2024-09:65, 2024-10:90, 2024-11:67, 2024-12:58, 2025-01:27, 2025-12:8] · regimes [spy_above_ma200=656@+0.40%] · themes [other:376, unknown:207, biotech_healthcare:45, space_aerospace:17, semiconductors:10]
- CORRECTION_LEADER_RECLAIM: months [2024-01:10, 2024-02:8, 2024-03:23, 2024-04:66, 2024-05:71, 2024-07:12, 2024-08:99, 2024-09:78, 2024-10:23, 2024-11:38, 2024-12:19, 2025-01:17, 2025-12:2] · regimes [spy_above_ma200=466@+1.38%] · themes [other:301, unknown:68, biotech_healthcare:46, space_aerospace:37, semiconductors:14]
- RECALL_SHADOW_RS_MOMENTUM: months [2024-01:71, 2024-02:104, 2024-03:100, 2024-04:98, 2024-05:121, 2024-06:85, 2024-07:112, 2024-08:90, 2024-09:102, 2024-10:116, 2024-11:92, 2024-12:116, 2025-01:28, 2025-12:1] · regimes [spy_above_ma200=1236@+0.14%] · themes [other:659, semiconductors:163, unknown:147, biotech_healthcare:145, space_aerospace:74]
- RECALL_SHADOW_PULLBACK: months [2024-01:57, 2024-02:92, 2024-03:81, 2024-04:101, 2024-05:68, 2024-06:52, 2024-07:64, 2024-08:46, 2024-09:54, 2024-10:86, 2024-11:56, 2024-12:89, 2025-01:26, 2025-12:3] · regimes [spy_above_ma200=875@+0.68%] · themes [other:528, unknown:132, semiconductors:75, biotech_healthcare:71, space_aerospace:49]
- POWER_TREND_EXTENSION: months [2024-01:7, 2024-02:13, 2024-03:15, 2024-04:5, 2024-05:3, 2024-06:8, 2024-07:4, 2024-08:4, 2024-09:13, 2024-10:10, 2024-11:23, 2024-12:55, 2025-01:8] · regimes [spy_above_ma200=168@+0.68%] · themes [other:102, semiconductors:31, hardware:21, memory_storage:7, space_aerospace:7]
- QQQ_TECH_TACTICAL_SHORT: months [2024-01:33, 2024-02:6, 2024-03:9, 2024-04:52, 2024-05:15, 2024-06:1, 2024-07:23, 2024-08:30, 2024-09:3, 2024-10:4, 2024-11:16, 2024-12:5, 2025-01:8, 2025-04:5, 2025-12:4, 2026-01:1] · regimes [spy_above_ma200=215@-1.21%] · themes [other:120, semiconductors:70, hardware:16, memory_storage:5, space_aerospace:4]
- SIMPLE_SECTOR_RS: months [2024-01:75, 2024-02:105, 2024-03:92, 2024-04:104, 2024-05:130, 2024-06:84, 2024-07:109, 2024-08:106, 2024-09:101, 2024-10:131, 2024-11:80, 2024-12:113, 2025-01:25, 2025-12:2, 2026-01:1] · regimes [spy_above_ma200=1258@-0.13%] · themes [other:650, unknown:211, biotech_healthcare:155, semiconductors:147, hardware:58]
- SIMPLE_MOM_20_60: months [2024-01:76, 2024-02:105, 2024-03:99, 2024-04:88, 2024-05:104, 2024-06:85, 2024-07:116, 2024-08:86, 2024-09:93, 2024-10:118, 2024-11:97, 2024-12:113, 2025-01:23, 2025-12:2, 2026-01:1] · regimes [spy_above_ma200=1206@+0.13%] · themes [other:705, biotech_healthcare:157, semiconductors:123, unknown:117, hardware:48]
- RANDOM_LIQUID: months [2024-01:61, 2024-02:104, 2024-03:96, 2024-04:115, 2024-05:117, 2024-06:82, 2024-07:118, 2024-08:103, 2024-09:99, 2024-10:118, 2024-11:97, 2024-12:105, 2025-01:32, 2025-04:2, 2025-12:11] · regimes [spy_above_ma200=1260@+0.36%] · themes [other:746, unknown:264, biotech_healthcare:101, semiconductors:100, space_aerospace:28]

### 2025_available

- Dates: 2025-01-02 to 2025-12-31
- Signal dates: 250 (sampled: False, ticker-days: 34330, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 10 | +60.00% | +1.18% | +0.42% | -0.00% | -9.45% | +10.00% | +10.00% | +1.43% | +0.78% |
| SNIPER_NO_ATR_CONTRACTION | 134 | +44.78% | -0.07% | -0.65% | -0.75% | -68.27% | +26.12% | +11.19% | +0.18% | -0.47% |
| PROD_VOYAGER_CURRENT | 590 | +49.49% | +0.39% | +0.11% | +0.15% | -99.96% | +34.24% | +18.31% | +0.64% | -0.01% |
| CORRECTION_LEADER_RECLAIM | 443 | +53.72% | +0.98% | -0.10% | -0.24% | -76.32% | +27.54% | +18.96% | +1.23% | +0.58% |
| RECALL_SHADOW_RS_MOMENTUM | 1056 | +46.40% | +0.54% | +0.18% | +0.14% | -99.08% | +47.63% | +31.16% | +0.79% | +0.14% |
| RECALL_SHADOW_PULLBACK | 726 | +52.89% | +0.77% | +0.38% | +0.43% | -97.70% | +32.92% | +19.15% | +1.02% | +0.37% |
| POWER_TREND_EXTENSION | 293 | +46.76% | +0.77% | +0.39% | +0.31% | -67.24% | +49.49% | +36.52% | +1.02% | +0.37% |
| QQQ_TECH_TACTICAL_SHORT | 388 | +42.53% | +0.46% | -0.44% | -0.61% | -96.90% | +54.90% | +37.89% | +0.74% | +0.01% |
| SIMPLE_SECTOR_RS | 1250 | +43.12% | +0.23% | -0.08% | -0.14% | -100.00% | +52.88% | +33.60% | +0.48% | -0.17% |
| SIMPLE_MOM_20_60 | 1082 | +40.57% | -0.07% | -0.49% | -0.60% | -99.99% | +56.84% | +33.46% | +0.18% | -0.47% |
| RANDOM_LIQUID | 1232 | +50.73% | +0.50% | -0.09% | -0.14% | -98.22% | +35.15% | +21.19% | +0.75% | +0.10% |
| SPY_BUY_HOLD | 1 | +100.00% | +17.17% | n/a | n/a | +0.00% | +0.00% | +0.00% | +17.42% | +16.77% |
| QQQ_BUY_HOLD | 1 | +100.00% | +20.02% | n/a | n/a | +0.00% | +0.00% | +0.00% | +20.27% | +19.62% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2025-03:1, 2025-05:1, 2025-06:1, 2025-08:1, 2025-09:4, 2025-12:1, 2026-01:1] · regimes [spy_above_ma200=10@+1.18%] · themes [unknown:5, other:2, semiconductors:2, biotech_healthcare:1]
- SNIPER_NO_ATR_CONTRACTION: months [2025-01:5, 2025-02:19, 2025-03:3, 2025-05:2, 2025-06:10, 2025-07:19, 2025-08:11, 2025-09:21, 2025-10:17, 2025-11:17, 2025-12:7, 2026-01:3] · regimes [spy_above_ma200=134@-0.07%] · themes [other:68, unknown:28, semiconductors:24, biotech_healthcare:14]
- PROD_VOYAGER_CURRENT: months [2025-01:52, 2025-02:35, 2025-03:79, 2025-04:79, 2025-05:30, 2025-06:9, 2025-07:17, 2025-08:12, 2025-09:23, 2025-10:41, 2025-11:27, 2025-12:51, 2026-01:135] · regimes [spy_above_ma200=423@+0.75%, spy_below_ma200=167@-0.54%] · themes [other:313, unknown:99, biotech_healthcare:86, space_aerospace:69, semiconductors:23]
- CORRECTION_LEADER_RECLAIM: months [2025-01:40, 2025-02:37, 2025-03:37, 2025-04:5, 2025-05:48, 2025-06:11, 2025-07:9, 2025-08:23, 2025-09:71, 2025-10:24, 2025-11:32, 2025-12:53, 2026-01:53] · regimes [spy_above_ma200=395@+0.63%, spy_below_ma200=48@+3.83%] · themes [other:250, biotech_healthcare:78, unknown:43, space_aerospace:23, hardware:22]
- RECALL_SHADOW_RS_MOMENTUM: months [2025-01:45, 2025-02:101, 2025-03:26, 2025-04:27, 2025-05:82, 2025-06:100, 2025-07:128, 2025-08:85, 2025-09:116, 2025-10:121, 2025-11:88, 2025-12:102, 2026-01:35] · regimes [spy_above_ma200=974@+0.61%, spy_below_ma200=82@-0.31%] · themes [other:502, semiconductors:159, memory_storage:100, space_aerospace:92, hardware:90]
- RECALL_SHADOW_PULLBACK: months [2025-01:43, 2025-02:39, 2025-03:22, 2025-04:42, 2025-05:10, 2025-06:46, 2025-07:92, 2025-08:91, 2025-09:80, 2025-10:95, 2025-11:68, 2025-12:57, 2026-01:41] · regimes [spy_above_ma200=667@+1.09%, spy_below_ma200=59@-2.85%] · themes [other:349, semiconductors:117, hardware:83, biotech_healthcare:70, unknown:46]
- POWER_TREND_EXTENSION: months [2025-01:6, 2025-02:13, 2025-05:40, 2025-06:19, 2025-07:33, 2025-08:26, 2025-09:38, 2025-10:70, 2025-11:23, 2025-12:21, 2026-01:4] · regimes [spy_above_ma200=273@+0.56%, spy_below_ma200=20@+3.55%] · themes [other:136, semiconductors:49, memory_storage:42, space_aerospace:34, hardware:32]
- QQQ_TECH_TACTICAL_SHORT: months [2025-01:23, 2025-02:8, 2025-03:33, 2025-04:74, 2025-05:1, 2025-06:2, 2025-08:20, 2025-09:14, 2025-10:8, 2025-11:52, 2025-12:22, 2026-01:131] · regimes [spy_above_ma200=312@+0.39%, spy_below_ma200=76@+0.78%] · themes [other:255, semiconductors:48, space_aerospace:44, hardware:36, memory_storage:5]
- SIMPLE_SECTOR_RS: months [2025-01:57, 2025-02:83, 2025-03:76, 2025-04:65, 2025-05:112, 2025-06:88, 2025-07:116, 2025-08:83, 2025-09:120, 2025-10:119, 2025-11:87, 2025-12:103, 2026-01:141] · regimes [spy_above_ma200=1040@+0.24%, spy_below_ma200=210@+0.21%] · themes [other:654, semiconductors:159, biotech_healthcare:140, space_aerospace:93, hardware:85]
- SIMPLE_MOM_20_60: months [2025-01:33, 2025-02:85, 2025-03:8, 2025-04:17, 2025-05:81, 2025-06:84, 2025-07:113, 2025-08:92, 2025-09:123, 2025-10:115, 2025-11:86, 2025-12:94, 2026-01:151] · regimes [spy_above_ma200=979@+0.17%, spy_below_ma200=103@-2.30%] · themes [other:580, semiconductors:139, space_aerospace:102, memory_storage:102, hardware:89]
- RANDOM_LIQUID: months [2025-01:39, 2025-02:64, 2025-03:76, 2025-04:90, 2025-05:90, 2025-06:80, 2025-07:96, 2025-08:95, 2025-09:93, 2025-10:112, 2025-11:72, 2025-12:86, 2026-01:239] · regimes [spy_above_ma200=1025@+0.35%, spy_below_ma200=207@+1.23%] · themes [other:712, unknown:192, biotech_healthcare:110, semiconductors:98, hardware:55]

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

### recent_60_trading_days

- Dates: 2026-03-18 to 2026-06-11
- Signal dates: 60 (sampled: False, ticker-days: 8400, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 3 | +66.67% | +4.42% | +3.53% | +3.02% | -6.25% | +33.33% | +66.67% | +4.67% | +4.02% |
| SNIPER_NO_ATR_CONTRACTION | 24 | +54.17% | +2.11% | +1.25% | +0.33% | -22.75% | +45.83% | +45.83% | +2.36% | +1.71% |
| PROD_VOYAGER_CURRENT | 70 | +84.29% | +3.21% | +0.22% | -0.56% | -14.44% | +8.57% | +21.43% | +3.46% | +2.81% |
| CORRECTION_LEADER_RECLAIM | 27 | +51.85% | +0.27% | -1.65% | -2.28% | -22.32% | +40.74% | +29.63% | +0.52% | -0.13% |
| RECALL_SHADOW_RS_MOMENTUM | 253 | +46.25% | +1.15% | +0.46% | -0.00% | -82.13% | +52.96% | +45.45% | +1.40% | +0.75% |
| RECALL_SHADOW_PULLBACK | 105 | +50.48% | +1.39% | -0.09% | -0.88% | -53.67% | +40.00% | +35.24% | +1.64% | +0.99% |
| POWER_TREND_EXTENSION | 198 | +48.48% | +1.33% | +0.51% | -0.15% | -64.93% | +51.01% | +44.44% | +1.58% | +0.93% |
| QQQ_TECH_TACTICAL_SHORT | 33 | +12.12% | -3.62% | -2.21% | -1.97% | -71.68% | +69.70% | +12.12% | -3.33% | -4.09% |
| SIMPLE_SECTOR_RS | 255 | +41.57% | +0.13% | -0.62% | -1.08% | -83.76% | +57.25% | +37.65% | +0.38% | -0.27% |
| SIMPLE_MOM_20_60 | 254 | +38.58% | -0.35% | -1.02% | -1.45% | -89.51% | +60.24% | +34.65% | -0.10% | -0.75% |
| RANDOM_LIQUID | 252 | +54.37% | +1.31% | -0.37% | -1.45% | -56.73% | +34.92% | +29.37% | +1.56% | +0.91% |
| SPY_BUY_HOLD | 1 | +100.00% | +12.36% | n/a | n/a | +0.00% | +0.00% | +0.00% | +12.61% | +11.96% |
| QQQ_BUY_HOLD | 1 | +100.00% | +21.55% | n/a | n/a | +0.00% | +0.00% | +0.00% | +21.80% | +21.15% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2026-05:3] · regimes [spy_above_ma200=3@+4.42%] · themes [other:3]
- SNIPER_NO_ATR_CONTRACTION: months [2026-04:4, 2026-05:16, 2026-06:4] · regimes [spy_above_ma200=24@+2.11%] · themes [other:12, semiconductors:11, unknown:1]
- PROD_VOYAGER_CURRENT: months [2026-03:7, 2026-04:63] · regimes [spy_above_ma200=19@+3.20%, spy_below_ma200=51@+3.21%] · themes [unknown:29, other:21, semiconductors:8, space_aerospace:5, memory_storage:4]
- CORRECTION_LEADER_RECLAIM: months [2026-03:5, 2026-04:22] · regimes [spy_above_ma200=17@-0.06%, spy_below_ma200=10@+0.83%] · themes [other:16, semiconductors:5, unknown:3, hardware:2, memory_storage:1]
- RECALL_SHADOW_RS_MOMENTUM: months [2026-03:43, 2026-04:95, 2026-05:100, 2026-06:15] · regimes [spy_above_ma200=193@+1.81%, spy_below_ma200=60@-0.98%] · themes [semiconductors:113, other:69, memory_storage:29, space_aerospace:16, hardware:15]
- RECALL_SHADOW_PULLBACK: months [2026-03:18, 2026-04:35, 2026-05:30, 2026-06:22] · regimes [spy_above_ma200=70@+1.08%, spy_below_ma200=35@+1.99%] · themes [other:64, semiconductors:14, hardware:11, unknown:10, biotech_healthcare:3]
- POWER_TREND_EXTENSION: months [2026-03:21, 2026-04:75, 2026-05:86, 2026-06:16] · regimes [spy_above_ma200=172@+2.01%, spy_below_ma200=26@-3.17%] · themes [semiconductors:105, other:57, space_aerospace:18, hardware:13, memory_storage:3]
- QQQ_TECH_TACTICAL_SHORT: months [2026-03:9, 2026-04:24] · regimes [spy_above_ma200=3@-4.53%, spy_below_ma200=30@-3.53%] · themes [semiconductors:15, hardware:7, memory_storage:5, space_aerospace:4, other:2]
- SIMPLE_SECTOR_RS: months [2026-03:43, 2026-04:99, 2026-05:100, 2026-06:13] · regimes [spy_above_ma200=195@+0.41%, spy_below_ma200=60@-0.78%] · themes [semiconductors:102, other:82, biotech_healthcare:20, memory_storage:19, space_aerospace:18]
- SIMPLE_MOM_20_60: months [2026-03:43, 2026-04:98, 2026-05:102, 2026-06:11] · regimes [spy_above_ma200=194@-0.13%, spy_below_ma200=60@-1.04%] · themes [semiconductors:138, other:63, memory_storage:22, biotech_healthcare:12, unknown:11]
- RANDOM_LIQUID: months [2026-03:20, 2026-04:104, 2026-05:98, 2026-06:30] · regimes [spy_above_ma200=192@+0.88%, spy_below_ma200=60@+2.69%] · themes [other:132, semiconductors:38, unknown:27, biotech_healthcare:21, space_aerospace:13]

### rolling_3m_2024-01

- Dates: 2024-01-02 to 2024-04-02
- Signal dates: 63 (sampled: False, ticker-days: 8316, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 5 | +100.00% | +2.46% | +1.09% | +1.55% | +0.00% | +0.00% | +0.00% | +2.71% | +2.06% |
| SNIPER_NO_ATR_CONTRACTION | 75 | +64.00% | +1.47% | +0.30% | +0.56% | -32.10% | +20.00% | +18.67% | +1.72% | +1.07% |
| PROD_VOYAGER_CURRENT | 144 | +59.72% | +0.49% | -0.92% | -1.06% | -34.32% | +15.97% | +7.64% | +0.74% | +0.09% |
| CORRECTION_LEADER_RECLAIM | 64 | +68.75% | +2.07% | +1.21% | +1.10% | -12.03% | +12.50% | +14.06% | +2.32% | +1.67% |
| RECALL_SHADOW_RS_MOMENTUM | 315 | +48.25% | +0.53% | -0.10% | +0.05% | -71.88% | +46.35% | +31.11% | +0.78% | +0.13% |
| RECALL_SHADOW_PULLBACK | 280 | +57.50% | +1.34% | +0.22% | +0.26% | -55.35% | +21.07% | +15.00% | +1.59% | +0.94% |
| POWER_TREND_EXTENSION | 39 | +43.59% | +0.08% | +0.01% | +0.30% | -40.33% | +51.28% | +28.21% | +0.33% | -0.32% |
| QQQ_TECH_TACTICAL_SHORT | 55 | +23.64% | -3.30% | -2.11% | -1.20% | -86.14% | +69.09% | +7.27% | -3.00% | -3.79% |
| SIMPLE_SECTOR_RS | 315 | +47.62% | +0.67% | +0.13% | +0.25% | -71.77% | +47.94% | +33.97% | +0.92% | +0.27% |
| SIMPLE_MOM_20_60 | 314 | +44.59% | +0.27% | -0.28% | -0.15% | -80.17% | +50.96% | +32.48% | +0.52% | -0.13% |
| RANDOM_LIQUID | 315 | +53.33% | +0.52% | -0.45% | -0.35% | -70.14% | +27.30% | +15.24% | +0.77% | +0.12% |
| SPY_BUY_HOLD | 1 | +100.00% | +10.39% | n/a | n/a | +0.00% | +0.00% | +0.00% | +10.64% | +9.99% |
| QQQ_BUY_HOLD | 1 | +100.00% | +10.19% | n/a | n/a | +0.00% | +0.00% | +0.00% | +10.44% | +9.79% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2024-01:2, 2024-02:1, 2024-03:2] · regimes [spy_above_ma200=5@+2.46%] · themes [other:2, unknown:2, semiconductors:1]
- SNIPER_NO_ATR_CONTRACTION: months [2024-01:19, 2024-02:26, 2024-03:26, 2024-04:4] · regimes [spy_above_ma200=75@+1.47%] · themes [semiconductors:31, other:24, unknown:16, biotech_healthcare:4]
- PROD_VOYAGER_CURRENT: months [2024-01:45, 2024-02:48, 2024-03:31, 2024-04:20] · regimes [spy_above_ma200=144@+0.49%] · themes [other:81, unknown:42, space_aerospace:13, biotech_healthcare:5, semiconductors:3]
- CORRECTION_LEADER_RECLAIM: months [2024-01:10, 2024-02:8, 2024-03:23, 2024-04:23] · regimes [spy_above_ma200=64@+2.07%] · themes [other:38, unknown:11, biotech_healthcare:7, semiconductors:6, space_aerospace:2]
- RECALL_SHADOW_RS_MOMENTUM: months [2024-01:71, 2024-02:104, 2024-03:100, 2024-04:40] · regimes [spy_above_ma200=315@+0.53%] · themes [other:114, semiconductors:69, unknown:56, biotech_healthcare:43, hardware:23]
- RECALL_SHADOW_PULLBACK: months [2024-01:57, 2024-02:92, 2024-03:81, 2024-04:50] · regimes [spy_above_ma200=280@+1.34%] · themes [other:154, unknown:48, semiconductors:40, biotech_healthcare:19, space_aerospace:10]
- POWER_TREND_EXTENSION: months [2024-01:7, 2024-02:13, 2024-03:15, 2024-04:4] · regimes [spy_above_ma200=39@+0.08%] · themes [hardware:19, other:9, semiconductors:6, memory_storage:5]
- QQQ_TECH_TACTICAL_SHORT: months [2024-01:33, 2024-02:6, 2024-03:9, 2024-04:7] · regimes [spy_above_ma200=55@-3.30%] · themes [other:50, semiconductors:5]
- SIMPLE_SECTOR_RS: months [2024-01:75, 2024-02:105, 2024-03:92, 2024-04:43] · regimes [spy_above_ma200=315@+0.67%] · themes [other:95, unknown:80, hardware:48, semiconductors:43, biotech_healthcare:43]
- SIMPLE_MOM_20_60: months [2024-01:76, 2024-02:105, 2024-03:99, 2024-04:34] · regimes [spy_above_ma200=314@+0.27%] · themes [other:109, unknown:58, semiconductors:48, hardware:48, biotech_healthcare:47]
- RANDOM_LIQUID: months [2024-01:61, 2024-02:104, 2024-03:96, 2024-04:54] · regimes [spy_above_ma200=315@+0.52%] · themes [other:187, unknown:63, biotech_healthcare:29, semiconductors:24, space_aerospace:7]

### rolling_3m_2024-04

- Dates: 2024-04-03 to 2024-07-02
- Signal dates: 63 (sampled: False, ticker-days: 8316, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 3 | +100.00% | +7.36% | +6.32% | +5.82% | +0.00% | +0.00% | +66.67% | +7.61% | +6.96% |
| SNIPER_NO_ATR_CONTRACTION | 29 | +48.28% | +0.52% | -0.44% | -0.75% | -21.08% | +17.24% | +6.90% | +0.77% | +0.12% |
| PROD_VOYAGER_CURRENT | 137 | +37.96% | -0.88% | -1.64% | -2.17% | -73.20% | +17.52% | +2.92% | -0.63% | -1.28% |
| CORRECTION_LEADER_RECLAIM | 114 | +49.12% | +0.74% | -0.06% | -0.37% | -65.35% | +19.30% | +14.04% | +0.99% | +0.34% |
| RECALL_SHADOW_RS_MOMENTUM | 302 | +40.73% | -0.60% | -1.22% | -1.54% | -93.38% | +48.34% | +19.87% | -0.35% | -1.00% |
| RECALL_SHADOW_PULLBACK | 198 | +40.40% | -0.32% | -1.16% | -1.55% | -86.35% | +31.31% | +15.66% | -0.07% | -0.72% |
| POWER_TREND_EXTENSION | 12 | +25.00% | -2.25% | -2.86% | -2.99% | -32.11% | +75.00% | +25.00% | -2.00% | -2.65% |
| QQQ_TECH_TACTICAL_SHORT | 61 | +42.62% | -0.27% | -0.78% | -0.85% | -78.05% | +54.10% | +24.59% | +0.03% | -0.76% |
| SIMPLE_SECTOR_RS | 315 | +39.68% | -0.50% | -1.15% | -1.51% | -92.78% | +52.06% | +25.40% | -0.25% | -0.90% |
| SIMPLE_MOM_20_60 | 282 | +39.36% | -0.65% | -1.22% | -1.59% | -91.19% | +49.29% | +20.21% | -0.40% | -1.05% |
| RANDOM_LIQUID | 315 | +47.62% | +0.09% | -0.85% | -1.37% | -88.97% | +33.02% | +15.87% | +0.34% | -0.31% |
| SPY_BUY_HOLD | 1 | +100.00% | +4.96% | n/a | n/a | +0.00% | +0.00% | +0.00% | +5.21% | +4.56% |
| QQQ_BUY_HOLD | 1 | +100.00% | +9.03% | n/a | n/a | +0.00% | +0.00% | +0.00% | +9.28% | +8.63% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2024-05:1, 2024-06:2] · regimes [spy_above_ma200=3@+7.36%] · themes [semiconductors:2, other:1]
- SNIPER_NO_ATR_CONTRACTION: months [2024-04:2, 2024-05:9, 2024-06:12, 2024-07:6] · regimes [spy_above_ma200=29@+0.52%] · themes [other:12, semiconductors:10, unknown:6, biotech_healthcare:1]
- PROD_VOYAGER_CURRENT: months [2024-04:39, 2024-05:51, 2024-06:35, 2024-07:12] · regimes [spy_above_ma200=137@-0.88%] · themes [other:80, unknown:44, biotech_healthcare:7, semiconductors:4, space_aerospace:2]
- CORRECTION_LEADER_RECLAIM: months [2024-04:43, 2024-05:71] · regimes [spy_above_ma200=114@+0.74%] · themes [other:77, space_aerospace:17, unknown:14, semiconductors:3, biotech_healthcare:3]
- RECALL_SHADOW_RS_MOMENTUM: months [2024-04:58, 2024-05:121, 2024-06:85, 2024-07:38] · regimes [spy_above_ma200=302@-0.60%] · themes [other:142, unknown:44, biotech_healthcare:41, semiconductors:38, space_aerospace:22]
- RECALL_SHADOW_PULLBACK: months [2024-04:51, 2024-05:68, 2024-06:52, 2024-07:27] · regimes [spy_above_ma200=198@-0.32%] · themes [other:115, semiconductors:25, unknown:17, space_aerospace:16, biotech_healthcare:14]
- POWER_TREND_EXTENSION: months [2024-04:1, 2024-05:3, 2024-06:8] · regimes [spy_above_ma200=12@-2.25%] · themes [other:7, semiconductors:4, memory_storage:1]
- QQQ_TECH_TACTICAL_SHORT: months [2024-04:45, 2024-05:15, 2024-06:1] · regimes [spy_above_ma200=61@-0.27%] · themes [other:24, semiconductors:18, hardware:15, memory_storage:3, space_aerospace:1]
- SIMPLE_SECTOR_RS: months [2024-04:61, 2024-05:130, 2024-06:84, 2024-07:40] · regimes [spy_above_ma200=315@-0.50%] · themes [other:142, unknown:63, biotech_healthcare:58, semiconductors:36, memory_storage:12]
- SIMPLE_MOM_20_60: months [2024-04:54, 2024-05:104, 2024-06:85, 2024-07:39] · regimes [spy_above_ma200=282@-0.65%] · themes [other:136, biotech_healthcare:51, unknown:34, semiconductors:30, space_aerospace:19]
- RANDOM_LIQUID: months [2024-04:61, 2024-05:117, 2024-06:82, 2024-07:55] · regimes [spy_above_ma200=315@+0.09%] · themes [other:185, unknown:73, semiconductors:28, biotech_healthcare:16, space_aerospace:6]

### rolling_3m_2024-07

- Dates: 2024-07-03 to 2024-10-01
- Signal dates: 63 (sampled: False, ticker-days: 8316, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 8 | +62.50% | +0.64% | -0.74% | -1.00% | -1.97% | +0.00% | +0.00% | +0.89% | +0.24% |
| SNIPER_NO_ATR_CONTRACTION | 36 | +41.67% | +0.04% | -1.18% | -0.91% | -21.51% | +11.11% | +2.78% | +0.29% | -0.36% |
| PROD_VOYAGER_CURRENT | 172 | +48.26% | +0.84% | -0.46% | -0.59% | -54.77% | +16.86% | +12.79% | +1.09% | +0.44% |
| CORRECTION_LEADER_RECLAIM | 206 | +54.85% | +1.00% | +0.34% | +0.47% | -73.93% | +24.27% | +17.48% | +1.25% | +0.60% |
| RECALL_SHADOW_RS_MOMENTUM | 306 | +38.89% | -0.65% | -1.11% | -0.60% | -96.11% | +45.10% | +19.28% | -0.40% | -1.05% |
| RECALL_SHADOW_PULLBACK | 158 | +46.84% | -0.17% | -0.45% | +0.03% | -92.70% | +35.44% | +15.82% | +0.08% | -0.57% |
| POWER_TREND_EXTENSION | 27 | +55.56% | +2.03% | +1.25% | +1.29% | -22.77% | +37.04% | +37.04% | +2.28% | +1.63% |
| QQQ_TECH_TACTICAL_SHORT | 56 | +39.29% | +0.09% | +0.27% | -0.10% | -83.87% | +58.93% | +39.29% | +0.37% | -0.38% |
| SIMPLE_SECTOR_RS | 315 | +35.56% | -0.94% | -1.19% | -0.64% | -98.87% | +51.43% | +20.63% | -0.69% | -1.34% |
| SIMPLE_MOM_20_60 | 296 | +35.47% | -0.79% | -0.99% | -0.50% | -97.81% | +54.05% | +24.32% | -0.54% | -1.19% |
| RANDOM_LIQUID | 315 | +50.79% | +0.46% | +0.18% | +0.81% | -92.71% | +32.38% | +19.05% | +0.71% | +0.06% |
| SPY_BUY_HOLD | 1 | +100.00% | +3.12% | n/a | n/a | +0.00% | +0.00% | +0.00% | +3.37% | +2.72% |
| QQQ_BUY_HOLD | 1 | +0.00% | -2.27% | n/a | n/a | -2.27% | +0.00% | +0.00% | -2.02% | -2.67% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2024-07:1, 2024-09:7] · regimes [spy_above_ma200=8@+0.64%] · themes [unknown:6, other:2]
- SNIPER_NO_ATR_CONTRACTION: months [2024-07:12, 2024-08:10, 2024-09:9, 2024-10:5] · regimes [spy_above_ma200=36@+0.04%] · themes [unknown:20, other:14, semiconductors:1, biotech_healthcare:1]
- PROD_VOYAGER_CURRENT: months [2024-07:16, 2024-08:44, 2024-09:65, 2024-10:47] · regimes [spy_above_ma200=172@+0.84%] · themes [other:78, unknown:68, biotech_healthcare:21, semiconductors:3, memory_storage:1]
- CORRECTION_LEADER_RECLAIM: months [2024-07:12, 2024-08:99, 2024-09:78, 2024-10:17] · regimes [spy_above_ma200=206@+1.00%] · themes [other:123, unknown:33, biotech_healthcare:33, space_aerospace:16, semiconductors:1]
- RECALL_SHADOW_RS_MOMENTUM: months [2024-07:74, 2024-08:90, 2024-09:102, 2024-10:40] · regimes [spy_above_ma200=306@-0.65%] · themes [other:195, biotech_healthcare:43, space_aerospace:31, unknown:21, semiconductors:16]
- RECALL_SHADOW_PULLBACK: months [2024-07:37, 2024-08:46, 2024-09:54, 2024-10:21] · regimes [spy_above_ma200=158@-0.17%] · themes [other:88, biotech_healthcare:31, unknown:28, space_aerospace:8, semiconductors:3]
- POWER_TREND_EXTENSION: months [2024-07:4, 2024-08:4, 2024-09:13, 2024-10:6] · regimes [spy_above_ma200=27@+2.03%] · themes [other:21, semiconductors:3, space_aerospace:2, memory_storage:1]
- QQQ_TECH_TACTICAL_SHORT: months [2024-07:23, 2024-08:30, 2024-09:3] · regimes [spy_above_ma200=56@+0.09%] · themes [semiconductors:39, other:13, space_aerospace:2, memory_storage:1, hardware:1]
- SIMPLE_SECTOR_RS: months [2024-07:69, 2024-08:106, 2024-09:101, 2024-10:39] · regimes [spy_above_ma200=315@-0.94%] · themes [other:242, unknown:34, biotech_healthcare:20, semiconductors:18, space_aerospace:1]
- SIMPLE_MOM_20_60: months [2024-07:77, 2024-08:86, 2024-09:93, 2024-10:40] · regimes [spy_above_ma200=296@-0.79%] · themes [other:206, biotech_healthcare:43, semiconductors:24, unknown:14, space_aerospace:9]
- RANDOM_LIQUID: months [2024-07:63, 2024-08:103, 2024-09:99, 2024-10:50] · regimes [spy_above_ma200=315@+0.46%] · themes [other:191, unknown:58, biotech_healthcare:29, semiconductors:25, space_aerospace:7]

### rolling_3m_2024-10

- Dates: 2024-10-02 to 2024-12-31
- Signal dates: 63 (sampled: False, ticker-days: 8316, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 7 | +42.86% | +0.64% | +0.44% | +0.32% | -7.01% | +14.29% | +0.00% | +0.89% | +0.24% |
| SNIPER_NO_ATR_CONTRACTION | 57 | +45.61% | +0.15% | +0.16% | +0.16% | -39.04% | +28.07% | +10.53% | +0.40% | -0.25% |
| PROD_VOYAGER_CURRENT | 203 | +50.74% | +0.82% | +0.42% | +0.07% | -82.31% | +15.27% | +12.81% | +1.07% | +0.42% |
| CORRECTION_LEADER_RECLAIM | 82 | +64.63% | +2.72% | +1.69% | +1.34% | -27.47% | +21.95% | +28.05% | +2.97% | +2.32% |
| RECALL_SHADOW_RS_MOMENTUM | 313 | +52.08% | +1.23% | +0.95% | +0.70% | -96.49% | +37.06% | +30.67% | +1.48% | +0.83% |
| RECALL_SHADOW_PULLBACK | 239 | +56.07% | +1.30% | +0.82% | +0.30% | -91.79% | +25.94% | +24.27% | +1.55% | +0.90% |
| POWER_TREND_EXTENSION | 90 | +47.78% | +0.92% | +0.51% | +0.04% | -72.70% | +42.22% | +31.11% | +1.18% | +0.53% |
| QQQ_TECH_TACTICAL_SHORT | 43 | +34.88% | -1.57% | +0.41% | +0.85% | -70.74% | +62.79% | +18.60% | -1.29% | -2.05% |
| SIMPLE_SECTOR_RS | 313 | +44.73% | +0.28% | +0.02% | -0.22% | -95.26% | +49.52% | +30.03% | +0.53% | -0.12% |
| SIMPLE_MOM_20_60 | 314 | +53.50% | +1.55% | +1.34% | +1.13% | -91.48% | +39.49% | +36.94% | +1.80% | +1.15% |
| RANDOM_LIQUID | 315 | +46.03% | +0.35% | -0.02% | -0.37% | -89.32% | +27.62% | +19.37% | +0.60% | -0.05% |
| SPY_BUY_HOLD | 1 | +100.00% | +3.39% | n/a | n/a | +0.00% | +0.00% | +0.00% | +3.64% | +2.99% |
| QQQ_BUY_HOLD | 1 | +100.00% | +6.49% | n/a | n/a | +0.00% | +0.00% | +0.00% | +6.74% | +6.09% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2024-10:4, 2024-11:2, 2024-12:1] · regimes [spy_above_ma200=7@+0.64%] · themes [other:3, unknown:3, semiconductors:1]
- SNIPER_NO_ATR_CONTRACTION: months [2024-10:6, 2024-11:28, 2024-12:20, 2025-01:3] · regimes [spy_above_ma200=57@+0.15%] · themes [other:33, unknown:14, semiconductors:8, biotech_healthcare:2]
- PROD_VOYAGER_CURRENT: months [2024-10:43, 2024-11:67, 2024-12:58, 2025-01:27, 2025-12:8] · regimes [spy_above_ma200=203@+0.82%] · themes [other:137, unknown:53, biotech_healthcare:12, space_aerospace:1]
- CORRECTION_LEADER_RECLAIM: months [2024-10:6, 2024-11:38, 2024-12:19, 2025-01:17, 2025-12:2] · regimes [spy_above_ma200=82@+2.72%] · themes [other:63, unknown:10, semiconductors:4, biotech_healthcare:3, space_aerospace:2]
- RECALL_SHADOW_RS_MOMENTUM: months [2024-10:76, 2024-11:92, 2024-12:116, 2025-01:28, 2025-12:1] · regimes [spy_above_ma200=313@+1.23%] · themes [other:208, semiconductors:40, unknown:26, space_aerospace:21, biotech_healthcare:18]
- RECALL_SHADOW_PULLBACK: months [2024-10:65, 2024-11:56, 2024-12:89, 2025-01:26, 2025-12:3] · regimes [spy_above_ma200=239@+1.30%] · themes [other:171, unknown:39, space_aerospace:15, biotech_healthcare:7, semiconductors:7]
- POWER_TREND_EXTENSION: months [2024-10:4, 2024-11:23, 2024-12:55, 2025-01:8] · regimes [spy_above_ma200=90@+0.92%] · themes [other:65, semiconductors:18, space_aerospace:5, hardware:2]
- QQQ_TECH_TACTICAL_SHORT: months [2024-10:4, 2024-11:16, 2024-12:5, 2025-01:8, 2025-04:5, 2025-12:4, 2026-01:1] · regimes [spy_above_ma200=43@-1.57%] · themes [other:33, semiconductors:8, memory_storage:1, space_aerospace:1]
- SIMPLE_SECTOR_RS: months [2024-10:92, 2024-11:80, 2024-12:113, 2025-01:25, 2025-12:2, 2026-01:1] · regimes [spy_above_ma200=313@+0.28%] · themes [other:171, semiconductors:50, biotech_healthcare:34, unknown:34, space_aerospace:11]
- SIMPLE_MOM_20_60: months [2024-10:78, 2024-11:97, 2024-12:113, 2025-01:23, 2025-12:2, 2026-01:1] · regimes [spy_above_ma200=314@+1.55%] · themes [other:254, semiconductors:21, biotech_healthcare:16, space_aerospace:12, unknown:11]
- RANDOM_LIQUID: months [2024-10:68, 2024-11:97, 2024-12:105, 2025-01:32, 2025-04:2, 2025-12:11] · regimes [spy_above_ma200=315@+0.35%] · themes [other:183, unknown:70, biotech_healthcare:27, semiconductors:23, space_aerospace:8]

### rolling_3m_2025-01

- Dates: 2025-01-02 to 2025-04-03
- Signal dates: 63 (sampled: False, ticker-days: 8308, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 1 | +100.00% | +0.60% | +6.20% | +6.84% | +0.00% | +0.00% | +0.00% | +0.85% | +0.20% |
| SNIPER_NO_ATR_CONTRACTION | 27 | +62.96% | +1.04% | +1.36% | +1.24% | -23.33% | +22.22% | +3.70% | +1.29% | +0.64% |
| PROD_VOYAGER_CURRENT | 259 | +33.20% | -1.57% | +0.56% | +1.20% | -99.95% | +53.67% | +10.81% | -1.32% | -1.97% |
| CORRECTION_LEADER_RECLAIM | 114 | +45.61% | -0.08% | +0.56% | +0.82% | -75.96% | +40.35% | +16.67% | +0.17% | -0.48% |
| RECALL_SHADOW_RS_MOMENTUM | 186 | +33.33% | -1.74% | -1.10% | -0.97% | -98.73% | +59.14% | +14.52% | -1.49% | -2.14% |
| RECALL_SHADOW_PULLBACK | 136 | +41.91% | -0.73% | +0.50% | +0.89% | -96.63% | +50.74% | +16.18% | -0.48% | -1.13% |
| POWER_TREND_EXTENSION | 19 | +10.53% | -4.51% | -4.63% | -4.83% | -59.06% | +78.95% | +5.26% | -4.26% | -4.91% |
| QQQ_TECH_TACTICAL_SHORT | 187 | +55.08% | +2.39% | -1.55% | -2.35% | -58.54% | +43.85% | +51.34% | +2.67% | +1.94% |
| SIMPLE_SECTOR_RS | 315 | +24.13% | -2.81% | -1.41% | -1.10% | -99.99% | +70.48% | +14.29% | -2.56% | -3.21% |
| SIMPLE_MOM_20_60 | 196 | +20.41% | -3.21% | -1.79% | -1.56% | -99.88% | +75.51% | +13.27% | -2.96% | -3.61% |
| RANDOM_LIQUID | 303 | +42.57% | -0.59% | +1.56% | +2.22% | -98.22% | +49.17% | +20.46% | -0.34% | -0.99% |
| SPY_BUY_HOLD | 1 | +0.00% | -8.63% | n/a | n/a | -8.63% | +0.00% | +0.00% | -8.38% | -9.03% |
| QQQ_BUY_HOLD | 1 | +0.00% | -12.33% | n/a | n/a | -12.33% | +0.00% | +0.00% | -12.08% | -12.73% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2025-03:1] · regimes [spy_above_ma200=1@+0.60%] · themes [biotech_healthcare:1]
- SNIPER_NO_ATR_CONTRACTION: months [2025-01:5, 2025-02:19, 2025-03:3] · regimes [spy_above_ma200=27@+1.04%] · themes [other:20, unknown:5, biotech_healthcare:2]
- PROD_VOYAGER_CURRENT: months [2025-01:52, 2025-02:35, 2025-03:79, 2025-04:42, 2026-01:51] · regimes [spy_above_ma200=175@-1.03%, spy_below_ma200=84@-2.69%] · themes [other:156, biotech_healthcare:52, unknown:37, space_aerospace:9, semiconductors:5]
- CORRECTION_LEADER_RECLAIM: months [2025-01:40, 2025-02:37, 2025-03:37] · regimes [spy_above_ma200=114@-0.08%] · themes [other:70, unknown:15, biotech_healthcare:15, space_aerospace:8, semiconductors:6]
- RECALL_SHADOW_RS_MOMENTUM: months [2025-01:45, 2025-02:101, 2025-03:26, 2025-04:14] · regimes [spy_above_ma200=166@-1.32%, spy_below_ma200=20@-5.21%] · themes [other:119, biotech_healthcare:26, semiconductors:24, space_aerospace:9, unknown:8]
- RECALL_SHADOW_PULLBACK: months [2025-01:43, 2025-02:39, 2025-03:22, 2025-04:32] · regimes [spy_above_ma200=96@+0.67%, spy_below_ma200=40@-4.07%] · themes [other:91, unknown:15, biotech_healthcare:13, space_aerospace:11, semiconductors:6]
- POWER_TREND_EXTENSION: months [2025-01:6, 2025-02:13] · regimes [spy_above_ma200=19@-4.51%] · themes [other:13, semiconductors:6]
- QQQ_TECH_TACTICAL_SHORT: months [2025-01:23, 2025-02:8, 2025-03:33, 2025-04:74, 2026-01:49] · regimes [spy_above_ma200=125@+2.52%, spy_below_ma200=62@+2.14%] · themes [other:166, semiconductors:14, space_aerospace:7]
- SIMPLE_SECTOR_RS: months [2025-01:57, 2025-02:83, 2025-03:76, 2025-04:26, 2026-01:73] · regimes [spy_above_ma200=230@-3.02%, spy_below_ma200=85@-2.21%] · themes [other:199, biotech_healthcare:55, semiconductors:35, unknown:17, memory_storage:7]
- SIMPLE_MOM_20_60: months [2025-01:33, 2025-02:85, 2025-03:8, 2025-04:7, 2026-01:63] · regimes [spy_above_ma200=169@-2.74%, spy_below_ma200=27@-6.12%] · themes [other:158, semiconductors:22, unknown:9, biotech_healthcare:6, space_aerospace:1]
- RANDOM_LIQUID: months [2025-01:39, 2025-02:64, 2025-03:76, 2025-04:44, 2026-01:80] · regimes [spy_above_ma200=220@-0.26%, spy_below_ma200=83@-1.48%] · themes [other:172, unknown:58, biotech_healthcare:32, semiconductors:24, space_aerospace:9]

### rolling_3m_2025-04

- Dates: 2025-04-04 to 2025-07-07
- Signal dates: 63 (sampled: False, ticker-days: 8662, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 2 | +50.00% | -0.92% | -3.19% | -4.16% | -3.42% | +0.00% | +0.00% | -0.67% | -1.32% |
| SNIPER_NO_ATR_CONTRACTION | 26 | +34.62% | -1.28% | -2.58% | -2.62% | -32.89% | +34.62% | +7.69% | -1.03% | -1.68% |
| PROD_VOYAGER_CURRENT | 101 | +59.41% | +1.61% | -3.26% | -4.86% | -31.62% | +23.76% | +21.78% | +1.86% | +1.21% |
| CORRECTION_LEADER_RECLAIM | 73 | +76.71% | +3.97% | +1.09% | +0.09% | -29.70% | +15.07% | +36.99% | +4.22% | +3.57% |
| RECALL_SHADOW_RS_MOMENTUM | 250 | +51.60% | +0.83% | -0.54% | -0.93% | -77.26% | +39.20% | +25.60% | +1.08% | +0.43% |
| RECALL_SHADOW_PULLBACK | 111 | +63.06% | +1.81% | +0.09% | -0.23% | -55.26% | +25.23% | +21.62% | +2.06% | +1.41% |
| POWER_TREND_EXTENSION | 75 | +53.33% | +1.27% | +0.08% | -0.30% | -47.55% | +40.00% | +33.33% | +1.52% | +0.87% |
| QQQ_TECH_TACTICAL_SHORT | 23 | +26.09% | -2.51% | +6.81% | +10.06% | -56.33% | +69.57% | +8.70% | -2.22% | -2.98% |
| SIMPLE_SECTOR_RS | 315 | +49.84% | +1.20% | -0.93% | -1.61% | -63.69% | +46.98% | +39.37% | +1.45% | +0.80% |
| SIMPLE_MOM_20_60 | 266 | +39.47% | -0.56% | -2.88% | -3.59% | -91.22% | +57.14% | +27.07% | -0.31% | -0.96% |
| RANDOM_LIQUID | 311 | +58.52% | +1.54% | -1.42% | -2.27% | -53.08% | +28.30% | +23.47% | +1.79% | +1.14% |
| SPY_BUY_HOLD | 1 | +100.00% | +27.00% | n/a | n/a | +0.00% | +0.00% | +0.00% | +27.25% | +26.60% |
| QQQ_BUY_HOLD | 1 | +100.00% | +34.98% | n/a | n/a | +0.00% | +0.00% | +0.00% | +35.23% | +34.58% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2025-05:1, 2025-06:1] · regimes [spy_above_ma200=2@-0.92%] · themes [other:1, unknown:1]
- SNIPER_NO_ATR_CONTRACTION: months [2025-05:2, 2025-06:10, 2025-07:14] · regimes [spy_above_ma200=26@-1.28%] · themes [other:16, semiconductors:9, unknown:1]
- PROD_VOYAGER_CURRENT: months [2025-04:37, 2025-05:30, 2025-06:9, 2025-07:8, 2026-01:17] · regimes [spy_above_ma200=18@+1.50%, spy_below_ma200=83@+1.63%] · themes [other:36, unknown:28, biotech_healthcare:27, space_aerospace:10]
- CORRECTION_LEADER_RECLAIM: months [2025-04:5, 2025-05:48, 2025-06:11, 2025-07:9] · regimes [spy_above_ma200=25@+4.26%, spy_below_ma200=48@+3.83%] · themes [other:53, unknown:8, biotech_healthcare:6, space_aerospace:4, memory_storage:2]
- RECALL_SHADOW_RS_MOMENTUM: months [2025-04:13, 2025-05:82, 2025-06:100, 2025-07:55] · regimes [spy_above_ma200=188@+0.69%, spy_below_ma200=62@+1.27%] · themes [other:134, space_aerospace:28, semiconductors:21, unknown:20, biotech_healthcare:20]
- RECALL_SHADOW_PULLBACK: months [2025-04:10, 2025-05:10, 2025-06:46, 2025-07:45] · regimes [spy_above_ma200=92@+2.25%, spy_below_ma200=19@-0.28%] · themes [other:52, semiconductors:19, unknown:16, hardware:11, biotech_healthcare:8]
- POWER_TREND_EXTENSION: months [2025-05:40, 2025-06:19, 2025-07:16] · regimes [spy_above_ma200=55@+0.45%, spy_below_ma200=20@+3.55%] · themes [other:43, space_aerospace:13, semiconductors:10, hardware:9]
- QQQ_TECH_TACTICAL_SHORT: months [2025-05:1, 2025-06:2, 2026-01:20] · regimes [spy_above_ma200=9@+1.75%, spy_below_ma200=14@-5.24%] · themes [other:23]
- SIMPLE_SECTOR_RS: months [2025-04:39, 2025-05:112, 2025-06:88, 2025-07:41, 2026-01:35] · regimes [spy_above_ma200=190@+0.77%, spy_below_ma200=125@+1.86%] · themes [other:201, biotech_healthcare:46, space_aerospace:25, semiconductors:24, hardware:9]
- SIMPLE_MOM_20_60: months [2025-04:10, 2025-05:81, 2025-06:84, 2025-07:41, 2026-01:50] · regimes [spy_above_ma200=190@-0.41%, spy_below_ma200=76@-0.94%] · themes [other:159, biotech_healthcare:32, space_aerospace:29, semiconductors:23, hardware:17]
- RANDOM_LIQUID: months [2025-04:46, 2025-05:90, 2025-06:80, 2025-07:52, 2026-01:43] · regimes [spy_above_ma200=187@+0.55%, spy_below_ma200=124@+3.04%] · themes [other:171, unknown:54, semiconductors:30, biotech_healthcare:29, hardware:13]

### rolling_3m_2025-07

- Dates: 2025-07-08 to 2025-10-03
- Signal dates: 63 (sampled: False, ticker-days: 8820, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 5 | +60.00% | +0.67% | -0.84% | -1.65% | -6.25% | +20.00% | +0.00% | +0.92% | +0.27% |
| SNIPER_NO_ATR_CONTRACTION | 46 | +47.83% | -0.06% | -1.16% | -1.43% | -31.24% | +19.57% | +8.70% | +0.19% | -0.46% |
| PROD_VOYAGER_CURRENT | 63 | +60.32% | +0.68% | -0.05% | -0.25% | -36.16% | +15.87% | +9.52% | +0.93% | +0.28% |
| CORRECTION_LEADER_RECLAIM | 94 | +57.45% | +1.51% | +0.26% | -0.07% | -51.15% | +22.34% | +15.96% | +1.76% | +1.11% |
| RECALL_SHADOW_RS_MOMENTUM | 315 | +58.10% | +2.54% | +2.06% | +2.02% | -65.70% | +38.41% | +44.76% | +2.79% | +2.14% |
| RECALL_SHADOW_PULLBACK | 257 | +52.92% | +0.89% | +0.27% | +0.20% | -84.02% | +26.85% | +18.68% | +1.14% | +0.49% |
| POWER_TREND_EXTENSION | 108 | +55.56% | +2.26% | +1.88% | +1.77% | -35.00% | +42.59% | +46.30% | +2.51% | +1.86% |
| QQQ_TECH_TACTICAL_SHORT | 45 | +17.78% | -3.44% | -1.52% | -0.81% | -82.59% | +77.78% | +15.56% | -3.15% | -3.91% |
| SIMPLE_SECTOR_RS | 315 | +57.78% | +2.56% | +2.10% | +2.06% | -71.58% | +40.00% | +46.35% | +2.81% | +2.16% |
| SIMPLE_MOM_20_60 | 315 | +57.78% | +2.68% | +2.29% | +2.23% | -76.46% | +40.95% | +50.79% | +2.93% | +2.28% |
| RANDOM_LIQUID | 314 | +50.64% | +0.45% | -0.53% | -0.73% | -59.52% | +30.57% | +18.79% | +0.70% | +0.05% |
| SPY_BUY_HOLD | 1 | +100.00% | +7.50% | n/a | n/a | +0.00% | +0.00% | +0.00% | +7.75% | +7.10% |
| QQQ_BUY_HOLD | 1 | +100.00% | +8.66% | n/a | n/a | +0.00% | +0.00% | +0.00% | +8.91% | +8.26% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2025-08:1, 2025-09:4] · regimes [spy_above_ma200=5@+0.67%] · themes [unknown:3, semiconductors:1, other:1]
- SNIPER_NO_ATR_CONTRACTION: months [2025-07:5, 2025-08:11, 2025-09:21, 2025-10:9] · regimes [spy_above_ma200=46@-0.06%] · themes [other:22, unknown:12, semiconductors:8, biotech_healthcare:4]
- PROD_VOYAGER_CURRENT: months [2025-07:9, 2025-08:12, 2025-09:23, 2025-10:19] · regimes [spy_above_ma200=63@+0.68%] · themes [other:45, unknown:9, space_aerospace:6, biotech_healthcare:3]
- CORRECTION_LEADER_RECLAIM: months [2025-08:23, 2025-09:71] · regimes [spy_above_ma200=94@+1.51%] · themes [other:56, hardware:18, biotech_healthcare:9, unknown:3, semiconductors:3]
- RECALL_SHADOW_RS_MOMENTUM: months [2025-07:73, 2025-08:85, 2025-09:116, 2025-10:41] · regimes [spy_above_ma200=315@+2.54%] · themes [other:136, semiconductors:68, hardware:37, memory_storage:31, space_aerospace:26]
- RECALL_SHADOW_PULLBACK: months [2025-07:47, 2025-08:91, 2025-09:80, 2025-10:39] · regimes [spy_above_ma200=257@+0.89%] · themes [other:115, semiconductors:48, hardware:48, space_aerospace:20, memory_storage:11]
- POWER_TREND_EXTENSION: months [2025-07:17, 2025-08:26, 2025-09:38, 2025-10:27] · regimes [spy_above_ma200=108@+2.26%] · themes [other:53, memory_storage:21, semiconductors:20, space_aerospace:7, hardware:7]
- QQQ_TECH_TACTICAL_SHORT: months [2025-08:20, 2025-09:14, 2026-01:11] · regimes [spy_above_ma200=45@-3.44%] · themes [other:22, hardware:8, space_aerospace:8, semiconductors:6, memory_storage:1]
- SIMPLE_SECTOR_RS: months [2025-07:75, 2025-08:83, 2025-09:120, 2025-10:36, 2026-01:1] · regimes [spy_above_ma200=315@+2.56%] · themes [other:135, semiconductors:59, hardware:45, biotech_healthcare:26, space_aerospace:21]
- SIMPLE_MOM_20_60: months [2025-07:72, 2025-08:92, 2025-09:123, 2025-10:27, 2026-01:1] · regimes [spy_above_ma200=315@+2.68%] · themes [other:147, semiconductors:65, space_aerospace:39, memory_storage:29, hardware:28]
- RANDOM_LIQUID: months [2025-07:44, 2025-08:95, 2025-09:93, 2025-10:51, 2026-01:31] · regimes [spy_above_ma200=314@+0.45%] · themes [other:181, unknown:44, biotech_healthcare:29, semiconductors:27, hardware:21]

### rolling_3m_2025-10

- Dates: 2025-10-06 to 2026-01-05
- Signal dates: 63 (sampled: False, ticker-days: 8820, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 3 | +66.67% | +6.49% | +5.89% | +6.35% | -0.04% | +0.00% | +66.67% | +6.74% | +6.09% |
| SNIPER_NO_ATR_CONTRACTION | 40 | +37.50% | +0.10% | +0.15% | +0.25% | -41.40% | +30.00% | +22.50% | +0.35% | -0.30% |
| PROD_VOYAGER_CURRENT | 169 | +64.50% | +2.58% | +1.53% | +1.71% | -26.18% | +17.16% | +31.36% | +2.83% | +2.18% |
| CORRECTION_LEADER_RECLAIM | 172 | +48.84% | +0.23% | -1.05% | -0.98% | -70.78% | +26.74% | +15.12% | +0.48% | -0.17% |
| RECALL_SHADOW_RS_MOMENTUM | 315 | +39.37% | -0.18% | -0.22% | -0.08% | -96.77% | +55.87% | +33.02% | +0.07% | -0.58% |
| RECALL_SHADOW_PULLBACK | 228 | +53.95% | +0.95% | +0.52% | +0.67% | -78.80% | +33.33% | +20.18% | +1.20% | +0.55% |
| POWER_TREND_EXTENSION | 96 | +40.62% | +0.04% | +0.23% | +0.46% | -66.33% | +57.29% | +36.46% | +0.29% | -0.36% |
| QQQ_TECH_TACTICAL_SHORT | 133 | +36.09% | -0.41% | +0.23% | +0.07% | -92.00% | +60.15% | +31.58% | -0.14% | -0.87% |
| SIMPLE_SECTOR_RS | 315 | +41.59% | +0.13% | +0.06% | +0.22% | -86.84% | +53.33% | +35.56% | +0.38% | -0.27% |
| SIMPLE_MOM_20_60 | 315 | +36.83% | -0.44% | -0.43% | -0.28% | -94.12% | +60.63% | +34.29% | -0.19% | -0.84% |
| RANDOM_LIQUID | 314 | +50.64% | +0.58% | +0.09% | +0.32% | -66.61% | +32.48% | +22.29% | +0.83% | +0.18% |
| SPY_BUY_HOLD | 1 | +100.00% | +2.31% | n/a | n/a | +0.00% | +0.00% | +0.00% | +2.56% | +1.91% |
| QQQ_BUY_HOLD | 1 | +100.00% | +1.35% | n/a | n/a | +0.00% | +0.00% | +0.00% | +1.60% | +0.95% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2025-12:1, 2026-01:2] · regimes [spy_above_ma200=3@+6.49%] · themes [semiconductors:2, unknown:1]
- SNIPER_NO_ATR_CONTRACTION: months [2025-10:8, 2025-11:17, 2025-12:7, 2026-01:8] · regimes [spy_above_ma200=40@+0.10%] · themes [other:12, unknown:12, semiconductors:8, biotech_healthcare:8]
- PROD_VOYAGER_CURRENT: months [2025-10:22, 2025-11:27, 2025-12:51, 2026-01:69] · regimes [spy_above_ma200=169@+2.58%] · themes [other:77, space_aerospace:45, unknown:25, semiconductors:18, biotech_healthcare:4]
- CORRECTION_LEADER_RECLAIM: months [2025-10:24, 2025-11:32, 2025-12:53, 2026-01:63] · regimes [spy_above_ma200=172@+0.23%] · themes [other:74, biotech_healthcare:51, unknown:17, semiconductors:11, space_aerospace:10]
- RECALL_SHADOW_RS_MOMENTUM: months [2025-10:80, 2025-11:88, 2025-12:102, 2026-01:45] · regimes [spy_above_ma200=315@-0.18%] · themes [other:115, memory_storage:63, semiconductors:46, hardware:36, space_aerospace:33]
- RECALL_SHADOW_PULLBACK: months [2025-10:56, 2025-11:68, 2025-12:57, 2026-01:47] · regimes [spy_above_ma200=228@+0.95%] · themes [other:96, biotech_healthcare:45, semiconductors:44, hardware:24, memory_storage:10]
- POWER_TREND_EXTENSION: months [2025-10:43, 2025-11:23, 2025-12:21, 2026-01:9] · regimes [spy_above_ma200=96@+0.04%] · themes [other:27, memory_storage:22, space_aerospace:18, hardware:16, semiconductors:13]
- QQQ_TECH_TACTICAL_SHORT: months [2025-10:8, 2025-11:52, 2025-12:22, 2026-01:51] · regimes [spy_above_ma200=133@-0.41%] · themes [other:44, space_aerospace:29, semiconductors:28, hardware:28, memory_storage:4]
- SIMPLE_SECTOR_RS: months [2025-10:83, 2025-11:87, 2025-12:103, 2026-01:42] · regimes [spy_above_ma200=315@+0.13%] · themes [other:123, space_aerospace:49, memory_storage:46, semiconductors:41, hardware:31]
- SIMPLE_MOM_20_60: months [2025-10:88, 2025-11:86, 2025-12:94, 2026-01:47] · regimes [spy_above_ma200=315@-0.44%] · themes [other:122, memory_storage:72, hardware:44, space_aerospace:34, semiconductors:29]
- RANDOM_LIQUID: months [2025-10:61, 2025-11:72, 2025-12:86, 2026-01:95] · regimes [spy_above_ma200=314@+0.58%] · themes [other:194, unknown:37, biotech_healthcare:21, space_aerospace:19, semiconductors:17]

### rolling_3m_2026-01

- Dates: 2026-01-06 to 2026-04-07
- Signal dates: 63 (sampled: False, ticker-days: 8820, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 4 | +0.00% | -3.54% | -3.60% | -3.50% | -13.57% | +50.00% | +0.00% | -3.29% | -3.94% |
| SNIPER_NO_ATR_CONTRACTION | 44 | +61.36% | +1.90% | +2.06% | +2.16% | -22.75% | +18.18% | +18.18% | +2.15% | +1.50% |
| PROD_VOYAGER_CURRENT | 214 | +56.07% | +1.00% | +0.67% | +0.71% | -68.01% | +18.69% | +15.42% | +1.25% | +0.60% |
| CORRECTION_LEADER_RECLAIM | 176 | +37.50% | -1.06% | -0.48% | -0.51% | -89.96% | +39.20% | +17.61% | -0.81% | -1.46% |
| RECALL_SHADOW_RS_MOMENTUM | 315 | +37.46% | -0.28% | -0.16% | -0.14% | -96.45% | +58.41% | +35.24% | -0.03% | -0.68% |
| RECALL_SHADOW_PULLBACK | 247 | +43.32% | -0.24% | +0.03% | +0.02% | -90.60% | +42.11% | +18.22% | +0.01% | -0.64% |
| POWER_TREND_EXTENSION | 159 | +41.51% | +0.27% | +0.45% | +0.53% | -87.53% | +57.23% | +37.74% | +0.52% | -0.13% |
| QQQ_TECH_TACTICAL_SHORT | 164 | +22.56% | -2.69% | -2.57% | -2.35% | -99.41% | +71.95% | +17.68% | -2.41% | -3.15% |
| SIMPLE_SECTOR_RS | 315 | +36.19% | -0.59% | -0.64% | -0.68% | -97.87% | +62.54% | +33.65% | -0.34% | -0.99% |
| SIMPLE_MOM_20_60 | 315 | +35.56% | -0.68% | -0.70% | -0.75% | -98.03% | +62.22% | +33.02% | -0.43% | -1.08% |
| RANDOM_LIQUID | 314 | +39.17% | -0.82% | -0.72% | -0.79% | -99.20% | +48.41% | +19.11% | -0.57% | -1.22% |
| SPY_BUY_HOLD | 1 | +0.00% | -4.75% | n/a | n/a | -4.75% | +0.00% | +0.00% | -4.50% | -5.15% |
| QQQ_BUY_HOLD | 1 | +0.00% | -5.66% | n/a | n/a | -5.66% | +0.00% | +0.00% | -5.41% | -6.06% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2026-01:4] · regimes [spy_above_ma200=4@-3.54%] · themes [biotech_healthcare:2, unknown:1, other:1]
- SNIPER_NO_ATR_CONTRACTION: months [2026-01:26, 2026-02:14, 2026-03:4] · regimes [spy_above_ma200=44@+1.90%] · themes [unknown:25, other:12, semiconductors:5, biotech_healthcare:2]
- PROD_VOYAGER_CURRENT: months [2026-01:14, 2026-02:75, 2026-03:71, 2026-04:54] · regimes [spy_above_ma200=163@+0.30%, spy_below_ma200=51@+3.21%] · themes [unknown:81, other:73, biotech_healthcare:21, space_aerospace:15, semiconductors:14]
- CORRECTION_LEADER_RECLAIM: months [2026-01:44, 2026-02:37, 2026-03:83, 2026-04:12] · regimes [spy_above_ma200=166@-1.17%, spy_below_ma200=10@+0.83%] · themes [other:96, unknown:24, semiconductors:18, biotech_healthcare:15, space_aerospace:10]
- RECALL_SHADOW_RS_MOMENTUM: months [2026-01:79, 2026-02:84, 2026-03:125, 2026-04:27] · regimes [spy_above_ma200=255@-0.11%, spy_below_ma200=60@-0.98%] · themes [other:126, memory_storage:49, hardware:45, space_aerospace:30, semiconductors:25]
- RECALL_SHADOW_PULLBACK: months [2026-01:39, 2026-02:73, 2026-03:108, 2026-04:27] · regimes [spy_above_ma200=212@-0.61%, spy_below_ma200=35@+1.99%] · themes [other:110, unknown:33, semiconductors:32, space_aerospace:30, biotech_healthcare:19]
- POWER_TREND_EXTENSION: months [2026-01:64, 2026-02:47, 2026-03:39, 2026-04:9] · regimes [spy_above_ma200=133@+0.94%, spy_below_ma200=26@-3.17%] · themes [other:50, space_aerospace:39, memory_storage:29, semiconductors:21, hardware:20]
- QQQ_TECH_TACTICAL_SHORT: months [2026-01:10, 2026-02:65, 2026-03:65, 2026-04:24] · regimes [spy_above_ma200=134@-2.50%, spy_below_ma200=30@-3.53%] · themes [space_aerospace:50, other:45, semiconductors:35, hardware:17, memory_storage:17]
- SIMPLE_SECTOR_RS: months [2026-01:83, 2026-02:82, 2026-03:123, 2026-04:27] · regimes [spy_above_ma200=255@-0.55%, spy_below_ma200=60@-0.78%] · themes [other:134, memory_storage:41, semiconductors:37, biotech_healthcare:29, unknown:28]
- SIMPLE_MOM_20_60: months [2026-01:82, 2026-02:83, 2026-03:123, 2026-04:27] · regimes [spy_above_ma200=255@-0.59%, spy_below_ma200=60@-1.04%] · themes [other:114, memory_storage:56, semiconductors:39, unknown:34, biotech_healthcare:29]
- RANDOM_LIQUID: months [2026-01:65, 2026-02:83, 2026-03:116, 2026-04:50] · regimes [spy_above_ma200=254@-1.65%, spy_below_ma200=60@+2.69%] · themes [other:183, semiconductors:34, unknown:28, hardware:22, biotech_healthcare:20]

### rolling_3m_2026-04

- Dates: 2026-04-08 to 2026-06-11
- Signal dates: 46 (sampled: False, ticker-days: 6440, skipped: 0)

| Variant | Trades | Win Rate | Avg | Rel SPY | Rel QQQ | Max DD | Stop | Target | Exp no-cost | Exp high-cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 3 | +66.67% | +4.42% | +3.53% | +3.02% | -6.25% | +33.33% | +66.67% | +4.67% | +4.02% |
| SNIPER_NO_ATR_CONTRACTION | 24 | +54.17% | +2.11% | +1.25% | +0.33% | -22.75% | +45.83% | +45.83% | +2.36% | +1.71% |
| PROD_VOYAGER_CURRENT | 9 | +100.00% | +6.47% | +5.32% | +3.71% | +0.00% | +0.00% | +44.44% | +6.72% | +6.07% |
| CORRECTION_LEADER_RECLAIM | 10 | +70.00% | +1.22% | -1.96% | -3.61% | -10.25% | +30.00% | +10.00% | +1.47% | +0.82% |
| RECALL_SHADOW_RS_MOMENTUM | 183 | +50.27% | +1.81% | +1.02% | +0.37% | -58.63% | +48.63% | +49.73% | +2.06% | +1.41% |
| RECALL_SHADOW_PULLBACK | 60 | +46.67% | +0.86% | -0.47% | -1.63% | -45.46% | +41.67% | +30.00% | +1.11% | +0.46% |
| POWER_TREND_EXTENSION | 168 | +52.98% | +2.01% | +1.02% | +0.20% | -50.17% | +46.43% | +48.21% | +2.26% | +1.61% |
| QQQ_TECH_TACTICAL_SHORT | 0 | n/a | n/a | n/a | n/a | +0.00% | n/a | n/a | n/a | n/a |
| SIMPLE_SECTOR_RS | 185 | +43.24% | +0.51% | -0.22% | -0.82% | -61.83% | +55.14% | +40.54% | +0.76% | +0.11% |
| SIMPLE_MOM_20_60 | 184 | +39.67% | -0.06% | -0.71% | -1.24% | -73.71% | +58.70% | +36.96% | +0.19% | -0.46% |
| RANDOM_LIQUID | 182 | +52.20% | +1.06% | -0.41% | -1.74% | -56.73% | +35.71% | +28.57% | +1.31% | +0.66% |
| SPY_BUY_HOLD | 1 | +100.00% | +9.07% | n/a | n/a | +0.00% | +0.00% | +0.00% | +9.32% | +8.67% |
| QQQ_BUY_HOLD | 1 | +100.00% | +18.10% | n/a | n/a | +0.00% | +0.00% | +0.00% | +18.35% | +17.70% |
| CASH | 0 | n/a | +0.00% | +0.00% | +0.00% | +0.00% | n/a | n/a | +0.00% | +0.00% |

Trade counts by month / regime / theme (base cost):

- PROD_SNIPER_CURRENT: months [2026-05:3] · regimes [spy_above_ma200=3@+4.42%] · themes [other:3]
- SNIPER_NO_ATR_CONTRACTION: months [2026-04:4, 2026-05:16, 2026-06:4] · regimes [spy_above_ma200=24@+2.11%] · themes [other:12, semiconductors:11, unknown:1]
- PROD_VOYAGER_CURRENT: months [2026-04:9] · regimes [spy_above_ma200=9@+6.47%] · themes [other:8, unknown:1]
- CORRECTION_LEADER_RECLAIM: months [2026-04:10] · regimes [spy_above_ma200=10@+1.22%] · themes [other:6, semiconductors:3, unknown:1]
- RECALL_SHADOW_RS_MOMENTUM: months [2026-04:68, 2026-05:100, 2026-06:15] · regimes [spy_above_ma200=183@+1.81%] · themes [semiconductors:99, other:34, memory_storage:24, space_aerospace:12, hardware:9]
- RECALL_SHADOW_PULLBACK: months [2026-04:8, 2026-05:30, 2026-06:22] · regimes [spy_above_ma200=60@+0.86%] · themes [other:41, semiconductors:7, unknown:5, hardware:4, biotech_healthcare:2]
- POWER_TREND_EXTENSION: months [2026-04:66, 2026-05:86, 2026-06:16] · regimes [spy_above_ma200=168@+2.01%] · themes [semiconductors:99, other:41, space_aerospace:13, hardware:10, memory_storage:3]
- SIMPLE_SECTOR_RS: months [2026-04:72, 2026-05:100, 2026-06:13] · regimes [spy_above_ma200=185@+0.51%] · themes [semiconductors:86, other:47, memory_storage:19, space_aerospace:16, biotech_healthcare:12]
- SIMPLE_MOM_20_60: months [2026-04:71, 2026-05:102, 2026-06:11] · regimes [spy_above_ma200=184@-0.06%] · themes [semiconductors:117, other:33, memory_storage:18, biotech_healthcare:8, space_aerospace:5]
- RANDOM_LIQUID: months [2026-04:54, 2026-05:98, 2026-06:30] · regimes [spy_above_ma200=182@+1.06%] · themes [other:93, semiconductors:30, unknown:17, biotech_healthcare:16, space_aerospace:11]

## Comparison Answers

- is_production_sniper_worse_than_simple_baselines: NO
- does_sniper_no_atr_improve_flow_and_returns: NO
- does_recall_shadow_have_backtested_edge: YES
- does_pullback_improve_recall_shadow_entry_quality: YES
- does_power_trend_extension_work_beyond_recent_regime: NO
- does_qqq_tactical_short_produce_usable_edge: NO
- is_voyager_worth_preserving_unchanged: YES
- variant_deserving_paper_shadow_proposal: NONE_BACKTEST_ONLY_REQUIRES_WALK_FORWARD

## Paper-Shadow Decision

NO_VARIANT_READY_FOR_PAPER_SHADOW

No paper signals, trade proposals, strategy registry edits, execution edits, Gatekeeper edits, Veto Council edits, live-capital edits, or historical evidence mutation were made.
