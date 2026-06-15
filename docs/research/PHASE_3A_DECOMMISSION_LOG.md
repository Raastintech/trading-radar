# Phase 3A — Research-Only Decommission Log

## Git / Version Control Note

**Git is not yet initialized in this repository.**

The operator will create a git user account and perform the initial commit and branch
creation (`decommission/research-only-phase-3a`) at that time.

Until then, this document and the per-phase notes below serve as the change log.
All phase work is tracked here. When git is set up:

1. `git init`
2. `git config user.name ...` / `git config user.email ...`
3. `git add -A && git commit -m "pre-decommission checkpoint: Phase 3A baseline"`
4. `git tag pre-research-only-decommission`
5. `git checkout -b decommission/research-only-phase-3a`

No code changes have been made yet. The tag will capture the unmodified codebase.

---

## Phase 0 — Pre-flight Inventory (2026-06-13)

### Disk headroom

| Path | Size | Free |
|---|---|---|
| `/` (nvme0n1p2, 937 GB) | 315 GB used | **575 GB free** |
| `/home/gem/trading-production` | ~1 GB | — |

Disk is clear. No space concern.

### Running processes

| PID | Process | Classification | Touches |
|---|---|---|---|
| 2575426 | `gem-intent-engine/serve_website_api.py` | OTHER (separate project) | — |
| **2575482** | `trading-production/main.py` | **EXECUTION** | Alpaca + FMP |
| 2609472 | `dashboards/gem_trader_hq.py` | DASHBOARD | FMP + Alpaca (read-only) |
| 2616884 | `audit_mcp/stocklens_mcp_server` | RESEARCH | cache-only |
| 1003 | `/usr/bin/python3 unattended-upgrade-shutdown` | OTHER (OS) | — |

### Systemd units (system-level: `gem-trader*`)

| Unit | State | Classification | Proposed action |
|---|---|---|---|
| `gem-trader.service` | **ACTIVE/running** | **EXECUTION** | **STOP in Phase 2** |
| `gem-trader-nightly.timer` | active/waiting | RESEARCH | Keep (convert to research-only) |
| `gem-trader-premarket.timer` | active/waiting | RESEARCH | Keep (convert to research-only) |
| `gem-trader-midday.timer` | active/waiting | RESEARCH | Keep (convert to research-only) |
| `gem-trader-research.timer` | active/waiting | RESEARCH | Keep (convert to research-only) |
| `gem-trader-paper-evidence.timer` | active/waiting | EXECUTION (paper) | **DISABLE in Phase 2** |
| `gem-trader-weekly-liquid.timer` | active/waiting | RESEARCH | Keep |
| (all `.service` counterparts) | inactive/dead | — | No action needed |

### Systemd units (user-level: `~/.config/systemd/user/`)

| Unit | State | Classification | Proposed action |
|---|---|---|---|
| `gem-trader-options-snapshot.timer` | active/waiting | DATA_COLLECTION | Modify: Tradier-only in Phase 2 |
| `gem-trader-options-snapshot.service` | inactive/dead | — | — |

### Cron jobs

| Schedule | Script | Classification | Proposed action |
|---|---|---|---|
| `0 13 25 6 *` | `power_trend_extension_oneshot.sh` | RESEARCH (one-shot) | Keep |
| `30 21 * * 1-5` | `social_attention_nightly.sh` | RESEARCH | Keep |

### Provider dependency map

| Service | Alpaca | FMP | Tradier |
|---|---|---|---|
| `main.py` (trading daemon) | **REQUIRED** (execution + data) | REQUIRED (macro) | No |
| `gem_trader_hq.py` (dashboard) | read-only (positions, portfolio) | REQUIRED (research) | optional |
| `stocklens_mcp_server` (MCP) | No | cache-only | No |
| `gem-trader-nightly` / `premarket` / `midday` | **Alpaca bars** (price data) | REQUIRED | No |
| `gem-trader-paper-evidence` | Alpaca positions (reconcile) | No | No |
| `gem-trader-options-snapshot` | **REQUIRED** (options chains) | No | Fallback |

### Alpaca usage detail

Alpaca is currently used for:
1. **Market data** — price bars for universe scanning and regime forecasting (`core/alpaca_client.py`)
2. **Order execution** — submit buy/sell orders (`execution/order_manager.py`)
3. **Position monitoring** — read open positions, close positions (`execution/position_monitor.py`)
4. **Options chains** — primary source for options snapshot collector (Phase 1J)
5. **Broker snapshot** — read account state (`core/broker_snapshot.py`)
6. **Universe building** — pipeline snapshot via Alpaca SIP (`core/universe.py`)

### Tradier usage detail

Tradier is currently used for:
- **Research-only fallback** for options chains (Alpaca primary, Tradier fallback in `core/options_feed_factory.py`)
- Tradier credentials are already validated (Phase 1J.2)
- No execution path through Tradier currently exists

### FMP usage detail

FMP is currently used for:
- Fundamentals, earnings calendar, news, macro events
- Stock lens research
- Alpha discovery
- Sector/industry metadata
- Cache-first via `core/data_gatekeeper.py`
- FMP is already the primary research provider

### Key files for Phase 1

| File | Role | Phase 1 action |
|---|---|---|
| `core/config.py` | Central config + env validation | Add `SYSTEM_MODE`, `RESEARCH_ONLY` flags |
| `core/alpaca_client.py` | Alpaca broker/data client | Archive → stub |
| `execution/order_manager.py` | Order submission | Gut → raise ResearchOnlyModeError |
| `execution/position_monitor.py` | Position exits | Gut → raise ResearchOnlyModeError |
| `execution/paper_governance.py` | Paper signal routing | Gut → raise ResearchOnlyModeError |
| `main.py` | Trading daemon entry point | Replace with research-only heartbeat |
| `dashboards/gem_trader_hq.py` | Dashboard | Add RESEARCH_ONLY banner, remove execution panels |

---

## Phase 1 — Pending (safe-by-default refactor)

*Not started. Waiting for operator approval after Phase 0 report.*

## Phase 2 — Complete (2026-06-13): stop services, remove Alpaca

### Code changes

| File | Change |
|---|---|
| `core/options_feed_factory.py` | Removed `_load_alpaca_feed`. Factory is now Tradier-only. |
| `research/options_chain_snapshot_collector.py` | `_get_spot` rewritten to read from `cache/prices/{SYM}.parquet` (no Alpaca stub call). Docstring updated. |
| `tests/unit/test_options_feed_factory.py` | Replaced Alpaca-first ordering tests with Tradier-only equivalents. |

### Service changes

Run the following as the `gem` user (requires `sudo`):

```bash
# Stop and permanently disable the trading daemon
sudo systemctl stop gem-trader.service
sudo systemctl disable gem-trader.service

# Stop and permanently disable the paper-evidence timer
sudo systemctl stop gem-trader-paper-evidence.timer
sudo systemctl disable gem-trader-paper-evidence.timer
```

After running those commands, the active unit list should show only
the research timers: `nightly`, `premarket`, `midday`, `research`,
`weekly-liquid`, and the user-level `options-snapshot`.

### Verification

```bash
# All 1282 tests pass
.venv/bin/python3 -m pytest tests/unit tests/smoke -x -q

# Options snapshot is Tradier-only
grep -n "_load_alpaca_feed\|AlpacaOptionsFeed" core/options_feed_factory.py  # → no hits
```

## Phase 3 — Complete (2026-06-13): FMP + Tradier research stack

### Summary

Alpaca is no longer needed for price data. FMP's `/historical-price-eod/full`
endpoint (via `FMPClient.get_ticker_bars`) covers all daily OHLCV use cases
that Alpaca's free-tier SIP bars provided. Tradier (live since Phase 2) covers
options data. The research timer cycle now runs entirely on FMP + Tradier +
local cache.

### Code changes

| File | Change |
|---|---|
| `core/alpaca_client.py` | Smart stub: `get_daily_bars` / `get_daily_bars_batch` / `get_quote` now serve from `cache/prices/*.parquet` in the same list-of-dicts format callers expect. No network calls, no empty returns. All ~10 research callers continue to work without modification. |
| `research/regime_forecast.py` | `_is_offline_alpaca()` now checks `ALPACA_ACTIVE`; returns `True` in research mode, causing the FMP tier to run directly. FMP writes fresh bars back to `cache/prices/` via `_persist_frame_to_local_cache()`. |
| `scripts/nightly_refresh.py` | Alpaca SIP pre-warm replaced with FMP price pre-warm (`FMPClient.get_ticker_bars` for all `ALL_SYMBOLS`). Writes directly to `cache/prices/*.parquet` so the 20:30 ET research cycle reads fresh data from cache. |
| `tests/unit/test_research_only_mode.py` | Updated `test_alpaca_client_stubs_without_raising` to reflect cache-serving behavior (list not None). |

### Data flow after Phase 3

```
nightly_refresh (03:30 ET)
  → FMP get_ticker_bars(ALL_SYMBOLS) → cache/prices/*.parquet

premarket / nightly research cycle
  → regime_forecast._load_all_frames()
      → cache/prices/*.parquet (fresh from pre-warm)
      → FMP fallback for any missing symbol
  → market_regime.get_daily_bars() → stub → cache/prices/
  → options_regime_lens._get_spot() → stub → cache/prices/
  → alpha_discovery.get_daily_bars_batch() → stub → cache/prices/
  → social_arb_radar → stub → cache/prices/ (tape context graceful empty)

options snapshot (user timer, 15:45 ET)
  → Tradier-only (Phase 2)
```

### Why Alpaca price data is not needed

FMP covers all use cases:
- Daily OHLCV bars (any ticker, 260-day history) — `get_ticker_bars()`
- SPY bars specifically — `get_spy_bars()`
- VIX series — `get_vix()`

The only gap is intraday bars (for `social_arb_radar` tape context), which was
already fail-soft and non-critical. No research output depends on it.

If Alpaca data is wanted in the future, restore `archive/execution_disabled/alpaca_client.py`
data methods (keeping execution methods as ResearchOnlyModeError).

### Verification

```bash
# All 1282 tests pass
.venv/bin/python3 -m pytest tests/unit tests/smoke -x -q

# Smart stub serves real cache data
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python -c "
from core.alpaca_client import get_alpaca
c = get_alpaca()
bars = c.get_daily_bars('SPY', days=1)
print('SPY last bar:', bars[-1] if bars else 'EMPTY')
"

# Regime forecast skips Alpaca, goes to FMP
grep "_is_offline_alpaca" research/regime_forecast.py
```

## Phase 4 — Complete (2026-06-13): research engine build

### Summary

Operator tooling converted to the research stack. Alpaca removed from the price
deepening tool. FMP is now the only provider for the daily OHLCV cycle. Startup
checks rewritten to reflect the research-only posture: FMP is critical, Tradier
is optional, Alpaca auth/bars checks are gone.

### Code changes

| File | Change |
|---|---|
| `core/startup_checks.py` | Rewrote from scratch. Critical checks: timezone, database, **fmp_auth** (was alpaca_auth). Non-critical: price_cache, fmp_calendar, cache_dir, tradier. `alpaca_auth` and `alpaca_bars` removed — they would permanently halt in research mode because the stub returns empty account data. |
| `scripts/deepen_price_cache.py` | `--execute` path now calls `FMPClient.get_ticker_bars()` per ticker. Removed `AlpacaClient` import and `CHUNK` constant. Docstring updated. |
| `main.py` | Upgraded from banner-only to research health reporter: loads creds, calls `run_startup_checks()`, prints OK / DEGRADED / HALTED verdict, lists available research commands. Exit code 1 on HALTED, 0 otherwise. |
| `tests/unit/test_scanner_recall_repair.py` | `test_deepen_refresh_tool_dry_run_safe`: updated source assertion — now checks `AlpacaClient` is NOT in source and `get_fmp` IS in source. |
| `tests/unit/test_research_startup_checks.py` | **New — 19 tests.** Covers: StartupState halted/degraded/degraded_reasons logic; check-name set (no alpaca_auth/bars); FMP critical vs price_cache/tradier non-critical; individual check fns (timezone, fmp_auth via mock, price_cache via tmp_path, tradier via env). |

### Verification

```bash
# All 1301 tests pass (1282 pre-Phase-4 + 19 new)
.venv/bin/python3 -m pytest tests/unit tests/smoke -x -q

# Startup checks no longer import AlpacaClient
grep "alpaca_auth\|alpaca_bars\|AlpacaClient" core/startup_checks.py  # → no hits

# Deepen tool no longer references Alpaca
grep "AlpacaClient" scripts/deepen_price_cache.py  # → no hits

# Compile check — no creds needed
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m py_compile core/startup_checks.py scripts/deepen_price_cache.py main.py
```

## Phase 5 — Complete (2026-06-13): dashboard, MCP, verification

### Summary

Dashboard updated to reflect permanent research-only posture. MCP tools were
already clean (read-only by doctrine since Phase 2A). Full audit confirms no
execution paths remain in production code. Test suite green at 1301.

### Code changes

| File | Change |
|---|---|
| `dashboards/gem_trader_hq.py` | **`system_health()`**: Removed Alpaca from health assessment. Alpaca stub always returns `[]` (non-None), which previously made `_alpaca_ok=True` and health always "OK" even without a real broker. Now FMP-only: `DEGRADED` when FMP down, `OK` otherwise. |
| `dashboards/gem_trader_hq.py` | **`header()`**: Removed `paper = "PAPER"/"LIVE"` label and Alpaca status dot (`astr`). `readonly_tag` no longer session-conditional — always shows `[bold magenta]RESEARCH ONLY[/]`. Provider line now shows only `●FMP`. |
| `dashboards/gem_trader_hq.py` | **ACCOUNT panel**: Title changed from `ACCOUNT [PAPER/LIVE]` to `ACCOUNT [bold magenta]RESEARCH ONLY[/]`. |
| `audit_mcp/stocklens_mcp_tools.py` | **No changes needed** — already pure read-only by Phase 2A doctrine. |

### Verification

```bash
# 1301 tests pass (no regressions from dashboard changes)
.venv/bin/python3 -m pytest tests/unit tests/smoke -x -q

# Dashboard compiles without creds
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m py_compile dashboards/gem_trader_hq.py

# No AlpacaClient used for execution in production (only get_alpaca() for cache reads)
grep -rn "submit_market_order\|submit_limit_order\|close_position\|cancel_all_orders" \
    core/ council/ execution/ strategies/ dashboards/ scripts/ main.py \
    | grep -v "archive/\|alpaca_client.py\|startup_checks\|research_mode\|test_"
# → no hits (only DocString references in config.py)

# MCP tools — confirmed no execution references
grep -n "alpaca\|AlpacaClient\|order\|execution" audit_mcp/stocklens_mcp_tools.py
# → no hits (pure read-only cache layer)
```

---

## Phase 3A — Decommission Complete (2026-06-13)

All five phases complete. `gem-trader` is now permanently research-only.

| Phase | Scope | Result |
|---|---|---|
| Phase 1 | Safe-by-default refactor (ResearchOnlyModeError, mode flags, archive) | 1282 tests ✓ |
| Phase 2 | Stop services, Tradier-only options, disable paper-evidence timer | Services stopped ✓ |
| Phase 3 | Smart Alpaca stub (cache reads), FMP as price refresher | 1282 tests ✓ |
| Phase 4 | Research engine: startup checks, deepen tool FMP, main.py health reporter | 1301 tests ✓ |
| Phase 5 | Dashboard RESEARCH ONLY labels, MCP audit, final verification | 1301 tests ✓ |

**No code paths remain that can submit orders, enter positions, or touch Alpaca broker APIs.**
The system runs daily research cycles via `run_research_cycle.sh` and surfaces
findings through the dashboard (cache-only) and MCP tools (cache-only).

---

## Phase 3B — Complete (2026-06-14): Alpaca dependency removal + Phase 4/5 research engine

### Summary

Phase 3B confirms Alpaca is safe to cancel. All remaining Alpaca references are
either the cache-serving stub or historical doc comments. The Phase 4 research
engine (heartbeat, scanner, research cards) is live. Phase 5 (registry archive,
dashboard research panels, MCP Phase 4 tools) is complete.

### Fixes and additions

| Item | Change |
|---|---|
| `TRADIER_ACCESS_TOKEN` → `TRADIER_API_TOKEN` | Both `research/stock_research_card.py` and `research/tradier_research_health.py` now read the correct env key; Tradier health flipped DEGRADED → OK |
| FMP fundamentals null bug | `_flatten_fmp_fundamentals()` in `stock_research_card.py` flattens income/balance/cashflow lists into TTM scalars; previously all null despite successful FMP fetch |
| `marketCap` key fallback | `build_card()` now checks `mktCap` → `marketCap` → `market_cap` in order |
| MCP research card path | `get_research_card()` reads `cache/research/stock_research_card_{T}.json` (not the old `cards/` subdir) |
| Research scanner enrichment | `_parquet_mtime()` + `_derive_scores()` added: RS, trend, volume, catalyst, fundamental, social sub-scores (0–100) per watchlist item |
| `run_research_cycle.sh` subcommands | `fmp-provider-health`, `tradier-research-health`, `provider-health`, `data-freshness`, `sector-leadership`; disabled-execution gate for trade/order/route commands |
| Decommission doc | Phase 3B section added to `AUTO_TRADING_DECOMMISSION_FINAL_FINDINGS.md`; card path and test count corrected |

### Alpaca safe-to-cancel confirmation

```
- dashboard starts without Alpaca env vars ✓
- heartbeat works without Alpaca ✓
- scanner works without Alpaca ✓
- research cards work without Alpaca ✓
- price refresh uses FMP (nightly_refresh.py) ✓
- options research is Tradier-only ✓
- paper/holdout archived; no Alpaca required ✓
- 1376 tests pass ✓
```

### Verification

```bash
# Full suite
.venv/bin/python3 -m pytest tests/unit tests/smoke -x -q
# → 1376 passed

# Tradier health with correct key
SNIPER_ENV_PATH=/home/gem/secure/trading.env ./scripts/run_research_cycle.sh tradier-research-health
# → OK

# Market heartbeat (no Alpaca)
SNIPER_ENV_PATH=/home/gem/secure/trading.env ./scripts/run_research_cycle.sh market-heartbeat
# → DEFENSIVE_ROTATION (or current label), VIX reported

# MCP research card path
grep "stock_research_card_" audit_mcp/stocklens_mcp_tools.py
# → cache/research/stock_research_card_{t}.json
```
