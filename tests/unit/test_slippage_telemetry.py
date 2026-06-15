"""
tests/unit/test_slippage_telemetry.py — pure-stats + temp-SQLite tests for
research/slippage_telemetry_report.py.

No live providers, no .env required — conftest stubs creds.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import slippage_telemetry_report as st  # noqa: E402


def _make_db(rows):
    """Create a temp SQLite DB with a decisions table populated with rows."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        """
        CREATE TABLE decisions (
            id TEXT, run_id TEXT, ts TEXT, ticker TEXT, strategy TEXT,
            direction TEXT, shares REAL, entry_price REAL, stop_loss REAL,
            target_price REAL, fill_price REAL, fill_qty REAL,
            slippage_bps REAL, fill_status TEXT, position_opened INTEGER,
            position_closed INTEGER
        )
        """
    )
    for i, r in enumerate(rows):
        conn.execute(
            """INSERT INTO decisions
               (id, run_id, ts, ticker, strategy, direction, shares,
                entry_price, stop_loss, target_price, fill_price, fill_qty,
                slippage_bps, fill_status, position_opened, position_closed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"id{i}", "run1", r.get("ts", "2026-05-01T15:30:00+00:00"),
                r["ticker"], r["strategy"], r["direction"],
                r.get("shares", 10), r.get("entry_price", 100.0),
                r.get("stop_loss", 95.0), r.get("target_price", 110.0),
                r.get("fill_price"), r.get("fill_qty"),
                r.get("slippage_bps"), r.get("fill_status"),
                r.get("position_opened", 0), r.get("position_closed", 0),
            ),
        )
    conn.commit()
    conn.close()
    return Path(tmp.name)


class TestAdverseBps:
    def test_long_positive_slip_is_adverse(self):
        row = {"direction": "LONG", "slippage_bps": 10.0}
        assert st._adverse_bps(row) == 10.0

    def test_short_positive_slip_is_favorable(self):
        # SHORT: fill higher than intent means we sold higher → favorable.
        # adverse_bps flips the sign, so positive slippage_bps → negative adverse.
        row = {"direction": "SHORT", "slippage_bps": 10.0}
        assert st._adverse_bps(row) == -10.0

    def test_short_negative_slip_is_adverse(self):
        row = {"direction": "SHORT", "slippage_bps": -8.0}
        assert st._adverse_bps(row) == 8.0

    def test_null_returns_none(self):
        assert st._adverse_bps({"direction": "LONG", "slippage_bps": None}) is None


class TestPercentile:
    def test_empty(self):
        assert st._percentile([], 90) is None

    def test_single(self):
        assert st._percentile([5.0], 90) == 5.0

    def test_median_of_three(self):
        assert st._percentile([1.0, 2.0, 3.0], 50) == 2.0

    def test_p90_interpolated(self):
        # values 1..10, p90 = 9.1 (linear interp between idx 8 and 9)
        v = [float(i) for i in range(1, 11)]
        assert abs(st._percentile(v, 90) - 9.1) < 1e-9


class TestSessionBucket:
    def test_premarket(self):
        assert st._session_bucket(7) == "premarket"

    def test_open_first_hour(self):
        assert st._session_bucket(9) == "open_first_hour"

    def test_mid_session(self):
        assert st._session_bucket(12) == "mid"

    def test_close_last_hour(self):
        assert st._session_bucket(15) == "close_last_hour"

    def test_postmarket(self):
        assert st._session_bucket(18) == "postmarket"

    def test_unknown(self):
        assert st._session_bucket(None) == "unknown"


class TestStatsBlock:
    def test_empty_returns_zero_block(self):
        b = st._stats_block([])
        assert b["n"] == 0
        assert b["median_bps"] is None
        assert b["p90_bps"] is None

    def test_basic_stats(self):
        b = st._stats_block([1.0, 2.0, 3.0, 4.0, 5.0])
        assert b["n"] == 5
        assert b["median_bps"] == 3.0
        assert b["mean_bps"] == 3.0
        assert b["worst_bps"] == 5.0
        # abs-stats equal value-stats here since all positive
        assert b["abs_median_bps"] == 3.0


class TestBuildReportFromDB:
    def _rows_mix(self):
        return [
            # Two LONG fills with +5 bps and +20 bps slippage
            {"ticker": "AAPL", "strategy": "SNIPER", "direction": "LONG",
             "slippage_bps": 5.0, "fill_price": 100.05, "fill_qty": 10,
             "shares": 10, "fill_status": "filled", "position_opened": 1},
            {"ticker": "AAPL", "strategy": "SNIPER", "direction": "LONG",
             "slippage_bps": 20.0, "fill_price": 100.2, "fill_qty": 10,
             "shares": 10, "fill_status": "filled", "position_opened": 1},
            # One SHORT fill with -10 bps slippage (price dropped after submit)
            # adverse = -(-10) = +10 (cost to us)
            {"ticker": "TSLA", "strategy": "SHORT", "direction": "SHORT",
             "slippage_bps": -10.0, "fill_price": 99.9, "fill_qty": 5,
             "shares": 5, "fill_status": "filled", "position_opened": 1},
            # One unfilled — no slippage data, position not opened
            {"ticker": "NVDA", "strategy": "SNIPER", "direction": "LONG",
             "slippage_bps": None, "fill_price": None, "fill_qty": 0,
             "shares": 10, "fill_status": "canceled", "position_opened": 0},
            # One partial fill — has slippage, fill_qty < shares
            {"ticker": "MSFT", "strategy": "SNIPER", "direction": "LONG",
             "slippage_bps": 3.0, "fill_price": 100.03, "fill_qty": 5,
             "shares": 10, "fill_status": "partially_filled",
             "position_opened": 1},
        ]

    def test_overall_counts(self):
        db = _make_db(self._rows_mix())
        rows = st.load_decisions(db, None)
        rep = st.build_report(rows)
        s = rep["summary"]
        assert s["submitted_rows"] == 5
        assert s["filled_rows"] == 4  # 4 have slippage_bps populated
        assert s["nofill_rows"] == 1
        assert s["partial_fills"] == 1
        assert s["status_counts"]["filled"] == 3
        assert s["status_counts"]["partially_filled"] == 1
        assert s["status_counts"]["canceled"] == 1

    def test_overall_adverse_stats(self):
        db = _make_db(self._rows_mix())
        rows = st.load_decisions(db, None)
        rep = st.build_report(rows)
        ov = rep["overall_adverse_bps"]
        # adverse bps: [5, 20, 10, 3] -> median between 5 and 10 = 7.5
        assert ov["n"] == 4
        assert ov["median_bps"] == 7.5
        assert ov["worst_bps"] == 20.0

    def test_warnings_triggered_when_above_assumption(self):
        # Force adverse bps above both thresholds
        rows = []
        for i in range(20):
            rows.append({
                "ticker": "BIG", "strategy": "SNIPER", "direction": "LONG",
                "slippage_bps": 25.0, "fill_price": 100.25, "fill_qty": 10,
                "shares": 10, "fill_status": "filled", "position_opened": 1,
            })
        db = _make_db(rows)
        rep = st.build_report(st.load_decisions(db, None))
        assert any("median |adverse|" in w for w in rep["warnings"])
        assert any("p90 |adverse|" in w for w in rep["warnings"])

    def test_no_warnings_inside_assumption(self):
        rows = []
        for i in range(20):
            rows.append({
                "ticker": "TIGHT", "strategy": "SNIPER", "direction": "LONG",
                "slippage_bps": 2.0, "fill_price": 100.02, "fill_qty": 10,
                "shares": 10, "fill_status": "filled", "position_opened": 1,
            })
        db = _make_db(rows)
        rep = st.build_report(st.load_decisions(db, None))
        # No "exceeds backtest" warnings should appear
        assert not any("exceeds backtest" in w for w in rep["warnings"])

    def test_empty_db_returns_zero_block(self):
        db = _make_db([])
        rep = st.build_report(st.load_decisions(db, None))
        assert rep["summary"]["submitted_rows"] == 0
        assert rep["overall_adverse_bps"]["n"] == 0
        assert rep["warnings"] == []

    def test_missing_db_file_safe(self):
        rows = st.load_decisions(Path("/nonexistent/path/to/x.db"), None)
        assert rows == []


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
