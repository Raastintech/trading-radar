# Strategy Failure Reason Miner (Phase 1H.4)

Generated: 2026-06-12T16:42:54.354764+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

Signal window: `2024-01-02` to `2026-06-11` (613 dates, 83134 ticker-days). Fidelity mismatches vs original signal functions: **0**.

Labels: winner = 10d exit-free forward return >= +3%, loser <= -3% (side-adjusted). Forward data is used only for labeling; decision fields are strictly as-of.

## Case Classification

| Variant | Accepted | Acc Winners | Acc Losers | Rej Winners | Rej Losers | Not Matured |
|---|---:|---:|---:|---:|---:|---:|
| PROD_SNIPER_CURRENT | 41 | 16 | 5 | 28557 | 24002 | 1944 |
| SNIPER_NO_ATR_CONTRACTION | 429 | 127 | 93 | 28446 | 23914 | 1944 |
| PROD_VOYAGER_CURRENT | 1708 | 678 | 311 | 27895 | 23696 | 1944 |
| RECALL_SHADOW_RS_MOMENTUM | 10319 | 4129 | 3004 | 24444 | 21003 | 1944 |
| RECALL_SHADOW_PULLBACK | 2531 | 916 | 672 | 27657 | 23335 | 1944 |
| POWER_TREND_EXTENSION | 939 | 453 | 283 | 28120 | 23724 | 1944 |
| CORRECTION_LEADER_RECLAIM | 2559 | 923 | 536 | 27650 | 23471 | 1944 |
| QQQ_TECH_TACTICAL_SHORT | 1083 | 493 | 371 | 23514 | 28202 | 1944 |

## Gate Verdicts

### PROD_SNIPER_CURRENT

| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | W rate | L rate | Opp Cost | Avoided | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| sniper_universe | 55046 | 92 | +3.22% | 0.4348 | 0.2826 | 5.652 | 2.6855 | OVERBLOCKS_WINNERS |
| liquidity_floor | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| min_bars_75 | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| first_breakout | 76528 | 52 | +2.06% | 0.4231 | 0.1346 | 1.5801 | 0.5086 | OVERBLOCKS_WINNERS |
| volume_confirm_1_4x | 73196 | 44 | +0.05% | 0.1818 | 0.1591 | 0.6582 | 0.6377 | NO_SIGNAL |
| atr_contraction_lt_0_85 | 68887 | 281 | +0.83% | 0.2811 | 0.2384 | 8.3719 | 6.0326 | KEEP_SOFT_WARNING |
| trend_ma50_rising | 43373 | 26 | -0.36% | 0.1538 | 0.2692 | 0.421 | 0.514 | KEEP_SOFT_WARNING |
| rs10_spy_positive | 44747 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| spy_above_ma200_regime | 7260 | 1 | +7.47% | 1.0 | 0.0 | 0.0747 | 0.0 | NEED_MORE_DATA |
| sniper_score_70 | 75465 | 9 | -0.19% | 0.2222 | 0.3333 | 0.1399 | 0.1574 | NEED_MORE_DATA |

**Stance: REPAIR** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.

### SNIPER_NO_ATR_CONTRACTION

| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | W rate | L rate | Opp Cost | Avoided | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| sniper_universe | 55046 | 934 | +2.71% | 0.4604 | 0.2709 | 57.4239 | 32.1268 | OVERBLOCKS_WINNERS |
| liquidity_floor | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| min_bars_75 | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| first_breakout | 76528 | 532 | +1.76% | 0.3816 | 0.1523 | 16.1205 | 6.7665 | OVERBLOCKS_WINNERS |
| volume_confirm_1_4x | 73196 | 1309 | +0.68% | 0.2613 | 0.1841 | 30.0628 | 21.2192 | KEEP_SOFT_WARNING |
| trend_ma50_rising | 43373 | 108 | -0.03% | 0.2593 | 0.2593 | 2.7598 | 2.7933 | NO_SIGNAL |
| rs10_spy_positive | 44747 | 6 | +3.84% | 0.5 | 0.0 | 0.2303 | 0.0 | NEED_MORE_DATA |
| spy_above_ma200_regime | 7260 | 9 | +5.75% | 0.4444 | 0.1111 | 0.5813 | 0.0635 | NEED_MORE_DATA |

**Stance: REPAIR** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.

### PROD_VOYAGER_CURRENT

| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | W rate | L rate | Opp Cost | Avoided | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| liquidity_floor | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| min_bars_260 | 8975 | 66 | +0.53% | 0.3939 | 0.3636 | 3.3689 | 3.0209 | KEEP_SOFT_WARNING |
| price_min_5 | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| ma_available | 6525 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| ma200_floor_0_92 | 15343 | 6 | +4.76% | 0.6667 | 0.0 | 0.2858 | 0.0 | NEED_MORE_DATA |
| ma50_extension_cap_12 | 13681 | 133 | +1.09% | 0.3835 | 0.2782 | 5.5254 | 4.082 | OVERBLOCKS_WINNERS |
| rs50_spy_positive | 40509 | 1458 | +0.65% | 0.2634 | 0.1955 | 35.0131 | 25.5665 | KEEP_SOFT_WARNING |
| dvol_ratio_0_85 | 23084 | 713 | +1.57% | 0.5245 | 0.195 | 22.7272 | 11.5016 | OVERBLOCKS_WINNERS |
| archetype_match | 69284 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| up_vol_ratio_floor | 6639 | 1358 | +0.38% | 0.2401 | 0.2018 | 29.0038 | 23.8632 | NO_SIGNAL |
| voyager_score_65 | 7123 | 206 | +2.20% | 0.4563 | 0.1893 | 8.5254 | 3.9991 | OVERBLOCKS_WINNERS |

**Stance: REPAIR** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.

### RECALL_SHADOW_RS_MOMENTUM

| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | W rate | L rate | Opp Cost | Avoided | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| liquidity_floor | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| min_bars_75 | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| rs_floor | 63897 | 1794 | +1.28% | 0.3517 | 0.2586 | 66.396 | 43.4946 | OVERBLOCKS_WINNERS |
| momentum_20 | 62095 | 616 | +1.89% | 0.388 | 0.2841 | 29.3483 | 17.7191 | OVERBLOCKS_WINNERS |
| momentum_60 | 55860 | 4810 | +1.48% | 0.3786 | 0.3073 | 223.8129 | 152.5978 | KEEP_SOFT_WARNING |
| extension_cap | 715 | 640 | +3.36% | 0.5047 | 0.3672 | 61.3015 | 39.7703 | OVERBLOCKS_WINNERS |
| above_ema20_not_false | 38580 | 425 | -5.23% | 0.2729 | 0.5929 | 17.8367 | 40.0513 | KEEP_HARD_BLOCK |

**Stance: REPAIR** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.

### RECALL_SHADOW_PULLBACK

| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | W rate | L rate | Opp Cost | Avoided | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| liquidity_floor | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| min_bars_75 | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| ext_available | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| momentum_60 | 55860 | 4349 | +0.81% | 0.3304 | 0.2486 | 138.4305 | 103.0956 | KEEP_SOFT_WARNING |
| rs_floor_pullback | 55710 | 3766 | +0.99% | 0.3396 | 0.2687 | 131.9231 | 94.7544 | KEEP_SOFT_WARNING |
| pullback_band | 21033 | 9933 | +2.06% | 0.4069 | 0.2979 | 525.4241 | 321.2562 | OVERBLOCKS_WINNERS |
| r5_floor | 26062 | 909 | +1.88% | 0.4125 | 0.3223 | 48.2012 | 31.0759 | OVERBLOCKS_WINNERS |
| above_ema20_true | 38580 | 165 | +2.43% | 0.3879 | 0.3212 | 11.2097 | 7.1969 | KEEP_SOFT_WARNING |

**Stance: REPAIR** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.

### POWER_TREND_EXTENSION

| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | W rate | L rate | Opp Cost | Avoided | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| liquidity_floor | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| min_bars_75 | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| extension_band_15_35 | 80988 | 5522 | +1.55% | 0.4064 | 0.3339 | 294.2259 | 208.4244 | KEEP_SOFT_WARNING |
| rs_floor | 63897 | 4 | +13.07% | 0.5 | 0.5 | 0.7366 | 0.2139 | NEED_MORE_DATA |
| volume_expansion_floor | 42990 | 293 | +4.95% | 0.5427 | 0.2867 | 25.7773 | 11.2666 | OVERBLOCKS_WINNERS |
| theme_sector_membership | 44218 | 336 | +0.16% | 0.4018 | 0.3869 | 17.7269 | 17.1851 | NO_SIGNAL |
| not_parabolic | 703 | 224 | +2.87% | 0.5 | 0.3348 | 19.1836 | 12.7442 | OVERBLOCKS_WINNERS |

**Stance: REPAIR** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.

### CORRECTION_LEADER_RECLAIM

| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | W rate | L rate | Opp Cost | Avoided | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| regime_allowed | 49641 | 3756 | +1.40% | 0.3355 | 0.2039 | 120.0858 | 67.6319 | OVERBLOCKS_WINNERS |
| liquidity_floor | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| min_bars_75 | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| correction_rs_floor | 40635 | 305 | +1.89% | 0.3607 | 0.1213 | 8.5855 | 2.8062 | OVERBLOCKS_WINNERS |
| price_positive | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| controlled_pullback | 13926 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| not_weaker_than_market | 56176 | 1038 | +2.44% | 0.4191 | 0.2958 | 58.1267 | 32.8409 | OVERBLOCKS_WINNERS |
| not_at_new_low | 12462 | 14 | +1.22% | 0.2143 | 0.0 | 0.2061 | 0.0351 | NEED_MORE_DATA |
| ema_reclaim | 12986 | 1727 | +1.77% | 0.3636 | 0.2038 | 62.6137 | 32.0709 | OVERBLOCKS_WINNERS |
| ma200_support | 20131 | 74 | +1.69% | 0.2973 | 0.3514 | 3.5034 | 2.2523 | KEEP_SOFT_WARNING |
| ma50_support | 13410 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| volume_confirm_or_dryup | 35318 | 1806 | +1.15% | 0.3433 | 0.2165 | 55.0028 | 34.2473 | OVERBLOCKS_WINNERS |
| not_extended_without_pullback | 2824 | 214 | +2.19% | 0.4252 | 0.3645 | 12.9823 | 8.304 | KEEP_SOFT_WARNING |
| not_parabolic_without_pullback | 1146 | 6 | +11.92% | 0.6667 | 0.1667 | 0.785 | 0.0696 | NEED_MORE_DATA |

**Stance: REPAIR** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.

### QQQ_TECH_TACTICAL_SHORT

| Gate | Fired | Sole-blocked (matured) | Blocked avg fwd10 | W rate | L rate | Opp Cost | Avoided | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| liquidity_floor | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| min_bars_75 | 0 | 0 | n/a | n/a | n/a | 0.0 | 0.0 | NEED_MORE_DATA |
| tech_membership | 48442 | 1149 | -1.94% | 0.2567 | 0.4212 | 30.019 | 52.3673 | KEEP_HARD_BLOCK |
| market_risk_weak | 52460 | 1490 | +1.80% | 0.5772 | 0.2537 | 80.9292 | 54.0811 | OVERBLOCKS_WINNERS |
| failed_leader | 72697 | 4397 | -1.28% | 0.3546 | 0.4096 | 149.5759 | 205.8599 | KEEP_HARD_BLOCK |
| tech_weakness | 48635 | 1182 | -1.45% | 0.3824 | 0.4078 | 50.8567 | 68.0258 | KEEP_HARD_BLOCK |

**Stance: KILL** — Confirmed harmful baseline: negative expectancy, -100% independent drawdown in 1H.1/1H.3, and no gate fix addresses being short a bull tape.

## Fairness Caveats

- The candidate pool is already pre-filtered (price>=5, avg dollar-volume>=5M, bars>=75, universe cap 140), so liquidity-gate statistics are conditional on that pool.
- Earnings proximity, point-in-time quote spread, Gatekeeper verdicts, and Stock Lens artifacts are NOT retained historically; affected patterns are reported NOT_MEASURABLE instead of guessed.
- Winner/loser labels use exit-free 10d forward returns, not the strategy's stop/target exits; strategy P&L is measured separately in the counterfactual backtest.
- Sole-blocker counterfactuals assume the rest of the gates stay unchanged; daily top-5 capacity effects are handled in the counterfactual backtest, not here.

## Safety

No paper signals, broker orders, trade proposals, production thresholds, Gatekeeper/Veto logic, execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.

