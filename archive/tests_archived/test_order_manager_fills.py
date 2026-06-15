"""
tests/unit/test_order_manager_fills.py

Phase 0 fill-correctness regression tests for execution/order_manager.py.

Pins these contracts on OrderManager.execute():
  1. FULL FILL          — position_opened=True, fill_qty == submitted, slippage telemetry populated.
  2. PARTIAL FILL       — position_opened=True, fill_qty < submitted, partial_fill_qty == remainder.
  3. NO FILL (canceled) — position_opened=False, return value is None, decision row not surfaced.
  4. NO FILL (timeout)  — broker still 'new' / 'accepted' with filled_qty=0 → treated as unfilled.
  5. MULTI-POLL PARTIAL — wait_for_fill returns the eventual partially_filled snap; same as case 2.

The tests stub AlpacaClient and DecisionLogger so no broker call, no DB write, and
no submission-gate dependency runs.  This keeps the test hermetic and safe for CI.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution import order_manager as om_mod  # noqa: E402


# ── Fakes ────────────────────────────────────────────────────────────────────

class _FakeAlpaca:
    """Minimal AlpacaClient stand-in.  Each test installs the fill snapshot it
    wants returned by wait_for_fill()."""

    def __init__(
        self,
        *,
        # 1M equity keeps MAX_POSITION_PCT (0.02) × 2 × equity = 40k > 10k trade
        # value (100 shares × $100), so the OrderManager's position-cap clip
        # does not silently reduce `shares` and confound fill arithmetic.
        equity: float = 1_000_000.0,
        buying_power: float = 1_000_000.0,
        positions: Optional[List[Dict]] = None,
        open_orders: Optional[List[Dict]] = None,
        submit_result: Optional[Dict] = None,
        fill_snap: Optional[Dict] = None,
    ):
        self._equity = equity
        self._bp = buying_power
        self._positions = positions or []
        self._open_orders = open_orders or []
        self._submit_result = submit_result
        self._fill_snap = fill_snap
        self.submit_calls: List[Dict] = []
        self.wait_calls: List[Dict] = []

    # methods OrderManager calls
    def get_account(self) -> Dict:
        return {"equity": self._equity, "buying_power": self._bp}

    def get_positions(self) -> List[Dict]:
        return list(self._positions)

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        if symbol is None:
            return list(self._open_orders)
        return [o for o in self._open_orders
                if (o.get("symbol") or "").upper() == symbol.upper()]

    def submit_limit_order(self, *, ticker: str, qty: float, side: str,
                            limit_price: float, **_) -> Optional[Dict]:
        self.submit_calls.append({
            "ticker": ticker, "qty": qty, "side": side, "limit_price": limit_price
        })
        return self._submit_result

    def wait_for_fill(self, order_id: str, timeout_s: float = 5.0,
                       poll_interval_s: float = 0.5) -> Optional[Dict]:
        self.wait_calls.append({"order_id": order_id, "timeout_s": timeout_s})
        return self._fill_snap


class _FakeDecisionLogger:
    """Records log_decision / log_veto calls so the test can assert on them."""

    def __init__(
        self,
        open_tickers: Optional[List[str]] = None,
        recent_unfilled: Optional[List[str]] = None,
    ):
        self.decisions: List[Dict] = []
        self.vetoes: List[Dict] = []
        self._open: set = {t.upper() for t in (open_tickers or [])}
        self._recent_unfilled: set = {t.upper() for t in (recent_unfilled or [])}

    def log_decision(self, **kw) -> str:
        self.decisions.append(kw)
        return f"dec-{len(self.decisions)}"

    def log_veto(self, **kw) -> None:
        self.vetoes.append(kw)

    def has_open_decision(self, ticker: str, strategy: Optional[str] = None) -> bool:
        return ticker.upper() in self._open

    def has_recent_unfilled_decision(
        self, ticker: str, strategy: Optional[str] = None, within_minutes: int = 390
    ) -> bool:
        return ticker.upper() in self._recent_unfilled


@pytest.fixture
def signal() -> Dict:
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
def council_approved() -> Dict:
    return {"verdict": "APPROVED", "votes": {"PortfolioAgent": "APPROVE"}}


@pytest.fixture(autouse=True)
def _bypass_submission_gate(monkeypatch):
    """The Phase 0 submission gate has its own coverage; here we want to focus
    on fill-correctness, so force the gate to allow."""
    monkeypatch.setattr(
        om_mod, "_gate_evaluate",
        lambda *_a, **_kw: (True, None, None),
    )


def _make_order_manager(
    monkeypatch,
    fake_alpaca: _FakeAlpaca,
    *,
    open_book_tickers: Optional[List[str]] = None,
    recent_unfilled_tickers: Optional[List[str]] = None,
) -> om_mod.OrderManager:
    """Build OrderManager with get_alpaca() stubbed to return our fake.
    open_book_tickers lets a test simulate the filled-position dedup;
    recent_unfilled_tickers simulates the resting-order book backstop."""
    monkeypatch.setattr(om_mod, "get_alpaca", lambda: fake_alpaca)
    return om_mod.OrderManager(
        _FakeDecisionLogger(open_book_tickers, recent_unfilled_tickers)
    )


# ── Tests ────────────────────────────────────────────────────────────────────

class TestOrderManagerFills:

    def test_full_fill_records_position_and_zero_slippage(
        self, monkeypatch, signal, council_approved
    ):
        """Broker fills 100/100 at the limit price exactly."""
        submit = {"order_id": "ord-1", "status": "accepted",
                  "filled_qty": 0, "filled_avg_price": None,
                  "limit_price": 100.0}
        fill = {"order_id": "ord-1", "status": "filled",
                "filled_qty": 100.0, "filled_avg_price": 100.0,
                "limit_price": 100.0}
        alp = _FakeAlpaca(submit_result=submit, fill_snap=fill)
        om = _make_order_manager(monkeypatch, alp)
        om._log = _FakeDecisionLogger()

        result = om.execute(signal, council_approved)

        assert result is not None
        assert result["fill_qty"] == 100.0
        assert result["filled_avg_price"] == 100.0
        assert result["partial_fill_qty"] == 0.0
        assert result["signal_price"] == 100.0
        assert result["submitted_limit_price"] == 100.0
        assert result["slippage_bps"] == 0.0
        assert result["slippage_pct"] == 0.0
        assert result["fill_status"] == "filled"
        # Decision row must be marked as opened.
        assert om._log.decisions, "expected exactly one decision logged"
        d = om._log.decisions[0]
        assert d["position_opened"] is True
        assert d["fill_qty"] == 100.0
        assert d["fill_price"] == 100.0
        assert d["fill_status"] == "filled"

    def test_full_fill_with_adverse_slippage(
        self, monkeypatch, signal, council_approved
    ):
        """LONG fill above the limit — positive bps means adverse."""
        submit = {"order_id": "ord-2", "status": "accepted",
                  "filled_qty": 0, "filled_avg_price": None,
                  "limit_price": 100.0}
        fill = {"order_id": "ord-2", "status": "filled",
                "filled_qty": 100.0, "filled_avg_price": 100.10,
                "limit_price": 100.0}
        alp = _FakeAlpaca(submit_result=submit, fill_snap=fill)
        om = _make_order_manager(monkeypatch, alp)
        om._log = _FakeDecisionLogger()

        result = om.execute(signal, council_approved)

        # 0.10 / 100.0 * 1e4 = 10 bps = 0.10%
        assert result is not None
        assert result["slippage_bps"] == 10.0
        assert result["slippage_pct"] == 0.10
        assert om._log.decisions[0]["slippage_bps"] == 10.0

    def test_partial_fill_records_actual_qty_and_remainder(
        self, monkeypatch, signal, council_approved
    ):
        """40 of 100 shares filled.  partial_fill_qty == 60."""
        submit = {"order_id": "ord-3", "status": "accepted",
                  "filled_qty": 0, "filled_avg_price": None,
                  "limit_price": 100.0}
        fill = {"order_id": "ord-3", "status": "partially_filled",
                "filled_qty": 40.0, "filled_avg_price": 100.05,
                "limit_price": 100.0}
        alp = _FakeAlpaca(submit_result=submit, fill_snap=fill)
        om = _make_order_manager(monkeypatch, alp)
        om._log = _FakeDecisionLogger()

        result = om.execute(signal, council_approved)

        assert result is not None
        assert result["fill_qty"] == 40.0
        assert result["partial_fill_qty"] == 60.0
        assert result["filled_avg_price"] == 100.05
        assert result["fill_status"] == "partially_filled"
        # Decision row reflects partial: position_opened=True (some
        # shares are held), but fill_qty captures actual exposure only.
        d = om._log.decisions[0]
        assert d["position_opened"] is True
        assert d["fill_qty"] == 40.0
        assert d["fill_status"] == "partially_filled"
        # Intended shares (the column for risk-sizing intent) is unchanged;
        # actual exposure lives in fill_qty.
        assert d["shares"] == 100

    def test_canceled_no_fill_returns_none_and_does_not_open_position(
        self, monkeypatch, signal, council_approved
    ):
        """Order canceled before any fill — must NOT create a position."""
        submit = {"order_id": "ord-4", "status": "accepted",
                  "filled_qty": 0, "filled_avg_price": None,
                  "limit_price": 100.0}
        fill = {"order_id": "ord-4", "status": "canceled",
                "filled_qty": 0.0, "filled_avg_price": None,
                "limit_price": 100.0}
        alp = _FakeAlpaca(submit_result=submit, fill_snap=fill)
        om = _make_order_manager(monkeypatch, alp)
        om._log = _FakeDecisionLogger()

        result = om.execute(signal, council_approved)

        assert result is None, "no-fill must not return a position dict"
        # Audit row exists but is NOT surfaced as an open position.
        assert om._log.decisions, "expected an audit decision row even on no-fill"
        d = om._log.decisions[0]
        assert d["position_opened"] is False
        assert d["fill_qty"] == 0.0
        assert d["fill_price"] is None
        assert d["fill_status"] == "canceled"
        assert d["slippage_bps"] is None

    def test_timeout_with_zero_fill_treated_as_unfilled(
        self, monkeypatch, signal, council_approved
    ):
        """wait_for_fill timed out while order is still 'new' / 'accepted' with
        filled_qty=0 — must NOT open a position."""
        submit = {"order_id": "ord-5", "status": "accepted",
                  "filled_qty": 0, "filled_avg_price": None,
                  "limit_price": 100.0}
        # wait_for_fill returns the still-working snapshot on timeout.
        fill = {"order_id": "ord-5", "status": "new",
                "filled_qty": 0.0, "filled_avg_price": None,
                "limit_price": 100.0}
        alp = _FakeAlpaca(submit_result=submit, fill_snap=fill)
        om = _make_order_manager(monkeypatch, alp)
        om._log = _FakeDecisionLogger()

        result = om.execute(signal, council_approved)

        assert result is None
        d = om._log.decisions[0]
        assert d["position_opened"] is False
        assert d["fill_status"] == "new"

    def test_none_filled_qty_treated_as_unfilled(
        self, monkeypatch, signal, council_approved
    ):
        """Some broker shapes return filled_qty=None instead of 0 — same outcome."""
        submit = {"order_id": "ord-6", "status": "accepted",
                  "filled_qty": None, "filled_avg_price": None,
                  "limit_price": 100.0}
        fill = {"order_id": "ord-6", "status": "expired",
                "filled_qty": None, "filled_avg_price": None,
                "limit_price": 100.0}
        alp = _FakeAlpaca(submit_result=submit, fill_snap=fill)
        om = _make_order_manager(monkeypatch, alp)
        om._log = _FakeDecisionLogger()

        result = om.execute(signal, council_approved)

        assert result is None
        assert om._log.decisions[0]["position_opened"] is False
        assert om._log.decisions[0]["fill_status"] == "expired"

    def test_multi_poll_partial_fill_uses_final_snapshot(
        self, monkeypatch, signal, council_approved
    ):
        """Even if wait_for_fill polls multiple times internally, OrderManager
        should rely on the snapshot it returns (which by contract is the most
        recent broker view).  Stub it returning a partially_filled terminal
        state and confirm the OrderManager records that final state."""
        submit = {"order_id": "ord-7", "status": "accepted",
                  "filled_qty": 0, "filled_avg_price": None,
                  "limit_price": 100.0}
        # Simulate the eventual settlement — partial then 'canceled' working.
        fill = {"order_id": "ord-7", "status": "canceled",
                "filled_qty": 60.0, "filled_avg_price": 100.20,
                "limit_price": 100.0}
        alp = _FakeAlpaca(submit_result=submit, fill_snap=fill)
        om = _make_order_manager(monkeypatch, alp)
        om._log = _FakeDecisionLogger()

        result = om.execute(signal, council_approved)

        # 60 filled before cancel → still a real position opened.
        assert result is not None
        assert result["fill_qty"] == 60.0
        assert result["partial_fill_qty"] == 40.0
        assert result["filled_avg_price"] == 100.20
        # 0.20 / 100 * 1e4 = 20 bps = 0.20 %
        assert result["slippage_bps"] == 20.0
        assert result["slippage_pct"] == 0.20
        d = om._log.decisions[0]
        assert d["position_opened"] is True
        assert d["fill_qty"] == 60.0
        assert d["fill_status"] == "canceled"

    def test_dedup_broker_only_skips(
        self, monkeypatch, signal, council_approved
    ):
        """Standard dedup: broker reports the ticker held → skip, no
        order, no decision row."""
        alp = _FakeAlpaca(
            positions=[{"ticker": "AAPL", "qty": 50.0, "side": "long",
                        "entry_price": 100.0, "current_price": 101.0,
                        "market_value": 5050.0, "unrealized_pnl": 50.0}],
            submit_result={"order_id": "should-not-happen", "status": "accepted"},
            fill_snap=None,
        )
        om = _make_order_manager(monkeypatch, alp)
        result = om.execute(signal, council_approved)
        assert result is None
        assert alp.submit_calls == [], "broker submit must not run when held"
        assert om._log.decisions == []

    def test_dedup_book_only_skips_when_broker_silent(
        self, monkeypatch, signal, council_approved
    ):
        """The 2026-05-04 CRS regression: broker briefly returns no
        positions but the DB knows we hold it. The book-side check must
        still skip; otherwise duplicate broker fills accumulate."""
        alp = _FakeAlpaca(
            positions=[],                            # broker silent
            submit_result={"order_id": "should-not-happen", "status": "accepted"},
            fill_snap=None,
        )
        om = _make_order_manager(
            monkeypatch, alp, open_book_tickers=["AAPL"],
        )
        result = om.execute(signal, council_approved)
        assert result is None
        assert alp.submit_calls == [], (
            "DB-side dedup must prevent the broker submit when book "
            "reports the ticker open"
        )
        assert om._log.decisions == []

    def test_dedup_both_silent_proceeds(
        self, monkeypatch, signal, council_approved
    ):
        """Neither broker nor DB reports the ticker held → order fires."""
        submit = {"order_id": "ord-1", "status": "accepted",
                  "filled_qty": 0, "filled_avg_price": None,
                  "limit_price": 100.0}
        fill = {"order_id": "ord-1", "status": "filled",
                "filled_qty": 100.0, "filled_avg_price": 100.0,
                "limit_price": 100.0}
        alp = _FakeAlpaca(submit_result=submit, fill_snap=fill)
        om = _make_order_manager(monkeypatch, alp)  # book empty by default
        result = om.execute(signal, council_approved)
        assert result is not None
        assert len(alp.submit_calls) == 1
        assert om._log.decisions and om._log.decisions[0]["position_opened"] is True

    def test_dedup_resting_broker_order_skips(
        self, monkeypatch, signal, council_approved
    ):
        """Resting-order dedup: broker has no *position* yet but a working
        DAY limit order for the same ticker+side is still resting from a
        prior scan cycle. Must skip — otherwise duplicate resting orders
        accumulate (the PEGA/CRS stuck-row bursts) and can all fill on one
        price touch."""
        alp = _FakeAlpaca(
            positions=[],                            # no fill yet
            open_orders=[{"symbol": "AAPL", "side": "buy", "status": "new",
                          "filled_qty": 0.0, "order_id": "resting-1"}],
            submit_result={"order_id": "should-not-happen", "status": "accepted"},
            fill_snap=None,
        )
        om = _make_order_manager(monkeypatch, alp)
        result = om.execute(signal, council_approved)
        assert result is None
        assert alp.submit_calls == [], (
            "must not submit a second order when one is already resting"
        )
        assert om._log.decisions == []

    def test_dedup_resting_order_opposite_side_does_not_block(
        self, monkeypatch, signal, council_approved
    ):
        """A working order on the *opposite* side (e.g. a resting sell) must
        not block a new buy — the guard is side-specific."""
        submit = {"order_id": "ord-1", "status": "accepted",
                  "filled_qty": 0, "filled_avg_price": None, "limit_price": 100.0}
        fill = {"order_id": "ord-1", "status": "filled",
                "filled_qty": 100.0, "filled_avg_price": 100.0, "limit_price": 100.0}
        alp = _FakeAlpaca(
            open_orders=[{"symbol": "AAPL", "side": "sell", "status": "new"}],
            submit_result=submit, fill_snap=fill,
        )
        om = _make_order_manager(monkeypatch, alp)
        result = om.execute(signal, council_approved)
        assert result is not None
        assert len(alp.submit_calls) == 1

    def test_dedup_recent_unfilled_book_backstop_skips(
        self, monkeypatch, signal, council_approved
    ):
        """Broker working-orders call returns nothing (e.g. transient
        failure → fail-open []), but the book remembers a recent unfilled
        order for the ticker. The DB backstop must still skip."""
        alp = _FakeAlpaca(
            positions=[], open_orders=[],            # broker shows nothing
            submit_result={"order_id": "should-not-happen", "status": "accepted"},
            fill_snap=None,
        )
        om = _make_order_manager(
            monkeypatch, alp, recent_unfilled_tickers=["AAPL"],
        )
        result = om.execute(signal, council_approved)
        assert result is None
        assert alp.submit_calls == []
        assert om._log.decisions == []

    def test_dedup_passed_in_open_orders_used_without_broker_call(
        self, monkeypatch, signal, council_approved
    ):
        """When the caller passes open_orders (main.py fetches once per
        cycle), the guard uses it and does not fall back to a per-ticker
        broker call."""
        called = {"n": 0}
        alp = _FakeAlpaca(
            submit_result={"order_id": "x", "status": "accepted"}, fill_snap=None,
        )
        orig = alp.get_open_orders

        def _trace(symbol=None):
            called["n"] += 1
            return orig(symbol)
        alp.get_open_orders = _trace  # type: ignore[assignment]

        om = _make_order_manager(monkeypatch, alp)
        result = om.execute(
            signal, council_approved,
            open_orders=[{"symbol": "AAPL", "side": "buy", "status": "new"}],
        )
        assert result is None, "passed-in resting order should block"
        assert called["n"] == 0, "must not call get_open_orders when list provided"
        assert alp.submit_calls == []

    def test_council_vetoed_short_circuits_before_broker(
        self, monkeypatch, signal, council_approved
    ):
        """Sanity guard: a council veto must return None without ever
        touching the broker — protects the no-fill path from being
        masked by an upstream skip."""
        submit_called: List[Any] = []
        alp = _FakeAlpaca(
            submit_result={"order_id": "should-not-happen", "status": "accepted"},
            fill_snap=None,
        )
        # wrap submit_limit_order to detect any unexpected call
        orig_submit = alp.submit_limit_order

        def _trace_submit(**kw):
            submit_called.append(kw)
            return orig_submit(**kw)
        alp.submit_limit_order = _trace_submit  # type: ignore[assignment]

        om = _make_order_manager(monkeypatch, alp)
        om._log = _FakeDecisionLogger()

        result = om.execute(signal, {"verdict": "VETOED"})

        assert result is None
        assert submit_called == [], "broker submit must not run on veto"
        assert om._log.decisions == []
