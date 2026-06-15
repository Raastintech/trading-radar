#!/bin/bash
# scripts/restart_trader.sh — Restart gem-trader (stop then start).
#
# Useful after a config change or code update.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=================================================================="
echo "  GEM-TRADER — RESTARTING"
echo "=================================================================="

# ── Prefer systemd if available ───────────────────────────────────────────────
if systemctl list-unit-files gem-trader.service &>/dev/null 2>&1; then
    echo "  Restarting gem-trader.service via systemd..."
    sudo systemctl restart gem-trader
    sleep 2
    if systemctl is-active --quiet gem-trader; then
        echo "  OK: gem-trader.service restarted"
    else
        echo "  FAIL: service did not restart. Check: sudo journalctl -u gem-trader -n 30"
        exit 1
    fi
    echo "=================================================================="
    exit 0
fi

# ── Direct restart ─────────────────────────────────────────────────────────────
"$SCRIPT_DIR/stop_trader.sh"
sleep 2
"$SCRIPT_DIR/start_trader.sh"
