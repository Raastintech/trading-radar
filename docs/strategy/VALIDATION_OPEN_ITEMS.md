# Validation Open Items

This file tracks validation items that remain open after the major runtime and
telemetry stabilization work.

Use `CURRENT_READINESS.md` for overall status. Use this file for specific
validation tasks that still need proof.


## 1. Runtime Control Path

- Status: `CLOSED`
- Priority: `High`
- Owner: `Runtime validation`

Current state:
- canonical daemon path is `start_trader.sh -> unified_master_trader_v3.py`
- readiness gate is active
- heartbeat and log flow are active


## 2. Outcome Resolver And Shadow Tracking

- Status: `CLOSED`
- Priority: `High`
- Owner: `Telemetry validation`

Current state:
- shadow outcomes resolve for both long and short directions
- analytics exclusions for invalid test trades are supported


## 3. Voyager Forward Reject Telemetry

- Status: `OPEN`
- Priority: `High`
- Owner: `Strategy telemetry`

Why open:
- historical Voyager reject rows often lack `rr`, `stop_loss`, and
  `target_price`
- forward code path was corrected on `2026-03-23`, but needs fresh live proof

Success condition:
- one new forward daemon session shows Voyager reject rows with complete
  `entry_price`, `stop_loss`, `target_price`, and `rr` when those levels are
  available from the scoring path


## 4. Live Short Exit Proof

- Status: `OPEN`
- Priority: `Medium`
- Owner: `Runtime validation`

Why open:
- short direction math is validated in scanners and shadow outcomes
- no fresh real closed short trade has yet proven the live exit lifecycle end
  to end

Success condition:
- one V3-managed short trade closes and preserves:
  - `direction='SHORT'`
  - correct `pnl` sign
  - correct `pnl_pct`
  - correct `actual_rr`
  - correct exit reason


## 5. Strategy Promotion Proof

- Status: `OPEN`
- Priority: `High`
- Owner: `Strategy validation`

Why open:
- runtime stability does not prove signal quality
- current approval rates remain too low for broad production promotion
- strategy decisions should now be based on fresh forward evidence only

Success condition:
- each strategy is reviewed against clean forward data
- any threshold or gate change has:
  - a declared hypothesis
  - a target metric
  - a rollback condition


## 6. Options Overlay Validation

- Status: `OPEN`
- Priority: `High`
- Owner: `Options validation`

Why open:
- Phase A options spreads overlay is now implemented, but only validated by
  unit/integration tests and paper-mode assumptions
- Alpaca multi-leg live routing exists behind explicit flags, but has not yet
  been proven against real broker responses in live market conditions

Success condition:
- paper mode captures real candidates, opens positions, and resolves closes
  over multiple forward sessions
- `print_options_report.py` and DB rows show coherent lifecycle state
- no options routing exception breaks the equity daemon
- live promotion remains disabled until broker behavior is verified


## 7. Documentation Sync

- Status: `CLOSED`
- Priority: `Medium`
- Owner: `Project governance`

Current state:
- `CURRENT_READINESS.md` is the current-state readiness source of truth
- `MASTER_PLAN.md` and `ARCHITECTURE_PHASE1.md` now reference the layered
  readiness standard
