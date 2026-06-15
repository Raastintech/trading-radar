# Strategy Threshold Sweep Results

Generated: 2026-06-12T15:25:51.272235+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

Grid size: `26`

The grid is intentionally limited and predeclared. Selection is by validation score after complexity penalty; test results are reported after selection and are not used to choose thresholds.

Production thresholds mutated: `False`

## Selected Rows

| Variant | Param | Validation Score | Test Trades | Test Exp | Overfit Risk |
|---|---:|---:|---:|---:|---|
| CORRECTION_LEADER_RECLAIM | 11 | +5.0584 | 323 | -0.0118 | HIGH_TEST_DECAY |
| SNIPER_NO_ATR_CONTRACTION | 11 | -1.4833 | 85 | +0.0394 | HIGH_TRAIN_ONLY |
| RECALL_SHADOW_RS_MOMENTUM | 11 | +4.6379 | 670 | +0.0035 | MODERATE_NEEDS_PAPER_SHADOW_GATE |
| RECALL_SHADOW_PULLBACK | 11 | +2.0769 | 427 | -0.0029 | HIGH_TEST_DECAY |
| POWER_TREND_EXTENSION | 11 | +7.0021 | 334 | +0.0197 | MODERATE_NEEDS_PAPER_SHADOW_GATE |
| QQQ_TECH_TACTICAL_SHORT | 10 | -1.5921 | 284 | -0.0116 | HIGH_TRAIN_ONLY |
| SIMPLE_SECTOR_RS | 11 | +2.8621 | 672 | +0.0003 | MODERATE_NEEDS_PAPER_SHADOW_GATE |
| SIMPLE_MOM_20_60 | 11 | +1.6875 | 670 | -0.0064 | ELEVATED |
| RANDOM_LIQUID | 11 | +0.7264 | 671 | +0.0030 | MODERATE_NEEDS_PAPER_SHADOW_GATE |

## Paper-Shadow Decision

NO_VARIANT_READY_FOR_PAPER_SHADOW

