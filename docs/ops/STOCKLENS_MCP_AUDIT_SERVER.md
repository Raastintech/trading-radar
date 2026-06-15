# Stock Lens MCP Audit Server V1

**Phase 2A · 2026-05-16 · doctrine: read-only audit**

The Stock Lens MCP server lets Claude **audit** the trading system's
cached research artifacts. Claude is the **auditor**, not the trader.
The server exposes only read-only views of artifacts that the daemon
and the nightly/premarket research timers have already written.

The server lives in `audit_mcp/` (named `audit_mcp` to avoid shadowing
the upstream `mcp` Python SDK on `sys.path`):

```
audit_mcp/
├── __init__.py
├── stocklens_mcp_server.py   # MCP stdio wrapper (thin)
└── stocklens_mcp_tools.py    # pure-Python tool layer (no MCP deps)
```

---

## Purpose

- Provide Claude with a structured, read-only audit interface to the
  Stock Lens research system.
- Surface artifact-level **contradictions** (Stock Lens vs Entry
  Validator vs Alpha vs Gatekeeper vs Market Forecast).
- Surface **late-chase risk** (extension, hot RSI, options-quality
  warnings, Gatekeeper BLOCK/WATCH).
- Surface **dashboard inconsistencies** (stale forecast, fragile
  regime, hygiene gate verdicts, freshness warnings).
- Surface **halt state** safely (read-only) so Claude can recommend
  whether operator review is needed without ever clearing a halt.

Claude is the auditor. The server cannot trade.

---

## What it can do

- Read cached JSON artifacts under `cache/research/`, `cache/state/`,
  `data/state/`, and `docs/scorecards/`.
- Read `db/trading.db` via SQLite's URI read-only mode
  (`file:db/trading.db?mode=ro`) for the `circuit_breaker_state` row.
- Cross-reference artifacts to flag contradictions, stale data,
  missing sidecars, and late-chase candidates.
- Return structured, JSON-serialisable verdicts. Missing artifacts
  produce a `{"status": "missing_artifact", "path": ..., "message": ...}`
  block — never a crash, never a fabricated value.

## What it cannot do

- ❌ Submit, modify, or cancel orders (Alpaca, Tradier, anything).
- ❌ Close, flatten, or hedge positions.
- ❌ Mutate the database (`INSERT/UPDATE/DELETE/ALTER/CREATE/DROP/
  REPLACE/TRUNCATE` are pattern-banned by `tests/unit/
  test_stocklens_mcp_server.py`).
- ❌ Call FMP, Alpaca, Tradier, or any provider HTTP API directly.
- ❌ Edit strategies, scoring, governance, execution, or paper-evidence
  resolvers.
- ❌ Clear, set, or modify the circuit breaker.
- ❌ Enable or disable live capital. The triple-gate
  (`PAPER_TRADING=false`, `ALPACA_PAPER=false`,
  `ALLOW_LIVE_CAPITAL=true`) remains untouched.
- ❌ Read, edit, or expose credentials. The server should run with
  `GEM_TRADER_SKIP_DOTENV=true`; provider env vars are not required.

These restrictions are pinned by unit tests
(`test_source_has_no_forbidden_identifiers`,
`test_source_has_no_mutating_sql`,
`test_source_opens_sqlite_in_readonly_mode`,
`test_source_does_not_import_order_modules`,
`test_open_readonly_db_rejects_writes`).

---

## Tools

| Tool | Reads | Notes |
|------|-------|-------|
| `get_market_forecast` | `cache/research/regime_forecast_latest.json` | Returns regime probabilities, breadth, vol, anchor freshness. |
| `get_alpha_discovery(top_n=25)` | `cache/research/alpha_discovery_board_latest.json` + overlay sidecar if present | Items capped + sorted by alpha_score. |
| `get_stock_lens(ticker)` | `cache/research/stock_lens_<TICKER>_latest.json` | Full lens payload. |
| `get_executive_gatekeeper(ticker)` | `cache/research/executive_gatekeeper_<TICKER>_latest.json` | Verdict + reasons. |
| `get_research_delta` | `cache/research/research_delta_latest.json` | Cross-cycle deltas. |
| `get_risk_telemetry` | slippage / concentration / shadow-sizing / hygiene sidecars | Compact bundle (large arrays stripped). |
| `get_paper_hygiene` | `paper_state_hygiene_latest.json` + `data/state/paper_legacy_quarantine.json` | Findings + quarantine list. |
| `get_broker_snapshot` | `cache/state/broker_positions_snapshot.json` | **Cached** — does not call Alpaca. |
| `get_evidence_rigor` | `docs/scorecards/evidence_rigor_report.json` + sleeve scorecards | Plus active-sleeve `.md` scorecards if present. |
| `get_holdout_status` | `docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md` + holdout scoreboard | Doc + scoreboard. |
| `audit_ticker_consistency(ticker)` | Lens + Gatekeeper + Alpha + Forecast | Returns `contradictions[]` + `verdict`. |
| `audit_dashboard_consistency` | Forecast + Alpha + Delta + Hygiene | Forecast freshness, posture flips, gate verdicts. |
| `audit_late_chase_candidates(top_n=25)` | Alpha board + per-ticker Lens + Gatekeeper | Flags extension / late-chase / blocked. |
| `audit_halt_state` | `db/trading.db` (read-only) + cached snapshot + hygiene summary | **Never clears the halt.** |

### Missing-artifact contract

Every tool that depends on a single artifact will return:

```json
{
  "status": "missing_artifact",
  "path": "cache/research/stock_lens_FOO_latest.json",
  "message": "Artifact not found. Run the relevant research cycle."
}
```

Aggregate tools (`get_paper_hygiene`, `audit_halt_state`,
`get_risk_telemetry`, `get_holdout_status`) will degrade per-block:
they return `status: ok` at the top level with per-block
`status: missing_artifact` for any absent sub-artifact.

---

## Claude Desktop config

Add the server to your Claude Desktop `claude_desktop_config.json`.
Use **only** the local audit server below — do **not** add direct
FMP / Alpaca / Tradier MCP servers; those would re-introduce
execution and provider-call surface area that this audit layer
deliberately avoids.

```json
{
  "mcpServers": {
    "stocklens-audit": {
      "command": "/home/gem/trading-production/.venv/bin/python",
      "args": [
        "-m",
        "audit_mcp.stocklens_mcp_server"
      ],
      "env": {
        "STOCKLENS_ROOT": "/home/gem/trading-production",
        "GEM_TRADER_SKIP_DOTENV": "true",
        "PYTHONPATH": "/home/gem/trading-production"
      }
    }
  }
}
```

`STOCKLENS_ROOT` is the repo root the server reads artifacts from
(defaults to `/home/gem/trading-production` if unset).
`GEM_TRADER_SKIP_DOTENV=true` ensures the server never touches the
trading `.env`. `PYTHONPATH` lets `python -m audit_mcp.stocklens_mcp_server`
resolve the package without an explicit install.

### Verifying the wiring

After launching Claude Desktop:

1. Open a new chat.
2. Type "List the tools exposed by the stocklens-audit server."
3. Claude should report 14 tools (the table above).
4. If Claude reports zero tools or a connection error, check the
   command path, `PYTHONPATH`, and that `mcp` is installed
   (`/home/gem/trading-production/.venv/bin/python -c "import mcp"`).

---

## Example audit prompts

Ask Claude things like:

- "Audit AAPL consistency."
- "Find late-chase Alpha candidates."
- "Review paper hygiene and risk telemetry."
- "Is the current dashboard internally conflicted?"
- "Read the holdout scoreboard and tell me how many days remain."
- "Show me the latest broker snapshot and the halt state — is operator
  review required?"
- "Compare the Stock Lens for NVDA against its Executive Gatekeeper
  verdict and the Alpha board entry — surface contradictions."

Claude will use the tools above and return narrative summaries that
cite the underlying artifact `path` + `_age_hours` so you can verify.

---

## Safety guarantees (pinned by tests)

| Guarantee | Test |
|-----------|------|
| No order / close / cancel calls in source | `test_source_has_no_forbidden_identifiers` |
| No mutating SQL strings in source | `test_source_has_no_mutating_sql` |
| Every `sqlite3.connect` uses `mode=ro` URI | `test_source_opens_sqlite_in_readonly_mode` |
| No imports from `execution.order_manager`, `execution.paper_governance`, `core.alpaca_client.AlpacaClient`, or `core.data_gatekeeper` | `test_source_does_not_import_order_modules` |
| SQLite handle rejects `UPDATE`/`INSERT`/`DELETE` against `circuit_breaker_state` | `test_open_readonly_db_rejects_writes` |
| Server runs without provider credentials | `test_initialization_does_not_touch_provider_credentials` |
| Every tool tolerates missing artifacts | `test_tool_degrades_gracefully_when_artifacts_missing` (parameterised) |

Run `.venv/bin/python -m pytest tests/unit/test_stocklens_mcp_server.py -v`
to re-pin these on demand.

---

## Operating principles preserved

- **Cache-only.** The MCP server never invokes
  `run_research_cycle.sh`, never starts the daemon, never calls
  providers. Stale artifacts produce stale verdicts; the operator's job
  is to re-run the appropriate cycle. The server tells you the
  `_age_hours` so you know.
- **Diagnostic only.** Outputs are diagnostic, not enforcement.
  Nothing here promotes a sleeve, clears a halt, or changes a gate.
- **Additive only.** This phase does not modify any existing artifact
  shape. If a future phase changes an artifact, update
  `audit_mcp/stocklens_mcp_tools.py` and the corresponding test.
- **Live trading remains gated.** The triple-key live-capital gate in
  `core/config.py` and `AlpacaClient.submit_*_order` is untouched.

---

## Maintenance

Add a new tool by:

1. Implementing a `def tool_name(...) -> Dict[str, Any]:` function in
   `audit_mcp/stocklens_mcp_tools.py`. Return a dict with a `status`
   field. Never raise on missing artifacts.
2. Registering it in the `TOOLS` dict at the bottom of that file with
   `fn`, `description`, and `args_schema`.
3. Parametrise it into the safety + missing-artifact tests in
   `tests/unit/test_stocklens_mcp_server.py`.

The MCP wrapper (`stocklens_mcp_server.py`) auto-exposes everything in
`TOOLS` — no edits to the wrapper needed.
