# MCP_AUDIT_SERVER_PLAN

**Project name:** Stock Lens MCP Audit Server V1
**Phase:** 2A (next build phase after Phase 1G Stability Window closes)
**Status:** **planned, not yet built**. Nothing in this document has shipped.

> This is a planning document. No MCP server, no MCP tools, and no
> Claude-facing endpoints exist yet in the repo. The doctrine here is the
> contract any future build must honor.

---

## Purpose

Give Claude (and other auditors via the same MCP server) a way to
**inspect the Stock Lens system and find flaws, contradictions, stale
artifacts, and misleading research conclusions** — without giving it the
ability to trade, mutate the DB, or talk to a broker.

The success metric is not "Claude generates more trades." It is "Claude
catches more flaws in the research artifacts than a fast operator
scrolling the dashboard."

---

## Allowed (V1)

The Stock Lens MCP Audit Server V1 may:

- **Read cached artifacts** under `cache/research/*` and `cache/state/*`
  (forecast, lens, alpha boards, executive gatekeeper, social arb,
  forward tracking summaries, risk telemetry, hygiene reports, broker
  snapshot).
- **Read repository docs** under `docs/` and the project root
  (`CLAUDE.md`, `PROJECT_INDEX.md`, etc.).
- **Read the DB in read-only mode** if needed. The connection must open
  with `?mode=ro` and use `PRAGMA query_only=ON`. Allowed tables for V1:
  `decisions`, `veto_log`, `paper_signals`, `paper_signal_outcomes`,
  `voyager_paper_signals`, `macro_events`, `cache_meta`.
- **Audit contradictions** across artifacts (see "Planned MCP tools").
- **Summarize flaws** in a structured form the operator can act on.
- **Suggest research improvements** as plain-text recommendations.

That is the entire allowed surface. Anything not on this list defaults to
forbidden.

---

## Forbidden (V1)

The audit server **must not**:

- **Execute trades.** No paper, no live.
- **Call Alpaca order endpoints.** No `submit_order`, `cancel_order`,
  `close_position`, `cancel_orders`. Not even via a wrapper.
- **Call Tradier execution endpoints.** Same rule.
- **Mutate the DB.** No `INSERT`, `UPDATE`, `DELETE`, no schema changes,
  no triggers. Read-only mode is enforced at the connection level, not
  by convention.
- **Enable live trading.** No write to `PAPER_TRADING`, `ALPACA_PAPER`,
  `ALLOW_LIVE_CAPITAL`, `LIVE_CONFIRM_FILE`, or any equivalent.
- **Edit strategy files.** No write access to `strategies/`,
  `execution/`, `core/strategy_registry.py`, council logic, scanner
  thresholds, scoring logic.
- **Expose secrets.** No `.env` read. No log line that includes an API
  key. The server process runs without `SNIPER_ENV_PATH` and without
  `core.config` import.
- **Directly call FMP / Alpaca / Tradier.** Provider calls happen only
  in the timer-driven research workflow
  (`./scripts/run_research_cycle.sh`) and the trading daemon. The MCP
  server reads cached output of those processes — never the wire.

A future phase may, with explicit operator approval, expand the allowed
surface. The default answer to every "can MCP also do X" question is
**no, surface this as a future phase request**.

---

## Planned MCP tools (V1 surface)

Each tool is **read-only** and returns a JSON payload assembled from the
cached artifacts described in `PROJECT_INDEX.md §E`.

### Artifact accessors

| Tool | Input | Returns |
|---|---|---|
| `get_market_forecast` | — | latest `regime_forecast_latest.json` |
| `get_alpha_discovery` | — | nightly board + premarket overlay |
| `get_stock_lens(ticker)` | `ticker: str` | latest per-ticker lens |
| `get_executive_gatekeeper(ticker)` | `ticker: str` | latest per-ticker gatekeeper |
| `get_research_delta` | — | latest `research_delta_latest.json` |
| `get_risk_telemetry` | — | slippage + concentration + shadow sizing sidecars |
| `get_paper_hygiene` | — | latest `paper_state_hygiene_latest.json` |
| `get_broker_snapshot` | — | `broker_positions_snapshot.json` |
| `get_evidence_rigor` | — | `docs/scorecards/evidence_rigor_report.json` |

### Audit tools

| Tool | Input | Returns |
|---|---|---|
| `audit_ticker_consistency(ticker)` | `ticker: str` | Cross-layer contradictions for a single ticker: lens label vs entry validator, posture vs forecast, options bullish-confirming vs lens bearish, alpha presence vs lens conclusion. Output: list of flagged contradictions with a short reason and a pointer to the source artifact. |
| `audit_dashboard_consistency()` | — | System-wide contradictions: research-aligned candidates against a breached forecast, REGIME CONFLICT cases, Phase 1F+ fragility verdicts, stale notes vs current lens. |
| `audit_late_chase_candidates()` | — | Tickers that look "ready" in one layer but show Entry Validator = Too Extended / Broken / Avoid in the lens, or 5d/10d return above an extension threshold, or alpha-tier "Late." |

Every audit tool returns a list of finding records of the shape:

```json
{
  "ticker": "MSFT",
  "code": "ENTRY_NOT_ACTIONABLE_VS_RESEARCH_ALIGNED",
  "severity": "WARN",
  "reason": "Research Assist labelled MSFT 'Research-aligned candidate' but Stock Lens entry layer is 'Broken / Avoid'.",
  "artifact_refs": [
    "cache/research/stock_lens_MSFT_latest.json",
    "cache/research/regime_forecast_latest.json"
  ]
}
```

Findings are diagnostic. They do not gate anything in the runtime.

---

## Doctrine

**MCP V1 is audit-only. Claude is auditor, not trader.**

If a future paper-execution path is ever built (Phase 2D in the roadmap),
it must:

1. Route through the existing `OrderManager` (`execution/order_manager.py`).
2. Pass the Submission Gate (`core/submission_gate.py`).
3. Pass the Circuit Breaker (`execution/circuit_breakers.py`).
4. Pass the Portfolio Risk check (`execution/portfolio_risk.py`).
5. Be visible to the Reconciler (`execution/position_reconciler.py`) on
   the next cycle.
6. Capture fill telemetry (Phase 0 fill-correctness contract).

The MCP server is **never** allowed to bypass any of those layers.
"Direct broker MCP" is not a future phase — it is permanently forbidden.

---

## Verification plan (when Phase 2A actually ships)

The future build must satisfy:

- A read-only DB connection helper that opens `?mode=ro&immutable=1`
  where possible, otherwise enforces `PRAGMA query_only=ON` immediately
  after open. A unit test must assert that an attempted `INSERT` raises.
- A unit test for every tool that asserts the returned payload references
  only files under `cache/`, `data/state/`, or `docs/` — never `db/`
  write paths, never `secure/`.
- A unit test that asserts the MCP process refuses to import
  `core.alpaca_client` and `core.fmp_client` at startup.
- A smoke test that loads each artifact accessor and confirms a missing
  cache file produces a graceful `{"_missing": true}` rather than an
  exception.
- A doctrine note added to `docs/ROADMAP_PHASES.md` flipping Phase 2A to
  ✅ complete only after these tests pass.

Until those tests exist, Phase 2A is **not** complete, and no Phase 2B
work begins.
