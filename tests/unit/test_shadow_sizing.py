"""
tests/unit/test_shadow_sizing.py — temp-SQLite + temp-parquet tests for
research/shadow_sizing_report.py.

No live providers, no .env required.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import shadow_sizing_report as ss  # noqa: E402


def _make_bars(close_seq, high_offset=1.0, low_offset=-1.0):
    """Build a daily-bar DataFrame from a close sequence.  ATR will be roughly
    (high - low) = 2.0 by default."""
    n = len(close_seq)
    dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "open":   close_seq,
        "high":   [c + high_offset for c in close_seq],
        "low":    [c + low_offset for c in close_seq],
        "close":  close_seq,
        "volume": [1_000_000] * n,
    }, index=pd.DatetimeIndex(dates, name="date"))


def _make_db(decisions=None, paper_signals=None, outcomes=None):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        """
        CREATE TABLE decisions (
            id TEXT, ts TEXT, ticker TEXT, strategy TEXT, direction TEXT,
            shares REAL, entry_price REAL, fill_price REAL, fill_qty REAL,
            stop_loss REAL, position_opened INTEGER, position_closed INTEGER
        )
        """
    )
    for i, r in enumerate(decisions or []):
        conn.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"d{i}", r.get("ts", "2026-05-01T10:00:00+00:00"),
                r["ticker"], r.get("strategy", "SNIPER"), r["direction"],
                r.get("shares", 10), r.get("entry_price", 100.0),
                r.get("fill_price", r.get("entry_price", 100.0)),
                r.get("fill_qty", r.get("shares", 10)),
                r.get("stop_loss", 95.0),
                r.get("position_opened", 1), r.get("position_closed", 0),
            ),
        )
    if paper_signals or outcomes:
        conn.execute(
            """CREATE TABLE paper_signals (
                id TEXT PRIMARY KEY, logged_at TEXT, ticker TEXT,
                side TEXT, strategy TEXT, sleeve TEXT,
                entry_price REAL, stop_loss REAL, sector TEXT, status TEXT
               )"""
        )
        for s in paper_signals or []:
            conn.execute(
                "INSERT INTO paper_signals VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    s["id"], s.get("logged_at", "2026-04-01T00:00:00+00:00"),
                    s["ticker"], s["side"], s.get("strategy", "SHORT"),
                    s.get("sleeve", "SHORT_A"),
                    s.get("entry_price", 100.0), s.get("stop_loss", 110.0),
                    s.get("sector", "Tech"), s.get("status", "open"),
                ),
            )
        conn.execute(
            """CREATE TABLE paper_signal_outcomes (
                signal_id TEXT, horizon_days INTEGER, return_pct REAL,
                adjusted_return_pct REAL, stop_hit INTEGER,
                hold_complete INTEGER, still_open INTEGER,
                outcome_date TEXT, measured_at TEXT
               )"""
        )
        for o in outcomes or []:
            conn.execute(
                "INSERT INTO paper_signal_outcomes VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    o["signal_id"], o["horizon_days"], o["return_pct"],
                    o.get("adjusted_return_pct"), o.get("stop_hit", 0),
                    o.get("hold_complete", 1), o.get("still_open", 0),
                    o.get("outcome_date", "2026-04-30"),
                    o.get("measured_at", "2026-04-30T00:00:00+00:00"),
                ),
            )
    conn.commit()
    conn.close()
    return Path(tmp.name)


class TestAtr:
    def test_flat_bars_atr_equals_range(self):
        bars = _make_bars([100.0] * 20, high_offset=2.0, low_offset=-2.0)
        atr = ss._atr_from_bars(bars, period=14)
        # high - low = 4.0; close shift means TR = max(4, |h-pc|=2, |l-pc|=2) = 4
        assert atr is not None
        assert abs(atr - 4.0) < 0.01

    def test_too_few_bars_returns_none(self):
        bars = _make_bars([100.0])
        assert ss._atr_from_bars(bars) is None

    def test_missing_columns_returns_none(self):
        bars = pd.DataFrame({"close": [100.0, 101.0]})
        assert ss._atr_from_bars(bars) is None


class TestShadowSizeRow:
    def test_full_inputs_produces_sizing(self):
        bars = _make_bars([100.0] * 20, high_offset=1.0, low_offset=-1.0)
        out = ss.shadow_size_row(
            equity=100_000.0, entry=100.0, stop=95.0, direction="LONG",
            bars=bars, actual_shares=100,
        )
        assert out["atr_per_share"] is not None
        assert out["atr_per_share"] > 0
        # shadow = equity * vol_target / atr
        target_vol = 100_000.0 * ss.SHADOW_VOL_TARGET
        expected_shadow = target_vol / out["atr_per_share"]
        assert abs(out["shadow_shares"] - round(expected_shadow, 2)) < 0.01
        assert out["actual_notional"] == 10_000.0
        assert out["actual_risk_at_stop"] == 500.0

    def test_no_bars_returns_warning(self):
        out = ss.shadow_size_row(
            equity=100_000.0, entry=100.0, stop=95.0, direction="LONG",
            bars=None, actual_shares=10,
        )
        assert out["shadow_shares"] is None
        assert out["warning"] == "no_atr_cache"

    def test_no_equity_returns_warning(self):
        bars = _make_bars([100.0] * 20)
        out = ss.shadow_size_row(
            equity=None, entry=100.0, stop=95.0, direction="LONG",
            bars=bars, actual_shares=10,
        )
        assert out["shadow_shares"] is None
        assert out["warning"] == "no_equity_hint"
        # ATR still measurable
        assert out["atr_per_share"] is not None

    def test_oversize_triggers_warning(self):
        # ATR = 4 with vol-target 0.5% on 100k equity -> shadow ≈ 500/4 = 125 shares
        bars = _make_bars([100.0] * 20, high_offset=2.0, low_offset=-2.0)
        out = ss.shadow_size_row(
            equity=100_000.0, entry=100.0, stop=95.0, direction="LONG",
            bars=bars, actual_shares=500,  # 4× the shadow size
        )
        assert out["size_ratio"] > 2.0
        assert out["warning"] == "actual_>=2x_vol_target_shadow"

    def test_undersize_triggers_warning(self):
        bars = _make_bars([100.0] * 20, high_offset=2.0, low_offset=-2.0)
        out = ss.shadow_size_row(
            equity=100_000.0, entry=100.0, stop=95.0, direction="LONG",
            bars=bars, actual_shares=10,  # well below shadow ~125
        )
        assert out["size_ratio"] < 0.4
        assert out["warning"] == "actual_<=0.4x_vol_target_shadow"


class TestBorrowDrag:
    def test_long_not_applicable(self):
        row = {"direction": "LONG"}
        out = ss.borrow_drag_open(row, datetime.now(timezone.utc))
        assert out["applies"] is False

    def test_short_drag_grows_with_days_held(self):
        entry_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        row = {
            "direction": "SHORT", "ts": entry_ts,
            "shares": 100, "entry_price": 50.0,
            "fill_qty": 100, "fill_price": 50.0,
        }
        out = ss.borrow_drag_open(row, datetime.now(timezone.utc))
        assert out["applies"] is True
        assert out["days_held"] > 29
        assert out["notional"] == 5000.0
        # 30 days * (100/1e4/252) ≈ 0.119% drag → ~$5.95 on $5000 notional
        assert 0.05 < out["drag_pct"] < 0.5
        assert out["drag_dollar"] > 0

    def test_short_drag_zero_when_no_ts(self):
        row = {"direction": "SHORT", "ts": None}
        out = ss.borrow_drag_open(row, datetime.now(timezone.utc))
        assert out["applies"] is True
        assert out.get("warning") == "no_entry_ts"


class TestBorrowAdjustClosed:
    def test_subtracts_drag_from_return(self):
        row = {"horizon_days": 30, "return_pct": 2.5}
        adj = ss.borrow_adjust_closed(row)
        assert adj["applies"] is True
        # drag = 30 * (100/1e4/252) * 100 ≈ 0.119%
        assert adj["borrow_drag_pct"] < 0.2
        assert adj["adjusted_return_pct"] < 2.5
        # Sanity: adj = ret - drag
        assert abs(adj["adjusted_return_pct"]
                   - (2.5 - adj["borrow_drag_pct"])) < 1e-6

    def test_missing_outcome_returns_inapplicable(self):
        adj = ss.borrow_adjust_closed({"horizon_days": None, "return_pct": None})
        assert adj["applies"] is False


class TestBuildReportFromDB:
    def test_open_positions_loaded_and_sized(self, tmp_path):
        # Cache bar parquet for AAPL
        (tmp_path / "prices").mkdir()
        bars = _make_bars([100.0] * 20, high_offset=2.0, low_offset=-2.0)
        bars.to_parquet(tmp_path / "prices" / "AAPL.parquet")
        db = _make_db(decisions=[
            {"ticker": "AAPL", "direction": "LONG", "strategy": "SNIPER",
             "shares": 10, "entry_price": 100.0,
             "stop_loss": 95.0, "position_opened": 1},
        ])
        rep = ss.build_report(
            db_path=db, cache_dir=tmp_path,
            equity_override=100_000.0,
        )
        assert rep["summary"]["open_positions"] == 1
        row = rep["open_positions"][0]
        assert row["ticker"] == "AAPL"
        assert row["sizing"]["shadow_shares"] is not None

    def test_short_open_position_accrues_drag(self, tmp_path):
        (tmp_path / "prices").mkdir()
        bars = _make_bars([100.0] * 20)
        bars.to_parquet(tmp_path / "prices" / "TSLA.parquet")
        entry_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        db = _make_db(decisions=[
            {"ticker": "TSLA", "direction": "SHORT", "strategy": "SHORT",
             "shares": 50, "entry_price": 200.0, "fill_qty": 50,
             "fill_price": 200.0, "ts": entry_ts, "position_opened": 1},
        ])
        rep = ss.build_report(
            db_path=db, cache_dir=tmp_path,
            equity_override=100_000.0,
        )
        assert rep["summary"]["open_shorts"] == 1
        assert rep["summary"]["open_short_borrow_drag_dollar"] > 0

    def test_closed_short_outcomes_adjusted(self, tmp_path):
        db = _make_db(
            paper_signals=[
                {"id": "ps1", "ticker": "MSFT", "side": "SHORT",
                 "strategy": "SHORT", "sleeve": "SHORT_A"},
            ],
            outcomes=[
                {"signal_id": "ps1", "horizon_days": 30, "return_pct": 5.0,
                 "still_open": 0, "hold_complete": 1},
            ],
        )
        rep = ss.build_report(
            db_path=db, cache_dir=tmp_path,
            equity_override=100_000.0,
        )
        rows = rep["closed_short_outcomes"]
        assert len(rows) == 1
        bs = rows[0]["borrow_shadow"]
        assert bs["applies"] is True
        assert bs["adjusted_return_pct"] < 5.0
        # median adjusted should be present
        assert rep["summary"]["closed_short_median_borrow_adjusted_pct"] is not None

    def test_empty_db_safe(self, tmp_path):
        db = _make_db()
        rep = ss.build_report(
            db_path=db, cache_dir=tmp_path,
            equity_override=100_000.0,
        )
        assert rep["summary"]["open_positions"] == 0
        assert rep["summary"]["recent_signals"] == 0
        assert rep["summary"]["closed_short_outcomes"] == 0
        # No "≥2x" warning when no data
        assert not any("≥2× vol-target" in w for w in rep["warnings"])


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
