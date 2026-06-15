# Strategy Lab Portfolio Simulation Results

Generated: 2026-06-12T15:27:29.394829+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

Signal window: `2024-01-02` to `2026-06-11`.

This is the corrected primary comparison over non-overlapping windows. Rolling/recent exact-full windows remain diagnostic because they overlap the yearly windows.

## Variant Summary

| Variant | Method Label | Independent N | Independent Exp | Independent MaxDD | Realistic Accepted | Realistic Return | Realistic CAGR | Realistic MaxDD | Exposure | Avg Concurrent |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | HIGH_QUALITY_LOW_FLOW | 41 | +1.58% | -9.48% | 35 | +5.12% | +2.07% | -1.37% | +4.35% | 0.4356 |
| SNIPER_NO_ATR_CONTRACTION | PROMISING_BUT_PORTFOLIO_RISK | 404 | +0.66% | -71.39% | 216 | +11.48% | +4.56% | -5.69% | +24.71% | 2.4976 |
| PROD_VOYAGER_CURRENT | PROMISING_BUT_PORTFOLIO_RISK | 1471 | +0.52% | -99.63% | 274 | +14.55% | +5.73% | -9.52% | +35.53% | 3.6232 |
| CORRECTION_LEADER_RECLAIM | PROMISING_BUT_PORTFOLIO_RISK | 1105 | +0.84% | -97.04% | 233 | +22.35% | +8.62% | -7.04% | +25.70% | 2.6003 |
| RECALL_SHADOW_RS_MOMENTUM | PROMISING_BUT_PORTFOLIO_RISK | 2800 | +0.37% | -99.96% | 671 | +0.65% | +0.26% | -17.56% | +33.10% | 3.5693 |
| RECALL_SHADOW_PULLBACK | PROMISING_BUT_PORTFOLIO_RISK | 1914 | +0.59% | -98.69% | 387 | +28.13% | +10.70% | -14.33% | +36.92% | 3.7651 |
| POWER_TREND_EXTENSION | PROMISING_BUT_PORTFOLIO_RISK | 793 | +0.95% | -92.69% | 321 | +32.19% | +12.12% | -5.22% | +13.13% | 1.4519 |
| QQQ_TECH_TACTICAL_SHORT | REJECT | 767 | -0.68% | -100.00% | 167 | -28.42% | -12.81% | -28.92% | +16.05% | 1.8026 |
| SIMPLE_SECTOR_RS | PROMISING_BUT_PORTFOLIO_RISK | 3018 | +0.03% | -100.00% | 812 | +2.93% | +1.19% | -32.28% | +33.29% | 3.509 |
| SIMPLE_MOM_20_60 | REJECT | 2797 | -0.05% | -100.00% | 803 | +1.35% | +0.55% | -18.88% | +29.48% | 3.1925 |
| RANDOM_LIQUID | REJECT | 2998 | +0.33% | -99.95% | 341 | +13.06% | +5.16% | -10.00% | +42.61% | 4.3801 |

## Benchmark Table

| Benchmark | Total Return | CAGR | MaxDD | Sharpe | Sortino | Exposure | Worst Month | Best Month |
|---|---:|---:|---:|---:|---:|---:|---|---|
| SPY | +60.89% | +21.55% | -18.76% | 1.3188 | 1.705 | +100.00% | 2025-03 -5.57% | 2026-04 +10.51% |
| QQQ | +81.25% | +27.64% | -22.77% | 1.2944 | 1.7323 | +100.00% | 2025-03 -7.59% | 2026-04 +15.69% |
| cash | +0.00% | +0.00% | +0.00% | None | None | +0.00% | n/a n/a | n/a n/a |

## Fairness Review

- Phase 1H.1 independent-trade maxDD is not a capital-constrained drawdown.
- Phase 1H.1 exact-full aggregate double-counts periods by mixing yearly and rolling windows.
- Strategy per-trade expectancy and buy-hold total return are different units.
- Buy-hold benchmark maxDD in Phase 1H.1 is summarized as one trade, so daily benchmark drawdown is understated.
- Strategies often sit partly or mostly in cash under realistic caps while SPY/QQQ are fully invested.

## Decision Update

NO_VARIANT_READY_FOR_PAPER_SHADOW

No variant is promoted by this audit. A paper-shadow proposal remains disallowed unless independent-trade, realistic-portfolio, exact walk-forward, and operator review all agree.
