#!/usr/bin/env bash
# ============================================================
# scripts/run_research_cycle.sh — Research workflow wrapper (Phases 5–7)
#
# Subcommand-driven runner for the research command center.  This is the
# only place provider calls happen for the daily cycle.  The dashboard
# reads cache only and never invokes this script.
#
# CADENCE ENFORCEMENT (Phase 6):
#   Social Arb runs daily (Mon-Fri) after market close.  Previous 3x/week
#   cadence published headlines that were 2-3 trading days stale by the
#   next render — news-catalyst signals decay in hours, not days.
#   Override with --force-social, or RESEARCH_SOCIAL_DAYS="d1,d2,..."
#   (ISO weekdays, 1=Mon … 7=Sun).  The nightly research timer only
#   fires Mon-Fri, so weekend cadence days will never actually run.
#
# COMMANDS:
#   nightly      — daily research cycle (after market close).
#                  forecast (provider) + alpha discovery (provider) +
#                  social arb on cadence days only (provider) +
#                  delta (cache only) + lenses-nightly (provider, capped) +
#                  resolver (cache only) + holdout report (cache only).
#                  flags: --force-social
#
#   premarket    — pre-open lite cycle.
#                  forecast (provider) + alpha discovery (provider) +
#                  alpha-overlay (provider, --mode premarket) + delta (cache).
#                  no social arb, no resolver, no lens prebuild.
#
#   midday       — cache-only intraday refresh.
#                  resolve + reports + delta + risk-telemetry.  no provider
#                  calls.  safe to fire at any time during regular hours.
#
#   resolve      — cache-only.  resolve forward outcomes from parquets and
#                  write summary reports.  no provider calls.
#
#   reports      — cache-only.  write summaries from existing ledgers.
#                  no resolver pass, no provider calls.
#
#   holdout      — cache-only. write the pre-registered 2026 H2 holdout
#                  daily status report. no provider calls, no DB writes.
#
#   forecast     — run regime forecast only (provider).
#
#   options-regime — Phase 1G.12: research-only Options Regime Lens
#                  (provider — options chains for SPY/QQQ/IWM/VXX).  Computes a
#                  GEX proxy, put/call skew, IV rank/percentile, term structure
#                  and a consolidated OPTIONS_REGIME label.  NOT a strategy, NOT
#                  a veto, NOT an approval.  Writes
#                  cache/research/options_regime_lens_latest.json +
#                  logs/options_regime_lens_latest.txt +
#                  data/research/options_regime_lens_history.jsonl.  Emits no
#                  signals, mutates no DB rows, touches no governance/execution.
#                  Runs in premarket + nightly (provider cycles); the dashboard
#                  and MCP only read the sidecar.  Flags: symbols…, --print.
#
#   options-chain-snapshot
#                — Phase 1J.1/1J.2: point-in-time options chain snapshot
#                  collection (provider).  DATA_COLLECTION_ONLY: no strategy,
#                  no signals, no orders, no proposals.  Pulls current chains
#                  (20-70 DTE, 45 target) for the capped liquid universe via
#                  the shared Alpaca+Tradier feed and appends
#                  data/options_snapshots/YYYY-MM-DD/<UNDERLYING>.parquet
#                  (idempotent per symbol/day — never overwrites).  Then runs
#                  the cache-only quality audit + health check.  Exits nonzero
#                  only on real collector failure.  Honors --dry-run (zero
#                  provider calls).  Driven by the Mon-Fri 15:45 ET timer
#                  (gem-trader-options-snapshot.timer).
#
#   options-chain-snapshot-quality
#                — cache-only.  Re-run the snapshot quality audit + health
#                  check from persisted parquets.  No provider calls.
#
#   alpha        — run Alpha Discovery board only (provider).
#
#   alpha-overlay — run Alpha Discovery premarket overlay only (provider,
#                  --mode premarket).  Writes alpha_discovery_overlay_latest.
#
#   social       — run Social Arb radar (provider).  honors cadence;
#                  use --force-social to override.
#
#   delta        — cache-only.  diff latest research artifacts vs the
#                  previous snapshot under cache/research/history/.
#                  writes cache/research/research_delta_latest.json +
#                  logs/research_delta_latest.txt.  no provider calls.
#
#   lenses-nightly
#                — provider.  prebuild Stock Lens artifacts for an
#                  auto-curated short list (alpha A/B + posture focus +
#                  structural READY/WATCH + social high-quality + open
#                  positions).  default cap 25.  skips tickers with a
#                  fresh (<24h) lens unless --force.  dry-run shows the
#                  plan only.  flags: --force, --max=N, --dry-run,
#                                       --no-positions, --liquid-top=N
#                  --liquid-top=N appends top-N most liquid US stocks
#                  (by 20d ADV) as a coverage tier.  Pair with a matching
#                  --max so the run actually builds them.
#
#   lenses-liquid [N]
#                — provider.  shortcut for a top-liquid coverage run:
#                  equivalent to 'lenses-nightly --liquid-top=N --max=N'
#                  with N defaulting to 100.  curated sources still union
#                  in (positions / alpha / posture / structural / social).
#
#   alpha-lens-refresh [--max=N] [prebuild flags…]
#                — provider.  Refresh Stock Lens artifacts for the final
#                  Alpha Discovery board top-N (default 20) and re-annotate
#                  the saved board + overlay JSON.  Restricts the prebuild
#                  sources to alpha + positions so the cap binds the alpha
#                  list, not the broader curated pool.  Cache-only after the
#                  prebuild — the re-annotation reads stock_lens_<TICKER>_
#                  latest.json and the optional executive_gatekeeper_*
#                  sidecars.  Never mutates execution / governance / paper
#                  evidence.
#
#   lens TICKER…  — run Single-Stock Research Lens for each ticker
#                  (provider), then resolve + report.
#
#   gatekeeper TICKER…
#                — cache-only.  Run Executive Gatekeeper V1 for each ticker
#                  (research/executive_gatekeeper_report.py).  Reads cached
#                  research artefacts + local DB; no provider calls.  Writes
#                  cache/research/executive_gatekeeper_<TICKER>_latest.json
#                  + logs/executive_gatekeeper_<TICKER>_latest.txt.  The
#                  dashboard never auto-runs this — invoke from the CLI.
#
#   gatekeeper-refresh
#                — cache-first.  Phase 2B.2: batched Executive Gatekeeper
#                  refresh.  Selects up to 25 tickers from open positions,
#                  top Alpha Discovery candidates, tickers with earnings
#                  in the next 5 days, missing/stale Gatekeeper artifacts,
#                  and any --watch tickers.  Runs Gatekeeper for each;
#                  writes per-ticker artifacts.  Does not auto-run from
#                  the dashboard.  Does not mutate paper evidence.
#                  Forwardable flags (passed through to
#                  research/gatekeeper_refresh.py):
#                    --max N            (default 25)
#                    --earnings-days N  (default 5)
#                    --watch TICKER…
#                    --dry-run          plan only, no Gatekeeper runs
#                    --skip-earnings    skip FMP earnings-calendar source
#                    --with-llm-summary forward to the Gatekeeper
#
#                  Phase 2B.3 cadence wiring: runs automatically inside
#                  `premarket` (after delta) and `nightly` (after
#                  lenses-nightly, before risk-telemetry).  The
#                  `mcp-audit-session` subcommand does NOT refresh by
#                  default; pass `--refresh-gatekeeper` to opt in.
#
# FRESHNESS-FIRST OPERATOR ORDER (Phase 2B.3):
#   1. forecast / alpha / lens refresh    (provider)
#   2. gatekeeper-refresh                 (cache-first; FMP earnings cal only)
#   3. risk-telemetry                     (cache-only)
#   4. mcp-audit-session                  (cache-only)
#   5. dashboard review                   (cache-only; never auto-runs anything)
#   The premarket / nightly subcommands above bake in steps 1-3 (and
#   nightly also runs 3).  Operators run step 4 ad-hoc or via
#   `mcp-audit-session regular --refresh-gatekeeper`.
#
#   provider-audit
#                — cache-only.  Phase 2B.4: inspect cache_meta /
#                  fmp_endpoint_log / fmp_budget_monthly and the per-
#                  pipeline JSON sidecars to predict tomorrow's
#                  premarket FMP load and verify the dashboard /
#                  research pipelines are fresh.  Never invokes a
#                  provider; never mutates DB / paper evidence /
#                  governance.  Writes
#                  cache/research/provider_freshness_audit_latest.json
#                  + logs/provider_freshness_audit_latest.txt.
#                  Forwardable flags:
#                    --ticker TICKER   per-ticker freshness check
#                    --print           also print prose to stdout
#                    --json            also print machine-readable JSON
#
#   risk-telemetry
#                — cache-only.  Phase 1B/1C/1D: paper-only risk diagnostics +
#                  paper-state hygiene.  runs slippage telemetry +
#                  portfolio concentration + shadow vol-target sizing &
#                  short-borrow drag + paper-state hygiene.  reads
#                  decisions / paper_signals / paper_signal_outcomes /
#                  voyager_paper_signals / veto_log / cached parquets only.
#                  writes cache/research/{slippage_telemetry,
#                  portfolio_concentration, shadow_sizing,
#                  paper_state_hygiene}_latest.json + matching logs/*.txt
#                  + data/state/paper_legacy_quarantine.json.
#                  no provider calls, no enforcement.
#                  Each report defaults to the clean-paper-evidence epoch
#                  (core.paper_evidence_epoch.CLEAN_PAPER_EVIDENCE_START)
#                  and publishes both ready_to_gate_all (legacy debt
#                  visible) and ready_to_gate_clean (clean-epoch only).
#                  Forwardable flags (passed to every sub-report):
#                    --since YYYY-MM-DDTHH:MM:SS+00:00   override the
#                                                       clean-epoch start
#
#   mcp-audit    — cache-only.  Phase 2B: run the MCP audit workflow
#                  bundle (daily_dashboard + late_chase + system_health).
#                  Composes audit_mcp helpers; no provider calls, no DB
#                  writes.  Outputs cache/research/mcp_audit_{daily,
#                  late_chase,system_health}_latest.{json,txt}.  Sidecars
#                  are net-new (mcp_audit_* namespace) — existing
#                  research artifacts are never modified.
#
#   mcp-audit-ticker TICKER…
#                — cache-only.  Phase 2B: run ticker_consistency_audit for
#                  one or more tickers.  Outputs
#                  cache/research/mcp_audit_<TICKER>_latest.{json,txt}.
#                  No provider calls, no DB writes.
#
#   mcp-audit-session [open|regular|close]
#                — cache-only.  Phase 2B.1: run the orchestrated session
#                  audit — daily_dashboard + system_health + late_chase
#                  plus auto-drilldown into the top anomaly tickers, then
#                  write the structured analysis sidecar
#                  cache/research/mcp_analysis_latest.json and the
#                  markdown summaries logs/mcp_audit_daily_latest.md +
#                  logs/mcp_audit_daily_<TIMESTAMP>.md.  Session label
#                  defaults to 'regular'.  No provider calls, no DB writes.
#
#   scanner-recall — cache-only.  Phase 1G.6: Scanner Recall Repair package.
#                  Runs emission-gap audit + RS recall lane + theme leadership
#                  radar + cap audit + price-cache coverage, then an aggregate
#                  summary.  Outputs cache/research/scanner_recall_repair_latest
#                  .json + logs/scanner_recall_repair_latest.txt (and each
#                  sub-report's own sidecar).  Research-only; no provider calls,
#                  no DB writes, no signals.  NOT wired into any timer.
#
#   rs-theme-triage — cache-only.  Phase 1G.9: routes RS-lane + LEADING-theme +
#                  proposed-dynamic early leaders to the Stock Lens/Gatekeeper as
#                  a research-only triage surface (bypasses the Voyager/Sniper
#                  score gates by DESIGN, changes neither).  Assigns routing
#                  triage labels, decomposes gate rejections, historizes forward.
#                  Outputs cache/research/rs_theme_lens_triage_latest.json +
#                  logs/...txt + docs/research/RS_THEME_LENS_TRIAGE.md.  No
#                  provider calls, no DB writes, no signals.  NOT wired to a timer.
#
#   rs-theme-forward — cache-only.  Phase 1G.11: forward-validates the frozen
#                  1G.10 LENS_READY/BLOCKED cohort vs the Alpha board, random
#                  liquid controls, a simple RS-top baseline, and SPY/QQQ; runs the
#                  Gatekeeper-precision + options-quality audits and emits a gated
#                  verdict (NEED_MORE_DATA / NO_VALUE / PROMISING_BUT_UNPROVEN /
#                  FORWARD_EDGE_DETECTED / READY_TO_ROUTE_TO_LENS_DAILY).  Pass
#                  --freeze-cohort to (re)freeze the immutable cohort.  Outputs
#                  cache/research/rs_theme_forward_validation_latest.json + logs +
#                  docs/research/RS_THEME_FORWARD_VALIDATION.md.  No provider calls,
#                  no DB writes, no signals.  NOT wired to a timer.
#
#   gatekeeper-precision — cache-only.  Phase 1G.13: autopsies every Executive-
#                  Gatekeeper BLOCK (per-ticker snapshots + frozen RS/theme 1G.10
#                  cohort) forward vs the not-blocked WATCH set, random/RS-top/Alpha
#                  controls and SPY; groups blocks by reason, isolates cache-depth
#                  artifacts, runs a research-only rescue simulation, and emits a
#                  gatekeeper_verdict + recommendation (A-E).  Outputs
#                  cache/research/gatekeeper_precision_audit_latest.json + logs +
#                  docs/research/GATEKEEPER_PRECISION_AUDIT.md.  No provider calls,
#                  no DB writes, no signals, no gate change.  NOT wired to a timer.
#
#   power-trend — cache-only.  Phase 1G.14: too-extended block audit.  Classifies
#                  every "too_extended"-flagged name into POWER_TREND / CLIMAX_CHASE
#                  / WAIT_FOR_RESET / LOW_QUALITY, measures forward outcomes vs random
#                  and by theme (semis/hardware/space/etc.), and emits a gated
#                  power_trend_verdict + recommendation (A-D) plus PROPOSED future
#                  Gatekeeper fields.  Outputs
#                  cache/research/power_trend_extension_latest.json + logs +
#                  docs/research/POWER_TREND_EXTENSION_STUDY.md.  No provider calls,
#                  no DB writes, no signals, no gate change.  NOT wired to a timer.
#
#   weekly-review — cache-only.  Phase 8B: resolve forward outcomes,
#                  rewrite forward summaries, then build the weekly
#                  mistake / false-confidence review.  Writes
#                  cache/research/weekly_review_latest.json +
#                  logs/weekly_review_latest.txt.  No provider calls.
#                  flags forwarded to review_misses.py:
#                    --lookback-days=N  (default 30)
#                    --print-text       (also print to stdout)
#
#   participation-audit — Phase 1G.17: cache-only scan→council→decision flow
#                  audit per active sleeve (daemon log + read-only DB).
#                  Writes participation_bottleneck_audit_latest.{json,txt} +
#                  docs/research/PARTICIPATION_BOTTLENECK_AUDIT.md.
#
#   sniper-starvation — Phase 1G.17: cache-only SNIPER gate-confluence
#                  autopsy + counterfactual relaxation replay. Thresholds
#                  NOT changed.
#
#   voyager-cache-audit — Phase 1G.17: cache-only VOYAGER starvation +
#                  cache-depth audit (true structure rejections vs
#                  data-depth artifacts). Gates NOT loosened.
#
#   holdout-feasibility — Phase 1G.17: cache-only sample-rate feasibility
#                  of the 2026H2 holdout. Never mutates the covenant.
#
#   recall-shadow-feeder — Phase 1G.17: research-only routing of top shadow
#                  candidates to Lens/Gatekeeper review. Plan-only unless
#                  --execute (lens refresh is provider-heavy). NO paper
#                  signals, NO proposals.
#
#   emission-calibration — Phase 1G.17: cache-only SNIPER/VOYAGER gate-
#                  variant calibration study (1-3/week research-flow
#                  target). PRODUCTION THRESHOLDS NOT MODIFIED.
#
#   recall-shadow-cohort-freeze — Phase 1G.17A: write-once freeze of the
#                  recall-shadow × Gatekeeper review cohort (immutable;
#                  refuses overwrite; --reprint re-renders).
#
#   recall-shadow-gk-forward — Phase 1G.17A: cache-only forward validation
#                  of the frozen cohort (1/3/5/10/20d; WATCH vs BLOCK;
#                  rel-SPY/QQQ; MFE/MAE; too-extended-block question).
#                  Runs daily inside midday; cohort never rewritten.
#
#   dry-run CMD  — print what would run; no provider calls, no resolver.
#                  e.g. ./scripts/run_research_cycle.sh dry-run nightly
#
# COST LABELS:
#   [PROVIDER] — calls Alpaca / FMP / yfinance / Anthropic.
#   [CACHE]    — reads cached parquets / JSON only; no network cost.
#
# Provider failures during a cycle (e.g. Social Arb cadence skip, Alpha
# Discovery transient error) are logged as warnings — they do not fail
# the whole cycle.  The resolver/reports always run for nightly so
# forward-tracking stays current.
#
# Examples:
#   ./scripts/run_research_cycle.sh nightly
#   ./scripts/run_research_cycle.sh nightly --force-social
#   ./scripts/run_research_cycle.sh resolve
#   ./scripts/run_research_cycle.sh delta
#   ./scripts/run_research_cycle.sh holdout
#   ./scripts/run_research_cycle.sh lenses-nightly --max=20
#   ./scripts/run_research_cycle.sh lens AAPL NVDA XOM
#   ./scripts/run_research_cycle.sh gatekeeper AAPL NVDA
#   ./scripts/run_research_cycle.sh risk-telemetry
#   ./scripts/run_research_cycle.sh dry-run nightly
# ============================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

export SNIPER_ENV_PATH="${SNIPER_ENV_PATH:-/home/gem/secure/trading.env}"
PY="${PY:-$ROOT/.venv/bin/python}"
DATE_FMT="%Y-%m-%d %H:%M:%S"

# Cadence days for Social Arb (1=Mon … 7=Sun, ISO weekday).  Override with
# RESEARCH_SOCIAL_DAYS, e.g. RESEARCH_SOCIAL_DAYS="2,4" for Tue+Thu only.
# Default is Mon-Fri — daily post-close. News-catalyst signals decay in
# hours, not days, so the previous 3x/week cadence published headlines
# that were 2-3 trading days stale by the next render. Provider cost is
# small (~50-80 FMP calls + 2-4 News API + ~10 Anthropic Haiku messages
# per run; News API free tier 100/day is the binding constraint).
# DEFAULT_MIN_RUN_INTERVAL_HOURS (10h) prevents duplicate runs in the
# same session if the timer fires repeatedly.
SOCIAL_CADENCE_DAYS="${RESEARCH_SOCIAL_DAYS:-1,2,3,4,5}"  # Mon-Fri daily

DRY_RUN=0
FORCE_SOCIAL=0

log()  { printf '[%s] %s\n' "$(date +"$DATE_FMT")" "$*"; }
warn() { printf '[%s] WARN %s\n' "$(date +"$DATE_FMT")" "$*" >&2; }
note() { printf '[%s] %s\n' "$(date +"$DATE_FMT")" "$*"; }

# ----- helpers -------------------------------------------------------

run_or_warn() {
    local label="$1"; shift
    log "▶ $label"
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '    DRY_RUN $ %s\n' "$*"
        return 0
    fi
    if ! "$@"; then
        warn "$label exited non-zero — continuing"
        return 0
    fi
}

require_env() {
    if [[ ! -f "$SNIPER_ENV_PATH" && "$DRY_RUN" != "1" ]]; then
        warn "credential file not found at $SNIPER_ENV_PATH"
        warn "[PROVIDER] subcommands will likely fail; [CACHE] still works"
    fi
}

is_social_cadence_day() {
    # ISO weekday: 1=Mon … 7=Sun
    local today_dow
    today_dow="$(date +%u)"
    IFS=',' read -ra days <<< "$SOCIAL_CADENCE_DAYS"
    for d in "${days[@]}"; do
        if [[ "$d" == "$today_dow" ]]; then
            return 0
        fi
    done
    return 1
}

cadence_label() {
    local today_dow
    today_dow="$(date +%u)"
    case "$today_dow" in
        1) echo "Mon" ;; 2) echo "Tue" ;; 3) echo "Wed" ;; 4) echo "Thu" ;;
        5) echo "Fri" ;; 6) echo "Sat" ;; 7) echo "Sun" ;;
    esac
}

# ----- subcommand bodies ---------------------------------------------

cmd_forecast() {
    log "[PROVIDER] regime forecast"
    run_or_warn "regime forecast (daily)" \
        "$PY" research/regime_forecast.py --mode daily
}

cmd_options_regime() {
    # Phase 1G.12 — research-only Options Regime Lens.  PROVIDER: pulls options
    # chains for SPY/QQQ/IWM/VXX (a few expiries each) via the shared Alpaca-
    # first / Tradier-enrich feed, computes a GEX proxy + skew + IV rank + term
    # structure + a consolidated regime label, and persists a daily IV snapshot.
    # Emits no signals, registers no strategy, mutates no DB rows, and touches
    # no governance/execution.  Defaults to SPY QQQ IWM VXX; extra positionals
    # override the symbol set.
    log "[PROVIDER] Phase 1G.12 options regime lens (research-only)"
    run_or_warn "options regime lens" \
        "$PY" research/options_regime_lens.py "$@"
}

cmd_options_chain_snapshot() {
    # Phase 1J.1/1J.2 — point-in-time options chain snapshot collection.
    # DATA_COLLECTION_ONLY: builds historical options data forward from today;
    # emits no signals, registers no strategy, builds no proposals, touches no
    # governance/execution.  Idempotent per (symbol, date): a same-day rerun
    # skips collected symbols at zero provider calls.  The collector's own
    # budget guards cap symbols/expiries/provider calls per run.
    log "[PROVIDER] Phase 1J options chain snapshots (DATA_COLLECTION_ONLY)"
    if [[ "$DRY_RUN" == "1" ]]; then
        run_or_warn "options chain snapshot (dry-run plan)" \
            "$PY" research/options_chain_snapshot_collector.py --dry-run "$@"
        return 0
    fi
    # Real collection failure must surface to systemd: no run_or_warn here.
    if ! "$PY" research/options_chain_snapshot_collector.py "$@"; then
        warn "options chain snapshot collector failed — see logs/options_chain_snapshot_collector_latest.txt"
        return 1
    fi
    run_or_warn "options snapshot quality audit (cache-only)" \
        "$PY" research/options_chain_snapshot_quality.py
    run_or_warn "options snapshot health check (cache-only)" \
        "$PY" research/options_chain_snapshot_health.py
}

cmd_options_chain_snapshot_quality() {
    log "[CACHE] options snapshot quality + health (cache-only, no provider calls)"
    run_or_warn "options snapshot quality audit (cache-only)" \
        "$PY" research/options_chain_snapshot_quality.py
    run_or_warn "options snapshot health check (cache-only)" \
        "$PY" research/options_chain_snapshot_health.py
}

cmd_alpha() {
    if [[ ! -f "$ROOT/research/alpha_discovery_board.py" ]]; then
        warn "research/alpha_discovery_board.py not found; skipping"
        return 0
    fi
    log "[PROVIDER] alpha discovery board"
    run_or_warn "alpha discovery board" \
        "$PY" research/alpha_discovery_board.py
}

cmd_alpha_overlay() {
    # Premarket overlay refresh.  Reads the cached nightly board and writes
    # alpha_discovery_overlay_latest.{json,txt}.  Cheap on FMP — re-uses the
    # cached board where possible; only fetches deltas needed for the overlay.
    if [[ ! -f "$ROOT/research/alpha_discovery_board.py" ]]; then
        warn "research/alpha_discovery_board.py not found; skipping"
        return 0
    fi
    log "[PROVIDER] alpha discovery overlay (premarket)"
    run_or_warn "alpha discovery overlay" \
        "$PY" research/alpha_discovery_board.py --mode premarket
}

cmd_social_inner() {
    if [[ ! -f "$ROOT/research/social_arb_radar.py" ]]; then
        warn "research/social_arb_radar.py not found; skipping"
        return 0
    fi
    log "[PROVIDER] social arb radar"
    run_or_warn "social arb radar" \
        "$PY" research/social_arb_radar.py
}

cmd_social_with_cadence() {
    # Honor cadence unless --force-social was supplied.
    if [[ "$FORCE_SOCIAL" == "1" ]]; then
        note "Social Arb: cadence override active (--force-social)"
        cmd_social_inner
        return $?
    fi
    if is_social_cadence_day; then
        note "Social Arb: cadence day ($(cadence_label)) — running"
        cmd_social_inner
        return $?
    fi
    note "Social Arb skipped — cadence day not reached (today=$(cadence_label), cadence_days=$SOCIAL_CADENCE_DAYS). Use --force-social to override."
    return 0
}

cmd_resolve() {
    log "[CACHE] resolve forecast + lens outcomes"
    run_or_warn "resolve forecast outcomes + write summary" \
        "$PY" research/forecast_forward_report.py
    run_or_warn "resolve stock lens outcomes + write summary" \
        "$PY" research/stock_lens_forward_report.py
}

cmd_reports() {
    log "[CACHE] write summaries (no resolver)"
    run_or_warn "write forecast summary (no resolver)" \
        "$PY" research/forecast_forward_report.py --no-resolve
    run_or_warn "write stock lens summary (no resolver)" \
        "$PY" research/stock_lens_forward_report.py --no-resolve
}

cmd_holdout() {
    log "[CACHE] pre-registered holdout 2026 H2 daily status"
    run_or_warn "holdout 2026 H2 scoreboard" \
        "$PY" research/holdout_scoreboard.py
}

cmd_lens() {
    local tickers=("$@")
    if [[ ${#tickers[@]} -eq 0 ]]; then
        warn "lens: no tickers provided.  e.g. './run_research_cycle.sh lens AAPL NVDA'"
        return 0
    fi
    for t in "${tickers[@]}"; do
        log "[PROVIDER] stock lens $t"
        run_or_warn "stock lens for $t" \
            "$PY" research/regime_forecast.py --ticker "$t"
    done
    cmd_resolve
}

cmd_gatekeeper() {
    local tickers=("$@")
    if [[ ${#tickers[@]} -eq 0 ]]; then
        warn "gatekeeper: no tickers provided.  e.g. './run_research_cycle.sh gatekeeper AAPL NVDA'"
        return 0
    fi
    if [[ ! -f "$ROOT/research/executive_gatekeeper_report.py" ]]; then
        warn "research/executive_gatekeeper_report.py not found; skipping"
        return 0
    fi
    for t in "${tickers[@]}"; do
        log "[CACHE] executive gatekeeper $t"
        run_or_warn "executive gatekeeper for $t" \
            "$PY" research/executive_gatekeeper_report.py --ticker "$t"
    done
}

cmd_gatekeeper_refresh() {
    if [[ ! -f "$ROOT/research/gatekeeper_refresh.py" ]]; then
        warn "research/gatekeeper_refresh.py not found; skipping"
        return 0
    fi
    log "[CACHE] Phase 2B.2 executive gatekeeper batched refresh"
    run_or_warn "gatekeeper refresh" \
        "$PY" research/gatekeeper_refresh.py "$@"
}

cmd_provider_audit() {
    if [[ ! -f "$ROOT/research/provider_freshness_audit.py" ]]; then
        warn "research/provider_freshness_audit.py not found; skipping"
        return 0
    fi
    log "[CACHE] Phase 2B.4 provider freshness + pipeline audit"
    run_or_warn "provider freshness audit" \
        "$PY" research/provider_freshness_audit.py "$@"
}

cmd_delta() {
    if [[ ! -f "$ROOT/research/research_delta.py" ]]; then
        warn "research/research_delta.py not found; skipping"
        return 0
    fi
    log "[CACHE] research delta — what changed today"
    run_or_warn "research delta (cache-only)" \
        "$PY" research/research_delta.py
}

cmd_lenses_nightly() {
    if [[ ! -f "$ROOT/research/prebuild_stock_lenses.py" ]]; then
        warn "research/prebuild_stock_lenses.py not found; skipping"
        return 0
    fi
    log "[PROVIDER] stock lens prebuild (curated · capped)"
    # Forward through any extra positional args (e.g. --max=20, --force,
    # --dry-run, --no-positions, --liquid-top=N) so callers can tune from
    # the CLI without editing this script.
    run_or_warn "stock lens prebuild" \
        "$PY" research/prebuild_stock_lenses.py "$@"
}

cmd_lenses_liquid() {
    if [[ ! -f "$ROOT/research/prebuild_stock_lenses.py" ]]; then
        warn "research/prebuild_stock_lenses.py not found; skipping"
        return 0
    fi
    local n="${1:-100}"
    shift || true
    log "[PROVIDER] stock lens prebuild (top-${n} liquid coverage tier · curated unions in)"
    run_or_warn "stock lens prebuild (liquid)" \
        "$PY" research/prebuild_stock_lenses.py --liquid-top="$n" --max="$n" "$@"
}

cmd_alpha_lens_refresh() {
    # Refresh Stock Lens artifacts for the FINAL Alpha Discovery board top-N
    # (default 20), then re-annotate the saved board + overlay so lens labels,
    # options_quality, and board_lens_conflict reflect the freshest evidence.
    #
    # Provider-aware (uses the same chain as `lenses-nightly`) but capped so
    # this can run on demand without the broader nightly footprint.  Never
    # mutates execution / governance.  Dashboard remains cache-only.
    if [[ ! -f "$ROOT/research/prebuild_stock_lenses.py" ]]; then
        warn "research/prebuild_stock_lenses.py not found; skipping"
        return 0
    fi
    local n="20"
    local pass=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --max=*) n="${1#--max=}"; shift ;;
            --max)   shift; n="${1:-20}"; shift ;;
            *) pass+=("$1"); shift ;;
        esac
    done
    log "[PROVIDER] alpha-lens-refresh — lenses for final Alpha board top-${n}"
    # Restrict to the alpha source so we don't drag in posture/structural/social
    # tickers; keep positions seeded because operators expect their book covered.
    run_or_warn "alpha-lens-refresh (prebuild)" \
        "$PY" research/prebuild_stock_lenses.py \
            --max="$n" \
            --skip-source posture \
            --skip-source structural \
            --skip-source social \
            --skip-source liquid \
            --skip-source reference \
            "${pass[@]}"
    log "[CACHE] alpha-lens-refresh — re-annotate Alpha board + overlay"
    run_or_warn "alpha board lens re-annotation" \
        "$PY" research/annotate_alpha_board.py
}

cmd_weekly_review() {
    if [[ ! -f "$ROOT/research/review_misses.py" ]]; then
        warn "research/review_misses.py not found; skipping"
        return 0
    fi
    log "=== weekly review (cache-only) ==="
    # Resolve any newly-matured snapshots and rewrite summaries first so the
    # review reads up-to-date outcomes; both steps are cache-only.
    cmd_resolve
    cmd_reports
    log "[CACHE] weekly mistake / false-confidence review"
    run_or_warn "weekly review (review_misses.py)" \
        "$PY" research/review_misses.py "$@"
    log "weekly review complete"
}

cmd_mcp_audit() {
    if [[ ! -f "$ROOT/research/mcp_audit_workflows.py" ]]; then
        warn "research/mcp_audit_workflows.py not found; skipping"
        return 0
    fi
    log "[CACHE] Phase 2B MCP audit workflows — daily + late_chase + system_health"
    run_or_warn "mcp audit: daily_dashboard"  "$PY" research/mcp_audit_workflows.py daily
    run_or_warn "mcp audit: late_chase"       "$PY" research/mcp_audit_workflows.py late-chase
    run_or_warn "mcp audit: system_health"    "$PY" research/mcp_audit_workflows.py system-health
}

cmd_mcp_audit_session() {
    if [[ ! -f "$ROOT/research/mcp_audit_orchestrator.py" ]]; then
        warn "research/mcp_audit_orchestrator.py not found; skipping"
        return 0
    fi
    # Phase 2B.3 — optional `--refresh-gatekeeper` flag.  When supplied,
    # batch-refresh Executive Gatekeeper artifacts BEFORE the orchestrator
    # so the MCP audit reads against fresh per-ticker verdicts.  Default
    # behavior is unchanged: no refresh unless explicitly requested.
    local refresh_gk=0
    local rest=()
    for arg in "$@"; do
        case "$arg" in
            --refresh-gatekeeper) refresh_gk=1 ;;
            *) rest+=("$arg") ;;
        esac
    done
    local session="regular"
    if [[ ${#rest[@]} -gt 0 ]]; then
        session="${rest[0]}"
        rest=("${rest[@]:1}")
    fi
    if [[ "$refresh_gk" == "1" ]]; then
        log "[CACHE] Phase 2B.3 — refreshing Executive Gatekeeper before MCP audit"
        cmd_gatekeeper_refresh
    fi
    log "[CACHE] Phase 2B.1 MCP audit session orchestration (session=$session)"
    if [[ ${#rest[@]} -gt 0 ]]; then
        run_or_warn "mcp audit session" \
            "$PY" research/mcp_audit_orchestrator.py --session "$session" "${rest[@]}"
    else
        run_or_warn "mcp audit session" \
            "$PY" research/mcp_audit_orchestrator.py --session "$session"
    fi
}

cmd_mcp_audit_ticker() {
    local tickers=("$@")
    if [[ ${#tickers[@]} -eq 0 ]]; then
        warn "mcp-audit-ticker: no tickers provided.  e.g. './run_research_cycle.sh mcp-audit-ticker SPY MDB'"
        return 0
    fi
    if [[ ! -f "$ROOT/research/mcp_audit_workflows.py" ]]; then
        warn "research/mcp_audit_workflows.py not found; skipping"
        return 0
    fi
    log "[CACHE] Phase 2B MCP ticker consistency audit (${#tickers[@]} ticker(s))"
    run_or_warn "mcp ticker audit" "$PY" research/mcp_audit_workflows.py ticker "${tickers[@]}"
}

cmd_risk_telemetry() {
    log "[CACHE] Phase 1B/1C paper risk telemetry + hygiene — slippage + concentration + shadow sizing + hygiene"
    run_or_warn "slippage telemetry report" \
        "$PY" research/slippage_telemetry_report.py "$@"
    run_or_warn "portfolio concentration report" \
        "$PY" research/portfolio_concentration_report.py "$@"
    run_or_warn "shadow sizing + borrow report" \
        "$PY" research/shadow_sizing_report.py "$@"
    # Phase 1C — diagnostic-only paper-state hygiene.  No mutations, no
    # provider calls, no enforcement.  ready_to_gate is published in the
    # JSON sidecar but is not wired to any block in this phase.
    run_or_warn "paper state hygiene report" \
        "$PY" research/paper_state_hygiene_report.py "$@"
    # Phase 1G.3 forward-resolution health (kept in nightly: monitors maturation
    # health for all active sleeves, not short-specific).
    run_or_warn "forward resolution health" \
        "$PY" research/forward_resolution_health.py --no-resolve
}

# Phase 4A.4 — archived research diagnostics (not wired into nightly).
# Each item has a completed verdict (NO / DETECTION_GAP / frozen) and no new
# data benefits from nightly re-run.  Available on demand via 'legacy-diagnostics'
# or their individual subcommands (legacy-decision-policy, short-radar, etc.).
cmd_legacy_diagnostics() {
    log "[CACHE] archived/legacy research diagnostics — manual only"
    # Phase 1G.2 T5 — legacy decision policy: rows missing fill data (verdict: quarantine).
    run_or_warn "legacy decision policy report" \
        "$PY" research/legacy_decision_policy_report.py
    # Phase 1G.3 — SHORT_A frozen 2026-05-24; radar kept for awareness but not nightly.
    run_or_warn "short opportunity radar" \
        "$PY" research/short_opportunity_radar.py
    # Phase 1G.16 — DETECTION_GAP_CONFIRMED; SHORT_A stays frozen; no nightly value.
    run_or_warn "short detection truth audit" \
        "$PY" research/short_detection_truth_audit.py
    run_or_warn "short detection forward" \
        "$PY" research/short_detection_forward_validation.py
    # Phase 1I — LEADER_RESET verdict: NO (failed independent DD + Sharpe gates).
    run_or_warn "leader reset event study" \
        "$PY" research/leader_reset_event_study.py
    # VOYAGER conversion audit — VOYAGER archived; no active sleeve to monitor.
    run_or_warn "voyager conversion audit" \
        "$PY" research/voyager_conversion_audit.py
    # Phase 1G.4 — Strategy Tournament: NO_VARIANT_READY_FOR_PAPER_SHADOW.
    run_or_warn "strategy tournament" \
        "$PY" research/strategy_tournament.py
}

cmd_legacy_decision_policy() {
    log "[CACHE] Phase 1G.2 T5 legacy decision policy report (read-only)"
    run_or_warn "legacy decision policy report" "$PY" research/legacy_decision_policy_report.py
}

# Phase 1G.3 standalone evidence-hygiene commands (all cache-only / research-only).
cmd_short_radar() {
    log "[CACHE] Phase 1G.3 short opportunity radar (research-only)"
    run_or_warn "short opportunity radar" "$PY" research/short_opportunity_radar.py --print
}

cmd_forward_health() {
    log "[CACHE] Phase 1G.3 forward-resolution health"
    run_or_warn "forward resolution health" "$PY" research/forward_resolution_health.py "$@"
}

# Phase 1G.16 — Short Detection Truth Audit (research-only / cache-only). Audits
# whether the SPY-biased short radar misses QQQ/tech tactical breakdowns. Never
# unfreezes SHORT_A; emits no signals; touches no governance/execution.
cmd_short_detection_audit() {
    log "[CACHE] Phase 1G.16 short detection truth audit (research-only)"
    run_or_warn "short detection truth audit" "$PY" research/short_detection_truth_audit.py --print
}

cmd_short_detection_forward() {
    log "[CACHE] Phase 1G.16 short detection forward validation (research-only)"
    run_or_warn "short detection forward" "$PY" research/short_detection_forward_validation.py --print
}

cmd_leader_reset_study() {
    log "[CACHE] Phase 1G.3 LEADER_RESET event study (research-only)"
    run_or_warn "leader reset event study" "$PY" research/leader_reset_event_study.py --print
}

cmd_voyager_audit() {
    log "[CACHE] Phase 1G.3 VOYAGER conversion audit (read-only)"
    run_or_warn "voyager conversion audit" "$PY" research/voyager_conversion_audit.py --print
}

cmd_strategy_tournament() {
    log "[CACHE] Phase 1G.4 strategy tournament / profitability discovery lab (research-only)"
    run_or_warn "strategy tournament" "$PY" research/strategy_tournament.py --print
}

cmd_scanner_recall() {
    # Phase 1G.6 — Scanner Recall Repair package.  CACHE-ONLY / RESEARCH-ONLY:
    # emission-gap audit + RS recall lane + theme leadership radar + cap audit +
    # price-cache coverage, then an aggregate summary.  No provider calls, no DB
    # writes, no signals.  NOT wired into any timer; operator-invoked only.
    log "[CACHE] Phase 1G.6 scanner recall repair (research-only)"
    run_or_warn "scanner recall repair" "$PY" -m research.scanner_recall_repair
}

cmd_rs_theme_triage() {
    # Phase 1G.9 — RS/Theme → Lens/Gatekeeper triage.  CACHE-ONLY / RESEARCH-
    # ONLY: assembles RS-lane + LEADING-theme + proposed-dynamic early leaders,
    # enriches with existing Lens/Gatekeeper/options artifacts, assigns routing
    # triage labels, decomposes gate rejections, and historizes forward.  No
    # provider calls, no DB writes, no signals, no gate change.  Operator-invoked.
    log "[CACHE] Phase 1G.9 RS/theme → lens/gatekeeper triage (research-only)"
    run_or_warn "rs/theme lens triage" "$PY" -m research.rs_theme_lens_triage "$@"
}

cmd_rs_theme_forward() {
    # Phase 1G.11 — RS/Theme forward validation.  CACHE-ONLY / RESEARCH-ONLY:
    # measures the frozen 1G.10 LENS_READY/BLOCKED cohort forward vs the Alpha
    # board, random liquid controls, a simple RS-top baseline, and SPY/QQQ; runs
    # the Gatekeeper-precision + options-quality audits and emits a gated verdict.
    # No provider calls, no DB writes, no signals, no gate change.  Operator-invoked.
    # Pass --freeze-cohort (optionally --force) to (re)freeze the immutable cohort.
    log "[CACHE] Phase 1G.11 RS/theme forward validation (research-only)"
    run_or_warn "rs/theme forward validation" "$PY" -m research.rs_theme_forward_validation "$@"
}

cmd_gatekeeper_precision() {
    # Phase 1G.13 — Gatekeeper Precision Audit + Blocked-Winner Autopsy.  CACHE-
    # ONLY / RESEARCH-ONLY: autopsies every Executive-Gatekeeper BLOCK (per-ticker
    # snapshots + frozen RS/theme 1G.10 cohort) forward vs the not-blocked WATCH
    # set, random/RS-top/Alpha controls and SPY, groups blocks by reason, isolates
    # cache-depth artifacts, and runs a research-only rescue simulation.  No
    # provider calls, no DB writes, no signals, no Gatekeeper/gate change.
    log "[CACHE] Phase 1G.13 gatekeeper precision audit (research-only)"
    run_or_warn "gatekeeper precision audit" "$PY" -m research.gatekeeper_precision_audit "$@"
}

cmd_power_trend() {
    # Phase 1G.14 — Power-Trend Extension Study (too-extended block audit).  CACHE-
    # ONLY / RESEARCH-ONLY: classifies every "too_extended"-flagged name (Gatekeeper
    # blocks + RS/theme + Alpha-board extended) into POWER_TREND / CLIMAX_CHASE /
    # WAIT_FOR_RESET / LOW_QUALITY, measures forward outcomes vs random + by theme,
    # and emits a gated recommendation + PROPOSED (not implemented) Gatekeeper fields.
    # No provider calls, no DB writes, no signals, no Gatekeeper/gate change.
    log "[CACHE] Phase 1G.14 power-trend extension study (research-only)"
    run_or_warn "power-trend extension study" "$PY" -m research.power_trend_extension_study "$@"
}

cmd_freshness_audit() {
    # Mode-3 Evidence Freshness mapping audit.  CACHE-ONLY / READ-ONLY: resolves
    # every Evidence Freshness panel field to its exact current source artifact,
    # age, and status (one read-only SELECT for the legacy daemon-scan ts).  No
    # providers, no DB writes, no execution/governance/universe/gate side effects.
    log "[CACHE] evidence freshness mapping audit (read-only)"
    run_or_warn "evidence freshness audit" "$PY" -m research.evidence_freshness_audit "$@"
}

cmd_recall_shadow() {
    # Path B — Recall-Repair Shadow Lane.  CACHE-ONLY / RESEARCH-ONLY: simple
    # sector_rs + mom_20_60 + theme-leadership board on the deep/current price
    # cache, dated + historized.  Tests whether simple signals catch winners
    # earlier than the funnel (recall ~1.1%).  No provider calls, no DB writes,
    # no signals, no trade proposals, no gate/universe/execution change.
    log "[CACHE] Path B recall-repair shadow lane (research-only)"
    run_or_warn "recall-repair shadow lane" "$PY" -m research.recall_repair_shadow_lane "$@"
}

cmd_recall_shadow_forward() {
    # Path B forward validation.  CACHE-ONLY / RESEARCH-ONLY: measures the
    # historized shadow board forward vs random / sector_rs / Alpha / SPY-QQQ /
    # cash and emits a GATED verdict.  No provider calls, no DB writes, no
    # signals, no routing.
    log "[CACHE] Path B recall-repair shadow forward validation (research-only)"
    run_or_warn "recall-repair shadow forward" "$PY" -m research.recall_repair_shadow_forward "$@"
}

cmd_participation_audit() {
    # Phase 1G.17 — participation bottleneck audit.  CACHE-ONLY / READ-ONLY:
    # scan→council→decision flow per sleeve from the daemon log + read-only DB.
    # No provider calls, no DB writes, no signals, no gate/execution change.
    log "[CACHE] participation bottleneck audit (research-only)"
    run_or_warn "participation bottleneck audit" \
        "$PY" -m research.participation_bottleneck_audit "$@"
}

cmd_sniper_starvation() {
    # Phase 1G.17 — SNIPER gate-confluence autopsy.  CACHE-ONLY / READ-ONLY.
    # Thresholds NOT changed; counterfactual relaxation replay only.
    log "[CACHE] SNIPER starvation audit (research-only)"
    run_or_warn "sniper starvation audit" \
        "$PY" -m research.sniper_starvation_audit "$@"
}

cmd_voyager_cache_audit() {
    # Phase 1G.17 — VOYAGER starvation + cache-depth audit.  CACHE-ONLY /
    # READ-ONLY.  Separates true structure rejections from data-depth
    # artifacts; gates NOT loosened.
    log "[CACHE] VOYAGER starvation + cache-depth audit (research-only)"
    run_or_warn "voyager starvation cache audit" \
        "$PY" -m research.voyager_starvation_cache_audit "$@"
}

cmd_holdout_feasibility() {
    # Phase 1G.17 — holdout sample-rate feasibility.  CACHE-ONLY / READ-ONLY.
    # Never mutates the covenant or its evidence; restatement is a proposal.
    log "[CACHE] holdout feasibility audit (research-only)"
    run_or_warn "holdout feasibility audit" \
        "$PY" -m research.holdout_feasibility_audit "$@"
}

cmd_recall_shadow_feeder() {
    # Phase 1G.17 — recall-shadow → Lens/Gatekeeper research-only feeder.
    # Plan-only by default (no provider calls); pass --execute to actually
    # refresh artifacts via the existing lens/gatekeeper runners.
    # NO paper signals, NO trade proposals, NO execution/governance path.
    log "[CACHE] recall-shadow lens feeder (research-only, plan unless --execute)"
    run_or_warn "recall shadow lens feeder" \
        "$PY" -m research.recall_shadow_lens_feeder "$@"
}

cmd_emission_calibration() {
    # Phase 1G.17 — SNIPER/VOYAGER emission-target calibration study.
    # CACHE-ONLY counterfactual replay; PRODUCTION THRESHOLDS NOT MODIFIED.
    log "[CACHE] emission calibration study (research-only)"
    run_or_warn "emission calibration study" \
        "$PY" -m research.emission_calibration_study "$@"
}

cmd_core_satellite() {
    # Phase 2A — core-satellite portfolio engine feasibility.  CACHE-ONLY /
    # RESEARCH-ONLY: regime-throttled index exposure vs buy-hold + static
    # benchmarks.  No provider calls, no DB writes, no signals.
    log "[CACHE] Phase 2A core-satellite portfolio engine (research-only)"
    run_or_warn "core-satellite portfolio" \
        "$PY" research/core_satellite_portfolio.py "$@"
}

cmd_core_satellite_leveraged() {
    # Phase 2A.1 — leveraged variant test (1.0x / 1.25x / 1.5x).  CACHE-ONLY /
    # RESEARCH-ONLY: adds margin/borrow-cost model, gap-risk stress, tax estimate.
    # Pre-registered gates vs QQQ Phase 2A benchmarks.  No provider calls, no DB
    # writes, no signals, no production changes.
    log "[CACHE] Phase 2A.1 core-satellite leveraged variants (research-only)"
    run_or_warn "core-satellite leveraged" \
        "$PY" research/core_satellite_leveraged.py "$@"
}

cmd_recall_shadow_cohort_freeze() {
    # Phase 1G.17A — write-once freeze of the recall-shadow × Gatekeeper
    # review cohort.  CACHE-ONLY / READ-ONLY inputs; refuses to overwrite an
    # existing frozen cohort (immutable evidence).  --reprint re-renders txt.
    log "[CACHE] recall-shadow GK cohort freeze (write-once, research-only)"
    run_or_warn "recall shadow GK cohort freeze" \
        "$PY" -m research.recall_shadow_gk_cohort_freeze "$@"
}

cmd_recall_shadow_gk_forward() {
    # Phase 1G.17A — forward validation of the frozen cohort at 1/3/5/10/20d
    # (WATCH vs BLOCK, rel-SPY/QQQ, MFE/MAE, too-extended-block question).
    # CACHE-ONLY; cohort file is never rewritten; history append-only.
    log "[CACHE] recall-shadow GK forward validation (research-only)"
    run_or_warn "recall shadow GK forward" \
        "$PY" -m research.recall_shadow_gk_forward "$@"
}

cmd_social_attention() {
    # Phase 1G.15 — Social Attention Radar V0.  RESEARCH-ONLY.  Detects early
    # crowd-attention anomalies (velocity / crowd-stage / social-vs-news lead)
    # from Google Trends + an operator-curated manual JSONL feed (+ opt-in
    # StockTwits/Reddit).  SEPARATE from the News Catalyst Radar; never a paper
    # signal / trade proposal / approval.  Only provider touch is Google Trends
    # (best-effort, rate-limit aware).  No DB writes, no PII stored.
    log "[PROVIDER] social attention radar (research-only)"
    run_or_warn "social attention radar" "$PY" -m research.social_attention_radar "$@"
}

cmd_social_attention_forward() {
    # Phase 1G.15 forward validation.  CACHE-ONLY / RESEARCH-ONLY: measures the
    # historized social-attention leads forward vs news-led / early-vs-viral /
    # velocity / random controls and emits a GATED verdict.  No provider calls,
    # no DB writes, no signals, no routing.
    log "[CACHE] social attention forward validation (research-only)"
    run_or_warn "social attention forward" "$PY" -m research.social_attention_forward_validation "$@"
}

cmd_market_heartbeat() {
    # Phase 4A — Market Heartbeat Engine.  RESEARCH-ONLY.  Reads cached price
    # parquets (SPY/QQQ/IWM/SMH/VXX + 11 sector ETFs + TLT/HYG); FMP for VIX
    # (optional).  Emits no signals, no trade recommendations, no paper signal,
    # no execution.  Alpaca not required.
    log "[CACHE] Phase 4A market heartbeat engine (research-only)"
    run_or_warn "market heartbeat" "$PY" research/market_heartbeat.py "$@"
}

cmd_research_scanner() {
    # Phase 4B/4C — Research Scanner + Watchlist Scorer.  RESEARCH-ONLY / CACHE-
    # FIRST.  Runs six scanner categories (Early Accumulation, Beaten-Down
    # Recovery, Sector/Theme Leaders, Catalyst Watch, Social Arb, Asymmetric)
    # against the price cache and assigns watchlist labels.  FMP optional (degrades
    # gracefully).  Emits no signals, no trade recommendations.  Alpaca not required.
    log "[CACHE] Phase 4B/4C research scanner + watchlist scorer (research-only)"
    run_or_warn "research scanner" "$PY" research/research_scanner.py "$@"
}

cmd_stock_research_card() {
    # Phase 4D — Stock Research Card Engine.  RESEARCH-ONLY.  Generates a per-
    # ticker research card: trend, RS, volume, catalyst, fundamentals, options
    # snapshot (Tradier research-only), social attention (degrades gracefully),
    # risk flags, and invalidation conditions.  No trade recommendations.
    # Tradier execution is disabled.  Alpaca not required.
    local tickers=("$@")
    if [[ ${#tickers[@]} -eq 0 ]]; then
        warn "stock-research-card: no tickers provided.  e.g. './run_research_cycle.sh stock-research-card AAPL NVDA'"
        return 0
    fi
    log "[CACHE] Phase 4D stock research card (research-only) — ${tickers[*]}"
    run_or_warn "stock research card" "$PY" research/stock_research_card.py "${tickers[@]}"
}

cmd_fmp_provider_health() {
    # Phase 3A closure — FMP provider health check.  Reports API key status,
    # cache_meta staleness, and price parquet freshness.  Research-only diagnostic.
    log "[CACHE] FMP provider health check (research-only)"
    run_or_warn "fmp provider health" "$PY" research/fmp_provider_health.py "$@"
}

cmd_tradier_research_health() {
    # Phase 3A closure — Tradier research-only health check.  Verifies token,
    # market clock probe, and confirms execution is permanently disabled.
    log "[PROVIDER] Tradier research health check (options research-only; no execution)"
    run_or_warn "tradier research health" "$PY" research/tradier_research_health.py "$@"
}

cmd_provider_health() {
    # Combined provider health: FMP + Tradier research checks in sequence.
    log "=== provider health (FMP + Tradier research-only) ==="
    cmd_fmp_provider_health
    cmd_tradier_research_health
}

cmd_data_freshness() {
    # Phase 3A closure — cache data freshness audit.  Reports sidecar ages
    # and price parquet staleness.  Cache-only; no provider calls.
    log "[CACHE] data freshness audit (cache-only)"
    run_or_warn "data freshness" "$PY" research/data_freshness_report.py "$@"
}

cmd_sector_leadership() {
    # Phase 3A closure — sector leadership ranking from cached price data.
    # Ranks sector ETFs by RS vs SPY.  Cache-only; no provider calls.
    log "[CACHE] sector leadership report (cache-only)"
    run_or_warn "sector leadership" "$PY" research/sector_leadership_report.py "$@"
}

cmd_research_coverage() {
    # Phase 4A Task 1 — Data confidence audit per ticker.  Checks price bar
    # depth, parquet age, FMP profile cache coverage, and options snapshot
    # availability.  Outputs HIGH/MEDIUM/LOW/INVALID per ticker.  Cache-only.
    log "[CACHE] Phase 4A research coverage audit (cache-only)"
    run_or_warn "research coverage" "$PY" research/research_coverage_audit.py "$@"
}

cmd_research_changes() {
    # Phase 4A Task 4 — Scanner watchlist change detector.  Compares current
    # research_scanner_latest.json against the previous snapshot to surface
    # new entries, dropped names, score movements, and label reclassifications.
    # Rotates current → prev on each run.  Cache-only.
    log "[CACHE] Phase 4A research change detector (cache-only)"
    run_or_warn "research changes" "$PY" research/research_change_detector.py "$@"
}

cmd_research_forward_tracker() {
    # Phase 4A Task 5 — Watchlist forward outcome tracker.  Records watchlist
    # entries by date and computes 5d/10d/20d forward returns from the price
    # cache.  Appends to data/research/research_watchlist_history.jsonl.
    # Outputs verdicts by label bucket.  Cache-only.
    log "[CACHE] Phase 4A research watchlist forward tracker (cache-only)"
    run_or_warn "research forward tracker" "$PY" research/research_watchlist_forward_tracker.py "$@"
}

cmd_ten_x_candidates() {
    # Phase 4A Task 6 — 10x speculative candidate radar.  Scans for names with
    # large ATH drawdown + turning momentum + theme exposure.  SPECULATIVE —
    # requires manual research.  No trade recommendations.  Cache-only.
    log "[CACHE] Phase 4A 10x candidate radar (cache-only, speculative)"
    run_or_warn "ten-x candidates" "$PY" research/ten_x_candidate_radar.py "$@"
}

cmd_daily_alpha_radar() {
    # Phase 4A.2 — Daily Alpha Radar report.  Aggregates scanner + coverage +
    # changes + forward-tracker + 10x sidecars into the quality-gated report
    # at docs/research/DAILY_ALPHA_RADAR_REPORT.md (priority labels, earliness
    # detail, quality-adjusted consensus, options coverage guard, catalyst
    # sanity).  No trade recommendations, no legacy strategy references.
    # Cache-only.
    log "[CACHE] Phase 4A.2 daily alpha radar report (cache-only)"
    run_or_warn "daily alpha radar" "$PY" research/daily_alpha_radar_report.py "$@"
}

cmd_nightly_operator_summary() {
    # Nightly Operator Summary — concise 25-50 line human-readable recap.
    # Reads all research sidecars written by the nightly cycle (forecast,
    # alpha radar, scanner, forward tracker, scanner-truth, provider audit)
    # and writes:
    #   docs/research/NIGHTLY_OPERATOR_SUMMARY.md    (docs artifact)
    #   cache/research/nightly_operator_summary_latest.json
    #   logs/nightly_operator_summary_latest.md
    # Cache-only; no provider calls.  Research-only language — no strategy
    # abbreviations (VOYAGER/SNIPER/SHORT_A), no trade approvals.
    log "[CACHE] nightly operator summary (cache-only, research-only language)"
    run_or_warn "nightly operator summary" \
        "$PY" research/nightly_operator_summary.py "$@"
}

_disabled_execution_cmd() {
    echo "RESEARCH_ONLY_MODE: command disabled."
}

cmd_premarket() {
    log "=== research cycle: PREMARKET ==="
    require_env
    cmd_forecast
    # Phase 1G.12 — options regime lens reads the morning options market right
    # after the forecast so the dashboard/MCP have a fresh market options view.
    cmd_options_regime
    cmd_alpha
    # Build the premarket overlay AFTER the nightly board so it reads the
    # freshest cached board as input.  Without this, the overlay sidecar is
    # never refreshed by any timer.
    cmd_alpha_overlay
    # Phase 7: cache-only delta after the premarket overlay refresh so the
    # dashboard's WHAT CHANGED panel reflects the morning view.  No
    # provider calls — purely a diff against the prior snapshot.
    cmd_delta
    # Phase 2B.3: refresh Executive Gatekeeper artifacts for the high-
    # priority short list (open positions + earnings today/tomorrow/this
    # week + missing/stale for top Alpha candidates).  Cache-first; the
    # only provider call is the FMP earnings calendar (cached 6 h, so a
    # premarket fire typically reads from cache).  Default cap 25.
    cmd_gatekeeper_refresh
    log "premarket cycle complete"
}

cmd_midday() {
    # Cache-only midday refresh.  Zero provider cost.  Re-resolves matured
    # forward outcomes, rewrites summaries, re-runs delta against the post-
    # premarket snapshot, and refreshes the paper-only risk telemetry +
    # hygiene sidecars so the dashboard's intraday view reflects the day so
    # far.  Safe to fire as often as the operator wants.
    log "=== research cycle: MIDDAY (cache-only) ==="
    cmd_resolve
    cmd_reports
    cmd_delta
    cmd_risk_telemetry "$@"
    # Phase 1G.17 — keep the participation verdict fresh for the dashboard
    # banner (cache-only: daemon log + read-only DB).
    cmd_participation_audit
    # Phase 1G.17A — mature the frozen recall-shadow × GK cohort daily
    # (cache-only; no-op until the cohort exists, never rewrites it).
    cmd_recall_shadow_gk_forward
    log "midday cycle complete"
}

cmd_nightly() {
    log "=== research cycle: NIGHTLY ($(cadence_label)) ==="
    require_env
    cmd_forecast
    # Phase 1G.12 — options regime lens on the post-close options market, on the
    # same cadence as the forecast so the next premarket reads a fresh sidecar.
    cmd_options_regime
    cmd_alpha
    cmd_social_with_cadence
    # Phase 7: compute delta BEFORE lens prebuild so "needs_action" can
    # surface tickers that just appeared on the alpha board / posture focus
    # for the prebuild step to satisfy on this same nightly run.
    cmd_delta
    # Phase 1F: bump curated lens cap from 25→35 so a wider slice of the
    # curated short-list rotates each night.  ~+10 lenses ≈ +50–100 FMP/run.
    cmd_lenses_nightly --max=35
    # Phase 2B.3 freshness-first ordering: Gatekeeper refresh runs AFTER
    # forecast/alpha/lenses have updated the cached state but BEFORE the
    # risk-telemetry tail.  Cache-only per-ticker reads; the only provider
    # touch is the FMP earnings calendar (cached 6 h).  This keeps the
    # high-priority short list's Gatekeeper artifacts fresh for the next
    # premarket and the operator dashboard.
    cmd_gatekeeper_refresh
    cmd_resolve
    cmd_holdout
    # Phase 1B/1C/1D risk telemetry runs as a cache-only tail of the nightly
    # cycle so the dashboard's RISK TELEMETRY strip refreshes on the same
    # cadence as the rest of the post-close artifacts.  No provider calls,
    # no DB writes, no enforcement.
    cmd_risk_telemetry
    # Dashboard-freshness tail: two diagnostic sidecars that previously had no
    # cadence and silently rotted (scanner-truth was 10d stale, forecast
    # validation 40d stale, both shown on the dashboard as if current).  Both are
    # RESEARCH-ONLY / CACHE-ONLY (no providers, no DB writes, no signals) and run
    # in seconds, so they refresh on the nightly cadence with everything else.
    #   - scanner_truth_review   → cache/research/scanner_truth_summary_latest.json
    #                              (RISK TELEMETRY panel "scanner truth" line)
    #   - validate_regime_forecaster --cache-only
    #                              → regime_forecast_validation_latest.json
    #                              (MARKET FORECAST DETAIL "Validation" block)
    run_or_warn "scanner truth review" \
        "$PY" -m research.scanner_truth_review
    run_or_warn "regime forecast validation" \
        "$PY" -m research.validate_regime_forecaster --cache-only
    # Path B — Recall-Repair Shadow Lane.  Append today's dated board to the
    # historizer and refresh the forward-validation verdict.  RESEARCH-ONLY /
    # CACHE-ONLY (no providers, no DB writes, no signals, no routing); runs in
    # seconds.  Nightly cadence is what grows history_days toward the ≥10-day
    # decision gate.  The shadow forward runs AFTER the lane so it sees today's
    # newly-historized row.
    run_or_warn "recall-repair shadow lane" \
        "$PY" -m research.recall_repair_shadow_lane
    run_or_warn "recall-repair shadow forward" \
        "$PY" -m research.recall_repair_shadow_forward
    # Phase 2B.4 — cache-only provider + pipeline freshness audit as the
    # final tail of the nightly cycle.  No provider calls; reads the
    # existing cache_meta / fmp_endpoint_log tables to predict tomorrow's
    # premarket load and to verify the pipeline sidecars are fresh.
    cmd_provider_audit
    # Phase 1G.11 — regenerate the RS/theme triage cohort, then compose the MCP
    # audit session LAST so both read every sidecar this nightly cycle just
    # refreshed (forecast/alpha/lens/gatekeeper/risk-telemetry).  Both are
    # cache-only (the orchestrator never calls providers/MCP/Claude; triage's
    # only provider touch is the FMP earnings calendar, cached 6 h and already
    # warmed above).  They feed the dashboard's RS/Theme Triage strip and the
    # MCP AUDIT SUMMARY panel (incl. the RS/Theme Fwd line).  session=close
    # (nightly fires post-market); gatekeeper is already fresh from above so
    # --refresh-gatekeeper is intentionally omitted.
    cmd_rs_theme_triage
    cmd_mcp_audit_session close
    # Phase 4A/4A.2 — refresh the research-scanner watchlist + its dependent
    # sidecars (coverage confidence, change detector, forward-outcome tracker,
    # 10x candidate radar) and rebuild the quality-gated Daily Alpha Radar
    # report last, so it reads everything else this nightly cycle refreshed.
    # Cache-first; FMP calls are optional/degrade gracefully. Research-only —
    # no trade recommendations, no signals, no execution.
    cmd_research_scanner
    cmd_research_coverage
    cmd_research_changes
    cmd_research_forward_tracker
    cmd_ten_x_candidates
    cmd_daily_alpha_radar
    # Nightly Operator Summary — runs LAST so it reads every sidecar the
    # nightly cycle just refreshed.  Cache-only; no provider calls.
    # No strategy abbreviations or trade language in output.
    cmd_nightly_operator_summary
    log "nightly cycle complete"
}

# ----- usage ---------------------------------------------------------

usage() {
    sed -n '2,/^# =\+$/p' "$0" | sed 's/^# \{0,1\}//' | sed '$d'
}

# ----- arg parsing ---------------------------------------------------

if [[ $# -lt 1 ]]; then
    usage
    exit 64
fi

# First pass: capture flags before / after the subcommand.
RAW_ARGS=("$@")
SUB=""
POS=()
for a in "${RAW_ARGS[@]}"; do
    case "$a" in
        --force-social) FORCE_SOCIAL=1 ;;
        --dry-run)      DRY_RUN=1 ;;
        -h|--help|help) usage; exit 0 ;;
        *)
            if [[ -z "$SUB" ]]; then
                SUB="$a"
            else
                POS+=("$a")
            fi
            ;;
    esac
done

# Special: dry-run as positional subcommand wraps another subcommand.
if [[ "$SUB" == "dry-run" ]]; then
    DRY_RUN=1
    if [[ ${#POS[@]} -lt 1 ]]; then
        warn "dry-run needs a subcommand, e.g. 'dry-run nightly'"
        exit 64
    fi
    SUB="${POS[0]}"
    POS=("${POS[@]:1}")
fi

if [[ "$DRY_RUN" == "1" ]]; then
    note "DRY_RUN — no provider calls, no resolver, no writes"
fi

case "$SUB" in
    premarket)        cmd_premarket        "${POS[@]}" ;;
    midday)           cmd_midday           "${POS[@]}" ;;
    nightly)          cmd_nightly          "${POS[@]}" ;;
    resolve)          cmd_resolve          "${POS[@]}" ;;
    reports)          cmd_reports          "${POS[@]}" ;;
    holdout)          cmd_holdout          "${POS[@]}" ;;
    lens)             cmd_lens             "${POS[@]}" ;;
    gatekeeper)         cmd_gatekeeper         "${POS[@]}" ;;
    gatekeeper-refresh) cmd_gatekeeper_refresh "${POS[@]}" ;;
    provider-audit)     cmd_provider_audit     "${POS[@]}" ;;
    forecast)         cmd_forecast         "${POS[@]}" ;;
    options-regime)   cmd_options_regime   "${POS[@]}" ;;
    options-chain-snapshot)         cmd_options_chain_snapshot         "${POS[@]}" ;;
    options-chain-snapshot-quality) cmd_options_chain_snapshot_quality "${POS[@]}" ;;
    alpha)            cmd_alpha            "${POS[@]}" ;;
    alpha-overlay)    cmd_alpha_overlay    "${POS[@]}" ;;
    social)           cmd_social_with_cadence "${POS[@]}" ;;
    delta)            cmd_delta            "${POS[@]}" ;;
    lenses-nightly)   cmd_lenses_nightly   "${POS[@]}" ;;
    lenses-liquid)    cmd_lenses_liquid    "${POS[@]}" ;;
    alpha-lens-refresh) cmd_alpha_lens_refresh "${POS[@]}" ;;
    weekly-review)    cmd_weekly_review    "${POS[@]}" ;;
    risk-telemetry)   cmd_risk_telemetry   "${POS[@]}" ;;
    mcp-audit)         cmd_mcp_audit         "${POS[@]}" ;;
    mcp-audit-ticker)  cmd_mcp_audit_ticker  "${POS[@]}" ;;
    mcp-audit-session) cmd_mcp_audit_session "${POS[@]}" ;;
    legacy-diagnostics)      cmd_legacy_diagnostics      "${POS[@]}" ;;
    legacy-decision-policy)  cmd_legacy_decision_policy  "${POS[@]}" ;;
    short-radar)        cmd_short_radar        "${POS[@]}" ;;
    short-detection-audit)   cmd_short_detection_audit   "${POS[@]}" ;;
    short-detection-forward) cmd_short_detection_forward "${POS[@]}" ;;
    forward-health)     cmd_forward_health     "${POS[@]}" ;;
    leader-reset-study) cmd_leader_reset_study "${POS[@]}" ;;
    voyager-audit)      cmd_voyager_audit      "${POS[@]}" ;;
    strategy-tournament) cmd_strategy_tournament "${POS[@]}" ;;
    scanner-recall)     cmd_scanner_recall     "${POS[@]}" ;;
    rs-theme-triage)    cmd_rs_theme_triage    "${POS[@]}" ;;
    rs-theme-forward)   cmd_rs_theme_forward   "${POS[@]}" ;;
    gatekeeper-precision) cmd_gatekeeper_precision "${POS[@]}" ;;
    power-trend)        cmd_power_trend        "${POS[@]}" ;;
    freshness-audit)       cmd_freshness_audit       "${POS[@]}" ;;
    recall-shadow)         cmd_recall_shadow         "${POS[@]}" ;;
    recall-shadow-forward) cmd_recall_shadow_forward "${POS[@]}" ;;
    social-attention)         cmd_social_attention         "${POS[@]}" ;;
    social-attention-forward) cmd_social_attention_forward "${POS[@]}" ;;
    participation-audit)   cmd_participation_audit   "${POS[@]}" ;;
    recall-shadow-cohort-freeze) cmd_recall_shadow_cohort_freeze "${POS[@]}" ;;
    recall-shadow-gk-forward)    cmd_recall_shadow_gk_forward    "${POS[@]}" ;;
    sniper-starvation)     cmd_sniper_starvation     "${POS[@]}" ;;
    voyager-cache-audit)   cmd_voyager_cache_audit   "${POS[@]}" ;;
    holdout-feasibility)   cmd_holdout_feasibility   "${POS[@]}" ;;
    recall-shadow-feeder)  cmd_recall_shadow_feeder  "${POS[@]}" ;;
    emission-calibration)  cmd_emission_calibration  "${POS[@]}" ;;
    core-satellite)        cmd_core_satellite        "${POS[@]}" ;;
    core-satellite-levered) cmd_core_satellite_leveraged "${POS[@]}" ;;
    market-heartbeat)      cmd_market_heartbeat      "${POS[@]}" ;;
    research-scanner)      cmd_research_scanner      "${POS[@]}" ;;
    stock-research-card)   cmd_stock_research_card   "${POS[@]}" ;;
    fmp-provider-health)     cmd_fmp_provider_health     "${POS[@]}" ;;
    tradier-research-health) cmd_tradier_research_health "${POS[@]}" ;;
    provider-health)         cmd_provider_health         "${POS[@]}" ;;
    data-freshness)          cmd_data_freshness          "${POS[@]}" ;;
    sector-leadership)       cmd_sector_leadership       "${POS[@]}" ;;
    research-coverage)         cmd_research_coverage         "${POS[@]}" ;;
    research-changes)          cmd_research_changes          "${POS[@]}" ;;
    research-forward-tracker)  cmd_research_forward_tracker  "${POS[@]}" ;;
    ten-x-candidates)          cmd_ten_x_candidates          "${POS[@]}" ;;
    daily-alpha-radar)          cmd_daily_alpha_radar          "${POS[@]}" ;;
    nightly-operator-summary)  cmd_nightly_operator_summary   "${POS[@]}" ;;
    live-trade|paper-trade|place-order|send-order|submit-order|bracket-order|promote-strategy|strategy-execute|auto-route)
        _disabled_execution_cmd ;;
    "")               usage; exit 64 ;;
    *)
        warn "unknown subcommand: $SUB"
        usage
        exit 64
        ;;
esac
