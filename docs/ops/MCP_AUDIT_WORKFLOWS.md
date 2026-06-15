# MCP Audit Workflows (Phase 2B)

Read-only audit workflows that bundle the Stock Lens MCP audit tools
(Phase 2A) into repeatable daily reports.

**Doctrine:** Claude is the auditor, not the trader. These workflows
**must never** propose trades, mutate the DB, call providers, modify
governance, or enable live trading. They compose the existing read-only
helpers in `audit_mcp/stocklens_mcp_tools.py` and write **new** sidecars
to `cache/research/` and `logs/` under the `mcp_audit_*` namespace.

## What's in the box

| Workflow | Composes | Sidecar slug |
|---|---|---|
| `daily_dashboard_audit` | `get_market_forecast`, `audit_dashboard_consistency`, `audit_halt_state`, `get_paper_hygiene`, `get_risk_telemetry` | `mcp_audit_daily_latest.{json,txt}` |
| `late_chase_audit` | `audit_late_chase_candidates` (+ bucketing into extended / watch / blocked / broken / missing-lens) | `mcp_audit_late_chase_latest.{json,txt}` |
| `system_health_audit` | `audit_halt_state`, `get_paper_hygiene`, `get_risk_telemetry`, `get_broker_snapshot` (+ derived `safe_for_paper_observation` verdict) | `mcp_audit_system_health_latest.{json,txt}` |
| `ticker_consistency_audit` | `audit_ticker_consistency(TICKER)`, `get_stock_lens(TICKER)`, `get_executive_gatekeeper(TICKER)` | `mcp_audit_<TICKER>_latest.{json,txt}` |

All outputs are atomically written (tmp file + rename). The JSON is the
machine-readable contract; the `.txt` is a compact operator-readable
rendering of the same data.

## Commands

```bash
# Daily bundle (cache-only; ~1s).
./scripts/run_research_cycle.sh mcp-audit

# Ticker consistency audit for one or more tickers.
./scripts/run_research_cycle.sh mcp-audit-ticker SPY MDB

# Phase 2B.1 — orchestrated session audit (default cadence).
./scripts/run_research_cycle.sh mcp-audit-session regular

# Phase 2B.3 — opt-in: refresh Executive Gatekeeper artifacts BEFORE
# the audit so per-ticker verdicts are fresh.  Cache-first; the only
# provider touch is the FMP earnings calendar (cached 6 h).
./scripts/run_research_cycle.sh mcp-audit-session regular --refresh-gatekeeper

# Direct python entry points (same behavior; useful in tests / CI).
.venv/bin/python research/mcp_audit_workflows.py daily        [--print]
.venv/bin/python research/mcp_audit_workflows.py late-chase   [--top-n N] [--print]
.venv/bin/python research/mcp_audit_workflows.py system-health [--print]
.venv/bin/python research/mcp_audit_workflows.py ticker TICKER [TICKER...] [--print]
.venv/bin/python research/mcp_audit_workflows.py all          [--print]
```

The runner does not import `core.config` (cache-only), so no
`SNIPER_ENV_PATH` is required.

## Outputs

```
cache/research/mcp_audit_daily_latest.json
cache/research/mcp_audit_late_chase_latest.json
cache/research/mcp_audit_system_health_latest.json
cache/research/mcp_audit_<TICKER>_latest.json
logs/mcp_audit_daily_latest.txt
logs/mcp_audit_late_chase_latest.txt
logs/mcp_audit_system_health_latest.txt
logs/mcp_audit_<TICKER>_latest.txt
```

All sidecars carry a `generated_at` ISO timestamp and a `workflow`
identifier so downstream readers can verify provenance.

### `system_health_audit` derived verdict

`payload["verdict"]` is a small dictionary of booleans:

```jsonc
{
  "halted": false,
  "ready_to_gate_clean": true,
  "ready_to_gate_all": false,
  "active_drift": false,
  "unknown_drift": false,
  "safe_for_paper_observation": true
}
```

`safe_for_paper_observation` is **a published audit verdict, not a
gate.** It is true iff the system is not halted, has no active or
unknown reconciler drift, and the clean-epoch hygiene gate is ready.
A `false` here means an operator should investigate before relying on
risk-telemetry numbers for paper sleeve promotion decisions.

## Example: daily run

```text
$ ./scripts/run_research_cycle.sh mcp-audit
[2026-05-16 19:30:00] [CACHE] Phase 2B MCP audit workflows — daily + late_chase + system_health
[2026-05-16 19:30:00] ▶ mcp audit: daily_dashboard
wrote cache/research/mcp_audit_daily_latest.json
wrote logs/mcp_audit_daily_latest.txt
[2026-05-16 19:30:01] ▶ mcp audit: late_chase
wrote cache/research/mcp_audit_late_chase_latest.json
wrote logs/mcp_audit_late_chase_latest.txt
[2026-05-16 19:30:02] ▶ mcp audit: system_health
wrote cache/research/mcp_audit_system_health_latest.json
wrote logs/mcp_audit_system_health_latest.txt
```

## Phase 2B.3 — Gatekeeper refresh cadence

The Executive Gatekeeper artifacts are refreshed on the following
cadence to keep the dashboard's per-ticker panels and the MCP audit
sidecar reading against fresh verdicts:

| Trigger | When | Source | Cap |
|---|---|---|---|
| `premarket` cycle | 08:00 ET Mon-Fri | nightly board → premarket overlay → delta → **gatekeeper-refresh** | 25 |
| `nightly` cycle | 20:30 ET Mon-Fri | forecast → alpha → social → delta → lenses-nightly → **gatekeeper-refresh** → resolve → risk-telemetry | 25 |
| `mcp-audit-session --refresh-gatekeeper` | operator opt-in | **gatekeeper-refresh** → orchestrator | 25 |
| `gatekeeper-refresh` (ad-hoc) | operator | full plan | 25 (`--max N` override) |

Ticker selection priority (lowest number first; ties broken by ticker):

| Priority | Source |
|---|---|
| 10 | Open positions (DB `decisions.position_opened=1 AND position_closed=0`) |
| 15 | Earnings today |
| 20 | Earnings tomorrow |
| 25 | Explicit `--watch TICKER…` |
| 30 | Earnings within next 5 days |
| 35 | Missing Gatekeeper artifact (Stock Lens already cached) |
| 40 | Stale Gatekeeper artifact (Stock Lens already cached) |
| 50 | Top Alpha Discovery candidates (A-tier first, by `alpha_score`) |

The CLI logs `selection by source:` so an operator can see why each
ticker is in the plan and whether the cap is biting against an
unexpected source.

### Freshness-first operator order

```
1. forecast / alpha / lens refresh       (provider)
2. gatekeeper-refresh                    (cache-first; FMP earnings cal only)
3. risk-telemetry                        (cache-only)
4. mcp-audit-session                     (cache-only)
5. dashboard review                      (cache-only; never auto-runs anything)
```

`premarket` and `nightly` bake in steps 1-3 (and nightly also runs 3
via `cmd_risk_telemetry`).  Step 4 is operator-triggered.

## Safe-use rules

The workflow runner is **read-only by construction**. The test suite
asserts the following invariants and breaks the build if any is
violated:

1. No imports from `alpaca`, `fmp`, `yfinance`, `execution.*`,
   `council.*`, `strategies.*`, `order_manager`, `paper_governance`,
   or `decision_logger`.
2. No `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`, or `ALTER`
   statements anywhere in the source.
3. The DB (`db/trading.db`) hash does not change after running the
   workflows (hashed before/after in tests).
4. Sidecar names always start with `mcp_audit_` — no existing core
   research artifact is ever overwritten.

In addition, the underlying helpers (`audit_mcp/stocklens_mcp_tools.py`)
open the DB in URI read-only mode and never mutate any file.

## Still no trading

These workflows do not:

- submit, cancel, or modify orders;
- write to `decisions`, `paper_signals`, `paper_signal_outcomes`,
  `voyager_paper_signals`, `veto_log`, `circuit_breaker_state`, or
  any other DB table;
- run scoring, governance, or strategy code;
- enable or disable the live-capital gate.

The live-capital gate (`core/config.py`) is unchanged by Phase 2B. The
operator's existing `ALLOW_LIVE_CAPITAL` setting is the only thing that
governs live execution; this module never touches it.

## How Claude Code should use the outputs

When the operator asks for an audit, Claude Code should:

1. Prefer the JSON sidecars under `cache/research/mcp_audit_*` over
   re-running the underlying tools — the sidecars carry the same data
   and are cheaper to read.
2. Quote `verdict` / `safe_for_paper_observation` exactly when
   reporting system state; do not paraphrase to imply a gate that does
   not exist.
3. If a sidecar is missing or older than the operator's expectation,
   rerun `./scripts/run_research_cycle.sh mcp-audit` (or the matching
   subcommand) rather than calling MCP tools individually — the
   sidecars are the auditable contract.
4. Never propose orders, position changes, or governance edits based
   on these outputs. The workflows are *diagnostic*: their job is to
   surface conflicts, not to act on them.
