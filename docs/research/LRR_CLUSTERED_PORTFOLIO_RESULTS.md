# LRR Clustered Portfolio Results (Phase 1I.2)

Generated: 2026-06-12T19:35:24.600649+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

## DID ANY PORTFOLIO-CONSTRUCTION CONFIG EARN PAPER-SHADOW? NO

Verdict: **LRR_FAMILY_ARCHIVE_RECOMMENDED**

Signal window: `2024-01-02` to `2026-06-12`. Signal rules, regime gate, and exits are FROZEN; only pre-registered portfolio construction varies. Baseline independent maxDD: -71.35%.

## Config Table

| Config | Trades | Expectancy | Ind MaxDD | Real Return | Real MaxDD | Sharpe | Same-Exp SPY Sharpe | Gates |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE_GATED | 269 | +1.88% | -71.35% | +16.68% | -6.75% | 0.9803 | 1.1125 | 6/8 |
| C1_MAX_1_PER_DAY | 81 | +2.59% | -38.85% | +16.04% | -4.90% | 1.1675 | 1.1401 | 7/8 |
| C2_MAX_2_PER_DAY | 147 | +1.97% | -52.48% | +21.56% | -4.63% | 1.2966 | 1.3511 | 6/8 |
| C3_TOP_RANKED_ONLY | 81 | +2.19% | -43.32% | +16.82% | -4.90% | 1.1993 | 1.2129 | 6/8 |
| C4_MAX_3_OPEN | 269 | +1.88% | -71.35% | +17.93% | -4.97% | 1.2302 | 0.8075 | 7/8 |
| C5_MAX_5_OPEN | 269 | +1.88% | -71.35% | +16.68% | -6.75% | 0.9803 | 1.1125 | 6/8 |
| C6_VOL_SCALED | 269 | +1.88% | -71.35% | +4.19% | -2.94% | 0.658 | 1.2319 | 6/8 |
| C7_SECTOR_THEME_CAP | 94 | +1.16% | -37.38% | +7.66% | -5.38% | 0.6512 | 0.9906 | 6/8 |
| C8_COOLDOWN_3D_AFTER_LOSS | 133 | +0.80% | -73.89% | +1.11% | -5.63% | 0.1123 | 0.5564 | 3/8 |
| C9_SKIP_DUP_SECTOR_OPEN | 89 | +1.32% | -41.77% | +10.09% | -6.12% | 0.6615 | 1.1666 | 5/8 |
| C10_COMBO_DEFENSIVE | 56 | +1.30% | -38.31% | -0.38% | -1.63% | -0.1083 | 0.87 | 2/8 |

Baselines: RANDOM_LIQUID realistic +13.06% (Sharpe 0.5818); cash +0.00%.

## Gate Detail Per Config

### BASELINE_GATED — LRR_REGIME_GATED with default lab caps (reference)

- [FAIL] sharpe_beats_same_exposure_spy: 0.9803 vs same-exp SPY 1.1125
- [PASS] realistic_maxdd_acceptable: -6.75% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -71.35% (floor -35.00%, baseline -71.35% + 25pts)
- [PASS] walk_forward_test_positive: test n=127, expectancy +1.18%
- [PASS] no_one_month_dependency: positive months 10/17, best share 0.0323
- [PASS] no_one_ticker_dependency: top ticker ('WDC', 13) (0.0483)
- [PASS] ytd_2026_positive: 2026 expectancy +0.49%
- [PASS] positive_edge_after_costs: expectancy +1.88% vs random +0.33%; realistic +16.68%
- walk-forward test: n=127, expectancy +1.18%
- 2026 YTD expectancy: +0.49%

### C1_MAX_1_PER_DAY — max 1 entry per day (by lab score)

- [PASS] sharpe_beats_same_exposure_spy: 1.1675 vs same-exp SPY 1.1401
- [PASS] realistic_maxdd_acceptable: -4.90% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -38.85% (floor -35.00%, baseline -71.35% + 25pts)
- [PASS] walk_forward_test_positive: test n=32, expectancy +3.24%
- [PASS] no_one_month_dependency: positive months 11/15, best share 0.1091
- [PASS] no_one_ticker_dependency: top ticker ('RUN', 6) (0.0741)
- [PASS] ytd_2026_positive: 2026 expectancy +2.66%
- [PASS] positive_edge_after_costs: expectancy +2.59% vs random +0.33%; realistic +16.04%
- walk-forward test: n=32, expectancy +3.24%
- 2026 YTD expectancy: +2.66%

### C2_MAX_2_PER_DAY — max 2 entries per day (by lab score)

- [FAIL] sharpe_beats_same_exposure_spy: 1.2966 vs same-exp SPY 1.3511
- [PASS] realistic_maxdd_acceptable: -4.63% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -52.48% (floor -35.00%, baseline -71.35% + 25pts)
- [PASS] walk_forward_test_positive: test n=60, expectancy +1.57%
- [PASS] no_one_month_dependency: positive months 12/17, best share 0.1033
- [PASS] no_one_ticker_dependency: top ticker ('WDC', 9) (0.0612)
- [PASS] ytd_2026_positive: 2026 expectancy +0.95%
- [PASS] positive_edge_after_costs: expectancy +1.97% vs random +0.33%; realistic +21.56%
- walk-forward test: n=60, expectancy +1.57%
- 2026 YTD expectancy: +0.95%

### C3_TOP_RANKED_ONLY — top composite-ranked signal only per day

- [FAIL] sharpe_beats_same_exposure_spy: 1.1993 vs same-exp SPY 1.2129
- [PASS] realistic_maxdd_acceptable: -4.90% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -43.32% (floor -35.00%, baseline -71.35% + 25pts)
- [PASS] walk_forward_test_positive: test n=32, expectancy +2.33%
- [PASS] no_one_month_dependency: positive months 9/15, best share 0.1739
- [PASS] no_one_ticker_dependency: top ticker ('TGTX', 7) (0.0864)
- [PASS] ytd_2026_positive: 2026 expectancy +2.36%
- [PASS] positive_edge_after_costs: expectancy +2.19% vs random +0.33%; realistic +16.82%
- walk-forward test: n=32, expectancy +2.33%
- 2026 YTD expectancy: +2.36%

### C4_MAX_3_OPEN — max 3 open positions

- [PASS] sharpe_beats_same_exposure_spy: 1.2302 vs same-exp SPY 0.8075
- [PASS] realistic_maxdd_acceptable: -4.97% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -71.35% (floor -35.00%, baseline -71.35% + 25pts)
- [PASS] walk_forward_test_positive: test n=127, expectancy +1.18%
- [PASS] no_one_month_dependency: positive months 10/17, best share 0.0323
- [PASS] no_one_ticker_dependency: top ticker ('WDC', 13) (0.0483)
- [PASS] ytd_2026_positive: 2026 expectancy +0.49%
- [PASS] positive_edge_after_costs: expectancy +1.88% vs random +0.33%; realistic +17.93%
- walk-forward test: n=127, expectancy +1.18%
- 2026 YTD expectancy: +0.49%

### C5_MAX_5_OPEN — max 5 open positions (engine default, explicit)

- [FAIL] sharpe_beats_same_exposure_spy: 0.9803 vs same-exp SPY 1.1125
- [PASS] realistic_maxdd_acceptable: -6.75% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -71.35% (floor -35.00%, baseline -71.35% + 25pts)
- [PASS] walk_forward_test_positive: test n=127, expectancy +1.18%
- [PASS] no_one_month_dependency: positive months 10/17, best share 0.0323
- [PASS] no_one_ticker_dependency: top ticker ('WDC', 13) (0.0483)
- [PASS] ytd_2026_positive: 2026 expectancy +0.49%
- [PASS] positive_edge_after_costs: expectancy +1.88% vs random +0.33%; realistic +16.68%
- walk-forward test: n=127, expectancy +1.18%
- 2026 YTD expectancy: +0.49%

### C6_VOL_SCALED — volatility-scaled sizing (2% ATR target, floor 0.25x)

- [FAIL] sharpe_beats_same_exposure_spy: 0.658 vs same-exp SPY 1.2319
- [PASS] realistic_maxdd_acceptable: -2.94% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -71.35% (floor -35.00%, baseline -71.35% + 25pts)
- [PASS] walk_forward_test_positive: test n=127, expectancy +1.18%
- [PASS] no_one_month_dependency: positive months 10/17, best share 0.0323
- [PASS] no_one_ticker_dependency: top ticker ('WDC', 13) (0.0483)
- [PASS] ytd_2026_positive: 2026 expectancy +0.49%
- [PASS] positive_edge_after_costs: expectancy +1.88% vs random +0.33%; realistic +4.19%
- walk-forward test: n=127, expectancy +1.18%
- 2026 YTD expectancy: +0.49%

### C7_SECTOR_THEME_CAP — sector cap 20% + skip duplicate open theme

- [FAIL] sharpe_beats_same_exposure_spy: 0.6512 vs same-exp SPY 0.9906
- [PASS] realistic_maxdd_acceptable: -5.38% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -37.38% (floor -35.00%, baseline -71.35% + 25pts)
- [PASS] walk_forward_test_positive: test n=47, expectancy +1.33%
- [PASS] no_one_month_dependency: positive months 10/16, best share 0.3154
- [PASS] no_one_ticker_dependency: top ticker ('INSP', 6) (0.0638)
- [PASS] ytd_2026_positive: 2026 expectancy +0.89%
- [PASS] positive_edge_after_costs: expectancy +1.16% vs random +0.33%; realistic +7.66%
- walk-forward test: n=47, expectancy +1.33%
- 2026 YTD expectancy: +0.89%

### C8_COOLDOWN_3D_AFTER_LOSS — 3-trading-day entry cooldown after a losing exit

- [FAIL] sharpe_beats_same_exposure_spy: 0.1123 vs same-exp SPY 0.5564
- [PASS] realistic_maxdd_acceptable: -5.63% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -73.89% (floor -35.00%, baseline -71.35% + 25pts)
- [FAIL] walk_forward_test_positive: test n=58, expectancy -1.07%
- [FAIL] no_one_month_dependency: positive months 8/17, best share 1.0314
- [PASS] no_one_ticker_dependency: top ticker ('WDC', 9) (0.0677)
- [FAIL] ytd_2026_positive: 2026 expectancy -0.52%
- [PASS] positive_edge_after_costs: expectancy +0.80% vs random +0.33%; realistic +1.11%
- walk-forward test: n=58, expectancy -1.07%
- 2026 YTD expectancy: -0.52%

### C9_SKIP_DUP_SECTOR_OPEN — skip entry if same sector already open

- [FAIL] sharpe_beats_same_exposure_spy: 0.6615 vs same-exp SPY 1.1666
- [PASS] realistic_maxdd_acceptable: -6.12% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -41.77% (floor -35.00%, baseline -71.35% + 25pts)
- [PASS] walk_forward_test_positive: test n=38, expectancy +0.47%
- [PASS] no_one_month_dependency: positive months 10/17, best share 0.3406
- [PASS] no_one_ticker_dependency: top ticker ('WDC', 4) (0.0449)
- [FAIL] ytd_2026_positive: 2026 expectancy -0.33%
- [PASS] positive_edge_after_costs: expectancy +1.32% vs random +0.33%; realistic +10.09%
- walk-forward test: n=38, expectancy +0.47%
- 2026 YTD expectancy: -0.33%

### C10_COMBO_DEFENSIVE — pre-registered combo: 2/day composite-ranked + max 3 open + vol-scaled + cooldown + dup-sector skip

- [FAIL] sharpe_beats_same_exposure_spy: -0.1083 vs same-exp SPY 0.87
- [PASS] realistic_maxdd_acceptable: -1.63% (floor -15.00%)
- [FAIL] independent_dd_materially_improved: -38.31% (floor -35.00%, baseline -71.35% + 25pts)
- [FAIL] walk_forward_test_positive: test n=20, expectancy -0.52%
- [FAIL] no_one_month_dependency: positive months 8/16, best share 0.9028
- [PASS] no_one_ticker_dependency: top ticker ('UPST', 4) (0.0714)
- [FAIL] ytd_2026_positive: 2026 expectancy -0.55%
- [FAIL] positive_edge_after_costs: expectancy +1.30% vs random +0.33%; realistic -0.38%
- walk-forward test: n=20, expectancy -0.52%
- 2026 YTD expectancy: -0.55%

## Safety

No paper signals, broker orders, trade proposals (doc-only), production thresholds, Gatekeeper/Veto logic,
execution/governance/live-capital modules, historical evidence, or SHORT_A frozen status were changed.
LRR entry rules, allowed regimes, and regime labels were not modified.

