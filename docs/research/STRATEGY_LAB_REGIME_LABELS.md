# Strategy Lab Regime Labels

Generated: 2026-06-12T15:43:41.357275+00:00

Research-only. Regime labels use only as-of price-derived market features.

Window: `2024-01-02` to `2026-06-11`

## Label Counts

| Label | Dates |
|---|---:|
| BULL_TREND | 306 |
| CHOP | 149 |
| HIGH_VOLATILITY | 17 |
| MARKET_CORRECTION | 8 |
| RECOVERY_RECLAIM | 16 |
| RISK_OFF | 43 |
| TECH_LED_CORRECTION | 74 |

## March 2026

Manual override used: `False`

| Date | Label | SPY DD | QQQ DD | QQQ vs SPY 20d |
|---|---|---:|---:|---:|
| 2026-03-02 | CHOP | -1.64% | -4.48% | -1.41% |
| 2026-03-03 | TECH_LED_CORRECTION | -2.51% | -5.50% | -1.75% |
| 2026-03-04 | CHOP | -1.82% | -4.06% | -0.30% |
| 2026-03-05 | CHOP | -2.37% | -4.35% | +1.23% |
| 2026-03-06 | TECH_LED_CORRECTION | -3.65% | -5.79% | +1.23% |
| 2026-03-09 | CHOP | -2.80% | -4.53% | +1.48% |
| 2026-03-10 | HIGH_VOLATILITY | -2.96% | -4.53% | +1.35% |
| 2026-03-11 | HIGH_VOLATILITY | -3.08% | -4.54% | +1.66% |
| 2026-03-12 | TECH_LED_CORRECTION | -4.56% | -6.18% | +1.16% |
| 2026-03-13 | TECH_LED_CORRECTION | -5.10% | -6.74% | +1.63% |
| 2026-03-16 | TECH_LED_CORRECTION | -4.13% | -5.69% | +1.61% |
| 2026-03-17 | MARKET_CORRECTION | -3.88% | -5.23% | +2.10% |
| 2026-03-18 | MARKET_CORRECTION | -5.22% | -6.55% | +1.82% |
| 2026-03-19 | MARKET_CORRECTION | -5.45% | -6.85% | +1.87% |
| 2026-03-20 | RISK_OFF | -6.80% | -8.57% | +1.27% |
| 2026-03-23 | RISK_OFF | -5.82% | -7.52% | +1.59% |
| 2026-03-24 | RISK_OFF | -6.14% | -8.15% | +0.90% |
| 2026-03-25 | RISK_OFF | -5.62% | -7.55% | +0.42% |
| 2026-03-26 | RISK_OFF | -7.30% | -9.75% | +0.45% |
| 2026-03-27 | RISK_OFF | -8.88% | -11.52% | +0.06% |
| 2026-03-30 | RISK_OFF | -9.19% | -12.19% | -0.40% |
| 2026-03-31 | RISK_OFF | -6.55% | -9.22% | +0.21% |

## Rules

- RISK_OFF: major index below 200d support during a correction with weak 20d returns.
- RECOVERY_RECLAIM: correction context with SPY and QQQ reclaiming 20 EMA and strong 5d returns.
- TECH_LED_CORRECTION: correction context plus QQQ/SMH/XLK weakness versus SPY.
- MARKET_CORRECTION: SPY or QQQ drawdown exceeds threshold, or both are below 20 EMA/50 MA with weak 20d returns.
- HIGH_VOLATILITY: ATR or VXX/VIXY proxy stress elevated without correction classification.
- CHOP: below short trend or flat 20d returns without correction classification.
- BULL_TREND: default positive/non-stressed state.
