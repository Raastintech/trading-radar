"""
tests/unit/test_council_heat_precheck.py

Fix 6: the council's portfolio agent now refuses proposals that would
exceed PortfolioRisk's single-name heat cap. Otherwise the council
approves and the submission gate refuses ~3× over (the HUBS-29 pattern
from the 2026-05-13 session).
"""
from __future__ import annotations

import os
import sys
from typing import Dict

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from council import veto_council as vc_mod  # noqa: E402
from execution.portfolio_risk import MAX_SINGLE_NAME_PCT  # noqa: E402


class _StubAlpaca:
    def __init__(self, positions=None):
        self._p = positions or []

    def get_positions(self):
        return list(self._p)


class _StubFMP:
    pass


@pytest.fixture
def make_council(monkeypatch):
    def _factory(positions=None):
        monkeypatch.setattr(vc_mod, "get_alpaca", lambda: _StubAlpaca(positions))
        monkeypatch.setattr(vc_mod, "get_fmp",    lambda: _StubFMP())
        return vc_mod.VetoCouncil()
    return _factory


def _state(equity: float = 80_000.0) -> Dict:
    return {
        "open_positions": 0,
        "max_positions":  8,
        "daily_pnl_pct":  0.0,
        "equity":         equity,
        "circuit_breaker": False,
    }


class TestPortfolioAgentHeatPrecheck:

    def test_oversized_proposal_is_vetoed_at_council(self, make_council):
        """HUBS-style trade: equity $80k × 5% cap = $4k single-name cap.
        Council must veto a $12k proposal."""
        c = make_council()
        signal = {"ticker": "HUBS", "strategy": "SHORT", "direction": "SHORT",
                  "shares": 60, "entry_price": 200.0,  # 12,000 notional
                  "stop_loss": 210.0, "target_price": 180.0}
        v = c._portfolio_agent("HUBS", signal, _state(equity=80_000.0))
        assert v["verdict"] == "VETO"
        assert "single-name heat" in v["reason"]
        assert "12,000" in v["reason"] or "12000" in v["reason"]

    def test_in_cap_proposal_approved(self, make_council):
        """$3.6k trade vs $4k cap → council approves at portfolio gate."""
        c = make_council()
        signal = {"ticker": "MSFT", "strategy": "VOYAGER", "direction": "LONG",
                  "shares": 10, "entry_price": 360.0,
                  "stop_loss": 340.0, "target_price": 400.0}
        v = c._portfolio_agent("MSFT", signal, _state(equity=80_000.0))
        assert v["verdict"] == "APPROVE", v

    def test_existing_position_consumes_cap_room(self, make_council):
        """Already holding $3k of HUBS → another $2k order must veto
        because total $5k > $4k cap."""
        positions = [{"ticker": "HUBS", "market_value": -3_000.0, "side": "short"}]
        c = make_council(positions=positions)
        signal = {"ticker": "HUBS", "strategy": "SHORT", "direction": "SHORT",
                  "shares": 10, "entry_price": 200.0,  # 2,000 incremental
                  "stop_loss": 210.0, "target_price": 180.0}
        v = c._portfolio_agent("HUBS", signal, _state(equity=80_000.0))
        assert v["verdict"] == "VETO"
        assert "5,000" in v["reason"]

    def test_no_equity_disables_check(self, make_council):
        """Defensive: missing equity hint must not crash; check skipped."""
        c = make_council()
        signal = {"ticker": "X", "strategy": "SHORT", "direction": "SHORT",
                  "shares": 1000, "entry_price": 100.0}
        s = _state(equity=0.0)
        v = c._portfolio_agent("X", signal, s)
        # Heat check skipped → falls through to APPROVE.
        assert v["verdict"] == "APPROVE"
