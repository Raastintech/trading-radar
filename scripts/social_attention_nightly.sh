#!/usr/bin/env bash
# Phase 1G.15B — LOCAL after-close Social Attention cadence (Reddit-free).
#
# Runs once per trading day after the US close to accumulate Social Attention
# history. RESEARCH-ONLY: no paper signals, no trade proposals, no execution /
# governance / live-capital changes, no dashboard provider calls. Reddit stays
# DISABLED (safe-nightly profile). StockTwits is the primary live source
# (cap 75 / hard max 100), Google Trends is auxiliary/low-cap, manual JSONL is
# the operator fallback.
#
# Degrade-safe by design: each step is independent and a failure in one NEVER
# aborts the others or the wrapper (always exits 0 so it can't wedge cron or
# affect unrelated jobs). A non-blocking flock prevents overlapping runs.
#
# Manual run:   ./scripts/social_attention_nightly.sh
# Schedule:     cron @ 21:30 UTC Mon-Fri (after 16:00 ET close year-round).

set -u
set -o pipefail

REPO="/home/gem/trading-production"
cd "$REPO" || exit 0

export SNIPER_ENV_PATH="${SNIPER_ENV_PATH:-/home/gem/secure/trading.env}"
RUNNER="$REPO/scripts/run_research_cycle.sh"
LOG="$REPO/logs/social_attention_nightly_$(date -u +%Y%m%d).log"
LOCK="$REPO/logs/.social_attention_nightly.lock"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG"; }

# Non-blocking lock: if a prior run is still going, log and bail (exit 0).
exec 9>"$LOCK" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then
    if ! flock -n 9; then
        log "SKIP: previous run still holding lock"
        exit 0
    fi
fi

# Each step guarded so one failure can't stop the next. ">>" appends to today's
# log; "|| true" + explicit WARN keeps the wrapper alive.
run_step() {
    local label="$1"; shift
    log "START $label :: $*"
    if "$@" >> "$LOG" 2>&1; then
        log "OK    $label"
    else
        log "WARN  $label exited non-zero (continuing; degrade-safe)"
    fi
}

log "=== social attention nightly cadence (safe-nightly, Reddit-free) ==="
run_step "collect"          "$RUNNER" social-attention --profile safe-nightly
run_step "forward"          "$RUNNER" social-attention-forward
run_step "mcp-audit-session" "$RUNNER" mcp-audit-session regular

# Confirm the source-health audit was written this run (visibility only).
AUDIT="$REPO/cache/research/social_attention_source_audit_latest.json"
if [[ -f "$AUDIT" ]]; then
    log "source_health audit present: $AUDIT"
else
    log "WARN  source_health audit missing after run"
fi
log "=== done ==="
exit 0
