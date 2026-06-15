#!/bin/bash
# scripts/stop_trader.sh — Stop gem-trader (systemd or direct).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PID_FILE="$ROOT_DIR/logs/daemon.pid"

is_pid_running() {
    local pid="${1:-}"
    [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]] && return 1
    kill -0 "$pid" 2>/dev/null
}

echo "=================================================================="
echo "  GEM-TRADER — STOPPING"
echo "=================================================================="

# ── Prefer systemd if the service is active ───────────────────────────────────
if systemctl is-active --quiet gem-trader 2>/dev/null; then
    echo "  Stopping gem-trader.service via systemd..."
    sudo systemctl stop gem-trader
    echo "  OK"
    echo "=================================================================="
    exit 0
fi

# ── Direct process stop ───────────────────────────────────────────────────────
if [[ -f "$PID_FILE" ]]; then
    DAEMON_PID=$(cat "$PID_FILE")
    if is_pid_running "$DAEMON_PID"; then
        echo "  Stopping daemon (PID: $DAEMON_PID) — sending SIGTERM..."
        kill "$DAEMON_PID"
        # Wait up to 10 s for clean shutdown
        for i in $(seq 1 10); do
            sleep 1
            is_pid_running "$DAEMON_PID" || break
        done
        if is_pid_running "$DAEMON_PID"; then
            echo "  Daemon did not exit in 10 s — sending SIGKILL"
            kill -9 "$DAEMON_PID" 2>/dev/null || true
        fi
        echo "  OK: Stopped"
    else
        echo "  WARN: Process $DAEMON_PID not running (stale PID file)"
    fi
    rm -f "$PID_FILE"
else
    # Fallback: find any running main.py
    PIDS=$(pgrep -f "python.*main\.py" 2>/dev/null || true)
    if [[ -n "$PIDS" ]]; then
        echo "  Stopping process(es): $PIDS"
        echo "$PIDS" | xargs -r kill 2>/dev/null || true
        sleep 3
        PIDS=$(pgrep -f "python.*main\.py" 2>/dev/null || true)
        [[ -n "$PIDS" ]] && echo "$PIDS" | xargs -r kill -9 2>/dev/null || true
        echo "  OK: Stopped"
    else
        echo "  WARN: No running trader process found"
    fi
fi

echo ""
echo "=================================================================="
echo "  GEM-TRADER STOPPED"
echo "=================================================================="
