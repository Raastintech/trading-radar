"""
tests/unit/test_portfolio_concentration.py — temp-SQLite + temp-parquet
tests for research/portfolio_concentration_report.py.

No live providers, no .env required.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import portfolio_concentration_report as pc  # noqa: E402


def _make_db(decisions, paper_sectors=None, profile_cache=None):
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
    for i, r in enumerate(decisions):
        conn.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"d{i}", "2026-05-01T10:00:00+00:00",
                r["ticker"], r.get("strategy", "SNIPER"), r["direction"],
                r.get("shares", 10), r.get("entry_price", 100.0),
                r.get("fill_price"), r.get("fill_qty"),
                r.get("stop_loss", 95.0),
                r.get("position_opened", 1), r.get("position_closed", 0),
            ),
        )
    if paper_sectors:
        conn.execute(
            """CREATE TABLE paper_signals (
                id TEXT, logged_at TEXT, ticker TEXT, sector TEXT
               )"""
        )
        for i, (t, sec) in enumerate(paper_sectors.items()):
            conn.execute(
                "INSERT INTO paper_signals VALUES (?,?,?,?)",
                (f"ps{i}", "2026-04-01T00:00:00+00:00", t, sec),
            )
    if profile_cache:
        conn.execute(
            "CREATE TABLE cache_meta (key TEXT PRIMARY KEY, fetched_at REAL, payload TEXT)"
        )
        for t, sec in profile_cache.items():
            conn.execute(
                "INSERT INTO cache_meta VALUES (?,?,?)",
                (f"fmp:profile:{t}", 0.0,
                 json.dumps({"ticker": t, "sector": sec, "industry": "x"})),
            )
    conn.commit()
    conn.close()
    return Path(tmp.name)


def _make_prices(tmpdir: Path, ticker: str, returns: list):
    """Build a parquet file at tmpdir/prices/{ticker}.parquet from a return series."""
    pdir = tmpdir / "prices"
    pdir.mkdir(parents=True, exist_ok=True)
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    n = len(closes)
    # Fixed-date sequence so freq/end-date math can't surprise us.
    dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(n)]
    df = pd.DataFrame({
        "open":   closes,
        "high":   [c * 1.01 for c in closes],
        "low":    [c * 0.99 for c in closes],
        "close":  closes,
        "volume": [1_000_000] * n,
    }, index=pd.DatetimeIndex(dates, name="date"))
    df.to_parquet(pdir / f"{ticker.upper()}.parquet")


class TestSectorResolution:
    def test_paper_then_profile_then_unknown(self):
        sectors = pc.resolve_sectors(
            ["A", "B", "C"],
            paper_sectors={"A": "Tech"},
            profile_sectors={"B": {"sector": "Energy", "industry": "x"}},
        )
        assert sectors == {"A": "Tech", "B": "Energy", "C": "UNKNOWN"}

    def test_paper_wins_over_profile(self):
        sectors = pc.resolve_sectors(
            ["A"],
            paper_sectors={"A": "Tech"},
            profile_sectors={"A": {"sector": "WrongAnswer", "industry": "x"}},
        )
        assert sectors["A"] == "Tech"


class TestPositionQtyPrice:
    def test_prefer_fill_qty_over_shares(self):
        row = {"shares": 100, "fill_qty": 95}
        assert pc._position_qty(row) == 95

    def test_fallback_to_shares(self):
        row = {"shares": 100, "fill_qty": None}
        assert pc._position_qty(row) == 100

    def test_prefer_fill_price(self):
        row = {"entry_price": 100, "fill_price": 99.5}
        assert pc._position_price(row) == 99.5


class TestExposure:
    def test_mixed_long_short(self):
        positions = [
            {"ticker": "A", "direction": "LONG", "strategy": "SNIPER",
             "shares": 100, "entry_price": 50.0},
            {"ticker": "B", "direction": "LONG", "strategy": "SNIPER",
             "shares": 50, "entry_price": 100.0},
            {"ticker": "C", "direction": "SHORT", "strategy": "SHORT",
             "shares": 20, "entry_price": 100.0},
        ]
        sectors = {"A": "Tech", "B": "Energy", "C": "Tech"}
        ex = pc.compute_exposure(positions, sectors, equity=100_000.0)
        # 100*50=5000 + 50*100=5000 + 20*100=2000
        assert ex["long_notional"] == 10_000.0
        assert ex["short_notional"] == 2_000.0
        assert ex["gross_notional"] == 12_000.0
        assert ex["net_notional"] == 8_000.0
        assert ex["gross_pct_equity"] == 12.0
        # Tech (A + C) > Energy (B)
        sec_names = [r["sector"] for r in ex["by_sector"]]
        assert sec_names[0] == "Tech"
        # Top ticker by notional is A or B (tied at 5000) ahead of C
        assert ex["by_ticker"][0]["notional"] in (5000.0,)

    def test_no_equity_gives_null_pct(self):
        positions = [{"ticker": "A", "direction": "LONG", "strategy": "X",
                      "shares": 10, "entry_price": 100.0}]
        ex = pc.compute_exposure(positions, {"A": "x"}, equity=None)
        assert ex["gross_pct_equity"] is None
        assert ex["gross_notional"] == 1000.0


class TestEndToEnd:
    def test_three_position_report(self, tmp_path):
        db = _make_db(
            decisions=[
                {"ticker": "AAPL", "direction": "LONG", "strategy": "SNIPER",
                 "shares": 10, "entry_price": 200.0, "fill_qty": 10,
                 "fill_price": 200.0, "position_opened": 1},
                {"ticker": "MSFT", "direction": "LONG", "strategy": "SNIPER",
                 "shares": 5, "entry_price": 300.0, "fill_qty": 5,
                 "fill_price": 300.0, "position_opened": 1},
                {"ticker": "TSLA", "direction": "SHORT", "strategy": "SHORT",
                 "shares": 4, "entry_price": 250.0, "fill_qty": 4,
                 "fill_price": 250.0, "position_opened": 1},
                # Closed — should not appear
                {"ticker": "OLD", "direction": "LONG", "strategy": "SNIPER",
                 "shares": 1, "position_opened": 1, "position_closed": 1},
            ],
            paper_sectors={"AAPL": "Technology", "MSFT": "Technology"},
            profile_cache={"TSLA": "Consumer Cyclical"},
        )
        rep = pc.build_report(db, tmp_path, equity_override=50_000.0,
                              skip_correlation=True)
        s = rep["summary"]
        assert s["n_positions"] == 3
        # AAPL: 2000, MSFT: 1500, TSLA: 1000 short
        assert s["gross_notional"] == 4500.0
        assert s["long_notional"] == 3500.0
        assert s["short_notional"] == 1000.0
        # Tech total 3500, Consumer Cyclical 1000
        sec_first = rep["by_sector"][0]
        assert sec_first["sector"] == "Technology"
        # Sector source counts: AAPL+MSFT from paper, TSLA from profile, 0 unknown
        ssc = rep["sector_source_counts"]
        assert ssc["paper_signals"] == 2
        assert ssc["fmp_profile"] == 1
        assert ssc["unknown"] == 0

    def test_warnings_top1_concentration(self, tmp_path):
        # Phase 1G.1: the concentration WARNING gate requires
        # n_positions >= CONCENTRATION_MIN_POSITIONS_FOR_WARNING (3).
        # We add a third tiny position so the n-gate clears and the
        # numerical top1 threshold drives the warning.
        db = _make_db([
            {"ticker": "BIG", "direction": "LONG", "strategy": "SNIPER",
             "shares": 100, "entry_price": 1000.0, "fill_qty": 100,
             "fill_price": 1000.0, "position_opened": 1},
            {"ticker": "SMALL", "direction": "LONG", "strategy": "SNIPER",
             "shares": 1, "entry_price": 100.0, "fill_qty": 1,
             "fill_price": 100.0, "position_opened": 1},
            {"ticker": "TINY", "direction": "LONG", "strategy": "SNIPER",
             "shares": 1, "entry_price": 50.0, "fill_qty": 1,
             "fill_price": 50.0, "position_opened": 1},
        ])
        rep = pc.build_report(db, tmp_path, equity_override=200_000.0,
                              skip_correlation=True)
        # BIG is ~99.85% of gross — well above 25% top1 threshold.
        assert any("top single name" in w for w in rep["warnings"])

    def test_two_position_concentration_is_info_not_warning(self, tmp_path):
        # Phase 1G.1: with n=2 < CONCENTRATION_MIN_POSITIONS_FOR_WARNING,
        # an extreme top1 share is reported as INFO, not WARNING.
        db = _make_db([
            {"ticker": "BIG", "direction": "LONG", "strategy": "SNIPER",
             "shares": 100, "entry_price": 1000.0, "fill_qty": 100,
             "fill_price": 1000.0, "position_opened": 1},
            {"ticker": "SMALL", "direction": "LONG", "strategy": "SNIPER",
             "shares": 1, "entry_price": 100.0, "fill_qty": 1,
             "fill_price": 100.0, "position_opened": 1},
        ])
        rep = pc.build_report(db, tmp_path, equity_override=200_000.0,
                              skip_correlation=True)
        # No actionable warning for the top1 share — too few positions.
        assert not any("top single name" in w for w in rep["warnings"])
        # But it lands in the info channel along with a SINGLE_POSITION_BOOK
        # marker (or similar n<3 message).
        info_text = " | ".join(rep.get("info") or [])
        assert "top single name" in info_text or "SINGLE_POSITION_BOOK" in info_text

    def test_empty_db_safe(self, tmp_path):
        db = _make_db([])
        rep = pc.build_report(db, tmp_path, equity_override=50_000.0,
                              skip_correlation=True)
        assert rep["summary"]["n_positions"] == 0
        assert rep["warnings"] == []


class TestCorrelation:
    def test_correlated_tickers_cluster_together(self, tmp_path):
        # AAPL and MSFT move together; UNREL is anticorrelated
        ret_a = [0.01, -0.02, 0.015, -0.01, 0.02, -0.015, 0.01, -0.005,
                 0.012, -0.018, 0.022, -0.011, 0.013, -0.006, 0.014, -0.012]
        ret_b = [r + 0.0005 for r in ret_a]   # near-perfectly correlated
        ret_c = [-r for r in ret_a]            # negatively correlated
        _make_prices(tmp_path, "AAPL", ret_a)
        _make_prices(tmp_path, "MSFT", ret_b)
        _make_prices(tmp_path, "UNREL", ret_c)
        db = _make_db([
            {"ticker": "AAPL", "direction": "LONG", "strategy": "SNIPER",
             "shares": 10, "entry_price": 100, "fill_qty": 10,
             "fill_price": 100, "position_opened": 1},
            {"ticker": "MSFT", "direction": "LONG", "strategy": "SNIPER",
             "shares": 10, "entry_price": 100, "fill_qty": 10,
             "fill_price": 100, "position_opened": 1},
            {"ticker": "UNREL", "direction": "LONG", "strategy": "SNIPER",
             "shares": 10, "entry_price": 100, "fill_qty": 10,
             "fill_price": 100, "position_opened": 1},
        ])
        rep = pc.build_report(db, tmp_path, equity_override=100_000.0,
                              skip_correlation=False)
        clusters = rep["correlation"]["clusters"]
        # AAPL/MSFT should cluster; UNREL should not be in that cluster
        joined = [set(c) for c in clusters]
        assert any({"AAPL", "MSFT"}.issubset(c) for c in joined)
        assert not any("UNREL" in c and "AAPL" in c for c in joined)

    def test_skipped_when_no_cache(self, tmp_path):
        db = _make_db([
            {"ticker": "NOPRICE", "direction": "LONG", "strategy": "X",
             "shares": 10, "entry_price": 100, "fill_qty": 10,
             "fill_price": 100, "position_opened": 1},
        ])
        rep = pc.build_report(db, tmp_path, equity_override=50_000.0,
                              skip_correlation=False)
        # No cache file for NOPRICE → skipped, no clusters
        # When only 1 ticker, correlation is trivially empty
        assert rep["correlation"]["clusters"] == []


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
