# LRR_REGIME_GATED Results (Phase 1I.1)

Generated: 2026-06-12T19:07:52.588410+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

## DID LRR_REGIME_GATED EARN PAPER-SHADOW? NO

Ladder verdict: **PROMISING_BUT_PORTFOLIO_RISK** — independent-trade maxDD -71.35% breaches -30.00% (caps would be doing the work)

Signal window: `2024-01-02` to `2026-06-11`. Allowed regimes (fixed a priori): `HIGH_VOLATILITY, RECOVERY_RECLAIM, TECH_LED_CORRECTION`.

## Comparison Table

| Variant | Trades | Expectancy | Ind MaxDD | Real Return | Real MaxDD | Sharpe | Same-Exp SPY Sharpe | Same-Exp QQQ Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LRR_REGIME_GATED | 269 | +1.88% | -71.35% | +16.68% | -6.75% | 0.9811 | 1.1133 | 1.0524 |
| LEADER_RESET_RECLAIM | 1687 | +0.57% | -99.64% | +34.28% | -14.35% | 0.9333 | 1.7836 | 1.6095 |
| POWER_TREND_EXTENSION | 793 | +0.95% | -92.69% | +32.19% | -5.22% | 1.166 | 2.0918 | 2.1432 |
| RECALL_SHADOW_PULLBACK | 1914 | +0.59% | -98.69% | +28.13% | -14.33% | 1.0509 | 2.0297 | 1.7905 |
| PROD_SNIPER_CURRENT | 41 | +1.58% | -9.48% | +5.12% | -1.37% | 1.121 | 0.7712 | 0.6563 |
| SNIPER_NO_ATR_CONTRACTION | 404 | +0.66% | -71.39% | +11.48% | -5.69% | 0.758 | 1.3313 | 1.1788 |
| RANDOM_LIQUID | 2998 | +0.33% | -99.95% | +13.06% | -10.00% | 0.5827 | 1.6971 | 1.68 |

Benchmarks: SPY +60.89% (Sharpe 1.3188), QQQ +81.25% (Sharpe 1.2944), cash +0.00%.

## Anti-Overfit Checks (Task 4)

| Check | Result | Detail |
|---|---|---|
| improves_sharpe_vs_same_exposure | FAIL | gated Sharpe 0.9811 vs same-exposure best 1.1133 |
| improves_maxdd_vs_unrestricted_lrr | PASS | realistic -6.75% vs -14.35%; independent -71.35% vs -99.64% |
| avoids_nov_2024_concentration | PASS | 2024-11 share of compounded gain = 0.0 (max 0.5) |
| improves_2026_ytd_decay | PASS | 2026 expectancy gated +0.49% vs unrestricted +0.29% |
| walk_forward_test_positive | PASS | test n=127, expectancy +1.18% |
| walk_forward_majority_positive | PASS | expectancy positive in 3/3 splits |
| works_in_multiple_months | PASS | positive months 10/17, best share 0.0323 |
| works_across_tickers_and_themes | PASS | top ticker 0.0483, top theme ('other', 133) (0.4944) |
| beats_random_after_costs | PASS | gated expectancy +1.88% vs random +0.33% |
| beats_same_exposure_return_after_costs | PASS | gated realistic +16.68% vs same-exposure best +9.13% |

## Walk-Forward (a-priori rules; decay diagnostic)

| Split | Trades | Expectancy | Rel SPY | MaxDD |
|---|---:|---:|---:|---:|
| train | 125 | +2.38% | +1.42% | -70.01% |
| validation | 17 | +3.45% | +0.84% | -15.35% |
| test | 127 | +1.18% | +1.24% | -55.40% |

## Exit Sweep (exits only; regime set frozen; diagnostic)

Binding config: `hold=10, target=0.10, trailing=None`.

| Config | Trades | Expectancy | Win | MaxDD | Stop% | Target% |
|---|---:|---:|---:|---:|---:|---:|
| hold5_tgt10_trailnone | 269 | +1.30% | 0.5836 | -68.21% | 0.1933 | 0.2714 |
| hold10_tgt10_trailnone | 269 | +1.88% | 0.6022 | -70.01% | 0.2937 | 0.4312 |
| hold20_tgt10_trailnone | 269 | +1.66% | 0.5762 | -70.57% | 0.3755 | 0.5167 |
| hold10_tgt8_trailnone | 269 | +1.84% | 0.6468 | -70.55% | 0.2677 | 0.539 |
| hold10_tgt15_trailnone | 269 | +1.84% | 0.5279 | -75.97% | 0.3197 | 0.2491 |
| hold10_tgt10_trail10 | 269 | +1.30% | 0.5056 | -65.47% | 0.4758 | 0.316 |
| hold20_tgt15_trail10 | 269 | +0.81% | 0.3829 | -67.21% | 0.6914 | 0.2082 |

## Regime / Year Breakdown (gated variant)

| Regime | Trades | Expectancy | Win |
|---|---:|---:|---:|
| HIGH_VOLATILITY | 67 | +1.78% | 0.5821 |
| RECOVERY_RECLAIM | 35 | +2.45% | 0.6 |
| TECH_LED_CORRECTION | 167 | +1.80% | 0.6108 |

| Year | Trades | Expectancy | Win | Compounded |
|---|---:|---:|---:|---:|
| 2024 | 113 | +2.61% | 0.6372 | +1211.80% |
| 2025 | 57 | +2.85% | 0.6842 | +306.93% |
| 2026 | 99 | +0.49% | 0.5152 | +11.90% |

## Paper-Shadow Status

**NO_VARIANT_READY_FOR_PAPER_SHADOW** (proposal created: `False`)

## Safety

No paper signals, broker orders, trade proposals (doc-only), production thresholds, Gatekeeper/Veto logic,
execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.
LRR_REGIME_GATED exists only in the research lab's variant map (not in lab defaults, not in the production
strategy registry). Regime labels are strictly as-of.

