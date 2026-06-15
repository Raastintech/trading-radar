# Strategy Threshold Sweep Results

Generated: 2026-06-12T04:27:35.773590+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

Grid size: `13`

The grid is intentionally limited and predeclared. Selection is by validation score after complexity penalty; test results are reported after selection and are not used to choose thresholds.

Production thresholds mutated: `False`

## Selected Rows

| Variant | Param | Validation Score | Test Trades | Test Exp | Overfit Risk |
|---|---:|---:|---:|---:|---|
| SNIPER_NO_ATR_CONTRACTION | 10 | -2.2152 | 5 | -0.0308 | HIGH_TRAIN_ONLY |
| RECALL_SHADOW_RS_MOMENTUM | 10 | +1.4406 | 14 | -0.0233 | HIGH_TEST_DECAY |
| RECALL_SHADOW_PULLBACK | 2 | +8.2145 | 1 | +0.0743 | HIGH_LOW_TEST_SAMPLE |
| POWER_TREND_EXTENSION | 11 | -1.7940 | 3 | -0.0825 | HIGH_LOW_TEST_SAMPLE |
| QQQ_TECH_TACTICAL_SHORT | 11 | +7.3308 | 2 | +0.0314 | HIGH_LOW_TEST_SAMPLE |
| SIMPLE_SECTOR_RS | 11 | +4.6872 | 21 | -0.0356 | ELEVATED |
| SIMPLE_MOM_20_60 | 10 | +0.8510 | 16 | -0.0269 | HIGH_TEST_DECAY |
| RANDOM_LIQUID | 10 | -0.1393 | 25 | -0.0032 | HIGH_TEST_DECAY |

## Paper-Shadow Decision

NO_VARIANT_READY_FOR_PAPER_SHADOW

