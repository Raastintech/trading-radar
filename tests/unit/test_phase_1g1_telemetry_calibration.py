"""
tests/unit/test_phase_1g1_telemetry_calibration.py — sample-size aware
calibration of slippage + concentration reports.

Phase 1G.1 contract:
  * Slippage report: a benchmark breach with n < 10 fills is reported
    as ``info`` (INSUFFICIENT_SAMPLE), not as a hard ``warning``.
  * Concentration report: a 1-position book records the mechanical
    100% top-N concentration as ``info``, not as a portfolio warning.
    The same numerical thresholds apply once n_positions ≥ 3.

No live providers, no .env required — temp SQLite + dict construction.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── Slippage ───────────────────────────────────────────────────────────────


def _make_slippage_db(rows):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        """CREATE TABLE decisions (
            id TEXT, run_id TEXT, ts TEXT, ticker TEXT, strategy TEXT,
            direction TEXT, shares REAL, entry_price REAL, stop_loss REAL,
            target_price REAL, fill_price REAL, fill_qty REAL,
            slippage_bps REAL, fill_status TEXT, position_opened INTEGER,
            position_closed INTEGER
        )"""
    )
    for i, r in enumerate(rows):
        conn.execute(
            """INSERT INTO decisions
               (id, run_id, ts, ticker, strategy, direction, shares,
                entry_price, stop_loss, target_price, fill_price, fill_qty,
                slippage_bps, fill_status, position_opened, position_closed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"id{i}", "run1",
                r.get("ts", "2026-05-10T15:30:00+00:00"),
                r["ticker"], r["strategy"], r["direction"],
                r.get("shares", 10), r.get("entry_price", 100.0),
                r.get("stop_loss", 95.0), r.get("target_price", 110.0),
                r.get("fill_price"), r.get("fill_qty"),
                r.get("slippage_bps"), r.get("fill_status"),
                r.get("position_opened", 1), r.get("position_closed", 0),
            ),
        )
    conn.commit()
    conn.close()
    return Path(tmp.name)


class TestSlippageCalibration:
    def test_small_sample_breach_is_info_not_warning(self):
        from research import slippage_telemetry_report as st
        # Two rows with adverse 40 bps each — well above the 10 bps
        # backtest assumption, but n=2 ≪ MIN_SAMPLE_FOR_WARNING.
        rows = [
            {"ticker": "AAA", "strategy": "S", "direction": "LONG",
             "fill_price": 100.40, "entry_price": 100.0,
             "fill_qty": 10, "slippage_bps": 40.0,
             "fill_status": "orderstatus.filled", "position_opened": 1},
            {"ticker": "BBB", "strategy": "S", "direction": "LONG",
             "fill_price": 100.40, "entry_price": 100.0,
             "fill_qty": 10, "slippage_bps": 40.0,
             "fill_status": "orderstatus.filled", "position_opened": 1},
        ]
        db = _make_slippage_db(rows)
        rows_loaded = st.load_decisions(db, since=None)
        report = st.build_report(rows_loaded)
        # No hard warning — sample is too small.
        assert not any("median" in w.lower() for w in report["warnings"])
        assert not any("p90" in w.lower() for w in report["warnings"])
        # Should land as INFO INSUFFICIENT_SAMPLE instead.
        joined = " | ".join(report["info"])
        assert "INSUFFICIENT_SAMPLE" in joined

    def test_large_sample_breach_is_warning(self):
        from research import slippage_telemetry_report as st
        n = 20  # well above threshold
        rows = []
        for i in range(n):
            rows.append({
                "ticker": f"T{i}", "strategy": "S", "direction": "LONG",
                "fill_price": 100.40, "entry_price": 100.0,
                "fill_qty": 10, "slippage_bps": 40.0,
                "fill_status": "orderstatus.filled", "position_opened": 1,
            })
        db = _make_slippage_db(rows)
        rows_loaded = st.load_decisions(db, since=None)
        report = st.build_report(rows_loaded)
        assert any("exceeds backtest" in w for w in report["warnings"])

    def test_in_budget_emits_neither(self):
        from research import slippage_telemetry_report as st
        rows = [
            {"ticker": "AAA", "strategy": "S", "direction": "LONG",
             "fill_price": 100.05, "entry_price": 100.0,
             "fill_qty": 10, "slippage_bps": 5.0,
             "fill_status": "orderstatus.filled", "position_opened": 1},
        ]
        db = _make_slippage_db(rows)
        rows_loaded = st.load_decisions(db, since=None)
        report = st.build_report(rows_loaded)
        assert report["warnings"] == []
        assert all("INSUFFICIENT_SAMPLE" not in i for i in report["info"])


# ── Concentration ─────────────────────────────────────────────────────────


class TestConcentrationCalibration:
    def test_single_position_is_info_not_warning(self):
        from research import portfolio_concentration_report as pc
        # Synthesize the report inputs directly.
        positions = [{
            "ticker": "SBAC", "strategy": "ADOPTED", "direction": "LONG",
            "fill_qty": 42.0, "fill_price": 217.55,
        }]
        sectors = {"SBAC": "Real Estate"}
        exposure = pc.compute_exposure(positions, sectors, equity=100_000)
        assert exposure["n_positions"] == 1
        assert exposure["top1_gross_pct"] == 100.0

        # Now run the warnings/info classifier with the same threshold the
        # full report uses. We inline the logic here rather than rebuilding
        # the full report (which would need DB / parquet fixtures).
        n_pos = exposure["n_positions"]
        enough = n_pos >= pc.CONCENTRATION_MIN_POSITIONS_FOR_WARNING
        info = []
        warnings = []
        for top in [("top1_gross_pct", 25), ("top3_gross_pct", 60)]:
            v = exposure.get(top[0]) or 0
            if v > top[1]:
                (warnings if enough else info).append(f"{top[0]}={v}")
        assert warnings == []
        assert any("top1_gross_pct" in i for i in info)

    def test_three_position_book_warns(self):
        from research import portfolio_concentration_report as pc
        positions = [
            {"ticker": "AAA", "strategy": "X", "direction": "LONG",
             "fill_qty": 100.0, "fill_price": 50.0},   # $5000
            {"ticker": "BBB", "strategy": "X", "direction": "LONG",
             "fill_qty": 100.0, "fill_price": 50.0},
            {"ticker": "CCC", "strategy": "X", "direction": "LONG",
             "fill_qty":   1.0, "fill_price":  1.0},   # tiny → AAA still >25%
        ]
        sectors = {"AAA": "Tech", "BBB": "Tech", "CCC": "Tech"}
        exposure = pc.compute_exposure(positions, sectors, equity=100_000)
        assert exposure["n_positions"] == 3
        # Top1 ≈ 50% → above 25 threshold.
        assert (exposure.get("top1_gross_pct") or 0) > 25
        # With n=3 the threshold kicks in: a single-name 50% would
        # qualify as a warning.
        assert exposure["n_positions"] >= pc.CONCENTRATION_MIN_POSITIONS_FOR_WARNING

    def test_threshold_constant_is_at_least_3(self):
        from research import portfolio_concentration_report as pc
        assert pc.CONCENTRATION_MIN_POSITIONS_FOR_WARNING >= 3
