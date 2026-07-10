# gem-trader — Documentation Index

> **⚠ RESEARCH-ONLY MODE (permanent, 2026-06-13).** All trading sleeves and
> execution paths are decommissioned. For current operating truth read, in
> order: `ROADMAP_PHASES.md` → `PROJECT_INDEX.md` → `ops/CLAUDE_BUILD_PLAYBOOK.md`.
> The strategy/sleeve documents indexed below are preserved as history and must
> not be treated as active doctrine.

## Current Operating Truth (read first)

| File | Purpose |
|------|---------|
| [ROADMAP_PHASES.md](ROADMAP_PHASES.md) | Current operating mode and phase completion state |
| [PROJECT_INDEX.md](PROJECT_INDEX.md) | Subsystems, commands, artifacts, current truth |
| [ops/CLAUDE_BUILD_PLAYBOOK.md](ops/CLAUDE_BUILD_PLAYBOOK.md) | Session rules, protected areas, refusals |

## Strategy & Architecture (historical — pre-decommission)

| File | Purpose |
|------|---------|
| [CURRENT_DOCTRINE_MAP.md](strategy/CURRENT_DOCTRINE_MAP.md) | Doctrine source ordering (historical trading-era map) |
| [PROJECT_NORTH_STAR.md](strategy/PROJECT_NORTH_STAR.md) | Core mission, non-negotiables, long-term vision |
| [MASTER_PLAN.md](strategy/MASTER_PLAN.md) | Long-term roadmap reference; not the live status source |
| [STRATEGY_DOCTRINE.md](strategy/STRATEGY_DOCTRINE.md) | Permanent Quant Research Doctrine and required research output format |
| [CURRENT_READINESS.md](strategy/CURRENT_READINESS.md) | **Historical only** — describes the pre-decommission trading phase, not current operational truth |
| [ARCHITECTURE_PHASE1.md](strategy/ARCHITECTURE_PHASE1.md) | System architecture and data flow (trading era) |
| [BREAKOUT_TIMING_ENGINE_BLUEPRINT.md](strategy/BREAKOUT_TIMING_ENGINE_BLUEPRINT.md) | Breakout timing engine design |
| [OPTIONS_PHASE1_SPEC.md](strategy/OPTIONS_PHASE1_SPEC.md) | Options strategy spec (historical; superseded by the research-only options layer) |

## Research & Backtesting

| File | Purpose |
|------|---------|
| [EXECUTION_PROMPT_SHORT.md](strategy/EXECUTION_PROMPT_SHORT.md) | SHORT strategy backtester run instructions (historical) |
| [VALIDATION_OPEN_ITEMS.md](strategy/VALIDATION_OPEN_ITEMS.md) | Open validation issues and known gaps |
| [CODEX_PHASE5_PROMPT.md](strategy/CODEX_PHASE5_PROMPT.md) | Phase 5 build prompts and context |
| [reports/RESEARCH_ARCHIVE.md](reports/RESEARCH_ARCHIVE.md) | Historical backtest results archive |
| [research/](research/) | Current research-engine docs: scanner, alpha radar, forecaster, options snapshots, decommission logs |

## Historical / Obsolete Doctrine References

These documents are preserved for history only. They are not current operating
truth and must not override `ROADMAP_PHASES.md` or `PROJECT_INDEX.md`.

| File | Status |
|------|--------|
| `../SNIPER_TRADING_AI_MASTER_DOC.md` | Historical / obsolete consolidated master doc |
| [../docs/strategy_scanner_council_audit.md](strategy_scanner_council_audit.md) | Historical pre-doctrine audit with obsolete VOYAGER assumptions |
| [system_pipeline_v2.md](system_pipeline_v2.md) | Historical pipeline snapshot with obsolete sleeve assignments |

## Operations

| File | Purpose |
|------|---------|
| [runtime_operations.md](runtime_operations.md) | Historical daemon runtime guide (daemon decommissioned) |
| [smoke_test_checklist.md](smoke_test_checklist.md) | Pre-flight checks (partially historical — see its banner) |
| [trade_log.md](trade_log.md) | Manual trade log and notes |
| [daily_notes/](daily_notes/) | Day-by-day session notes |

## Live System (Ubuntu /home/gem/trading-production/)

```
production/
├── main.py                    # Former daemon entry point — gem-trader.service stopped+disabled 2026-06-13; do not restart
├── requirements.txt           # pip dependencies
├── core/
│   ├── config.py              # Credential loader (reads /home/gem/secure/trading.env)
│   ├── fmp_client.py          # FMP premium client + Gatekeeper cache (primary provider)
│   ├── data_gatekeeper.py     # SQLite metadata + Parquet price cache
│   ├── options_feed_factory.py# Tradier-only options feed (research)
│   └── alpaca_client.py       # Cache-serving stub (execution decommissioned 2026-06; no network calls)
├── research/                  # Research engine: scanner, alpha radar, forecaster, options snapshot collector
├── strategies/                # DEAD CODE — all sleeves permanently decommissioned 2026-06-13
├── council/                   # DEAD CODE — veto council (no signals flow through it)
├── db/
│   ├── trading.db             # Canonical operational DB (WAL)
│   └── trading_performance.db # Legacy, read-only reference
└── legacy/                    # Migrated/archived files — not part of the runtime
```

## Data Sources (Production)

| Source | Purpose | Plan |
|--------|---------|------|
| FMP | **Primary provider** — earnings, fundamentals, economic calendar, VIX, news, daily price bars | Premium (budget tracked monthly; spend cache-first) |
| Tradier | Options chains, IV, greeks, OI — research + daily 15:45 ET snapshot collector | Active (research-only) |
| Alpaca | Cache-serving stub — no network calls; paid subscription dropped post-decommission, free-tier account kept as fallback only | Free tier |
| yfinance | Optional debug fallback only — never primary | None |

## Key Operational Commands (Ubuntu server)

```bash
# Research cycles (systemd timers call these; manual invocation is safe)
./scripts/run_research_cycle.sh nightly        # forecast + alpha + delta + lenses + resolver + holdout
./scripts/run_research_cycle.sh premarket      # lite cycle: forecast + alpha + delta
./scripts/run_research_cycle.sh resolve        # cache-only: resolve forward outcomes
./scripts/run_research_cycle.sh risk-telemetry # cache-only diagnostics

# Dashboard (read-only, cache-only TUI)
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python dashboards/gem_trader_hq.py

# Tests
.venv/bin/python3 -m pytest tests/unit -x -q

# Timers (the daemon gem-trader.service is stopped + disabled — do not restart it)
systemctl list-timers 'gem-trader-*'

# DB backup (WAL-safe)
./scripts/backup_db.sh
```
