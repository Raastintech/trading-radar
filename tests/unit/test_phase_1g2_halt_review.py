"""
tests/unit/test_phase_1g2_halt_review.py — Phase 1G.2 T6

Tests for scripts/review_halt_state usability changes:
  - Recommendation embeds the exact ``--clear-reviewed`` command when
    the halt is clear-eligible.
  - ``--dry-run-and-print-decision`` overrides ``--clear-reviewed`` so
    no mutation happens even when both are passed.
  - ``--clear-reviewed`` still requires ``--reason``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

# review_halt_state is a script not a package member; import by path.
import importlib.util


def _load_review_halt_state():
    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "_rhs", str(repo / "scripts" / "review_halt_state.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_recommendation_includes_command_when_eligible():
    rhs = _load_review_halt_state()
    cb = {"id": 1, "halted": True, "reason": "drift", "tripped_at": None,
          "cleared_at": None, "cleared_by": None}
    drift = {"active_drift": False, "historical_drift": True,
             "recent_count": 0, "older_count": 5,
             "first_recent_ts": None, "last_recent_ts": None,
             "last_drift_ts": "2026-05-18T13:34:48"}
    rec = rhs._recommendation(cb, eligible=True, blockers=[], drift=drift,
                              max_snap_age_hours=1.0)
    assert "--clear-reviewed" in rec
    assert "--reason" in rec
    assert "operator-reviewed:" in rec


def test_recommendation_when_active_drift():
    rhs = _load_review_halt_state()
    cb = {"id": 1, "halted": True, "reason": "drift", "tripped_at": None,
          "cleared_at": None, "cleared_by": None}
    drift = {"active_drift": True, "historical_drift": False,
             "recent_count": 4, "older_count": 0,
             "first_recent_ts": "2026-05-18T13:30:00",
             "last_recent_ts": "2026-05-18T13:34:48",
             "last_drift_ts": "2026-05-18T13:34:48"}
    rec = rhs._recommendation(cb, eligible=False, blockers=["active drift detected"],
                              drift=drift, max_snap_age_hours=1.0)
    assert "investigate the active drift" in rec


def test_recommendation_when_not_halted():
    rhs = _load_review_halt_state()
    cb = {"id": 1, "halted": False, "reason": "", "tripped_at": None,
          "cleared_at": "2026-05-18T22:31:17", "cleared_by": "operator"}
    drift = {"active_drift": False, "historical_drift": False,
             "recent_count": 0, "older_count": 0,
             "first_recent_ts": None, "last_recent_ts": None,
             "last_drift_ts": None}
    rec = rhs._recommendation(cb, eligible=False, blockers=[], drift=drift)
    assert "not halted" in rec


def test_dry_run_flag_blocks_mutation_even_with_clear_reviewed(monkeypatch, tmp_path):
    rhs = _load_review_halt_state()
    # Build a fake DB path that exists (so the script gets past the
    # initial cfg.DB_PATH existence check) but is otherwise empty.
    fake_db = tmp_path / "trading.db"
    fake_db.touch()
    monkeypatch.setattr(rhs.cfg, "DB_PATH", str(fake_db), raising=False)

    # Patch build_review_report to return a clear-eligible report so the
    # write path would otherwise run.
    fake_report = {
        "generated_at": "2026-05-23T01:00:00+00:00",
        "db_path": str(fake_db),
        "halted": True, "halt_reason": "drift",
        "tripped_at": None, "cleared_at": None, "cleared_by": None,
        "broker_book_match": {
            "match": True, "reason": "", "broker_count": 0, "book_count": 0,
            "broker_only_tickers": [], "book_only_tickers": [], "qty_mismatch": [],
        },
        "broker_snapshot_age_hours": 0.05,
        "active_drift": False, "historical_drift": True,
        "drift_summary": {"recent_count": 0, "older_count": 0,
                          "last_drift_ts": None, "active_drift": False,
                          "historical_drift": True,
                          "first_recent_ts": None, "last_recent_ts": None},
        "hygiene_summary": {"ready_to_gate_clean": True, "ready_to_gate_all": False,
                            "errors_full": 0, "errors_clean": 0},
        "clear_eligible": True,
        "clear_blockers": [],
        "recommendation": "eligible to clear — run: ...",
        "limits": {"max_snapshot_age_hours": 1.0,
                   "reconciler_lookback_min": 30},
    }
    monkeypatch.setattr(rhs, "build_review_report", lambda **kw: fake_report)

    # Sentinel: perform_clear must never be called when the explicit
    # dry-run flag is set.
    called = {"clear": False}

    def _boom(*a, **kw):
        called["clear"] = True
        return True

    monkeypatch.setattr(rhs, "perform_clear", _boom)

    rc = rhs.main([
        "--clear-reviewed",
        "--reason", "test",
        "--dry-run-and-print-decision",
    ])
    assert rc == 0
    assert called["clear"] is False, "perform_clear must not run under dry-run"


def test_clear_reviewed_requires_reason(monkeypatch, tmp_path):
    rhs = _load_review_halt_state()
    fake_db = tmp_path / "trading.db"
    fake_db.touch()
    monkeypatch.setattr(rhs.cfg, "DB_PATH", str(fake_db), raising=False)
    monkeypatch.setattr(rhs, "build_review_report", lambda **kw: {
        "generated_at": "x", "db_path": str(fake_db),
        "halted": True, "halt_reason": "", "tripped_at": None,
        "cleared_at": None, "cleared_by": None,
        "broker_book_match": {
            "match": True, "reason": "", "broker_count": 0, "book_count": 0,
            "broker_only_tickers": [], "book_only_tickers": [], "qty_mismatch": [],
        },
        "broker_snapshot_age_hours": 0.05,
        "active_drift": False, "historical_drift": False,
        "drift_summary": {"recent_count": 0, "older_count": 0,
                          "last_drift_ts": None, "active_drift": False,
                          "historical_drift": False,
                          "first_recent_ts": None, "last_recent_ts": None},
        "hygiene_summary": None,
        "clear_eligible": True, "clear_blockers": [],
        "recommendation": "...",
        "limits": {"max_snapshot_age_hours": 1.0, "reconciler_lookback_min": 30},
    })
    monkeypatch.setattr(rhs, "perform_clear", lambda *a, **kw: True)
    # No --reason → exit code 2
    rc = rhs.main(["--clear-reviewed"])
    assert rc == 2
