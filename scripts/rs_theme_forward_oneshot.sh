#!/usr/bin/env bash
# Phase 1G.11 — ONE-SHOT local rerun of the RS/theme forward validation.
#
# Armed for 2026-06-03 13:00 UTC (09:00 America/New_York, EDT) to capture the
# first matured (5d) reading of the frozen 1G10 cohort, then SELF-DISARMS from
# the crontab so it never repeats. Cache-only / research-only: it does NOT freeze
# a new cohort, create paper signals or trade proposals, or change the universe,
# strategy gates, execution, governance, or live-capital settings.
#
# Re-arm (if needed): re-add the crontab line shown in scripts (grep marker
# `rs_theme_forward_oneshot`). Disarm early: `crontab -e` and delete that line.
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"
ROOT="/home/gem/trading-production"
MARKER="rs_theme_forward_oneshot"          # crontab tag used for self-disarm
LOG="$ROOT/logs/rs_theme_forward_oneshot_$(date -u +%Y%m%d).log"

cd "$ROOT" || exit 1

{
  echo "== RS/theme forward one-shot rerun $(date -u +%FT%TZ) =="
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \
    .venv/bin/python -m research.rs_theme_forward_validation
  echo "-- first-matured summary --"
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python - <<'PY'
import json
d = json.load(open("cache/research/rs_theme_forward_validation_latest.json"))
ph = (d.get("by_horizon") or {}).get("5d", {}) or {}
def rel(k):
    return (ph.get(k) or {}).get("mean_rel_spy") if ph.get(k) else None
print("verdict                :", d.get("verdict"))
print("matured_lens_ready_5d  :", d.get("n_matured_lens_ready_primary"))
print("5d rel-SPY A_lens_ready:", rel("A_lens_ready"))
print("5d rel-SPY B_blocked   :", rel("B_blocked"))
print("5d rel-SPY C_alpha     :", rel("C_alpha_board"))
print("5d rel-SPY D_random    :", rel("D_random_control"))
PY
} >>"$LOG" 2>&1

# Self-disarm: drop our own crontab line so this fires exactly once.
( crontab -l 2>/dev/null | grep -v "$MARKER" | crontab - ) || true
echo "disarmed crontab line ($MARKER) at $(date -u +%FT%TZ)" >>"$LOG"
