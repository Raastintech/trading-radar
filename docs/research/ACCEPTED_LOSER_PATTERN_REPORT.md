# Accepted-Loser Pattern Report (Phase 1H.4)

Generated: 2026-06-12T16:42:54.354764+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

Purpose: find protections that should be added. A pattern is flagged MISSING_PROTECTION when it is at least 1.3x more prevalent among accepted losers than accepted winners, covers >=10% of losers, has >=30 hits, and degrades forward returns by >=0.5%.

## PROD_SNIPER_CURRENT

| Pattern | Hits | Loser prev | Winner prev | Lift | Fwd10 with | Fwd10 without | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| below_ma50 | 0 | 0.0 | 0.0 | None | n/a | +3.05% | NEED_MORE_DATA |
| failed_reclaim_below_ema20 | 0 | 0.0 | 0.0 | None | n/a | +3.05% | NEED_MORE_DATA |
| gap_up_entry_gt_3pct | 0 | 0.0 | 0.0 | None | n/a | +3.05% | NEED_MORE_DATA |
| low_liquidity_tail_dvol_lt_10m | 0 | 0.0 | 0.0 | None | n/a | +3.05% | NEED_MORE_DATA |
| no_trend_support_below_ma200 | 1 | 0.2 | 0.0 | None | -4.14% | +3.23% | NEED_MORE_DATA |
| parabolic_climax_r10_gt_30 | 0 | 0.0 | 0.0 | None | n/a | +3.05% | NEED_MORE_DATA |
| repeated_ticker_exposure_5d | 6 | 0.0 | 0.25 | None | +6.65% | +2.43% | NEED_MORE_DATA |
| risk_off_or_correction_regime | 8 | 0.0 | 0.125 | None | +1.11% | +3.52% | NEED_MORE_DATA |
| sector_weakness | 6 | 0.0 | 0.1875 | None | +6.53% | +2.46% | NEED_MORE_DATA |
| too_extended_without_power_trend | 0 | 0.0 | 0.0 | None | n/a | +3.05% | NEED_MORE_DATA |
| volume_exhaustion | 28 | 0.8 | 0.75 | 1.067 | +3.38% | +2.35% | NEED_MORE_DATA |
| weak_rs | 6 | 0.0 | 0.1875 | None | +2.90% | +3.08% | NEED_MORE_DATA |
| earnings_soon | - | - | - | - | - | - | NOT_MEASURABLE (earnings calendar history is not retained point-in-time (NOT_RETAINED)) |
| high_spread_low_liquidity_quote | - | - | - | - | - | - | NOT_MEASURABLE (no point-in-time quote spread retained; only avg dollar-volume proxies exist) |

## SNIPER_NO_ATR_CONTRACTION

| Pattern | Hits | Loser prev | Winner prev | Lift | Fwd10 with | Fwd10 without | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| below_ma50 | 0 | 0.0 | 0.0 | None | n/a | +1.33% | NEED_MORE_DATA |
| failed_reclaim_below_ema20 | 0 | 0.0 | 0.0 | None | n/a | +1.33% | NEED_MORE_DATA |
| gap_up_entry_gt_3pct | 18 | 0.0753 | 0.0315 | 2.39 | +1.29% | +1.33% | NEED_MORE_DATA |
| low_liquidity_tail_dvol_lt_10m | 0 | 0.0 | 0.0 | None | n/a | +1.33% | NEED_MORE_DATA |
| no_trend_support_below_ma200 | 9 | 0.043 | 0.0079 | 5.462 | -3.56% | +1.44% | NEED_MORE_DATA |
| parabolic_climax_r10_gt_30 | 17 | 0.043 | 0.0709 | 0.607 | +6.10% | +1.13% | NEED_MORE_DATA |
| repeated_ticker_exposure_5d | 139 | 0.3226 | 0.2992 | 1.078 | +1.21% | +1.39% | NO_SIGNAL |
| risk_off_or_correction_regime | 36 | 0.043 | 0.0866 | 0.497 | +1.10% | +1.35% | NO_SIGNAL |
| sector_weakness | 38 | 0.0645 | 0.1181 | 0.546 | +2.50% | +1.22% | NO_SIGNAL |
| too_extended_without_power_trend | 11 | 0.043 | 0.0236 | 1.821 | +2.24% | +1.31% | NEED_MORE_DATA |
| volume_exhaustion | 126 | 0.2688 | 0.3858 | 0.697 | +3.09% | +0.58% | NO_SIGNAL |
| weak_rs | 21 | 0.0323 | 0.0551 | 0.585 | +0.99% | +1.35% | NEED_MORE_DATA |
| earnings_soon | - | - | - | - | - | - | NOT_MEASURABLE (earnings calendar history is not retained point-in-time (NOT_RETAINED)) |
| high_spread_low_liquidity_quote | - | - | - | - | - | - | NOT_MEASURABLE (no point-in-time quote spread retained; only avg dollar-volume proxies exist) |

## PROD_VOYAGER_CURRENT

| Pattern | Hits | Loser prev | Winner prev | Lift | Fwd10 with | Fwd10 without | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| below_ma50 | 740 | 0.4405 | 0.6283 | 0.701 | +6.74% | +0.69% | NO_SIGNAL |
| failed_reclaim_below_ema20 | 921 | 0.5788 | 0.6976 | 0.83 | +5.54% | +0.70% | NO_SIGNAL |
| gap_up_entry_gt_3pct | 145 | 0.0322 | 0.1873 | 0.172 | +19.51% | +1.82% | NO_SIGNAL |
| low_liquidity_tail_dvol_lt_10m | 0 | 0.0 | 0.0 | None | n/a | +3.34% | NEED_MORE_DATA |
| no_trend_support_below_ma200 | 186 | 0.0579 | 0.1829 | 0.316 | +12.28% | +2.23% | NO_SIGNAL |
| parabolic_climax_r10_gt_30 | 0 | 0.0 | 0.0 | None | n/a | +3.34% | NEED_MORE_DATA |
| repeated_ticker_exposure_5d | 1284 | 0.7363 | 0.8068 | 0.913 | +3.84% | +1.76% | NO_SIGNAL |
| risk_off_or_correction_regime | 702 | 0.3698 | 0.5162 | 0.716 | +4.71% | +2.36% | NO_SIGNAL |
| sector_weakness | 811 | 0.4791 | 0.556 | 0.862 | +4.78% | +2.00% | NO_SIGNAL |
| too_extended_without_power_trend | 0 | 0.0 | 0.0 | None | n/a | +3.34% | NEED_MORE_DATA |
| volume_exhaustion | 326 | 0.1736 | 0.1858 | 0.934 | +2.49% | +3.54% | NO_SIGNAL |
| weak_rs | 984 | 0.5949 | 0.6578 | 0.904 | +4.60% | +1.58% | NO_SIGNAL |
| earnings_soon | - | - | - | - | - | - | NOT_MEASURABLE (earnings calendar history is not retained point-in-time (NOT_RETAINED)) |
| high_spread_low_liquidity_quote | - | - | - | - | - | - | NOT_MEASURABLE (no point-in-time quote spread retained; only avg dollar-volume proxies exist) |

## RECALL_SHADOW_RS_MOMENTUM

| Pattern | Hits | Loser prev | Winner prev | Lift | Fwd10 with | Fwd10 without | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| below_ma50 | 11 | 0.0017 | 0.0007 | 2.291 | -1.15% | +2.18% | NEED_MORE_DATA |
| failed_reclaim_below_ema20 | 0 | 0.0 | 0.0 | None | n/a | +2.18% | NEED_MORE_DATA |
| gap_up_entry_gt_3pct | 570 | 0.0589 | 0.0654 | 0.901 | +3.82% | +2.08% | NO_SIGNAL |
| low_liquidity_tail_dvol_lt_10m | 0 | 0.0 | 0.0 | None | n/a | +2.18% | NEED_MORE_DATA |
| no_trend_support_below_ma200 | 300 | 0.0479 | 0.0165 | 2.911 | -2.93% | +2.34% | NO_SIGNAL |
| parabolic_climax_r10_gt_30 | 610 | 0.0712 | 0.0707 | 1.007 | +2.97% | +2.13% | NO_SIGNAL |
| repeated_ticker_exposure_5d | 9070 | 0.9141 | 0.9077 | 1.007 | +2.14% | +2.53% | NO_SIGNAL |
| risk_off_or_correction_regime | 1223 | 0.1262 | 0.1201 | 1.05 | +1.67% | +2.25% | NO_SIGNAL |
| sector_weakness | 173 | 0.0156 | 0.016 | 0.979 | +2.00% | +2.18% | NO_SIGNAL |
| too_extended_without_power_trend | 820 | 0.0915 | 0.0901 | 1.016 | +1.72% | +2.22% | NO_SIGNAL |
| volume_exhaustion | 2258 | 0.223 | 0.2357 | 0.946 | +2.82% | +1.99% | NO_SIGNAL |
| weak_rs | 1 | 0.0 | 0.0 | None | +0.24% | +2.18% | NEED_MORE_DATA |
| earnings_soon | - | - | - | - | - | - | NOT_MEASURABLE (earnings calendar history is not retained point-in-time (NOT_RETAINED)) |
| high_spread_low_liquidity_quote | - | - | - | - | - | - | NOT_MEASURABLE (no point-in-time quote spread retained; only avg dollar-volume proxies exist) |

## RECALL_SHADOW_PULLBACK

| Pattern | Hits | Loser prev | Winner prev | Lift | Fwd10 with | Fwd10 without | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| below_ma50 | 15 | 0.0074 | 0.0066 | 1.136 | +3.36% | +1.82% | NEED_MORE_DATA |
| failed_reclaim_below_ema20 | 0 | 0.0 | 0.0 | None | n/a | +1.83% | NEED_MORE_DATA |
| gap_up_entry_gt_3pct | 65 | 0.0223 | 0.0415 | 0.538 | +7.60% | +1.68% | NO_SIGNAL |
| low_liquidity_tail_dvol_lt_10m | 0 | 0.0 | 0.0 | None | n/a | +1.83% | NEED_MORE_DATA |
| no_trend_support_below_ma200 | 107 | 0.067 | 0.0415 | 1.614 | +0.02% | +1.92% | NO_SIGNAL |
| parabolic_climax_r10_gt_30 | 0 | 0.0 | 0.0 | None | n/a | +1.83% | NEED_MORE_DATA |
| repeated_ticker_exposure_5d | 1555 | 0.6399 | 0.5917 | 1.081 | +1.57% | +2.29% | NO_SIGNAL |
| risk_off_or_correction_regime | 431 | 0.1637 | 0.1965 | 0.833 | +2.23% | +1.75% | NO_SIGNAL |
| sector_weakness | 130 | 0.0565 | 0.0404 | 1.4 | +0.27% | +1.92% | NO_SIGNAL |
| too_extended_without_power_trend | 0 | 0.0 | 0.0 | None | n/a | +1.83% | NEED_MORE_DATA |
| volume_exhaustion | 834 | 0.3512 | 0.3603 | 0.975 | +2.45% | +1.51% | NO_SIGNAL |
| weak_rs | 40 | 0.0164 | 0.0153 | 1.071 | +1.27% | +1.84% | NO_SIGNAL |
| earnings_soon | - | - | - | - | - | - | NOT_MEASURABLE (earnings calendar history is not retained point-in-time (NOT_RETAINED)) |
| high_spread_low_liquidity_quote | - | - | - | - | - | - | NOT_MEASURABLE (no point-in-time quote spread retained; only avg dollar-volume proxies exist) |

## POWER_TREND_EXTENSION

| Pattern | Hits | Loser prev | Winner prev | Lift | Fwd10 with | Fwd10 without | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| below_ma50 | 0 | 0.0 | 0.0 | None | n/a | +4.84% | NEED_MORE_DATA |
| failed_reclaim_below_ema20 | 0 | 0.0 | 0.0 | None | n/a | +4.84% | NEED_MORE_DATA |
| gap_up_entry_gt_3pct | 105 | 0.1201 | 0.1015 | 1.183 | +3.55% | +5.01% | NO_SIGNAL |
| low_liquidity_tail_dvol_lt_10m | 0 | 0.0 | 0.0 | None | n/a | +4.84% | NEED_MORE_DATA |
| no_trend_support_below_ma200 | 14 | 0.0353 | 0.0088 | 4.002 | -10.34% | +5.08% | NEED_MORE_DATA |
| parabolic_climax_r10_gt_30 | 370 | 0.3958 | 0.4371 | 0.905 | +6.01% | +3.99% | NO_SIGNAL |
| repeated_ticker_exposure_5d | 666 | 0.7456 | 0.755 | 0.988 | +5.16% | +3.86% | NO_SIGNAL |
| risk_off_or_correction_regime | 108 | 0.1025 | 0.128 | 0.8 | +4.63% | +4.87% | NO_SIGNAL |
| sector_weakness | 2 | 0.0071 | 0.0 | None | -10.31% | +4.87% | NEED_MORE_DATA |
| too_extended_without_power_trend | 357 | 0.3675 | 0.3974 | 0.925 | +2.99% | +6.10% | NO_SIGNAL |
| volume_exhaustion | 0 | 0.0 | 0.0 | None | n/a | +4.84% | NEED_MORE_DATA |
| weak_rs | 0 | 0.0 | 0.0 | None | n/a | +4.84% | NEED_MORE_DATA |
| earnings_soon | - | - | - | - | - | - | NOT_MEASURABLE (earnings calendar history is not retained point-in-time (NOT_RETAINED)) |
| high_spread_low_liquidity_quote | - | - | - | - | - | - | NOT_MEASURABLE (no point-in-time quote spread retained; only avg dollar-volume proxies exist) |

## CORRECTION_LEADER_RECLAIM

| Pattern | Hits | Loser prev | Winner prev | Lift | Fwd10 with | Fwd10 without | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| below_ma50 | 29 | 0.0037 | 0.0173 | 0.215 | +4.86% | +1.52% | NEED_MORE_DATA |
| failed_reclaim_below_ema20 | 0 | 0.0 | 0.0 | None | n/a | +1.55% | NEED_MORE_DATA |
| gap_up_entry_gt_3pct | 87 | 0.0765 | 0.0282 | 2.715 | +0.16% | +1.60% | NO_SIGNAL |
| low_liquidity_tail_dvol_lt_10m | 0 | 0.0 | 0.0 | None | n/a | +1.55% | NEED_MORE_DATA |
| no_trend_support_below_ma200 | 79 | 0.0429 | 0.0336 | 1.278 | +0.16% | +1.60% | NO_SIGNAL |
| parabolic_climax_r10_gt_30 | 2 | 0.0 | 0.0022 | None | +41.01% | +1.52% | NEED_MORE_DATA |
| repeated_ticker_exposure_5d | 1479 | 0.5933 | 0.584 | 1.016 | +1.48% | +1.66% | NO_SIGNAL |
| risk_off_or_correction_regime | 777 | 0.2687 | 0.3044 | 0.882 | +1.36% | +1.64% | NO_SIGNAL |
| sector_weakness | 247 | 0.1119 | 0.0975 | 1.148 | +0.65% | +1.65% | NO_SIGNAL |
| too_extended_without_power_trend | 5 | 0.0037 | 0.0033 | 1.148 | +25.34% | +1.51% | NEED_MORE_DATA |
| volume_exhaustion | 1541 | 0.6231 | 0.6392 | 0.975 | +1.53% | +1.60% | NO_SIGNAL |
| weak_rs | 89 | 0.0299 | 0.0358 | 0.835 | +1.36% | +1.56% | NO_SIGNAL |
| earnings_soon | - | - | - | - | - | - | NOT_MEASURABLE (earnings calendar history is not retained point-in-time (NOT_RETAINED)) |
| high_spread_low_liquidity_quote | - | - | - | - | - | - | NOT_MEASURABLE (no point-in-time quote spread retained; only avg dollar-volume proxies exist) |

## QQQ_TECH_TACTICAL_SHORT

| Pattern | Hits | Loser prev | Winner prev | Lift | Fwd10 with | Fwd10 without | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| below_ma50 | 509 | 0.6415 | 0.3671 | 1.747 | -3.19% | +1.06% | MISSING_PROTECTION |
| failed_reclaim_below_ema20 | 996 | 0.9811 | 0.9878 | 0.993 | -1.07% | -1.96% | NO_SIGNAL |
| gap_up_entry_gt_3pct | 228 | 0.1563 | 0.3185 | 0.491 | +0.90% | -1.66% | NO_SIGNAL |
| low_liquidity_tail_dvol_lt_10m | 0 | 0.0 | 0.0 | None | n/a | -1.08% | NEED_MORE_DATA |
| no_trend_support_below_ma200 | 59 | 0.0701 | 0.0446 | 1.57 | -3.47% | -0.93% | NO_SIGNAL |
| parabolic_climax_r10_gt_30 | 0 | 0.0 | 0.0 | None | n/a | -1.08% | NEED_MORE_DATA |
| repeated_ticker_exposure_5d | 776 | 0.7736 | 0.8032 | 0.963 | -0.97% | -1.44% | NO_SIGNAL |
| risk_off_or_correction_regime | 452 | 0.5499 | 0.3773 | 1.457 | -3.94% | +1.23% | MISSING_PROTECTION |
| sector_weakness | 894 | 0.9137 | 0.854 | 1.07 | -1.36% | +1.08% | NO_SIGNAL |
| too_extended_without_power_trend | 0 | 0.0 | 0.0 | None | n/a | -1.08% | NEED_MORE_DATA |
| volume_exhaustion | 589 | 0.5175 | 0.6207 | 0.834 | +0.40% | -3.14% | NO_SIGNAL |
| weak_rs | 1009 | 0.9973 | 0.998 | 0.999 | -1.08% | -0.53% | NO_SIGNAL |
| earnings_soon | - | - | - | - | - | - | NOT_MEASURABLE (earnings calendar history is not retained point-in-time (NOT_RETAINED)) |
| high_spread_low_liquidity_quote | - | - | - | - | - | - | NOT_MEASURABLE (no point-in-time quote spread retained; only avg dollar-volume proxies exist) |

