#!/usr/bin/env bash
# Weekly upkeep for the frozen M1 manual-pick cohort (research-only).
#
# Two steps, in order:
#   1. refresh ONLY the cohort's own tickers in cache/replay_prices
#      (~75 calls, versus 3,269 for the whole replay universe), so the
#      forward horizons can be measured when they mature;
#   2. resolve the cohort — cache-only, zero provider calls.
#
# It deliberately does NOT run the shadow pool. A pool run needs the whole
# universe inside the guard's 2-session cache-lag limit, which is a 3,269-call
# refresh; the cohort is what is being tracked, and it is self-contained.
#
# The rule is NOT re-derived here and cannot be: the cohort is write-once, and
# `freeze` refuses to overwrite it. Early results cannot feed back into it.
set -euo pipefail

ROOT="/home/gem/trading-production"
VENV="${ROOT}/.venv/bin/python"
ENV_PATH="${SNIPER_ENV_PATH:-/home/gem/secure/trading.env}"
SESSION="${1:-2026-09-04}"
COHORT="${ROOT}/research/backtests/cohorts/m1_manual_picks_${SESSION}.json"

cd "${ROOT}"

if [[ ! -f "${COHORT}" ]]; then
  echo "no frozen cohort at ${COHORT} — nothing to track." >&2
  exit 1
fi

TICKERS="$(GEM_TRADER_SKIP_DOTENV=true "${VENV}" - "${COHORT}" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
names = [p["ticker"] for p in c["picks"]] + [x["ticker"] for x in c["control_not_picked"]]
print(",".join(sorted(set(names))))
PY
)"

echo "=== M1 cohort refresh — session ${SESSION}, $(tr -cd ',' <<<"${TICKERS}" | wc -c) tickers +1 ==="
SNIPER_ENV_PATH="${ENV_PATH}" "${VENV}" -m research.backtests.m1_price_refresh \
  fetch --execute-fetch --max-calls 100 --only-tickers "${TICKERS}"

echo
echo "=== M1 cohort resolve — cache-only ==="
GEM_TRADER_SKIP_DOTENV=true "${VENV}" -m research.backtests.m1_manual_pick_tracker \
  resolve --session "${SESSION}"
