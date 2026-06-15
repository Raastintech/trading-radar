# Strategy Walk-Forward Results

Generated: 2026-06-12T15:14:39.872537+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

Mode: `train_validation_test` · exact: `True` · sampled: `False`

Selection rule: train ranks candidates; validation selects; test is evaluated only after selection.

Test used for selection: `False`

## Splits

- train: 2024-01-02 to 2025-03-21
- validation: 2025-03-24 to 2025-10-29
- test: 2025-10-30 to 2026-06-11

## Final Test Results

| Variant | Verdict | Param | Train Exp | Val Exp | Test Exp | Test Trades | Test Decay | Overfit Risk | Stable | Blockers |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| PROD_SNIPER_CURRENT | NEED_MORE_DATA | 4 | +0.0384 | +0.0118 | +0.0594 | 9 | -0.0210 | HIGH_LOW_TEST_SAMPLE | True | test_trade_count_below_20 |
| SNIPER_NO_ATR_CONTRACTION | REJECT | 4 | +0.0162 | -0.0000 | +0.0394 | 85 | -0.0232 | HIGH_TRAIN_ONLY | True | validation_expectancy_not_positive, test_drawdown_too_high |
| PROD_VOYAGER_CURRENT | PROMISING_BUT_OVERFIT_RISK | 4 | +0.0218 | +0.0328 | +0.0318 | 340 | -0.0100 | MODERATE_NEEDS_PAPER_SHADOW_GATE | True | test_drawdown_too_high |
| CORRECTION_LEADER_RECLAIM | PROMISING_BUT_OVERFIT_RISK | 4 | +0.0208 | +0.0384 | -0.0118 | 323 | +0.0325 | HIGH_TEST_DECAY | True | test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| RECALL_SHADOW_RS_MOMENTUM | PROMISING_BUT_OVERFIT_RISK | 4 | +0.0013 | +0.0350 | +0.0035 | 670 | -0.0022 | MODERATE_NEEDS_PAPER_SHADOW_GATE | True | test_drawdown_too_high |
| RECALL_SHADOW_PULLBACK | PROMISING_BUT_OVERFIT_RISK | 4 | +0.0096 | +0.0211 | -0.0029 | 427 | +0.0125 | HIGH_TEST_DECAY | True | test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| POWER_TREND_EXTENSION | PROMISING_BUT_OVERFIT_RISK | 0 | +0.0015 | +0.0157 | +0.0096 | 377 | -0.0081 | MODERATE_NEEDS_PAPER_SHADOW_GATE | True | test_drawdown_too_high |
| QQQ_TECH_TACTICAL_SHORT | REJECT | 4 | +0.0145 | -0.0131 | -0.0297 | 282 | +0.0442 | HIGH_TRAIN_ONLY | True | validation_expectancy_not_positive, test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| SIMPLE_SECTOR_RS | REJECT | 3 | -0.0043 | +0.0045 | -0.0018 | 749 | -0.0025 | ELEVATED | True | train_expectancy_not_positive, test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| SIMPLE_MOM_20_60 | REJECT | 0 | -0.0027 | +0.0089 | -0.0052 | 724 | +0.0025 | ELEVATED | True | train_expectancy_not_positive, test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| RANDOM_LIQUID | PROMISING_BUT_OVERFIT_RISK | 4 | +0.0063 | +0.0189 | +0.0030 | 671 | +0.0033 | MODERATE_NEEDS_PAPER_SHADOW_GATE | True | test_does_not_beat_spy_qqq, test_drawdown_too_high |

## Paper-Shadow Decision

NO_VARIANT_READY_FOR_PAPER_SHADOW

