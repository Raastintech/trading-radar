# PROJECT_INDEX

Authoritative entry point for the Stock Lens / `trading-production` repo.
Read this **first** in any new Claude/Codex session. It is intentionally short.
Detailed roadmap, build rules, and the next planned phase live in sibling docs
linked at the bottom.

---

## A. Project Identity

- **Name:** Stock Lens / `trading-production` (systemd unit: `gem-trader.service`)
- **Purpose:** retail trading **research + paper-validation command center**
- **Not** live-capital approved
- **Not** an auto-trading system yet
- Built to **discover opportunities, audit flaws, track evidence, and prevent
  false confidence**

The system runs a persistent daemon that scans, vetoes, sizes, paper-routes,
and exits positions, but live capital is gated behind three independent keys
plus an optional file-on-disk confirmation. Until the pre-registered 2026 H2
holdout closes (2026-12-01) the expectation is `ALLOW_LIVE_CAPITAL=false`.

## B. Core Doctrine

- **Paper-only** until evidence and safety gates justify otherwise.
- **No sleeve is capital-proven yet** — see §G.
- **Clean paper evidence starts at `CLEAN_PAPER_EVIDENCE_START`**
  (currently `2026-05-08T00:00:00+00:00`, single source:
  `core/paper_evidence_epoch.py`).
- **Legacy paper rows are preserved** but quarantined in
  `data/state/paper_legacy_quarantine.json`. No row is deleted or rewritten —
  schema migrations are additive only.
- **Research tools are not trade approval.** Stock Lens, Alpha Discovery,
  Executive Gatekeeper, dashboard, fragility overlay — all are research aids
  the operator (or, in a future phase, an audit-only MCP) consults. None of
  them route an order.
- **Claude / MCP may audit, but must not trade in V1.** Any future MCP
  capability must route through the existing `OrderManager`, Submission Gate,
  Circuit Breaker, Portfolio Risk, and Reconciler. Direct broker calls from
  Claude/MCP are forbidden.

## C. Major Subsystems

| Subsystem | One-line role | Key path |
|---|---|---|
| **Market Forecast** | Daily regime probability + headline invalidation | `research/regime_forecast.py` → `cache/research/regime_forecast_latest.json` |
| **Market Posture** | Candidate-pressure summary across active sleeves | `core/research_assist_bte.py` |
| **Stock Lens** | Per-ticker bias / entry / options / posture composite | `research/stock_lens_runner.py` → `cache/research/stock_lens_<TICKER>_latest.json` |
| **Alpha Discovery** | Nightly + premarket overlay candidate boards | `core/alpha_discovery.py` → `cache/research/alpha_discovery_*_latest.json` |
| **Daily Entry Validator** | "Too Extended / Watch Reclaim / Broken / Avoid" verdict per ticker | inside Stock Lens layers |
| **Executive Gatekeeper** | Long-horizon fundamental gate per ticker (manual run) | `research/executive_gatekeeper_report.py` → `cache/research/executive_gatekeeper_<TICKER>_latest.json` |
| **Options Quality V2** | Bullish-confirming / Bearish-hedge classification | embedded layer of Stock Lens |
| **Social Arb Radar** | 3×/week social-flow cross-check, cache-only by default | `research/social_arb_radar.py` |
| **Research Journal** | Manual conclusions per ticker with review windows | `research/research_journal.py` → `data/state/research_notes.jsonl` |
| **Forward Tracking** | Honest hit-rate ledger for forecasts and lens calls | `cache/research/forecast_forward_summary_latest.json`, `cache/research/stock_lens_forward_summary_latest.json` |
| **Evidence Rigor Audit** | Cross-strategy P&L and confidence sanity report | `docs/scorecards/evidence_rigor_report.{md,json}` |
| **Paper Hygiene / Clean Epoch** | Phase 1C/1D diagnostic of paper-evidence integrity | `research/paper_state_hygiene_report.py` → `cache/research/paper_state_hygiene_latest.json` |
| **Risk Telemetry** | Phase 1B cache-only reports: slippage, concentration, shadow sizing | `research/{slippage,portfolio_concentration,shadow_sizing}_*.py` |
| **Dashboard** | Read-only TUI command center (cache-only) | `dashboards/gem_trader_hq.py` |
| **Submission Gate / Circuit Breakers** | Re-check the submission-time state before any broker call | `core/submission_gate.py`, `execution/circuit_breakers.py` |
| **Reconciler / Broker Snapshot** | Compare broker truth against `decisions` table | `execution/position_reconciler.py`, `core/broker_snapshot.py` |
| **MCP Audit Server** | Read-only Claude-facing audit tools — 17 tools, Phase 2A/2B complete | `audit_mcp/stocklens_mcp_server.py` |

## D. Key Commands

```bash
# Research workflow (provider calls happen ONLY in these timers/jobs)
./scripts/run_research_cycle.sh nightly          # full nightly cycle (provider)
./scripts/run_research_cycle.sh premarket        # forecast + alpha + delta (provider)
./scripts/run_research_cycle.sh resolve          # cache-only: resolve forward outcomes
./scripts/run_research_cycle.sh weekly-review    # weekly summary (cache-only)
./scripts/run_research_cycle.sh risk-telemetry   # Phase 1B/1C/1D cache-only reports
./scripts/run_research_cycle.sh gatekeeper TICKER
./scripts/run_research_cycle.sh lenses-nightly   # provider, capped
./scripts/run_research_cycle.sh lenses-liquid 100

# Operator-invoked broker snapshot (read-only)
SNIPER_ENV_PATH=/home/gem/secure/trading.env \
  .venv/bin/python scripts/snapshot_broker_positions.py

# Dashboard (cache-only TUI)
SNIPER_ENV_PATH=/home/gem/secure/trading.env \
  .venv/bin/python dashboards/gem_trader_hq.py

# Sniper H3 forward instrumentation
.venv/bin/python research/sniper_h3_forward_report.py

# Tests
.venv/bin/python3 -m pytest tests/unit tests/smoke -q
```

Two env shapes are canonical (see CLAUDE.md for detail):
`SNIPER_ENV_PATH=/home/gem/secure/trading.env` for any tool that imports
`core.config`, or `GEM_TRADER_SKIP_DOTENV=true` for offline / compile-only.

## E. Important Artifacts

- `cache/research/*` — every research artifact JSON (forecast, lens, alpha,
  hygiene, telemetry, weekly review, holdout scoreboard, social arb).
- `cache/state/broker_positions_snapshot.json` — Phase 1E broker truth view,
  refreshed by the daemon on each reconcile cycle (Phase 1F+).
- `cache/state/*` — operator state caches.
- `data/state/paper_legacy_quarantine.json` — Phase 1D ledger of legacy
  pre-clean-epoch paper rows that are intentionally not in the active count.
- `data/state/research_notes.jsonl` — manual journal entries, latest wins.
- `logs/*` — daemon logs, paper-evidence logs, sidecar text dumps.
- `db/trading.db` — canonical operational SQLite (decisions, veto_log, trades,
  paper_signals, macro_events, circuit_breaker_state, cache_meta). WAL mode.
- `docs/scorecards/*` — per-sleeve scorecards and evidence rigor reports.
- `docs/research/*` — deep doctrine docs (regime forecaster, holdout pre-reg,
  executive gatekeeper, social arb, research command center runbook).
- `docs/ops/*` — operator-facing docs (clean-epoch policy, audit notes,
  DB backup/restore, this playbook, MCP plan).
- `research/sleeves/trades/*.csv` — historical paper-trade CSVs:
  `SHORT_A.csv`, `SNIPER_V6.csv`, `VOYAGER_PAPER.csv`.

## F. Safety Status

- **RESEARCH_ONLY_MODE — permanent (Phase 3A complete 2026-06-13).**
- All execution paths (`submit_market_order`, `submit_limit_order`, `close_position`,
  `cancel_all_orders`) raise `ResearchOnlyModeError`. There is no path to a live or
  paper order from any current code.
- `gem-trader.service` (trading daemon) is stopped and disabled.
- `gem-trader-paper-evidence.timer` is stopped and disabled.
- Alpaca broker client is a cache-serving stub (`get_daily_bars` → `cache/prices/*.parquet`).
  No network calls to Alpaca. Alpaca API keys are no longer required.
- `PAPER_TRADING`, `ALPACA_PAPER`, `ALLOW_LIVE_CAPITAL` env vars are retained in
  `core/config.py` but have no effect — execution paths are gone.
- **DB hardening complete:** WAL + `synchronous=FULL` + atomic backup via
  `scripts/backup_db.sh` (SQLite online `.backup` API).
- Historical paper rows (decisions, paper_signals, paper_signal_outcomes) are
  preserved read-only. No new rows are written.

## G. Current Known Truth

- **System is permanently research-only.** All sleeves (SNIPER, VOYAGER, SHORT_A,
  REMORA, CONTRARIAN) are DECOMMISSIONED or FROZEN. No new paper signals.
  No capital promotion path. See `docs/research/AUTO_TRADING_DECOMMISSION_FINAL_FINDINGS.md`.
- **SNIPER V6 was historically indistinguishable from random.** Paper rows preserved
  for historical reference; `paper_ledger=True` for resolution only.
- **VOYAGER paper rows preserved** but strategy is decommissioned. Historical
  paper evidence remains in `decisions` and `voyager_paper_signals`.
- **SHORT_A frozen since 2026-05-24** — net-negative, noisy. Historical rows preserved.
- **Clean epoch telemetry** (Phase 1D) survives as a read-only diagnostic record;
  `ready_to_gate_*` verdicts are historical, not active gates.
- **Current daily cycle:** market heartbeat → research scanner → stock research cards
  → regime forecast → alpha discovery → social arb → lens → risk telemetry.
  FMP is the sole live data provider. Tradier for options research (read-only).
  See `docs/ROADMAP_PHASES.md` for the decommission record.

---

## Where to go next

- **`docs/ROADMAP_PHASES.md`** — full phase map, completion state, what comes next.
- **`docs/ops/CLAUDE_BUILD_PLAYBOOK.md`** — session rules and protected areas.
- **`docs/ops/MCP_AUDIT_SERVER_PLAN.md`** — Phase 2A design.
- **`docs/strategy/CURRENT_READINESS.md`** — sleeve-by-sleeve operational truth.
- **`docs/ops/CLEAN_PAPER_EVIDENCE_EPOCH.md`** — Phase 1D doctrine.
- **`CLAUDE.md`** — code-edit guardrails and command shortcuts.
