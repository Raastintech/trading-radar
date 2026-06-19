# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **⚠ RESEARCH-ONLY MODE — PERMANENT (as of 2026-06-13)**
>
> All auto-trading, paper-trading, and broker execution paths are **permanently decommissioned**.
> SNIPER, VOYAGER, SHORT, REMORA, CONTRARIAN — all trading sleeves are shut down.
> No paper signals. No order routing. No capital promotion path.
>
> **This system is now a pure research intelligence engine**: daily market heartbeat,
> six-category research scanner, per-ticker research cards, and read-only FMP + Tradier data.
>
> **For any future session or audit:** ignore all references to trading, paper signals,
> order management, paper governance, or strategy promotion — those paths are dead code.
> Work only on the research engine, scanner recall, candidate discovery, and forward evidence.
>
> Full decommission record: `docs/research/AUTO_TRADING_DECOMMISSION_FINAL_FINDINGS.md`

For current project truth, read **in this order** (stop at the first contradiction):
1. `docs/ROADMAP_PHASES.md` — **read first**: current operating mode, phase completion state.
2. `docs/PROJECT_INDEX.md` — subsystems, commands, artifacts, current truth.
3. `docs/ops/CLAUDE_BUILD_PLAYBOOK.md` — session rules, protected areas, refusals.

> `docs/strategy/CURRENT_READINESS.md` describes the pre-decommission trading phase.
> It is **historical only** — do not treat it as current operational truth.

## Repository purpose

`gem-trader` is a **research-only stock intelligence engine**. It runs a daily research cycle via systemd timers, producing a six-category research watchlist, per-ticker research cards, forward-evidence tracking, and a nightly operator summary. The goal is to surface the best candidate stocks for human review — not to trade them automatically.

**Alpaca is not required and not used for execution.** FMP is the primary data provider (fundamentals, events, macro, price history). Tradier provides options chain data for research. The daemon (`gem-trader.service`) runs the research heartbeat only.

## Credentials and environment

Credentials live OUTSIDE the repo at `/home/gem/secure/trading.env`. **Never** read, edit, or echo `.env`, `secure/trading.env`, or anything resembling a credentials file — tell the user what to change instead.

Two ways to invoke Python tools in this repo:

- With creds (provider calls, scanners, dashboard, paper jobs):
  `SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python <script>`
- Without creds (compile checks, offline tests, cache-only tooling):
  `GEM_TRADER_SKIP_DOTENV=true .venv/bin/python <script>`

`core/config.py` raises `RuntimeError` on missing required env vars at import time, so any module importing it will fail without one of the two paths above. `tests/conftest.py` injects stub creds for the test suite.

## Common commands

```bash
# Tests (canonical)
.venv/bin/python3 -m pytest tests/unit -x -q
.venv/bin/python3 -m pytest tests/smoke tests/unit -x -q
.venv/bin/python3 -m pytest tests/unit/test_submission_gate.py -v   # single file
.venv/bin/python3 -m pytest tests/unit/test_submission_gate.py::test_name -v   # single test

# Compile checks (no creds needed)
GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m py_compile <file.py>

# Daemon control (systemd is canonical; scripts wrap it)
./scripts/start_trader.sh
./scripts/stop_trader.sh
./scripts/restart_trader.sh
./scripts/check_status.sh           # session state, daemon PID, heartbeat, paper-loop health
sudo systemctl {start,stop,restart,status} gem-trader
sudo journalctl -u gem-trader -f    # live logs

# Dashboard (read-only operator UI)
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python dashboards/gem_trader_hq.py
# Optional flags (no one-shot/headless mode — it is a live full-screen TUI):
#   --ticker TICKER   pre-load a ticker for analysis
#   --mode N          start on panel mode 1-4
#   --refresh N       refresh interval in seconds (default 5)

# Smoke checklist (run before any session change)
# Each step is documented as a copy-pasteable one-liner in:
docs/smoke_test_checklist.md

# Research / paper-evidence cycles (provider calls — only place they happen for the daily cycle)
./scripts/run_research_cycle.sh nightly        # forecast + alpha + delta + lenses + resolver + holdout
./scripts/run_research_cycle.sh premarket      # lite cycle: forecast + alpha + delta
./scripts/run_research_cycle.sh resolve        # cache-only: resolve forward outcomes
./scripts/run_research_cycle.sh risk-telemetry # cache-only: slippage + concentration + shadow sizing
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python scripts/run_paper_evidence.py

# DB backup (uses SQLite online .backup API — WAL-safe)
./scripts/backup_db.sh
```

## Architecture

### Runtime layout (research-only)

`main.py` is the entry point for the research heartbeat daemon. It no longer runs any trading
loop — it boots the research-mode health reporter and writes `logs/trader_heartbeat.json`.

The actual daily research work happens via systemd timers calling `scripts/run_research_cycle.sh`:
1. **Nightly (20:30 ET):** forecast + alpha radar + delta + lenses + gatekeeper refresh + risk-telemetry + nightly operator summary + targeted backfill dry-run
2. **Premarket (08:00 ET):** forecast + alpha + delta
3. **Midday (12:30 ET):** resolve + reports + delta + risk-telemetry (cache-only)
4. **Paper-evidence timer (18:15 ET):** decommissioned — timer disabled
5. **Options collector (15:45 ET):** daily point-in-time chain snapshot

There is no scan→veto→allocate→execute loop. No `OrderManager`, no `VetoCouncil`, no
`paper_governance`. Those modules exist as archived code only.

### Module separation (doctrine)

- **Production:** `core/`, `council/`, `execution/`, `strategies/`, `dashboards/`. Must be import-safe and deterministic. **Production code must not import from `legacy/`.**
- **Research:** `research/`. May experiment freely but **cannot** be invoked from `main.py` or any production strategy module. Backtesters live in `research/backtests/` and `research/sleeves/`. Paper-evidence resolvers live in `research/paper_trades/`.
- **Legacy:** `legacy/` is migrated/archived only — not part of the runtime.

### Sleeve registry — DECOMMISSIONED

All trading sleeves (`SNIPER`, `VOYAGER`, `SHORT_A`, `REMORA`, `CONTRARIAN`, `SHORT_B`,
`PATHFINDER`) are **permanently decommissioned** as of Phase 3A (2026-06-13). They emit
no signals, generate no paper evidence, and have no promotion path. `core/strategy_registry.py`
is preserved for historical ledger helpers only — do not treat it as an operational signal
source. The code in `strategies/` is dead code; do not modify it for research purposes.

### Doctrine source ordering

When operating-truth questions arise, read in this order (per `docs/INDEX.md` and `docs/strategy/CURRENT_DOCTRINE_MAP.md`):

1. `docs/strategy/CURRENT_READINESS.md` — current platform phase, active sleeves, recent phase notes
2. `docs/strategy/STRATEGY_DOCTRINE.md` — permanent quant research doctrine
3. Active sleeve specs / scorecards in `docs/strategy/` and `docs/scorecards/`

`Trading-System-Master-Spec-V2.md` and `SNIPER_TRADING_AI_MASTER_DOC.md` at the repo root are **historical / archived** — the latter has an explicit "OBSOLETE — DO NOT USE" banner. Do not let either override the docs above.

### Data and provider policy

Only `Alpaca` (market data + execution) and `FMP` (fundamentals/events/macro/VIX/news) are in the primary execution path. `yfinance` is **debug-only fallback** — never primary. `core/data_gatekeeper.py` is a SQLite+Parquet cache layer that fronts every FMP call; FMP budget is tracked monthly via `fmp_budget_monthly`. The cache is shared by the daemon, the dashboard, and research scripts.

The dashboard is **cache-only** — it never calls providers and never invokes `run_research_cycle.sh`. Provider calls for the daily cycle happen only in:
- the daemon (`main.py`),
- `scripts/run_research_cycle.sh` (nightly + premarket timers),
- `scripts/run_paper_evidence.py` (paper-evidence timer),
- `scripts/nightly_refresh.py` (cache pre-warm + cleanup).

### Live-capital gate

`core/config.py` requires THREE independent env keys to all be set before any live order can be submitted:

```
PAPER_TRADING=false
ALPACA_PAPER=false
ALLOW_LIVE_CAPITAL=true
```

Optionally a `LIVE_CONFIRM_FILE` path must also exist on disk. The check lives inside `AlpacaClient.submit_*_order` and `close_position`, so any future caller (engine, repl, script) inherits the gate. **Until the holdout closes (2026-12-01), the expectation is `ALLOW_LIVE_CAPITAL=false`.**

### Database

- `db/trading.db` — canonical operational DB (decisions, veto_log, trades, paper_signals, macro_events). WAL mode.
- `db/trading_performance.db` — legacy, read-only reference.
- Schema reference: `db/schema.sql`. The `DecisionLogger` also creates tables via `CREATE TABLE IF NOT EXISTS`, so the schema file is for manual migration only.
- All decision writes must go through `core.decision_logger.DecisionLogger`.
- Migrations are additive only (e.g. Phase 12B added `aux_h3 TEXT` to `paper_signals` while preserving 1137 existing rows).

### systemd unit map

| Unit | Schedule | Purpose |
|------|----------|---------|
| `gem-trader.service` | always-on | Core trading loop (`main.py`) |
| `gem-trader-nightly.timer` | 03:30 ET Mon-Fri | Cache cleanup + pre-warm (`scripts/nightly_refresh.py`); now also refreshes the regime-forecast parquet universe via Alpaca SIP so the premarket / nightly forecast anchors on the most recent completed session. |
| `gem-trader-premarket.timer` | 08:00 ET Mon-Fri | Premarket research (`run_research_cycle.sh premarket`): forecast + alpha + alpha-overlay + delta |
| `gem-trader-midday.timer` | 12:30 ET Mon-Fri | Midday cache-only refresh (`run_research_cycle.sh midday`): resolve + reports + delta + risk-telemetry. No provider calls. |
| `gem-trader-paper-evidence.timer` | 18:15 ET Mon-Fri | Paper-outcome resolver + scoreboard (`scripts/run_paper_evidence.py`) |
| `gem-trader-research.timer` | 20:30 ET Mon-Fri | Nightly research cycle (`run_research_cycle.sh nightly`); runs `After=` paper-evidence. Fires 20:30 ET (was 19:00 ET) so Alpaca SIP daily bars for today's close are reliably published before the forecast runs. Includes risk-telemetry tail and lens cap `--max=35`. |
| `gem-trader-weekly-liquid.timer` | 14:00 ET Sat | Weekly liquid-top stock-lens refresh (`run_research_cycle.sh lenses-liquid 80`). Closes the staleness gap for off-curated tickers. |

### Phase 2B MCP audit workflows (cache-only)

`./scripts/run_research_cycle.sh mcp-audit` and `mcp-audit-ticker TICKER…` run the read-only Phase 2B audit workflows (`research/mcp_audit_workflows.py`). Outputs land in `cache/research/mcp_audit_*_latest.{json,txt}` and `logs/mcp_audit_*_latest.txt`; the runner imports only `audit_mcp.stocklens_mcp_tools` helpers and never touches providers, governance, or execution. Doctrine: `docs/ops/MCP_AUDIT_WORKFLOWS.md`.

### Options data sources (research-only)

Options chain data feeds the Stock Lens, Alpha Discovery overlay, and Social Arb radar via `core/options_feed_factory.py:load_options_feed()` — Alpaca primary, Tradier fallback. **Alpaca's snapshot endpoint does not return OI/greeks/IV;** the adapter merges OI from `/v2/options/contracts`. Tradier provides greeks + IV when its token validates (currently pending account activation). Live execution path is unchanged. Doctrine: `docs/ops/OPTIONS_DATA_SOURCES.md`.

### Phase 2B.1 MCP audit session orchestration (cache-only)

`./scripts/run_research_cycle.sh mcp-audit-session [open|regular|close]` runs `research/mcp_audit_orchestrator.py`: composes daily + system_health + late_chase, auto-drills the top-10 anomaly tickers (deduped, severity-ordered), classifies state {NORMAL, CONFLICTED, FRAGILE, STALE, BLOCKED}, and writes `cache/research/mcp_analysis_latest.json` plus `logs/mcp_audit_daily_latest.md` (+ a timestamped copy). The dashboard's Risk-mode `MCP AUDIT SUMMARY` panel reads only that JSON sidecar — never calls MCP tools, providers, or Claude. Same forbidden-import / no-mutation invariants as Phase 2B.

### Phase 2B.2/2B.3 Gatekeeper freshness + refresh cadence (cache-only dashboard)

Phase 2B.2 wired stale-suppression: the dashboard's Executive Gatekeeper panel hides cached "Top reasons" when the artifact is older than the per-kind threshold (24h normal / 6h earnings-day / 4h intraday warn) and shows the exact rerun command. The MCP audit panels hide their cached state when the sidecar is older than 12h, the session has changed since `generated_at`, or `regime_forecast_latest.json` is newer than the sidecar. An `EARNINGS TODAY / TOMORROW / THIS WEEK / POST-EARNINGS` badge surfaces in the ticker frame from the existing earnings calendar. Doctrine: `docs/ops/MCP_AUDIT_WORKFLOWS.md` → "Gatekeeper refresh cadence".

Phase 2B.3 added cadence so the artifacts stay fresh on their own:
- `cmd_premarket` runs `gatekeeper-refresh` after `cmd_delta`.
- `cmd_nightly` runs it after `cmd_lenses_nightly` and before `cmd_risk_telemetry`.
- `cmd_mcp_audit_session` accepts an optional `--refresh-gatekeeper` flag (default off) that runs `gatekeeper-refresh` first.
- Freshness-first operator order: `forecast/alpha/lens → gatekeeper-refresh → risk-telemetry → mcp-audit-session → dashboard review`.
- Ticker selection priority: open positions (10) > earnings today (15) > earnings tomorrow (20) > explicit watch (25) > earnings this week (30) > missing artifact (35) > stale artifact (40) > Alpha top (50). Cap 25 default; `--max N` override. Cache-first; the only provider touch is the FMP earnings calendar (cached 6h). No execution / governance / paper-evidence changes — the dashboard remains cache-only.

### Phase 1B/1C/1D/1E risk telemetry + hygiene (paper-only diagnostics)

Four cache-only reports in `research/` measure execution friction, concentration, vol-relative sizing, and paper-state hygiene without touching governance:

| Script | Reads | Writes |
|--------|-------|--------|
| `research/slippage_telemetry_report.py` | `decisions` (`entry_price` → `fill_price` slippage) | `cache/research/slippage_telemetry_latest.json` + `logs/slippage_telemetry_latest.txt` |
| `research/portfolio_concentration_report.py` | open `decisions`, `paper_signals.sector`, `cache_meta` (fmp:profile cache), `cache/prices/*.parquet` | `cache/research/portfolio_concentration_latest.json` + `logs/portfolio_concentration_latest.txt` |
| `research/shadow_sizing_report.py` | open `decisions`, `paper_signals` + `paper_signal_outcomes`, `cache/prices/*.parquet` | `cache/research/shadow_sizing_latest.json` + `logs/shadow_sizing_latest.txt` |
| `research/paper_state_hygiene_report.py` | `decisions`, `paper_signals`, `paper_signal_outcomes`, `voyager_paper_signals`, `veto_log` | `cache/research/paper_state_hygiene_latest.json` + `logs/paper_state_hygiene_latest.txt` |

Run all four via `./scripts/run_research_cycle.sh risk-telemetry` (cache-only, no provider calls). Dashboard mode 3 (Risk) shows a compact `RISK TELEMETRY` strip that reads the first three sidecars; it never invokes the reports. The Phase 1C hygiene sidecar is not wired into the dashboard yet, per the "do not overbuild dashboard UI" guidance.

**Phase 1D — clean-paper-evidence epoch.** Each report runs dual-scope (full ledger + clean epoch). The clean epoch starts at `CLEAN_PAPER_EVIDENCE_START` (single source: `core/paper_evidence_epoch.py`, currently `2026-05-08T00:00:00+00:00`); rows logged earlier pre-date Phase 0 fill telemetry and are quarantined out of `ready_to_gate_clean`. The full-ledger view drives `ready_to_gate_all` and keeps legacy debt visible. Legacy `decisions` rows with missing fill data are listed in `data/state/paper_legacy_quarantine.json` (read-only on the DB — no rows are mutated, deleted, or backfilled). Every report accepts `--since <ISO-ts>` to override the cutoff. JSON sidecars keep the pre-Phase-1D flat shape at the root for dashboard back-compat. Doctrine reference: `docs/ops/CLEAN_PAPER_EVIDENCE_EPOCH.md`.

**Phase 1E — broker-snapshot diagnostics + dashboard visibility.** `scripts/snapshot_broker_positions.py` is an operator-invoked, read-only Alpaca `get_positions` call that writes `cache/state/broker_positions_snapshot.json`. It is **not** wired into `risk-telemetry` (which stays cache-only). When the snapshot is present, the hygiene report enriches each quarantine entry's `broker_position_match` with one of `match` / `no_broker_position` / `closed_by_book`; missing snapshot ⇒ `null` + `broker_match_source=unavailable_no_cached_snapshot`. Dashboard `RISK TELEMETRY` panel (mode 3) gains two compact rows surfacing the Phase 1D verdicts and Phase 1E match coverage; both are cache-only reads. No new gates, no DB writes, no live trading.

**Doctrine for this layer:**
- Diagnostic only — no enforcement. Reports surface warnings, they never block orders. The Phase 1C/1D `ready_to_gate_*` fields are published verdicts, not active gates; promotion to a real gate is a separate phase.
- No mutation of `decisions`, `paper_signals`, `paper_signal_outcomes`, `voyager_paper_signals`, or `veto_log`. Borrow-adjusted returns are computed alongside, not in place of, the originals. The hygiene report is read-only by convention (only `SELECT` statements).
- Backtest friction benchmark (15 bps one-way, 10 bps slippage component) is imported from `research/paper_trades/resolve_tactical_outcomes.py:ROUND_TRIP_FRICTION_PCT`. Keep these in sync if the friction model changes.
- Sector resolution layer order: `paper_signals.sector` → Gatekeeper `cache_meta` key `fmp:profile:{TICKER}` → `UNKNOWN`. The FMP fundamentals JSON files in `cache/fundamentals/{TICKER}.json` do **not** contain a sector field.
- ATR is computed on the fly from `cache/prices/{TICKER}.parquet` (stale parquets are accepted — diagnostic-only).
- Equity hint reads from `logs/trader_heartbeat.json`; the heartbeat does not currently persist account equity, so `shadow_shares` degrades to `n/a` unless `--equity N` is passed.
- Hygiene report thresholds are doctrine-defaulted in-module (e.g. 180d paper_signals stale-open horizon, 365d decisions stale-open horizon, 500 bps extreme-slippage cutoff, 24h duplicate-open window, 7d reconciler drift lookback, Phase 12B SNIPER `aux_h3` coverage cutoff `2026-05-05`). Adjust in `research/paper_state_hygiene_report.py` if doctrine changes.

**Tunable env vars (read by `shadow_sizing_report.py` only, not added to `core/config.py`):**
- `SHADOW_VOL_TARGET` — single-position daily $-vol budget as fraction of equity. Default `0.005` (0.5%).
- `SHADOW_BORROW_BPS_ANNUAL` — annualized short-borrow rate in bps. Default `100` (1.0%/yr).

## Testing notes

- `tests/unit/` and `tests/smoke/` are the active suites; `tests/integration/` is currently empty.
- `tests/conftest.py` stubs `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `FMP_API_KEY` so collection succeeds — every test must mock the actual clients; stubs never reach the wire.
- Smoke tests (`tests/smoke/test_strategy_scanners.py`) cover scanner contracts. Run smoke + unit before any non-trivial change to scanners or governance.
- `docs/smoke_test_checklist.md` is the operational pre-flight (Alpaca bars, FMP fetch, startup checks, scanner output, council, position monitor, heartbeat). It is the canonical pre-session check.

## Operating principles to preserve

- **Additive migrations only.** Existing paper-evidence rows are validation history — schema changes must not drop or rewrite them. The Phase 12B pattern (new nullable column, schema-aware readers, zero-row-safe reports) is the template.
- **Promotion ladder is non-negotiable.** Sleeves move research → backtest → paper → shadow → limited live → full live. A sleeve that fails any gate is archived or redesigned, not silently re-run.
- **One strategy in deep validation at a time.** When a sleeve is in active validation, frozen sleeves stay frozen — do not parallel-promote.
- **Do not overbuild dashboard UI.** Per current operator guidance, prefer CLI + JSON sidecars for new evidence reports; wire the dashboard only after the report stabilizes.
- **Hard separation rule.** Never make production code import from `research/` or `legacy/`. The reverse direction is fine.
