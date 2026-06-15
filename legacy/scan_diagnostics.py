"""
scan_diagnostics.py — Phase 4 Scan Quality Diagnostics Engine

Analyzes recent scan runs from trading_performance.db and produces:
  - Funnel counts per run (scanned → scored → approved)
  - Top reject reasons by strategy
  - Rejection rate by regime + VIX bucket
  - Near-miss list (scored within 10 pts of threshold but rejected)
  - Reject reason stability (same dominant reason 3+ consecutive runs)
  - Suggestions based on patterns
  - JSON artifact to reports/scan_quality_latest.json
  - DB insert to scan_quality_reports table
"""

import sqlite3
import json
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from vix_snapshot import get_vix_snapshot

try:
    from rr_shadow_calibration import RRShadowCalibrationEngine as _RRShadow
    _HAS_RR_SHADOW = True
except ImportError:
    _RRShadow = None  # type: ignore
    _HAS_RR_SHADOW = False

try:
    from shadow_outcome_tracker import ShadowOutcomeTracker as _ShadowTracker, SHADOW_POLICIES as _SHADOW_POLICIES
    _HAS_SHADOW_TRACKER = True
except ImportError:
    _ShadowTracker = None
    _SHADOW_POLICIES = {}
    _HAS_SHADOW_TRACKER = False

try:
    from pilot_policy_controller import PilotPolicyController as _PilotCtrl
    _HAS_PILOT_CTRL = True
except ImportError:
    _PilotCtrl = None  # type: ignore
    _HAS_PILOT_CTRL = False

try:
    from options_intelligence import diagnose_options_feed as _diagnose_options_feed
    _HAS_OPTIONS_DIAG = True
except ImportError:
    _diagnose_options_feed = None  # type: ignore
    _HAS_OPTIONS_DIAG = False

try:
    from breakout_timing_engine import BreakoutTimingTracker as _BTETracker
    _HAS_BTE = True
except ImportError:
    _BTETracker = None  # type: ignore
    _HAS_BTE = False

try:
    from short_management_shadow_monitor import ShortManagementShadowMonitor as _ShortShadowMonitor
    _HAS_SHORT_SHADOW_MONITOR = True
except ImportError:
    _ShortShadowMonitor = None  # type: ignore
    _HAS_SHORT_SHADOW_MONITOR = False

# VIX threshold at which CONTRARIAN strategy activates
_CONTRARIAN_VIX_THRESHOLD = 28.0
# Warn when VIX is within this many points of the threshold
_VIX_PROXIMITY_WARN_DELTA = 3.0

logger = logging.getLogger(__name__)

DB_PATH = "trading_performance.db"
REPORTS_DIR = "reports"
REPORT_FILE = os.path.join(REPORTS_DIR, "scan_quality_latest.json")

# Maps historical strategy aliases to canonical names
_STRATEGY_ALIASES = {
    "REAPER": "CONTRARIAN",
    "CONTRARIAN": "CONTRARIAN",
    "SNIPER": "SNIPER",
    "VOYAGER": "VOYAGER",
    "REMORA": "REMORA",
    "SHORT": "SHORT",
}

_MODE_ALIASES = {
    "ALL": None,
    "SHOW_CONFLUENCE": "SHOW_CONFLUENCE",
    "RESEARCH": "SHOW_CONFLUENCE",
    "DAEMON": "UNIFIED_MASTER_V3",
    "UNIFIED_MASTER_V3": "UNIFIED_MASTER_V3",
    "UNIFIED_MASTER": "UNIFIED_MASTER",
}

_MODE_QUERY_ALIASES = {
    "UNIFIED_MASTER_V3": ("UNIFIED_MASTER_V3", "DAEMON"),
    "SHOW_CONFLUENCE": ("SHOW_CONFLUENCE",),
    "UNIFIED_MASTER": ("UNIFIED_MASTER",),
}

# Minimum score to qualify as "near miss" (threshold - this value)
NEAR_MISS_DELTA = 10.0

# Recommendation thresholds by strategy (score below this = not approved)
_SCORE_THRESHOLDS = {
    "SNIPER": 60.0,
    "VOYAGER": 60.0,
    "REMORA": 70.0,
    "SHORT": 55.0,
    "CONTRARIAN": 60.0,
}
_DEFAULT_THRESHOLD = 60.0

_LIVE_RR_THRESHOLDS = {
    "SNIPER": 2.5,
    "SHORT": 3.0,
    "VOYAGER": 2.0,
    "REMORA": 2.0,
    "CONTRARIAN": 1.5,
}


def _normalize_strategy(raw: Optional[str]) -> str:
    if not raw:
        return "UNKNOWN"
    return _STRATEGY_ALIASES.get(raw.upper().strip(), raw.upper().strip())


def _normalize_mode_filter(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    return _MODE_ALIASES.get(str(raw).upper().strip(), str(raw).upper().strip())


class ScanDiagnosticsEngine:
    """Reads scan data from DB and produces quality diagnostics."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._last_vix_snapshot: Optional[Dict[str, object]] = None
        self._ensure_table()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_diagnostics(
        self,
        days: int = 7,
        mode: Optional[str] = None,
        min_watchlist: int = 5,
        exclude_unknown_regime: bool = False,
        with_rr_shadow: bool = False,
        with_shadow_outcomes: bool = False,
        with_pilot: bool = False,
        with_rr_gap: bool = False,
        with_options_health: bool = False,
    ) -> dict:
        """
        Run full diagnostics for the specified window.

        Args:
            days: Look-back window in calendar days.
            mode: Filter by runs.mode (e.g. 'show_confluence', 'daemon'). None = all.
            min_watchlist: Exclude runs with fewer tickers (avoids test runs).
            exclude_unknown_regime: Hide UNKNOWN regime bucket in regime breakdown output.
            with_rr_shadow: Embed RR shadow calibration section (analysis-only).
            with_pilot: Include pilot events summary from pilot_events table (Phase 4.3).
            with_rr_gap: Include scanner-vs-execution RR gap summary.

        Returns:
            Diagnostics dict with funnel, reject reasons, near-misses, suggestions, etc.
        """
        mode_filter = _normalize_mode_filter(mode)
        runs = self._get_recent_runs(days=days, mode=mode_filter, min_watchlist=min_watchlist)
        if not runs:
            report = self._empty_report(
                days=days,
                mode=mode or "all",
                exclude_unknown_regime=exclude_unknown_regime,
            )
            self._save_json(report)
            return report

        run_ids = [r["run_id"] for r in runs]
        window_start = min(r["timestamp"] for r in runs)
        window_end = max(r["timestamp"] for r in runs)

        funnel = self._get_reject_funnel(run_ids)
        top_reasons = self._get_top_reject_reasons(run_ids)
        near_misses = self._get_near_misses(run_ids)
        gate_overlap = self._get_gate_overlap(run_ids)
        rr_threshold_diagnostics = self._get_rr_threshold_diagnostics(run_ids)
        regime_breakdown = self._get_rejection_by_regime(
            run_ids,
            exclude_unknown_regime=exclude_unknown_regime,
        )
        reason_stability = self._get_reason_stability(run_ids)
        live_vix = self._fetch_live_vix()
        suggestions = self._generate_suggestions(
            funnel,
            top_reasons,
            near_misses,
            reason_stability,
            gate_overlap,
            rr_threshold_diagnostics,
            live_vix=live_vix,
        )

        # Optional RR shadow calibration section (analysis-only, no execution impact)
        rr_shadow = None
        rr_shadow_suggestions = []
        if with_rr_shadow and _HAS_RR_SHADOW:
            try:
                shadow_engine = _RRShadow(db_path=self.db_path)
                rr_shadow = shadow_engine.run_calibration(
                    days=days, mode=mode_filter, min_watchlist=min_watchlist
                )
                rr_shadow_suggestions = rr_shadow.get("suggestions", [])
            except Exception as e:
                logger.warning(f"RR shadow calibration failed: {e}")

        # Optional shadow outcome summary
        shadow_outcome_summary = None
        if with_shadow_outcomes and _HAS_SHADOW_TRACKER:
            try:
                tracker = _ShadowTracker(db_path=self.db_path)
                tracker.compute_outcomes(horizon_days=None, retry_no_data=True)
                shadow_outcome_summary = tracker.get_summary()
            except Exception as e:
                logger.warning(f"shadow outcome summary failed: {e}")

        bte_advisory = None
        bte_score_comparison = None
        bte_short_advisory = None
        bte_short_score_comparison = None
        short_management_shadow = None
        if _HAS_BTE:
            try:
                bte_tracker = _BTETracker(db_path=self.db_path)
                bte_calibration = bte_tracker.get_calibration_report(
                    horizon_days=10,
                    min_segment_n=3,
                )
                bte_advisory = self._summarize_bte_calibration(bte_calibration)
                bte_compare = bte_tracker.get_score_comparison_report(
                    horizon_days=10,
                    min_segment_n=3,
                )
                bte_score_comparison = self._summarize_bte_score_comparison(bte_compare)
                bte_short_calibration = bte_tracker.get_short_calibration_report(
                    horizon_days=10,
                    min_segment_n=3,
                )
                bte_short_advisory = self._summarize_bte_short_calibration(bte_short_calibration)
                bte_short_compare = bte_tracker.get_short_score_comparison_report(
                    horizon_days=10,
                    min_segment_n=3,
                )
                bte_short_score_comparison = self._summarize_bte_short_score_comparison(bte_short_compare)
            except Exception as e:
                logger.warning(f"BTE advisory summary failed: {e}")
        if _HAS_SHORT_SHADOW_MONITOR:
            try:
                short_shadow_monitor = _ShortShadowMonitor(db_path=self.db_path)
                short_management_shadow = short_shadow_monitor.get_summary(
                    days=days,
                    run_ids=run_ids,
                )
            except Exception as e:
                logger.warning(f"short shadow monitor summary failed: {e}")

        # Optional pilot summary (Phase 4.3, read-only, off by default)
        pilot_summary = None
        if with_pilot and _HAS_PILOT_CTRL:
            try:
                pilot_summary = self._get_pilot_summary(days=days)
            except Exception as e:
                logger.warning(f"pilot summary failed: {e}")

        rr_execution_gap = None
        if with_rr_gap:
            try:
                rr_execution_gap = self._get_rr_execution_gap(run_ids)
            except Exception as e:
                logger.warning(f"rr execution gap failed: {e}")

        # Optional options feed health (calls diagnose_options_feed once)
        options_feed_health = None
        if with_options_health and _HAS_OPTIONS_DIAG:
            try:
                options_feed_health = _diagnose_options_feed("SPY")
                # Inject health-based suggestion if feed is offline
                src = (options_feed_health or {}).get("score_adj_source", "unavailable")
                token_set = (options_feed_health or {}).get("tradier_token_set", False)
                if src == "unavailable" and not token_set:
                    suggestions.append(
                        "Options feed offline — set TRADIER_API_TOKEN env var to enable real-time PCR/gamma scoring"
                    )
                elif src == "unavailable" and token_set:
                    suggestions.append(
                        "Options feed configured but returning no data — check TRADIER_USE_SANDBOX setting"
                    )
            except Exception as e:
                logger.warning(f"options feed health check failed: {e}")

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": days,
            "window_start": window_start,
            "window_end": window_end,
            "mode_filter": mode or "all",
            "min_watchlist": min_watchlist,
            "exclude_unknown_regime": bool(exclude_unknown_regime),
            "run_count": len(runs),
            "funnel": funnel,
            "top_reject_reasons": top_reasons,
            "near_misses": near_misses,
            "gate_overlap": gate_overlap,
            "rr_threshold_diagnostics": rr_threshold_diagnostics,
            "rejection_by_regime": regime_breakdown,
            "reason_stability": reason_stability,
            "suggestions": suggestions,
            "rr_shadow_calibration": rr_shadow,
            "rr_shadow_suggestions": rr_shadow_suggestions,
            "shadow_outcome_summary": shadow_outcome_summary,
            "bte_advisory": bte_advisory,
            "bte_score_comparison": bte_score_comparison,
            "bte_short_advisory": bte_short_advisory,
            "bte_short_score_comparison": bte_short_score_comparison,
            "short_management_shadow": short_management_shadow,
            "pilot_summary": pilot_summary,
            "rr_execution_gap": rr_execution_gap,
            "options_feed_health": options_feed_health,
            "live_vix": live_vix,
            "live_vix_source": (self._last_vix_snapshot or {}).get("source"),
            "live_vix_reason": (self._last_vix_snapshot or {}).get("reason"),
            "contrarian_vix_threshold": _CONTRARIAN_VIX_THRESHOLD,
        }

        self._save_json(report)
        self._insert_db_report(
            window_start=window_start,
            window_end=window_end,
            run_count=len(runs),
            payload=report,
        )
        return report

    def get_latest_snapshot(self) -> Optional[dict]:
        """
        Return the most recent diagnostics from DB (for dashboard display).
        Returns None if no reports exist yet.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT payload_json FROM scan_quality_reports ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return json.loads(row["payload_json"])
        except Exception as e:
            logger.debug(f"get_latest_snapshot: {e}")
        return None

    def print_report(self, report: dict) -> None:
        """Print a terminal-friendly summary of a diagnostics report."""
        sep = "─" * 60
        print(f"\n{'═' * 60}")
        print(f"  SCAN QUALITY REPORT")
        window_start = str(report.get('window_start') or "—")
        window_end = str(report.get('window_end') or "—")
        print(f"  Window: {report.get('window_days')}d  |  Runs: {report.get('run_count')}  |  Mode: {report.get('mode_filter')}")
        print(f"  {window_start[:19]}  →  {window_end[:19]}")
        print(f"{'═' * 60}")

        if not report.get("run_count"):
            print("\n  No runs matched the selected filters.")
            return

        funnel = report.get("funnel", {})
        if funnel:
            print(f"\n  FUNNEL (totals across all runs)")
            print(f"  {sep}")
            scanned = funnel.get("total_scanned", 0)
            approved = funnel.get("total_approved", 0)
            rejected = funnel.get("total_rejected", 0)
            rate = funnel.get("approval_rate_pct", 0.0)
            print(f"  Scanned:  {scanned:>6}")
            print(f"  Approved: {approved:>6}  ({rate:.1f}%)")
            print(f"  Rejected: {rejected:>6}")
            by_strat = funnel.get("by_strategy", {})
            if by_strat:
                print(f"\n  By strategy:")
                for strat, counts in sorted(by_strat.items()):
                    a = counts.get("approved", 0)
                    r = counts.get("rejected", 0)
                    total = a + r
                    pct = (a / total * 100) if total else 0
                    print(f"    {strat:<12}  approved={a}  rejected={r}  rate={pct:.0f}%")
            ce = int(funnel.get("contrarian_eligible_runs", 0) or 0)
            cs = int(funnel.get("contrarian_scanned_runs", 0) or 0)
            if ce or cs:
                print(f"\n  Contrarian runs: eligible={ce}  scanned={cs}")

        top_reasons = report.get("top_reject_reasons", [])
        if top_reasons:
            print(f"\n  TOP REJECT REASONS")
            print(f"  {sep}")
            for item in top_reasons[:10]:
                print(f"  [{item.get('count'):>4}]  {item.get('reason'):<35}  strat={item.get('strategy', 'ALL')}")

        near_misses = report.get("near_misses", [])
        if near_misses:
            print(f"\n  NEAR MISSES  (score within {NEAR_MISS_DELTA:.0f}pts of threshold)")
            print(f"  {sep}")
            for nm in near_misses[:10]:
                print(f"  {nm.get('ticker'):<6}  score={nm.get('score'):>5.1f}  threshold={nm.get('threshold'):.0f}  gap={nm.get('gap'):.1f}  strat={nm.get('strategy')}  reason={nm.get('reason')}")

        bte = report.get("bte_advisory") or {}
        if bte:
            print(f"\n  BTE ADVISORY")
            print(f"  {sep}")
            print(
                f"  Horizon={bte.get('horizon_days')}d  n={bte.get('overall_n', 0)}  "
                f"breakout_count={bte.get('overall_breakout_count', 0)}  "
                f"breakout_rate={bte.get('overall_breakout_rate')}"
            )
            if not bte.get("guidance_ready"):
                print(f"  Guidance not ready: {bte.get('guidance_reason') or 'insufficient_breakout_sample'}")
            else:
                if bte.get("top_candidate_state"):
                    print(
                        f"  Top state: {bte.get('top_candidate_state')}  "
                        f"window={bte.get('top_candidate_state_window') or '—'}"
                    )
                if bte.get("top_score_bucket"):
                    print(
                        f"  Best score bucket: {bte.get('top_score_bucket')}  "
                        f"lift={bte.get('top_score_bucket_lift')}  "
                        f"window={bte.get('top_score_bucket_window') or '—'}"
                    )
                if bte.get("top_composite_segment"):
                    print(
                        f"  Best composite: {bte.get('top_composite_segment')}  "
                        f"n={bte.get('top_composite_n', 0)}  "
                        f"rate={bte.get('top_composite_breakout_rate')}"
                    )
        bte_compare = report.get("bte_score_comparison") or {}
        if bte_compare:
            print(f"\n  BTE VS RAW SCORE")
            print(f"  {sep}")
            print(
                f"  Horizon={bte_compare.get('horizon_days')}d  n={bte_compare.get('overall_n', 0)}  "
                f"breakout_count={bte_compare.get('overall_breakout_count', 0)}  "
                f"breakout_rate={bte_compare.get('overall_breakout_rate')}"
            )
            if not bte_compare.get("guidance_ready"):
                print(
                    f"  Comparison not ready: "
                    f"{bte_compare.get('guidance_reason') or bte_compare.get('winner_reason') or 'insufficient_sample'}"
                )
            else:
                if bte_compare.get("best_bte_bucket"):
                    print(
                        f"  Best BTE bucket: {bte_compare.get('best_bte_bucket')}  "
                        f"lift={bte_compare.get('best_bte_bucket_lift')}  "
                        f"window={bte_compare.get('best_bte_bucket_window') or '—'}"
                    )
                if bte_compare.get("best_raw_bucket"):
                    print(
                        f"  Best raw score bucket: {bte_compare.get('best_raw_bucket')}  "
                        f"lift={bte_compare.get('best_raw_bucket_lift')}  "
                        f"window={bte_compare.get('best_raw_bucket_window') or '—'}"
                    )
                if bte_compare.get("winner_dimension"):
                    print(f"  Current edge leader: {bte_compare.get('winner_dimension')}")

        bte_short = report.get("bte_short_advisory") or {}
        if bte_short:
            print(f"\n  BTE SHORT ADVISORY")
            print(f"  {sep}")
            print(
                f"  Horizon={bte_short.get('horizon_days')}d  n={bte_short.get('overall_n', 0)}  "
                f"breakdown_count={bte_short.get('overall_breakdown_count', 0)}  "
                f"breakdown_rate={bte_short.get('overall_breakdown_rate')}"
            )
            if not bte_short.get("guidance_ready"):
                print(f"  Guidance not ready: {bte_short.get('guidance_reason') or 'insufficient_breakdown_sample'}")
            else:
                if bte_short.get("top_candidate_state"):
                    print(
                        f"  Top state: {bte_short.get('top_candidate_state')}  "
                        f"window={bte_short.get('top_candidate_state_window') or '—'}"
                    )
                if bte_short.get("top_score_bucket"):
                    print(
                        f"  Best score bucket: {bte_short.get('top_score_bucket')}  "
                        f"lift={bte_short.get('top_score_bucket_lift')}  "
                        f"window={bte_short.get('top_score_bucket_window') or '—'}"
                    )
                if bte_short.get("top_composite_segment"):
                    print(
                        f"  Best composite: {bte_short.get('top_composite_segment')}  "
                        f"n={bte_short.get('top_composite_n', 0)}  "
                        f"rate={bte_short.get('top_composite_breakdown_rate')}"
                    )

        bte_short_compare = report.get("bte_short_score_comparison") or {}
        if bte_short_compare:
            print(f"\n  BTE SHORT VS RAW SCORE")
            print(f"  {sep}")
            print(
                f"  Horizon={bte_short_compare.get('horizon_days')}d  n={bte_short_compare.get('overall_n', 0)}  "
                f"breakdown_count={bte_short_compare.get('overall_breakdown_count', 0)}  "
                f"breakdown_rate={bte_short_compare.get('overall_breakdown_rate')}"
            )
            if not bte_short_compare.get("guidance_ready"):
                print(
                    f"  Comparison not ready: "
                    f"{bte_short_compare.get('guidance_reason') or bte_short_compare.get('winner_reason') or 'insufficient_sample'}"
                )
            else:
                if bte_short_compare.get("best_bte_bucket"):
                    print(
                        f"  Best BTE bucket: {bte_short_compare.get('best_bte_bucket')}  "
                        f"lift={bte_short_compare.get('best_bte_bucket_lift')}  "
                        f"window={bte_short_compare.get('best_bte_bucket_window') or '—'}"
                    )
                if bte_short_compare.get("best_raw_bucket"):
                    print(
                        f"  Best raw score bucket: {bte_short_compare.get('best_raw_bucket')}  "
                        f"lift={bte_short_compare.get('best_raw_bucket_lift')}  "
                        f"window={bte_short_compare.get('best_raw_bucket_window') or '—'}"
                    )
                if bte_short_compare.get("winner_dimension"):
                    print(f"  Current edge leader: {bte_short_compare.get('winner_dimension')}")

        short_shadow = report.get("short_management_shadow") or {}
        if short_shadow:
            selector = short_shadow.get("selector") or {}
            print(f"\n  SHORT SHADOW MONITOR")
            print(f"  {sep}")
            print(
                f"  Rule: "
                f"{'/'.join(selector.get('candidate_state') or ['ALL'])} + "
                f"{'/'.join(selector.get('decision_cohort') or ['ALL'])} + "
                f"{'/'.join(selector.get('regime_status') or ['ALL'])}/"
                f"{'/'.join(selector.get('regime_volatility') or ['ALL'])} "
                f"-> {short_shadow.get('shadow_policy_name') or '—'}"
            )
            print(
                f"  Logged={short_shadow.get('total_logged', 0)}  "
                f"matched={short_shadow.get('matched_count', 0)}  "
                f"rate={short_shadow.get('matched_rate')}"
            )
            matched_cohorts = short_shadow.get("matched_cohorts") or []
            if matched_cohorts:
                top = matched_cohorts[0]
                print(f"  Top matched cohort: {top.get('label')}  n={top.get('n', 0)}")
            recent_matches = short_shadow.get("recent_matches") or []
            if recent_matches:
                latest = recent_matches[0]
                print(
                    f"  Latest match: {latest.get('ticker')}  "
                    f"{latest.get('decision_cohort')}  "
                    f"{latest.get('candidate_state')}  "
                    f"{latest.get('decision_timestamp')}"
                )

        gate_overlap = report.get("gate_overlap", {})
        top_combos = gate_overlap.get("top_combos", []) if isinstance(gate_overlap, dict) else []
        coverage = gate_overlap.get("coverage", {}) if isinstance(gate_overlap, dict) else {}
        if top_combos:
            print(f"\n  GATE OVERLAP (notes.gates_failed)")
            print(f"  {sep}")
            for item in top_combos[:5]:
                print(
                    f"  {item.get('combo', 'unknown'):<18}  n={item.get('count', 0):>4}  "
                    f"{item.get('pct_of_tagged', 0):>5.1f}%"
                )
            if coverage:
                print(
                    f"  Coverage: {coverage.get('rows_with_gates', 0)}/"
                    f"{coverage.get('total_reject_rows', 0)} rejects "
                    f"({coverage.get('coverage_pct', 0):.1f}%)"
                )

        regime_bd = report.get("rejection_by_regime", [])
        if regime_bd:
            print(f"\n  REJECTION RATE BY REGIME")
            print(f"  {sep}")
            for rb in regime_bd:
                print(f"  {rb.get('regime'):<14}  rejected={rb.get('rejected'):>4}  approved={rb.get('approved'):>4}  rate={rb.get('rejection_rate_pct'):.0f}%")
            if report.get("exclude_unknown_regime"):
                print("  (UNKNOWN regime excluded)")

        stability = report.get("reason_stability", {})
        if stability.get("stable"):
            dom = stability.get("dominant_reason", "")
            streak = stability.get("consecutive_runs", 0)
            print(f"\n  ⚠  STABLE REJECT PATTERN: '{dom}' dominant in {streak} consecutive runs")

        suggestions = report.get("suggestions", [])
        if suggestions:
            print(f"\n  SUGGESTIONS")
            print(f"  {sep}")
            for s in suggestions:
                print(f"  • {s}")

        print(f"\n{'═' * 60}\n")

    # ------------------------------------------------------------------
    # Internal: data queries
    # ------------------------------------------------------------------

    def _get_recent_runs(
        self,
        days: int,
        mode: Optional[str],
        min_watchlist: int,
    ) -> List[dict]:
        """Return run rows within the window that meet min_watchlist."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Schema-adaptive: check which columns exist in runs
            cur.execute("PRAGMA table_info(runs)")
            run_cols = {r["name"] for r in cur.fetchall()}

            where_parts = ["timestamp >= ?"]
            params: list = [cutoff]

            if min_watchlist and "watchlist_size" in run_cols:
                where_parts.append("(watchlist_size IS NULL OR watchlist_size >= ?)")
                params.append(min_watchlist)

            if mode and mode != "all" and "mode" in run_cols:
                mode_aliases = _MODE_QUERY_ALIASES.get(str(mode).upper().strip(), (mode,))
                placeholders = ",".join("UPPER(?)" for _ in mode_aliases)
                where_parts.append(f"UPPER(mode) IN ({placeholders})")
                params.extend(mode_aliases)

            where_clause = " AND ".join(where_parts)
            cur.execute(
                f"SELECT run_id, timestamp, mode, watchlist_size FROM runs WHERE {where_clause} ORDER BY timestamp ASC",
                params,
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"_get_recent_runs error: {e}")
            return []

    def _get_reject_funnel(self, run_ids: List[str]) -> dict:
        """
        Compute funnel counts: total scanned, approved, rejected, by strategy.
        """
        if not run_ids:
            return {}
        placeholders = ",".join("?" * len(run_ids))
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Schema-adaptive
            cur.execute("PRAGMA table_info(decisions)")
            dcols = {r["name"] for r in cur.fetchall()}

            has_exec_denied = "execution_denied" in dcols
            has_strategy = "strategy" in dcols

            # Total scanned = all decision rows for these runs
            cur.execute(
                f"SELECT COUNT(*) as n FROM decisions WHERE run_id IN ({placeholders})",
                run_ids,
            )
            total_scanned = cur.fetchone()["n"]

            # Approved = execution_denied=0 AND council_decision != 'SCANNER_REJECT'
            if has_exec_denied:
                cur.execute(
                    f"SELECT COUNT(*) as n FROM decisions WHERE run_id IN ({placeholders}) AND execution_denied=0 AND council_decision != 'SCANNER_REJECT'",
                    run_ids,
                )
            else:
                cur.execute(
                    f"SELECT COUNT(*) as n FROM decisions WHERE run_id IN ({placeholders}) AND council_decision != 'SCANNER_REJECT'",
                    run_ids,
                )
            total_approved = cur.fetchone()["n"]

            # Rejected = execution_denied=1 OR council_decision='SCANNER_REJECT'
            if has_exec_denied:
                cur.execute(
                    f"SELECT COUNT(*) as n FROM decisions WHERE run_id IN ({placeholders}) AND (execution_denied=1 OR council_decision='SCANNER_REJECT')",
                    run_ids,
                )
            else:
                cur.execute(
                    f"SELECT COUNT(*) as n FROM decisions WHERE run_id IN ({placeholders}) AND council_decision='SCANNER_REJECT'",
                    run_ids,
                )
            total_rejected = cur.fetchone()["n"]

            approval_rate = (total_approved / total_scanned * 100) if total_scanned else 0.0

            by_strategy: Dict[str, dict] = {}
            if has_strategy:
                cur.execute(
                    f"SELECT strategy, execution_denied, COUNT(*) as n FROM decisions WHERE run_id IN ({placeholders}) GROUP BY strategy, execution_denied",
                    run_ids,
                )
                for row in cur.fetchall():
                    strat = _normalize_strategy(row["strategy"])
                    denied = row["execution_denied"] if has_exec_denied else 0
                    count = row["n"]
                    bucket = by_strategy.setdefault(strat, {"approved": 0, "rejected": 0})
                    if denied == 1:
                        bucket["rejected"] += count
                    else:
                        bucket["approved"] += count

            contrarian_eligible_runs = 0
            contrarian_scanned_runs = 0
            try:
                cur.execute("PRAGMA table_info(runs)")
                rcols = {r["name"] for r in cur.fetchall()}
                if {"regime_status", "regime_volatility"}.issubset(rcols):
                    cur.execute(
                        f"""
                        SELECT run_id,
                               COALESCE(UPPER(TRIM(regime_status)), 'UNKNOWN') AS status,
                               COALESCE(UPPER(TRIM(regime_volatility)), 'UNKNOWN') AS vol
                        FROM runs
                        WHERE run_id IN ({placeholders})
                        """,
                        run_ids,
                    )
                    for row in cur.fetchall():
                        status = row["status"] or "UNKNOWN"
                        vol = row["vol"] or "UNKNOWN"
                        if vol in ("HIGH", "EXTREME") or status in ("BEAR", "CORRECTION"):
                            contrarian_eligible_runs += 1

                if has_strategy:
                    cur.execute(
                        f"""
                        SELECT COUNT(DISTINCT run_id) AS n
                        FROM decisions
                        WHERE run_id IN ({placeholders})
                          AND UPPER(COALESCE(strategy, '')) IN ('CONTRARIAN', 'REAPER')
                        """,
                        run_ids,
                    )
                    row = cur.fetchone()
                    contrarian_scanned_runs = int((row["n"] if row else 0) or 0)
                    # Contrarian scanner can run with zero decisions when no setups pass.
                    # In that case, use eligible runs as scan-attempt proxy.
                    if contrarian_scanned_runs == 0 and contrarian_eligible_runs > 0:
                        contrarian_scanned_runs = contrarian_eligible_runs
            except Exception:
                contrarian_eligible_runs = 0
                contrarian_scanned_runs = 0

            conn.close()
            return {
                "total_scanned": total_scanned,
                "total_approved": total_approved,
                "total_rejected": total_rejected,
                "approval_rate_pct": round(approval_rate, 2),
                "by_strategy": by_strategy,
                "contrarian_eligible_runs": int(contrarian_eligible_runs),
                "contrarian_scanned_runs": int(contrarian_scanned_runs),
            }
        except Exception as e:
            logger.error(f"_get_reject_funnel error: {e}")
            return {}

    def _get_top_reject_reasons(self, run_ids: List[str]) -> List[dict]:
        """
        Return top reject reasons (by count), with strategy breakdown.
        Uses execution_deny_reason column (schema-adaptive).
        """
        if not run_ids:
            return []
        placeholders = ",".join("?" * len(run_ids))
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("PRAGMA table_info(decisions)")
            dcols = {r["name"] for r in cur.fetchall()}

            if "execution_deny_reason" not in dcols:
                conn.close()
                return []

            has_strategy = "strategy" in dcols
            strat_sel = ", strategy" if has_strategy else ""
            strat_grp = ", strategy" if has_strategy else ""

            cur.execute(
                f"""
                SELECT execution_deny_reason as reason{strat_sel}, COUNT(*) as cnt
                FROM decisions
                WHERE run_id IN ({placeholders})
                  AND execution_deny_reason IS NOT NULL
                  AND execution_deny_reason != ''
                GROUP BY execution_deny_reason{strat_grp}
                ORDER BY cnt DESC
                LIMIT 30
                """,
                run_ids,
            )
            rows = cur.fetchall()
            conn.close()

            # Merge per-strategy rows into aggregated list
            seen: Dict[Tuple, int] = {}
            for row in rows:
                reason = row["reason"] or "unknown"
                strat = _normalize_strategy(row["strategy"] if has_strategy else None)
                key = (reason, strat)
                seen[key] = seen.get(key, 0) + row["cnt"]

            result = [
                {"reason": k[0], "strategy": k[1], "count": v}
                for k, v in sorted(seen.items(), key=lambda x: -x[1])
            ]
            return result[:20]
        except Exception as e:
            logger.error(f"_get_top_reject_reasons error: {e}")
            return []

    @staticmethod
    def _summarize_bte_calibration(report: dict) -> Optional[dict]:
        if not report:
            return None
        overall = report.get("overall") or {}
        dims = report.get("dimensions") or {}
        state_rows = dims.get("candidate_state") or []
        score_rows = dims.get("score_bucket") or []
        composite_rows = report.get("composite_segments") or []

        def _best(rows: List[dict]) -> Optional[dict]:
            eligible = [row for row in rows if row.get("guidance_eligible")]
            if not eligible:
                return None
            eligible.sort(
                key=lambda row: (
                    -(float(row.get("lift_vs_overall") or 0.0)),
                    -(float(row.get("breakout_rate") or 0.0)),
                    -int(row.get("n") or 0),
                )
            )
            return eligible[0]

        top_state = _best(state_rows)
        top_score = _best(score_rows)
        top_composite = _best(composite_rows)

        def _window(row: Optional[dict]) -> Optional[str]:
            if not row:
                return None
            low = row.get("timing_window_low_days")
            high = row.get("timing_window_high_days")
            if low is None or high is None:
                return None
            return f"{low:g}-{high:g}d"

        return {
            "model_version": report.get("model_version"),
            "horizon_days": report.get("horizon_days"),
            "guidance_ready": bool(report.get("guidance_ready")),
            "guidance_reason": report.get("guidance_reason"),
            "overall_n": int(overall.get("n") or 0),
            "overall_breakout_count": int(overall.get("breakout_count") or 0),
            "overall_breakout_rate": overall.get("breakout_rate"),
            "top_candidate_state": top_state.get("label") if top_state else None,
            "top_candidate_state_lift": top_state.get("lift_vs_overall") if top_state else None,
            "top_candidate_state_window": _window(top_state),
            "top_score_bucket": top_score.get("label") if top_score else None,
            "top_score_bucket_lift": top_score.get("lift_vs_overall") if top_score else None,
            "top_score_bucket_window": _window(top_score),
            "top_composite_segment": top_composite.get("label") if top_composite else None,
            "top_composite_n": int(top_composite.get("n") or 0) if top_composite else 0,
            "top_composite_breakout_rate": top_composite.get("breakout_rate") if top_composite else None,
            "top_composite_window": _window(top_composite),
        }

    @staticmethod
    def _summarize_bte_short_calibration(report: dict) -> Optional[dict]:
        if not report:
            return None
        overall = report.get("overall") or {}
        dims = report.get("dimensions") or {}
        state_rows = dims.get("candidate_state") or []
        score_rows = dims.get("score_bucket") or []
        composite_rows = report.get("composite_segments") or []

        def _best(rows: List[dict]) -> Optional[dict]:
            eligible = [row for row in rows if row.get("guidance_eligible")]
            if not eligible:
                return None
            eligible.sort(
                key=lambda row: (
                    -(float(row.get("lift_vs_overall") or 0.0)),
                    -(float(row.get("breakout_rate") or 0.0)),
                    -int(row.get("n") or 0),
                )
            )
            return eligible[0]

        top_state = _best(state_rows)
        top_score = _best(score_rows)
        top_composite = _best(composite_rows)

        def _window(row: Optional[dict]) -> Optional[str]:
            if not row:
                return None
            low = row.get("timing_window_low_days")
            high = row.get("timing_window_high_days")
            if low is None or high is None:
                return None
            return f"{low:g}-{high:g}d"

        return {
            "model_version": report.get("model_version"),
            "horizon_days": report.get("horizon_days"),
            "guidance_ready": bool(report.get("guidance_ready")),
            "guidance_reason": report.get("guidance_reason"),
            "overall_n": int(overall.get("n") or 0),
            "overall_breakdown_count": int(overall.get("breakout_count") or 0),
            "overall_breakdown_rate": overall.get("breakout_rate"),
            "top_candidate_state": top_state.get("label") if top_state else None,
            "top_candidate_state_lift": top_state.get("lift_vs_overall") if top_state else None,
            "top_candidate_state_window": _window(top_state),
            "top_score_bucket": top_score.get("label") if top_score else None,
            "top_score_bucket_lift": top_score.get("lift_vs_overall") if top_score else None,
            "top_score_bucket_window": _window(top_score),
            "top_composite_segment": top_composite.get("label") if top_composite else None,
            "top_composite_n": int(top_composite.get("n") or 0) if top_composite else 0,
            "top_composite_breakdown_rate": top_composite.get("breakout_rate") if top_composite else None,
            "top_composite_window": _window(top_composite),
        }

    @staticmethod
    def _summarize_bte_score_comparison(report: dict) -> Optional[dict]:
        if not report:
            return None
        overall = report.get("overall") or {}
        dims = report.get("dimensions") or {}
        bte_rows = dims.get("bte_pre_breakout_score") or []
        raw_rows = dims.get("raw_sniper_score") or []

        def _best(rows: List[dict]) -> Optional[dict]:
            eligible = [row for row in rows if row.get("guidance_eligible")]
            if not eligible:
                return None
            eligible.sort(
                key=lambda row: (
                    -(float(row.get("lift_vs_overall") or 0.0)),
                    -(float(row.get("breakout_rate") or 0.0)),
                    -int(row.get("n") or 0),
                )
            )
            return eligible[0]

        best_bte = _best(bte_rows)
        best_raw = _best(raw_rows)

        def _window(row: Optional[dict]) -> Optional[str]:
            if not row:
                return None
            low = row.get("timing_window_low_days")
            high = row.get("timing_window_high_days")
            if low is None or high is None:
                return None
            return f"{low:g}-{high:g}d"

        return {
            "model_version": report.get("model_version"),
            "horizon_days": report.get("horizon_days"),
            "guidance_ready": bool(report.get("guidance_ready")),
            "guidance_reason": report.get("guidance_reason"),
            "overall_n": int(overall.get("n") or 0),
            "overall_breakout_count": int(overall.get("breakout_count") or 0),
            "overall_breakout_rate": overall.get("breakout_rate"),
            "best_bte_bucket": best_bte.get("label") if best_bte else None,
            "best_bte_bucket_lift": best_bte.get("lift_vs_overall") if best_bte else None,
            "best_bte_bucket_window": _window(best_bte),
            "best_raw_bucket": best_raw.get("label") if best_raw else None,
            "best_raw_bucket_lift": best_raw.get("lift_vs_overall") if best_raw else None,
            "best_raw_bucket_window": _window(best_raw),
            "winner_dimension": report.get("winner_dimension"),
            "winner_reason": report.get("winner_reason"),
        }

    @staticmethod
    def _summarize_bte_short_score_comparison(report: dict) -> Optional[dict]:
        if not report:
            return None
        overall = report.get("overall") or {}
        dims = report.get("dimensions") or {}
        bte_rows = dims.get("bte_pre_breakdown_score") or []
        raw_rows = dims.get("raw_short_score") or []

        def _best(rows: List[dict]) -> Optional[dict]:
            eligible = [row for row in rows if row.get("guidance_eligible")]
            if not eligible:
                return None
            eligible.sort(
                key=lambda row: (
                    -(float(row.get("lift_vs_overall") or 0.0)),
                    -(float(row.get("breakout_rate") or 0.0)),
                    -int(row.get("n") or 0),
                )
            )
            return eligible[0]

        best_bte = _best(bte_rows)
        best_raw = _best(raw_rows)

        def _window(row: Optional[dict]) -> Optional[str]:
            if not row:
                return None
            low = row.get("timing_window_low_days")
            high = row.get("timing_window_high_days")
            if low is None or high is None:
                return None
            return f"{low:g}-{high:g}d"

        return {
            "model_version": report.get("model_version"),
            "horizon_days": report.get("horizon_days"),
            "guidance_ready": bool(report.get("guidance_ready")),
            "guidance_reason": report.get("guidance_reason"),
            "overall_n": int(overall.get("n") or 0),
            "overall_breakdown_count": int(overall.get("breakout_count") or 0),
            "overall_breakdown_rate": overall.get("breakout_rate"),
            "best_bte_bucket": best_bte.get("label") if best_bte else None,
            "best_bte_bucket_lift": best_bte.get("lift_vs_overall") if best_bte else None,
            "best_bte_bucket_window": _window(best_bte),
            "best_raw_bucket": best_raw.get("label") if best_raw else None,
            "best_raw_bucket_lift": best_raw.get("lift_vs_overall") if best_raw else None,
            "best_raw_bucket_window": _window(best_raw),
            "winner_dimension": report.get("winner_dimension"),
            "winner_reason": report.get("winner_reason"),
        }

    def _get_near_misses(self, run_ids: List[str]) -> List[dict]:
        """
        Find tickers that scored within NEAR_MISS_DELTA points of their strategy threshold
        but were still rejected. Sorted by gap ascending (closest to approval first).
        """
        if not run_ids:
            return []
        placeholders = ",".join("?" * len(run_ids))
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("PRAGMA table_info(decisions)")
            dcols = {r["name"] for r in cur.fetchall()}

            needed = {"execution_denied", "avg_score", "execution_deny_reason"}
            if not needed.issubset(dcols):
                conn.close()
                return []

            has_strategy = "strategy" in dcols
            has_ticker = "ticker" in dcols
            has_timestamp = "timestamp" in dcols

            strat_sel = ", strategy" if has_strategy else ""
            ticker_sel = ", ticker" if has_ticker else ""
            ts_sel = ", timestamp" if has_timestamp else ""

            cur.execute(
                f"""
                SELECT avg_score as score, execution_deny_reason as reason{strat_sel}{ticker_sel}{ts_sel}
                FROM decisions
                WHERE run_id IN ({placeholders})
                  AND execution_denied = 1
                  AND avg_score IS NOT NULL
                  AND avg_score > 0
                ORDER BY avg_score DESC
                LIMIT 200
                """,
                run_ids,
            )
            rows = cur.fetchall()
            conn.close()

            near_misses = []
            for row in rows:
                score = row["score"]
                strat = _normalize_strategy(row["strategy"] if has_strategy else None)
                threshold = _SCORE_THRESHOLDS.get(strat, _DEFAULT_THRESHOLD)
                gap = threshold - score
                if 0 < gap <= NEAR_MISS_DELTA:
                    near_misses.append({
                        "ticker": row["ticker"] if has_ticker else "?",
                        "score": round(score, 1),
                        "threshold": threshold,
                        "gap": round(gap, 1),
                        "strategy": strat,
                        "reason": row["reason"] if "reason" in row.keys() else "",
                        "timestamp": row["timestamp"] if has_timestamp else "",
                    })

            near_misses.sort(key=lambda x: x["gap"])
            return near_misses[:20]
        except Exception as e:
            logger.error(f"_get_near_misses error: {e}")
            return []

    def _get_gate_overlap(self, run_ids: List[str]) -> dict:
        """
        Parse notes.gates_failed arrays from reject rows and summarize overlap.

        Expected notes payload shape (from scanner reject logging):
            {"type":"scanner_reject", "gates_failed":["rr","pathway",...], ...}
        """
        if not run_ids:
            return {}
        placeholders = ",".join("?" * len(run_ids))
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("PRAGMA table_info(decisions)")
            dcols = {r["name"] for r in cur.fetchall()}
            if "notes" not in dcols:
                conn.close()
                return {}

            has_exec_denied = "execution_denied" in dcols
            has_strategy = "strategy" in dcols
            strat_sel = ", strategy" if has_strategy else ""
            reject_clause = "execution_denied = 1" if has_exec_denied else "council_decision = 'SCANNER_REJECT'"

            cur.execute(
                f"""
                SELECT notes{strat_sel}
                FROM decisions
                WHERE run_id IN ({placeholders})
                  AND ({reject_clause})
                  AND notes IS NOT NULL
                  AND TRIM(notes) != ''
                """,
                run_ids,
            )
            rows = cur.fetchall()
            conn.close()

            combo_counts: Dict[str, int] = {}
            by_strategy: Dict[str, Dict[str, int]] = {}
            tagged_rows = 0
            missing_tag_rows = 0

            for row in rows:
                notes_raw = row["notes"]
                strat = _normalize_strategy(row["strategy"] if has_strategy else None)
                try:
                    notes = json.loads(notes_raw)
                except Exception:
                    missing_tag_rows += 1
                    continue
                gates = notes.get("gates_failed")
                if not isinstance(gates, list) or not gates:
                    missing_tag_rows += 1
                    continue

                norm_gates = []
                for g in gates:
                    key = str(g or "").strip().lower()
                    if not key:
                        continue
                    key = key.replace("-", "_")
                    if key not in ("rr", "pathway", "score"):
                        key = "other"
                    norm_gates.append(key)
                norm_gates = sorted(set(norm_gates))
                if not norm_gates:
                    missing_tag_rows += 1
                    continue

                tagged_rows += 1
                combo = "+".join(norm_gates)
                combo_counts[combo] = combo_counts.get(combo, 0) + 1
                strat_bucket = by_strategy.setdefault(strat, {})
                strat_bucket[combo] = strat_bucket.get(combo, 0) + 1

            if not combo_counts:
                return {
                    "coverage": {
                        "rows_with_gates": 0,
                        "rows_without_gates": len(rows),
                        "total_reject_rows": len(rows),
                        "coverage_pct": 0.0,
                    },
                    "top_combos": [],
                    "by_strategy": {},
                }

            total_tagged = max(1, tagged_rows)
            top_combos = []
            for combo, count in sorted(combo_counts.items(), key=lambda x: -x[1])[:10]:
                top_combos.append({
                    "combo": combo,
                    "count": count,
                    "pct_of_tagged": round(count / total_tagged * 100, 1),
                })

            strat_summary: Dict[str, dict] = {}
            for strat, combos in sorted(by_strategy.items()):
                if not combos:
                    continue
                top_combo, top_count = max(combos.items(), key=lambda x: x[1])
                strat_total = sum(combos.values())
                strat_summary[strat] = {
                    "top_combo": top_combo,
                    "top_count": top_count,
                    "top_pct": round(top_count / max(1, strat_total) * 100, 1),
                }

            total_rows = tagged_rows + missing_tag_rows
            return {
                "coverage": {
                    "rows_with_gates": tagged_rows,
                    "rows_without_gates": missing_tag_rows,
                    "total_reject_rows": total_rows,
                    "coverage_pct": round(tagged_rows / max(1, total_rows) * 100, 1),
                },
                "top_combos": top_combos,
                "by_strategy": strat_summary,
            }
        except Exception as e:
            logger.error(f"_get_gate_overlap error: {e}")
            return {}

    def _get_rejection_by_regime(
        self,
        run_ids: List[str],
        exclude_unknown_regime: bool = False,
    ) -> List[dict]:
        """
        Rejection rate bucketed by regime_status (from decisions or runs table).
        """
        if not run_ids:
            return []
        placeholders = ",".join("?" * len(run_ids))
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("PRAGMA table_info(decisions)")
            dcols = {r["name"] for r in cur.fetchall()}

            # Use regime_status from decisions if available, else skip
            if "regime_status" not in dcols or "execution_denied" not in dcols:
                conn.close()
                return []

            cur.execute(
                f"""
                SELECT COALESCE(NULLIF(TRIM(UPPER(regime_status)), ''), 'UNKNOWN') as regime,
                       execution_denied,
                       COUNT(*) as cnt
                FROM decisions
                WHERE run_id IN ({placeholders})
                GROUP BY COALESCE(NULLIF(TRIM(UPPER(regime_status)), ''), 'UNKNOWN'), execution_denied
                ORDER BY regime
                """,
                run_ids,
            )
            rows = cur.fetchall()
            conn.close()

            buckets: Dict[str, dict] = {}
            for row in rows:
                regime = row["regime"] or "UNKNOWN"
                b = buckets.setdefault(regime, {"approved": 0, "rejected": 0})
                if row["execution_denied"] == 1:
                    b["rejected"] += row["cnt"]
                else:
                    b["approved"] += row["cnt"]

            result = []
            for regime, b in sorted(buckets.items()):
                if exclude_unknown_regime and regime == "UNKNOWN":
                    continue
                total = b["approved"] + b["rejected"]
                rate = (b["rejected"] / total * 100) if total else 0.0
                result.append({
                    "regime": regime,
                    "approved": b["approved"],
                    "rejected": b["rejected"],
                    "rejection_rate_pct": round(rate, 1),
                })
            return result
        except Exception as e:
            logger.error(f"_get_rejection_by_regime error: {e}")
            return []

    def _get_rr_threshold_diagnostics(self, run_ids: List[str]) -> dict:
        """
        Summarize RR reject pressure versus live thresholds by strategy.
        """
        if not run_ids:
            return {}
        placeholders = ",".join("?" * len(run_ids))
        out: Dict[str, dict] = {}
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(decisions)")
            dcols = {r["name"] for r in cur.fetchall()}
            needed = {"strategy", "rr", "execution_deny_reason", "execution_denied", "council_decision"}
            if not needed.issubset(dcols):
                conn.close()
                return {}

            cur.execute(
                f"""
                SELECT UPPER(COALESCE(strategy, 'UNKNOWN')) AS strategy,
                       COUNT(*) AS reject_count,
                       SUM(CASE WHEN execution_deny_reason='risk_reward_too_low' THEN 1 ELSE 0 END) AS rr_reject_count,
                       MAX(CASE WHEN rr IS NOT NULL THEN rr END) AS max_rr
                FROM decisions
                WHERE run_id IN ({placeholders})
                  AND (execution_denied=1 OR council_decision='SCANNER_REJECT')
                GROUP BY UPPER(COALESCE(strategy, 'UNKNOWN'))
                """,
                run_ids,
            )
            rows = cur.fetchall()
            conn.close()

            for row in rows:
                strat = _normalize_strategy(row["strategy"])
                max_rr = row["max_rr"]
                out[strat] = {
                    "reject_count": int(row["reject_count"] or 0),
                    "rr_reject_count": int(row["rr_reject_count"] or 0),
                    "max_rr": round(float(max_rr), 2) if max_rr is not None else None,
                    "live_threshold": _LIVE_RR_THRESHOLDS.get(strat),
                }
            return out
        except Exception as e:
            logger.error(f"_get_rr_threshold_diagnostics error: {e}")
            return {}

    def _get_rr_execution_gap(self, run_ids: List[str]) -> Optional[dict]:
        """
        Compare scanner RR versus execution RR from filled/open trades.
        """
        if not run_ids:
            return None
        placeholders = ",".join("?" * len(run_ids))
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("PRAGMA table_info(trades)")
            tcols = {r["name"] for r in cur.fetchall()}
            needed_trades = {"ticker", "strategy", "entry_price", "stop_loss", "target_price"}
            if not needed_trades.issubset(tcols):
                conn.close()
                return None

            # Strategy-scoped scanner RR fallback map from decisions in this window.
            cur.execute("PRAGMA table_info(decisions)")
            dcols = {r["name"] for r in cur.fetchall()}
            rr_map: Dict[Tuple[str, str], float] = {}
            if {"ticker", "strategy", "rr"}.issubset(dcols):
                cur.execute(
                    f"""
                    SELECT UPPER(COALESCE(ticker, '')) AS ticker,
                           UPPER(COALESCE(strategy, 'UNKNOWN')) AS strategy,
                           AVG(rr) AS avg_rr
                    FROM decisions
                    WHERE run_id IN ({placeholders})
                      AND rr IS NOT NULL
                    GROUP BY UPPER(COALESCE(ticker, '')), UPPER(COALESCE(strategy, 'UNKNOWN'))
                    """,
                    run_ids,
                )
                for row in cur.fetchall():
                    rr_map[(row["ticker"], _normalize_strategy(row["strategy"]))] = float(row["avg_rr"])
            window_keys = set(rr_map.keys())

            scanner_rr_col = "scanner_rr" if "scanner_rr" in tcols else None
            select_cols = [
                "id",
                "UPPER(COALESCE(ticker, '')) AS ticker",
                "UPPER(COALESCE(strategy, 'UNKNOWN')) AS strategy",
                "UPPER(COALESCE(direction, 'LONG')) AS direction" if "direction" in tcols else "'LONG' AS direction",
                "entry_price",
                "stop_loss",
                "target_price",
            ]
            if scanner_rr_col:
                select_cols.append("scanner_rr")

            cur.execute(
                f"SELECT {', '.join(select_cols)} FROM trades "
                "WHERE entry_price IS NOT NULL AND stop_loss IS NOT NULL AND target_price IS NOT NULL"
            )
            trade_rows = cur.fetchall()
            conn.close()

            by_strategy: Dict[str, Dict[str, float]] = {}
            for row in trade_rows:
                try:
                    entry = float(row["entry_price"])
                    stop = float(row["stop_loss"])
                    target = float(row["target_price"])
                    risk = abs(entry - stop)
                    if risk <= 0:
                        continue
                    direction = (row["direction"] or "LONG").upper()
                    reward = (entry - target) if direction == "SHORT" else (target - entry)
                    if reward <= 0:
                        continue
                    execution_rr = reward / risk
                except Exception:
                    continue

                strategy = _normalize_strategy(row["strategy"])
                if window_keys and (row["ticker"], strategy) not in window_keys:
                    continue
                scanner_rr = None
                if scanner_rr_col:
                    try:
                        if row["scanner_rr"] is not None:
                            scanner_rr = float(row["scanner_rr"])
                    except Exception:
                        scanner_rr = None
                if scanner_rr is None:
                    scanner_rr = rr_map.get((row["ticker"], strategy))
                if scanner_rr is None or scanner_rr <= 0:
                    continue

                bucket = by_strategy.setdefault(strategy, {
                    "sum_scanner_rr": 0.0,
                    "sum_execution_rr": 0.0,
                    "count": 0,
                })
                bucket["sum_scanner_rr"] += float(scanner_rr)
                bucket["sum_execution_rr"] += float(execution_rr)
                bucket["count"] += 1

            if not by_strategy:
                return {
                    "sample_size": 0,
                    "by_strategy": {},
                }

            total_n = 0
            out_by_strategy: Dict[str, dict] = {}
            for strat, b in sorted(by_strategy.items()):
                n = int(b["count"])
                if n <= 0:
                    continue
                avg_scanner = b["sum_scanner_rr"] / n
                avg_exec = b["sum_execution_rr"] / n
                out_by_strategy[strat] = {
                    "avg_scanner_rr": round(avg_scanner, 3),
                    "avg_execution_rr": round(avg_exec, 3),
                    "avg_gap": round(avg_exec - avg_scanner, 3),
                    "count": n,
                }
                total_n += n

            return {
                "sample_size": total_n,
                "by_strategy": out_by_strategy,
            }
        except Exception as e:
            logger.error(f"_get_rr_execution_gap error: {e}")
            return None

    def _get_reason_stability(self, run_ids: List[str]) -> dict:
        """
        Detect if the same reject reason has been dominant for 3+ consecutive runs.
        Returns {stable: bool, dominant_reason: str, consecutive_runs: int}.
        """
        if not run_ids:
            return {"stable": False}
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("PRAGMA table_info(decisions)")
            dcols = {r["name"] for r in cur.fetchall()}

            if "execution_deny_reason" not in dcols:
                conn.close()
                return {"stable": False}

            # For each run, find the most common reject reason
            per_run: List[Tuple[str, str]] = []  # (run_id, dominant_reason)
            for run_id in run_ids:
                cur.execute(
                    """
                    SELECT execution_deny_reason as reason, COUNT(*) as cnt
                    FROM decisions
                    WHERE run_id = ?
                      AND execution_deny_reason IS NOT NULL
                      AND execution_deny_reason != ''
                    GROUP BY execution_deny_reason
                    ORDER BY cnt DESC
                    LIMIT 1
                    """,
                    (run_id,),
                )
                row = cur.fetchone()
                if row:
                    per_run.append((run_id, row["reason"]))

            conn.close()

            if len(per_run) < 3:
                return {"stable": False}

            # Check trailing consecutive streak
            last_reason = per_run[-1][1]
            streak = 1
            for _, reason in reversed(per_run[:-1]):
                if reason == last_reason:
                    streak += 1
                else:
                    break

            if streak >= 3:
                return {
                    "stable": True,
                    "dominant_reason": last_reason,
                    "consecutive_runs": streak,
                }
            return {"stable": False, "dominant_reason": last_reason, "consecutive_runs": streak}
        except Exception as e:
            logger.error(f"_get_reason_stability error: {e}")
            return {"stable": False}

    def _fetch_live_vix(self) -> Optional[float]:
        """Fetch VIX from the shared live/proxy helper used by the runtime."""
        self._last_vix_snapshot = get_vix_snapshot()
        value = (self._last_vix_snapshot or {}).get("vix_level")
        return float(value) if isinstance(value, (int, float)) else None

    def _generate_suggestions(
        self,
        funnel: dict,
        top_reasons: List[dict],
        near_misses: List[dict],
        reason_stability: dict,
        gate_overlap: dict,
        rr_threshold_diagnostics: Optional[dict] = None,
        live_vix: Optional[float] = None,
    ) -> List[str]:
        """Generate actionable suggestions based on pattern analysis."""
        suggestions = []
        rr_threshold_diagnostics = rr_threshold_diagnostics or {}

        # Very low approval rate
        rate = funnel.get("approval_rate_pct", 0)
        total = funnel.get("total_scanned", 0)
        if total >= 20 and rate < 5:
            suggestions.append(
                f"Approval rate is {rate:.1f}% — consider relaxing score thresholds or widening universe criteria."
            )

        # Very high approval rate (might mean filters are too loose)
        if total >= 20 and rate > 40:
            suggestions.append(
                f"Approval rate is {rate:.1f}% — unusually high. Check if thresholds or data quality controls are working."
            )

        # Top reason dominates heavily
        if top_reasons:
            top = top_reasons[0]
            top_count = top["count"]
            total_rejected = funnel.get("total_rejected", 0)
            if total_rejected and top_count / total_rejected > 0.50:
                suggestions.append(
                    f"'{top['reason']}' accounts for {top_count/total_rejected*100:.0f}% of all rejections — single bottleneck. "
                    f"Review that filter condition."
                )

        # Many near misses
        if len(near_misses) >= 5:
            avg_gap = sum(nm["gap"] for nm in near_misses) / len(near_misses)
            suggestions.append(
                f"{len(near_misses)} near-miss tickers (avg gap {avg_gap:.1f}pts from threshold). "
                f"Consider a trial batch with threshold lowered by 5pts to measure outcome quality."
            )

        # Stable pattern warning
        if reason_stability.get("stable"):
            reason = reason_stability["dominant_reason"]
            streak = reason_stability["consecutive_runs"]
            suggestions.append(
                f"'{reason}' has been the dominant reject reason for {streak} consecutive runs — persistent structural blocker."
            )

        # risk_reward_too_low is very common
        rr_count = sum(r["count"] for r in top_reasons if r["reason"] == "risk_reward_too_low")
        if rr_count > 0 and funnel.get("total_rejected", 0):
            pct = rr_count / funnel["total_rejected"] * 100
            if pct > 30:
                suggestions.append(
                    f"R:R failures represent {pct:.0f}% of rejections. Consider adjusting stop/target calculation or universe quality."
                )

        # Phase 5: detect structurally unreachable RR threshold on SHORT.
        short_rr_diag = rr_threshold_diagnostics.get("SHORT", {}) if isinstance(rr_threshold_diagnostics, dict) else {}
        short_max_rr = short_rr_diag.get("max_rr")
        short_threshold = short_rr_diag.get("live_threshold")
        short_rr_rejects = int(short_rr_diag.get("rr_reject_count", 0) or 0)
        if (
            short_max_rr is not None
            and short_threshold is not None
            and short_rr_rejects > 100
            and float(short_max_rr) < float(short_threshold)
        ):
            suggestions.append(
                f"SHORT RR ceiling ({float(short_max_rr):.2f}) is below live threshold "
                f"({float(short_threshold):.1f}) — stop/target calibration needed"
            )

        # universe_prefilter_failed is very common (suggests universe too broad)
        pre_count = sum(r["count"] for r in top_reasons if r["reason"] == "universe_prefilter_failed")
        if pre_count > 0 and funnel.get("total_rejected", 0):
            pct = pre_count / funnel["total_rejected"] * 100
            if pct > 40:
                suggestions.append(
                    f"Pre-filter is rejecting {pct:.0f}% of universe — consider tightening universe source to save scan time."
                )

        # Gate overlap insights (when notes.gates_failed is available)
        coverage = (gate_overlap or {}).get("coverage", {}) if isinstance(gate_overlap, dict) else {}
        top_combos = (gate_overlap or {}).get("top_combos", []) if isinstance(gate_overlap, dict) else []
        if coverage and coverage.get("coverage_pct", 0) >= 40 and top_combos:
            top = top_combos[0]
            combo = top.get("combo", "")
            pct = top.get("pct_of_tagged", 0.0)
            if combo and pct >= 40:
                suggestions.append(
                    f"Gate overlap shows '{combo}' in {pct:.0f}% of tagged rejects — prioritize that gate pair before threshold tweaks."
                )

        # Phase 5: REMORA strategy-specific threshold note.
        remora_bucket = (funnel.get("by_strategy", {}) or {}).get("REMORA", {})
        remora_approved = int(remora_bucket.get("approved", 0) or 0)
        remora_rejected = int(remora_bucket.get("rejected", 0) or 0)
        if remora_rejected > 0 and remora_approved == 0:
            remora_reasons = [
                r for r in (top_reasons or [])
                if _normalize_strategy(r.get("strategy")) == "REMORA"
            ]
            if remora_reasons:
                dominant = max(remora_reasons, key=lambda x: x.get("count", 0))
                if dominant.get("reason") == "score_below_threshold":
                    suggestions.append(
                        "REMORA score threshold (70) is tighter than Sniper/Voyager (60) and Short (55) — review for catalyst-specific calibration"
                    )

        # VIX proximity alert: warn when approaching CONTRARIAN activation threshold.
        if live_vix is not None:
            gap = _CONTRARIAN_VIX_THRESHOLD - live_vix
            if 0 < gap <= _VIX_PROXIMITY_WARN_DELTA:
                suggestions.append(
                    f"VIX at {live_vix:.1f} — {gap:.1f} points from CONTRARIAN activation "
                    f"threshold ({_CONTRARIAN_VIX_THRESHOLD:.0f}). Monitor closely; "
                    f"Contrarian strategy will auto-activate on next scan above threshold."
                )
            elif gap <= 0:
                suggestions.append(
                    f"VIX at {live_vix:.1f} — above CONTRARIAN threshold "
                    f"({_CONTRARIAN_VIX_THRESHOLD:.0f}). Contrarian strategy should be active."
                )

        if not suggestions:
            suggestions.append("No significant quality issues detected in this window.")

        return suggestions

    # ------------------------------------------------------------------
    # Internal: storage
    # ------------------------------------------------------------------

    def _empty_report(
        self,
        days: int,
        mode: Optional[str],
        exclude_unknown_regime: bool = False,
    ) -> dict:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": days,
            "window_start": None,
            "window_end": None,
            "mode_filter": mode or "all",
            "exclude_unknown_regime": bool(exclude_unknown_regime),
            "run_count": 0,
            "funnel": {},
            "top_reject_reasons": [],
            "near_misses": [],
            "gate_overlap": {},
            "rr_threshold_diagnostics": {},
            "rejection_by_regime": [],
            "reason_stability": {"stable": False},
            "suggestions": ["No qualifying runs found in this window."],
            "shadow_outcome_summary": None,
            "short_management_shadow": None,
            "rr_execution_gap": None,
            "options_feed_health": None,
            "live_vix": None,
            "live_vix_source": None,
            "live_vix_reason": None,
            "contrarian_vix_threshold": _CONTRARIAN_VIX_THRESHOLD,
        }

    def _get_pilot_summary(self, days: int = 7) -> Optional[dict]:
        """
        Query pilot_events table for summary counts over the diagnostic window.
        Returns None if pilot_events table does not exist.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Check table exists
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='pilot_events'"
            )
            if not cur.fetchone():
                conn.close()
                return None

            # Prefer run-scoped events only (ignore synthetic/test rows with blank run_id).
            # If runs table exists, require pilot_events.run_id to resolve to a real run.
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'")
            has_runs = bool(cur.fetchone())
            days_window = f"-{int(days)} days"
            if has_runs:
                cur.execute(
                    """
                    SELECT pe.event_type, COUNT(*) as n
                    FROM pilot_events pe
                    JOIN runs r ON r.run_id = pe.run_id
                    WHERE datetime(pe.created_at) >= datetime('now', ?)
                      AND TRIM(COALESCE(pe.run_id, '')) <> ''
                    GROUP BY pe.event_type
                    """,
                    (days_window,),
                )
            else:
                cur.execute(
                    """
                    SELECT event_type, COUNT(*) as n
                    FROM pilot_events
                    WHERE datetime(created_at) >= datetime('now', ?)
                      AND TRIM(COALESCE(run_id, '')) <> ''
                    GROUP BY event_type
                    """,
                    (days_window,),
                )
            counts = {row["event_type"]: row["n"] for row in cur.fetchall()}

            considered = counts.get("CANDIDATE", 0)
            eligible = counts.get("ELIGIBLE", 0)
            blocked = counts.get("BLOCKED", 0)
            executed = counts.get("EXECUTED", 0)
            closed = counts.get("CLOSED", 0)
            win_pct = None

            # trades table is more authoritative for executed/closed pilot outcomes
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
            has_trades = bool(cur.fetchone())
            if has_trades:
                cur.execute("PRAGMA table_info(trades)")
                tcols = {row["name"] for row in cur.fetchall()}
                entry_col = "entry_time" if "entry_time" in tcols else ("entry_date" if "entry_date" in tcols else None)
                if entry_col and "is_pilot" in tcols:
                    analytics_filter = "COALESCE(analytics_excluded, 0) = 0" if "analytics_excluded" in tcols else "1=1"
                    exit_date_expr = []
                    if "status" in tcols:
                        exit_date_expr.append("UPPER(COALESCE(status, '')) = 'CLOSED'")
                    if "exit_time" in tcols:
                        exit_date_expr.append("exit_time IS NOT NULL")
                    if "exit_date" in tcols:
                        exit_date_expr.append("exit_date IS NOT NULL")
                    closed_expr = " OR ".join(exit_date_expr) if exit_date_expr else "0"

                    win_expr = None
                    if "win" in tcols:
                        win_expr = "COALESCE(win, 0) = 1"
                    elif "pnl" in tcols:
                        win_expr = "COALESCE(pnl, 0) > 0"
                    elif "pnl_pct" in tcols:
                        win_expr = "COALESCE(pnl_pct, 0) > 0"
                    elif "pnl_percent" in tcols:
                        win_expr = "COALESCE(pnl_percent, 0) > 0"

                    cur.execute(
                        f"""
                        SELECT
                            COUNT(*) AS executed,
                            SUM(CASE WHEN ({closed_expr}) THEN 1 ELSE 0 END) AS closed,
                            SUM(CASE WHEN ({closed_expr}) AND ({win_expr or '0'}) THEN 1 ELSE 0 END) AS wins
                        FROM trades
                        WHERE COALESCE(is_pilot, 0) = 1
                          AND {analytics_filter}
                          AND datetime({entry_col}) >= datetime('now', ?)
                        """,
                        (days_window,),
                    )
                    trade_rollup = cur.fetchone()
                    if trade_rollup is not None:
                        executed = int(trade_rollup["executed"] or 0)
                        closed = int(trade_rollup["closed"] or 0)
                        wins = int(trade_rollup["wins"] or 0)
                        win_pct = round(wins / closed * 100, 1) if closed else None

            conn.close()

            if not counts and executed == 0:
                return {"window_days": days, "no_data": True}

            return {
                "window_days": days,
                "considered": considered,
                "eligible": eligible,
                "blocked": blocked,
                "executed_paper": executed,
                "closed": closed,
                "open": max(0, executed - closed),
                "win_pct": win_pct,
            }
        except Exception as exc:
            logger.warning(f"_get_pilot_summary error: {exc}")
            return None

    def _save_json(self, report: dict) -> None:
        try:
            os.makedirs(REPORTS_DIR, exist_ok=True)
            with open(REPORT_FILE, "w") as f:
                json.dump(report, f, indent=2, default=str)
            logger.info(f"Scan quality report saved to {REPORT_FILE}")
        except Exception as e:
            logger.warning(f"Could not save scan quality JSON: {e}")

    def _ensure_table(self) -> None:
        """Idempotent CREATE TABLE for scan_quality_reports."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_quality_reports (
                    report_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at   TEXT NOT NULL,
                    window_start TEXT,
                    window_end   TEXT,
                    run_count    INTEGER,
                    payload_json TEXT,
                    notes        TEXT
                )
                """
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"_ensure_table error: {e}")

    def _insert_db_report(
        self,
        window_start: str,
        window_end: str,
        run_count: int,
        payload: dict,
        notes: str = None,
    ) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO scan_quality_reports (created_at, window_start, window_end, run_count, payload_json, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    window_start,
                    window_end,
                    run_count,
                    json.dumps(payload, default=str),
                    notes,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"_insert_db_report error: {e}")
