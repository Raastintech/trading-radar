#!/bin/bash
# scripts/check_status.sh — gem-trader operational status.
#
# Shows:
#  • Whether the daemon is running (systemd or direct PID)
#  • Current session state (PREMARKET / REGULAR / POSTMARKET / CLOSED)
#  • Heartbeat age and stage
#  • Last 15 log lines

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

VENV_PYTHON="$ROOT_DIR/.venv/bin/python3"
PID_FILE="$ROOT_DIR/logs/daemon.pid"
HB_FILE="$ROOT_DIR/logs/trader_heartbeat.json"
PAPER_STATUS_FILE="$ROOT_DIR/logs/paper_evidence_status.json"

is_pid_running() {
    local pid="${1:-}"
    [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]] && return 1
    kill -0 "$pid" 2>/dev/null
}

echo "=================================================================="
echo "  GEM-TRADER STATUS"
echo "=================================================================="
echo ""

# ── Session state (always shown, even if daemon is down) ──────────────────────
SESSION_INFO=$(GEM_TRADER_SKIP_DOTENV=true "$VENV_PYTHON" -c "
from core.session import get_session_state, next_session_change
s = get_session_state()
ns, nt = next_session_change()
print(f'{s.value}  next={ns.value} at {nt.strftime(\"%H:%M\")} ET')
" 2>/dev/null || echo "UNKNOWN")
echo "  Session  : $SESSION_INFO"

# ── Daemon running? ───────────────────────────────────────────────────────────
DAEMON_RUNNING=false

if systemctl is-active --quiet gem-trader 2>/dev/null; then
    DAEMON_RUNNING=true
    SVC_PID=$(systemctl show gem-trader --property=MainPID --value 2>/dev/null || echo "?")
    echo "  Daemon   : RUNNING via systemd (PID: $SVC_PID)"
elif [[ -f "$PID_FILE" ]]; then
    PID=$(cat "$PID_FILE")
    if is_pid_running "$PID"; then
        DAEMON_RUNNING=true
        echo "  Daemon   : RUNNING (PID: $PID)"
    else
        echo "  Daemon   : NOT RUNNING (stale PID file: $PID)"
    fi
else
    ORPHAN=$(pgrep -f "python.*main\.py" 2>/dev/null | head -1 || true)
    if [[ -n "$ORPHAN" ]]; then
        DAEMON_RUNNING=true
        echo "  Daemon   : RUNNING (orphan PID: $ORPHAN — no PID file)"
    else
        echo "  Daemon   : NOT RUNNING"
    fi
fi

# ── Heartbeat ─────────────────────────────────────────────────────────────────
if [[ -f "$HB_FILE" ]]; then
    HB_INFO=$(GEM_TRADER_SKIP_DOTENV=true "$VENV_PYTHON" -c "
import json, sys
from datetime import datetime, timezone
from pathlib import Path
hb = json.loads(Path('$HB_FILE').read_text())
ts = hb.get('last_heartbeat_ts','')
age = '?'
if ts:
    try:
        dt = datetime.fromisoformat(ts)
        age = str(int((datetime.now(timezone.utc) - dt).total_seconds())) + 's'
    except Exception:
        pass
stage = hb.get('heartbeat_stage', hb.get('session_state','?'))
source = (hb.get('universe') or {}).get('source', '?')
halt_part = ''
if hb.get('halted'):
    reason = (hb.get('halt_reason') or '')[:60]
    halt_part = f'  HALTED!  reason={reason!r}'
print(f'age={age}  stage={stage}  is_trading={hb.get(\"is_trading\",\"?\")}  universe={source}{halt_part}')
" 2>/dev/null || echo "parse error")
    echo "  Heartbeat: $HB_INFO"
else
    echo "  Heartbeat: no file (daemon not yet run)"
fi

# ── Paper evidence loop ───────────────────────────────────────────────────────
echo ""
if [[ -f "$PAPER_STATUS_FILE" ]]; then
    PAPER_INFO=$(GEM_TRADER_SKIP_DOTENV=true "$VENV_PYTHON" -c "
import json
from datetime import datetime, timezone
from pathlib import Path
p = Path('$PAPER_STATUS_FILE')
st = json.loads(p.read_text())
finished = st.get('finished_at') or ''
age = '?'
if finished:
    try:
        dt = datetime.fromisoformat(finished)
        age = str(int((datetime.now(timezone.utc) - dt).total_seconds() // 60)) + 'm'
    except Exception:
        pass
resolver = st.get('resolver_result') or {}
print(
    f\"ok={st.get('ok')} age={age} finished={finished[:19]} \"
    f\"last_success={(st.get('last_success_at') or '')[:19] or '?'} \"
    f\"last_failure={(st.get('last_failure_at') or '')[:19] or '?'} \"
    f\"resolver_ok={st.get('resolver_ok')} scoreboard_ok={st.get('scoreboard_ok')} \"
    f\"signals={resolver.get('signals_seen', '?')} outcomes={resolver.get('outcomes_updated', '?')} \"
    f\"error={st.get('error') or 'none'}\"
)
" 2>/dev/null || echo "parse error")
    echo "  Paper    : $PAPER_INFO"
    echo "  Paper rpt: $ROOT_DIR/logs/paper_scoreboard_latest.txt"
else
    echo "  Paper    : no paper evidence status yet"
fi

# ── Recent logs ───────────────────────────────────────────────────────────────
if [[ "$DAEMON_RUNNING" == "true" ]]; then
    echo ""
    # systemd journal (preferred)
    if systemctl is-active --quiet gem-trader 2>/dev/null; then
        echo "  Recent log (journalctl):"
        echo "------------------------------------------------------------------"
        sudo journalctl -u gem-trader -n 15 --no-pager 2>/dev/null || true
    else
        # Latest log file
        LOG_FILE=$(ls -t "$ROOT_DIR/logs/gem-trader"*.log 2>/dev/null | head -1)
        if [[ -f "$LOG_FILE" ]]; then
            echo "  Log: $LOG_FILE"
            echo ""
            echo "  Recent log:"
            echo "------------------------------------------------------------------"
            tail -15 "$LOG_FILE"
        fi
    fi
fi

echo ""
echo "=================================================================="
echo "  Commands:"
echo "    Start   : ./scripts/start_trader.sh"
echo "    Stop    : ./scripts/stop_trader.sh"
echo "    Restart : ./scripts/restart_trader.sh"
echo "    Log     : tail -f logs/gem-trader.log"
echo "    Paper   : tail -f logs/gem-trader-paper-evidence.log"
echo "=================================================================="
