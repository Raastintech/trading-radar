# Strategy Evidence Rigor Report

Generated: 2026-05-04 02:01 UTC  ·  Script: `research/strategy_evidence_audit.py`

Scope: SNIPER_V6 · VOYAGER_PAPER · SHORT_A. Analysis-only; no strategy / scoring / governance / execution changes.

Bootstrap CIs are 95%, 2,000 resamples, fixed seed `20260503`. Walk-forward windows: 18m train / 6m test. Random control: 5× synthetic entries per closed trade, same universe / horizon / stop-target geometry / friction.

## Top-line verdicts

| Sleeve | Source | n closed | n open | Date range | Verdict |
|---|---|---:|---:|---|---|
| `SNIPER_V6` | backtest_csv | 225 | 0 | 2020-01-17 → 2024-12-04 | **INDISTINGUISHABLE_FROM_RANDOM** |
| `VOYAGER_PAPER` | backtest_csv | 64 | 0 | 2022-01-03 → 2024-10-01 | **INDISTINGUISHABLE_FROM_RANDOM** |
| `SHORT_A` | backtest_csv | 13 | 2 | 2024-07-25 → 2026-04-23 | **WEAK_AND_THIN** |

---

## SNIPER_V6

- **Source:** backtest_csv  ·  `research/backtests/sniper_backtest.py`
- **n_total / n_closed / n_open:** 225 / 225 / 0
- **Date range:** 2020-01-17 → 2024-12-04
- **Horizons (days):** 5, 10, 20

### Per-horizon statistics (bootstrap 95% CI)

| Horizon | n | Win rate | Avg raw | Avg adj | Expectancy | Stop hit | Target hit | Max DD |
|---:|---:|---|---|---|---|---|---|---|
| 5d | 75 | 49.3% [38.7%, 61.3%] | +0.76% | +0.46% [-0.38%, +1.33%] | +0.46% [-0.38%, +1.33%] | 28.0% [17.3%, 38.7%] | 2.7% [0.0%, 6.7%] | 18.7% |
| 10d | 75 | 50.7% [38.7%, 62.7%] | +0.87% | +0.57% [-0.54%, +1.72%] | +0.57% [-0.54%, +1.72%] | 42.7% [32.0%, 54.7%] | 13.3% [6.7%, 21.3%] | 23.6% |
| 20d | 75 | 42.7% [30.7%, 54.7%] | +1.01% | +0.71% [-0.57%, +1.99%] | +0.71% [-0.57%, +1.99%] | 50.7% [38.7%, 62.7%] | 22.7% [13.3%, 32.0%] | 23.6% |
| **all** | **225** | **47.6%** [40.9%, 54.2%] | **+0.88%** | **+0.58%** [-0.04%, +1.22%] | **+0.58%** [-0.04%, +1.22%] | 40.4% [34.2%, 46.7%] | 12.9% [8.4%, 17.3%] | 48.9% |

### Walk-forward (18m train / 6m test)

| Train window | Test window | n test | WR test | Avg adj test |
|---|---|---:|---|---|
| 2020-01-17 → 2021-07-19 | 2021-07-19 → 2022-01-18 | 8 | 50.0% | +0.35% |
| 2020-07-18 → 2022-01-18 | 2022-01-18 → 2022-07-20 | 1 | 0.0% | -1.71% |
| 2021-01-17 → 2022-07-20 | 2022-07-20 → 2023-01-19 | 2 | 0.0% | -2.77% |
| 2021-07-19 → 2023-01-19 | 2023-01-19 → 2023-07-21 | 11 | 63.6% | +3.02% |
| 2022-01-18 → 2023-07-21 | 2023-07-21 → 2024-01-20 | 10 | 60.0% | -0.24% |
| 2022-07-20 → 2024-01-20 | 2024-01-20 → 2024-07-21 | 7 | 57.1% | +0.09% |

_Stability: **stable (≥50% windows positive)**._

### Random-entry control (primary horizon: 10d (mandate-aligned))

| Stat | Strategy | Random control | Δ (strategy − random) |
|---|---|---|---|
| Win rate | 50.7% | 42.1% | +8.53pp |
| Avg adj return | +0.57% | +0.16% | +0.40pp |
| Stop-hit rate | 42.7% | 48.5% | -5.87pp |
| Target-hit rate | 13.3% | 13.9% | -0.53pp |

_Random sample size: n=375 synthetic entries. Same tickers, random entry dates, same horizon, same stop/target geometry, same friction._

### Friction sensitivity (primary horizon: 10d)

| RT friction | n | Avg adj (95% CI) | Win rate (95% CI) |
|---:|---:|---|---|
| 0.00% | 75 | +0.87% [-0.24%, +2.02%] | 50.7% [38.7%, 62.7%] |
| 0.30% | 75 | +0.57% [-0.54%, +1.72%] | 50.7% [38.7%, 62.7%] |
| 0.50% | 75 | +0.37% [-0.74%, +1.52%] | 48.0% [36.0%, 60.0%] |
| 1.00% | 75 | -0.13% [-1.24%, +1.02%] | 42.7% [30.7%, 54.7%] |

_Sensitivity is computed on `raw_return_pct` at audit time so the table is independent of whichever friction the export already subtracted. Use it to gauge how robust the headline number is to a wider-than-assumed cost stack._

### Execution friction audit

| Component | Status |
|---|---|
| commission | MODELED — 5 bps each side (10 bps RT) |
| slippage | MODELED — 10 bps each side (20 bps RT) |
| spread | GAP — implicit only (folded into slippage), not separately modeled |
| gap_risk | GAP — overnight gaps not separately stress-tested |
| borrow_locate | N/A — long-only |
| options_realism | N/A — equity outright |
| friction_rt_pct | 0.30 |

---

## VOYAGER_PAPER

- **Source:** backtest_csv  ·  `research/backtests/voyager_v2_backtest.py + voyager_paper_signals`
- **n_total / n_closed / n_open:** 64 / 64 / 0
- **Date range:** 2022-01-03 → 2024-10-01
- **Horizons (days):** 30, 90, 130, 252

### Per-horizon statistics (bootstrap 95% CI)

| Horizon | n | Win rate | Avg raw | Avg adj | Expectancy | Stop hit | Target hit | Max DD |
|---:|---:|---|---|---|---|---|---|---|
| 30d | 16 | 56.2% [31.2%, 81.2%] | +1.30% | +1.00% [-3.93%, +6.12%] | +1.00% [-3.93%, +6.12%] | — — | — — | 33.4% |
| 90d | 16 | 56.2% [31.2%, 81.2%] | +7.69% | +7.39% [-1.51%, +15.64%] | +7.39% [-1.51%, +15.64%] | — — | — — | 23.9% |
| 130d | 16 | 62.5% [37.5%, 87.5%] | +8.55% | +8.25% [-3.20%, +20.78%] | +8.25% [-3.20%, +20.78%] | — — | — — | 31.6% |
| 252d | 16 | 62.5% [37.5%, 87.5%] | +20.53% | +20.23% [+3.34%, +38.18%] | +20.23% [+3.34%, +38.18%] | — — | — — | 31.2% |
| **all** | **64** | **59.4%** [46.9%, 70.3%] | **+9.52%** | **+9.22%** [+3.70%, +15.44%] | **+9.22%** [+3.70%, +15.44%] | — — | — — | 64.5% |

### Walk-forward (18m train / 6m test)

> Insufficient date span or sample size for rolling validation (need ≥50 closed trades and ≥24 months of coverage).

### Random-entry control (primary horizon: 252d (mandate-aligned))

| Stat | Strategy | Random control | Δ (strategy − random) |
|---|---|---|---|
| Win rate | 62.5% | 48.8% | +13.75pp |
| Avg adj return | +20.23% | +6.42% | +13.81pp |
| Stop-hit rate | — | 47.5% | — |
| Target-hit rate | — | 31.2% | — |

_Random sample size: n=80 synthetic entries. Same tickers, random entry dates, same horizon, same stop/target geometry, same friction._

### Random-entry control (all-horizons aggregate)

| Stat | Strategy | Random control | Δ (strategy − random) |
|---|---|---|---|
| Win rate | 59.4% | 57.5% | +1.88pp |
| Avg adj return | +9.22% | +6.58% | +2.64pp |

_Random sample size: n=320 synthetic entries, each at the actual hold_days of the matching strategy trade._

### Friction sensitivity (primary horizon: 252d)

| RT friction | n | Avg adj (95% CI) | Win rate (95% CI) |
|---:|---:|---|---|
| 0.00% | 16 | +20.53% [+3.64%, +38.48%] | 62.5% [37.5%, 87.5%] |
| 0.30% | 16 | +20.23% [+3.34%, +38.18%] | 62.5% [37.5%, 87.5%] |
| 0.50% | 16 | +20.03% [+3.14%, +37.98%] | 62.5% [37.5%, 87.5%] |
| 1.00% | 16 | +19.53% [+2.64%, +37.48%] | 56.2% [31.2%, 81.2%] |

_Sensitivity is computed on `raw_return_pct` at audit time so the table is independent of whichever friction the export already subtracted. Use it to gauge how robust the headline number is to a wider-than-assumed cost stack._

### Execution friction audit

| Component | Status |
|---|---|
| commission | GAP — not modeled in forward-return calc (raw only) |
| slippage | GAP — not modeled |
| spread | GAP — not modeled |
| gap_risk | GAP — not modeled |
| borrow_locate | N/A — long-only |
| options_realism | N/A — equity outright |
| friction_rt_pct | 0.00 (raw forward returns) |

> Survivorship: universe excludes delisted names. 13F lookahead handled via filing_date.

---

## SHORT_A

- **Source:** backtest_csv  ·  `research/sleeves/short_backtester.py + paper_signals (sleeve='SHORT_A')`
- **n_total / n_closed / n_open:** 15 / 13 / 2
- **Date range:** 2024-07-25 → 2026-04-23
- **Horizons (days):** 3, 5, 6, 9, 10, 15, 21, 22, 35, 39, 43, 45

### Per-horizon statistics (bootstrap 95% CI)

| Horizon | n | Win rate | Avg raw | Avg adj | Expectancy | Stop hit | Target hit | Max DD |
|---:|---:|---|---|---|---|---|---|---|
| 3d | 2 | 50.0% [0.0%, 100.0%] | +1.98% | +1.68% [-0.39%, +3.76%] | +1.68% [-0.39%, +3.76%] | 0.0% [0.0%, 0.0%] | 0.0% [0.0%, 0.0%] | 0.4% |
| 5d | 2 | 100.0% [100.0%, 100.0%] | +1.99% | +1.69% [+1.20%, +2.18%] | +1.69% [+1.20%, +2.18%] | 0.0% [0.0%, 0.0%] | 0.0% [0.0%, 0.0%] | 0.0% |
| 6d | 1 | 0.0% — | -10.00% | -10.72% — | -10.72% — | 100.0% — | 0.0% — | 10.7% |
| 9d | 1 | 0.0% — | -10.00% | -10.72% — | -10.72% — | 100.0% — | 0.0% — | 10.7% |
| 10d | 0 | — | — | — | — | — | — | — |
| 15d | 1 | 0.0% — | -10.00% | -10.74% — | -10.74% — | 100.0% — | 0.0% — | 10.7% |
| 21d | 1 | 0.0% — | -10.00% | -10.76% — | -10.76% — | 100.0% — | 0.0% — | 10.8% |
| 22d | 1 | 0.0% — | -10.00% | -10.76% — | -10.76% — | 100.0% — | 0.0% — | 10.8% |
| 35d | 1 | 0.0% — | -10.00% | -10.80% — | -10.80% — | 100.0% — | 0.0% — | 10.8% |
| 39d | 1 | 0.0% — | -0.01% | -0.32% — | -0.32% — | 0.0% — | 0.0% — | 0.3% |
| 43d | 1 | 100.0% — | +1.60% | +1.28% — | +1.28% — | 0.0% — | 0.0% — | 0.0% |
| 45d | 1 | 0.0% — | -6.00% | -6.32% — | -6.32% — | 0.0% — | 0.0% — | 6.3% |
| **all** | **13** | **30.8%** [7.7%, 61.5%] | **-4.34%** | **-4.85%** [-8.04%, -1.68%] | **-4.85%** [-8.04%, -1.68%] | 46.2% [23.1%, 76.9%] | 0.0% [0.0%, 0.0%] | 52.2% |

### Walk-forward (18m train / 6m test)

> Insufficient date span or sample size for rolling validation (need ≥50 closed trades and ≥24 months of coverage).

### Random-entry control (primary horizon: 5d (mandate-aligned))

| Stat | Strategy | Random control | Δ (strategy − random) |
|---|---|---|---|
| Win rate | 100.0% | 40.0% | +60.00pp |
| Avg adj return | +1.69% | -0.76% | +2.45pp |
| Stop-hit rate | 0.0% | 20.0% | -20.00pp |
| Target-hit rate | 0.0% | 0.0% | +0.00pp |

_Random sample size: n=10 synthetic entries. Same tickers, random entry dates, same horizon, same stop/target geometry, same friction._

### Random-entry control (all-horizons aggregate)

| Stat | Strategy | Random control | Δ (strategy − random) |
|---|---|---|---|
| Win rate | 30.8% | 49.2% | -18.46pp |
| Avg adj return | -4.85% | +0.86% | -5.71pp |

_Random sample size: n=65 synthetic entries, each at the actual hold_days of the matching strategy trade._

### Friction sensitivity (primary horizon: 5d)

| RT friction | n | Avg adj (95% CI) | Win rate (95% CI) |
|---:|---:|---|---|
| 0.00% | 2 | +1.99% [+1.50%, +2.48%] | 100.0% [100.0%, 100.0%] |
| 0.30% | 2 | +1.69% [+1.20%, +2.18%] | 100.0% [100.0%, 100.0%] |
| 0.50% | 2 | +1.49% [+1.00%, +1.98%] | 100.0% [100.0%, 100.0%] |
| 1.00% | 2 | +0.99% [+0.50%, +1.48%] | 100.0% [100.0%, 100.0%] |

_Sensitivity is computed on `raw_return_pct` at audit time so the table is independent of whichever friction the export already subtracted. Use it to gauge how robust the headline number is to a wider-than-assumed cost stack._

### Execution friction audit

| Component | Status |
|---|---|
| commission | MODELED — folded into slippage_bps (configurable, default 0) |
| slippage | MODELED — slippage_bps each side, RT applied |
| spread | MODELED — spread_bps each side, RT applied |
| gap_risk | PARTIAL — halt_gap_penalty_pct applies only when squeeze-like flag triggers; gap_risk_max_up_20d_pct logged per trade for analysis |
| borrow_locate | MODELED — borrow_fee_annual_pct accrued over hold_days |
| options_realism | N/A — short equity only |
| friction_rt_pct | depends on CLI args; live paper uses adjusted_return_pct from execution_policy |

> Live paper signals carry an adjusted_return_pct already net of declared friction; live n is currently very small (governance-blocked dominate).

---

## Limitations

- **Trade-level data sources:** SNIPER_V6 and VOYAGER_PAPER come from historical backtest exports (`research/sleeves/export_backtest_trades.py`); SHORT_A comes from the live paper DB (`paper_signals` + outcomes), which is still very thin.
- Walk-forward and random control require closed-trade outcomes; still-open paper trades are excluded. Walk-forward is run on the sleeve's mandate-aligned primary horizon to keep WR / expectancy interpretable.
- Random control reuses cached prices for the same tickers as the strategy. If the strategy universe is survivorship-biased (SNIPER's `LARGE_CAP_UNIVERSE` is hand-curated), the random control inherits that bias and the comparison is conservative (random looks better than it would on a delisted-inclusive set).
- Friction audit is a static read of the source code. It does not verify that live execution matches the backtest's friction assumption.
- **VOYAGER_PAPER backtest reports raw forward returns with no friction.** At long horizons (252d) friction is small relative to return, but at 30d even a 30 bps RT cost would meaningfully shift the distribution.
- **SHORT_A historical depth missing**: only live paper rows available (n closed = 4). Run `research/sleeves/short_backtester.py --export_trades_csv research/sleeves/trades/SHORT_A.csv` separately to populate historical trades — short_backtester needs FMP credentials and is too heavyweight to drive from the audit pipeline.
- **No edge claim is capital-proven.** This is a paper/research evidence audit. "Robust" or "promising" verdicts mean the data clears statistical thresholds at the sample sizes available — not that the strategy will work in size.
