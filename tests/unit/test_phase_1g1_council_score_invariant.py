"""
tests/unit/test_phase_1g1_council_score_invariant.py — Phase 1G.1 invariant
checks for the veto council's Tier-2 score range.

Audit context:
  Before Phase 1G.1 the ``_flow_agent`` could return Tier-2 scores far
  outside [0, 100]; combined with the weighted-sum step this produced
  soft_score values like -1685 / -1896 in the veto_log. These tests
  pin the new invariant: scores must stay in [0, 100], an off-scale
  vote triggers a SCORE_ANOMALY safe veto, and the flow agent's own
  math no longer produces out-of-range outputs.

No live providers are touched — all council agents are monkey-patched
to deterministic stubs and the Alpaca/FMP singletons are intercepted
in ``__init__``.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _import_council():
    """Defer the import so the credential stub in tests/conftest.py
    runs first."""
    from council import veto_council as vc  # noqa: WPS433
    return vc


# ── Pure-helper tests ──────────────────────────────────────────────────────


class TestSafeAgentScore:
    def test_in_range_round(self):
        vc = _import_council()
        assert vc._safe_agent_score(73.4) == 73
        assert vc._safe_agent_score(0) == 0
        assert vc._safe_agent_score(100) == 100

    def test_below_floor_clamps(self):
        vc = _import_council()
        assert vc._safe_agent_score(-1685.5) == vc.SOFT_SCORE_FLOOR
        assert vc._safe_agent_score(-1) == vc.SOFT_SCORE_FLOOR

    def test_above_ceiling_clamps(self):
        vc = _import_council()
        assert vc._safe_agent_score(101) == vc.SOFT_SCORE_CEILING
        assert vc._safe_agent_score(10_000) == vc.SOFT_SCORE_CEILING

    def test_non_numeric_defaults_neutral(self):
        vc = _import_council()
        assert vc._safe_agent_score(None) == 50
        assert vc._safe_agent_score("nan") == 50
        assert vc._safe_agent_score(float("nan")) == 50


class TestValidateVotes:
    def test_all_in_range(self):
        vc = _import_council()
        votes = {
            "sector":    {"verdict": "APPROVE", "score": 55},
            "flow":      {"verdict": "APPROVE", "score": 60},
            "sentiment": {"verdict": "APPROVE", "score": 50},
        }
        ok, msgs = vc._validate_votes(votes)
        assert ok and msgs == []

    def test_negative_flow_flagged(self):
        vc = _import_council()
        votes = {
            "flow": {"verdict": "APPROVE", "score": -425},
        }
        ok, msgs = vc._validate_votes(votes)
        assert not ok
        assert any("flow" in m for m in msgs)

    def test_above_ceiling_flagged(self):
        vc = _import_council()
        votes = {
            "momentum": {"verdict": "APPROVE", "score": 150},
        }
        ok, msgs = vc._validate_votes(votes)
        assert not ok

    def test_nan_flagged(self):
        vc = _import_council()
        votes = {
            "spread": {"verdict": "APPROVE", "score": float("nan")},
        }
        ok, msgs = vc._validate_votes(votes)
        assert not ok

    def test_tier1_without_score_ignored(self):
        vc = _import_council()
        # Tier-1 agents (regime, macro, portfolio) do not report a score.
        votes = {
            "regime":    {"verdict": "APPROVE"},
            "portfolio": {"verdict": "APPROVE", "reason": "ok"},
            "flow":      {"verdict": "APPROVE", "score": 50},
        }
        ok, msgs = vc._validate_votes(votes)
        assert ok and msgs == []


# ── Flow-agent source-fix test ─────────────────────────────────────────────


class _StubAlpaca:
    def __init__(self, bars: List[Dict[str, Any]]):
        self._bars = bars

    def get_intraday_bars(self, *a, **kw):
        return self._bars


class TestFlowAgentClamp:
    """The flow agent's raw math now stays in [0, 100] even for extreme
    intraday volume accelerations. We construct synthetic 5-min bars
    that would have produced large negative scores before Phase 1G.1.
    """

    def _agent(self, bars):
        vc = _import_council()
        council = vc.VetoCouncil.__new__(vc.VetoCouncil)
        council._alpaca = _StubAlpaca(bars)
        council._fmp = None  # not used by flow_agent
        council._cooldown = {}
        return council

    @staticmethod
    def _bars(early_vol: float, late_vol: float, n: int = 20) -> List[Dict[str, Any]]:
        # First 10 bars at ``early_vol``, last 5 at ``late_vol``.
        out = []
        for i in range(n):
            v = early_vol if i < 10 else late_vol
            out.append({"volume": v})
        return out

    def test_short_against_heavy_buy_clamps_to_floor(self):
        agent = self._agent(self._bars(early_vol=1000.0, late_vol=10000.0))
        vote = agent._flow_agent("XYZ", "SHORT")
        # vol_accel = 10, raw = 50 + (1-10)*50 = -400 → clamps to 0.
        assert vote["verdict"] == "APPROVE"
        assert 0 <= vote["score"] <= 100
        assert vote["score"] == 0

    def test_long_with_heavy_buy_clamps_to_ceiling(self):
        agent = self._agent(self._bars(early_vol=1000.0, late_vol=10000.0))
        vote = agent._flow_agent("XYZ", "LONG")
        # vol_accel = 10, raw = 50 + (10-1)*50 = 500 → clamps to 100.
        assert 0 <= vote["score"] <= 100
        assert vote["score"] == 100

    def test_neutral_accel_stays_at_50(self):
        agent = self._agent(self._bars(early_vol=1000.0, late_vol=1000.0))
        long_vote = agent._flow_agent("XYZ", "LONG")
        short_vote = agent._flow_agent("XYZ", "SHORT")
        assert long_vote["score"] == 50
        assert short_vote["score"] == 50

    def test_insufficient_data_returns_50(self):
        agent = self._agent(self._bars(early_vol=1000, late_vol=1000, n=3))
        vote = agent._flow_agent("XYZ", "LONG")
        assert vote["score"] == 50


# ── End-to-end SCORE_ANOMALY veto ──────────────────────────────────────────


class TestScoreAnomalyVeto:
    """When a Tier-2 agent returns a score outside [0, 100], the council
    must return a SCORE_ANOMALY safe veto without including the bad
    score in the weighted total."""

    def _council_with_bad_flow(self):
        vc = _import_council()
        council = vc.VetoCouncil.__new__(vc.VetoCouncil)
        council._alpaca = None
        council._fmp = None
        council._cooldown = {}

        # Tier-1 agents all approve.
        council._regime_agent = lambda _t: {"verdict": "APPROVE", "reason": "ok"}
        council._macro_agent = lambda _t: {"verdict": "APPROVE", "reason": "ok"}
        council._portfolio_agent = lambda _t, _s, _st: {"verdict": "APPROVE", "reason": "ok"}

        # Tier-2 with one off-scale score.
        council._sector_agent    = lambda _t, _d: {"verdict": "APPROVE", "score": 55}
        council._flow_agent      = lambda _t, _d: {"verdict": "APPROVE", "score": -8000}
        council._sentiment_agent = lambda _t, _d: {"verdict": "APPROVE", "score": 60}
        council._earnings_agent  = lambda _t:     {"verdict": "APPROVE", "score": 80}
        council._spread_agent    = lambda _t:     {"verdict": "APPROVE", "score": 70}
        council._momentum_agent  = lambda _t, _d: {"verdict": "APPROVE", "score": 50}
        return council, vc

    def test_anomaly_routes_to_safe_veto(self):
        council, vc = self._council_with_bad_flow()
        signal = {
            "ticker": "ABC", "strategy": "SHORT", "direction": "SHORT",
            "entry_price": 100.0, "stop_loss": 105.0, "target_price": 90.0,
            "shares": 10.0,
        }
        portfolio_state = {
            "daily_pnl_pct": 0.0, "open_positions": 0,
            "max_positions": 8, "circuit_breaker": False,
            "equity": 100_000,
        }
        result = council.evaluate(signal, portfolio_state)
        assert result["verdict"] == "VETOED"
        assert result["agent"] == vc.SCORE_ANOMALY_AGENT
        assert "SCORE_ANOMALY" in result["reason"]
        assert result["soft_score"] == 0

    def test_in_range_path_unchanged(self):
        council, _vc = self._council_with_bad_flow()
        # Bring flow back into range and verify normal soft-score path.
        council._flow_agent = lambda _t, _d: {"verdict": "APPROVE", "score": 80}
        signal = {
            "ticker": "ABC", "strategy": "SHORT", "direction": "SHORT",
            "entry_price": 100.0, "stop_loss": 105.0, "target_price": 90.0,
            "shares": 10.0,
        }
        portfolio_state = {
            "daily_pnl_pct": 0.0, "open_positions": 0,
            "max_positions": 8, "circuit_breaker": False,
            "equity": 100_000,
        }
        result = council.evaluate(signal, portfolio_state)
        assert result["verdict"] in {"APPROVED", "VETOED"}
        # Soft score must be in [0, 100] regardless of verdict.
        assert 0 <= result["soft_score"] <= 100
