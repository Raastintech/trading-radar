# GIT_BASELINE_TRACKING_PLAN

**Date:** 2026-06-15
**Phase:** 3C — Research-Only Checkpoint / Git Baseline Safety Audit

---

## Background

The initial git history contains only 3 commits:
- `d4dfbc0` Phase 4 Research Engine
- `1236d80` Phase 5 — research-only finalization
- `6af0499` Phase 3B (tagged `research-only-phase3b-accepted`, `research-only-baseline`)

Most project files are untracked (363 entries in `git status --short`). This document
classifies every untracked entry and prescribes a safe staged add.

**Rule: Do NOT run `git add .` — it will sweep in runtime state, CSVs from sleeves, and
any unindexed notebooks. Use the explicit commands in §F below.**

---

## A. Safe Source Code to Track

All Python source modules and shell scripts are safe. No secrets, no runtime state.

```
audit_mcp/__init__.py
audit_mcp/stocklens_mcp_server.py
core/                          (all .py files)
council/                       (all .py files)
execution/                     (all .py files — execution methods raise ResearchOnlyModeError)
legacy/                        (archived modules — safe, no active execution path)
main.py
research/__init__.py
research/*.py                  (all research scripts)
research/archived/             (archived research — safe)
research/backtests/            (backtesting scripts)
research/event_studies/        (event study scripts)
research/paper_trades/         (paper trade resolution scripts)
research/scanner_truth/        (scanner truth analysis)
research/sleeves/              (sleeve scripts — see §B for trades CSV note)
strategies/                    (strategy modules — decommissioned but safe to track)
scripts/                       (all shell and Python scripts in scripts/)
requirements.txt
.env.template                  (safe — empty placeholder values only, no real keys)
gem-trader.service             (systemd unit file — source artifact)
systemd/                       (systemd unit files — source artifacts)
scripts/systemd/               (copies of unit files)
archive/                       (directory itself; execution_disabled/* already gitignored)
```

---

## B. Safe Docs to Track

All markdown and doc files under `docs/` are safe research records.

```
CLAUDE.md
SNIPER_TRADING_AI_MASTER_DOC.md   (historical/archived; has OBSOLETE banner — safe to track)
Trading-System-Master-Spec-V2.md  (historical/archived — safe to track)
docs/INDEX.md
docs/PROJECT_INDEX.md
docs/ROADMAP_PHASES.md
docs/daily_notes/
docs/ops/
docs/reports/                     (research summary docs — different from root reports/)
docs/research/                    (all .md research docs)
docs/runtime_operations.md
docs/scorecards/
docs/smoke_test_checklist.md
docs/strategy/
docs/strategy_scanner_council_audit.md
docs/system_pipeline_v2.md
docs/trade_log.md
```

Note: `docs/reports/` is a docs subdirectory and is safe to track. The root-level
`reports/` is runtime output and is now in `.gitignore`.

---

## C. Safe Tests to Track

```
tests/__init__.py
tests/conftest.py
tests/integration/             (currently empty)
tests/smoke/
tests/unit/                    (all test_*.py files)
```

---

## D. Runtime Data — Already Ignored or Newly Ignored

The following are already covered by `.gitignore` and will NOT appear in `git status`
after the gitignore update. Listed for reference:

| Path | gitignore rule |
|---|---|
| `cache/` | `cache/` |
| `data/` | `data/` |
| `logs/` | `logs/` |
| `*.db`, `*.sqlite` | `*.db`, `*.sqlite` |
| `*.parquet`, `*.feather` | `*.parquet`, `*.feather` |
| `.venv/` | `.venv/` |
| `__pycache__/` | `__pycache__/` |
| `archive/execution_disabled/*` | `archive/execution_disabled/*` |
| `trader_heartbeat.json` | added 2026-06-15 |
| `reports/` (root level) | added 2026-06-15 |

Note: `research/sleeves/trades/` contains paper trade CSVs (SHORT_A.csv, SNIPER_V6.csv,
VOYAGER_PAPER.csv). These are historical paper-trade records documented in CLAUDE.md.
They are safe to track (no real money, no secrets). Operator decision: **include** — they
are the paper validation record.

---

## E. Dangerous Findings

| Finding | Severity | Status |
|---|---|---|
| `.env.template` untracked | LOW — empty template, no real keys | Safe to track |
| `trader_heartbeat.json` was untracked | MEDIUM — runtime JSON with scan state | Fixed: added to `.gitignore` |
| `reports/` (root) was untracked | LOW — runtime scan reports | Fixed: added to `.gitignore` |
| `db/trading.db` | HIGH — SQLite database with trade history | Already ignored via `*.db` |
| `db/trading_performance.db` | HIGH — legacy performance DB | Already ignored via `*.db` |
| `db/schema.sql` | SAFE — schema DDL only, no data | Safe to track |
| `db/migrations/` | SAFE — DDL migration scripts | Safe to track |
| No `.env` or `trading.env` in untracked list | GOOD | Already ignored |
| No `secure/` in untracked list | GOOD | Already ignored |
| No `cache/` or `data/` in untracked list | GOOD | Already ignored |

**DANGEROUS_UNTRACKED_FILES_FOUND: NO** (after `.gitignore` additions)

---

## F. Exact Safe `git add` Command

Run these in order. Review `git status` after each block before proceeding.

```bash
# Block 1: Root files (safe source + docs)
git add CLAUDE.md .env.template .gitignore requirements.txt main.py gem-trader.service
git add SNIPER_TRADING_AI_MASTER_DOC.md Trading-System-Master-Spec-V2.md

# Block 2: Core source modules
git add core/ council/ execution/ strategies/ audit_mcp/ legacy/ archive/

# Block 3: Research scripts and papers
git add research/

# Block 4: Scripts and systemd
git add scripts/ systemd/

# Block 5: Tests
git add tests/

# Block 6: Docs
git add docs/

# Block 7: DB schema (NOT the .db files — those are ignored)
git add db/schema.sql db/migrations/

# Verify before committing — no .db, .parquet, .env, cache, data, logs
git status --short | egrep '(\.db|\.parquet|\.env|cache/|data/|logs/|\.venv)' || echo "CLEAN"
```

**DO NOT run `git add .`**

---

## G. Explicit Exclusions (never stage these)

```
.venv/                    gitignored
cache/                    gitignored
data/                     gitignored
logs/                     gitignored
db/trading.db             gitignored (*.db)
db/trading_performance.db gitignored (*.db)
trader_heartbeat.json     gitignored (added 2026-06-15)
reports/                  gitignored (added 2026-06-15)
backups/                  (not in tracking plan — operator backup directory)
```

---

## H. Status

```
BASELINE_READY_TO_STAGE: YES
DANGEROUS_UNTRACKED_FILES_FOUND: NO
```

Operator must run Block 1–7 commands and confirm `git status` is clean of secrets/runtime
before creating the baseline commit.
