# Strategy Tournament — Results (research-only)

**Generated:** 2026-06-16T05:50:04  
**Phase:** 1G.4 · research-only (no signals, no execution, no live capital)  
**Event spine:** `data/state/stock_lens_forward_log.jsonl` (1547 snapshots)  
**Clean epoch:** ≥ 2026-05-08 · **friction** 0.3% round-trip  
**Headline regime:** Bull Continuation · **next maturity due** 2026-06-16

## 1. Executive summary

> **No strategy ready. Stay research-only.**

- **Best candidate:** `None` → **NEED_MORE_DATA**
- **Short side:** OFF (radar: SHORTS_OFF)
- **Options expression:** RESEARCH_ONLY
- **Recommended next phase:** No family qualifies. Stay research-only: accumulate matured forward outcomes (esp. a non-bull regime) and re-run the tournament.

## 2. Strategy ranking (clean epoch)

| rank | family | verdict | n(5d) | net 5d | net 10d | rel-SPY 5d | caution |
|---|---|---|---|---|---|---|---|
| 1 | SIMPLE_MOMENTUM_BASELINE | WATCHLIST_RESEARCH | 708 | 0.518 | 1.8501 | 0.6374 | 224 immature events excluded |
| 2 | LEADER_RESET | REJECT | 184 | -0.9981 | -1.2838 | -0.7891 | 42 immature events excluded |
| 3 | 13F_EMERGING | WATCHLIST_RESEARCH | 63 | -1.7106 | 0.0189 | -1.4476 | 18 immature events excluded |
| 4 | FAILED_LEADER_SHORT | NEED_MORE_DATA | 16 | 0.2494 | 3.4761 | 0.6561 | sample below minimum; 13 immature events excluded; edge collapses without the single best event (outlier-dependent) |
| 5 | OPTIONS_FLOW_CONFIRMATION | NEED_MORE_DATA | 29 | -0.7755 | -1.4431 | -1.0333 | sample below minimum; 10 immature events excluded |
| 6 | OPTIONS_EXPRESSION_ON_VALID_SETUP | NEED_MORE_DATA | 25 | -0.8651 | -1.457 | -1.1631 | sample below minimum; 6 immature events excluded |
| 7 | POST_EARNINGS_DRIFT | NEED_MORE_DATA | 0 | None | None | None | sample below minimum |
| 8 | RISK_OFF_RELATIVE_WEAKNESS_SHORT | NEED_MORE_DATA | 0 | None | None | None | sample below minimum |
| — | CASH_NO_TRADE (baseline) | — | 0 | 0.0 | 0.0 | — | flat |
| — | RANDOM_LIQUID_CONTROL (baseline) | — | 129 | 1.2055 | 1.9786 | 1.3978 | control |

## 3. Pass/fail verdicts

### LEADER_RESET — **REJECT**

Negative net expectancy at both horizons on an adequate sample.

- sample (resolved 5d): **184** · caution: 42 immature events excluded
- biggest weakness: Negative net expectancy at both horizons on an adequate sample.
- falsifier: reset cohorts fail to beat late/extended momentum across regimes

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `184` | ✅ |
| net_5d_expectancy_positive | > 0 | `-0.9981` | ❌ |
| net_10d_expectancy_positive | > 0 | `-1.2838` | ❌ |
| beats_spy | mean rel-SPY 5d > 0 | `-0.7891` | ❌ |
| beats_random_control | net 5d > random control | `[-0.9981, 1.2055]` | ❌ |
| beats_cash | net 5d > 0 | `-0.9981` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `-5.1355` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.3478` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `-1.1714` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### OPTIONS_EXPRESSION_ON_VALID_SETUP — **NEED_MORE_DATA**

Only 25 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.

- sample (resolved 5d): **25** · caution: sample below minimum; 6 immature events excluded
- biggest weakness: Only 25 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.
- falsifier: options structures add no edge over the underlying long after fees

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `25` | ❌ |
| net_5d_expectancy_positive | > 0 | `-0.8651` | ❌ |
| net_10d_expectancy_positive | > 0 | `-1.457` | ❌ |
| beats_spy | mean rel-SPY 5d > 0 | `-1.1631` | ❌ |
| beats_random_control | net 5d > random control | `[-0.8651, 1.2055]` | ❌ |
| beats_cash | net 5d > 0 | `-0.8651` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `-4.618` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.36` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `-1.3062` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### POST_EARNINGS_DRIFT — **NEED_MORE_DATA**

Only 0 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.

- sample (resolved 5d): **0** · caution: sample below minimum
- biggest weakness: No earnings-reaction data in the event spine — cannot be tested here.
- falsifier: drift cohorts show no continuation once earnings data is added

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `0` | ❌ |
| net_5d_expectancy_positive | > 0 | `None` | ❌ |
| net_10d_expectancy_positive | > 0 | `None` | ❌ |
| beats_spy | mean rel-SPY 5d > 0 | `None` | ❌ |
| beats_random_control | net 5d > random control | `[None, 1.2055]` | ❌ |
| beats_cash | net 5d > 0 | `None` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `None` | ❌ |
| stop_hit_rate_acceptable | < 0.5 | `None` | ❌ |
| not_one_outlier | net 5d > 0 after dropping best event | `None` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### OPTIONS_FLOW_CONFIRMATION — **NEED_MORE_DATA**

Only 29 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.

- sample (resolved 5d): **29** · caution: sample below minimum; 10 immature events excluded
- biggest weakness: Only 29 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.
- falsifier: confirming-flow cohort underperforms the no-flow long cohort

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `29` | ❌ |
| net_5d_expectancy_positive | > 0 | `-0.7755` | ❌ |
| net_10d_expectancy_positive | > 0 | `-1.4431` | ❌ |
| beats_spy | mean rel-SPY 5d > 0 | `-1.0333` | ❌ |
| beats_random_control | net 5d > random control | `[-0.7755, 1.2055]` | ❌ |
| beats_cash | net 5d > 0 | `-0.7755` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `-4.754` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.3448` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `-1.1504` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### 13F_EMERGING — **WATCHLIST_RESEARCH**

Adequate sample but edge is mixed/marginal vs baselines.

- sample (resolved 5d): **63** · caution: 18 immature events excluded
- biggest weakness: Adequate sample but edge is mixed/marginal vs baselines.
- falsifier: emerging names underperform crowded leaders / random control

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `63` | ✅ |
| net_5d_expectancy_positive | > 0 | `-1.7106` | ❌ |
| net_10d_expectancy_positive | > 0 | `0.0189` | ✅ |
| beats_spy | mean rel-SPY 5d > 0 | `-1.4476` | ❌ |
| beats_random_control | net 5d > random control | `[-1.7106, 1.2055]` | ❌ |
| beats_cash | net 5d > 0 | `-1.7106` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `-6.0544` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.4762` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `-2.1149` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### SIMPLE_MOMENTUM_BASELINE — **WATCHLIST_RESEARCH**

Adequate sample but edge is mixed/marginal vs baselines.

- sample (resolved 5d): **708** · caution: 224 immature events excluded
- biggest weakness: Adequate sample but edge is mixed/marginal vs baselines.
- falsifier: complex families fail to beat this simple baseline

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `708` | ✅ |
| net_5d_expectancy_positive | > 0 | `0.518` | ✅ |
| net_10d_expectancy_positive | > 0 | `1.8501` | ✅ |
| beats_spy | mean rel-SPY 5d > 0 | `0.6374` | ✅ |
| beats_random_control | net 5d > random control | `[0.518, 1.2055]` | ❌ |
| beats_cash | net 5d > 0 | `0.518` | ✅ |
| mae_acceptable | mean 5d MAE > -8.0 | `-4.7842` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.3263` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `0.4264` | ✅ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### FAILED_LEADER_SHORT — **NEED_MORE_DATA**

Only 16 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.

- sample (resolved 5d): **16** · caution: sample below minimum; 13 immature events excluded; edge collapses without the single best event (outlier-dependent)
- biggest weakness: Only 16 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.
- falsifier: short cohort is net-negative or worse than cash in any regime

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `16` | ❌ |
| net_5d_expectancy_positive | > 0 | `0.2494` | ✅ |
| net_10d_expectancy_positive | > 0 | `3.4761` | ✅ |
| beats_spy | mean rel-SPY 5d > 0 | `0.6561` | ✅ |
| beats_random_control | net 5d > random control | `[0.2494, 1.2055]` | ❌ |
| beats_cash | net 5d > 0 | `0.2494` | ✅ |
| mae_acceptable | mean 5d MAE > -8.0 | `-5.3377` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.3125` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `-0.2182` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### RISK_OFF_RELATIVE_WEAKNESS_SHORT — **NEED_MORE_DATA**

Only 0 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.

- sample (resolved 5d): **0** · caution: sample below minimum
- biggest weakness: Only 0 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.
- falsifier: no positive edge once a real risk-off sample exists

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `0` | ❌ |
| net_5d_expectancy_positive | > 0 | `None` | ❌ |
| net_10d_expectancy_positive | > 0 | `None` | ❌ |
| beats_spy | mean rel-SPY 5d > 0 | `None` | ❌ |
| beats_random_control | net 5d > random control | `[None, 1.2055]` | ❌ |
| beats_cash | net 5d > 0 | `None` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `None` | ❌ |
| stop_hit_rate_acceptable | < 0.5 | `None` | ❌ |
| not_one_outlier | net 5d > 0 after dropping best event | `None` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

## 4. Current sleeve verdicts

| sleeve | registry status | disposition | evidence |
|---|---|---|---|
| SNIPER | decommissioned | KEEP PAPER (low evidence) | Active paper but too few matured outcomes to judge; keep gathering. |
| VOYAGER | decommissioned | RESEARCH ONLY | Weak approval->signal conversion (see voyager_conversion_audit); noisy, rarely converts. Keep paper logging but treat as research. |
| SHORT_A | frozen | FREEZE (research only) | Frozen 2026-05-24; net-negative, noisy, fights bull tape. No risk-off sample exists to revalidate. Keep frozen. |
| REMORA | frozen | FREEZE | Frozen; no tournament cohort supports reactivation. |
| CONTRARIAN | frozen | FREEZE | Frozen; mean-reversion thesis untested in this dataset. |
| PATHFINDER | future_research | RESEARCH ONLY | Future-research only; no active cohort. |
| ALPHA_DISCOVERY | research engine (strongest component) | KEEP / RESEARCH ENGINE | Strongest current component: its tracks drive the LEADER_RESET (emerging->13F net5d=-1.7106, reset net5d=-0.9981) cohorts. Keep as the discovery feed, not a direct executor. |

## 5. Best next paper candidate

No family qualifies. Stay research-only: accumulate matured forward outcomes (esp. a non-bull regime) and re-run the tournament.

## 6. Best options expression

RESEARCH_ONLY — label counts across valid-setup events: `{'NO_OPTIONS_EDGE': 858, 'CALL_DEBIT_SPREAD': 41}`. Theoretical option P/L is **unavailable** (no historical chain); only structure labels are emitted.

## 7. Short-side readiness

Short side: **OFF** (radar state `SHORTS_OFF`). No risk-off/stress sample exists in the spine, so short families are structurally untestable and stay research-only.

## 8. No-trade / cash recommendation

No strategy ready. Stay research-only.

## 9. Data limitations

- Single-regime sample: only Bull Continuation / Bull Pullback present (no risk-off / stress / high-VIX) — short & risk-off families cannot be tested.
- No per-event earnings-reaction field — POST_EARNINGS_DRIFT is NOT_ENOUGH_DATA.
- No historical options chain — options theoretical P/L is unavailable; options expression is structure-label only.
- rel-return vs QQQ / sector ETF not present per event (only vs SPY).
- 20d forward outcomes not yet resolved in any row.
- Stop/target order unknown when both touched — sim assumes stop first (conservative).

## 10. Recommended next phase

No family qualifies. Stay research-only: accumulate matured forward outcomes (esp. a non-bull regime) and re-run the tournament.

### Anti-label-fix guarantees

- Immature events are excluded from every pass/fail figure.
- BLOCKED / NO_EDGE / NOT_ENOUGH_DATA counts are reported, not hidden.
- Gate thresholds are fixed in-module, not tuned on this sample.
- Verdicts derive from gates; no verdict is upgraded by renaming.
- Single-outlier dependence is tested (drop-best-event recompute).

> Research-only. This lab emits no signals, no orders, no trade proposals, and changes no registry or live-capital setting.
