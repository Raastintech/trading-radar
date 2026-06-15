"""
Edge Analytics — Phase 3 Performance Intelligence

Answers:
  - Which strategies actually work?
  - Which pathways actually work?
  - Which score buckets have real expectancy?
  - Where are we aligned with institutions vs exploiting their constraints?
  - Where should capital be pressed, reduced, or stood down?

Edge type classification is deterministic and auditable — derived from
PROJECT_NORTH_STAR.md strategy descriptions, no ML labels.

  ALIGNMENT     — trading with durable institutional direction
                  (Voyager accumulation, confirmed distribution Short)
  INEFFICIENCY  — exploiting institutional timing, speed, or pricing constraints
                  (Sniper breakout timing, Remora catalyst speed, Short early deterioration)
  CONTRARIAN    — taking the other side of panic, forced selling, or overshoot
                  (Contrarian / Reaper)

All methods degrade gracefully when closed trade data is sparse.
Funnel and classification analytics are always available.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

DB_PATH = "trading_performance.db"

# ──────────────────────────────────────────────────────────────────
# EDGE TYPE MAPS  (deterministic, from PROJECT_NORTH_STAR.md)
# ──────────────────────────────────────────────────────────────────

STRATEGY_EDGE_TYPE: Dict[str, Tuple[str, str]] = {
    # strategy_upper → (edge_type, one-line rationale)
    "VOYAGER":     ("ALIGNMENT",    "institutional accumulation + trend participation"),
    "SNIPER":      ("INEFFICIENCY", "breakout timing before institutional repricing"),
    "REMORA":      ("INEFFICIENCY", "catalyst dislocation — small-size speed advantage"),
    "SHORT":       ("INEFFICIENCY", "deterioration before full institutional repricing"),
    "CONTRARIAN":  ("CONTRARIAN",   "panic / forced selling / volatility overshoot"),
    "REAPER":      ("CONTRARIAN",   "panic / forced selling / volatility overshoot"),
    "RPR":         ("CONTRARIAN",   "panic / forced selling / volatility overshoot"),
}

PATHWAY_EDGE_TYPE: Dict[str, Tuple[str, str]] = {
    # pathway_upper → (edge_type, rationale)
    "MOMENTUM_BREAKOUT":        ("INEFFICIENCY", "timing inefficiency — late institutional repricing"),
    "CATALYST_SPIKE":           ("INEFFICIENCY", "catalyst dislocation — retail lag"),
    "VOLUME_BREAKOUT":          ("ALIGNMENT",    "institutional block buying confirmation"),
    "FUNDAMENTAL_GROWTH":       ("ALIGNMENT",    "earnings / revenue driven accumulation"),
    "TREND_CONTINUATION":       ("ALIGNMENT",    "trend participation with institutional flow"),
    "SECTOR_ROTATION":          ("ALIGNMENT",    "sector-level institutional rotation"),
    "DETERIORATION_TECHNICAL":  ("INEFFICIENCY", "early technical breakdown — pre repricing"),
    "DETERIORATION_FUNDAMENTAL":("ALIGNMENT",    "earnings / guidance driven distribution"),
    "MEAN_REVERSION":           ("CONTRARIAN",   "statistical reversion from overshoot"),
    "OVERSOLD_BOUNCE":          ("CONTRARIAN",   "panic / forced selling recovery"),
    "SQUEEZE_SETUP":            ("INEFFICIENCY", "mechanical squeeze — structural constraint"),
    "EARNINGS_SURPRISE":        ("INEFFICIENCY", "earnings gap — pricing inefficiency"),
    "RS_BREAKOUT":              ("INEFFICIENCY", "relative strength divergence — crowd catch-up"),
    "SMART_MONEY_FLOW":         ("ALIGNMENT",    "options / dark pool flow confirmation"),
}

EDGE_TYPE_DESCRIPTION = {
    "ALIGNMENT":    "trading with institutional direction",
    "INEFFICIENCY": "exploiting institutional constraints or timing lag",
    "CONTRARIAN":   "opposite side of panic, forced selling, or overshoot",
}


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def classify_edge_type(strategy: str, pathway: str = None) -> Tuple[str, str]:
    """
    Returns (edge_type, rationale) for a strategy / pathway combination.
    Pathway takes precedence when known.
    Falls back to strategy-level classification.
    Returns ('UNKNOWN', '') when neither is recognized.
    """
    if pathway:
        key = str(pathway).upper().strip()
        if key in PATHWAY_EDGE_TYPE:
            return PATHWAY_EDGE_TYPE[key]

    if strategy:
        key = str(strategy).upper().strip()
        if key in STRATEGY_EDGE_TYPE:
            return STRATEGY_EDGE_TYPE[key]

    return ("UNKNOWN", "no classification available")


class EdgeAnalytics:
    """
    Phase 3 performance intelligence.

    All query methods return empty-safe dicts/lists when trade data is sparse.
    The scanner funnel is always available from the decisions table.
    Expectancy metrics populate as closed trades accumulate in the trades table.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _conn(self):
        return sqlite3.connect(self.db_path)

    # ──────────────────────────────────────────────────────────────
    # REALIZED EXPECTANCY  (from trades table, closed trades only)
    # ──────────────────────────────────────────────────────────────

    def _closed_trades(self, lookback_days: int = 90) -> List[dict]:
        """Return closed trades as dicts within the lookback window."""
        cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()
        try:
            conn = self._conn()
            cur = conn.cursor()
            # Dynamically fetch only columns that exist (handles pre-Phase-3 DBs)
            cur.execute("PRAGMA table_info(trades)")
            all_cols = {row[1] for row in cur.fetchall()}

            base_cols = [
                "ticker", "strategy", "direction", "score", "grade",
                "primary_pathway", "exit_reason", "pnl", "pnl_percent",
                "actual_rr", "hold_days", "entry_price", "exit_price",
                "entry_date", "exit_date",
            ]
            phase3_cols = [
                "revision_score", "insider_cluster_score", "squeeze_risk_score",
                "volume_profile_quality", "forecast_p_win", "forecast_expected_return",
                "forecast_error", "composite_score", "pathway",
            ]
            select_cols = [c for c in base_cols + phase3_cols if c in all_cols]
            col_str = ", ".join(select_cols)

            cur.execute(f"""
                SELECT {col_str}
                FROM trades
                WHERE exit_date IS NOT NULL
                  AND exit_date >= ?
                ORDER BY exit_date DESC
            """, (cutoff,))
            col_names = [d[0] for d in cur.description]
            rows = [dict(zip(col_names, row)) for row in cur.fetchall()]
            conn.close()
            return rows
        except Exception:
            return []

    def _expectancy_block(self, trades: List[dict]) -> dict:
        """Compute expectancy stats from a list of trade dicts."""
        if not trades:
            return {"n": 0}
        pnls = [_safe_float(t["pnl"]) for t in trades]
        rets = [_safe_float(t["pnl_percent"]) for t in trades]
        rrs  = [_safe_float(t["actual_rr"]) for t in trades if _safe_float(t["actual_rr"]) != 0]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        n = len(trades)
        win_rate = len(wins) / n if n else 0.0
        avg_win  = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        # Expectancy = (WR * avg_win) + ((1-WR) * avg_loss)
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
        avg_rr = sum(rrs) / len(rrs) if rrs else 0.0
        return {
            "n":           n,
            "win_rate":    round(win_rate * 100, 1),
            "avg_win":     round(avg_win, 2),
            "avg_loss":    round(avg_loss, 2),
            "expectancy":  round(expectancy, 2),
            "total_pnl":   round(sum(pnls), 2),
            "avg_return":  round(sum(rets) / n, 2) if n else 0.0,
            "avg_rr":      round(avg_rr, 2),
        }

    def get_expectancy_by_strategy(self, lookback_days: int = 90) -> Dict[str, dict]:
        trades = self._closed_trades(lookback_days)
        groups: Dict[str, List[dict]] = {}
        for t in trades:
            key = str(t["strategy"] or "UNKNOWN").upper()
            groups.setdefault(key, []).append(t)
        return {k: self._expectancy_block(v) for k, v in groups.items()}

    def get_expectancy_by_pathway(self, lookback_days: int = 90) -> Dict[str, dict]:
        trades = self._closed_trades(lookback_days)
        groups: Dict[str, List[dict]] = {}
        for t in trades:
            key = str(t["primary_pathway"] or "UNKNOWN").upper()
            groups.setdefault(key, []).append(t)
        return {k: self._expectancy_block(v) for k, v in groups.items()}

    def get_expectancy_by_score_bucket(self, lookback_days: int = 90) -> Dict[str, dict]:
        trades = self._closed_trades(lookback_days)
        bucket_order = ["90+", "80s", "70s", "60s", "<60"]
        groups: Dict[str, List[dict]] = {}
        for t in trades:
            s = _safe_float(t["score"])
            if s >= 90:   bucket = "90+"
            elif s >= 80: bucket = "80s"
            elif s >= 70: bucket = "70s"
            elif s >= 60: bucket = "60s"
            else:         bucket = "<60"
            groups.setdefault(bucket, []).append(t)
        # Return in order
        return {b: self._expectancy_block(groups[b]) for b in bucket_order if b in groups}

    def get_expectancy_by_direction(self, lookback_days: int = 90) -> Dict[str, dict]:
        trades = self._closed_trades(lookback_days)
        groups: Dict[str, List[dict]] = {}
        for t in trades:
            key = str(t["direction"] or "LONG").upper()
            groups.setdefault(key, []).append(t)
        return {k: self._expectancy_block(v) for k, v in groups.items()}

    def get_exit_reason_distribution(self, lookback_days: int = 90) -> Dict[str, int]:
        trades = self._closed_trades(lookback_days)
        dist: Dict[str, int] = {}
        for t in trades:
            r = str(t["exit_reason"] or "UNKNOWN")
            dist[r] = dist.get(r, 0) + 1
        return dict(sorted(dist.items(), key=lambda x: -x[1]))

    def get_hold_distribution(self, lookback_days: int = 90) -> dict:
        trades = self._closed_trades(lookback_days)
        if not trades:
            return {"n": 0}
        days_list = [int(t["hold_days"] or 0) for t in trades]
        return {
            "n":      len(days_list),
            "min":    min(days_list),
            "max":    max(days_list),
            "avg":    round(sum(days_list) / len(days_list), 1),
            "median": sorted(days_list)[len(days_list) // 2],
        }

    # ──────────────────────────────────────────────────────────────
    # EDGE TYPE MIX  (open + recent closed positions)
    # ──────────────────────────────────────────────────────────────

    def get_edge_type_mix(self, lookback_days: int = 90) -> Dict[str, dict]:
        """
        Returns count of open + recently-closed positions by edge type.
        Always available regardless of closed trade count.
        """
        result: Dict[str, dict] = {
            "ALIGNMENT":    {"open": 0, "closed": 0, "strategies": set()},
            "INEFFICIENCY": {"open": 0, "closed": 0, "strategies": set()},
            "CONTRARIAN":   {"open": 0, "closed": 0, "strategies": set()},
            "UNKNOWN":      {"open": 0, "closed": 0, "strategies": set()},
        }
        try:
            cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()
            conn = self._conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT strategy, primary_pathway,
                       CASE WHEN exit_date IS NULL THEN 'open' ELSE 'closed' END AS status
                FROM trades
                WHERE exit_date IS NULL OR exit_date >= ?
            """, (cutoff,))
            for strategy, pathway, status in cur.fetchall():
                edge_type, _ = classify_edge_type(strategy, pathway)
                bucket = result.get(edge_type, result["UNKNOWN"])
                bucket[status] = bucket.get(status, 0) + 1
                if strategy:
                    bucket["strategies"].add(str(strategy).upper())
            conn.close()
        except Exception:
            pass
        # Convert sets to sorted lists for JSON-safety
        for v in result.values():
            v["strategies"] = sorted(v.get("strategies", set()))
        return result

    # ──────────────────────────────────────────────────────────────
    # SCANNER FUNNEL  (from decisions table — always available)
    # ──────────────────────────────────────────────────────────────

    def get_scanner_funnel(self, lookback_days: int = 7) -> Dict[str, dict]:
        """
        Pass / reject counts by strategy from the decisions table.
        Shows selectivity: how many tickers evaluated vs approved.
        """
        cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()
        funnel: Dict[str, dict] = {}
        try:
            conn = self._conn()
            cur = conn.cursor()

            # Rejects
            cur.execute("""
                SELECT strategy, execution_deny_reason, COUNT(*) as n
                FROM decisions
                WHERE council_decision = 'SCANNER_REJECT'
                  AND timestamp >= ?
                  AND strategy IS NOT NULL
                GROUP BY strategy, execution_deny_reason
            """, (cutoff,))
            for strategy, reason, n in cur.fetchall():
                s = str(strategy).upper()
                funnel.setdefault(s, {"approved": 0, "rejected": 0, "reasons": {}})
                funnel[s]["rejected"] += n
                funnel[s]["reasons"][reason or "unknown"] = (
                    funnel[s]["reasons"].get(reason or "unknown", 0) + n
                )

            # Approvals — position_opened=1 or council_decision signals execution
            cur.execute("""
                SELECT strategy, COUNT(*) as n
                FROM decisions
                WHERE position_opened = 1
                  AND timestamp >= ?
                  AND strategy IS NOT NULL
                GROUP BY strategy
            """, (cutoff,))
            for strategy, n in cur.fetchall():
                s = str(strategy).upper()
                funnel.setdefault(s, {"approved": 0, "rejected": 0, "reasons": {}})
                funnel[s]["approved"] += n

            conn.close()
        except Exception:
            pass

        # Compute pass rate
        for s, d in funnel.items():
            total = d["approved"] + d["rejected"]
            d["total_evaluated"] = total
            d["pass_rate"] = round(d["approved"] / total * 100, 1) if total else 0.0

        return funnel

    # ──────────────────────────────────────────────────────────────
    # REGIME EXPECTANCY  (when regime data is available)
    # ──────────────────────────────────────────────────────────────

    def get_regime_expectancy(self, lookback_days: int = 90) -> Dict[str, dict]:
        """
        Expectancy grouped by regime_status stored in trades at entry.
        Returns empty when no regime column exists or trades are sparse.
        """
        try:
            conn = self._conn()
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(trades)")
            cols = {row[1] for row in cur.fetchall()}
            conn.close()
            if "regime_status" not in cols:
                return {}
        except Exception:
            return {}

        trades = self._closed_trades(lookback_days)
        groups: Dict[str, List[dict]] = {}
        for t in trades:
            key = str(t.get("regime_status") or "UNKNOWN").upper()
            groups.setdefault(key, []).append(t)
        return {k: self._expectancy_block(v) for k, v in groups.items()}

    # ──────────────────────────────────────────────────────────────
    # PHASE 3 — ENRICHMENT SIGNAL ATTRIBUTION
    # ──────────────────────────────────────────────────────────────

    def get_expectancy_by_revision_bucket(self, lookback_days: int = 90) -> Dict[str, dict]:
        """
        Expectancy segmented by analyst revision_score bucket at entry.
        Reveals whether bullish analyst consensus actually predicts better outcomes.
        """
        trades = self._closed_trades(lookback_days)
        groups: Dict[str, List[dict]] = {}
        for t in trades:
            rs = _safe_float(t.get("revision_score"), default=-1)
            if rs < 0:
                bucket = "NO_DATA"
            elif rs >= 70:
                bucket = "BULLISH(70+)"
            elif rs >= 55:
                bucket = "NEUTRAL(55-70)"
            else:
                bucket = "BEARISH(<55)"
            groups.setdefault(bucket, []).append(t)
        return {k: self._expectancy_block(v) for k, v in groups.items()}

    def get_expectancy_by_insider_bucket(self, lookback_days: int = 90) -> Dict[str, dict]:
        """
        Expectancy segmented by insider_cluster_score bucket at entry.
        Reveals whether insider buying clusters predict better entry timing.
        """
        trades = self._closed_trades(lookback_days)
        groups: Dict[str, List[dict]] = {}
        for t in trades:
            ic = _safe_float(t.get("insider_cluster_score"), default=-1)
            if ic < 0:
                bucket = "NO_DATA"
            elif ic >= 60:
                bucket = "CLUSTER(60+)"
            elif ic >= 30:
                bucket = "SOME(30-60)"
            else:
                bucket = "NONE(<30)"
            groups.setdefault(bucket, []).append(t)
        return {k: self._expectancy_block(v) for k, v in groups.items()}

    def get_expectancy_by_squeeze_bucket(self, lookback_days: int = 90) -> Dict[str, dict]:
        """
        Expectancy segmented by squeeze_risk_score bucket at entry.
        For LONG trades: high squeeze = mechanical tailwind.
        """
        trades = self._closed_trades(lookback_days)
        groups: Dict[str, List[dict]] = {}
        for t in trades:
            sq = _safe_float(t.get("squeeze_risk_score"), default=-1)
            if sq < 0:
                bucket = "NO_DATA"
            elif sq >= 70:
                bucket = "HIGH_SQUEEZE(70+)"
            elif sq >= 50:
                bucket = "MODERATE(50-70)"
            else:
                bucket = "LOW(<50)"
            groups.setdefault(bucket, []).append(t)
        return {k: self._expectancy_block(v) for k, v in groups.items()}

    def get_expectancy_by_volume_profile(self, lookback_days: int = 90) -> Dict[str, dict]:
        """
        Expectancy segmented by volume_profile_quality at entry.
        STRONG (at POC inside VA) should predict better mean reversion / support.
        """
        trades = self._closed_trades(lookback_days)
        groups: Dict[str, List[dict]] = {}
        for t in trades:
            q = str(t.get("volume_profile_quality") or "NONE").upper()
            groups.setdefault(q, []).append(t)
        return {k: self._expectancy_block(v) for k, v in groups.items()}

    def get_expectancy_by_forecast_decile(self, lookback_days: int = 90) -> Dict[str, dict]:
        """
        Expectancy segmented by forecast_p_win decile at entry.
        Validates whether the forecast engine is well-calibrated.
        """
        trades = self._closed_trades(lookback_days)
        groups: Dict[str, List[dict]] = {}
        for t in trades:
            pw = _safe_float(t.get("forecast_p_win"), default=-1)
            if pw < 0:
                bucket = "NO_FORECAST"
            elif pw >= 0.70:
                bucket = "HIGH_CONF(>=70%)"
            elif pw >= 0.55:
                bucket = "MED_CONF(55-70%)"
            elif pw >= 0.40:
                bucket = "LOW_CONF(40-55%)"
            else:
                bucket = "WEAK(<40%)"
            groups.setdefault(bucket, []).append(t)
        return {k: self._expectancy_block(v) for k, v in groups.items()}

    def get_forecast_calibration(self, lookback_days: int = 90) -> dict:
        """
        Compare forecast_expected_return to actual pnl_percent.
        Returns mean/std of forecast_error to measure calibration.
        """
        trades = self._closed_trades(lookback_days)
        errors = []
        for t in trades:
            fe = t.get("forecast_error")
            if fe is not None:
                try:
                    errors.append(float(fe))
                except Exception:
                    pass

        if not errors:
            return {"n": 0, "note": "No forecast_error data yet"}

        n       = len(errors)
        mean_e  = sum(errors) / n
        var_e   = sum((e - mean_e) ** 2 for e in errors) / n
        import math
        std_e   = math.sqrt(var_e)
        mae     = sum(abs(e) for e in errors) / n

        # Calibration score: 0=worst, 100=perfect
        # MAE < 1% → 100pts; MAE at 10% → 0pts
        cal_score = max(0.0, min(100.0, 100.0 - mae * 10.0))

        return {
            "n":              n,
            "mean_error":     round(mean_e, 4),
            "std_error":      round(std_e, 4),
            "mae":            round(mae, 4),
            "calibration_score": round(cal_score, 1),
            "note": (
                "Well calibrated" if cal_score >= 75
                else "Moderate calibration" if cal_score >= 50
                else "Poor calibration — model needs more data"
            ),
        }

    # ──────────────────────────────────────────────────────────────
    # OPERATOR SUMMARY  (compact dict for dashboard rendering)
    # ──────────────────────────────────────────────────────────────

    def get_operator_summary(self, lookback_days: int = 90) -> dict:
        """
        Single call that returns all Phase 3 intelligence for the operator panel.
        Designed to be called once per dashboard render cycle.
        """
        by_strategy  = self.get_expectancy_by_strategy(lookback_days)
        by_pathway   = self.get_expectancy_by_pathway(lookback_days)
        by_score     = self.get_expectancy_by_score_bucket(lookback_days)
        by_direction = self.get_expectancy_by_direction(lookback_days)
        exit_dist    = self.get_exit_reason_distribution(lookback_days)
        hold_dist    = self.get_hold_distribution(lookback_days)
        edge_mix     = self.get_edge_type_mix(lookback_days)
        funnel       = self.get_scanner_funnel(lookback_days=7)

        closed_n = sum(v["n"] for v in by_strategy.values())

        # Best / worst strategy by expectancy (requires ≥3 trades)
        ranked = sorted(
            [(k, v) for k, v in by_strategy.items() if v.get("n", 0) >= 3],
            key=lambda x: x[1]["expectancy"],
            reverse=True,
        )
        best_strategy = ranked[0][0]  if ranked else None
        worst_strategy = ranked[-1][0] if len(ranked) > 1 else None

        # Best pathway
        ranked_path = sorted(
            [(k, v) for k, v in by_pathway.items() if v.get("n", 0) >= 3],
            key=lambda x: x[1]["expectancy"],
            reverse=True,
        )
        best_pathway = ranked_path[0][0] if ranked_path else None

        # Phase 3 attribution segments
        by_revision     = self.get_expectancy_by_revision_bucket(lookback_days)
        by_insider      = self.get_expectancy_by_insider_bucket(lookback_days)
        by_squeeze      = self.get_expectancy_by_squeeze_bucket(lookback_days)
        by_vol_profile  = self.get_expectancy_by_volume_profile(lookback_days)
        by_forecast     = self.get_expectancy_by_forecast_decile(lookback_days)
        forecast_cal    = self.get_forecast_calibration(lookback_days)

        return {
            "closed_n":        closed_n,
            "lookback_days":   lookback_days,
            "by_strategy":     by_strategy,
            "by_pathway":      by_pathway,
            "by_score_bucket": by_score,
            "by_direction":    by_direction,
            "exit_dist":       exit_dist,
            "hold_dist":       hold_dist,
            "edge_mix":        edge_mix,
            "funnel":          funnel,
            "best_strategy":   best_strategy,
            "worst_strategy":  worst_strategy,
            "best_pathway":    best_pathway,
            # Phase 3
            "by_revision":      by_revision,
            "by_insider":       by_insider,
            "by_squeeze":       by_squeeze,
            "by_vol_profile":   by_vol_profile,
            "by_forecast":      by_forecast,
            "forecast_calibration": forecast_cal,
            "data_ready":      closed_n >= 10,
        }
