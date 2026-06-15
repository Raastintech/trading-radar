"""
tests/unit/test_paper_evidence_epoch.py — Phase 1D epoch module tests.

Covers ``core.paper_evidence_epoch`` directly plus the back-compat
invariant that each Phase 1B telemetry sidecar still exposes the
pre-Phase-1D flat shape at the JSON root (so the dashboard's RISK
TELEMETRY panel keeps working without changes).
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import paper_evidence_epoch as epoch  # noqa: E402


# ── is_legacy ────────────────────────────────────────────────────────────────

class TestIsLegacy:
    def test_pre_epoch_is_legacy(self):
        assert epoch.is_legacy("2026-04-15T12:00:00+00:00") is True

    def test_at_epoch_is_not_legacy(self):
        assert epoch.is_legacy(epoch.CLEAN_PAPER_EVIDENCE_START) is False

    def test_post_epoch_is_not_legacy(self):
        assert epoch.is_legacy("2027-01-01T00:00:00+00:00") is False

    def test_none_is_legacy(self):
        assert epoch.is_legacy(None) is True

    def test_empty_is_legacy(self):
        assert epoch.is_legacy("") is True

    def test_unparseable_is_legacy(self):
        assert epoch.is_legacy("not-a-timestamp") is True

    def test_naive_timestamp_treated_as_utc(self):
        # Naive ts that is strictly before the epoch ⇒ legacy.
        assert epoch.is_legacy("2026-01-01T00:00:00") is True
        # Naive ts after the epoch ⇒ not legacy.
        assert epoch.is_legacy("2027-01-01T00:00:00") is False

    def test_custom_cutoff(self):
        ts = "2026-05-10T00:00:00+00:00"
        assert epoch.is_legacy(ts) is False  # default cutoff 2026-05-08
        assert epoch.is_legacy(ts, "2026-06-01T00:00:00+00:00") is True

    def test_z_suffix_supported(self):
        assert epoch.is_legacy("2026-04-15T12:00:00Z") is True
        assert epoch.is_legacy("2027-01-01T00:00:00Z") is False


# ── Back-compat: dashboard-shape keys at JSON root ───────────────────────────
#
# The dashboard's RISK TELEMETRY panel reads flat keys (``summary``,
# ``overall_adverse_bps``, ``warnings``, ``correlation``). Phase 1D adds
# clean-epoch dual views WITHOUT moving those keys.

def _empty_db() -> Path:
    p = Path(tempfile.mkdtemp()) / "trading.db"
    p.touch()
    return p


def test_slippage_dual_scope_preserves_flat_shape():
    from research import slippage_telemetry_report as st

    db = _empty_db()
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, ts TEXT, ticker TEXT, strategy TEXT,
            direction TEXT, fill_price REAL, fill_qty REAL,
            slippage_bps REAL, fill_status TEXT, shares REAL,
            position_opened INTEGER DEFAULT 0
        )
    """)
    conn.commit(); conn.close()

    dual = st.build_dual_scope_report(db)
    # Pre-Phase-1D readers expect these at the JSON root.
    assert "summary" in dual
    assert "overall_adverse_bps" in dual
    assert "warnings" in dual
    # Phase 1D additive sub-blocks.
    assert "clean_epoch_start" in dual
    assert "full_ledger" in dual
    assert "clean_epoch" in dual
    # full_ledger view mirrors the root.
    assert dual["full_ledger"]["summary"] == dual["summary"]


def test_concentration_dual_scope_preserves_flat_shape(tmp_path):
    from research import portfolio_concentration_report as pc

    db = _empty_db()
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, ts TEXT, ticker TEXT, strategy TEXT,
            direction TEXT, shares REAL, entry_price REAL, fill_price REAL,
            fill_qty REAL,
            position_opened INTEGER DEFAULT 0,
            position_closed INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE paper_signals (
            id TEXT PRIMARY KEY, logged_at TEXT, strategy TEXT, sleeve TEXT,
            ticker TEXT, side TEXT, signal_version TEXT, entry_price REAL,
            sector TEXT, status TEXT
        )
    """)
    conn.commit(); conn.close()

    dual = pc.build_dual_scope_report(db, tmp_path)
    assert "summary" in dual
    assert "warnings" in dual
    assert "clean_epoch" in dual
    assert "full_ledger" in dual
    assert dual["clean_epoch_start"] == epoch.CLEAN_PAPER_EVIDENCE_START


def test_shadow_dual_scope_preserves_flat_shape(tmp_path):
    from research import shadow_sizing_report as ss

    db = _empty_db()
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, ts TEXT, ticker TEXT, strategy TEXT,
            direction TEXT, shares REAL, entry_price REAL, fill_price REAL,
            fill_qty REAL,
            position_opened INTEGER DEFAULT 0,
            position_closed INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE paper_signals (
            id TEXT PRIMARY KEY, logged_at TEXT, strategy TEXT, sleeve TEXT,
            ticker TEXT, side TEXT, signal_version TEXT, entry_price REAL,
            status TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE paper_signal_outcomes (
            id TEXT PRIMARY KEY, signal_id TEXT, horizon_days INTEGER,
            measured_at TEXT, return_pct REAL, still_open INTEGER,
            hold_complete INTEGER
        )
    """)
    conn.commit(); conn.close()

    dual = ss.build_dual_scope_report(
        db_path=db, cache_dir=tmp_path,
    )
    assert "summary" in dual
    assert "warnings" in dual
    assert "clean_epoch" in dual
    assert "full_ledger" in dual
    assert dual["clean_epoch_start"] == epoch.CLEAN_PAPER_EVIDENCE_START


def test_constants_exported():
    """The two public constants must be importable for downstream
    callers (reports, dashboards if/when they pick up clean-epoch)."""
    assert isinstance(epoch.CLEAN_PAPER_EVIDENCE_START, str)
    assert epoch.QUARANTINE_REASON_PRE_FILL_TELEMETRY == "pre_fill_telemetry_legacy"
    # CLEAN_PAPER_EVIDENCE_START must be ISO-parseable.
    from datetime import datetime
    datetime.fromisoformat(
        epoch.CLEAN_PAPER_EVIDENCE_START.replace("Z", "+00:00")
    )
