# M1 Shadow Source Pool

*Session 2026-09-04. Generated 2026-09-08T03:24:11Z by `research/backtests/m1_shadow_pool.py`. Zero provider calls.*

> **RESEARCH ONLY — this is a POOL, not a list of ideas.** Membership is the only claim. Names are alphabetical; position carries no information. No ranking, no conviction tiers, no scores, no trade signals, and no live verdict. Must never be pooled with the live forward ledger, program verdicts, HC/EO routing, Alpha Focus, MCP or the dashboard.

## Why a pool and not a shortlist

M1 (the 12-1 momentum quintile) is the only strategy family in this programme with out-of-sample support, and it works as a **basket**. Its ticker-clustered confidence interval is negative at every horizon tested: the basket beats the tape while the typical name inside it does not. Thirteen conviction overlays, an earnings-surprise dataset and a complete analyst-action study all failed to rank names inside it. So this report publishes membership and refuses to order it.

## 1. Data integrity

| field | value |
|---|---|
| status | M1_DATA_DEGRADED |
| may_run | True |
| universe_size | 3363 |
| names_with_enough_bars | 3147 |
| coverage_pct | 93.58 |
| median_bars | 1678.0 |
| stale_count | 19 |
| shallow_count | 177 |
| contamination_pct | 1.546 |
| m1_pool_size_if_run | 626 |
| refusal_reason | None |

The guard (`m1_data_guard`) ran first and returned `M1_DATA_DEGRADED`. Had it refused, this document would not exist.

## 2. The pool

- membership (12-1 quintile): **626** of 3130 eligible names
- published: **75**, uniform random draw without replacement from membership (seed 20260904, session date (YYYYMMDD) unless --seed overrides)
- Taking the 'top N' of the membership would be a ranking, and the ticker-clustered evidence says position inside M1 carries no information. A seeded uniform draw publishes a readable slice without making a claim the evidence does not support.

```
ANIK     ANNX     ARIS     ATKR     AVBP     BE       BFLY     CDNA     CEVA     CGEM   
CNC      COKE     CORT     CRDO     CYPH     DELL     DNTH     ECPG     FEIM     FIGS   
FLNC     FORM     FTH      GOOGL    HBB      HSBC     ILMN     INTC     JBHT     JBL    
KOD      KODK     KOPN     KRYS     LBRT     LCUT     LFST     LGND     LINC     LQDA   
MEI      MG       MPC      MTRN     MYRG     NKTR     NUAI     NUVB     ONDS     PBF    
PLX      PRM      RIG      RIO      RLJ      ROG      ROST     RXT      SABS     SANM   
SITM     SKE      SRZN     STOK     SYRE     TECK     TEO      TGTX     TPR      TWLO   
VRT      WDC      XMTR     ZBIO     ZNTL   
```

## 3. Tags

Annotations, not scores. Nothing here is combined into a number.

| ticker | sector | analyst attention | earnings | quality | liquidity | risk flags |
|---|---|---|---|---|---|---|
| ANIK | UNKNOWN | covered_no_change | no_recent_report_in_cached_feed | quality_fail | thin_under_5M | — |
| ANNX | UNKNOWN | rating_raised | no_recent_report_in_cached_feed | quality_fail | moderate_5M_20M | below_ma200 |
| ARIS | UNKNOWN | no_action_in_window | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| ATKR | Industrials | rating_cut | no_recent_report_in_cached_feed | no_fundamental_data | liquid_20M_100M | at_or_near_52w_high |
| AVBP | Healthcare | covered_no_change | no_recent_report_in_cached_feed | quality_fail | moderate_5M_20M | — |
| BE | Industrials | rating_raised | no_recent_report_in_cached_feed | quality_fail | very_liquid_100M_plus | — |
| BFLY | Healthcare | no_action_in_window | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | high_daily_range |
| CDNA | Healthcare | covered_no_change | no_recent_report_in_cached_feed | quality_pass | liquid_20M_100M | at_or_near_52w_high |
| CEVA | Technology | covered_no_change | no_recent_report_in_cached_feed | no_fundamental_data | liquid_20M_100M | deep_drawdown_from_52w_high, below_ma200 |
| CGEM | Healthcare | covered_no_change | no_recent_report_in_cached_feed | quality_fail | moderate_5M_20M | — |
| CNC | Healthcare | rating_raised | no_recent_report_in_cached_feed | quality_fail | very_liquid_100M_plus | at_or_near_52w_high |
| COKE | Consumer Defensive | no_coverage_data | no_recent_report_in_cached_feed | quality_pass | liquid_20M_100M | — |
| CORT | Healthcare | rating_cut | no_recent_report_in_cached_feed | quality_pass | liquid_20M_100M | — |
| CRDO | Technology | covered_no_change | no_recent_report_in_cached_feed | quality_fail | very_liquid_100M_plus | high_daily_range, deep_drawdown_from_52w_high, below_ma200 |
| CYPH | UNKNOWN | no_coverage_data | no_recent_report_in_cached_feed | quality_fail | moderate_5M_20M | extended_far_above_ma50, high_daily_range |
| DELL | Technology | rating_cut | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | at_or_near_52w_high |
| DNTH | Healthcare | covered_no_change | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| ECPG | Financial Services | covered_no_change | no_recent_report_in_cached_feed | no_fundamental_data | liquid_20M_100M | — |
| FEIM | Technology | no_action_in_window | no_recent_report_in_cached_feed | quality_fail | moderate_5M_20M | — |
| FIGS | UNKNOWN | covered_no_change | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| FLNC | Utilities | rating_changes_both_ways | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | deep_drawdown_from_52w_high, below_ma200 |
| FORM | Technology | rating_raised | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | — |
| FTH | UNKNOWN | no_coverage_data | no_recent_report_in_cached_feed | no_fundamental_data | moderate_5M_20M | extended_far_above_ma50, at_or_near_52w_high |
| GOOGL | Communication Services | covered_no_change | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | — |
| HBB | UNKNOWN | no_coverage_data | no_recent_report_in_cached_feed | no_fundamental_data | thin_under_5M | — |
| HSBC | Financial Services | no_action_in_window | no_recent_report_in_cached_feed | quality_fail | very_liquid_100M_plus | at_or_near_52w_high |
| ILMN | Healthcare | no_coverage_data | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | — |
| INTC | Technology | rating_raised | no_recent_report_in_cached_feed | quality_fail | very_liquid_100M_plus | — |
| JBHT | Industrials | rating_changes_both_ways | no_recent_report_in_cached_feed | no_fundamental_data | very_liquid_100M_plus | — |
| JBL | Technology | rating_raised | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | — |
| KOD | Healthcare | covered_no_change | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| KODK | Industrials | no_coverage_data | no_recent_report_in_cached_feed | quality_fail | moderate_5M_20M | — |
| KOPN | Technology | covered_no_change | no_recent_report_in_cached_feed | quality_fail | moderate_5M_20M | — |
| KRYS | Healthcare | covered_no_change | no_recent_report_in_cached_feed | quality_pass | liquid_20M_100M | — |
| LBRT | Energy | no_action_in_window | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | below_ma200 |
| LCUT | UNKNOWN | no_coverage_data | no_recent_report_in_cached_feed | no_fundamental_data | thin_under_5M | — |
| LFST | Healthcare | rating_cut | no_recent_report_in_cached_feed | quality_pass | liquid_20M_100M | at_or_near_52w_high |
| LGND | Healthcare | covered_no_change | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| LINC | Consumer Defensive | covered_no_change | no_recent_report_in_cached_feed | no_fundamental_data | liquid_20M_100M | deep_drawdown_from_52w_high, below_ma200 |
| LQDA | Healthcare | no_action_in_window | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| MEI | Technology | rating_changes_both_ways | no_recent_report_in_cached_feed | quality_fail | moderate_5M_20M | high_daily_range |
| MG | UNKNOWN | no_action_in_window | no_recent_report_in_cached_feed | no_fundamental_data | thin_under_5M | at_or_near_52w_high |
| MPC | Energy | covered_no_change | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | at_or_near_52w_high |
| MTRN | Basic Materials | covered_no_change | no_recent_report_in_cached_feed | no_fundamental_data | liquid_20M_100M | — |
| MYRG | Industrials | covered_no_change | no_recent_report_in_cached_feed | no_fundamental_data | liquid_20M_100M | deep_drawdown_from_52w_high, below_ma200 |
| NKTR | Healthcare | no_action_in_window | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| NUAI | Energy | no_coverage_data | no_recent_report_in_cached_feed | no_fundamental_data | liquid_20M_100M | high_daily_range |
| NUVB | UNKNOWN | covered_no_change | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| ONDS | Technology | covered_no_change | no_recent_report_in_cached_feed | quality_fail | very_liquid_100M_plus | deep_drawdown_from_52w_high, below_ma200 |
| PBF | UNKNOWN | rating_raised | no_recent_report_in_cached_feed | quality_fail | very_liquid_100M_plus | at_or_near_52w_high |
| PLX | UNKNOWN | no_action_in_window | no_recent_report_in_cached_feed | no_fundamental_data | thin_under_5M | — |
| PRM | Basic Materials | covered_no_change | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| RIG | Energy | covered_no_change | no_recent_report_in_cached_feed | quality_fail | very_liquid_100M_plus | — |
| RIO | Basic Materials | no_coverage_data | no_recent_report_in_cached_feed | no_fundamental_data | very_liquid_100M_plus | — |
| RLJ | UNKNOWN | no_coverage_data | no_recent_report_in_cached_feed | no_fundamental_data | moderate_5M_20M | — |
| ROG | UNKNOWN | covered_no_change | no_recent_report_in_cached_feed | quality_pass | liquid_20M_100M | — |
| ROST | Consumer Cyclical | rating_cut | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | — |
| RXT | Technology | covered_no_change | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | high_daily_range, deep_drawdown_from_52w_high |
| SABS | UNKNOWN | no_coverage_data | no_recent_report_in_cached_feed | quality_fail | thin_under_5M | below_ma200 |
| SANM | Technology | covered_no_change | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | — |
| SITM | Technology | covered_no_change | no_data_in_cached_feed | quality_fail | very_liquid_100M_plus | — |
| SKE | UNKNOWN | no_coverage_data | no_recent_report_in_cached_feed | no_fundamental_data | moderate_5M_20M | — |
| SRZN | UNKNOWN | covered_no_change | no_recent_report_in_cached_feed | quality_fail | thin_under_5M | below_ma200 |
| STOK | UNKNOWN | covered_no_change | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | below_ma200 |
| SYRE | Healthcare | rating_cut | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| TECK | Basic Materials | covered_no_change | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | — |
| TEO | UNKNOWN | rating_raised | no_recent_report_in_cached_feed | no_fundamental_data | thin_under_5M | — |
| TGTX | Healthcare | covered_no_change | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| TPR | Consumer Cyclical | covered_no_change | no_recent_report_in_cached_feed | no_fundamental_data | very_liquid_100M_plus | below_ma200 |
| TWLO | Technology | rating_raised | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | — |
| VRT | Industrials | rating_raised | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | — |
| WDC | Technology | covered_no_change | no_recent_report_in_cached_feed | quality_pass | very_liquid_100M_plus | — |
| XMTR | Industrials | covered_no_change | no_recent_report_in_cached_feed | quality_fail | liquid_20M_100M | — |
| ZBIO | UNKNOWN | covered_no_change | no_recent_report_in_cached_feed | quality_fail | moderate_5M_20M | — |
| ZNTL | UNKNOWN | no_action_in_window | no_recent_report_in_cached_feed | quality_fail | thin_under_5M | — |

⚠︎ = sits in the old scanner's score=100 saturation zone, one of the two cohorts the replay found reliably negative. Carried as a warning, not a filter — this report annotates, it does not remove.

## 4. Pool statistics

- sector concentration: largest **UNKNOWN** at 28.0%, 10 sectors (membership: 35.0%)
- liquidity: {'thin_under_5M': 9, 'moderate_5M_20M': 12, 'liquid_20M_100M': 30, 'very_liquid_100M_plus': 24}
- dollar volume $M (p10/median/p90): {'p10': 3.74, 'median': 51.5, 'p90': 846.82}
- ATR14 % (p10/median/p90): {'p10': 2.7, 'median': 4.6, 'p90': 7.16}
- risk flags: {'below_ma200': 12, 'at_or_near_52w_high': 10, 'high_daily_range': 6, 'deep_drawdown_from_52w_high': 7, 'extended_far_above_ma50': 2}
- names with a rating change in 63 sessions: **19**
- names with usable earnings data: **0** (the cached calendar ends 2026-01-31, 216 days before this session — so the earnings column is stale by construction, which is a fact about the feed and not about the names)
- names passing fundamental quality: **18**

| live surface | status | overlap |
|---|---|---|
| high_conviction | read | 5 (6.7%) |
| emerging_outlier | read | 0 (0.0%) |
| alpha_focus | read | 5 (6.7%) |
| topic_shock | absent | 0 (0.0%) |
| social_attention | read | 3 (4.0%) |
| research_scanner | read | 5 (6.7%) |

## 5. Forward scaffold

75 unresolved rows at horizons [20, 45, 60, 90] sessions, measured as the pool, equal-weight, against SPY/QQQ. these rows are unresolved. No forward claim is made, and none may be made from a single session's pool. These rows are **not** part of the Phase 4B forward ledger.

## 6. Human research checklist

- **why is it moving** — What actually moved this name over the last twelve months? Sector, product, balance sheet, or a single gap?
- **latest catalyst** — Most recent identifiable event, with its date. If none, say none.
- **earnings revenue trend** — Direction of revenue and margin over the last four quarters.
- **analyst attention** — Has coverage changed recently, and in which direction? (Attention, not direction, is what the evidence supports.)
- **liquidity tradability** — Could a real position be entered and exited without moving the book?
- **dilution debt risk** — Share count trend and debt against cash.
- **extended or exhausted** — Distance from MA50/MA200, position against the 52-week high, daily range.
- **what invalidates it** — The specific observation that would remove this name from consideration.

## What this report is not

- It never ranks or orders the pool.
- It never builds a conviction tier, a top-10 or a top-25 slice.
- It never publishes per-name momentum (it would invite a re-sort).
- It never emits a trade signal, entry, stop, target or size.
- It never emits a live verdict token.
