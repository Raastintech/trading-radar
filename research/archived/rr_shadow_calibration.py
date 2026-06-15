"""
rr_shadow_calibration.py — Phase 4.1 Shadow R:R Calibration Engine

READ-ONLY ANALYSIS. Zero changes to execution behavior.

Analyzes historical RR rejects and computes hypothetical filter throughput
if different RR thresholds had been applied. No profitability inference.
No live code touched.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DB_PATH = "trading_performance.db"
REPORTS_DIR = "reports"
SHADOW_REPORT_FILE = os.path.join(REPORTS_DIR, "rr_shadow_latest.json")

# ── Strategy configuration ────────────────────────────────────────────────────
# Current live thresholds (never modified here — reference only)
_CURRENT_THRESHOLDS: Dict[str, float] = {
    "SNIPER": 2.5,
    "SHORT":  3.0,
    "VOYAGER": 2.0,   # included only when RR data present
    "REMORA":  2.0,
    "CONTRARIAN": 2.0,
}

# Ladder of alternative thresholds to evaluate per strategy
# Listed ascending; includes current threshold for baseline reference
_THRESHOLD_LADDERS: Dict[str, List[float]] = {
    "SNIPER":     [2.0, 2.2, 2.5, 3.0],
    "SHORT":      [2.5, 3.0, 3.5, 4.0],
    "VOYAGER":    [1.5, 1.8, 2.0, 2.5],
    "REMORA":     [1.5, 1.8, 2.0, 2.5],
    "CONTRARIAN": [1.5, 1.8, 2.0, 2.5],
}

_STRATEGY_ALIASES = {
    "REAPER": "CONTRARIAN",
}

_MIN_SAMPLES_FOR_CONFIDENCE = 30  # below this → flag as low_sample


def _normalize_strategy(raw: Optional[str]) -> str:
    if not raw:
        return "UNKNOWN"
    key = raw.upper().strip()
    return _STRATEGY_ALIASES.get(key, key)


def _percentile(sorted_vals: List[float], pct: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = max(0, min(len(sorted_vals) - 1, int(pct / 100 * len(sorted_vals))))
    return round(sorted_vals[idx], 4)


class RRShadowCalibrationEngine:
    """
    Reads RR reject rows from decisions table and computes hypothetical
    filter throughput under alternative threshold ladders.

    Produces:
      - per-strategy RR distribution (p10/p25/p50/p75/p90)
      - per-threshold hypothetical_pass_count and pass_rate_delta
      - confidence flags (low_sample, missing_rr)
      - data_quality subsection
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._ensure_table()

    # ── Public API ────────────────────────────────────────────────────────────

    def run_calibration(
        self,
        days: int = 7,
        mode: Optional[str] = None,
        min_watchlist: int = 5,
    ) -> dict:
        """
        Run shadow RR calibration for the given window.

        Args:
            days: look-back window in calendar days
            mode: filter by run mode (or None for all)
            min_watchlist: exclude runs with fewer tickers

        Returns:
            dict with per_strategy calibration + data_quality + suggestions
        """
        run_ids = self._get_run_ids(days=days, mode=mode, min_watchlist=min_watchlist)
        if not run_ids:
            result = self._empty_result(days=days)
            self._save_json(result)
            return result

        per_strategy: Dict[str, dict] = {}
        data_quality: Dict[str, dict] = {}

        # Get all RR reject rows for these runs
        all_rr_rows = self._fetch_rr_rejects(run_ids)

        # Group by strategy
        by_strat: Dict[str, List[float]] = {}
        missing_rr_by_strat: Dict[str, int] = {}
        for row in all_rr_rows:
            strat = _normalize_strategy(row["strategy"])
            rr_val = row["rr"]
            if rr_val is None:
                missing_rr_by_strat[strat] = missing_rr_by_strat.get(strat, 0) + 1
            else:
                by_strat.setdefault(strat, []).append(float(rr_val))

        # Compute calibration per strategy
        for strat, rr_vals in by_strat.items():
            if strat not in _THRESHOLD_LADDERS:
                continue  # no ladder defined → skip
            rr_sorted = sorted(rr_vals)
            n = len(rr_sorted)
            n_missing = missing_rr_by_strat.get(strat, 0)
            n_total_rr_rejects = n + n_missing
            current_threshold = _CURRENT_THRESHOLDS.get(strat, 2.5)

            distribution = {
                "p10": _percentile(rr_sorted, 10),
                "p25": _percentile(rr_sorted, 25),
                "p50": _percentile(rr_sorted, 50),
                "p75": _percentile(rr_sorted, 75),
                "p90": _percentile(rr_sorted, 90),
                "min": round(rr_sorted[0], 4) if rr_sorted else None,
                "max": round(rr_sorted[-1], 4) if rr_sorted else None,
                "mean": round(sum(rr_sorted) / n, 4) if n else None,
            }

            # Baseline: count that already pass current threshold
            # (These should be 0 or near 0 since they were labeled rr_too_low;
            #  any above threshold were mislabeled — we flag that separately)
            already_pass = sum(1 for v in rr_sorted if v >= current_threshold)

            thresholds_result = []
            for thr in _THRESHOLD_LADDERS[strat]:
                pass_count = sum(1 for v in rr_sorted if v >= thr)
                # delta vs current threshold (baseline is always the live threshold)
                baseline_pass = sum(1 for v in rr_sorted if v >= current_threshold)
                delta = pass_count - baseline_pass
                pass_rate = round(pass_count / n * 100, 1) if n else 0.0
                thresholds_result.append({
                    "threshold": thr,
                    "is_current": abs(thr - current_threshold) < 0.001,
                    "hypothetical_pass_count": pass_count,
                    "hypothetical_pass_rate_pct": pass_rate,
                    "delta_vs_current": delta,
                })

            flags = []
            if n < _MIN_SAMPLES_FOR_CONFIDENCE:
                flags.append("low_sample")
            if n_missing > 0:
                flags.append("some_rr_missing")
            if already_pass > 0:
                flags.append(f"mislabeled_rr_rejects:{already_pass}")

            per_strategy[strat] = {
                "n_rr_rejects_with_rr": n,
                "n_rr_rejects_missing_rr": n_missing,
                "total_rr_rejects": n_total_rr_rejects,
                "current_threshold": current_threshold,
                "rr_distribution": distribution,
                "threshold_ladder": thresholds_result,
                "confidence_flags": flags,
            }

            data_quality[strat] = {
                "total_rr_rejects": n_total_rr_rejects,
                "rr_available": n,
                "rr_missing": n_missing,
                "rr_availability_pct": round(n / n_total_rr_rejects * 100, 1) if n_total_rr_rejects else 0.0,
            }

        # Include missing-rr-only strategies in data_quality
        for strat, missing in missing_rr_by_strat.items():
            if strat not in data_quality:
                data_quality[strat] = {
                    "total_rr_rejects": missing,
                    "rr_available": 0,
                    "rr_missing": missing,
                    "rr_availability_pct": 0.0,
                }

        suggestions = self._generate_suggestions(per_strategy, data_quality)
        compact_summary = self._build_compact_summary(per_strategy)
        paper_shadow_policy = self._build_paper_shadow_policy(
            per_strategy=per_strategy,
            window_days=days,
        )

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": days,
            "mode_filter": mode or "all",
            "run_count": len(run_ids),
            "per_strategy": per_strategy,
            "data_quality": data_quality,
            "compact_summary": compact_summary,
            "paper_shadow_policy": paper_shadow_policy,
            "suggestions": suggestions,
        }

        self._save_json(result)
        self._insert_db_report(result)
        return result

    def get_latest_snapshot(self) -> Optional[dict]:
        """Return most recent shadow report from DB for dashboard display."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT payload_json FROM rr_shadow_reports ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return json.loads(row["payload_json"])
        except Exception as e:
            logger.debug(f"get_latest_snapshot: {e}")
        return None

    def print_report(self, report: dict) -> None:
        """Print operator-friendly terminal summary."""
        sep = "─" * 64
        print(f"\n{'═' * 64}")
        print(f"  RR SHADOW CALIBRATION REPORT")
        print(f"  Window: {report.get('window_days')}d  |  Runs: {report.get('run_count')}  |  Mode: {report.get('mode_filter')}")
        print(f"  Generated: {report.get('generated_at', '')[:19]}")
        print(f"{'═' * 64}")

        dq = report.get("data_quality", {})
        if dq:
            print(f"\n  RR DATA QUALITY")
            print(f"  {sep}")
            for strat, q in sorted(dq.items()):
                avail = q.get("rr_availability_pct", 0.0)
                n_avail = q.get("rr_available", 0)
                n_miss = q.get("rr_missing", 0)
                bar = "●" * int(avail / 10) + "○" * (10 - int(avail / 10))
                print(f"  {strat:<12}  [{bar}] {avail:.0f}%  ({n_avail} available, {n_miss} missing)")

        per_strat = report.get("per_strategy", {})
        for strat, data in sorted(per_strat.items()):
            dist = data.get("rr_distribution", {})
            flags = data.get("confidence_flags", [])
            cur_thr = data.get("current_threshold", "?")
            n_total = data.get("total_rr_rejects", 0)

            print(f"\n  STRATEGY: {strat}  (current threshold: ≥{cur_thr}  |  n_rejects={n_total})")
            print(f"  {sep}")
            if dist.get("p50") is not None:
                print(f"  RR Distribution:  p10={dist['p10']:.2f}  p25={dist['p25']:.2f}  "
                      f"p50={dist['p50']:.2f}  p75={dist['p75']:.2f}  p90={dist['p90']:.2f}")
            if flags:
                print(f"  ⚠ Flags: {', '.join(flags)}")

            print(f"\n  {'Threshold':<12}  {'Would Pass':<12}  {'Pass Rate':<12}  {'Delta vs Live'}")
            print(f"  {'-'*55}")
            for t in data.get("threshold_ladder", []):
                thr = t["threshold"]
                is_cur = " ← LIVE" if t["is_current"] else ""
                delta = t["delta_vs_current"]
                delta_str = f"{delta:+d}" if delta != 0 else "baseline"
                print(f"  ≥{thr:<11.1f}  {t['hypothetical_pass_count']:<12}  "
                      f"{t['hypothetical_pass_rate_pct']:.1f}%{' ':7}  {delta_str}{is_cur}")

        suggestions = report.get("suggestions", [])
        if suggestions:
            print(f"\n  CALIBRATION INSIGHTS")
            print(f"  {sep}")
            for s in suggestions:
                print(f"  • {s}")

        policy = report.get("paper_shadow_policy", {})
        candidates = policy.get("candidates", []) if isinstance(policy, dict) else []
        if candidates:
            print(f"\n  PAPER SHADOW POLICY (NO LIVE CHANGE)")
            print(f"  {sep}")
            print(f"  Horizon: {policy.get('horizon_days', '?')} days")
            for c in candidates[:5]:
                print(
                    f"  {c.get('strategy'):<10}  test ≥{c.get('threshold')}  "
                    f"recover {c.get('recovery_count')} ({c.get('recovery_pct'):.1f}%)"
                )

        print(f"\n{'═' * 64}\n")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_run_ids(
        self,
        days: int,
        mode: Optional[str],
        min_watchlist: int,
    ) -> List[str]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("PRAGMA table_info(runs)")
            run_cols = {r["name"] for r in cur.fetchall()}

            where_parts = ["timestamp >= ?"]
            params: list = [cutoff]

            if min_watchlist and "watchlist_size" in run_cols:
                where_parts.append("(watchlist_size IS NULL OR watchlist_size >= ?)")
                params.append(min_watchlist)

            if mode and mode != "all" and "mode" in run_cols:
                where_parts.append("UPPER(mode) = UPPER(?)")
                params.append(mode)

            where_clause = " AND ".join(where_parts)
            cur.execute(
                f"SELECT run_id FROM runs WHERE {where_clause}",
                params,
            )
            ids = [r["run_id"] for r in cur.fetchall()]
            conn.close()
            return ids
        except Exception as e:
            logger.error(f"_get_run_ids error: {e}")
            return []

    def _fetch_rr_rejects(self, run_ids: List[str]) -> List[dict]:
        """Fetch all rows rejected for RR with available fields."""
        if not run_ids:
            return []
        placeholders = ",".join("?" * len(run_ids))
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("PRAGMA table_info(decisions)")
            dcols = {r["name"] for r in cur.fetchall()}

            # Schema-adaptive select
            sel_rr = "rr" if "rr" in dcols else "NULL as rr"
            sel_strategy = "strategy" if "strategy" in dcols else "NULL as strategy"
            sel_notes = "notes" if "notes" in dcols else "NULL as notes"

            cur.execute(
                f"""
                SELECT {sel_rr}, {sel_strategy}, {sel_notes}
                FROM decisions
                WHERE run_id IN ({placeholders})
                  AND execution_deny_reason = 'risk_reward_too_low'
                """,
                run_ids,
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()

            # Try to recover RR from notes JSON if rr column is null
            for row in rows:
                if row.get("rr") is None and row.get("notes"):
                    try:
                        notes_data = json.loads(row["notes"])
                        rr_from_notes = notes_data.get("rr")
                        if rr_from_notes is not None:
                            row["rr"] = float(rr_from_notes)
                    except Exception:
                        pass

            return rows
        except Exception as e:
            logger.error(f"_fetch_rr_rejects error: {e}")
            return []

    def _generate_suggestions(
        self,
        per_strategy: Dict[str, dict],
        data_quality: Dict[str, dict],
    ) -> List[str]:
        suggestions = []
        for strat, data in sorted(per_strategy.items()):
            cur_thr = data["current_threshold"]
            n = data["n_rr_rejects_with_rr"]
            if n == 0:
                continue
            dist = data["rr_distribution"]
            ladder = data["threshold_ladder"]

            # Find baseline (current threshold) pass count
            baseline_entry = next((t for t in ladder if t["is_current"]), None)
            baseline_pass = baseline_entry["hypothetical_pass_count"] if baseline_entry else 0

            # Find best non-current candidate below current that recovers meaningful tickers
            for t in ladder:
                if t["is_current"]:
                    continue
                delta = t["delta_vs_current"]
                thr = t["threshold"]
                if thr < cur_thr and delta > 0:
                    # Candidate recovery
                    daily_rate = round(delta / max(1, data.get("total_rr_rejects", n)) * n / 7, 1)
                    suggestions.append(
                        f"{strat}: lowering RR threshold to ≥{thr} would recover "
                        f"{delta} candidates ({t['hypothetical_pass_rate_pct']:.0f}% pass rate) "
                        f"vs {baseline_pass} at live threshold ≥{cur_thr}. "
                        f"Median rejected RR={dist.get('p50', 0):.2f}."
                    )
                    break  # only suggest the first (smallest) meaningful step

            flags = data.get("confidence_flags", [])
            if "low_sample" in flags:
                suggestions.append(
                    f"{strat}: only {n} RR-reject samples — confidence low, collect more data before acting."
                )

        # Data quality issues
        for strat, dq in sorted(data_quality.items()):
            if dq.get("rr_availability_pct", 100) < 80 and dq.get("total_rr_rejects", 0) >= 10:
                suggestions.append(
                    f"{strat}: RR field missing for {dq['rr_missing']} of {dq['total_rr_rejects']} "
                    f"RR-reject rows ({100 - dq['rr_availability_pct']:.0f}% missing). "
                    f"Ensure rr is logged in scanner reject rows for reliable calibration."
                )

        if not suggestions:
            suggestions.append("No significant RR calibration opportunities detected in this window.")

        return suggestions

    def _build_compact_summary(self, per_strategy: Dict[str, dict]) -> List[dict]:
        """Build compact summary for dashboard display: [{strategy, best_thr, recovery}]."""
        result = []
        for strat, data in sorted(per_strategy.items()):
            cur_thr = data["current_threshold"]
            ladder = data.get("threshold_ladder", [])
            best = None
            for t in ladder:
                if t["is_current"]:
                    continue
                if t["threshold"] < cur_thr and t["delta_vs_current"] > 0:
                    best = t
                    break
            if best:
                result.append({
                    "strategy": strat,
                    "best_alt_threshold": best["threshold"],
                    "recovery_count": best["delta_vs_current"],
                    "recovery_pass_rate_pct": best["hypothetical_pass_rate_pct"],
                })
        return result

    def _build_paper_shadow_policy(
        self,
        per_strategy: Dict[str, dict],
        window_days: int,
    ) -> dict:
        """
        Build a paper-only shadow policy candidate list from RR ladders.

        This is advisory output only; no execution path consumes it.
        """
        candidates = []
        for strat, data in sorted(per_strategy.items()):
            ladder = data.get("threshold_ladder", [])
            cur_thr = data.get("current_threshold")
            total = max(1, int(data.get("total_rr_rejects", 0)))
            if cur_thr is None or not ladder:
                continue

            # Choose the first lower threshold that yields meaningful recovery.
            best = None
            for t in ladder:
                thr = t.get("threshold")
                if thr is None or t.get("is_current"):
                    continue
                if thr >= cur_thr:
                    continue
                delta = int(t.get("delta_vs_current", 0) or 0)
                if delta <= 0:
                    continue
                recovery_pct = (delta / total) * 100
                # Meaningful = at least 5% of RR rejects or >=20 rows.
                if recovery_pct >= 5.0 or delta >= 20:
                    best = t
                    break
            if not best:
                continue

            delta = int(best.get("delta_vs_current", 0) or 0)
            candidates.append({
                "strategy": strat,
                "current_threshold": cur_thr,
                "threshold": best.get("threshold"),
                "recovery_count": delta,
                "recovery_pct": round((delta / total) * 100, 1),
                "window_rr_rejects": total,
            })

        return {
            "mode": "PAPER_ONLY",
            "horizon_days": int(window_days or 0),
            "candidates": candidates,
            "note": "Advisory only. Does not modify live thresholds or execution behavior.",
        }

    def _empty_result(self, days: int) -> dict:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "window_days": days,
            "mode_filter": "all",
            "run_count": 0,
            "per_strategy": {},
            "data_quality": {},
            "compact_summary": [],
            "paper_shadow_policy": {"mode": "PAPER_ONLY", "horizon_days": int(days or 0), "candidates": []},
            "suggestions": ["No qualifying runs found in this window."],
        }

    def _save_json(self, report: dict) -> None:
        try:
            os.makedirs(REPORTS_DIR, exist_ok=True)
            with open(SHADOW_REPORT_FILE, "w") as f:
                json.dump(report, f, indent=2, default=str)
            logger.info(f"RR shadow report saved to {SHADOW_REPORT_FILE}")
        except Exception as e:
            logger.warning(f"Could not save shadow JSON: {e}")

    def _ensure_table(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS rr_shadow_reports (
                    report_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at   TEXT NOT NULL,
                    window_days  INTEGER,
                    run_count    INTEGER,
                    payload_json TEXT,
                    notes        TEXT
                )
                """
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"_ensure_table rr_shadow error: {e}")

    def _insert_db_report(self, report: dict, notes: str = None) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO rr_shadow_reports (created_at, window_days, run_count, payload_json, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    report.get("window_days"),
                    report.get("run_count"),
                    json.dumps(report, default=str),
                    notes,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"_insert_db_report rr_shadow error: {e}")
