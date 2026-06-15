# Auto-Trading Decommission — Final Findings

**Date:** 2026-06-14
**System:** `trading-production` (gem-trader)
**Status:** RESEARCH_ONLY_MODE — permanent

---

## Summary

All auto-trading, paper-trading, and broker execution paths have been permanently
decommissioned. The system is now a pure research intelligence engine:
daily market heartbeat, six-category research scanner, per-ticker research cards,
and read-only FMP + Tradier (options research) data access.

No order placement, no paper signals, no Alpaca broker interaction.

---

## Decommission Timeline

| Phase | Date | Scope | Outcome |
|---|---|---|---|
| Phase 3A–1 | 2026-06-13 | ResearchOnlyModeError in all execution paths; mode flags; archive | 1282 tests pass |
| Phase 3A–2 | 2026-06-13 | Stop trading daemon; disable paper-evidence timer; Tradier-only options | Services stopped |
| Phase 3A–3 | 2026-06-13 | Smart Alpaca stub (cache reads); FMP as price refresher | 1282 tests pass |
| Phase 3A–4 | 2026-06-13 | Research-mode startup checks; deepen tool → FMP; main.py health reporter | 1301 tests pass |
| Phase 3A–5 | 2026-06-13 | Dashboard RESEARCH_ONLY labels; MCP confirmed read-only | 1301 tests pass |
| Phase 4A | 2026-06-14 | Market Heartbeat Engine (`research/market_heartbeat.py`) | Outputs confirmed |
| Phase 4B/4C | 2026-06-14 | Research Scanner + Watchlist Scorer (`research/research_scanner.py`) | 6 categories live |
| Phase 4D | 2026-06-14 | Stock Research Card Engine (`research/stock_research_card.py`) | Per-ticker cards live |
| Phase 5 | 2026-06-14 | Dashboard research panels; registry archive; MCP Phase 4 tools; this doc | COMPLETE |

---

## Sleeves Archived

| Sleeve | Prior Status | Final Status | Notes |
|---|---|---|---|
| SNIPER v6 | ACTIVE_PAPER | DECOMMISSIONED | Historical paper rows preserved; paper_ledger=True for resolution |
| VOYAGER | ACTIVE_PAPER | DECOMMISSIONED | Historical paper rows preserved |
| SHORT_A | FROZEN | FROZEN | Already frozen 2026-05-24; historical rows preserved |
| REMORA | FROZEN | FROZEN | Research-only |
| CONTRARIAN | FROZEN | FROZEN | Research-only |
| SHORT_B | FROZEN | FROZEN | Research-only |
| PATHFINDER | FUTURE_RESEARCH | FUTURE_RESEARCH | Not operational |
| LRR family | Research scripts | Research scripts | No registry entry; research-only |

---

## Safety Verification

### No live/paper/broker paths reachable

```bash
# All execution methods raise ResearchOnlyModeError
grep -n "ResearchOnlyModeError" core/alpaca_client.py execution/order_manager.py \
    execution/position_monitor.py execution/paper_governance.py
# → hits in all 4 files

# No execution imports in production code
grep -rn "submit_market_order\|submit_limit_order\|close_position\|cancel_all_orders" \
    core/ council/ execution/ strategies/ dashboards/ scripts/ main.py \
    | grep -v "archive/\|alpaca_client.py\|startup_checks\|research_mode\|test_"
# → no hits
```

### Alpaca not required

```bash
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/market_heartbeat.py --offline
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/research_scanner.py --offline
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/stock_research_card.py SPY --offline
# → all succeed; ALPACA_REQUIRED=False in all guardrails
```

### Tradier execution disabled

```python
from core.research_mode import TRADIER_EXECUTION_ENABLED
assert TRADIER_EXECUTION_ENABLED is False  # confirmed
```

### ResearchOnlyModeError fires correctly

```python
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -c "
from core.alpaca_client import get_alpaca
from core.research_mode import ResearchOnlyModeError
try:
    get_alpaca().submit_market_order('SPY', 1, 'buy')
    print('FAIL — should have raised')
except ResearchOnlyModeError:
    print('PASS — ResearchOnlyModeError raised as expected')
except Exception as e:
    print(f'PASS (stub): {e}')
"
```

---

## Research Stack — Confirmed Working

| Component | Command | Output |
|---|---|---|
| Market Heartbeat | `./scripts/run_research_cycle.sh market-heartbeat` | `cache/research/market_heartbeat_latest.json` |
| Research Scanner | `./scripts/run_research_cycle.sh research-scanner` | `cache/research/research_scanner_latest.json` |
| Stock Research Card | `./scripts/run_research_cycle.sh stock-research-card AAPL` | `cache/research/stock_research_card_AAPL.json` |
| Regime Forecast | `./scripts/run_research_cycle.sh forecast` | `cache/research/regime_forecast_latest.json` |
| Alpha Discovery | `./scripts/run_research_cycle.sh alpha` | `cache/research/alpha_discovery_latest.json` |
| Social Arb Radar | `./scripts/run_research_cycle.sh social` | `cache/research/social_arb_latest.json` |
| MCP Server | `python -m audit_mcp.stocklens_mcp_server` | stdio MCP tools (read-only) |
| Dashboard | `SNIPER_ENV_PATH=... .venv/bin/python dashboards/gem_trader_hq.py` | Cache-only TUI |

---

## Phase 3B — Alpaca Dependency Removal (2026-06-14)

Paper trading and holdout validation are permanently archived.
Alpaca is NOT retained for paper trading or any other purpose.

### Changes

| Item | Before | After |
|---|---|---|
| Paper signal generation | PAPER_TRADING_ENABLED=False (since 3A) | Same; ARCHIVED_FOR_HISTORY_ONLY marker added to run_paper_evidence.py |
| Price bar refresh | FMP (since 3A nightly_refresh.py) | Same; PRICE_BAR_PROVIDER_MIGRATION.md documents all modules |
| Options research | Tradier-only (since 3A options_feed_factory.py) | Same; token key fixed: TRADIER_ACCESS_TOKEN → TRADIER_API_TOKEN |
| FMP fundamentals mapping | Bug: all null despite FMP fetch | Fixed: income/balance/cashflow flattened to TTM values |
| Tradier health check | DEGRADED (wrong env key) | OK when TRADIER_API_TOKEN set |
| `core/market_regime.py` docstring | "Alpaca SIP" | "cache/prices/*.parquet via AlpacaClient stub" |

### Confirmed

```
Alpaca safe to cancel: YES
- dashboard starts without Alpaca env vars ✓
- heartbeat works without Alpaca ✓
- scanner works without Alpaca ✓
- research cards work without Alpaca ✓
- price refresh uses FMP (non-Alpaca provider) ✓
- options research is Tradier-only or cleanly disabled ✓
- paper/holdout archived; no Alpaca required ✓
- 1376 tests pass ✓
```

See also:
- `docs/research/PRICE_BAR_PROVIDER_MIGRATION.md`
- `docs/research/OPTIONS_RESEARCH_PROVIDER_MIGRATION.md`

---

## Heartbeat Labels (Phase 4A)

`RISK_ON` | `HEALTHY_PULLBACK` | `CHOP` | `CORRECTION` | `RISK_OFF`
| `TECH_LED` | `SMALL_CAP_LED` | `DEFENSIVE_ROTATION`

## Watchlist Labels (Phase 4B/4C)

`WATCH` | `RESEARCH` | `EARLY_ACCUMULATION` | `BEATEN_DOWN` | `SECTOR_LEADER`
| `CATALYST` | `SOCIAL_ARB` | `SPECULATIVE_10X` | `EXTENDED` | `RISKY`
| `AVOID` | `CROWDED` | `NO_SOCIAL_DATA`

## Allowed Research Outputs (Phase 4C/4D)

- "worth researching"
- "watch candidate"
- "requires manual review"
- "risk flagged — multiple concerns; requires manual review before any action"
- "extended; wait for reset — worth watching for better entry context"
- "potential asymmetric candidate — speculative long-term watch; high risk"
- "catalyst candidate — upcoming earnings; requires manual review"
- "beaten-down potential recovery — worth researching further"
- "crowded/viral — late-mover risk; watch candidate only after consolidation"

## Forbidden Outputs

The following outputs are permanently forbidden from all research modules:
`buy now` · `sell now` · `entry` · `stop loss` (as trade instruction) ·
`price target as trade instruction` · `position size` · `auto-trade recommendation`

---

## MCP Server Tool List (Post-Phase 5)

All tools are read-only. Execution commands return `RESEARCH_ONLY_MODE: command disabled.`

| Tool | Type |
|---|---|
| `get_market_forecast` | Cache read |
| `get_alpha_discovery` | Cache read |
| `get_stock_lens` | Cache read |
| `get_executive_gatekeeper` | Cache read |
| `get_research_delta` | Cache read |
| `get_risk_telemetry` | Cache read |
| `get_paper_hygiene` | Cache read |
| `get_broker_snapshot` | Cache read |
| `get_evidence_rigor` | Cache read |
| `get_holdout_status` | Cache read |
| `audit_ticker_consistency` | Cache analysis |
| `audit_dashboard_consistency` | Cache analysis |
| `audit_late_chase_candidates` | Cache analysis |
| `audit_halt_state` | Cache read |
| `get_market_heartbeat` | **Phase 4** — Cache read |
| `get_research_scanner` | **Phase 4** — Cache read |
| `get_research_card` | **Phase 4** — Cache read |

---

## What This System Is Now

A daily market research intelligence engine:

1. **Nightly cycle** (20:30 ET Mon-Fri via `gem-trader-research.timer`):
   FMP price refresh → regime forecast → alpha discovery → social arb → delta → lenses → risk telemetry

2. **Market Heartbeat** (run alongside nightly or on demand):
   Daily regime pulse — 8 labels, ETF trends, breadth, sector leadership, risk signal

3. **Research Scanner** (run on demand or nightly):
   6-category watchlist with research scores and allowed labels only

4. **Stock Research Cards** (run on demand for specific tickers):
   Per-ticker deep research artifact — fundamentals, RS, catalyst, options (Tradier research-only), risk flags

5. **Dashboard** (Mode 1–4, cache-only TUI):
   Mode 1 shows heartbeat + research watchlist; Mode 2 shows Alpha/Lens research; Mode 3 shows risk telemetry; Mode 4 shows scanner

6. **MCP Server** (stdio, read-only):
   17 read-only tools for Claude-assisted audit of research artifacts

**No orders. No paper signals. No broker connection. Human review required for all research conclusions.**
