# LEADER_RESET_RECLAIM Results (Phase 1I)

Generated: 2026-06-12T17:16:30.531898+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

## DID WE FIND A PAPER-SHADOW CANDIDATE? NO

Signal window: `2024-01-02` to `2026-06-11`.

## Strategy Rules (fixed a priori)

- **prior_leadership**: max(rs60_spy, rs60_qqq) >= 0.05 and r60 >= 0.15
- **reset**: pullback from 20d high in [-0.18, -0.05], above MA200, price >= MA50*0.92, r10 >= -0.18 (no climax failure), r20 <= 0.6 (no parabolic)
- **reclaim**: 10 EMA reclaim preferred (20 EMA fallback) with an up close; entry next open
- **risk**: stop = max(2x ATR, distance below reclaim low + 1%) clamped to [4%, 10%]; target +10%; max hold 10d primary (5/20 sensitivity)
- **fitting**: all thresholds fixed a priori; not exposed to any parameter sweep
- **lineage**: Phase 1G.3 leader_reset_event_study -> Phase 1H.4 overblocking evidence -> this standalone test

## Comparison Table (exact backtest + realistic portfolio)

| Variant | Trades | Expectancy | Win | Rel SPY | Ind MaxDD | Real Return | Real MaxDD | Sharpe | Same-Exp SPY |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LEADER_RESET_RECLAIM | 1687 | +0.57% | 0.5252 | +0.15% | -99.64% | +34.28% | -14.35% | 0.9333 | +22.67% |
| PROD_SNIPER_CURRENT | 41 | +1.58% | 0.6098 | +0.76% | -9.48% | +5.12% | -1.37% | 1.121 | +2.01% |
| POWER_TREND_EXTENSION | 793 | +0.95% | 0.4716 | +0.54% | -92.69% | +32.19% | -5.22% | 1.166 | +10.28% |
| RECALL_SHADOW_PULLBACK | 1914 | +0.59% | 0.5068 | +0.11% | -98.69% | +28.13% | -14.33% | 1.0509 | +26.02% |
| RANDOM_LIQUID | 2998 | +0.33% | 0.4903 | -0.26% | -99.95% | +13.06% | -10.00% | 0.5827 | +30.26% |

## Benchmarks (full exposure)

- SPY: +60.89% (maxDD -18.76%)
- QQQ: +81.25% (maxDD -22.77%)

## Walk-Forward (a-priori rules; decay diagnostic)

| Split | Trades | Expectancy | Win | Rel SPY | MaxDD |
|---|---:|---:|---:|---:|---:|
| train | 782 | +0.34% | 0.5166 | -0.19% | -99.68% |
| validation | 394 | +1.46% | 0.5609 | +0.84% | -94.82% |
| test | 511 | +0.24% | 0.5108 | +0.13% | -98.89% |

## Hold Sensitivity (10d is primary, fixed a priori)

| Hold | Trades | Expectancy | Win | Rel SPY | MaxDD | Stop% | Target% |
|---|---:|---:|---:|---:|---:|---:|---:|
| hold_5d | 1710 | +0.24% | 0.5158 | +0.04% | -99.26% | 0.2234 | 0.2234 |
| hold_10d | 1687 | +0.57% | 0.5252 | +0.15% | -99.68% | 0.3515 | 0.3705 |
| hold_20d | 1644 | +0.54% | 0.5085 | +0.04% | -99.97% | 0.4392 | 0.4647 |

## Regime Breakdown

| Regime | Trades | Expectancy | Win |
|---|---:|---:|---:|
| BULL_TREND | 904 | +0.53% | 0.5144 |
| CHOP | 439 | +0.17% | 0.5103 |
| HIGH_VOLATILITY | 67 | +1.78% | 0.5821 |
| MARKET_CORRECTION | 20 | -0.99% | 0.45 |
| RECOVERY_RECLAIM | 35 | +2.45% | 0.6 |
| RISK_OFF | 55 | -1.27% | 0.4727 |
| TECH_LED_CORRECTION | 167 | +1.80% | 0.6108 |

## Year Breakdown

| Year | Trades | Expectancy | Win | Compounded |
|---|---:|---:|---:|---:|
| 2024 | 680 | +0.58% | 0.5235 | +492.81% |
| 2025 | 610 | +0.75% | 0.5344 | +978.34% |
| 2026 | 397 | +0.29% | 0.5139 | -36.11% |

## Accepted Losers / Rejected Winners (1H.4 miner, exit-free 10d labels)

- Accepted winners/losers: 1000 / 733
- Rejected winners/losers: 27573 / 23274
- Trace fidelity mismatches: 0

| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | Verdict |
|---|---:|---:|---:|---|
| liquidity_floor | 0 | 0 | n/a | NEED_MORE_DATA |
| min_bars_75 | 0 | 0 | n/a | NEED_MORE_DATA |
| prior_leadership_rs60 | 47977 | 28 | +3.97% | KEEP_SOFT_WARNING |
| prior_momentum_r60 | 55860 | 490 | +1.87% | OVERBLOCKS_WINNERS |
| reset_band_5_18 | 48322 | 8641 | +1.70% | OVERBLOCKS_WINNERS |
| trend_intact_ma200 | 25553 | 160 | -1.01% | KEEP_HARD_BLOCK |
| trend_intact_ma50_92 | 13410 | 4 | -0.56% | NEED_MORE_DATA |
| no_crash_r10 | 2407 | 0 | n/a | NEED_MORE_DATA |
| no_parabolic_r20 | 891 | 124 | +2.35% | KEEP_SOFT_WARNING |
| ema_reclaim_close_strength | 14816 | 6554 | +1.81% | KEEP_SOFT_WARNING |

## Eligibility Verdict

**DID WE FIND A PAPER-SHADOW CANDIDATE? NO**

Blockers:
- independent-trade maxDD -99.64% breaches -30.00% (portfolio caps would be doing the work, the 1H.2 CLR failure mode)
- does not beat same-exposure SPY/QQQ Sharpe (0.9333 vs 1.7836)
- edge depends on one month or lacks multiple positive months

## Safety

No paper signals, broker orders, trade proposals (doc-only), production thresholds, Gatekeeper/Veto logic,
execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.
Production gates were not loosened; LEADER_RESET_RECLAIM is registered only in the research lab's variant map
and is not part of the lab's default variant list.

