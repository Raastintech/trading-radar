"""
tests/unit/test_phase_1e_broker_match.py — Phase 1E broker-snapshot
enrichment tests.

Covers:
  - quarantine entries are enriched with ``broker_position_match`` when a
    broker snapshot is present
  - the three labels: ``match``, ``no_broker_position``, ``closed_by_book``
  - missing / unreadable snapshot ⇒ ``broker_position_match=None`` and
    ``broker_match_source=unavailable_no_cached_snapshot``
  - the snapshot CLI's ``write_sidecar`` produces the expected payload
    shape and is atomic (.tmp followed by rename)
  - DB is not mutated by the enrichment pass
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import paper_state_hygiene_report as h  # noqa: E402
from scripts import snapshot_broker_positions as snap  # noqa: E402


_DECISIONS_DDL = """
CREATE TABLE decisions (
    id TEXT PRIMARY KEY, run_id TEXT, ts TEXT NOT NULL, ticker TEXT NOT NULL,
    strategy TEXT NOT NULL, direction TEXT NOT NULL, signal_score REAL,
    shares REAL, entry_price REAL, stop_loss REAL, target_price REAL,
    risk_reward REAL, order_id TEXT,
    position_opened INTEGER DEFAULT 0, position_closed INTEGER DEFAULT 0,
    exit_price REAL, pnl REAL, pnl_pct REAL, veto_votes TEXT, notes TEXT,
    fill_price REAL, fill_qty REAL, slippage_bps REAL,
    fill_status TEXT, exit_fill_price REAL
)
"""

PRE_EPOCH_TS = "2026-04-15T12:00:00+00:00"


def _make_db_with_legacy_rows(tmp_path: Path) -> Path:
    db = tmp_path / "trading.db"
    conn = sqlite3.connect(str(db))
    conn.execute(_DECISIONS_DDL)
    # Three pre-epoch legacy rows, all missing fill_price:
    #   - AAA open in book (still open)         → expect "match" if broker has AAA
    #   - BBB open in book                       → expect "no_broker_position"
    #   - CCC closed in book                     → expect "closed_by_book"
    conn.execute(
        "INSERT INTO decisions (id, ts, ticker, strategy, direction, "
        "position_opened, position_closed) VALUES (?,?,?,?,?,?,?)",
        ("d-aaa", PRE_EPOCH_TS, "AAA", "SNIPER", "long", 1, 0),
    )
    conn.execute(
        "INSERT INTO decisions (id, ts, ticker, strategy, direction, "
        "position_opened, position_closed) VALUES (?,?,?,?,?,?,?)",
        ("d-bbb", PRE_EPOCH_TS, "BBB", "SNIPER", "long", 1, 0),
    )
    conn.execute(
        "INSERT INTO decisions (id, ts, ticker, strategy, direction, "
        "position_opened, position_closed, pnl, exit_price) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("d-ccc", PRE_EPOCH_TS, "CCC", "SNIPER", "long", 1, 1, None, None),
    )
    conn.commit(); conn.close()
    return db


def _write_broker_snapshot(path: Path, tickers: list[str]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source":       "alpaca.get_positions",
        "count":        len(tickers),
        "positions": [
            {"ticker": t, "qty": 10.0, "side": "long",
             "entry_price": 100.0}
            for t in tickers
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ── Enrichment labels ────────────────────────────────────────────────────────

class TestBrokerMatchEnrichment:
    def test_match_when_broker_has_open_position(self, tmp_path):
        db = _make_db_with_legacy_rows(tmp_path)
        snap_path = tmp_path / "broker.json"
        _write_broker_snapshot(snap_path, ["AAA"])

        conn = sqlite3.connect(str(db))
        entries = h.build_legacy_quarantine(
            conn, h.CLEAN_PAPER_EVIDENCE_START,
            broker_snapshot_path=snap_path,
        )
        conn.close()

        by_id = {e["decision_id"]: e for e in entries}
        assert by_id["d-aaa"]["broker_position_match"] == "match"

    def test_no_broker_position_when_ticker_absent(self, tmp_path):
        db = _make_db_with_legacy_rows(tmp_path)
        snap_path = tmp_path / "broker.json"
        _write_broker_snapshot(snap_path, ["ZZZ"])  # different ticker

        conn = sqlite3.connect(str(db))
        entries = h.build_legacy_quarantine(
            conn, h.CLEAN_PAPER_EVIDENCE_START,
            broker_snapshot_path=snap_path,
        )
        conn.close()

        by_id = {e["decision_id"]: e for e in entries}
        assert by_id["d-bbb"]["broker_position_match"] == "no_broker_position"

    def test_closed_by_book_when_decision_closed(self, tmp_path):
        db = _make_db_with_legacy_rows(tmp_path)
        snap_path = tmp_path / "broker.json"
        _write_broker_snapshot(snap_path, ["AAA"])  # CCC is closed in book

        conn = sqlite3.connect(str(db))
        entries = h.build_legacy_quarantine(
            conn, h.CLEAN_PAPER_EVIDENCE_START,
            broker_snapshot_path=snap_path,
        )
        conn.close()

        by_id = {e["decision_id"]: e for e in entries}
        assert by_id["d-ccc"]["broker_position_match"] == "closed_by_book"

    def test_missing_snapshot_leaves_field_none(self, tmp_path):
        db = _make_db_with_legacy_rows(tmp_path)
        # Snapshot path that does NOT exist.
        snap_path = tmp_path / "does-not-exist.json"

        conn = sqlite3.connect(str(db))
        entries = h.build_legacy_quarantine(
            conn, h.CLEAN_PAPER_EVIDENCE_START,
            broker_snapshot_path=snap_path,
        )
        conn.close()

        for e in entries:
            assert e["broker_position_match"] is None

    def test_unreadable_snapshot_degrades_to_none(self, tmp_path):
        db = _make_db_with_legacy_rows(tmp_path)
        snap_path = tmp_path / "broker.json"
        snap_path.write_text("not-json{{", encoding="utf-8")

        conn = sqlite3.connect(str(db))
        entries = h.build_legacy_quarantine(
            conn, h.CLEAN_PAPER_EVIDENCE_START,
            broker_snapshot_path=snap_path,
        )
        conn.close()

        for e in entries:
            assert e["broker_position_match"] is None


# ── Report-level integration ─────────────────────────────────────────────────

class TestBuildReportWithBrokerSnapshot:
    def test_legacy_quarantine_block_carries_broker_match_counts(self, tmp_path):
        db = _make_db_with_legacy_rows(tmp_path)
        snap_path = tmp_path / "broker.json"
        _write_broker_snapshot(snap_path, ["AAA"])

        rpt = h.build_report(
            db, clean_epoch_iso=h.CLEAN_PAPER_EVIDENCE_START,
            broker_snapshot_path=snap_path,
        )
        lq = rpt["legacy_quarantine"]
        assert lq["broker_match_source"] == "broker_snapshot_sidecar"
        assert lq["broker_snapshot_generated_at"] is not None
        bmc = lq["broker_match_counts"]
        # One match (AAA), one no_broker_position (BBB), one closed_by_book (CCC)
        assert bmc["match"] == 1
        assert bmc["no_broker_position"] == 1
        assert bmc["closed_by_book"] == 1
        assert bmc["unknown"] == 0

    def test_no_snapshot_reports_unavailable_source(self, tmp_path):
        db = _make_db_with_legacy_rows(tmp_path)
        rpt = h.build_report(
            db, clean_epoch_iso=h.CLEAN_PAPER_EVIDENCE_START,
            broker_snapshot_path=tmp_path / "missing.json",
        )
        lq = rpt["legacy_quarantine"]
        assert lq["broker_match_source"] == "unavailable_no_cached_snapshot"
        assert lq["broker_snapshot_generated_at"] is None
        # broker_match_counts always present, all entries 'unknown'.
        assert lq["broker_match_counts"]["unknown"] == 3
        assert lq["broker_match_counts"]["match"] == 0

    def test_operator_review_includes_broker_match_counts(self, tmp_path):
        db = _make_db_with_legacy_rows(tmp_path)
        snap_path = tmp_path / "broker.json"
        _write_broker_snapshot(snap_path, ["AAA", "BBB"])

        rpt = h.build_report(
            db, clean_epoch_iso=h.CLEAN_PAPER_EVIDENCE_START,
            broker_snapshot_path=snap_path,
        )
        op = rpt["operator_review"]
        assert op["broker_position_match_source"] == "broker_snapshot_sidecar"
        bmc = op["broker_match_counts"]
        assert bmc["match"] == 2  # AAA + BBB
        assert bmc["closed_by_book"] == 1  # CCC


# ── Snapshot CLI ─────────────────────────────────────────────────────────────

class TestSnapshotCLI:
    def test_write_sidecar_shape(self, tmp_path):
        out = tmp_path / "broker_positions_snapshot.json"
        positions = [
            {"ticker": "AAA", "qty": 10.0, "side": "long",
             "entry_price": 100.0, "current_price": 105.0,
             "market_value": 1050.0, "unrealized_pnl": 50.0},
        ]
        payload = snap.write_sidecar(positions, out)

        assert out.exists()
        on_disk = json.loads(out.read_text(encoding="utf-8"))
        assert on_disk["source"] == "alpaca.get_positions"
        assert on_disk["count"] == 1
        assert on_disk["positions"][0]["ticker"] == "AAA"
        assert payload["count"] == 1

    def test_write_sidecar_empty_positions(self, tmp_path):
        out = tmp_path / "broker_positions_snapshot.json"
        payload = snap.write_sidecar([], out)
        on_disk = json.loads(out.read_text(encoding="utf-8"))
        assert on_disk["count"] == 0
        assert on_disk["positions"] == []
        assert payload["count"] == 0

    def test_write_sidecar_atomic_tmp_then_rename(self, tmp_path):
        out = tmp_path / "broker_positions_snapshot.json"
        snap.write_sidecar([], out)
        # After atomic rename, no .tmp leftover.
        assert not out.with_suffix(out.suffix + ".tmp").exists()

    def test_normalize_position_handles_bad_qty(self):
        # Fix 7: normalize lives in core.broker_snapshot now; the script
        # is a thin wrapper around it. This test still pins the shape
        # the hygiene-report enricher depends on.
        from core.broker_snapshot import normalize_position
        normalized = normalize_position({
            "ticker": "AAA", "qty": "not-a-number", "side": "LONG",
        })
        assert normalized["qty"] is None
        assert normalized["ticker"] == "AAA"
        assert normalized["side"] == "long"


# ── No DB mutation ───────────────────────────────────────────────────────────

def test_enrichment_does_not_mutate_db(tmp_path):
    db = _make_db_with_legacy_rows(tmp_path)
    snap_path = tmp_path / "broker.json"
    _write_broker_snapshot(snap_path, ["AAA"])

    before_hash = hashlib.md5(db.read_bytes()).hexdigest()
    before_size = db.stat().st_size

    rpt = h.build_report(
        db, clean_epoch_iso=h.CLEAN_PAPER_EVIDENCE_START,
        broker_snapshot_path=snap_path,
    )
    assert rpt["legacy_quarantine"]["count"] == 3

    after_hash = hashlib.md5(db.read_bytes()).hexdigest()
    after_size = db.stat().st_size
    assert before_hash == after_hash
    assert before_size == after_size
