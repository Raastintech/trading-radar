"""
Forecast Engine — Historical Base-Rate Forecasting

Entry snapshot:
  Segments closed trades by (strategy, pathway, score_bucket) and
  computes: p_win, expected_return, expected_rr, horizon_days,
  conf_low/high, model_version.

Exit attribution:
  forecast_error = actual_return_pct - forecast_expected_return

These are stored on the trades table (Phase 3 columns).

Usage:
    from forecast_engine import ForecastEngine
    fe = ForecastEngine()

    # At entry — returns dict to persist on trade row
    snap = fe.entry_snapshot(strategy="SNIPER", pathway="MOMENTUM",
                              composite_score=72, min_samples=10)

    # At exit — returns forecast_error float
    err = fe.exit_error(forecast_expected_return=snap["expected_return"],
                        actual_return_pct=3.5)
"""

import sqlite3
import os
import math
import json
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.environ.get("TRADING_DB_PATH", "trading_performance.db")

MODEL_VERSION = "v1.0"

# Minimum closed trades in segment before we trust the estimate
MIN_SAMPLE_DEFAULT = 10


def _score_bucket(score: float) -> str:
    """Map composite score to bucket label."""
    if score >= 80:
        return "HIGH"
    elif score >= 65:
        return "MED_HIGH"
    elif score >= 50:
        return "MED"
    else:
        return "LOW"


def _wilson_conf(wins: int, n: int, z: float = 1.645) -> tuple[float, float]:
    """
    Wilson score interval for a proportion.
    z=1.645 → 90% CI.  Returns (low, high) as proportions.
    """
    if n == 0:
        return (0.0, 1.0)
    p_hat = wins / n
    denom = 1 + z * z / n
    centre = (p_hat + z * z / (2 * n)) / denom
    spread = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - spread), min(1.0, centre + spread))


class ForecastEngine:
    """
    Reads from trading_performance.db trades table.
    Uses closed rows (status='CLOSED' or exit_date fallback) with non-null return.
    """

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path

    # -----------------------------------------------------------------------
    # public API
    # -----------------------------------------------------------------------

    def entry_snapshot(
        self,
        strategy: str,
        pathway: str = "",
        composite_score: float = 50.0,
        min_samples: int = MIN_SAMPLE_DEFAULT,
    ) -> dict:
        """
        Build a forecast snapshot to store when entering a trade.

        Falls back to broader segments when the most-specific segment has
        too few samples (< min_samples).  If even the strategy-level
        segment is too small, returns a zero-confidence placeholder.

        Returns dict with keys:
            forecast_p_win, forecast_expected_return, forecast_expected_rr,
            forecast_horizon_days, forecast_conf_low, forecast_conf_high,
            forecast_model_version
        """
        bucket = _score_bucket(composite_score)

        # Try progressively broader segments
        for seg_strategy, seg_pathway, seg_bucket in [
            (strategy, pathway, bucket),    # most specific
            (strategy, pathway, None),
            (strategy, None,    bucket),
            (strategy, None,    None),       # strategy only
            (None,     None,    None),       # all-time base rate
        ]:
            rows = self._query_closed(seg_strategy, seg_pathway, seg_bucket)
            if len(rows) >= min_samples:
                return self._compute_snapshot(rows, segment_info={
                    "strategy": seg_strategy,
                    "pathway":  seg_pathway,
                    "bucket":   seg_bucket,
                    "n":        len(rows),
                })

        # Not enough data at all — return placeholder
        return self._placeholder_snapshot(strategy, pathway, bucket, n=len(rows) if rows else 0)

    def exit_error(
        self,
        forecast_expected_return: Optional[float],
        actual_return_pct: float,
    ) -> Optional[float]:
        """
        Forecast error at exit.
        forecast_error = actual_return_pct − expected_return
        Returns None if no forecast was recorded.
        """
        if forecast_expected_return is None:
            return None
        return round(actual_return_pct - forecast_expected_return, 4)

    # -----------------------------------------------------------------------
    # internal
    # -----------------------------------------------------------------------

    def _query_closed(
        self,
        strategy: Optional[str],
        pathway: Optional[str],
        score_bucket: Optional[str],
    ) -> list[dict]:
        """Return closed trade rows matching the given segment filters."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cur  = conn.cursor()

            # Check required columns exist
            cur.execute("PRAGMA table_info(trades)")
            cols = {row["name"] for row in cur.fetchall()}
            pnl_col = "pnl_pct" if "pnl_pct" in cols else ("pnl_percent" if "pnl_percent" in cols else None)
            if pnl_col is None:
                conn.close()
                return []

            if "status" in cols:
                closed_clause = "UPPER(COALESCE(status, '')) = 'CLOSED'"
            elif "exit_date" in cols:
                closed_clause = "exit_date IS NOT NULL"
            else:
                conn.close()
                return []

            clauses = [closed_clause, f"{pnl_col} IS NOT NULL"]
            params  = []

            if strategy:
                clauses.append("UPPER(strategy) = UPPER(?)")
                params.append(strategy)

            if pathway and "pathway" in cols:
                clauses.append("UPPER(pathway) = UPPER(?)")
                params.append(pathway)

            if score_bucket and "composite_score" in cols:
                # Compute bucket from stored composite_score
                bucket_clause = self._score_bucket_sql(score_bucket)
                if bucket_clause:
                    clauses.append(bucket_clause)

            where = " AND ".join(clauses)
            order_col = "exit_time" if "exit_time" in cols else ("exit_date" if "exit_date" in cols else "id")
            sql   = f"SELECT * FROM trades WHERE {where} ORDER BY {order_col} DESC LIMIT 500"
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception:
            return []

    @staticmethod
    def _score_bucket_sql(bucket: str) -> Optional[str]:
        """Return SQL fragment to filter by score bucket."""
        mapping = {
            "HIGH":     "composite_score >= 80",
            "MED_HIGH": "composite_score >= 65 AND composite_score < 80",
            "MED":      "composite_score >= 50 AND composite_score < 65",
            "LOW":      "composite_score < 50",
        }
        return mapping.get(bucket)

    def _compute_snapshot(self, rows: list[dict], segment_info: dict) -> dict:
        n     = len(rows)
        wins  = sum(
            1 for r in rows
            if ((r.get("pnl_pct") if r.get("pnl_pct") is not None else r.get("pnl_percent")) or 0) > 0
        )
        p_win = wins / n

        returns = [
            (r.get("pnl_pct") if r.get("pnl_pct") is not None else r.get("pnl_percent")) or 0.0
            for r in rows
        ]
        exp_ret = sum(returns) / n

        # Expected RR from rows that have risk_amount / pnl
        rr_vals = []
        for r in rows:
            ra  = r.get("risk_amount")
            pnl = r.get("pnl_usd") or r.get("pnl")
            if ra and pnl and ra > 0:
                rr_vals.append(pnl / ra)
        exp_rr = sum(rr_vals) / len(rr_vals) if rr_vals else None

        # Average holding period (days)
        horizons = []
        for r in rows:
            entry_t = r.get("entry_time")
            exit_t  = r.get("exit_time")
            if entry_t and exit_t:
                try:
                    from datetime import datetime
                    fmt = "%Y-%m-%dT%H:%M:%S" if "T" in str(entry_t) else "%Y-%m-%d %H:%M:%S"
                    e = datetime.fromisoformat(str(entry_t).replace("Z", ""))
                    x = datetime.fromisoformat(str(exit_t).replace("Z", ""))
                    horizons.append((x - e).total_seconds() / 86400)
                except Exception:
                    pass
        horizon = round(sum(horizons) / len(horizons)) if horizons else None

        conf_low, conf_high = _wilson_conf(wins, n)

        return {
            "forecast_p_win":           round(p_win, 4),
            "forecast_expected_return": round(exp_ret, 4),
            "forecast_expected_rr":     round(exp_rr, 3) if exp_rr is not None else None,
            "forecast_horizon_days":    horizon,
            "forecast_conf_low":        round(conf_low, 4),
            "forecast_conf_high":       round(conf_high, 4),
            "forecast_model_version":   MODEL_VERSION,
            "_segment":                 segment_info,  # diagnostic only
        }

    @staticmethod
    def _placeholder_snapshot(strategy, pathway, bucket, n) -> dict:
        return {
            "forecast_p_win":           None,
            "forecast_expected_return": None,
            "forecast_expected_rr":     None,
            "forecast_horizon_days":    None,
            "forecast_conf_low":        None,
            "forecast_conf_high":       None,
            "forecast_model_version":   MODEL_VERSION,
            "_segment":                 {
                "strategy": strategy, "pathway": pathway,
                "bucket":   bucket,   "n":       n,
                "note":     "insufficient_data",
            },
        }


# ---------------------------------------------------------------------------
# standalone test
# ---------------------------------------------------------------------------

def _test():
    print("🧪 ForecastEngine — standalone test\n")

    import tempfile, os

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = tmp.name

    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, status TEXT, strategy TEXT,
            pathway TEXT, composite_score REAL, pnl_pct REAL,
            risk_amount REAL, pnl_usd REAL,
            entry_time TEXT, exit_time TEXT
        )
    """)
    import random
    random.seed(7)
    for i in range(30):
        score = random.uniform(60, 90)
        pnl   = random.uniform(-2.0, 6.0)
        conn.execute(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)",
            (i, "closed", "SNIPER", "MOMENTUM", score, pnl,
             1000.0, pnl * 10,
             "2025-01-01T09:30:00", "2025-01-03T15:00:00")
        )
    conn.commit()
    conn.close()

    fe = ForecastEngine(db_path=db)
    snap = fe.entry_snapshot("SNIPER", "MOMENTUM", 72, min_samples=10)

    print(f"  Segment: {snap.get('_segment')}")
    print(f"  p_win:           {snap['forecast_p_win']}")
    print(f"  expected_return: {snap['forecast_expected_return']}")
    print(f"  conf_low/high:   {snap['forecast_conf_low']} / {snap['forecast_conf_high']}")

    err = fe.exit_error(snap["forecast_expected_return"], 4.2)
    print(f"  forecast_error:  {err}")

    os.unlink(db)
    print("\n✅ ForecastEngine test complete.")


if __name__ == "__main__":
    _test()
