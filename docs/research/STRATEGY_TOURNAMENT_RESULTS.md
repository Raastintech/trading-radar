# Strategy Tournament — Results (research-only)

**Generated:** 2026-06-13T00:38:06  
**Phase:** 1G.4 · research-only (no signals, no execution, no live capital)  
**Event spine:** `data/state/stock_lens_forward_log.jsonl` (1417 snapshots)  
**Clean epoch:** ≥ 2026-05-08 · **friction** 0.3% round-trip  
**Headline regime:** Chop / Range · **next maturity due** 2026-06-13

## 1. Executive summary

> **No strategy ready for paper. Best candidate earns a deeper point-in-time backtest only. Stay research-only.**

- **Best candidate:** `SIMPLE_MOMENTUM_BASELINE` → **READY_FOR_DEEPER_BACKTEST**
- **Short side:** OFF (radar: SHORTS_OFF)
- **Options expression:** RESEARCH_ONLY
- **Recommended next phase:** Run a point-in-time backtest for SIMPLE_MOMENTUM_BASELINE across regimes before any paper spec. Stay research-only; do not activate.

## 2. Strategy ranking (clean epoch)

| rank | family | verdict | n(5d) | net 5d | net 10d | rel-SPY 5d | caution |
|---|---|---|---|---|---|---|---|
| 1 | SIMPLE_MOMENTUM_BASELINE | READY_FOR_DEEPER_BACKTEST | 696 | 0.504 | 1.9393 | 0.6498 | 143 immature events excluded |
| 2 | LEADER_RESET | REJECT | 180 | -1.0904 | -1.1256 | -0.8364 | 32 immature events excluded |
| 3 | 13F_EMERGING | WATCHLIST_RESEARCH | 63 | -1.7106 | 0.0189 | -1.4476 | 11 immature events excluded |
| 4 | FAILED_LEADER_SHORT | NEED_MORE_DATA | 16 | 0.2494 | 3.4761 | 0.6561 | sample below minimum; 9 immature events excluded; edge collapses without the single best event (outlier-dependent) |
| 5 | OPTIONS_FLOW_CONFIRMATION | NEED_MORE_DATA | 26 | -1.2804 | -1.4431 | -1.3589 | sample below minimum; 8 immature events excluded |
| 6 | OPTIONS_EXPRESSION_ON_VALID_SETUP | NEED_MORE_DATA | 22 | -1.474 | -1.457 | -1.5656 | sample below minimum; 7 immature events excluded |
| 7 | POST_EARNINGS_DRIFT | NEED_MORE_DATA | 0 | None | None | None | sample below minimum |
| 8 | RISK_OFF_RELATIVE_WEAKNESS_SHORT | NEED_MORE_DATA | 0 | None | None | None | sample below minimum |
| — | CASH_NO_TRADE (baseline) | — | 0 | 0.0 | 0.0 | — | flat |
| — | RANDOM_LIQUID_CONTROL (baseline) | — | 130 | 0.4249 | 1.0009 | 0.5715 | control |

## 3. Pass/fail verdicts

### LEADER_RESET — **REJECT**

Negative net expectancy at both horizons on an adequate sample.

- sample (resolved 5d): **180** · caution: 32 immature events excluded
- biggest weakness: Negative net expectancy at both horizons on an adequate sample.
- falsifier: reset cohorts fail to beat late/extended momentum across regimes

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `180` | ✅ |
| net_5d_expectancy_positive | > 0 | `-1.0904` | ❌ |
| net_10d_expectancy_positive | > 0 | `-1.1256` | ❌ |
| beats_spy | mean rel-SPY 5d > 0 | `-0.8364` | ❌ |
| beats_random_control | net 5d > random control | `[-1.0904, 0.4249]` | ❌ |
| beats_cash | net 5d > 0 | `-1.0904` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `-5.168` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.3556` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `-1.268` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### OPTIONS_EXPRESSION_ON_VALID_SETUP — **NEED_MORE_DATA**

Only 22 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.

- sample (resolved 5d): **22** · caution: sample below minimum; 7 immature events excluded
- biggest weakness: Only 22 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.
- falsifier: options structures add no edge over the underlying long after fees

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `22` | ❌ |
| net_5d_expectancy_positive | > 0 | `-1.474` | ❌ |
| net_10d_expectancy_positive | > 0 | `-1.457` | ❌ |
| beats_spy | mean rel-SPY 5d > 0 | `-1.5656` | ❌ |
| beats_random_control | net 5d > random control | `[-1.474, 0.4249]` | ❌ |
| beats_cash | net 5d > 0 | `-1.474` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `-4.6821` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.4091` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `-2.0072` | ❌ |
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
| beats_random_control | net 5d > random control | `[None, 0.4249]` | ❌ |
| beats_cash | net 5d > 0 | `None` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `None` | ❌ |
| stop_hit_rate_acceptable | < 0.5 | `None` | ❌ |
| not_one_outlier | net 5d > 0 after dropping best event | `None` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### OPTIONS_FLOW_CONFIRMATION — **NEED_MORE_DATA**

Only 26 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.

- sample (resolved 5d): **26** · caution: sample below minimum; 8 immature events excluded
- biggest weakness: Only 26 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.
- falsifier: confirming-flow cohort underperforms the no-flow long cohort

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `26` | ❌ |
| net_5d_expectancy_positive | > 0 | `-1.2804` | ❌ |
| net_10d_expectancy_positive | > 0 | `-1.4431` | ❌ |
| beats_spy | mean rel-SPY 5d > 0 | `-1.3589` | ❌ |
| beats_random_control | net 5d > random control | `[-1.2804, 0.4249]` | ❌ |
| beats_cash | net 5d > 0 | `-1.2804` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `-4.8239` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.3846` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `-1.7205` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### 13F_EMERGING — **WATCHLIST_RESEARCH**

Adequate sample but edge is mixed/marginal vs baselines.

- sample (resolved 5d): **63** · caution: 11 immature events excluded
- biggest weakness: Adequate sample but edge is mixed/marginal vs baselines.
- falsifier: emerging names underperform crowded leaders / random control

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `63` | ✅ |
| net_5d_expectancy_positive | > 0 | `-1.7106` | ❌ |
| net_10d_expectancy_positive | > 0 | `0.0189` | ✅ |
| beats_spy | mean rel-SPY 5d > 0 | `-1.4476` | ❌ |
| beats_random_control | net 5d > random control | `[-1.7106, 0.4249]` | ❌ |
| beats_cash | net 5d > 0 | `-1.7106` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `-6.0544` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.4762` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `-2.1149` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### SIMPLE_MOMENTUM_BASELINE — **READY_FOR_DEEPER_BACKTEST**

Clears edge + control + risk gates, but on a single-regime (bull-tape) sample — earns a point-in-time backtest, not yet paper.

- sample (resolved 5d): **696** · caution: 143 immature events excluded
- biggest weakness: Clears edge + control + risk gates, but on a single-regime (bull-tape) sample — earns a point-in-time backtest, not yet paper.
- falsifier: complex families fail to beat this simple baseline

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `696` | ✅ |
| net_5d_expectancy_positive | > 0 | `0.504` | ✅ |
| net_10d_expectancy_positive | > 0 | `1.9393` | ✅ |
| beats_spy | mean rel-SPY 5d > 0 | `0.6498` | ✅ |
| beats_random_control | net 5d > random control | `[0.504, 0.4249]` | ✅ |
| beats_cash | net 5d > 0 | `0.504` | ✅ |
| mae_acceptable | mean 5d MAE > -8.0 | `-4.8016` | ✅ |
| stop_hit_rate_acceptable | < 0.5 | `0.329` | ✅ |
| not_one_outlier | net 5d > 0 after dropping best event | `0.4107` | ✅ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

### FAILED_LEADER_SHORT — **NEED_MORE_DATA**

Only 16 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.

- sample (resolved 5d): **16** · caution: sample below minimum; 9 immature events excluded; edge collapses without the single best event (outlier-dependent)
- biggest weakness: Only 16 resolved-5d clean-epoch events (< 30). Cannot accept or reject — accumulate more matured evidence.
- falsifier: short cohort is net-negative or worse than cash in any regime

| gate | need | got | pass |
|---|---|---|---|
| min_mature_sample | >= 30 resolved 5d | `16` | ❌ |
| net_5d_expectancy_positive | > 0 | `0.2494` | ✅ |
| net_10d_expectancy_positive | > 0 | `3.4761` | ✅ |
| beats_spy | mean rel-SPY 5d > 0 | `0.6561` | ✅ |
| beats_random_control | net 5d > random control | `[0.2494, 0.4249]` | ❌ |
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
| beats_random_control | net 5d > random control | `[None, 0.4249]` | ❌ |
| beats_cash | net 5d > 0 | `None` | ❌ |
| mae_acceptable | mean 5d MAE > -8.0 | `None` | ❌ |
| stop_hit_rate_acceptable | < 0.5 | `None` | ❌ |
| not_one_outlier | net 5d > 0 after dropping best event | `None` | ❌ |
| risk_definable | stop defined & heat-cap fit | `True` | ✅ |

## 4. Current sleeve verdicts

| sleeve | registry status | disposition | evidence |
|---|---|---|---|
| SNIPER | active_paper | KEEP PAPER (low evidence) | Active paper but too few matured outcomes to judge; keep gathering. |
| VOYAGER | active_paper | RESEARCH ONLY | Weak approval->signal conversion (see voyager_conversion_audit); noisy, rarely converts. Keep paper logging but treat as research. |
| SHORT_A | frozen | FREEZE (research only) | Frozen 2026-05-24; net-negative, noisy, fights bull tape. No risk-off sample exists to revalidate. Keep frozen. |
| REMORA | frozen | FREEZE | Frozen; no tournament cohort supports reactivation. |
| CONTRARIAN | frozen | FREEZE | Frozen; mean-reversion thesis untested in this dataset. |
| PATHFINDER | future_research | RESEARCH ONLY | Future-research only; no active cohort. |
| ALPHA_DISCOVERY | research engine (strongest component) | KEEP / RESEARCH ENGINE | Strongest current component: its tracks drive the LEADER_RESET (emerging->13F net5d=-1.7106, reset net5d=-1.0904) cohorts. Keep as the discovery feed, not a direct executor. |

## 5. Best next paper candidate

Run a point-in-time backtest for SIMPLE_MOMENTUM_BASELINE across regimes before any paper spec. Stay research-only; do not activate.

## 6. Best options expression

RESEARCH_ONLY — label counts across valid-setup events: `{'NO_OPTIONS_EDGE': 781, 'CALL_DEBIT_SPREAD': 39}`. Theoretical option P/L is **unavailable** (no historical chain); only structure labels are emitted.

## 7. Short-side readiness

Short side: **OFF** (radar state `SHORTS_OFF`). No risk-off/stress sample exists in the spine, so short families are structurally untestable and stay research-only.

## 8. No-trade / cash recommendation

No strategy ready for paper. Best candidate earns a deeper point-in-time backtest only. Stay research-only.

## 9. Data limitations

- Single-regime sample: only Bull Continuation / Bull Pullback present (no risk-off / stress / high-VIX) — short & risk-off families cannot be tested.
- No per-event earnings-reaction field — POST_EARNINGS_DRIFT is NOT_ENOUGH_DATA.
- No historical options chain — options theoretical P/L is unavailable; options expression is structure-label only.
- rel-return vs QQQ / sector ETF not present per event (only vs SPY).
- 20d forward outcomes not yet resolved in any row.
- Stop/target order unknown when both touched — sim assumes stop first (conservative).

## 10. Recommended next phase

Run a point-in-time backtest for SIMPLE_MOMENTUM_BASELINE across regimes before any paper spec. Stay research-only; do not activate.

### Anti-label-fix guarantees

- Immature events are excluded from every pass/fail figure.
- BLOCKED / NO_EDGE / NOT_ENOUGH_DATA counts are reported, not hidden.
- Gate thresholds are fixed in-module, not tuned on this sample.
- Verdicts derive from gates; no verdict is upgraded by renaming.
- Single-outlier dependence is tested (drop-best-event recompute).

> Research-only. This lab emits no signals, no orders, no trade proposals, and changes no registry or live-capital setting.
