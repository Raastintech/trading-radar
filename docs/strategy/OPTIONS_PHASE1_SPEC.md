# Options Phase 1 Spec

Snapshot Date: `2026-03-23`

This document defines the correct first options build for `SniperTradingAI`.

It replaces the earlier idea of starting with the wheel as the primary options
engine. The wheel remains a valid Phase 2 strategy, but it is not the best
first implementation for the current system.


## 1. Executive Decision

Build **defined-risk option spreads first**, then build the **wheel** second.

Phase 1 order:

1. `VOYAGER` / `CONTRARIAN` -> **bull put credit spreads**
2. `SHORT` -> **bear call credit spreads**
3. `SNIPER` -> **call debit spreads**
4. `REMORA` -> excluded from initial options rollout
5. `Wheel` -> Phase 2, Voyager-only at first


## 2. Why This Is The Right Order

There is no single "most profitable" options strategy across all regimes.
That is not how real options trading works.

The correct question is:

`Which simple options structures best convert the current equity engine's edge into tradable, controlled-risk positions?`

For this system, the answer is **vertical spreads first** because:

- the equity engine already produces directional and quality signals
- defined-risk spreads are simpler than assignment-based wheel mechanics
- spreads use less capital than stock or cash-secured puts
- spreads can monetize setups that fail stock-level R/R thresholds
- spreads fit both long and short strategies cleanly

The wheel is valuable, but it is:

- assignment-heavy
- operationally more complex
- poor fit for `SNIPER`
- best when stock and options live at the same broker


## 3. Broker Decision

### Execution

Use **Alpaca first** for options execution.

Reason:
- the equity system already uses Alpaca
- Alpaca now supports live options trading
- Alpaca supports:
  - Level 1: covered calls, cash-secured puts
  - Level 2: long calls and puts
  - Level 3: spreads / multi-leg orders
- assignment and stock/options state stay cleaner if the broker is unified

### Market Data

Use a provider hierarchy:

1. Alpaca options market data if configured and available
2. `TradierOptionsFeed`
3. `yfinance` fallback for paper/research only

Current repo status:
- real-time Tradier chain feed already exists in [tradier_options_feed.py](./tradier_options_feed.py)
- options chain abstraction already exists in [options_intelligence.py](./options_intelligence.py)

### Why Not Tradier First For Execution

Tradier is usable, but not ideal as the primary execution venue for the first
build because:

- your equities already live at Alpaca
- a future wheel engine becomes messy if stock is at one broker and options at another
- assignment, covered-call transitions, and stock delivery are cleaner at one broker

Tradier is still useful as:
- a chain/greeks data source
- a fallback broker path later if needed

### Better Than Tradier?

If you deliberately want an options-specialist secondary stack, `tastytrade`
is a better candidate than Tradier for a pure options system.

For this repo, though, the best first production architecture is:

- **Alpaca execution**
- **Alpaca or Tradier data**
- **no multi-broker assignment dependency in Phase 1**


## 4. How We Use The Existing System

The options engine must be an **overlay**, not a replacement for the current
equity scanners.

Use the current scanners as the underlying filter layer:

- `VOYAGER` provides durable institutional long candidates
- `SNIPER` provides tactical breakout candidates
- `SHORT` provides bearish deterioration candidates
- `CONTRARIAN` provides panic-reversal candidates

The options engine should source underlyings from:

1. current-cycle approved opportunities
2. recent high-quality scanner rejects that failed **stock-level R/R**

This second group matters.

The equity engine often rejects because stock R/R is too poor. Options spreads
can improve capital efficiency and payoff geometry on those same underlying
theses.

That is the true bridge between the current system and options.


## 5. Underlying Selection Rules

### Use Allowed Strategies

- `VOYAGER`
- `SNIPER`
- `SHORT`
- `CONTRARIAN`

### Do Not Use Initially

- `REMORA`

Reason:
- Remora is quiet-flow/open-window driven and operationally noisier
- it should not be the first options deployment path

### Underlying Source Query

Preferred:
- current in-memory opportunity objects from the live scan cycle

Fallback:
- `decisions` rows from the last `24-48h`

Allowed underlying classes:

1. **Approved equity candidates**
2. **High-score R/R rejects**

R/R reject salvage rules:
- strategy in allowed set
- `execution_deny_reason='risk_reward_too_low'`
- complete `entry_price`, `stop_loss`, `target_price`
- score near live threshold or better
- no data-quality or pathway-hard-fail issue


## 6. Strategy Mapping

### 6.1 Voyager -> Bull Put Credit Spread

Use when:
- long-quality thesis is intact
- IV is fair-to-rich
- underlying trend and structure are still constructive

Why:
- Voyager names are durable, liquid, and institutionally supported
- a bull put spread monetizes "stay above support" better than a wheel at this stage

Default template:
- DTE: `30-45`
- short put delta: `0.18-0.28`
- long put delta: `0.05-0.15`
- width: `2-10` dollars depending on underlying price/liquidity
- min credit / width: `>= 0.20`


### 6.2 Contrarian -> Bull Put Credit Spread

Use when:
- reversal signal is active
- VIX / underlying IV is rich
- signal is confirmed by regime + washout logic

Why:
- panic reversals often carry inflated put premium
- defined-risk premium selling is better than naked put exposure

Stricter than Voyager:
- higher minimum liquidity
- tighter earnings/event filter
- smaller max risk budget


### 6.3 Short -> Bear Call Credit Spread

Use when:
- deterioration thesis is active
- IV is fair-to-rich
- bearish structure remains intact

Why:
- directly converts current short scanner into defined-risk premium selling
- operationally simpler than short stock plus easier to size

Default template:
- DTE: `30-45`
- short call delta: `0.18-0.28`
- long call delta: `0.05-0.15`
- min credit / width: `>= 0.20`


### 6.4 Sniper -> Call Debit Spread

Use when:
- breakout-quality signal is active
- IV is not excessively rich
- move is expected to be directional, not just stagnant

Why:
- Sniper is about convex upside timing
- covered calls and wheel mechanics are the wrong expression for breakout edge
- debit spreads preserve upside while capping premium outlay

Default template:
- DTE: `30-60`
- long call delta: `0.55-0.70`
- short call delta: `0.25-0.45`
- max debit as % of width: strategy-specific cap


## 7. Core Risk Rules

### Credit Spreads

- take profit at `50%` of max credit
- stop loss at `1.5x-2.0x` credit paid
- auto-close at `21 DTE` if still open
- auto-close before earnings blackout
- exit early if short strike delta expands beyond threshold and thesis weakens

### Debit Spreads

- take profit at `40-60%` return on debit or when spread value reaches target
- stop if:
  - underlying hits thesis invalidation, or
  - spread loses predefined % of debit
- do not carry low-DTE decaying debit spreads blindly into expiry

### Global Rules

- always use limit orders
- never use naked short options in Phase 1
- never use undefined-risk multi-leg structures in Phase 1
- no iron condors, calendars, butterflies, ratio spreads, or short straddles in Phase 1


## 8. IV And Event Rules

### IV Regime Use

Credit spreads:
- allowed only when IV rank is fair-to-rich

Debit spreads:
- preferred when IV rank is cheap-to-fair

### Earnings

Do **not** use `macro_calendar.py` for ticker earnings.

Reason:
- [macro_calendar.py](./macro_calendar.py) is a macro-events calendar, not a per-ticker earnings service

Create a dedicated earnings adapter for:
- next earnings date
- earnings within N trading days

No new position if earnings are inside blackout window.


## 9. Position Sizing

Phase 1 account risk model:

- max risk per spread position: `0.5% - 1.0%` of account
- max options portfolio risk: separate cap from equity portfolio
- do not stack multiple option structures on the same ticker initially
- do not open a new spread if the underlying already has an open equity position unless explicitly allowed

Spread risk basis:
- credit spread risk = width - credit
- debit spread risk = debit paid


## 10. Recommended Architecture

Build the options overlay as a fully isolated module.

Do not modify existing strategy logic.

### New Files

1. `options_underlying_router.py`
2. `options_earnings_adapter.py`
3. `options_iv_engine.py`
4. `options_spread_scanner.py`
5. `options_state_manager.py`
6. `options_broker_router.py`
7. `options_logger.py`
8. `options_master.py`
9. `print_options_report.py`

### New Tables

1. `option_positions`
2. `option_legs`
3. `option_iv_history`
4. `option_candidates`

### Integration Rule

Additive integration only:
- one init block in `unified_master_trader_v3.py`
- one cycle-execution block in `run_scan_cycle`

No changes to:
- scanner logic
- equity execution flow
- existing `trades` or `decisions` schema usage for equity positions


## 11. Claude Code Implementation Prompt

```text
Build the SniperTradingAI Options Spread Engine as a fully isolated module.

MISSION
Create a production-candidate options overlay that converts the existing equity
scanner edge into defined-risk options structures without modifying existing
equity strategy logic.

PHASE ORDER
Phase A: options spreads only
Phase B: wheel engine later

NON-NEGOTIABLE DESIGN RULES
1. Zero modifications to existing scanner decision logic.
2. Options positions must never be written into the existing equity `trades`
   table.
3. Use separate DB tables for options state and legs.
4. All options execution must be non-fatal to the equity daemon.
5. Phase A supports only defined-risk spreads:
   - bull put credit spreads
   - bear call credit spreads
   - call debit spreads
6. No naked short options, no iron condors, no calendars, no butterflies,
   no straddles, no strangles in Phase A.
7. Alpaca is the primary options execution broker.
8. Tradier is allowed as options chain / greeks data source and optional broker
   fallback only if explicitly configured.
9. Use current live scanner output and recent decision telemetry as the
   underlying filter layer.
10. Do not use `macro_calendar.py` as a ticker earnings source. Build a
    dedicated earnings adapter.

STRATEGY MAP
1. VOYAGER -> Bull Put Credit Spread
2. CONTRARIAN -> Bull Put Credit Spread
3. SHORT -> Bear Call Credit Spread
4. SNIPER -> Call Debit Spread
5. REMORA -> excluded from Phase A

UNDERLYING SELECTION
Use only tickers from:
1. current-cycle approved scanner opportunities, OR
2. last 48h high-quality scanner rejects with:
   - execution_deny_reason='risk_reward_too_low'
   - complete entry/stop/target data
   - no hard data failure
   - score quality near or above live threshold

Use these sources:
- in-memory current-cycle opportunity lists if available
- fallback to `decisions` table

ENTRY STRUCTURES

Bull Put Credit Spread
- DTE: 30-45
- short put delta: 0.18-0.28
- long put delta: 0.05-0.15
- minimum OI per leg: 500
- minimum option volume per leg: 50
- max bid/ask spread: 5% of mid
- minimum credit/width ratio: 0.20

Bear Call Credit Spread
- DTE: 30-45
- short call delta: 0.18-0.28
- long call delta: 0.05-0.15
- same liquidity rules
- same minimum credit/width ratio

Call Debit Spread
- DTE: 30-60
- long call delta: 0.55-0.70
- short call delta: 0.25-0.45
- same liquidity rules
- prefer cheap/fair IV regime

IV RULES
- Create `option_iv_history` and compute IV rank / IV percentile
- Credit spreads allowed only in fair/rich IV
- Debit spreads preferred in cheap/fair IV
- bootstrap with shorter history if 252d history unavailable, but store all
  daily IV snapshots and upgrade to 252d rank when data matures

EARNINGS RULES
- create `options_earnings_adapter.py`
- do not open new spreads inside earnings blackout window
- auto-close short premium spreads ahead of earnings

RISK RULES

Credit spreads:
- auto-take-profit at 50% of max credit
- hard stop at 1.5x to 2.0x original credit
- auto-manage at 21 DTE
- delta expansion / thesis-break exit support

Debit spreads:
- profit target on spread return or target value
- stop on thesis invalidation or debit-loss threshold
- avoid holding decaying low-DTE spreads into expiry by default

POSITION SIZING
- max spread risk per position: 0.5% to 1.0% of account
- one structure per ticker initially
- no stacking with open equity position unless explicitly allowed by config

FILES TO CREATE

1. options_underlying_router.py
   - reads current cycle opportunities and recent decision telemetry
   - returns strategy-tagged eligible underlyings
   - supports approved candidates and RR-reject salvage candidates

2. options_earnings_adapter.py
   - returns next earnings date per ticker
   - blackout check helper
   - no dependency on macro calendar

3. options_iv_engine.py
   - stores daily IV snapshots
   - computes iv_rank / iv_percentile
   - returns IV regime

4. options_spread_scanner.py
   - chain selection and spread construction
   - strategy-aware structure selection
   - contract scoring:
     - underlying thesis quality
     - IV fit
     - liquidity
     - delta fit
     - DTE fit
     - credit/width or debit/width efficiency

5. options_state_manager.py
   - position dataclass
   - lifecycle states
   - open/manage/close state transitions

6. options_broker_router.py
   - Alpaca primary execution
   - Tradier optional fallback if configured
   - limit orders only
   - multi-leg support where broker allows it
   - never let options routing exceptions break equity runtime

7. options_logger.py
   - ensure tables:
     - option_positions
     - option_legs
     - option_iv_history
     - option_candidates

8. options_master.py
   - main orchestration
   - manage open options positions
   - build new candidates
   - execute paper or live
   - summarize cycle output

9. print_options_report.py
   - daily / intraday summary for open and closed options positions
   - grouped by strategy and structure

DB TABLES

option_positions:
- id
- ticker
- underlying_strategy
- structure_type
- state
- broker
- paper_mode
- opened_at
- closed_at
- max_risk_usd
- max_profit_usd
- total_pnl_usd
- total_pnl_pct
- notes

option_legs:
- id
- position_id
- leg_role
- contract_symbol
- side
- qty
- strike
- expiry
- dte_at_entry
- delta_at_entry
- iv_rank_at_entry
- premium_entry
- premium_exit
- opened_at
- closed_at
- close_reason
- pnl_usd

option_iv_history:
- id
- ticker
- date
- atm_iv
- iv_rank_30d
- iv_rank_252d
- iv_pct_252d

option_candidates:
- id
- run_id
- ticker
- underlying_strategy
- candidate_type
- structure_type
- score
- rejected_reason
- payload_json
- created_at

INTEGRATION INTO unified_master_trader_v3.py

Block 1:
- initialize `OptionsMaster` near other optional modules
- import-guarded
- non-fatal on failure

Block 2:
- call `run_options_cycle()` near end of scan cycle
- pass:
  - current regime snapshot
  - current cycle approved opportunities if available
  - active equity positions
- non-fatal on failure

ENV FLAGS

Phase A paper:
- OPTIONS_ENABLE=1
- OPTIONS_LIVE=0

Phase A live:
- OPTIONS_ENABLE=1
- OPTIONS_LIVE=1
- ALPACA_OPTIONS_ENABLED=1

Optional fallback:
- TRADIER_ACCOUNT_ID=...
- TRADIER_API_TOKEN=...

VALIDATION CRITERIA BEFORE LIVE

Minimum paper window:
- 30 calendar days

Require before live activation:
- at least 10 completed spread trades
- clean open/close lifecycle logs
- no earnings-blackout violations
- no undefined-risk trades
- no single options position loss > 1.5% of account
- win rate and expectancy reviewed by structure:
  - bull put credit spread
  - bear call credit spread
  - call debit spread

DO NOT MODIFY
- voyager_production_v2_complete.py
- sniper_scanner_v2.py
- remora_scanner_v2.py
- short_scanner_v1.py
- contrarian_scanner.py
- decision_logger.py
- equity `trades` table behavior

If you must add integration, do it only in additive, isolated hooks.
```


## 12. Phase 2 Wheel Rules

Only after spread engine paper validation.

Wheel scope:
- `VOYAGER` only at first
- maybe `CONTRARIAN` later
- not `SNIPER`
- not `REMORA`

Wheel broker rule:
- if the wheel is built, stock and options must be managed at the same broker
- because Alpaca already carries the equity stack, the wheel should be
  **Alpaca-first**


## 13. Honest Standard

This design is meant to be **best-in-class for this system**, not marketed as
"guaranteed best in the world."

That standard requires:
- technical correctness
- risk discipline
- truthful telemetry
- evidence-based promotion

That is the only professional way to build it.
