# Phase 4A.5 — T7: Research-Only Safety Audit

*Generated 2026-06-16 | Adversarial audit | RESEARCH_ONLY*

---

## 1. Mode Enforcement

**Single source of truth:** `core/research_mode.py`

```python
SYSTEM_MODE = "RESEARCH_ONLY"
LIVE_TRADING_ENABLED       = False
PAPER_TRADING_ENABLED      = False
BROKER_EXECUTION_ENABLED   = False
STRATEGY_PROMOTION_ENABLED = False
AUTO_ORDER_ROUTING_ENABLED = False
ALPACA_REQUIRED = False
ALPACA_ACTIVE   = False
TRADIER_RESEARCH_ENABLED  = True
TRADIER_EXECUTION_ENABLED = False
FMP_RESEARCH_ENABLED = True
```

`ResearchOnlyModeError` is defined here and raised by ALL execution paths in `core/alpaca_client.py`. ✅

**Re-enabling execution requires deliberate code restoration** from `archive/execution_disabled/alpaca_client.py`. A flag flip alone is not sufficient. ✅

---

## 2. Execution Path Blocks

| Command / Method | Blocked by |
|-----------------|-----------|
| `submit_market_order()` | `AlpacaClient.submit_market_order` → `raise ResearchOnlyModeError` |
| `submit_limit_order()` | Same |
| `submit_bracket_order()` | Same |
| `close_position()` | Same |
| `cancel_all_orders()` | Same |
| Paper trade routing | `PAPER_TRADING_ENABLED = False` in research_mode.py |
| Strategy promotion | `STRATEGY_PROMOTION_ENABLED = False` |
| Auto-route signals | `AUTO_ORDER_ROUTING_ENABLED = False` |

All five `AlpacaClient` execution methods raise `ResearchOnlyModeError` immediately, before any market connectivity check. ✅

---

## 3. Broker Call Audit

| Component | Broker call? | Verdict |
|-----------|-------------|---------|
| `research_scanner.py` | No | ✅ |
| `research_candidate_enrichment.py` | No | ✅ |
| `research_scoring.py` | No | ✅ |
| `catalyst_sanity.py` | No | ✅ |
| `ten_x_candidate_radar.py` | No | ✅ |
| `daily_alpha_radar_report.py` | No | ✅ |
| `research_watchlist_forward_tracker.py` | No | ✅ |
| `dashboards/gem_trader_hq.py` | No (cache-only) | ✅ |
| `scripts/run_research_cycle.sh nightly` | FMP provider calls (forecast, alpha, lenses); NO Alpaca calls | ✅ |
| MCP audit tools | No (cache-only reads) | ✅ |

---

## 4. Forbidden Command Checks

Grep across all `research/`, `scripts/`, `docs/research/` files:

| Forbidden term | Occurrences in research pipeline | Status |
|---------------|----------------------------------|--------|
| `live-trade` | 0 | ✅ CLEAN |
| `paper-trade` | 0 | ✅ CLEAN |
| `place-order` | 0 | ✅ CLEAN |
| `submit-order` | 0 | ✅ CLEAN |
| `bracket-order` | 0 | ✅ CLEAN |
| `promote-strategy` | 0 | ✅ CLEAN |
| `strategy-execute` | 0 | ✅ CLEAN |
| `auto-route` | 0 | ✅ CLEAN |

Grep across research artifacts (`DAILY_ALPHA_RADAR_REPORT.md`, `research_scanner_latest.json`, `daily_alpha_radar_latest.json`):

| Forbidden trade term | Occurrences | Status |
|---------------------|-------------|--------|
| `buy now` | 0 | ✅ CLEAN |
| `sell now` | 0 | ✅ CLEAN |
| `entry price` | 0 | ✅ CLEAN |
| `stop loss` | 0 | ✅ CLEAN |
| `position size` | 0 | ✅ CLEAN |
| `trade recommendation` | 0 | ✅ CLEAN |
| `paper signal` | 0 | ✅ CLEAN |
| `live signal` | 0 | ✅ CLEAN |
| `auto trade` | 0 | ✅ CLEAN |

Legacy term grep across `DAILY_ALPHA_RADAR_REPORT.md`:

| Legacy term | Occurrences | Status |
|------------|-------------|--------|
| VOYAGER / SNIPER / REMORA / SHORT_A | 0 | ✅ CLEAN |
| holdout scoreboard / risk telemetry | 0 | ✅ CLEAN |
| paper loop / broker snap / active paper | 0 | ✅ CLEAN |

---

## 5. Dashboard Safety

- Dashboard (`gem_trader_hq.py`) is cache-only — it reads JSON sidecars and never calls FMP, Alpaca, or Tradier ✅
- Dashboard verification script (`scripts/verify_dashboard_modes_offline.py`) confirmed: **`RENDER_OK MARKET,WATCHLIST,INTEL,RESEARCH`** ✅
- No trade panels exist in the dashboard in RESEARCH_ONLY mode ✅
- RESEARCH_ONLY_BANNER displayed on all module outputs ✅

---

## 6. MCP Tool Safety

MCP tools (`research/mcp_audit_workflows.py`) are read-only:
- They read sidecar JSON files
- They never write to the DB, never call providers, never invoke execution
- The MCP server exposes only `get_*` and `audit_*` operations
- No `mcp__stocklens-audit` tool can trigger a trade or mutation ✅

---

## 7. Tradier Safety

`TRADIER_EXECUTION_ENABLED = False` in `core/research_mode.py`.
Tradier is used only for options chain data collection (15:45 ET timer) and research enrichment. No execution paths use Tradier. ✅

---

## Overall Verdict

| Check | Status |
|-------|--------|
| Execution commands disabled | ✅ PASS — all Alpaca execution methods raise ResearchOnlyModeError |
| Paper trading disabled | ✅ PASS |
| Alpaca not required | ✅ PASS |
| No broker calls in nightly | ✅ PASS |
| No order functions reachable from research cycle | ✅ PASS |
| Dashboard has no trade panels | ✅ PASS |
| MCP exposes read-only tools | ✅ PASS |
| Daily report has no trade language | ✅ PASS — all forbidden terms CLEAN |
| Scanner/card outputs have no trade language | ✅ PASS |

**RESEARCH_ONLY_MODE: FULLY ENFORCED.** The system cannot accidentally drift back into trading mode through a configuration flag or environment variable. Restoration requires deliberate code changes from the archive.
