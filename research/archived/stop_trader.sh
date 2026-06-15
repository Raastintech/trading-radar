#!/bin/bash
# stop_trader.sh — Linux/Ubuntu production stop for gem-trader

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Credentials outside the repo (same path as start_trader.sh)
export SNIPER_ENV_PATH="${SNIPER_ENV_PATH:-/home/gem/secure/trading.env}"

PID_FILE="$SCRIPT_DIR/daemon.pid"

is_pid_running() {
    local pid="${1:-}"
    [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]] && return 1
    kill -0 "$pid" 2>/dev/null
}

stop_matching_traders() {
    local pids
    pids=$(pgrep -f "unified_master_trader_v3\.py" 2>/dev/null || true)
    [[ -z "$pids" ]] && return
    echo "Stopping matching trader process(es): $pids"
    echo "$pids" | xargs -r kill 2>/dev/null || true
    sleep 2
    pids=$(pgrep -f "unified_master_trader_v3\.py" 2>/dev/null || true)
    [[ -n "$pids" ]] && echo "$pids" | xargs -r kill -9 2>/dev/null || true
}

echo "=================================================================="
echo "  GEM-TRADER V3 - STOPPING"
echo "=================================================================="

if [[ -f "$PID_FILE" ]]; then
    DAEMON_PID=$(cat "$PID_FILE")
    if is_pid_running "$DAEMON_PID"; then
        echo "Stopping daemon (PID: $DAEMON_PID)..."
        kill "$DAEMON_PID"
        sleep 2
        is_pid_running "$DAEMON_PID" && kill -9 "$DAEMON_PID" 2>/dev/null || true
        echo "   OK: Daemon stopped"
    else
        echo "   WARN: Daemon not running"
    fi
    rm -f "$PID_FILE"
else
    echo "   WARN: No daemon PID file found"
fi

stop_matching_traders

echo ""
echo "=================================================================="
echo "  SYSTEM STOPPED"
echo "=================================================================="
