"""
tests/unit/test_phase_1g3_halt_dry_run_no_mutation.py — Phase 1G.3 T8

Explicit guarantee: `--dry-run-and-print-decision` never mutates, even when
`--clear-reviewed --reason "..."` is passed alongside it. Also confirms the
normal --clear-reviewed path DOES call perform_clear when eligible, and that a
missing reason blocks the clear.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

rv = importlib.import_module("scripts.review_halt_state")


def _eligible_report():
    return {
        "halted": True,
        "clear_eligible": True,
        "clear_blockers": [],
        "drift": {"active_drift": False, "historical_drift": True},
    }


def test_dry_run_blocks_mutation_even_with_clear_reviewed(monkeypatch, tmp_path):
    db = tmp_path / "trading.db"
    db.write_text("")  # existence check only
    monkeypatch.setattr(rv.cfg, "DB_PATH", str(db))
    monkeypatch.setattr(rv, "build_review_report", lambda **kw: _eligible_report())
    monkeypatch.setattr(rv, "render_text", lambda r: "REPORT")

    calls = []
    monkeypatch.setattr(rv, "perform_clear", lambda reason, db_path: calls.append(reason) or True)

    rc = rv.main([
        "--clear-reviewed", "--dry-run-and-print-decision",
        "--reason", "operator test",
    ])
    assert rc == 0
    assert calls == []  # perform_clear NEVER called in dry-run


def test_clear_reviewed_requires_reason(monkeypatch, tmp_path):
    db = tmp_path / "trading.db"
    db.write_text("")
    monkeypatch.setattr(rv.cfg, "DB_PATH", str(db))
    monkeypatch.setattr(rv, "build_review_report", lambda **kw: _eligible_report())
    monkeypatch.setattr(rv, "render_text", lambda r: "REPORT")
    calls = []
    monkeypatch.setattr(rv, "perform_clear", lambda reason, db_path: calls.append(reason) or True)

    rc = rv.main(["--clear-reviewed"])  # no reason
    assert rc == 2
    assert calls == []


def test_clear_reviewed_calls_perform_clear_when_eligible(monkeypatch, tmp_path):
    db = tmp_path / "trading.db"
    db.write_text("")
    monkeypatch.setattr(rv.cfg, "DB_PATH", str(db))
    monkeypatch.setattr(rv, "build_review_report", lambda **kw: _eligible_report())
    monkeypatch.setattr(rv, "render_text", lambda r: "REPORT")
    # After clear, state reads not-halted.
    monkeypatch.setattr(rv, "_load_circuit_breaker_state", lambda db_path: {"halted": False})
    calls = []
    monkeypatch.setattr(rv, "perform_clear", lambda reason, db_path: calls.append(reason) or True)

    rc = rv.main(["--clear-reviewed", "--reason", "drift resolved"])
    assert rc == 0
    assert len(calls) == 1
    assert "operator-reviewed: drift resolved" == calls[0]
