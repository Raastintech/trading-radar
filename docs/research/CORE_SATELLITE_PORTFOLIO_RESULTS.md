# Core-Satellite Portfolio Results (Phase 2A)

Generated: 2026-06-13T00:59:26.059227+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

## DID WE FIND A CORE PORTFOLIO ENGINE? YES

Verdict: **CORE_ENGINE_CANDIDATE**. Window `2024-01-02`..`2026-06-11` (613 trading days). Regime-day counts: `{'BULL_TREND': 306, 'CHOP': 149, 'MARKET_CORRECTION': 8, 'TECH_LED_CORRECTION': 74, 'RECOVERY_RECLAIM': 16, 'HIGH_VOLATILITY': 17, 'RISK_OFF': 43}`.

cash earns 0% (no T-bill history retained); throttled/static returns are understated by roughly the cash yield.

## Variant Table

| Variant | Return | CAGR | MaxDD | Sharpe | Sortino | Calmar | Vol | Hit | Worst M | Exposure | TimeInMkt | Turn/yr | R/Exp | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BENCHMARK_SPY_BUY_HOLD | +60.39% | +21.37% | -18.76% | 1.2995 | 1.6833 | 1.1391 | +15.95% | 0.7 | 2025-03 -5.57% | +100.00% | 1.0 | 0.0 | +60.39% | BENCHMARK |
| BENCHMARK_QQQ_BUY_HOLD | +80.31% | +27.34% | -22.77% | 1.2738 | 1.7084 | 1.2004 | +20.75% | 0.6667 | 2025-03 -7.59% | +100.00% | 1.0 | 0.0 | +80.31% | BENCHMARK |
| STATIC_60_QQQ_40_CASH | +44.22% | +16.20% | -14.20% | 1.2738 | 1.7084 | 1.1406 | +12.45% | 0.6667 | 2025-03 -4.55% | +60.00% | 1.0 | 0.0 | +73.71% | BENCHMARK |
| STATIC_80_QQQ_20_CASH | +61.60% | +21.74% | -18.57% | 1.2738 | 1.7084 | 1.1707 | +16.60% | 0.6667 | 2025-03 -6.07% | +80.00% | 1.0 | 0.0 | +76.99% | BENCHMARK |
| REGIME_THROTTLED_QQQ | +52.69% | +18.95% | -9.99% | 1.3857 | 1.7562 | 1.8963 | +13.21% | 0.5667 | 2026-06 -4.05% | +74.73% | 0.9984 | 19.5128 | +70.50% | CORE_ENGINE_CANDIDATE |
| REGIME_THROTTLED_SPY_QQQ_BLEND | +48.65% | +17.65% | -9.38% | 1.4132 | 1.7541 | 1.8807 | +12.07% | 0.6333 | 2026-06 -4.53% | +74.73% | 0.9984 | 19.5128 | +65.10% | CORE_ENGINE_CANDIDATE |
| CORE_PLUS_SNIPER_OBSERVATION | +47.37% | +17.23% | -9.02% | 1.3989 | 1.7728 | 1.9103 | +11.93% | 0.5667 | 2026-06 -3.64% | +77.26% | 1.0 | 17.5615 | +61.31% | OBSERVATION_ONLY |
| CORE_PLUS_LRR_C1_WATCH | +48.97% | +17.75% | -9.22% | 1.4238 | 1.813 | 1.9241 | +12.04% | 0.5667 | 2026-06 -3.64% | +77.26% | 1.0 | 17.5615 | +63.39% | OBSERVATION_ONLY |
| CORE_PLUS_BOTH_SATELLITES | +45.94% | +16.76% | -8.69% | 1.4267 | 1.8154 | 1.9297 | +11.37% | 0.5667 | 2026-06 -3.44% | +78.52% | 1.0 | 16.5859 | +58.50% | OBSERVATION_ONLY |

## Year Slices

| Variant | 2024 | 2025 | 2026 YTD | 2026 maxDD | Rolling-3m %pos |
|---|---:|---:|---:|---:|---:|
| BENCHMARK_SPY_BUY_HOLD | +25.59% | +17.71% | +8.49% | -8.88% | 0.8348 |
| BENCHMARK_QQQ_BUY_HOLD | +27.74% | +20.77% | +16.88% | -11.72% | 0.7731 |
| STATIC_60_QQQ_40_CASH | +16.27% | +12.72% | +10.04% | -7.15% | 0.7804 |
| STATIC_80_QQQ_20_CASH | +21.95% | +16.80% | +13.45% | -9.45% | 0.7804 |
| REGIME_THROTTLED_QQQ | +17.20% | +14.47% | +13.81% | -6.81% | 0.735 |
| REGIME_THROTTLED_SPY_QQQ_BLEND | +15.27% | +15.33% | +11.81% | -7.33% | 0.7332 |
| CORE_PLUS_SNIPER_OBSERVATION | +15.97% | +12.93% | +12.53% | -6.18% | 0.7387 |
| CORE_PLUS_LRR_C1_WATCH | +16.47% | +13.45% | +12.74% | -5.86% | 0.7477 |
| CORE_PLUS_BOTH_SATELLITES | +15.73% | +12.55% | +12.04% | -5.62% | 0.7459 |

## Core Gates

### REGIME_THROTTLED_QQQ

- [PASS] no_lookahead: weight uses prior-close regime by construction (test-pinned)
- [PASS] full_span_positive: +52.69%
- [PASS] ytd_2026_positive: +13.81%
- [PASS] risk_or_return_vs_qqq: maxDD -9.99% vs QQQ -22.77% (need <=70%), return +52.69% vs QQQ +80.31%
- [PASS] sharpe_or_calmar_beats_index: sharpe 1.3857 vs max(SPY 1.2995, QQQ 1.2738); calmar 1.8963 vs max(1.1391, 1.2004)
- [PASS] not_one_month_dependent: positive months 17/30, best share 0.2527
- [PASS] churn_acceptable: change days 0.1993 (max 0.25), turnover/yr 19.5128 (max 25.0)
- [PASS] simple_to_operate: 7-label ladder, one decision per close, daily-or-slower rebalance
- same-exposure SPY: return +41.39%, sharpe 1.568, calmar 2.0316
- same-exposure QQQ: return +52.69%, sharpe 1.3857, calmar 1.8963

### REGIME_THROTTLED_SPY_QQQ_BLEND

- [PASS] no_lookahead: weight uses prior-close regime by construction (test-pinned)
- [PASS] full_span_positive: +48.65%
- [PASS] ytd_2026_positive: +11.81%
- [PASS] risk_or_return_vs_qqq: maxDD -9.38% vs QQQ -22.77% (need <=70%), return +48.65% vs QQQ +80.31%
- [PASS] sharpe_or_calmar_beats_index: sharpe 1.4132 vs max(SPY 1.2995, QQQ 1.2738); calmar 1.8807 vs max(1.1391, 1.2004)
- [PASS] not_one_month_dependent: positive months 19/30, best share 0.2601
- [PASS] churn_acceptable: change days 0.1993 (max 0.25), turnover/yr 19.5128 (max 25.0)
- [PASS] simple_to_operate: 7-label ladder, one decision per close, daily-or-slower rebalance
- same-exposure SPY: return +41.39%, sharpe 1.568, calmar 2.0316
- same-exposure QQQ: return +52.69%, sharpe 1.3857, calmar 1.8963

## Satellite Value-Add

- **CORE_PLUS_BOTH_SATELLITES**: OBSERVATION_ONLY — dCAGR -2.18%, dSharpe 0.041, dMaxDD +1.31% vs REGIME_THROTTLED_QQQ. satellite adds complexity without risk-adjusted improvement; keep observation-only.
- **CORE_PLUS_LRR_C1_WATCH**: OBSERVATION_ONLY — dCAGR -1.20%, dSharpe 0.0381, dMaxDD +0.77% vs REGIME_THROTTLED_QQQ. satellite adds complexity without risk-adjusted improvement; keep observation-only.
- **CORE_PLUS_SNIPER_OBSERVATION**: OBSERVATION_ONLY — dCAGR -1.72%, dSharpe 0.0132, dMaxDD +0.97% vs REGIME_THROTTLED_QQQ. satellite adds complexity without risk-adjusted improvement; keep observation-only.

## Safety

No paper signals, broker orders, trade proposals (doc-only), production thresholds, Gatekeeper/Veto logic,
execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.
Regime labels are strictly as-of; exposure uses the prior close's label (test-pinned).

