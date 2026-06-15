# Architecture Phase 1

This document defines the canonical runtime and module boundaries for the
independent equity trading terminal.

Scope:
- stabilize the current live architecture
- stop version drift across V2/V3 and multiple daemon entrypoints
- identify which components are core, which should be migrated, and which
  should be treated as legacy during Phase 1


## 1. Canonical Runtime

The live production path is:

1. `start_trader.sh`
2. `unified_master_trader_v3.py`
3. `trade_journal.jsonl` / `trader_heartbeat.json` / `logs/trader_v3_YYYYMMDD.log`
4. operator UIs:
   - `live_dashboard_v3.py`
   - `chief_strategist_terminal.py`
   - `command_center_terminal_v3.py`

The canonical shutdown path is:

1. `stop_trader.sh`

This is the only runtime that should be treated as production for Phase 1.


## 2. Product Positioning

This system is not trying to replicate Bloomberg breadth.

It is being built as:

`An equity trading terminal for independent traders`

Primary product goals:
- decision speed
- regime-aware scanning
- explainable setup ranking
- integrated execution workflow
- institutional-style risk awareness for non-institutional users


## 3. Core Architecture Surface

These modules are required for the current live system to function and should be
considered protected core architecture.

### 3.1 Runtime / orchestration

- `start_trader.sh`
- `stop_trader.sh`
- `unified_master_trader_v3.py`
- `trading_state.py`

### 3.2 Data / broker / market feed

- `alpaca_data.py`
- `live_feed.py`
- `config.py`
- Alpaca trading client usage inside `unified_master_trader_v3.py`

### 3.3 Core strategy logic

- `voyager_production_v2_complete.py`
- `sniper_scanner_v2.py`
- `remora_scanner_v2.py`
- `short_scanner_v1.py`
- `contrarian_scanner.py`

### 3.4 Shared scoring / mapping / fundamentals

- `enhanced_strategy_scoring.py`
- `strategy_check_mapper.py`
- `fundamental_data_fetcher.py`
- `strategy_display.py`

### 3.5 Discovery / universe construction

- `universe_snapshot_builder.py`
- `voyager_adaptive_universe.py`
- `sniper_adaptive_universe.py`
- `remora_adaptive_universe.py`
- `triple_confluence_detector.py`

### 3.6 Execution / safety / risk gates

- `execution_policy.py`

### 3.7 Operator surfaces

- `live_dashboard_v3.py`
- `chief_strategist_terminal.py`
- `command_center_terminal_v3.py`
- `terminal_helpers.py`

### 3.8 Core system artifacts

- `trade_journal.jsonl`
- `trader_heartbeat.json`
- `logs/trader_v3_YYYYMMDD.log`
- `trading_performance.db`
- `position_overrides.json`


## 4. Current Live Runtime Behavior

Today the active V3 daemon does the following:

- initializes broker connection and account equity
- uses `AlpacaDataFeed` as the central data source
- derives market-breadth context from the canonical Alpaca-backed breadth
  universe used by `market_breadth_monitor.py`, using a stock/sector composite
  breadth model rather than a flat constituent scrape
- runs `VoyagerProductionV2Complete`
- builds a shared dynamic base universe and routes strategy-specific universes
- runs dedicated Sniper scanning
- runs dedicated Remora scanning
- runs dedicated Short scanning
- runs Contrarian scanning under fear conditions
- executes approved trades with policy checks and bracket validation
- writes heartbeat and trade journal metadata
- feeds the dashboards and terminals via logs, heartbeat, DB, and journal

Important current limitations:

- strategy quality is not yet proven just because runtime plumbing is live
- historical telemetry contains legacy/non-canonical rows and should not drive
  forward strategy changes by itself
- broad Phase 2 edge expansion should not proceed until current readiness and
  change-control rules are respected

Current-state rule:

- use `CURRENT_READINESS.md` for live readiness status
- use this file for architectural boundaries and canonical runtime definition


## 5. Components To Migrate From V2

These modules still matter and should be evaluated for migration into V3 rather
than left behind in the older stack.

### 5.1 Portfolio / allocation layer

- `portfolio_coordinator.py`
- `regime_filter.py`

Why:
- portfolio heat
- strategy allocation
- capital coordination across strategies

### 5.2 Legacy institutional trading stack

- `signal_generator.py`
- `veto_council.py`
- `profit_taker.py`
- `remora_engine.py`
- `remora_data_adapter.py`
- `remora_veto_council.py`
- `remora_position_manager.py`

Why:
- contains older but still useful execution and validation logic
- may hold filters not yet represented in V3-native scanners

### 5.3 Overlay / post-trade intelligence

- `system_fixes.py`
- `ml_exit_optimizer.py`
- `performance_analytics.py`
- `decision_logger.py`
- `dynamic_watchlist_builder.py`
- `unusual_whales_integration.py`

Why:
- these are differentiation features, not noise
- they matter for institutional-style workflow and trader edge


## 6. V2 Migration Matrix

This section converts the V2 carryover list into an actionable decision set.

Statuses:
- `MIGRATE`: move into V3 architecture in Phase 1 or early Phase 2
- `DEFER`: useful, but not required to stabilize the core architecture now
- `RETIRE`: keep only as historical reference unless a specific capability is
  later re-adopted

### 6.1 Migrate

#### `decision_logger.py`

Status:
- `MIGRATE`

Why:
- directly supports attribution, dashboards, and auditability
- already writes into `trading_performance.db`, which the UI uses
- low architectural risk and high product value

Phase 1 target:
- make V3 the canonical writer for decision-level logging, not just trade journal

#### `portfolio_coordinator.py`

Status:
- `MIGRATE`

Why:
- contains portfolio-level constraints that V3 still lacks as a first-class system
- useful for capital allocation, position caps, portfolio heat, and strategy budgeting

Phase 1 target:
- extract the allocation/risk-budget concepts
- do not copy the entire V2 mode system blindly

#### `system_fixes.py`

Status:
- `MIGRATE`

Why:
- `OptimizedRiskOverlay` and `LimitOrderExecutor` are practical, not cosmetic
- these are implementation improvements that fit the V3 runtime well

Phase 1 target:
- migrate the pieces that improve execution and cached risk checks
- avoid pulling V2-only assumptions with them

#### `performance_analytics.py`

Status:
- `MIGRATE`

Why:
- performance attribution is a core moat for an independent-trader terminal
- current pathway performance UI is ahead of the underlying realized-PnL model

Phase 1 target:
- align analytics with V3 journal/DB schema
- prioritize realized trade outcomes over old Sniper/Remora-only reporting


### 6.2 Defer

#### `ml_exit_optimizer.py`

Status:
- `DEFER`

Why:
- strategically valuable, but not necessary to stabilize the live architecture
- dependent on clean realized trade data, which is not mature enough yet

Bring forward when:
- exit/outcome logging is trustworthy
- enough labeled history exists for meaningful optimization

#### `unusual_whales_integration.py`

Status:
- `DEFER`

Why:
- potentially differentiating, but adds paid external dependency risk
- not required for core platform stabilization
- options-flow overlay should not be added before the core strategy runtime is coherent

Bring forward when:
- you explicitly want premium flow intelligence in the product
- data vendor reliability and cost fit the product roadmap

#### `dynamic_watchlist_builder.py`

Status:
- `DEFER`

Why:
- useful discovery logic, but V3 already has adaptive universe builders
- should be compared against the current universe stack before merging

Bring forward when:
- you want one unified discovery layer replacing fragmented universe builders

#### `regime_filter.py`

Status:
- `DEFER`

Why:
- regime concepts still matter, but V3 already has regime handling across multiple modules
- needs harmonization rather than direct reuse

Bring forward when:
- you are ready to consolidate regime logic into one canonical model


### 6.3 Retire

#### `signal_generator.py`

Status:
- `RETIRE`

Why:
- tightly coupled to the older V2 signal stack
- V3 already has a dedicated Sniper scanner and newer weighted scoring path

Reuse rule:
- only mine specific filters if they are proven missing from V3

#### `veto_council.py`

Status:
- `RETIRE`

Why:
- powerful conceptually, but tied to the V2 approval architecture
- would create parallel approval logic if pulled in wholesale

Reuse rule:
- treat as idea/reference source, not a live dependency

#### `profit_taker.py`

Status:
- `RETIRE`

Why:
- old exit logic belongs to the V2 stack
- V3 should evolve one canonical exit/risk-management path instead

#### `remora_data_adapter.py`

Status:
- `RETIRE`

Why:
- part of the older Remora architecture
- if Remora is promoted inside V3, use the current `remora_scanner_v2.py` /
  `remora_engine.py` path rather than reviving the older adapter chain

#### `remora_veto_council.py`

Status:
- `RETIRE`

Why:
- same issue as `veto_council.py`
- creates duplicated decision logic

#### `remora_position_manager.py`

Status:
- `RETIRE`

Why:
- valuable as reference, but should not become a second position-management system
- V3 needs one unified execution and lifecycle model


### 6.4 Special Case

#### `remora_engine.py`

Status:
- `MIGRATE PARTIALLY`

Why:
- it appears to contain real institutional-flow logic and weighted-scoring integration
- unlike other V2 Remora modules, this file may still hold core signal logic worth preserving

Phase 1 target:
- treat this as a signal-logic donor to strengthen `remora_scanner_v2.py` and V3 Remora integration
- do not revive the full old Remora subsystem around it


## 7. Files To Treat As Legacy During Phase 1

These files should not be treated as active production architecture unless
explicitly re-adopted.

- `unified_master_trader_v2.py`
- `production_daemon.py`
- `ultimate_production_daemon.py`
- `start_daemon.sh`
- `live_dashboard_v2.py`
- older Voyager scanner variants:
  - `voyager_scanner.py`
  - `voyager_scanner_fixed.py`
  - `voyager_scanner_enhanced.py`
  - `voyager_complete.py`
  - `voyager_production_v2.py`
- older premarket variants:
  - `premarket_scanner.py`
  - `premarket_scanner_vix_aware.py`
- simple/older terminal variants:
  - `command_center_terminal_v2_simple.py`

Phase 1 policy:
- do not delete these yet
- do not continue feature development in them
- treat them as reference or migration sources only


## 8. Phase 1 Stabilization Goals

Phase 1 is about architectural clarity, not feature sprawl.

### 8.1 Define one production stack

Required decision:
- `unified_master_trader_v3.py` is the only production daemon

Required behavior:
- all new trading, scoring, journaling, and UI work targets V3

### 8.2 Stop runtime drift

Required action:
- no new feature work in V2 daemons or alternate daemon entrypoints
- no ambiguity about which launcher is "real"

### 8.3 Preserve critical V2 intelligence

Required action:
- inventory V2-only filters and risk systems before deprecating them
- move them intentionally into V3 or explicitly reject them

### 8.4 Promote all intended strategies to first-class V3 modules

Target end state:
- Voyager
- Sniper
- Remora
- Short
- Contrarian as an overlay/opportunistic strategy

### 8.5 Keep operator surfaces synced to the same data model

Required:
- dashboards and terminals should consume the same canonical strategy,
  score, pathway, and journal fields


## 9. Phase 2 Entry Criteria

Do not begin broad Phase 2 expansion until the following are true:

1. `start_trader.sh` and `stop_trader.sh` are the only supported daemon control path
2. `unified_master_trader_v3.py` is documented as canonical
3. dedicated Short and Remora integration plan is defined
4. V2 migration candidates are explicitly categorized:
   - migrate
   - defer
   - retire
5. dashboards and journal fields are aligned to the V3 data model
6. `CURRENT_READINESS.md` shows runtime and telemetry at least `GREEN/AMBER`
   with explicit open items for strategy proof


## 10. Immediate Build Order After Phase 1

Once architecture is stabilized, the next implementation order should be:

1. integrate dedicated Short scanner into V3
2. integrate dedicated Remora scanner into V3
3. migrate portfolio coordinator / risk overlay concepts from V2
4. migrate realized trade analytics and decision attribution
5. unify alerting / opportunity triage across dashboards


## 11. Non-Goals For Phase 1

Do not do these first:

- broad UI redesign for its own sake
- adding more parallel scanner variants
- Bloomberg-style asset class expansion
- adding many new panels without stronger data plumbing
- deleting old files before migration decisions are recorded


## 12. Phase 1 Execution Checklist

1. Mark `unified_master_trader_v3.py` as the only supported daemon path.
2. Keep `start_trader.sh` / `stop_trader.sh` as the only supported control scripts.
3. Integrate dedicated `short_scanner_v1.py` into V3.
4. Integrate dedicated `remora_scanner_v2.py` into V3.
5. Add decision-level logging to V3 using `decision_logger.py`.
6. Define the minimum portfolio-coordination subset to migrate from `portfolio_coordinator.py`.
7. Define the minimum execution/risk subset to migrate from `system_fixes.py`.
8. Align realized trade analytics with `performance_analytics.py` or replace it with a V3-native equivalent.
9. Freeze feature development in V2-only daemon paths and legacy scanners.
10. Revisit deferred modules only after the above are complete.


## 13. Working Rule

For all future development unless explicitly overridden:

`Build on the V3 daemon path, migrate useful V2 intelligence into it, and freeze legacy variants.`

Additional rule:

`Do not confuse architecture completion with strategy proof.`
