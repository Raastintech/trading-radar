#!/bin/bash
# check_status.sh — Trading system status (Linux/Ubuntu)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export SNIPER_ENV_PATH="${SNIPER_ENV_PATH:-/home/gem/secure/trading.env}"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python3"

echo "=================================================================="
echo "  GEM-TRADER V3 STATUS"
echo "=================================================================="
echo ""

is_pid_running() {
    local pid="${1:-}"
    [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]] && return 1
    kill -0 "$pid" 2>/dev/null
}

heartbeat_status() {
    "$VENV_PYTHON" - <<'PY'
from trading_state import get_daemon_status
print(get_daemon_status())
PY
}

heartbeat_summary() {
    "$VENV_PYTHON" - <<'PY'
import json
from datetime import datetime
from pathlib import Path
path = Path("trader_heartbeat.json")
if not path.exists():
    print("   heartbeat=missing")
    raise SystemExit(0)
with path.open() as f:
    hb = json.load(f)
last = hb.get("last_heartbeat_ts")
age = "unknown"
if last:
    try:
        age = str(int((datetime.now() - datetime.fromisoformat(last)).total_seconds())) + "s"
    except Exception:
        pass
print(f"   last_heartbeat={last} age={age} market_status={hb.get('market_status')} is_trading={hb.get('is_trading')}")
stage = hb.get("heartbeat_stage")
if stage:
    print(f"   stage={stage}")
PY
}

PID_FILE="$SCRIPT_DIR/daemon.pid"

if [[ -f "$PID_FILE" ]]; then
    PID=$(cat "$PID_FILE")
    HB_STATUS=$(heartbeat_status 2>/dev/null || echo "UNKNOWN")

    if [[ "$HB_STATUS" == "LIVE" || "$HB_STATUS" == "STALE" ]]; then
        echo "  System : $HB_STATUS"
        echo "  PID    : $PID"

        LOG_FILE=$(ls -t "$SCRIPT_DIR/logs/trader_v3_"*.log 2>/dev/null | head -1)
        if [[ -f "$LOG_FILE" ]]; then
            echo "  Log    : $LOG_FILE"
            echo ""
            echo "  Last 10 lines:"
            echo "------------------------------------------------------------------"
            tail -10 "$LOG_FILE"
        fi

        if [[ -f "$SCRIPT_DIR/trader_heartbeat.json" ]]; then
            echo ""
            echo "  Heartbeat:"
            heartbeat_summary 2>/dev/null || true
        fi
    else
        if is_pid_running "$PID"; then
            echo "  System : RUNNING (PID: $PID) — heartbeat: $HB_STATUS"
        else
            echo "  System : NOT RUNNING (stale PID file: $PID)"
        fi
    fi
else
    # Check if systemd service is active instead
    if systemctl is-active --quiet gem-trader 2>/dev/null; then
        echo "  System : RUNNING via systemd (gem-trader.service)"
        echo ""
        sudo journalctl -u gem-trader -n 15 --no-pager 2>/dev/null || true
    else
        echo "  System : NOT RUNNING (no PID file, gem-trader service not active)"
    fi
fi

echo ""
echo "=================================================================="
