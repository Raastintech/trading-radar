"""
tests/unit/test_phase_1g1_review_halt_state.py — review_halt_state.py
read-only review path + clear-eligibility logic.

These tests cover the pure functions inside the script:
  * _broker_book_match
  * _classify_drift
  * _is_clear_eligible
  * _recommendation

Live broker calls / actual circuit_breaker writes are NOT exercised
here. The clear path is itself end-to-end safe because the script
runs preconditions before invoking CircuitBreakers.clear_halt.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _import_review():
    import importlib
    return importlib.import_module("scripts.review_halt_state")


# ── broker/book match ────────────────────────────────────────────────────


class TestBrokerBookMatch:
    def test_no_snapshot_no_match(self):
        rv = _import_review()
        bb = rv._broker_book_match(None, open_decisions=[])
        assert bb["match"] is False
        assert "no broker snapshot" in bb["reason"]

    def test_empty_snapshot_empty_book_matches(self):
        rv = _import_review()
        bb = rv._broker_book_match({"positions": []}, open_decisions=[])
        assert bb["match"] is True
        assert bb["broker_count"] == 0
        assert bb["book_count"] == 0

    def test_broker_only_ticker_flagged(self):
        rv = _import_review()
        bb = rv._broker_book_match(
            {"positions": [{"ticker": "CRK", "qty": -128.0}]},
            open_decisions=[],
        )
        assert bb["match"] is False
        assert bb["broker_only_tickers"] == ["CRK"]

    def test_book_only_ticker_flagged(self):
        rv = _import_review()
        bb = rv._broker_book_match(
            {"positions": []},
            open_decisions=[{"ticker": "SBAC", "fill_qty": 42.0, "shares": 42}],
        )
        assert bb["match"] is False
        assert bb["book_only_tickers"] == ["SBAC"]

    def test_quantity_match_within_tolerance(self):
        rv = _import_review()
        bb = rv._broker_book_match(
            {"positions": [{"ticker": "SBAC", "qty": 42.0}]},
            open_decisions=[{"ticker": "SBAC", "fill_qty": 42.2, "shares": 42}],
        )
        assert bb["match"] is True
        assert bb["broker_count"] == 1 and bb["book_count"] == 1

    def test_quantity_mismatch_flagged(self):
        rv = _import_review()
        bb = rv._broker_book_match(
            {"positions": [{"ticker": "SBAC", "qty": 50.0}]},
            open_decisions=[{"ticker": "SBAC", "fill_qty": 42.0, "shares": 42}],
        )
        assert bb["match"] is False
        assert len(bb["qty_mismatch"]) == 1


# ── drift classification ─────────────────────────────────────────────────


class TestClassifyDrift:
    def test_recent_drift_with_mismatch_is_active(self):
        rv = _import_review()
        reconc = [
            {"ts": "now", "_recent": True, "ticker": "CRK", "strategy": "*",
             "verdict": "RECONCILE_DRIFT", "agent": "position_reconciler",
             "reason": "BROKER_ONLY: ..."},
        ]
        bb = {"match": False, "reason": "x"}
        d = rv._classify_drift(reconc, bb)
        assert d["active_drift"] is True
        assert d["historical_drift"] is False

    def test_old_drift_with_match_is_historical(self):
        rv = _import_review()
        reconc = [
            {"ts": "now", "_recent": False, "ticker": "CRK", "strategy": "*",
             "verdict": "RECONCILE_DRIFT", "agent": "position_reconciler",
             "reason": "BROKER_ONLY: ..."},
        ]
        bb = {"match": True, "reason": ""}
        d = rv._classify_drift(reconc, bb)
        assert d["active_drift"] is False
        assert d["historical_drift"] is True

    def test_no_drift_rows_neither(self):
        rv = _import_review()
        d = rv._classify_drift([], {"match": True, "reason": ""})
        assert d["active_drift"] is False
        assert d["historical_drift"] is False


# ── clear-eligibility ────────────────────────────────────────────────────


def _cb(halted=True, reason="x"):
    return {"halted": halted, "reason": reason, "id": 1,
            "tripped_at": "x", "cleared_at": None, "cleared_by": None}


def _hygiene(ready_clean=True, ready_all=True):
    return {
        "hygiene": {
            "summary": {
                "ready_to_gate_clean": ready_clean,
                "ready_to_gate_all":   ready_all,
            }
        }
    }


class TestIsClearEligible:
    def test_eligible_when_everything_lines_up(self):
        rv = _import_review()
        bb = {"match": True, "reason": ""}
        drift = {"active_drift": False, "historical_drift": True}
        eligible, blockers = rv._is_clear_eligible(
            _cb(), bb, snap_age_hours=0.1,
            drift=drift, hygiene=_hygiene(), max_snap_age_hours=1.0,
        )
        assert eligible is True
        assert blockers == []

    def test_blocked_when_not_halted(self):
        rv = _import_review()
        bb = {"match": True, "reason": ""}
        drift = {"active_drift": False, "historical_drift": False}
        eligible, blockers = rv._is_clear_eligible(
            _cb(halted=False), bb, snap_age_hours=0.1,
            drift=drift, hygiene=_hygiene(), max_snap_age_hours=1.0,
        )
        assert eligible is False
        assert any("not halted" in b for b in blockers)

    def test_blocked_when_broker_mismatch(self):
        rv = _import_review()
        bb = {"match": False, "reason": "broker_only=1 book_only=0 qty_mismatch=0"}
        drift = {"active_drift": False, "historical_drift": False}
        eligible, blockers = rv._is_clear_eligible(
            _cb(), bb, snap_age_hours=0.1,
            drift=drift, hygiene=_hygiene(), max_snap_age_hours=1.0,
        )
        assert eligible is False
        assert any("do not match" in b for b in blockers)

    def test_blocked_when_snapshot_stale(self):
        rv = _import_review()
        bb = {"match": True, "reason": ""}
        drift = {"active_drift": False, "historical_drift": False}
        eligible, blockers = rv._is_clear_eligible(
            _cb(), bb, snap_age_hours=2.5,
            drift=drift, hygiene=_hygiene(), max_snap_age_hours=1.0,
        )
        assert eligible is False
        assert any("snapshot" in b for b in blockers)

    def test_blocked_when_active_drift(self):
        rv = _import_review()
        bb = {"match": False, "reason": "x"}
        drift = {"active_drift": True, "historical_drift": False, "recent_count": 4}
        eligible, blockers = rv._is_clear_eligible(
            _cb(), bb, snap_age_hours=0.1,
            drift=drift, hygiene=_hygiene(), max_snap_age_hours=1.0,
        )
        assert eligible is False
        assert any("active drift" in b for b in blockers)

    def test_blocked_when_hygiene_not_ready_clean(self):
        rv = _import_review()
        bb = {"match": True, "reason": ""}
        drift = {"active_drift": False, "historical_drift": False}
        eligible, blockers = rv._is_clear_eligible(
            _cb(), bb, snap_age_hours=0.1,
            drift=drift, hygiene=_hygiene(ready_clean=False),
            max_snap_age_hours=1.0,
        )
        assert eligible is False
        assert any("ready_to_gate_clean" in b for b in blockers)

    def test_blocked_when_hygiene_sidecar_missing(self):
        rv = _import_review()
        bb = {"match": True, "reason": ""}
        drift = {"active_drift": False, "historical_drift": False}
        eligible, blockers = rv._is_clear_eligible(
            _cb(), bb, snap_age_hours=0.1,
            drift=drift, hygiene=None, max_snap_age_hours=1.0,
        )
        assert eligible is False
        assert any("hygiene sidecar missing" in b for b in blockers)


class TestRecommendation:
    def test_missing_cb_recommends_bootstrap(self):
        rv = _import_review()
        msg = rv._recommendation(None, eligible=False, blockers=[],
                                 drift={"active_drift": False})
        assert "missing" in msg

    def test_not_halted_no_action(self):
        rv = _import_review()
        msg = rv._recommendation(_cb(halted=False), eligible=False,
                                 blockers=[], drift={"active_drift": False})
        assert "no action" in msg

    def test_eligible_message_includes_clear_command(self):
        rv = _import_review()
        msg = rv._recommendation(_cb(), eligible=True, blockers=[],
                                 drift={"active_drift": False})
        assert "--clear-reviewed" in msg

    def test_blocked_active_drift_message(self):
        rv = _import_review()
        msg = rv._recommendation(_cb(), eligible=False,
                                 blockers=["x"],
                                 drift={"active_drift": True})
        assert "active drift" in msg
