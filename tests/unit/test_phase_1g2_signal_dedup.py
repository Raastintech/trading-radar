"""
tests/unit/test_phase_1g2_signal_dedup.py — Phase 1G.2 T1

Tests for ``core.signal_hygiene`` setup-state hashing and the
``find_recent_duplicate`` query against a temp SQLite DB. No provider
calls, no real DB writes outside the temp file each test creates.
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from core import signal_hygiene as sh


# ── Test DB helper ─────────────────────────────────────────────────────


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS paper_signals (
    id TEXT PRIMARY KEY,
    logged_at TEXT NOT NULL,
    strategy TEXT NOT NULL,
    sleeve TEXT,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    signal_version TEXT,
    entry_price REAL,
    stop_loss REAL,
    target_price REAL,
    risk_reward REAL,
    score REAL,
    sector TEXT,
    regime_context TEXT,
    key_features TEXT,
    allocation_bucket TEXT,
    allocation_pct REAL,
    qualified_reason TEXT,
    notes TEXT,
    status TEXT,
    aux_h3 TEXT,
    setup_state_hash TEXT
);
"""


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "trading.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(_SCHEMA_SQL)
    return db


def _insert(
    db_path: Path,
    *,
    strategy: str = "SHORT",
    ticker: str = "INTU",
    signal_version: str = "SHORT_A",
    setup_state_hash: str = "abc123",
    logged_at: str | None = None,
    status: str = "governance_blocked",
    score: float = 85.0,
    side: str = "SHORT",
    risk_reward: float = 2.0,
    entry_price: float = 313.04,
    regime_context: str = '{"regime_cluster": "earnings_event_short"}',
) -> str:
    sig_id = str(uuid.uuid4())
    if logged_at is None:
        logged_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO paper_signals (id, logged_at, strategy, sleeve, ticker, "
            "side, signal_version, entry_price, score, risk_reward, status, "
            "regime_context, setup_state_hash, key_features) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sig_id, logged_at, strategy, signal_version, ticker, side,
                signal_version, entry_price, score, risk_reward, status,
                regime_context, setup_state_hash, "{}",
            ),
        )
    return sig_id


# ── setup_state_hash ───────────────────────────────────────────────────


def test_setup_state_hash_is_deterministic():
    opp = {"score": 85.0, "direction": "SHORT", "risk_reward": 2.0, "entry_price": 313.04}
    payload = {"regime_context": {"regime_cluster": "earnings_event_short"}}
    h1 = sh.setup_state_hash(opp, payload)
    h2 = sh.setup_state_hash(opp, payload)
    assert h1 == h2
    assert isinstance(h1, str) and len(h1) == 12


def test_setup_state_hash_changes_on_score_bucket_flip():
    opp_low = {"score": 84.0, "direction": "SHORT", "risk_reward": 2.0, "entry_price": 313.04}
    opp_high = {"score": 85.0, "direction": "SHORT", "risk_reward": 2.0, "entry_price": 313.04}
    payload = {"regime_context": {"regime_cluster": "earnings_event_short"}}
    assert sh.setup_state_hash(opp_low, payload) != sh.setup_state_hash(opp_high, payload)


def test_setup_state_hash_stable_under_micro_price_drift():
    # Same score bucket, same RR bucket, same entry bucket: hash matches.
    payload = {"regime_context": {"regime_cluster": "earnings_event_short"}}
    a = {"score": 85.0, "direction": "SHORT", "risk_reward": 2.0, "entry_price": 313.04}
    b = {"score": 85.0, "direction": "SHORT", "risk_reward": 2.0, "entry_price": 313.06}
    assert sh.setup_state_hash(a, payload) == sh.setup_state_hash(b, payload)


def test_setup_state_hash_changes_on_regime_cluster():
    opp = {"score": 85.0, "direction": "SHORT", "risk_reward": 2.0, "entry_price": 313.04}
    pay_a = {"regime_context": {"regime_cluster": "earnings_event_short"}}
    pay_b = {"regime_context": {"regime_cluster": "structural_short"}}
    assert sh.setup_state_hash(opp, pay_a) != sh.setup_state_hash(opp, pay_b)


# ── find_recent_duplicate ──────────────────────────────────────────────


def test_same_signal_same_day_is_suppressed(tmp_path: Path):
    db = _make_db(tmp_path)
    _insert(db, setup_state_hash="hash_abc")
    v = sh.find_recent_duplicate(
        strategy="SHORT", ticker="INTU", signal_version="SHORT_A",
        setup_state_hash_value="hash_abc", db_path=db,
    )
    assert v.suppress is True
    assert v.matched_id is not None
    assert "setup_state_hash" in v.reason


def test_changed_state_emits_new_signal(tmp_path: Path):
    db = _make_db(tmp_path)
    _insert(db, setup_state_hash="hash_v1")
    v = sh.find_recent_duplicate(
        strategy="SHORT", ticker="INTU", signal_version="SHORT_A",
        setup_state_hash_value="hash_v2", db_path=db,
    )
    assert v.suppress is False
    assert v.matched_id is None


def test_next_utc_day_re_emits(tmp_path: Path):
    db = _make_db(tmp_path)
    yesterday = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    _insert(db, setup_state_hash="hash_xyz", logged_at=yesterday)
    v = sh.find_recent_duplicate(
        strategy="SHORT", ticker="INTU", signal_version="SHORT_A",
        setup_state_hash_value="hash_xyz", db_path=db,
        window_hours=24.0,
    )
    assert v.suppress is False, "row older than the window should not suppress"


def test_different_strategy_or_version_emits_separately(tmp_path: Path):
    db = _make_db(tmp_path)
    _insert(db, strategy="SHORT", signal_version="SHORT_A", setup_state_hash="hash_abc")
    # Different strategy
    v1 = sh.find_recent_duplicate(
        strategy="SNIPER", ticker="INTU", signal_version="SHORT_A",
        setup_state_hash_value="hash_abc", db_path=db,
    )
    assert v1.suppress is False
    # Different signal_version
    v2 = sh.find_recent_duplicate(
        strategy="SHORT", ticker="INTU", signal_version="SHORT_B",
        setup_state_hash_value="hash_abc", db_path=db,
    )
    assert v2.suppress is False


def test_diagnostic_rows_do_not_block_real_signal(tmp_path: Path):
    db = _make_db(tmp_path)
    _insert(db, status="presize_blocked", setup_state_hash="hash_abc")
    _insert(db, status="regime_suppressed", setup_state_hash="hash_abc")
    v = sh.find_recent_duplicate(
        strategy="SHORT", ticker="INTU", signal_version="SHORT_A",
        setup_state_hash_value="hash_abc", db_path=db,
    )
    assert v.suppress is False


def test_legacy_row_without_hash_falls_back_to_recomputed_match(tmp_path: Path):
    """Rows logged before the Phase 1G.2 migration carry NULL
    setup_state_hash but still expose the bucket inputs. The dedup
    code should reconstruct the hash and detect a match."""
    db = _make_db(tmp_path)
    # Insert a legacy row with hash NULL but matching fields.
    sig_id = str(uuid.uuid4())
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO paper_signals (id, logged_at, strategy, sleeve, ticker, "
            "side, signal_version, entry_price, score, risk_reward, status, "
            "regime_context, key_features) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sig_id, datetime.now(timezone.utc).isoformat(),
                "SHORT", "SHORT_A", "INTU", "SHORT", "SHORT_A",
                313.04, 85.0, 2.0, "governance_blocked",
                '{"regime_cluster": "earnings_event_short"}', "{}",
            ),
        )
    opp = {"score": 85.0, "direction": "SHORT", "risk_reward": 2.0, "entry_price": 313.04}
    payload = {"regime_context": {"regime_cluster": "earnings_event_short"}}
    h = sh.setup_state_hash(opp, payload)
    v = sh.find_recent_duplicate(
        strategy="SHORT", ticker="INTU", signal_version="SHORT_A",
        setup_state_hash_value=h, db_path=db,
    )
    assert v.suppress is True
    assert v.matched_id == sig_id


def test_no_db_present_does_not_suppress(tmp_path: Path):
    v = sh.find_recent_duplicate(
        strategy="SHORT", ticker="INTU", signal_version="SHORT_A",
        setup_state_hash_value="anything",
        db_path=tmp_path / "does_not_exist.db",
    )
    assert v.suppress is False


def test_counters_track_suppressions():
    counters = sh.HygieneCounters()
    counters.record_dup("SHORT", "INTU", "same hash")
    counters.record_dup("SHORT", "INTU", "same hash")
    counters.record_dup("SHORT", "BIRK", "same hash")
    snap = counters.snapshot()
    assert snap["duplicate_suppressed"]["count"] == 3
    assert snap["duplicate_suppressed"]["by_ticker"]["INTU"] == 2
    assert snap["duplicate_suppressed"]["by_ticker"]["BIRK"] == 1
    assert snap["duplicate_suppressed"]["by_strategy"]["SHORT"] == 3
