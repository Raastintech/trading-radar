#!/bin/bash
# scripts/start_trader.sh — Start gem-trader.
#
# Preferred path: systemd (if gem-trader.service is installed)
#   sudo systemctl start gem-trader
#
# Direct path: launch main.py directly under nohup (dev/paper sessions)
#   ./scripts/start_trader.sh
#
# Credentials live at /home/gem/secure/trading.env (never in the repo).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

VENV_PYTHON="$ROOT_DIR/.venv/bin/python3"
LOG_DIR="$ROOT_DIR/logs"
LOG_FILE="$LOG_DIR/gem-trader_$(date +%Y%m%d).log"
PID_FILE="$LOG_DIR/daemon.pid"
CRED_FILE="${SNIPER_ENV_PATH:-/home/gem/secure/trading.env}"

mkdir -p "$LOG_DIR"

is_pid_running() {
    local pid="${1:-}"
    [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]] && return 1
    kill -0 "$pid" 2>/dev/null
}

echo "=================================================================="
echo "  GEM-TRADER — STARTING"
echo "=================================================================="

# ── Prefer systemd if the service is installed ────────────────────────────────
if systemctl list-unit-files gem-trader.service &>/dev/null 2>&1; then
    echo "  Using systemd (gem-trader.service)"
    sudo systemctl start gem-trader
    sleep 2
    if systemctl is-active --quiet gem-trader; then
        echo "  OK: gem-trader.service active"
        echo "  Monitor: sudo journalctl -u gem-trader -f"
        echo "  Stop:    sudo systemctl stop gem-trader"
    else
        echo "  FAIL: service did not start. Check: sudo journalctl -u gem-trader -n 30"
        exit 1
    fi
    echo "=================================================================="
    exit 0
fi

# ── Direct launch (no systemd) ────────────────────────────────────────────────

# Step 1: Validate credentials file
echo "Step 1: Checking credentials..."
if [[ ! -f "$CRED_FILE" ]]; then
    echo "  FAIL: Credential file not found: $CRED_FILE"
    echo "  Set SNIPER_ENV_PATH or place credentials at /home/gem/secure/trading.env"
    exit 1
fi
echo "  OK: $CRED_FILE"

# Step 2: Stop any existing process
echo "Step 2: Stopping any existing trader..."
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if is_pid_running "$OLD_PID"; then
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
        is_pid_running "$OLD_PID" && kill -9 "$OLD_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
fi
# Catch any orphaned processes
ORPHANS=$(pgrep -f "python.*main\.py" 2>/dev/null || true)
if [[ -n "$ORPHANS" ]]; then
    echo "  Stopping orphaned processes: $ORPHANS"
    echo "$ORPHANS" | xargs -r kill 2>/dev/null || true
    sleep 2
    ORPHANS=$(pgrep -f "python.*main\.py" 2>/dev/null || true)
    [[ -n "$ORPHANS" ]] && echo "$ORPHANS" | xargs -r kill -9 2>/dev/null || true
fi
echo "  OK"

# Step 3: Load credentials and set runtime flags
echo "Step 3: Loading credentials..."
set -a
# shellcheck disable=SC1091
source "$CRED_FILE"
set +a
export ALPACA_PAPER="${ALPACA_PAPER:-true}"
export ALLOW_SHORTS="${ALLOW_SHORTS:-true}"
if [[ "$ALPACA_PAPER" == "true" ]]; then
    echo "  MODE: PAPER TRADING"
else
    echo "  !! MODE: LIVE TRADING !!"
fi

# Step 4: Start daemon
echo "Step 4: Launching daemon..."
SNIPER_ENV_PATH="$CRED_FILE" nohup "$VENV_PYTHON" -u main.py >> "$LOG_FILE" 2>&1 &
DAEMON_PID=$!
echo "$DAEMON_PID" > "$PID_FILE"

sleep 3
if is_pid_running "$DAEMON_PID"; then
    echo "  OK: PID $DAEMON_PID"
else
    echo "  FAIL: daemon died at startup. Last 30 lines:"
    tail -30 "$LOG_FILE"
    exit 1
fi

echo ""
echo "=================================================================="
echo "  GEM-TRADER RUNNING"
echo "  PID    : $DAEMON_PID"
echo "  Log    : $LOG_FILE"
echo "  Paper  : $ALPACA_PAPER"
echo ""
echo "  Monitor : tail -f $LOG_FILE"
echo "  Status  : ./scripts/check_status.sh"
echo "  Stop    : ./scripts/stop_trader.sh"
echo "=================================================================="
