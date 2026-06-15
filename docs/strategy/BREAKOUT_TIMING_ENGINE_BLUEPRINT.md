# Breakout Timing Engine Blueprint

## Purpose

The Breakout Timing Engine (`BTE`) is an advisory-only subsystem that predicts
**when** a high-quality breakout is likely to trigger.

It is not a separate trading system.
It is a timing layer that sits beside the current scanner stack and reuses the
canonical runtime, market data, logging, and risk infrastructure already in the
platform.

Initial mandate:
- improve `SNIPER` timing quality without changing live execution behavior
- collect evidence on pre-breakout setups before promoting BTE into execution or
  veto logic
- remain advisory until the model proves incremental value over the current
  setup-detection stack

This design follows:
- `PROJECT_NORTH_STAR.md`
- `MASTER_PLAN.md`
- `STRATEGY_DOCTRINE.md`
- `ARCHITECTURE_PHASE1.md`

## Position In The System

BTE is not meant to replace:
- `sniper_scanner_v2.py`
- `candidate_ranking.py`
- `forecast_engine.py`
- `shadow_rate_forecaster.py`

BTE is meant to complement them.

Current forecast stack answers:
- what happened historically to trades like this over a fixed horizon?

BTE must answer:
- how likely is this pre-breakout setup to trigger soon?
- what is the expected timing window?
- how long did similar setups historically take to break out?

## Operating Mode

Phase 1 mode:
- advisory only
- no live order-routing authority
- no veto authority
- no score veto in `veto_council`
- no execution dependency in `unified_master_trader_v3.py`

Promotion rule:
- BTE remains advisory until calibration and incremental signal value are
  proven on shadow data and then on a meaningful forward sample

## Core Design

### 1. Candidate Definition

A BTE candidate is not a breakout trade.
It is a **pre-breakout pressure snapshot**.

Initial source sleeve:
- `SNIPER`

Candidate characteristics:
- compression / contraction
- constructive base geometry
- improving relative strength
- elevated or improving volume
- proximity to breakout pivot without excessive extension

### 2. Feature Layer

Initial BTE feature set should reuse current live breakout math where possible:
- ATR contraction
- volume ratio vs 20-day average
- RS acceleration vs `SPY`
- prior breakout window / pivot
- base tightness
- close position in the daily range
- higher-lows proxy inside the base
- distance to pivot

Smart-money features can be layered later:
- options chain signal
- insider cluster score
- analyst revision score
- live block-trade participation

### 3. Label Layer

BTE requires explicit breakout-event labels.

Initial event definition:
- breakout occurs when a future daily bar closes above the stored pivot
- breakout bar must also show confirming participation:
  - minimum breakout volume ratio
  - minimum breakout close position in bar range

Initial label outputs:
- `breakout_triggered`
- `trading_days_to_breakout`
- `breakout_timestamp`
- `breakout_volume_ratio`
- `breakout_close_position`
- `max_close_pct_above_pivot`
- `end_return_pct` over the observation horizon

### 4. Forecast Layer

BTE forecast output should be separate from the current trade-outcome forecast.

Target BTE outputs:
- `breakout_probability`
- `timing_window_low_days`
- `timing_window_high_days`
- `median_days_to_breakout`
- `confidence_band`
- `sample_size`
- `model_version`

Phase 1 does not ship a production forecaster yet.
Phase 1 builds the candidate and label dataset correctly.

### 5. Integration Plan

BTE integration order:
1. advisory CLI and DB tables
2. offline reports and diagnostics
3. ranker-side advisory context only
4. strategist/dashboard display
5. veto-council or live decision integration only after proof

## Data Model

### `bte_candidates`

Purpose:
- store pre-breakout snapshots

Required fields:
- ticker
- strategy_source
- candidate_date
- snapshot_timestamp
- entry_price
- breakout_pivot
- consolidation_low
- recent_atr
- avg_volume_20
- atr_contraction_pct
- volume_ratio
- rs_acceleration
- close_position
- base_tightness
- higher_lows
- pivot_distance_pct
- pre_breakout_score
- candidate_state
- model_version
- outcome_status

### `bte_outcomes`

Purpose:
- store timing and breakout-resolution outcomes

Required fields:
- candidate_id
- horizon_days
- breakout_triggered
- breakout_timestamp
- trading_days_to_breakout
- breakout_close
- breakout_volume_ratio
- breakout_close_position
- max_close_pct_above_pivot
- end_return_pct
- data_source
- data_available
- model_version

## Evaluation Standard

BTE should not be promoted on narrative quality.
It should be promoted only if it demonstrates:
- enough labeled sample size
- useful calibration of breakout probability
- stable timing-window estimation
- incremental value over current `SNIPER` setup scoring
- no degradation of execution discipline

Minimum promotion questions:
1. Does BTE sort better than raw `SNIPER` score alone?
2. Does BTE improve timing selectivity?
3. Does BTE reduce false-start breakout setups?
4. Is the timing estimate stable across regimes?

## Build Phases

### Phase 1: Advisory Dataset
- create BTE tables
- create feature extractor
- create candidate seeding tool
- create timing outcome labeler
- create summary report

### Phase 2: Empirical Timing Model
- segment by setup quality, compression, RS, volume, and regime
- compute breakout probability and timing distributions
- validate calibration

### Phase 3: Advisory Surface
- display BTE context in strategist and scanner diagnostics
- attach BTE advisory fields to ranked candidates

### Phase 4: Governance Review
- decide whether BTE remains advisory
- or is promoted into veto / ranking / execution assist

## Non-Goals For Phase 1

Do not:
- change live entry thresholds
- change live order submission logic
- route BTE directly into `veto_council`
- add another daemon
- fork scanner logic into a second trading stack

## Initial Implementation Boundary

First implementation slice should include:
- blueprint document
- advisory-only Python module for:
  - feature extraction
  - candidate persistence
  - breakout labeling
  - summary reporting
- focused tests

That is enough to start collecting real evidence without perturbing live
trading behavior.
