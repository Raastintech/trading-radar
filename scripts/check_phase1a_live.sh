#!/usr/bin/env bash
# ============================================================
# scripts/check_phase1a_live.sh — confirm the running gem-trader process
# is on Phase 0 + Phase 1A code (submission-gate wiring + live-capital
# two-key gate).
#
# Reports three checks:
#   1. systemd shows gem-trader.service active.
#   2. The process start time is ≥ the mtime of main.py — i.e. the
#      running binary was launched AFTER the most recent main.py edit.
#   3. The journal shows a "PHASE0_WIRING ok" line emitted by the
#      current process, not a previous incarnation.
#
# Exits 0 if all three pass; 1 otherwise.  Designed to be safe to run
# at any time — it does NOT restart the service.  Restart is an
# operator action: ``sudo systemctl restart gem-trader``.
# ============================================================

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAIN_PY="${ROOT}/main.py"
SVC="gem-trader.service"

ok=0
fail=0

note()  { echo "  $*"; }
pass()  { echo "PASS $*"; ok=$((ok+1)); }
warn()  { echo "WARN $*"; }
err()   { echo "FAIL $*"; fail=$((fail+1)); }

echo "── Phase 1A liveness check ──"
echo "main.py:        ${MAIN_PY}"
echo

# 1. service active?
if systemctl is-active --quiet "${SVC}" 2>/dev/null; then
    pass "${SVC} is active"
else
    state="$(systemctl is-active "${SVC}" 2>&1 || true)"
    err "${SVC} not active (state=${state})"
fi

# 2. running PID start time vs main.py mtime
pid="$(systemctl show -p MainPID --value "${SVC}" 2>/dev/null || true)"
if [[ -n "${pid}" && "${pid}" != "0" ]]; then
    # /proc/<pid>/stat field 22 (starttime) is in clock ticks since boot;
    # easier: use ps -o lstart and date -d to compare to main.py mtime.
    proc_start_epoch="$(ps -o lstart= -p "${pid}" 2>/dev/null | xargs -I{} date -d "{}" +%s 2>/dev/null || true)"
    main_mtime_epoch="$(stat -c %Y "${MAIN_PY}" 2>/dev/null || echo 0)"
    if [[ -z "${proc_start_epoch}" ]]; then
        err "could not read PID ${pid} start time"
    elif [[ "${proc_start_epoch}" -ge "${main_mtime_epoch}" ]]; then
        pass "process pid=${pid} started after main.py last edit"
        note "  process_start = $(date -u -d @${proc_start_epoch} '+%Y-%m-%d %H:%M:%S UTC')"
        note "  main.py mtime = $(date -u -d @${main_mtime_epoch} '+%Y-%m-%d %H:%M:%S UTC')"
    else
        err "process started BEFORE main.py last edit — restart required"
        note "  process_start = $(date -u -d @${proc_start_epoch} '+%Y-%m-%d %H:%M:%S UTC')"
        note "  main.py mtime = $(date -u -d @${main_mtime_epoch} '+%Y-%m-%d %H:%M:%S UTC')"
        note "  remediation:  sudo systemctl restart ${SVC}"
    fi
else
    err "could not resolve MainPID for ${SVC}"
fi

# 3. journal contains PHASE0_WIRING line for the current process incarnation
if command -v journalctl >/dev/null 2>&1; then
    line="$(journalctl -u "${SVC}" --since "$(systemctl show -p ActiveEnterTimestamp --value "${SVC}" 2>/dev/null || echo '1 day ago')" --no-pager 2>/dev/null | grep -m1 'PHASE0_WIRING ok' || true)"
    if [[ -n "${line}" ]]; then
        pass "PHASE0_WIRING line present in current journal window"
        note "  ${line##*PHASE0_WIRING}"
    else
        err "no PHASE0_WIRING line in current journal — process may be running pre-Phase 1A code"
        note "  remediation:  sudo systemctl restart ${SVC}  &&  rerun this check"
    fi
else
    warn "journalctl not available — skipping log-line check"
fi

echo
if [[ "${fail}" -eq 0 ]]; then
    echo "OK  Phase 1A wiring verified live (${ok} checks)."
    exit 0
else
    echo "PROBLEM  ${fail} check(s) failed.  See FAIL lines above."
    exit 1
fi
