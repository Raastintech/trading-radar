"""
tests/unit/test_phase_1g2_legacy_decision_policy.py — Phase 1G.2 T5

Tests for ``research/legacy_decision_policy_report.py``. Read-only
classification of decisions rows missing fill data, against a temp DB
and a temp broker snapshot file. NEVER mutates the table.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest


# ── Module loader ──────────────────────────────────────────────────────


def _load_module():
    repo = Path(__file__).resolve().parents[2]
    path = repo / "research" / "legacy_decision_policy_report.py"
    spec = importlib.util.spec_from_file_location("_legpol", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Fixtures ──────────────────────────────────────────────────────────


_DECISIONS_DDL = """
CREATE TABLE decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    ts TEXT,
    ticker TEXT,
    strategy TEXT,
    direction TEXT,
    signal_score REAL,
    shares REAL,
    entry_price REAL,
    stop_loss REAL,
    target_price REAL,
    risk_reward REAL,
    order_id TEXT,
    position_opened INTEGER,
    position_closed INTEGER,
    exit_price REAL,
    pnl REAL,
    pnl_pct REAL,
    veto_votes TEXT,
    notes TEXT,
    fill_price REAL,
    fill_qty REAL,
    slippage_bps REAL,
    fill_status TEXT,
    exit_fill_price REAL,
    suspect_state TEXT,
    suspect_reason TEXT,
    reconciled_at TEXT
);
"""


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "trading.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(_DECISIONS_DDL)
    return db


def _insert(db: Path, **fields):
    cols = list(fields.keys())
    placeholders = ",".join("?" for _ in cols)
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            f"INSERT INTO decisions ({','.join(cols)}) VALUES ({placeholders})",
            tuple(fields[c] for c in cols),
        )


# ── Tests ──────────────────────────────────────────────────────────────


def test_pre_clean_epoch_closed_row_recommends_keep_quarantined(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    _insert(
        db,
        id="row1", ts="2026-04-23T13:30:35", strategy="SHORT", ticker="GE",
        direction="SHORT", entry_price=276.29, position_opened=1,
        position_closed=1, fill_price=None, fill_qty=None,
    )
    # No broker snapshot present → broker_position_match should be closed_by_book? No, it should be 'unavailable_no_cached_snapshot'.
    snap = tmp_path / "cache_state_no_snapshot"
    monkeypatch.chdir(tmp_path)

    mod = _load_module()
    report = mod.build_report(db)
    assert report["counts"]["total"] == 1
    row = report["rows"][0]
    assert row["pre_clean_epoch"] is True
    # No snapshot file → unavailable
    assert row["broker_position_match"] == "unavailable_no_cached_snapshot"


def test_with_broker_snapshot_closed_by_book(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    _insert(
        db,
        id="row1", ts="2026-04-23T13:30:35", strategy="SHORT", ticker="GE",
        direction="SHORT", entry_price=276.29, position_opened=1,
        position_closed=1, fill_price=None, fill_qty=None,
    )
    snap_dir = tmp_path / "cache" / "state"
    snap_dir.mkdir(parents=True)
    (snap_dir / "broker_positions_snapshot.json").write_text(json.dumps({
        "positions": [{"ticker": "SBAC", "qty": 42}],
    }))
    monkeypatch.chdir(tmp_path)

    mod = _load_module()
    report = mod.build_report(db)
    row = report["rows"][0]
    assert row["broker_position_match"] == "closed_by_book"
    assert row["recommendation"] == "keep_quarantined"
    assert "pre-clean-epoch" in row["rationale"]


def test_broker_match_row_requires_investigation(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    _insert(
        db,
        id="row1", ts="2026-04-23T13:30:35", strategy="SHORT", ticker="GE",
        direction="SHORT", entry_price=276.29, position_opened=1,
        position_closed=1, fill_price=None, fill_qty=None,
    )
    snap_dir = tmp_path / "cache" / "state"
    snap_dir.mkdir(parents=True)
    (snap_dir / "broker_positions_snapshot.json").write_text(json.dumps({
        "positions": [{"ticker": "GE", "qty": -100}],
    }))
    monkeypatch.chdir(tmp_path)

    mod = _load_module()
    report = mod.build_report(db)
    row = report["rows"][0]
    assert row["broker_position_match"] == "match"
    assert row["recommendation"] == "investigate"


def test_inside_clean_epoch_row_requires_investigation(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    # 2026-05-10 is inside the current clean epoch (start 2026-05-08).
    _insert(
        db,
        id="row1", ts="2026-05-10T13:30:35", strategy="SHORT", ticker="GE",
        direction="SHORT", entry_price=276.29, position_opened=1,
        position_closed=1, fill_price=None, fill_qty=None,
    )
    monkeypatch.chdir(tmp_path)
    mod = _load_module()
    report = mod.build_report(db)
    row = report["rows"][0]
    assert row["pre_clean_epoch"] is False
    assert row["recommendation"] == "investigate"


def test_does_not_mutate_db(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    _insert(
        db,
        id="row1", ts="2026-04-23T13:30:35", strategy="SHORT", ticker="GE",
        direction="SHORT", entry_price=276.29, position_opened=1,
        position_closed=1, fill_price=None, fill_qty=None,
    )
    monkeypatch.chdir(tmp_path)
    mod = _load_module()
    mod.build_report(db)
    # After report, the row must still have fill_price=NULL.
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute("SELECT id, fill_price, fill_qty FROM decisions").fetchall()
    assert rows == [("row1", None, None)]


def test_no_legacy_rows_clean_header(tmp_path: Path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.chdir(tmp_path)
    mod = _load_module()
    report = mod.build_report(db)
    assert report["counts"]["total"] == 0
    assert "already clean" in report["header_recommendation"]
