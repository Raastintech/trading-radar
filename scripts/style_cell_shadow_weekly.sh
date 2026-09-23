#!/usr/bin/env bash
# Weekly snapshot for the style_cell_leader_pool forward shadow (research-only).
#
# The ledger only earns its floor by accruing unseen forward sessions. It sat
# at one accepted session (2026-09-10) for two weeks because the cadence was
# "manual" and nobody ran it, so the lane was registered as collecting while
# collecting nothing. This is that cadence.
#
# ZERO PROVIDER CALLS, by construction and not by convention:
#   * GEM_TRADER_SKIP_DOTENV=true — no credentials are loaded;
#   * research/agent_lab/forward_shadow.py imports only stdlib, numpy, pandas
#     and research.backtests.common (itself stdlib-only), so there is no
#     provider client in the import graph at all;
#   * the module calls offline_env() before parsing argv;
#   * `run` reads cached parquet and writes the append-only ledger. The
#     `plan-refresh` subcommand only *names* the symbols a refresh would need
#     and delegates — it never fetches — and this script never invokes it.
#   * tests/unit/test_agent_lab_forward_shadow.py runs a session with
#     socket.socket removed and asserts it completes.
#
# Deliberately NOT passed, and not to be added here:
#   --allow-backdate   a stale cache must leave a visible gap, never a row
#                      backfilled into forward evidence
#   --max-calls        a refresh-plan override; this path plans no refresh
#   any provider or credential override of any kind
#
# A refusal is a correct outcome, not a failure. If the freshness or depth
# gates fail, the module records a REFUSED row and stops; the gap is then in
# the ledger rather than silently absent. The unit stays green so a genuine
# breakage is still distinguishable from an expected stale-cache week.
set -euo pipefail

ROOT="/home/gem/trading-production"
VENV="${ROOT}/.venv/bin/python"
SOURCE="live_plus_replay_cache"

cd "${ROOT}"

echo "=== style-cell leader forward shadow — weekly snapshot ($(date -u +%FT%TZ)) ==="
echo "    source=${SOURCE} · cache-only · zero provider calls · no price refresh"
echo

# No --quiet: the refusal reason belongs in the log when a gate fails.
GEM_TRADER_SKIP_DOTENV=true "${VENV}" -m research.agent_lab.forward_shadow \
  run --source "${SOURCE}"

echo
echo "=== ledger status ==="
GEM_TRADER_SKIP_DOTENV=true "${VENV}" - <<'PY'
import json
p = "cache/research/agent_lab/style_cell_leader_forward_latest.json"
try:
    s = json.load(open(p))
except FileNotFoundError:
    raise SystemExit(f"no artifact at {p} — the run wrote nothing")
m = s.get("maturity", {})
floor = m.get("floor", {})
print(f"  verdict           : {s.get('verdict')}")
print(f"  latest session    : {s.get('latest_session')} [{s.get('latest_status')}]")
if s.get("latest_refusal_reason"):
    print(f"  refusal reason    : {s['latest_refusal_reason']}")
print(f"  accepted sessions : {s.get('accepted_ok_sessions')} "
      f"(refused {s.get('sessions_refused')}, retracted {s.get('retracted_snapshots')})")
print(f"  matured at {s.get('primary_horizon', 60)}d    : "
      f"{m.get('matured_snapshots_at_primary_horizon')} of "
      f"{floor.get('matured_snapshots_at_primary_horizon')} needed · "
      f"floor reached: {m.get('floor_reached')}")
print(f"  provider calls    : {s.get('provider_calls')}")
PY
