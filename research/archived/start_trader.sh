#!/bin/bash
# start_trader.sh — Linux/Ubuntu production startup for gem-trader
# Starts unified_master_trader_v3.py under the .venv, logs to logs/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python3"
LOG_FILE="$SCRIPT_DIR/logs/trader_v3_$(date +%Y%m%d).log"
PID_FILE="$SCRIPT_DIR/daemon.pid"
HEARTBEAT_FILE="$SCRIPT_DIR/trader_heartbeat.json"

mkdir -p "$SCRIPT_DIR/logs"

is_pid_running() {
    local pid="${1:-}"
    [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]] && return 1
    kill -0 "$pid" 2>/dev/null
}

cleanup_trader_processes() {
    local pids
    pids=$(pgrep -f "unified_master_trader_v3\.py" 2>/dev/null || true)
    [[ -z "$pids" ]] && return
    echo "   Found existing trader process(es): $pids"
    echo "$pids" | xargs -r kill 2>/dev/null || true
    sleep 2
    pids=$(pgrep -f "unified_master_trader_v3\.py" 2>/dev/null || true)
    [[ -n "$pids" ]] && echo "$pids" | xargs -r kill -9 2>/dev/null || true
}

echo "=================================================================="
echo "  GEM-TRADER V3 - STARTING"
echo "=================================================================="

# Step 1: Clean up stale processes
echo "Step 1: Cleaning up..."
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    is_pid_running "$OLD_PID" && kill "$OLD_PID" 2>/dev/null || true
    sleep 1
    rm -f "$PID_FILE"
fi
cleanup_trader_processes
echo "   OK: Ready to start"

# Step 2: Startup readiness gate
echo "Step 2: Running startup readiness gate..."
if "$VENV_PYTHON" startup_readiness_check.py; then
    echo "   OK: Startup readiness gate passed"
else
    echo "   FAIL: Startup readiness gate failed — aborting"
    exit 1
fi

# Step 3: Refresh diagnostics
echo "Step 3: Refreshing diagnostics artifacts..."
DIAG_DAYS="${SCAN_QUALITY_WINDOW_DAYS:-7}"
if "$VENV_PYTHON" refresh_diagnostics_artifacts.py --days "$DIAG_DAYS"; then
    echo "   OK: Diagnostics refreshed"
else
    echo "   WARN: Diagnostics refresh failed — continuing anyway"
fi

# Step 4: Load credentials and start daemon
echo "Step 4: Starting trading daemon..."
# Credentials live outside the repo at /home/gem/secure/trading.env
# SNIPER_ENV_PATH tells secure_env.py where to find them.
export SNIPER_ENV_PATH="${SNIPER_ENV_PATH:-/home/gem/secure/trading.env}"
if [[ ! -f "$SNIPER_ENV_PATH" ]]; then
    echo "   FAIL: Credential file not found: $SNIPER_ENV_PATH"
    exit 1
fi
set -a
# shellcheck disable=SC1091
source "$SNIPER_ENV_PATH"
set +a

# Remove old heartbeat
rm -f "$HEARTBEAT_FILE"

# Resolve runtime flags (same logic as original but Linux-safe)
export SHORT_LIVE_ENABLED="${SHORT_LIVE_ENABLED:-1}"
export VALIDATE_SHORT_ONLY="${VALIDATE_SHORT_ONLY:-0}"
export ALPACA_PAPER="${ALPACA_PAPER:-true}"

if [[ "$ALPACA_PAPER" == "true" || "$ALPACA_PAPER" == "1" ]]; then
    export PILOT_MAX_NEW_PER_SCAN="${PILOT_MAX_NEW_PER_SCAN:-3}"
    export PILOT_MAX_CONCURRENT="${PILOT_MAX_CONCURRENT:-6}"
    echo "   Paper pilot caps: max_new=$PILOT_MAX_NEW_PER_SCAN, max_concurrent=$PILOT_MAX_CONCURRENT"
fi

nohup "$VENV_PYTHON" -u unified_master_trader_v3.py >> "$LOG_FILE" 2>&1 &
DAEMON_PID=$!
echo "$DAEMON_PID" > "$PID_FILE"

sleep 5

if is_pid_running "$DAEMON_PID"; then
    echo "   OK: Daemon started (PID: $DAEMON_PID)"
else
    echo "   FAIL: Daemon failed to start!"
    tail -50 "$LOG_FILE"
    exit 1
fi

echo ""
echo "=================================================================="
echo "  GEM-TRADER V3 RUNNING"
echo "=================================================================="
echo "  Daemon PID : $DAEMON_PID"
echo "  Log        : $LOG_FILE"
echo ""
echo "  Monitor  : tail -f $LOG_FILE"
echo "  Status   : sudo systemctl status gem-trader"
echo "  Stop     : ./stop_trader.sh"
echo "=================================================================="
