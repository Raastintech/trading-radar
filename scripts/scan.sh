#!/usr/bin/env bash
# ============================================================
#  scan.sh — Multi-strategy ticker scanner (Linux/Ubuntu)
#
#  Usage:
#    ./scan.sh AAPL                     # single ticker
#    ./scan.sh AAPL MSFT TSLA NVDA      # multiple tickers
#    ./scan.sh                          # full EOD scan (adaptive universe)
#    ./scan.sh --force                  # full scan regardless of time
#
#  All strategies run on every ticker provided:
#    Voyager, Sniper, Remora, Short, Contrarian (Reaper)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export SNIPER_ENV_PATH="${SNIPER_ENV_PATH:-/home/gem/secure/trading.env}"

if [[ ! -f "$SNIPER_ENV_PATH" ]]; then
    echo "ERROR: Credential file not found: $SNIPER_ENV_PATH"
    exit 1
fi

set -a
# shellcheck disable=SC1091
source "$SNIPER_ENV_PATH"
set +a

VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python3"

if [[ $# -eq 0 ]]; then
    echo ""
    echo "Running full EOD scan (adaptive universe)..."
    echo ""
    "$VENV_PYTHON" eod_scanner_v3.py --force
else
    "$VENV_PYTHON" eod_scanner_v3.py --force "$@"
fi
