# Strategy Walk-Forward Results

Generated: 2026-06-12T04:26:47.820447+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

Mode: `train_validation_test`

Selection rule: train ranks candidates; validation selects; test is evaluated only after selection.

Test used for selection: `False`

## Splits

- train: 2024-01-02 to 2025-03-21
- validation: 2025-03-24 to 2025-10-29
- test: 2025-10-30 to 2026-06-11

## Final Test Results

| Variant | Verdict | Param | Train Exp | Val Exp | Test Exp | Test Trades | Blockers |
|---|---:|---:|---:|---:|---:|---:|---|
| PROD_SNIPER_CURRENT | NEED_MORE_DATA | 0 | +0.0000 | +0.0000 | +0.0000 | 0 | test_trade_count_below_20, train_expectancy_not_positive, validation_expectancy_not_positive, test_expectancy_not_positive, test_does_not_beat_spy_qqq |
| SNIPER_NO_ATR_CONTRACTION | NEED_MORE_DATA | 3 | +0.0098 | -0.0266 | -0.0386 | 4 | test_trade_count_below_20, validation_expectancy_not_positive, test_expectancy_not_positive, test_does_not_beat_spy_qqq |
| PROD_VOYAGER_CURRENT | NEED_MORE_DATA | 3 | +0.0102 | +0.0038 | +0.0155 | 7 | test_trade_count_below_20 |
| RECALL_SHADOW_RS_MOMENTUM | PROMISING_BUT_OVERFIT_RISK | 2 | +0.0121 | +0.0129 | -0.0139 | 26 | test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| RECALL_SHADOW_PULLBACK | NEED_MORE_DATA | 3 | +0.0114 | +0.0258 | -0.0076 | 12 | test_trade_count_below_20, test_expectancy_not_positive, test_does_not_beat_spy_qqq |
| POWER_TREND_EXTENSION | NEED_MORE_DATA | 4 | -0.0494 | +0.0708 | -0.0825 | 2 | test_trade_count_below_20, train_expectancy_not_positive, test_expectancy_not_positive, test_does_not_beat_spy_qqq |
| QQQ_TECH_TACTICAL_SHORT | NEED_MORE_DATA | 3 | -0.0238 | +0.0000 | -0.0038 | 4 | test_trade_count_below_20, train_expectancy_not_positive, validation_expectancy_not_positive, test_expectancy_not_positive |
| SIMPLE_SECTOR_RS | PROMISING_BUT_OVERFIT_RISK | 0 | +0.0032 | +0.0069 | -0.0049 | 38 | test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| SIMPLE_MOM_20_60 | PROMISING_BUT_OVERFIT_RISK | 4 | +0.0125 | +0.0272 | -0.0423 | 24 | test_expectancy_not_positive, test_does_not_beat_spy_qqq, test_drawdown_too_high |
| RANDOM_LIQUID | PROMISING_BUT_OVERFIT_RISK | 0 | +0.0063 | +0.0142 | +0.0070 | 40 | test_does_not_beat_spy_qqq, test_drawdown_too_high |

## Paper-Shadow Decision

NO_VARIANT_READY_FOR_PAPER_SHADOW

