#!/usr/bin/env bash
# Phase 1G.13 + 1G.14 — ONE-SHOT local rerun of the gatekeeper precision audit and
# the power-trend extension study, after the 10d/20d forward windows mature.
#
# Armed for 2026-06-25 13:00 UTC (09:00 America/New_York, EDT) so the 10d/20d
# windows on the May/June "too_extended" cohort are matured and the POWER_TREND
# bucket can clear its 15-name evidence floor, then SELF-DISARMS from the crontab
# so it never repeats. Cache-only / research-only: it does NOT change the
# Gatekeeper, Veto Council, universe, strategy gates, strategy registry, execution,
# governance, or live-capital settings, and creates no paper signals / proposals.
#
# Re-arm (if needed): re-add the crontab line tagged `power_trend_extension_oneshot`.
# Disarm early: `crontab -e` and delete that line.
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"
ROOT="/home/gem/trading-production"
MARKER="power_trend_extension_oneshot"     # crontab tag used for self-disarm
LOG="$ROOT/logs/power_trend_extension_oneshot_$(date -u +%Y%m%d).log"

cd "$ROOT" || exit 1

{
  echo "== power-trend / gatekeeper-precision one-shot rerun $(date -u +%FT%TZ) =="

  echo "-- refreshing cache-only audits --"
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \
    .venv/bin/python -m research.gatekeeper_precision_audit
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \
    .venv/bin/python -m research.power_trend_extension_study

  echo "-- matured-window summary --"
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python - <<'PY'
import json

def load(p):
    try:
        return json.load(open(p))
    except Exception as e:
        print("could not read", p, "->", e)
        return {}

gk = load("cache/research/gatekeeper_precision_audit_latest.json")
bh = gk.get("by_horizon") or {}
def gkrel(h, k):
    return ((bh.get(h) or {}).get(k) or {}).get("mean_rel_spy")
print("== GATEKEEPER PRECISION (1G.13) ==")
print("verdict        :", (gk.get("verdict") or {}).get("gatekeeper_verdict"),
      "-> rec", (gk.get("verdict") or {}).get("recommendation"))
for h in ("5d", "10d", "20d"):
    print(f"{h:>4} block={gkrel(h,'A_blocked')} watch={gkrel(h,'B_watch_not_blocked')}",
          "matured_block=", ((bh.get(h) or {}).get("A_blocked") or {}).get("n_matured"))

pt = load("cache/research/power_trend_extension_latest.json")
ph = pt.get("by_horizon_label") or {}
def ptrel(h, lbl):
    return ((ph.get(h) or {}).get(lbl) or {}).get("mean_rel_spy")
co = pt.get("cohort") or {}
print()
print("== POWER-TREND EXTENSION (1G.14) ==")
print("verdict           :", (pt.get("verdict") or {}).get("power_trend_verdict"),
      "-> rec", (pt.get("verdict") or {}).get("recommendation"))
print("cohort n          :", co.get("n_total"),
      "| matured POWER_TREND 5d:", co.get("n_power_trend_matured_primary"),
      "(floor", pt.get("min_matured_for_verdict"), ")")
for h in ("5d", "10d", "20d"):
    print(f"{h:>4} power={ptrel(h,'POWER_TREND_EXTENSION')}",
          f"climax={ptrel(h,'CLIMAX_CHASE_EXTENSION')}",
          f"wait={ptrel(h,'EXTENDED_BUT_WAIT_FOR_RESET')}",
          f"lowq={ptrel(h,'LOW_QUALITY_EXTENSION')}",
          f"random={ptrel(h,'RANDOM_CONTROL')}")
print()
print("FLOOR CLEARED:", (co.get("n_power_trend_matured_primary") or 0)
      >= (pt.get("min_matured_for_verdict") or 1))
PY
} >>"$LOG" 2>&1

# Self-disarm: drop our own crontab line so this fires exactly once.
( crontab -l 2>/dev/null | grep -v "$MARKER" | crontab - ) || true
echo "disarmed crontab line ($MARKER) at $(date -u +%FT%TZ)" >>"$LOG"
