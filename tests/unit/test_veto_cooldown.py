"""
tests/unit/test_veto_cooldown.py

Pins the Fix-5 contract: once VetoCouncil vetoes a (ticker, strategy)
inside a session, repeat evaluations of the same setup return a cached
VETOED verdict without re-running the agents or producing a fresh
log row. The cache invalidates when the entry price moves more than
COOLDOWN_REPRICE_BPS so a real intraday regime shift gets a re-look.

Why: on 2026-05-13 CAI was vetoed 72× in a single session at scores
27.9 / 28.6 / 27.9 — identical setup, identical answer, 72 redundant
veto_log rows. Cool-down collapses that to one veto per session per
material reprice.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from council import veto_council as vc_mod  # noqa: E402


class _StubAlpaca:
    def get_positions(self):
        return []


class _StubFMP:
    pass


@pytest.fixture(autouse=True)
def _stub_clients(monkeypatch):
    monkeypatch.setattr(vc_mod, "get_alpaca", lambda: _StubAlpaca())
    monkeypatch.setattr(vc_mod, "get_fmp",    lambda: _StubFMP())


def _make_council(monkeypatch, *, soft_score: float):
    """Return a VetoCouncil whose Tier-1 agents always pass and whose
    Tier-2 agents emit a fixed soft score for testing the cool-down."""
    c = vc_mod.VetoCouncil()
    monkeypatch.setattr(c, "_regime_agent",
                        lambda t: {"verdict": "PASS", "reason": ""})
    monkeypatch.setattr(c, "_macro_agent",
                        lambda t: {"verdict": "PASS", "reason": ""})
    monkeypatch.setattr(c, "_portfolio_agent",
                        lambda t, s, p: {"verdict": "PASS", "reason": ""})
    # Each Tier-2 agent returns score=soft_score so weighted sum = soft_score.
    for name in ("sector", "flow", "sentiment", "earnings", "spread", "momentum"):
        monkeypatch.setattr(
            c, f"_{name}_agent",
            lambda *a, _s=soft_score, **kw: {"score": _s, "reason": "stub"},
        )
    return c


def _sig(ticker: str = "CAI", entry: float = 50.0) -> Dict[str, Any]:
    return {
        "ticker":       ticker,
        "strategy":     "SHORT",
        "direction":    "SHORT",
        "entry_price":  entry,
        "stop_loss":    entry * 1.05,
        "target_price": entry * 0.90,
    }


class TestVetoCooldown:

    def test_second_eval_uses_cache_when_price_unchanged(self, monkeypatch):
        c = _make_council(monkeypatch, soft_score=30)
        first = c.evaluate(_sig(), portfolio_state={})
        assert first["verdict"] == "VETOED"
        assert first["cached"] is False

        second = c.evaluate(_sig(), portfolio_state={})
        assert second["verdict"] == "VETOED"
        assert second["cached"] is True, (
            "Same ticker+strategy+entry within a session must short-"
            "circuit; otherwise CAI-style 72×/day veto spam returns."
        )

    def test_cache_invalidates_on_material_reprice(self, monkeypatch):
        c = _make_council(monkeypatch, soft_score=30)
        c.evaluate(_sig(entry=50.00), portfolio_state={})
        # 0.6% move — above the 50 bps reprice threshold
        again = c.evaluate(_sig(entry=50.30), portfolio_state={})
        assert again["cached"] is False, (
            "A >50 bps move must invalidate the cool-down so a new "
            "veto/approve decision can be reached."
        )

    def test_cache_holds_on_small_reprice(self, monkeypatch):
        c = _make_council(monkeypatch, soft_score=30)
        c.evaluate(_sig(entry=50.00), portfolio_state={})
        # 0.2% move — below the 50 bps reprice threshold
        again = c.evaluate(_sig(entry=50.10), portfolio_state={})
        assert again["cached"] is True

    def test_approved_eval_is_not_cached(self, monkeypatch):
        """Approved setups must NOT be cached — we want a fresh
        evaluation each cycle on a live opportunity."""
        c = _make_council(monkeypatch, soft_score=80)
        first = c.evaluate(_sig(), portfolio_state={})
        assert first["verdict"] == "APPROVED"
        second = c.evaluate(_sig(), portfolio_state={})
        # Both runs evaluate the agents; neither is "cached"
        assert second.get("cached") is False

    def test_different_strategy_same_ticker_not_cached(self, monkeypatch):
        """SNIPER veto must not silence a SHORT evaluation on the same
        ticker (they read different agent weight profiles and represent
        different setups)."""
        c = _make_council(monkeypatch, soft_score=30)
        c.evaluate(_sig(), portfolio_state={})
        other = dict(_sig())
        other["strategy"] = "SNIPER"
        other["direction"] = "LONG"
        again = c.evaluate(other, portfolio_state={})
        assert again["cached"] is False
