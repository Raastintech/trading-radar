# Strategy Lab Correction Strategy Results

Generated: 2026-06-12T15:45:34.825099+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

## Top-Line Result

Did we find a profitable trading candidate? **MAYBE: NEED_MORE_DATA**

Signal window: `2024-01-02` to `2026-06-11`.

## March 2026 Regime Label

March 2026 label counts: `{'CHOP': 4, 'HIGH_VOLATILITY': 2, 'MARKET_CORRECTION': 3, 'RISK_OFF': 8, 'TECH_LED_CORRECTION': 5}`.
Manual override used: `False`.

## CORRECTION_LEADER_RECLAIM Rules

Entry: next open after reclaim signal; close fallback if open unavailable

Exit: Strategy Lab max-hold 5/10/20 capable; default max hold 10 with reclaim/ATR-derived stop metadata and generic target/trailing parameters

- market regime is MARKET_CORRECTION, TECH_LED_CORRECTION, CHOP, or RECOVERY_RECLAIM
- positive relative strength versus SPY/QQQ or sector over configured 20/40/60 lookback
- ticker avoids new-low behavior and has controlled pullback versus recent high and market drawdown
- price reclaims configured 10/20 EMA using bars available as of the signal date
- volume expands on reclaim or sell-volume dries up
- price remains above major trend support where computable
- earnings/fundamental future labels are not used because dated history is not retained
- illiquid names are filtered by price and average dollar-volume floor
- parabolic exhaustion is rejected unless there was a reset

## Portfolio-Mode Results

| Variant | Verdict | Accepted | Return | CAGR | MaxDD | Sharpe | Exposure | Correction Exp |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| CORRECTION_LEADER_RECLAIM | PROMISING_BUT_PORTFOLIO_RISK | 233 | +22.35% | +8.62% | -7.04% | 1.1187 | +25.70% | +1.28% |
| PROD_SNIPER_CURRENT | NEED_MORE_DATA | 35 | +5.12% | +2.07% | -1.37% | 1.121 | +4.35% | +0.94% |
| SNIPER_NO_ATR_CONTRACTION | PROMISING_BUT_PORTFOLIO_RISK | 216 | +11.48% | +4.56% | -5.69% | 0.758 | +24.71% | +1.05% |
| PROD_VOYAGER_CURRENT | PROMISING_BUT_PORTFOLIO_RISK | 274 | +14.55% | +5.73% | -9.52% | 0.7701 | +35.53% | +1.10% |
| RECALL_SHADOW_RS_MOMENTUM | PROMISING_BUT_PORTFOLIO_RISK | 671 | +0.65% | +0.26% | -17.56% | 0.0979 | +33.10% | +0.58% |
| RECALL_SHADOW_PULLBACK | PROMISING_BUT_PORTFOLIO_RISK | 387 | +28.13% | +10.70% | -14.33% | 1.0509 | +36.92% | +1.36% |
| POWER_TREND_EXTENSION | PROMISING_BUT_PORTFOLIO_RISK | 321 | +32.19% | +12.12% | -5.22% | 1.166 | +13.13% | +1.36% |
| SIMPLE_SECTOR_RS | PROMISING_BUT_PORTFOLIO_RISK | 812 | +2.93% | +1.19% | -32.28% | 0.1545 | +33.29% | +0.25% |
| SIMPLE_MOM_20_60 | REJECT | 803 | +1.35% | +0.55% | -18.88% | 0.117 | +29.48% | -0.12% |
| RANDOM_LIQUID | REJECT | 341 | +13.06% | +5.16% | -10.00% | 0.5827 | +42.61% | +1.05% |
| QQQ_TECH_TACTICAL_SHORT | REJECT | 167 | -28.42% | -12.81% | -28.92% | -1.8645 | +16.05% | -2.04% |

## Exact Backtest Results

| Variant | Trades | Expectancy | Rel SPY | Rel QQQ | MaxDD | Lab Verdict |
|---|---:|---:|---:|---:|---:|---|
| CORRECTION_LEADER_RECLAIM | 2237 | +0.84% | +0.01% | -0.30% | -90.61% | PROMISING_BUT_OVERFIT_RISK |
| PROD_SNIPER_CURRENT | 85 | +1.68% | +1.48% | +1.29% | -13.57% | PROMISING_BUT_OVERFIT_RISK |
| SNIPER_NO_ATR_CONTRACTION | 832 | +0.70% | +0.16% | +0.01% | -68.27% | PROMISING_BUT_OVERFIT_RISK |
| PROD_VOYAGER_CURRENT | 3012 | +0.58% | +0.20% | -0.14% | -99.96% | PROMISING_BUT_OVERFIT_RISK |
| RECALL_SHADOW_RS_MOMENTUM | 5853 | +0.40% | +0.02% | -0.08% | -99.91% | PROMISING_BUT_OVERFIT_RISK |
| RECALL_SHADOW_PULLBACK | 3933 | +0.61% | +0.04% | -0.14% | -98.65% | PROMISING_BUT_OVERFIT_RISK |
| POWER_TREND_EXTENSION | 1784 | +0.99% | -0.01% | -0.20% | -87.98% | PROMISING_BUT_OVERFIT_RISK |
| SIMPLE_SECTOR_RS | 6291 | +0.03% | -0.35% | -0.46% | -100.00% | PROMISING_BUT_OVERFIT_RISK |
| SIMPLE_MOM_20_60 | 5848 | -0.06% | -0.56% | -0.68% | -99.99% | PROMISING_BUT_OVERFIT_RISK |
| RANDOM_LIQUID | 6248 | +0.37% | -0.28% | -0.52% | -99.34% | PROMISING_BUT_OVERFIT_RISK |
| QQQ_TECH_TACTICAL_SHORT | 1567 | -0.74% | -0.51% | -0.16% | -99.41% | PROMISING_BUT_OVERFIT_RISK |

## Correction-Only Results

| Variant | March N/Exp | Market Corr N/Exp | Tech Corr N/Exp | Recovery N/Exp | Family N/Exp |
|---|---:|---:|---:|---:|---:|
| CORRECTION_LEADER_RECLAIM | 52 / -0.88% | 33 / +1.25% | 327 / +1.02% | 69 / +2.53% | 429 / +1.28% |
| PROD_SNIPER_CURRENT | 0 / n/a | 0 / n/a | 8 / +0.86% | 1 / +1.57% | 9 / +0.94% |
| SNIPER_NO_ATR_CONTRACTION | 4 / +0.57% | 0 / n/a | 27 / +0.88% | 2 / +3.22% | 29 / +1.05% |
| PROD_VOYAGER_CURRENT | 99 / +0.55% | 32 / +1.69% | 253 / +1.29% | 34 / -0.87% | 319 / +1.10% |
| RECALL_SHADOW_RS_MOMENTUM | 110 / -0.44% | 35 / +1.00% | 295 / +0.34% | 72 / +1.38% | 402 / +0.58% |
| RECALL_SHADOW_PULLBACK | 76 / +0.03% | 29 / +1.81% | 200 / +1.33% | 26 / +1.07% | 255 / +1.36% |
| POWER_TREND_EXTENSION | 30 / -1.98% | 5 / +0.15% | 43 / +1.22% | 21 / +1.92% | 69 / +1.36% |
| SIMPLE_SECTOR_RS | 110 / -1.30% | 40 / +0.19% | 355 / -0.06% | 80 / +1.66% | 475 / +0.25% |
| SIMPLE_MOM_20_60 | 110 / -0.68% | 28 / -0.66% | 286 / -0.25% | 72 / +0.58% | 386 / -0.12% |
| RANDOM_LIQUID | 110 / -1.02% | 40 / -0.55% | 351 / +0.95% | 80 / +2.30% | 471 / +1.05% |
| QQQ_TECH_TACTICAL_SHORT | 75 / -3.56% | 23 / -2.66% | 192 / -1.94% | 1 / -6.26% | 216 / -2.04% |

## Same-Exposure Benchmarks

| Variant | Strategy Return | Same-Exp SPY | Same-Exp QQQ | Strategy Sharpe | Same-Exp SPY Sharpe | Same-Exp QQQ Sharpe |
|---|---:|---:|---:|---:|---:|---:|
| CORRECTION_LEADER_RECLAIM | +22.35% | +15.77% | +18.82% | 1.1187 | 1.3207 | 1.1815 |
| PROD_SNIPER_CURRENT | +5.12% | +2.01% | +2.51% | 1.121 | 0.7712 | 0.6563 |
| SNIPER_NO_ATR_CONTRACTION | +11.48% | +11.87% | +14.70% | 0.758 | 1.3313 | 1.1788 |
| PROD_VOYAGER_CURRENT | +14.55% | +20.10% | +22.56% | 0.7701 | 1.3442 | 1.1677 |
| RECALL_SHADOW_RS_MOMENTUM | +0.65% | +21.87% | +28.44% | 0.0979 | 1.8362 | 1.7346 |
| RECALL_SHADOW_PULLBACK | +28.13% | +26.02% | +32.48% | 1.0509 | 2.0297 | 1.7905 |
| POWER_TREND_EXTENSION | +32.19% | +10.28% | +14.65% | 1.166 | 2.0918 | 2.1432 |
| SIMPLE_SECTOR_RS | +2.93% | +25.25% | +32.89% | 0.1545 | 2.0532 | 1.9265 |
| SIMPLE_MOM_20_60 | +1.35% | +22.24% | +28.60% | 0.117 | 2.2678 | 2.0195 |
| RANDOM_LIQUID | +13.06% | +30.26% | +40.12% | 0.5827 | 1.6971 | 1.68 |
| QQQ_TECH_TACTICAL_SHORT | -28.42% | +7.19% | +8.79% | -1.8645 | 0.859 | 0.8287 |

## Walk-Forward Results

| Variant | Verdict | Test N | Test Exp | Overfit Risk | Blockers |
|---|---|---:|---:|---|---|
| CORRECTION_LEADER_RECLAIM | PROMISING_BUT_OVERFIT_RISK | 323 | -1.18% | HIGH_TEST_DECAY | test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| PROD_SNIPER_CURRENT | NEED_MORE_DATA | 9 | +5.94% | HIGH_LOW_TEST_SAMPLE | test_trade_count_below_20 |
| SNIPER_NO_ATR_CONTRACTION | REJECT | 85 | +3.94% | HIGH_TRAIN_ONLY | validation_expectancy_not_positive, test_drawdown_too_high |
| PROD_VOYAGER_CURRENT | PROMISING_BUT_OVERFIT_RISK | 340 | +3.18% | MODERATE_NEEDS_PAPER_SHADOW_GATE | test_drawdown_too_high |
| RECALL_SHADOW_RS_MOMENTUM | PROMISING_BUT_OVERFIT_RISK | 670 | +0.35% | MODERATE_NEEDS_PAPER_SHADOW_GATE | test_drawdown_too_high |
| RECALL_SHADOW_PULLBACK | PROMISING_BUT_OVERFIT_RISK | 427 | -0.29% | HIGH_TEST_DECAY | test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| POWER_TREND_EXTENSION | PROMISING_BUT_OVERFIT_RISK | 377 | +0.96% | MODERATE_NEEDS_PAPER_SHADOW_GATE | test_drawdown_too_high |
| SIMPLE_SECTOR_RS | REJECT | 749 | -0.18% | ELEVATED | train_expectancy_not_positive, test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| SIMPLE_MOM_20_60 | REJECT | 724 | -0.52% | ELEVATED | train_expectancy_not_positive, test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| RANDOM_LIQUID | PROMISING_BUT_OVERFIT_RISK | 671 | +0.30% | MODERATE_NEEDS_PAPER_SHADOW_GATE | test_does_not_beat_spy_qqq, test_drawdown_too_high |
| QQQ_TECH_TACTICAL_SHORT | REJECT | 282 | -2.97% | HIGH_TRAIN_ONLY | validation_expectancy_not_positive, test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |

## Threshold Sweep Results

| Variant | Param | Test N | Test Exp | Overfit Risk |
|---|---:|---:|---:|---|
| CORRECTION_LEADER_RECLAIM | 11 | 323 | -1.18% | HIGH_TEST_DECAY |
| PROD_SNIPER_CURRENT | None | 0 | n/a | None |
| SNIPER_NO_ATR_CONTRACTION | 11 | 85 | +3.94% | HIGH_TRAIN_ONLY |
| PROD_VOYAGER_CURRENT | None | 0 | n/a | None |
| RECALL_SHADOW_RS_MOMENTUM | 11 | 670 | +0.35% | MODERATE_NEEDS_PAPER_SHADOW_GATE |
| RECALL_SHADOW_PULLBACK | 11 | 427 | -0.29% | HIGH_TEST_DECAY |
| POWER_TREND_EXTENSION | 11 | 334 | +1.97% | MODERATE_NEEDS_PAPER_SHADOW_GATE |
| SIMPLE_SECTOR_RS | 11 | 672 | +0.03% | MODERATE_NEEDS_PAPER_SHADOW_GATE |
| SIMPLE_MOM_20_60 | 11 | 670 | -0.64% | ELEVATED |
| RANDOM_LIQUID | 11 | 671 | +0.30% | MODERATE_NEEDS_PAPER_SHADOW_GATE |
| QQQ_TECH_TACTICAL_SHORT | 10 | 284 | -1.16% | HIGH_TRAIN_ONLY |

## Benchmark Table

| Benchmark | Return | CAGR | MaxDD | Sharpe | Exposure |
|---|---:|---:|---:|---:|---:|
| SPY | +60.89% | +21.55% | -18.76% | 1.3188 | +100.00% |
| QQQ | +81.25% | +27.64% | -22.77% | 1.2944 | +100.00% |
| cash | +0.00% | +0.00% | +0.00% | None | +0.00% |
| 50% SPY / 50% cash | +27.82% | +10.59% | -9.74% | 1.3188 | +50.00% |
| 50% QQQ / 50% cash | +36.39% | +13.57% | -11.95% | 1.2944 | +50.00% |

## Updated Variant Verdicts

| Variant | Verdict | Reasons |
|---|---|---|
| CORRECTION_LEADER_RECLAIM | PROMISING_BUT_PORTFOLIO_RISK | independent-trade drawdown remains too high; does not improve same-exposure risk-adjusted return; walk-forward does not confirm the edge; walk-forward overfit risk: HIGH_TEST_DECAY; threshold sweep overfit risk: HIGH_TEST_DECAY |
| PROD_SNIPER_CURRENT | NEED_MORE_DATA | walk-forward test sample too small; edge unconfirmed |
| SNIPER_NO_ATR_CONTRACTION | PROMISING_BUT_PORTFOLIO_RISK | independent-trade drawdown remains too high; does not beat same-exposure SPY/QQQ total return; does not improve same-exposure risk-adjusted return; walk-forward does not confirm the edge; walk-forward overfit risk: HIGH_TRAIN_ONLY; threshold sweep overfit risk: HIGH_TRAIN_ONLY |
| PROD_VOYAGER_CURRENT | PROMISING_BUT_PORTFOLIO_RISK | independent-trade drawdown remains too high; does not beat same-exposure SPY/QQQ total return; does not improve same-exposure risk-adjusted return; walk-forward does not confirm the edge |
| RECALL_SHADOW_RS_MOMENTUM | PROMISING_BUT_PORTFOLIO_RISK | independent-trade drawdown remains too high; realistic max drawdown is not acceptable; does not beat same-exposure SPY/QQQ total return; does not improve same-exposure risk-adjusted return; edge is too month-dependent or lacks multiple positive months; walk-forward does not confirm the edge |
| RECALL_SHADOW_PULLBACK | PROMISING_BUT_PORTFOLIO_RISK | independent-trade drawdown remains too high; does not beat same-exposure SPY/QQQ total return; does not improve same-exposure risk-adjusted return; walk-forward does not confirm the edge; walk-forward overfit risk: HIGH_TEST_DECAY; threshold sweep overfit risk: HIGH_TEST_DECAY |
| POWER_TREND_EXTENSION | PROMISING_BUT_PORTFOLIO_RISK | independent-trade drawdown remains too high; does not improve same-exposure risk-adjusted return; sector concentration above 70%; walk-forward does not confirm the edge |
| SIMPLE_SECTOR_RS | PROMISING_BUT_PORTFOLIO_RISK | independent-trade drawdown remains too high; realistic max drawdown is not acceptable; does not beat same-exposure SPY/QQQ total return; does not improve same-exposure risk-adjusted return; walk-forward does not confirm the edge |
| SIMPLE_MOM_20_60 | REJECT | independent-trade expectancy is not positive |
| RANDOM_LIQUID | REJECT | random-liquid control is not a strategy edge |
| QQQ_TECH_TACTICAL_SHORT | REJECT | kept only as rejected harmful baseline; not optimized |

## Paper-Shadow Eligibility

Status: `NO_VARIANT_READY_FOR_PAPER_SHADOW`
Proposal created: `False`

No paper-shadow was activated. No paper signals, broker orders, production thresholds, Gatekeeper/Veto logic, execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.

## Recommended Operator Decision

Do not activate paper-shadow. Continue research/data collection.

