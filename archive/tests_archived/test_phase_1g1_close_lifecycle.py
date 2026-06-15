"""
tests/unit/test_phase_1g1_close_lifecycle.py — Phase 1G.1 lifecycle
correctness for position_monitor.check_exits().

Audit context (CRK incident, 2026-05-18):
  A SHORT CRK row was marked ``position_closed=1`` immediately after
  ``alpaca.close_position()`` returned an *accepted* order. The order
  later filled partially (broker -234 → -128) and then fully (0). The
  reconciler correctly flagged the intermediate BROKER_ONLY drift and
  tripped the operator-only halt. The book was lying about reality.

Phase 1G.1 fix:
  ``check_exits`` now awaits the close order's terminal state and
  classifies the outcome. A fully-filled close runs the normal
  ``log_exit``. A partial fill marks the decision row
  ``suspect_state='closing_in_progress'`` and keeps it open. A
  rejected / pending close leaves the row open without any close
  bookkeeping.

These tests use stub Alpaca + DecisionLogger doubles — no provider
calls, no DB writes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── Stubs ─────────────────────────────────────────────────────────────────


class _StubAlpaca:
    """In-memory order book. ``close_position`` returns the order we'll
    see, and ``wait_for_fill`` returns the terminal state we choose."""

    def __init__(
        self,
        positions: List[Dict[str, Any]],
        accept_order: Dict[str, Any],
        terminal_order: Optional[Dict[str, Any]],
    ):
        self._positions = positions
        self._accept = accept_order
        self._terminal = terminal_order
        self.closed_calls: List[str] = []
        self.wait_calls: List[str] = []

    def get_positions(self):
        return list(self._positions)

    def close_position(self, ticker):
        self.closed_calls.append(ticker)
        return dict(self._accept)

    def wait_for_fill(self, order_id, timeout_s=8.0, poll_interval_s=1.0):
        self.wait_calls.append(order_id)
        return None if self._terminal is None else dict(self._terminal)


class _StubLogger:
    def __init__(self, open_decisions: List[Dict[str, Any]]):
        self._open = open_decisions
        self.exit_calls: List[Dict[str, Any]] = []
        self.suspect_calls: List[Dict[str, Any]] = []

    def get_open_decisions(self):
        return list(self._open)

    def log_exit(
        self,
        decision_id: str,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        exit_fill_price: Optional[float] = None,
    ) -> None:
        self.exit_calls.append({
            "decision_id":     decision_id,
            "exit_price":      exit_price,
            "pnl":             pnl,
            "pnl_pct":         pnl_pct,
            "exit_fill_price": exit_fill_price,
        })

    def mark_suspect(
        self,
        decision_id: str,
        suspect_state: str,
        suspect_reason: str = "",
    ) -> None:
        self.suspect_calls.append({
            "decision_id":    decision_id,
            "suspect_state":  suspect_state,
            "suspect_reason": suspect_reason,
        })


def _make_monitor(alpaca: _StubAlpaca, logger: _StubLogger):
    from execution.position_monitor import PositionMonitor
    pm = PositionMonitor.__new__(PositionMonitor)
    pm._alpaca = alpaca
    pm._log = logger
    return pm


def _stop_loss_breached_position(qty: float = 234.0) -> Dict[str, Any]:
    """SHORT CRK-style position with current price above the stop."""
    return {
        "ticker":          "CRK",
        "current_price":   16.00,
        "unrealized_pnl":  -315.0,
        "qty":             qty,
    }


def _stop_loss_breached_decision() -> Dict[str, Any]:
    return {
        "id":            "dec1",
        "ticker":        "CRK",
        "strategy":      "SHORT",
        "direction":     "SHORT",
        "entry_price":   14.65,
        "fill_price":    14.65,
        "stop_loss":     15.50,        # broken — current 16.00
        "target_price":  12.50,
        "ts":            "2026-05-07T13:34:58+00:00",
    }


# ── Outcome classifier ────────────────────────────────────────────────────


class TestClassifyCloseOutcome:
    def test_filled_matches_target(self):
        from execution.position_monitor import PositionMonitor
        out = PositionMonitor._classify_close_outcome(
            {"status": "filled", "filled_qty": 234.0}, position_qty=234.0,
        )
        assert out == "filled"

    def test_partial_fill_below_target(self):
        from execution.position_monitor import PositionMonitor
        out = PositionMonitor._classify_close_outcome(
            {"status": "partially_filled", "filled_qty": 106.0},
            position_qty=234.0,
        )
        assert out == "partial"

    def test_accepted_no_fill(self):
        from execution.position_monitor import PositionMonitor
        out = PositionMonitor._classify_close_outcome(
            {"status": "accepted", "filled_qty": 0},
            position_qty=234.0,
        )
        assert out == "pending"

    def test_rejected(self):
        from execution.position_monitor import PositionMonitor
        out = PositionMonitor._classify_close_outcome(
            {"status": "rejected", "filled_qty": 0},
            position_qty=234.0,
        )
        assert out == "pending"

    def test_canceled_with_partial_fill_is_partial(self):
        from execution.position_monitor import PositionMonitor
        out = PositionMonitor._classify_close_outcome(
            {"status": "canceled", "filled_qty": 50.0},
            position_qty=234.0,
        )
        assert out == "partial"

    def test_no_response_pending(self):
        from execution.position_monitor import PositionMonitor
        out = PositionMonitor._classify_close_outcome(None, position_qty=10.0)
        assert out == "pending"


# ── Lifecycle integration ─────────────────────────────────────────────────


class TestCheckExitsLifecycle:
    def test_full_fill_closes_decision(self):
        alpaca = _StubAlpaca(
            positions=[_stop_loss_breached_position()],
            accept_order={"order_id": "o1", "status": "accepted",
                          "filled_qty": 0, "filled_avg_price": None},
            terminal_order={"order_id": "o1", "status": "filled",
                            "filled_qty": 234.0, "filled_avg_price": 16.01},
        )
        logger = _StubLogger([_stop_loss_breached_decision()])
        pm = _make_monitor(alpaca, logger)
        closed = pm.check_exits()

        assert len(closed) == 1
        assert closed[0]["close_outcome"] == "filled"
        # Normal close bookkeeping.
        assert len(logger.exit_calls) == 1
        assert logger.exit_calls[0]["decision_id"] == "dec1"
        assert logger.exit_calls[0]["exit_fill_price"] == 16.01
        # No suspect marker on a clean close.
        assert logger.suspect_calls == []

    def test_partial_fill_marks_suspect_and_keeps_open(self):
        """The bug we are fixing: broker only filled part of the close.
        Decision row must NOT be closed; it must be marked
        ``closing_in_progress`` so the reconciler keeps it visible."""
        alpaca = _StubAlpaca(
            positions=[_stop_loss_breached_position()],
            accept_order={"order_id": "o1", "status": "accepted",
                          "filled_qty": 0, "filled_avg_price": None},
            terminal_order={"order_id": "o1", "status": "partially_filled",
                            "filled_qty": 106.0, "filled_avg_price": 15.99},
        )
        logger = _StubLogger([_stop_loss_breached_decision()])
        pm = _make_monitor(alpaca, logger)
        closed = pm.check_exits()

        assert closed == []  # not fully closed
        assert logger.exit_calls == []  # row stays open
        assert len(logger.suspect_calls) == 1
        s = logger.suspect_calls[0]
        assert s["decision_id"] == "dec1"
        assert s["suspect_state"] == "closing_in_progress"
        assert "filled_qty=106" in s["suspect_reason"]

    def test_pending_status_marks_suspect_and_keeps_open(self):
        """close_position returns an accepted-but-not-filled order; the
        broker never fills before our wait budget runs out."""
        alpaca = _StubAlpaca(
            positions=[_stop_loss_breached_position()],
            accept_order={"order_id": "o1", "status": "accepted",
                          "filled_qty": 0, "filled_avg_price": None},
            terminal_order={"order_id": "o1", "status": "accepted",
                            "filled_qty": 0, "filled_avg_price": None},
        )
        logger = _StubLogger([_stop_loss_breached_decision()])
        pm = _make_monitor(alpaca, logger)
        closed = pm.check_exits()

        assert closed == []
        assert logger.exit_calls == []
        assert len(logger.suspect_calls) == 1
        assert logger.suspect_calls[0]["suspect_state"] == "closing_in_progress"

    def test_close_returns_none_leaves_row_alone(self):
        """Provider error (or live-capital gate refusal) returns None.
        The row must stay open and nothing should be written."""
        alpaca = _StubAlpaca(
            positions=[_stop_loss_breached_position()],
            accept_order={},   # close_position returns this; treated as falsy
            terminal_order=None,
        )

        # Replace close_position to return None directly.
        def _close_none(_ticker):
            return None
        alpaca.close_position = _close_none  # type: ignore[assignment]

        logger = _StubLogger([_stop_loss_breached_decision()])
        pm = _make_monitor(alpaca, logger)
        closed = pm.check_exits()
        assert closed == []
        assert logger.exit_calls == []
        assert logger.suspect_calls == []
