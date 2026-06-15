# Core-Satellite Leveraged Results (Phase 2A.1)

Generated: 2026-06-13T02:18:08.904980+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

## Setup

- Window: `2024-01-02`..`2026-06-11` (613 trading days).
- Borrow rate: **6.5%/yr** (Fed funds avg 2024-2026 ≈5% + 1.5% broker spread; fixed assumption).
- Formula: `daily_net = core_w*r_core - max(0, core_w-1.0)*rate/252`
- Borrow cost accrues only on the leveraged portion above 1.0x exposure. At CHOP (60%) × 1.25x = 75% gross → no borrowing. At BULL (100%) × 1.25x = 125% gross → borrow 25% at rate.

## Pre-Registered Gates (fixed before run)

| Gate | Threshold |
|---|---|
| CAGR after cost | >+27.34% (QQQ Phase 2A) |
| maxDD absolute floor | >=-22.77% (QQQ; not just vs 1.0x base) |
| Sharpe | >1.2738 (QQQ) |
| Calmar | >1.2004 (QQQ) |
| Year-dependent | positive months ≥2, best share ≤65% |
| All years positive | 2024 + 2025 + 2026 YTD all positive |
| No lookahead | prior-close regime only (test-pinned) |

## Results Table

| Variant | Lev | CAGR | maxDD | Sharpe | Calmar | Worst M | Turn/yr | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| BENCHMARK_QQQ_BUY_HOLD | 1.00x | +27.34% | -22.77% | 1.2738 | 1.2004 | -7.59% | 0.0 | BENCHMARK |
| BENCHMARK_SPY_BUY_HOLD | 1.00x | +21.37% | -18.76% | 1.2995 | 1.1391 | -5.57% | 0.0 | BENCHMARK |
| REGIME_THROTTLED_QQQ_1_00x | 1.00x | +18.95% | -9.99% | 1.3857 | 1.8963 | -4.05% | 19.5128 | LEVERED_REJECT |
| REGIME_THROTTLED_BLEND_1_00x | 1.00x | +17.65% | -9.38% | 1.4132 | 1.8807 | -4.53% | 19.5128 | LEVERED_REJECT |
| REGIME_THROTTLED_QQQ_1_25x | 1.25x | +22.83% | -12.51% | 1.3341 | 1.8247 | -5.12% | 24.391 | LEVERED_REJECT |
| REGIME_THROTTLED_BLEND_1_25x | 1.25x | +21.21% | -11.75% | 1.3568 | 1.8043 | -5.70% | 24.391 | LEVERED_REJECT |
| REGIME_THROTTLED_QQQ_1_50x | 1.50x | +26.70% | -14.99% | 1.2996 | 1.7811 | -6.18% | 29.2692 | LEVERED_REJECT |
| REGIME_THROTTLED_BLEND_1_50x | 1.50x | +24.76% | -14.09% | 1.3192 | 1.758 | -6.87% | 29.2692 | LEVERED_REJECT |

## Year Slices

| Variant | Lev | 2024 | 2025 | 2026 YTD | 2026 maxDD | Roll-3m %+ |
|---|---:|---:|---:|---:|---:|---:|
| BENCHMARK_QQQ_BUY_HOLD | 1.00x | +27.74% | +20.77% | +16.88% | -11.72% | 0.7731 |
| BENCHMARK_SPY_BUY_HOLD | 1.00x | +25.59% | +17.71% | +8.49% | -8.88% | 0.8348 |
| REGIME_THROTTLED_QQQ_1_00x | 1.00x | +17.20% | +14.47% | +13.81% | -6.81% | 0.735 |
| REGIME_THROTTLED_BLEND_1_00x | 1.00x | +15.27% | +15.33% | +11.81% | -7.33% | 0.7332 |
| REGIME_THROTTLED_QQQ_1_25x | 1.25x | +20.39% | +17.21% | +17.03% | -8.49% | 0.726 |
| REGIME_THROTTLED_BLEND_1_25x | 1.25x | +18.00% | +18.35% | +14.48% | -9.12% | 0.7187 |
| REGIME_THROTTLED_QQQ_1_50x | 1.50x | +23.51% | +19.91% | +20.26% | -10.16% | 0.7151 |
| REGIME_THROTTLED_BLEND_1_50x | 1.50x | +20.67% | +21.35% | +17.15% | -10.88% | 0.7024 |

## Gap Risk (QQQ Underlying × Leverage)

Tail events in empirical QQQ daily-return distribution:

| Quantile | QQQ | 1.00x | 1.25x | 1.50x |
|---|---:|---:|---:|---:|
| p5.0 | -2.04% | -2.04% | -2.55% | -3.06% |
| p2.0 | -2.93% | -2.93% | -3.67% | -4.40% |
| p1.0 | -3.61% | -3.61% | -4.51% | -5.41% |
| p0.5 | -4.80% | -4.80% | -6.00% | -7.20% |
| p0.1 | -6.21% | -6.21% | -7.76% | -9.32% |

Monte Carlo 1-week (n=10000, seed=42):
| Leverage | p5 worst week | p1 worst week | median week |
|---|---:|---:|---:|
| 1.00x | -4.12% | -6.62% | +0.55% |
| 1.25x | -5.17% | -8.28% | +0.64% |
| 1.50x | -6.21% | -9.92% | +0.74% |

Worst historical consecutive 5-day outcome:
| Leverage | Worst 5-day |
|---|---:|
| 1.00x | -11.98% |
| 1.25x | -14.88% |
| 1.50x | -17.72% |

## Tax Estimate (Approximation — NOT Financial Advice)

Assumes daily-rebalanced ETF regime throttling → all realized gains short-term.
IRA/Roth/401k: $0 tax impact. Taxable brokerage worst-case below.

| Variant | Lev | Pre-tax CAGR | Worst-case (35% STCG) | Mid-case (17.5%) |
|---|---:|---:|---:|---:|
| BENCHMARK_QQQ_BUY_HOLD | 1.00x | +27.34% | +17.77% | +22.55% |
| BENCHMARK_SPY_BUY_HOLD | 1.00x | +21.37% | +13.89% | +17.63% |
| REGIME_THROTTLED_QQQ_1_00x | 1.00x | +18.95% | +12.31% | +15.63% |
| REGIME_THROTTLED_BLEND_1_00x | 1.00x | +17.65% | +11.47% | +14.56% |
| REGIME_THROTTLED_QQQ_1_25x | 1.25x | +22.83% | +14.84% | +18.83% |
| REGIME_THROTTLED_BLEND_1_25x | 1.25x | +21.21% | +13.78% | +17.50% |
| REGIME_THROTTLED_QQQ_1_50x | 1.50x | +26.70% | +17.35% | +22.03% |
| REGIME_THROTTLED_BLEND_1_50x | 1.50x | +24.76% | +16.10% | +20.43% |

## Gate Details

### REGIME_THROTTLED_QQQ_1_00x

- [PASS] **no_lookahead**: weight = prior-close regime × leverage, by construction (test-pinned)
- [FAIL] **cagr_beats_qqq_after_cost**: variant CAGR +18.95% vs gate +27.34% (Phase 2A QQQ)
- [PASS] **maxdd_lte_qqq_absolute**: maxDD -9.99% vs absolute floor -22.77% (must not exceed QQQ; not just vs unlevered base)
- [PASS] **sharpe_beats_qqq**: Sharpe 1.3857 vs gate 1.2738
- [PASS] **calmar_beats_qqq**: Calmar 1.8963 vs gate 1.2004
- [PASS] **not_one_year_dependent**: positive months 17/30, best share 0.2527
- [PASS] **all_years_positive**: 2024 +17.20%, 2025 +14.47%, 2026 YTD +13.81%

### REGIME_THROTTLED_BLEND_1_00x

- [PASS] **no_lookahead**: weight = prior-close regime × leverage, by construction (test-pinned)
- [FAIL] **cagr_beats_qqq_after_cost**: variant CAGR +17.65% vs gate +27.34% (Phase 2A QQQ)
- [PASS] **maxdd_lte_qqq_absolute**: maxDD -9.38% vs absolute floor -22.77% (must not exceed QQQ; not just vs unlevered base)
- [PASS] **sharpe_beats_qqq**: Sharpe 1.4132 vs gate 1.2738
- [PASS] **calmar_beats_qqq**: Calmar 1.8807 vs gate 1.2004
- [PASS] **not_one_year_dependent**: positive months 19/30, best share 0.2601
- [PASS] **all_years_positive**: 2024 +15.27%, 2025 +15.33%, 2026 YTD +11.81%

### REGIME_THROTTLED_QQQ_1_25x

- [PASS] **no_lookahead**: weight = prior-close regime × leverage, by construction (test-pinned)
- [FAIL] **cagr_beats_qqq_after_cost**: variant CAGR +22.83% vs gate +27.34% (Phase 2A QQQ)
- [PASS] **maxdd_lte_qqq_absolute**: maxDD -12.51% vs absolute floor -22.77% (must not exceed QQQ; not just vs unlevered base)
- [PASS] **sharpe_beats_qqq**: Sharpe 1.3341 vs gate 1.2738
- [PASS] **calmar_beats_qqq**: Calmar 1.8247 vs gate 1.2004
- [PASS] **not_one_year_dependent**: positive months 17/30, best share 0.2571
- [PASS] **all_years_positive**: 2024 +20.39%, 2025 +17.21%, 2026 YTD +17.03%

### REGIME_THROTTLED_BLEND_1_25x

- [PASS] **no_lookahead**: weight = prior-close regime × leverage, by construction (test-pinned)
- [FAIL] **cagr_beats_qqq_after_cost**: variant CAGR +21.21% vs gate +27.34% (Phase 2A QQQ)
- [PASS] **maxdd_lte_qqq_absolute**: maxDD -11.75% vs absolute floor -22.77% (must not exceed QQQ; not just vs unlevered base)
- [PASS] **sharpe_beats_qqq**: Sharpe 1.3568 vs gate 1.2738
- [PASS] **calmar_beats_qqq**: Calmar 1.8043 vs gate 1.2004
- [PASS] **not_one_year_dependent**: positive months 19/30, best share 0.2655
- [PASS] **all_years_positive**: 2024 +18.00%, 2025 +18.35%, 2026 YTD +14.48%

### REGIME_THROTTLED_QQQ_1_50x

- [PASS] **no_lookahead**: weight = prior-close regime × leverage, by construction (test-pinned)
- [FAIL] **cagr_beats_qqq_after_cost**: variant CAGR +26.70% vs gate +27.34% (Phase 2A QQQ)
- [PASS] **maxdd_lte_qqq_absolute**: maxDD -14.99% vs absolute floor -22.77% (must not exceed QQQ; not just vs unlevered base)
- [PASS] **sharpe_beats_qqq**: Sharpe 1.2996 vs gate 1.2738
- [PASS] **calmar_beats_qqq**: Calmar 1.7811 vs gate 1.2004
- [PASS] **not_one_year_dependent**: positive months 17/30, best share 0.2594
- [PASS] **all_years_positive**: 2024 +23.51%, 2025 +19.91%, 2026 YTD +20.26%

### REGIME_THROTTLED_BLEND_1_50x

- [PASS] **no_lookahead**: weight = prior-close regime × leverage, by construction (test-pinned)
- [FAIL] **cagr_beats_qqq_after_cost**: variant CAGR +24.76% vs gate +27.34% (Phase 2A QQQ)
- [PASS] **maxdd_lte_qqq_absolute**: maxDD -14.09% vs absolute floor -22.77% (must not exceed QQQ; not just vs unlevered base)
- [PASS] **sharpe_beats_qqq**: Sharpe 1.3192 vs gate 1.2738
- [PASS] **calmar_beats_qqq**: Calmar 1.758 vs gate 1.2004
- [PASS] **not_one_year_dependent**: positive months 18/30, best share 0.2686
- [PASS] **all_years_positive**: 2024 +20.67%, 2025 +21.35%, 2026 YTD +17.15%

## Safety

No paper signals, broker orders, trade proposals, production thresholds,
Gatekeeper/Veto/execution/governance/live-capital changes, historical evidence
mutations, or SHORT_A frozen-status changes. Regime labels strictly as-of;
exposure uses prior-close label (test-pinned). Borrow-rate model is a fixed
stated assumption, not a tuned parameter.

Candidates: **NONE**

