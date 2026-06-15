# Strategy Kill and Repair List (Phase 1H.4)

Generated: 2026-06-12T16:42:54.354764+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

## Strategies to KILL

- **QQQ_TECH_TACTICAL_SHORT** — Confirmed harmful baseline: negative expectancy, -100% independent drawdown in 1H.1/1H.3, and no gate fix addresses being short a bull tape.

## Strategies to REPAIR

- **PROD_SNIPER_CURRENT** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.
- **SNIPER_NO_ATR_CONTRACTION** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.
- **PROD_VOYAGER_CURRENT** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.
- **RECALL_SHADOW_RS_MOMENTUM** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.
- **RECALL_SHADOW_PULLBACK** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.
- **POWER_TREND_EXTENSION** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.
- **CORRECTION_LEADER_RECLAIM** — Accepted flow has positive forward returns and specific gates/protections are identified for replacement testing.

## Strategies to PRESERVE as-is

- none

## Strategies needing more data

- none

## Filters to KEEP (hard blocks that prevent losses)

- `QQQ_TECH_TACTICAL_SHORT.failed_leader` (net avoided loss 56.284)
- `QQQ_TECH_TACTICAL_SHORT.tech_membership` (net avoided loss 22.3483)
- `RECALL_SHADOW_RS_MOMENTUM.above_ema20_not_false` (net avoided loss 22.2146)
- `QQQ_TECH_TACTICAL_SHORT.tech_weakness` (net avoided loss 17.1691)

## Filters to SOFTEN (mixed evidence)

- `PROD_SNIPER_CURRENT.atr_contraction_lt_0_85`
- `PROD_SNIPER_CURRENT.trend_ma50_rising`
- `SNIPER_NO_ATR_CONTRACTION.volume_confirm_1_4x`
- `PROD_VOYAGER_CURRENT.min_bars_260`
- `PROD_VOYAGER_CURRENT.rs50_spy_positive`
- `RECALL_SHADOW_RS_MOMENTUM.momentum_60`
- `RECALL_SHADOW_PULLBACK.momentum_60`
- `RECALL_SHADOW_PULLBACK.rs_floor_pullback`
- `RECALL_SHADOW_PULLBACK.above_ema20_true`
- `POWER_TREND_EXTENSION.extension_band_15_35`

## Filters to REPLACE (overblock winners)

- `RECALL_SHADOW_PULLBACK.pullback_band` (opportunity cost 525.4241)
- `CORRECTION_LEADER_RECLAIM.regime_allowed` (opportunity cost 120.0858)
- `QQQ_TECH_TACTICAL_SHORT.market_risk_weak` (opportunity cost 80.9292)
- `RECALL_SHADOW_RS_MOMENTUM.rs_floor` (opportunity cost 66.396)
- `CORRECTION_LEADER_RECLAIM.ema_reclaim` (opportunity cost 62.6137)
- `RECALL_SHADOW_RS_MOMENTUM.extension_cap` (opportunity cost 61.3015)
- `CORRECTION_LEADER_RECLAIM.not_weaker_than_market` (opportunity cost 58.1267)
- `SNIPER_NO_ATR_CONTRACTION.sniper_universe` (opportunity cost 57.4239)
- `CORRECTION_LEADER_RECLAIM.volume_confirm_or_dryup` (opportunity cost 55.0028)
- `RECALL_SHADOW_PULLBACK.r5_floor` (opportunity cost 48.2012)

## Filters needing more data

- `PROD_SNIPER_CURRENT.liquidity_floor` (matured sole-blocked n=0)
- `PROD_SNIPER_CURRENT.min_bars_75` (matured sole-blocked n=0)
- `PROD_SNIPER_CURRENT.rs10_spy_positive` (matured sole-blocked n=0)
- `PROD_SNIPER_CURRENT.spy_above_ma200_regime` (matured sole-blocked n=1)
- `PROD_SNIPER_CURRENT.sniper_score_70` (matured sole-blocked n=9)
- `SNIPER_NO_ATR_CONTRACTION.liquidity_floor` (matured sole-blocked n=0)
- `SNIPER_NO_ATR_CONTRACTION.min_bars_75` (matured sole-blocked n=0)
- `SNIPER_NO_ATR_CONTRACTION.rs10_spy_positive` (matured sole-blocked n=6)
- `SNIPER_NO_ATR_CONTRACTION.spy_above_ma200_regime` (matured sole-blocked n=9)
- `PROD_VOYAGER_CURRENT.liquidity_floor` (matured sole-blocked n=0)
- `PROD_VOYAGER_CURRENT.price_min_5` (matured sole-blocked n=0)
- `PROD_VOYAGER_CURRENT.ma_available` (matured sole-blocked n=0)
- `PROD_VOYAGER_CURRENT.ma200_floor_0_92` (matured sole-blocked n=6)
- `PROD_VOYAGER_CURRENT.archetype_match` (matured sole-blocked n=0)
- `RECALL_SHADOW_RS_MOMENTUM.liquidity_floor` (matured sole-blocked n=0)

## Worst filter by opportunity cost

- `RECALL_SHADOW_PULLBACK.pullback_band` — opportunity cost 525.4241 across 9933 matured sole-blocked cases

## Best protective filter by avoided loss

- `QQQ_TECH_TACTICAL_SHORT.failed_leader` — avoided loss 205.8599, net value 56.284

## Replacement Test Outcome (from counterfactual backtest)

- Status: **NO_FILTER_REPLACEMENT_READY_FOR_PAPER_SHADOW**
- Best replacement candidate: **PULLBACK_RISK_REGIME_PROTECTION** (REPLACEMENT_IMPROVES_FLOW_ONLY, expectancy delta -0.02%)

## Safety

Research-only. Nothing here changes production thresholds, gates, execution, governance, paper evidence, or SHORT_A's frozen status.

