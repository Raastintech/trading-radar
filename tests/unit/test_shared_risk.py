"""
tests/unit/test_shared_risk.py

Unit tests for strategies/shared/risk.py.
No external dependencies — pure math.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from strategies.shared.risk import calc_atr


def _make_bars(n: int, base_close: float = 100.0, spread: float = 2.0):
    """Synthetic OHLCV bars: high = close + spread, low = close - spread."""
    return [
        {
            "open":   base_close,
            "high":   base_close + spread,
            "low":    base_close - spread,
            "close":  base_close,
            "volume": 1_000_000,
        }
        for _ in range(n)
    ]


class TestCalcAtr:
    def test_returns_float(self):
        bars = _make_bars(20)
        result = calc_atr(bars)
        assert isinstance(result, float)

    def test_correct_value_flat_bars(self):
        # With flat bars (spread = 2.0), each TR = 2*spread = 4.0 (h-l = 4, |h-pc|=2, |l-pc|=2)
        # high = 102, low = 98, close = 100, prev close = 100
        # TR = max(4, 2, 2) = 4.0
        bars = _make_bars(15, spread=2.0)
        result = calc_atr(bars)
        assert abs(result - 4.0) < 0.001, f"Expected 4.0, got {result}"

    def test_empty_bars_returns_zero(self):
        assert calc_atr([]) == 0.0

    def test_single_bar_returns_zero(self):
        assert calc_atr(_make_bars(1)) == 0.0

    def test_two_bars_returns_tr(self):
        bars = _make_bars(2, spread=3.0)
        result = calc_atr(bars)
        # TR = max(high-low=6, |high-prev_close|=3, |low-prev_close|=3) = 6
        assert abs(result - 6.0) < 0.001, f"Expected 6.0, got {result}"

    def test_period_respected(self):
        bars_big_spread   = _make_bars(5, spread=10.0)
        bars_small_spread = _make_bars(20, spread=1.0)
        all_bars = bars_big_spread + bars_small_spread
        # default period=14 → uses last 14 bars which are all small-spread
        result = calc_atr(all_bars, period=14)
        # TR of small-spread bars = max(2, 1, 1) = 2
        assert abs(result - 2.0) < 0.001, f"Expected 2.0, got {result}"

    def test_custom_period(self):
        bars = _make_bars(30, spread=5.0)
        result_14 = calc_atr(bars, period=14)
        result_5  = calc_atr(bars, period=5)
        # Both should equal same value since spread is constant
        assert abs(result_14 - result_5) < 0.001


class TestCircuitBreakers:
    """Unit tests for execution/circuit_breakers.py.

    Phase 0 hardening note: state is now persisted to a SQLite file.
    Each test gets its own tempdir so production trading.db is never
    touched and tests don't see each other's halt state."""

    def setup_method(self):
        import tempfile
        from execution.circuit_breakers import CircuitBreakers
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cb = CircuitBreakers(db_path=f"{self._tmpdir.name}/cb.db")

    def teardown_method(self):
        self._tmpdir.cleanup()

    def _state(self, daily_pnl_pct: float = 0.0):
        return {"daily_pnl_pct": daily_pnl_pct, "equity": 100_000}

    def test_no_halt_when_pnl_ok(self):
        halted, _ = self.cb.check_daily_loss(self._state(-0.02))
        assert halted is False

    def test_halt_triggered_at_threshold(self):
        halted, reason = self.cb.check_daily_loss(self._state(-0.05))
        assert halted is True
        assert "circuit breaker" in reason.lower()

    def test_halt_triggered_below_threshold(self):
        halted, _ = self.cb.check_daily_loss(self._state(-0.08))
        assert halted is True

    def test_halt_does_not_auto_clear_on_recovery(self):
        """Phase 0: halt stays tripped across an intraday recovery.
        Operator must call ``clear_halt`` to resume.  Replaces the
        prior auto-clear test which encoded the rally-trap behavior."""
        self.cb.check_daily_loss(self._state(-0.06))  # trip
        assert self.cb.is_globally_halted()[0] is True
        # Recovery: P&L back well above the threshold.
        halted, _ = self.cb.check_daily_loss(self._state(-0.01))
        assert halted is True, "halt must persist through recovery"
        assert self.cb.is_globally_halted()[0] is True
        # Only manual clear lifts it.
        self.cb.clear_halt(cleared_by="test")
        assert self.cb.is_globally_halted()[0] is False

    def test_halt_persists_across_instances(self):
        """Phase 0: a halt written by one instance is loaded by the
        next.  Simulates a process restart."""
        self.cb.force_halt("operator halt")
        from execution.circuit_breakers import CircuitBreakers
        cb2 = CircuitBreakers(db_path=f"{self._tmpdir.name}/cb.db")
        halted, reason = cb2.is_globally_halted()
        assert halted is True
        assert "operator halt" in reason

    def test_force_halt(self):
        self.cb.force_halt("test halt")
        assert self.cb.is_globally_halted()[0] is True

    def test_clear_halt(self):
        self.cb.force_halt("test")
        self.cb.clear_halt()
        assert self.cb.is_globally_halted()[0] is False

    def test_kill_strategy(self):
        self.cb.kill_strategy("SNIPER")
        assert not self.cb.is_strategy_active("SNIPER")
        assert self.cb.is_strategy_active("VOYAGER")

    def test_revive_strategy(self):
        self.cb.kill_strategy("SNIPER")
        self.cb.revive_strategy("SNIPER")
        assert self.cb.is_strategy_active("SNIPER")

    def test_gate_blocked_by_circuit_breaker(self):
        allowed, reason = self.cb.gate("SNIPER", self._state(-0.06))
        assert allowed is False
        assert reason != ""

    def test_gate_blocked_by_kill_switch(self):
        self.cb.kill_strategy("VOYAGER")
        allowed, reason = self.cb.gate("VOYAGER", self._state(0.01))
        assert allowed is False
        assert "killed" in reason.lower()

    def test_gate_allowed_when_clear(self):
        allowed, reason = self.cb.gate("SNIPER", self._state(0.0))
        assert allowed is True
        assert reason == ""


class TestPortfolioRisk:
    """
    Phase 3A: PortfolioRisk.check() is permanently disabled.
    Tests verify: constants are importable, check() raises ResearchOnlyModeError.
    Original behavior tests live in archive/tests_archived/test_shared_risk_risk_check.py
    (to be archived if ever separated; currently these were inline).
    """

    def setup_method(self):
        import pytest  # noqa: F401 — ensure pytest available in setup
        from execution.portfolio_risk import PortfolioRisk
        from core.research_mode import ResearchOnlyModeError
        self.risk = PortfolioRisk()
        self.ResearchOnlyModeError = ResearchOnlyModeError
        self.equity = 100_000.0

    def _signal(self, ticker="AAPL", direction="LONG", strategy="SNIPER",
                entry=100.0, shares=10):
        return {
            "ticker": ticker, "direction": direction,
            "strategy": strategy, "entry_price": entry, "shares": shares,
        }

    def test_check_raises_research_only(self):
        import pytest
        with pytest.raises(self.ResearchOnlyModeError):
            self.risk.check(self._signal(), [], self.equity)

    def test_constants_importable(self):
        from execution.portfolio_risk import (
            MAX_SINGLE_NAME_PCT,
            MAX_GROSS_SHORT_PCT,
            MAX_POSITIONS_TOTAL,
            BOOK_A, BOOK_B, BOOK_C,
        )
        assert MAX_SINGLE_NAME_PCT == 0.05
        assert MAX_GROSS_SHORT_PCT == 0.30
        assert MAX_POSITIONS_TOTAL == 10
        assert "SNIPER" in BOOK_A


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
