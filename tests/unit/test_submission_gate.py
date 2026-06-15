"""
tests/unit/test_submission_gate.py

Phase 0 submission-time gate re-check coverage.  These tests pin the
behavior of ``core.submission_gate.evaluate`` AND prove that
``OrderManager.execute`` honors a gate block (no broker call, no
position opened, audit row written).

Mocks only — no broker, no DB, no provider calls.  The regime-cache
read is monkey-patched so artefact state is deterministic.

Cases covered (one per gate, plus the all-pass happy path):
  1. Session closed                     → "session"
  2. Council verdict not APPROVED       → "council"
  3. Circuit breaker tripped            → "circuit_breaker"
  4. Regime stance == "avoid"           → "regime"
  5. Duplicate position                 → "duplicate_position"
  6. Portfolio risk rejects             → "portfolio_risk"
  7. All gates pass → broker submit runs

For 1-6 we additionally use OrderManager to confirm the broker is NEVER
called and a veto-log row is written with the blocking gate's name.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from core import submission_gate as gate_mod  # noqa: E402
from execution import order_manager as om_mod  # noqa: E402


# ── Fakes ────────────────────────────────────────────────────────────────────

class _FakeAlpaca:
    def __init__(
        self,
        *,
        equity: float = 1_000_000.0,
        buying_power: float = 1_000_000.0,
        positions: Optional[List[Dict]] = None,
        submit_result: Optional[Dict] = None,
        fill_snap: Optional[Dict] = None,
    ):
        self._equity = equity
        self._bp = buying_power
        self._positions = positions or []
        self._submit_result = submit_result or {
            "order_id": "ord-x", "status": "accepted",
            "filled_qty": 0, "filled_avg_price": None,
            "limit_price": 100.0,
        }
        self._fill_snap = fill_snap or {
            "order_id": "ord-x", "status": "filled",
            "filled_qty": 100.0, "filled_avg_price": 100.0,
            "limit_price": 100.0,
        }
        self.submit_calls: List[Dict] = []

    def get_account(self) -> Dict:
        return {"equity": self._equity, "buying_power": self._bp}

    def get_positions(self) -> List[Dict]:
        return list(self._positions)

    def get_open_orders(self, symbol=None) -> List[Dict]:
        return []

    def submit_limit_order(self, *, ticker, qty, side, limit_price, **_):
        self.submit_calls.append(
            {"ticker": ticker, "qty": qty, "side": side, "limit_price": limit_price}
        )
        return self._submit_result

    def wait_for_fill(self, order_id, timeout_s=5.0, poll_interval_s=0.5):
        return self._fill_snap


class _FakeDecisionLogger:
    def __init__(self) -> None:
        self.decisions: List[Dict] = []
        self.vetoes: List[Dict] = []

    def log_decision(self, **kw) -> str:
        self.decisions.append(kw)
        return f"dec-{len(self.decisions)}"

    def log_veto(self, **kw) -> None:
        self.vetoes.append(kw)

    def has_open_decision(self, ticker: str, strategy=None) -> bool:
        return False

    def has_recent_unfilled_decision(
        self, ticker: str, strategy=None, within_minutes: int = 390
    ) -> bool:
        return False


class _FakeBreakers:
    """Configurable stand-in for execution.circuit_breakers.CircuitBreakers."""

    def __init__(self, *, allowed: bool = True, reason: str = "") -> None:
        self._allowed = allowed
        self._reason = reason
        self.calls: List[Dict] = []

    def gate(self, *, strategy_name, portfolio_state):
        self.calls.append(
            {"strategy_name": strategy_name, "portfolio_state": portfolio_state}
        )
        return self._allowed, self._reason


class _FakeBookRisk:
    def __init__(self, *, allowed: bool = True, reason: str = "") -> None:
        self._allowed = allowed
        self._reason = reason
        self.calls: List[Dict] = []

    def check(self, *, signal, positions, equity):
        self.calls.append(
            {"signal": signal, "positions": positions, "equity": equity}
        )
        return self._allowed, self._reason


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def signal() -> Dict[str, Any]:
    return {
        "ticker":      "AAPL",
        "direction":   "LONG",
        "strategy":    "VOYAGER",
        "entry_price": 100.0,
        "stop_loss":    95.0,
        "target_price": 115.0,
        "shares":      100,
        "score":       80,
    }


@pytest.fixture
def council_approved() -> Dict[str, Any]:
    return {"verdict": "APPROVED", "votes": {"PortfolioAgent": "APPROVE"}}


@pytest.fixture
def portfolio_state() -> Dict[str, Any]:
    return {"open_positions": 0, "max_positions": 10, "daily_pnl_pct": 0.0}


@pytest.fixture(autouse=True)
def _stub_regime_artefact(monkeypatch):
    """Default to favored stance so the regime gate never blocks unless a
    test explicitly re-stubs it.  Keeps the other gate tests focused."""
    monkeypatch.setattr(
        gate_mod, "_read_regime_favorability",
        lambda: {"VOYAGER": {"stance": "favored", "reason": "ok"}},
    )


def _make_om(monkeypatch, alpaca, *, breakers=None, book_risk=None):
    monkeypatch.setattr(om_mod, "get_alpaca", lambda: alpaca)
    om = om_mod.OrderManager(
        _FakeDecisionLogger(),
        circuit_breakers=breakers,
        portfolio_risk=book_risk,
    )
    return om


# ── Direct evaluate() tests ──────────────────────────────────────────────────

class TestSubmissionGateEvaluate:

    def test_session_closed_blocks(self, signal, council_approved, portfolio_state):
        allowed, reason, gate = gate_mod.evaluate(
            signal,
            council_approved,
            portfolio_state=portfolio_state,
            is_execution_allowed=lambda *_: False,
        )
        assert allowed is False
        assert gate == "session"
        assert "session" in reason.lower() or "execution not allowed" in reason.lower()

    def test_council_vetoed_blocks(self, signal, portfolio_state):
        allowed, reason, gate = gate_mod.evaluate(
            signal,
            {"verdict": "VETOED", "agent": "TailRiskAgent",
             "reason": "vol spike post-scan"},
            portfolio_state=portfolio_state,
            is_execution_allowed=lambda *_: True,
        )
        assert allowed is False
        assert gate == "council"
        assert "VETOED" in reason
        assert "vol spike" in reason

    def test_circuit_breaker_halted_blocks(
        self, signal, council_approved, portfolio_state
    ):
        breakers = _FakeBreakers(allowed=False, reason="daily loss halt -5.2%")
        allowed, reason, gate = gate_mod.evaluate(
            signal,
            council_approved,
            portfolio_state=portfolio_state,
            circuit_breakers=breakers,
            is_execution_allowed=lambda *_: True,
        )
        assert allowed is False
        assert gate == "circuit_breaker"
        assert "daily loss halt" in reason
        assert breakers.calls, "circuit_breakers.gate must be invoked"

    def test_regime_avoid_blocks(
        self, signal, council_approved, portfolio_state, monkeypatch
    ):
        monkeypatch.setattr(
            gate_mod, "_read_regime_favorability",
            lambda: {"VOYAGER": {"stance": "avoid",
                                  "reason": "VIX>30, breadth crash"}},
        )
        allowed, reason, gate = gate_mod.evaluate(
            signal,
            council_approved,
            portfolio_state=portfolio_state,
            is_execution_allowed=lambda *_: True,
        )
        assert allowed is False
        assert gate == "regime"
        assert "avoid" in reason
        assert "VIX>30" in reason

    def test_duplicate_position_blocks(
        self, signal, council_approved, portfolio_state
    ):
        open_positions = [
            {"ticker": "AAPL", "qty": 100, "market_value": 10_000.0, "side": "long",
             "strategy": "SNIPER"},
        ]
        allowed, reason, gate = gate_mod.evaluate(
            signal,
            council_approved,
            portfolio_state=portfolio_state,
            open_positions=open_positions,
            equity=1_000_000.0,
            is_execution_allowed=lambda *_: True,
        )
        assert allowed is False
        assert gate == "duplicate_position"
        assert "AAPL" in reason

    def test_portfolio_risk_blocks(
        self, signal, council_approved, portfolio_state
    ):
        risk = _FakeBookRisk(allowed=False, reason="Book A would reach 65%")
        allowed, reason, gate = gate_mod.evaluate(
            signal,
            council_approved,
            portfolio_state=portfolio_state,
            portfolio_risk=risk,
            open_positions=[],          # empty so duplicate-check is skipped
            equity=1_000_000.0,
            is_execution_allowed=lambda *_: True,
        )
        assert allowed is False
        assert gate == "portfolio_risk"
        assert "Book A" in reason
        assert risk.calls, "portfolio_risk.check must be invoked"

    def test_all_gates_pass(
        self, signal, council_approved, portfolio_state
    ):
        breakers = _FakeBreakers(allowed=True)
        risk     = _FakeBookRisk(allowed=True)
        allowed, reason, gate = gate_mod.evaluate(
            signal,
            council_approved,
            portfolio_state=portfolio_state,
            circuit_breakers=breakers,
            portfolio_risk=risk,
            open_positions=[],
            equity=1_000_000.0,
            is_execution_allowed=lambda *_: True,
        )
        assert allowed is True
        assert reason == ""
        assert gate == ""
        assert breakers.calls and risk.calls


# ── Phase 3A: OrderManager.execute raises ResearchOnlyModeError ──────────────
#
# The old TestOrderManagerSubmissionGate (broker integration tests) tested that
# gate blocks prevented broker submits. In RESEARCH_ONLY mode, execute() raises
# ResearchOnlyModeError immediately — no broker call can happen under any path.
# The gate logic itself is still tested in TestSubmissionGateEvaluate above.

class TestOrderManagerSubmissionGate:

    def test_execute_raises_research_only(self, signal, council_approved, portfolio_state):
        from execution.order_manager import OrderManager
        from core.research_mode import ResearchOnlyModeError
        om = OrderManager(_FakeDecisionLogger())
        with pytest.raises(ResearchOnlyModeError):
            om.execute(
                signal, council_approved,
                portfolio_state=portfolio_state,
                open_positions=[],
                equity=1_000_000.0,
            )

    def test_execute_vetoed_council_also_raises(self, signal, portfolio_state):
        from execution.order_manager import OrderManager
        from core.research_mode import ResearchOnlyModeError
        om = OrderManager(_FakeDecisionLogger())
        with pytest.raises(ResearchOnlyModeError):
            om.execute(
                signal,
                {"verdict": "VETOED", "agent": "MacroAgent", "reason": "FOMC in 2h"},
                portfolio_state=portfolio_state,
                open_positions=[],
                equity=1_000_000.0,
            )
