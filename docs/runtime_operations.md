# gem-trader — Runtime Operations Guide

**Date:** 2026-04-16  
**Model:** session-aware supervised daemon

---

## Design Rationale

### Why systemd, not cron

| | cron | systemd (current) |
|---|---|---|
| Core trading loop | Restarted every N minutes — cold boot each cycle | Single persistent process — warm state, no cold boot overhead |
| Session awareness | Cron has no concept of market hours | Daemon reads `SessionState` and dispatches per session |
| Failure recovery | Silent failure if job dies mid-cycle | `Restart=on-failure` with 30 s back-off |
| Log visibility | Scattered across log files | `journalctl -u gem-trader` gives unified stream |
| Clean shutdown | `kill` without SIGTERM handling | SIGTERM → graceful finish + heartbeat update |

**Rule:** core trading loop = systemd service. Nightly batch jobs (cache cleanup, data pre-warm) = systemd timers.

---

## Session States

The daemon in `main.py` checks `core.session.get_session_state()` each loop iteration and dispatches differently per state.

| State | Window (ET) | Execution | Behavior |
|-------|------------|-----------|----------|
| `PREMARKET` | 04:00–09:30 | **No** | Runs pre-market brief: FMP refresh, economic calendar, earnings look-ahead, treasury rates. Repeats every 5 min. |
| `REGULAR` | 09:30–16:00 | **Yes** | Full scan → veto → allocate → execute loop every 5 min. Position monitor every 60 s. |
| `POSTMARKET` | 16:00–20:00 | **No** | Runs post-market brief: EOD snapshot, SPY bar, earnings surprises, open position P&L. Repeats every 5 min. |
| `CLOSED` | 20:00–04:00 + weekends + holidays | **No** | Sleeps 60 s per tick. Writes heartbeat. No data fetches. |

Early-close days (Thanksgiving Friday, Christmas Eve, July 3 if weekday): REGULAR ends 13:00, POSTMARKET ends 17:00.

### Code location

```
core/session.py
  SessionState            — enum
  get_session_state()     — returns current SessionState
  is_execution_allowed()  — True only during REGULAR
  next_session_change()   — (next_state, datetime)
```

---

## Process Supervision (systemd)

### Install the service

```bash
# Copy service files
sudo cp /home/gem/trading-production/systemd/gem-trader.service \
        /etc/systemd/system/

sudo cp /home/gem/trading-production/systemd/gem-trader-nightly.service \
        /home/gem/trading-production/systemd/gem-trader-nightly.timer \
        /etc/systemd/system/

sudo cp /home/gem/trading-production/systemd/gem-trader-paper-evidence.service \
        /home/gem/trading-production/systemd/gem-trader-paper-evidence.timer \
        /etc/systemd/system/

# Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable gem-trader
sudo systemctl enable gem-trader-nightly.timer
sudo systemctl enable gem-trader-paper-evidence.timer

# Start
sudo systemctl start gem-trader
sudo systemctl start gem-trader-nightly.timer
sudo systemctl start gem-trader-paper-evidence.timer
```

### Operator commands

```bash
# Status (human-friendly)
./scripts/check_status.sh

# Start / stop / restart
./scripts/start_trader.sh
./scripts/stop_trader.sh
./scripts/restart_trader.sh

# systemd equivalents
sudo systemctl start   gem-trader
sudo systemctl stop    gem-trader
sudo systemctl restart gem-trader
sudo systemctl status  gem-trader

# Live logs
sudo journalctl -u gem-trader -f
tail -f logs/gem-trader.log
```

---

## Without systemd (direct launch)

If systemd is not available or for development/paper sessions, the scripts detect this and use `nohup`:

```bash
# Set credentials path (shell needs it for the direct launch path)
export SNIPER_ENV_PATH=/home/gem/secure/trading.env

./scripts/start_trader.sh    # launches main.py via nohup, writes PID to logs/daemon.pid
./scripts/check_status.sh    # reads PID file + heartbeat JSON
./scripts/stop_trader.sh     # SIGTERM → waits 10 s → SIGKILL if needed
```

---

## Heartbeat

The daemon writes `logs/trader_heartbeat.json` every loop iteration:

```json
{
  "last_heartbeat_ts": "2026-04-16T09:35:01.234567+00:00",
  "session_state":     "REGULAR",
  "is_trading":        true,
  "market_status":     "REGULAR",
  "heartbeat_stage":   "scanning"
}
```

`heartbeat_stage` values (REGULAR session):
- `position_monitor` — running position exit checks
- `macro_refresh` — refreshing FMP economic calendar
- `universe_rebuild` — rebuilding dynamic ticker universe
- `scanning` — running 5 strategy scanners
- `evaluating` — council → allocator → order loop

`heartbeat_stage` values (pre/post market):
- `premarket_brief` / `postmarket_brief` — running research pass

`check_status.sh` reads this file and computes heartbeat age.

---

## Nightly Refresh (systemd timer)

`gem-trader-nightly.timer` fires at **03:30 ET Mon–Fri**:

1. Expires Parquet cache entries older than 24 h
2. Pre-warms SPY bars (90 days) and VIX into cache
3. Logs to `logs/gem-trader-nightly.log`

This ensures the first REGULAR cycle of the day starts with fresh data rather than spending time on FMP cold fetches.

---

## Paper Evidence Loop (systemd timer)

`gem-trader-paper-evidence.timer` fires at **18:15 ET Mon-Fri**:

1. Runs `scripts/run_paper_evidence.py`
2. Resolves tactical paper outcomes for `SNIPER_V6` and `SHORT_A`
3. Refreshes the unified paper scoreboard
4. Writes status to `logs/paper_evidence_status.json`
5. Writes the latest report to `logs/paper_scoreboard_latest.txt`

The time is after the regular US equity session and after daily bars are
expected to be current for Alpaca/cache reads. The resolver is idempotent, so a
manual rerun is safe.

Check status:

```bash
./scripts/check_status.sh
```

Manual run:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python scripts/run_paper_evidence.py
```

---

## What Is Read-Only vs Executable

**Read-only at all times (dashboard and PREMARKET/POSTMARKET/CLOSED):**
- Gatekeeper cache reads
- Alpaca `get_positions()`, `get_account()`
- All FMP data fetches
- DecisionLogger reads (scan_results, veto_log, macro_events)
- Universe builder
- VetoCouncil `evaluate()` (no side effects)
- PortfolioAllocator `evaluate()` (no side effects)

**Write operations allowed only during REGULAR session:**
- `OrderManager.execute()` — places orders via Alpaca
- `PositionMonitor.check_exits()` — places exit orders via Alpaca
- `DecisionLogger.log_scan_result()` / `log_veto()` — writes to SQLite

The `is_execution_allowed()` check in `main.py` gates every order submission.

---

## Dashboard Session Visibility

The dashboard header always shows:
- **Session badge**: `[REGULAR]` (green), `[PRE-MARKET]` (yellow), `[POST-MARKET]` (yellow), `[CLOSED]` (dim)
- **Read-only indicator**: `READ-ONLY · REGULAR in Nm` when execution is not allowed
- **System health**: `[SYSTEM OK]` / `[SYSTEM DEGRADED]` / `[SYSTEM HALT]`

The Trade Readiness panel shows the next session boundary when market is closed.

## Dashboard Validation And Research Layers

The dashboard separates paper validation from discretionary research.

Core validation layer:

- Active paper sleeves only: `VOYAGER`, `SNIPER_V6`, `SHORT_A`
- Monitor mode shows:
  - Paper Evidence: raw/effective rows, open/closed rows, governance blocks,
    observe-only/duplicate rows, resolver and scoreboard timestamps
  - Paper Readiness: progress toward paper-promotion thresholds, with immature
    samples shown as `not enough evidence yet`
  - Governance Blocks: same-ticker, sector, regime-cluster, max-position,
    duplicate/open-exposure, frozen-sleeve, and other block counts
- Risk mode shows Evidence Freshness:
  - daily bars current
  - universe snapshot age
  - scanner last cycle
  - paper resolver last success
  - scoreboard refresh
  - paper-loop health

Secondary research layer:

- Research mode includes a Research Assist panel for discretionary/manual
  research only.
- Research Assist may show market-bias hints, Market Posture research context,
  top liquid names, and top trending names.
- These items are not paper signals, do not feed paper evidence, and must not be
  confused with approved strategy output.

Market Posture status:

- `core/research_assist_bte.py` is the active Market Posture path (user-facing
  panels say "Market Posture"; the file name and primary symbols are kept for
  backward compatibility with existing imports).
- Market Posture is cache-only: inputs are the current universe snapshot,
  regime context, and VIX. It does not call providers, route orders, log
  paper evidence, or override active sleeves.
- Market Posture is **not** the legacy Breakout Timing Engine. It does not
  evaluate confirmed Sniper breakouts, does not output ENTER/WAIT/SKIP, and
  does not compute breakout probabilities or timing windows. The legacy BTE
  blueprint at `docs/strategy/BREAKOUT_TIMING_ENGINE_BLUEPRINT.md` describes a
  separate, unbuilt Sniper-specific breakout-timing overlay that would be
  advisory-only and require per-candidate breakout-timing features and
  validation; it is intentionally not implemented at this time.
- Existing historical BTE code remains legacy/archive. It was originally a
  Sniper pre-breakout timing dataset/labeling concept and is not wired into
  active paper validation.
- Top liquid methodology: rank dashboard `strategy_candidates` by explicit
  `avg_dollar_volume_20`, using `current_dollar_volume` as a tie-break.
- Top trending methodology: rank by a compact participation/momentum score:
  weighted absolute 5d/20d move multiplied by `volume_ratio_5d`.
- Freshness assumption: all Research Assist lists inherit the universe snapshot
  freshness and completed-bar policy shown in the dashboard.

---

## Deployment Checklist

Before going live (ALPACA_PAPER=false):

- [ ] `core/startup_checks.py` shows all green (no DEGRADED)
- [ ] `docs/smoke_test_checklist.md` passes all 7 non-interactive checks
- [ ] `systemctl status gem-trader` shows `active (running)`
- [ ] Heartbeat age < 120 s in `check_status.sh`
- [ ] Dashboard header shows `[SYSTEM OK]` and `[REGULAR]` during market hours
- [ ] Confirm `ALPACA_PAPER=false` only after full paper session review
