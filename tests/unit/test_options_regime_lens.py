"""
tests/unit/test_options_regime_lens.py — Phase 1G.12.

Covers the Options Regime Lens: GEX-proxy safety with missing gamma/OI, skew
computed only from comparable deltas/strikes, IV-rank NOT_ENOUGH_HISTORY gating,
idempotent IV-history append, deterministic regime labels, and the research-only
invariants (no execution/governance/strategy imports, no DB mutation, no paper
signals / trade proposals, and dashboard/MCP cache-only surfacing).
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from research import options_regime_lens as L  # noqa: E402


# ── synthetic chain helpers ───────────────────────────────────────────────────

def _row(strike, iv, gamma, oi, delta, vol=100):
    return {"strike": strike, "impliedVolatility": iv, "gamma": gamma,
            "openInterest": oi, "delta": delta, "volume": vol,
            "bid": 1.0, "ask": 1.1}


def _calls(spot, *, gamma=0.02, oi=500, base_iv=0.18, slope=0.10):
    rows = []
    for k in range(int(spot * 0.8), int(spot * 1.21), max(1, int(spot * 0.05))):
        # call delta ~ falls as strike rises; clamp to (0,1)
        cd = max(0.02, min(0.98, (spot - k) / spot + 0.5))
        rows.append(_row(k, base_iv + abs(k - spot) / spot * slope, gamma, oi, cd))
    return pd.DataFrame(rows)


def _puts(spot, *, gamma=0.02, oi=500, base_iv=0.18, slope=0.10):
    rows = []
    for k in range(int(spot * 0.8), int(spot * 1.21), max(1, int(spot * 0.05))):
        pd_ = -max(0.02, min(0.98, (k - spot) / spot + 0.5))
        rows.append(_row(k, base_iv + abs(k - spot) / spot * slope, gamma, oi, pd_))
    return pd.DataFrame(rows)


class FakeFeed:
    """Minimal feed with the get_expirations/get_chain contract."""

    def __init__(self, chain_builder, today):
        self._build = chain_builder
        self._today = today

    def get_expirations(self, sym):
        return [(self._today + timedelta(days=d)).isoformat() for d in (7, 30, 45, 60)]

    def get_chain(self, sym, expiry):
        return self._build(sym, expiry)

    def status(self):
        return {"name": "fake"}


# ── Task 3 — gamma proxy safety ───────────────────────────────────────────────

def test_gamma_proxy_handles_missing_gamma_safely():
    spot = 100.0
    calls = _calls(spot, gamma=0.0)        # no gamma at all
    puts = _puts(spot, gamma=0.0)
    g = L.gamma_proxy(calls, puts, spot=spot)
    assert g["gamma_regime"] == L.NOT_ENOUGH_DATA
    assert g["total_gamma_proxy"] is None
    assert g["gamma_confidence"] in ("none", "low")


def test_gamma_proxy_handles_missing_oi_safely():
    spot = 100.0
    calls = _calls(spot, oi=0)
    puts = _puts(spot, oi=0)
    g = L.gamma_proxy(calls, puts, spot=spot)
    assert g["gamma_regime"] == L.NOT_ENOUGH_DATA


def test_gamma_proxy_handles_empty_frames():
    g = L.gamma_proxy(pd.DataFrame(), pd.DataFrame(), spot=100.0)
    assert g["gamma_regime"] == L.NOT_ENOUGH_DATA
    g2 = L.gamma_proxy(None, None, spot=100.0)
    assert g2["gamma_regime"] == L.NOT_ENOUGH_DATA


def test_gamma_proxy_positive_when_calls_dominate():
    spot = 100.0
    calls = _calls(spot, gamma=0.05, oi=2000)   # heavy call gamma
    puts = _puts(spot, gamma=0.01, oi=200)
    g = L.gamma_proxy(calls, puts, spot=spot)
    assert g["gamma_regime"] == "positive_gamma_regime"
    assert g["total_gamma_proxy"] > 0
    assert g["gamma_confidence"] in ("medium", "high")


def test_gamma_proxy_negative_when_puts_dominate():
    spot = 100.0
    calls = _calls(spot, gamma=0.01, oi=200)
    puts = _puts(spot, gamma=0.05, oi=2000)     # heavy put gamma
    g = L.gamma_proxy(calls, puts, spot=spot)
    assert g["gamma_regime"] == "negative_gamma_regime"
    assert g["total_gamma_proxy"] < 0


# ── Task 4 — skew only from comparable strikes/deltas ──────────────────────────

def test_skew_uses_delta_basis_when_available():
    spot = 100.0
    # puts richer than calls → PUT_HEDGE_DEMAND
    calls = _calls(spot, base_iv=0.18, slope=0.05)
    puts = _puts(spot, base_iv=0.30, slope=0.20)
    s = L.skew_diagnostics(calls, puts, spot=spot)
    assert s["skew_basis"] == "delta"
    assert s["skew_state"] == "PUT_HEDGE_DEMAND"
    assert s["put_call_skew_25d"] is not None and s["put_call_skew_25d"] > 0


def test_skew_falls_back_to_moneyness_without_deltas():
    spot = 100.0
    calls = _calls(spot, base_iv=0.18)
    puts = _puts(spot, base_iv=0.30)
    # strip deltas → force moneyness fallback
    calls = calls.drop(columns=["delta"])
    puts = puts.drop(columns=["delta"])
    s = L.skew_diagnostics(calls, puts, spot=spot)
    assert s["skew_basis"] == "moneyness"
    assert s["skew_state"] in ("PUT_HEDGE_DEMAND", "BALANCED", "CALL_CHASE")
    assert s["skew_confidence"] == "low"


def test_skew_not_enough_data_without_iv():
    spot = 100.0
    calls = _calls(spot)
    puts = _puts(spot)
    calls["impliedVolatility"] = 0.0
    puts["impliedVolatility"] = 0.0
    s = L.skew_diagnostics(calls, puts, spot=spot)
    assert s["skew_state"] == L.NOT_ENOUGH_DATA


def test_skew_call_chase_when_calls_richer():
    spot = 100.0
    calls = _calls(spot, base_iv=0.32, slope=0.20)   # calls richer
    puts = _puts(spot, base_iv=0.18, slope=0.05)
    s = L.skew_diagnostics(calls, puts, spot=spot)
    assert s["skew_state"] == "CALL_CHASE"


# ── Task 5 — IV rank history gating + idempotent append ────────────────────────

def test_iv_rank_not_enough_history():
    short = [0.18, 0.19, 0.20]  # < min obs
    r = L.iv_rank_percentile(short, 0.19)
    assert r["iv_rank"] is None and r["iv_percentile"] is None
    assert L._iv_state_from_rank(r["iv_rank"], r["n"]) == "NOT_ENOUGH_HISTORY"


def test_iv_rank_computes_with_enough_history():
    series = [0.10 + 0.001 * i for i in range(40)]  # 40 obs, range 0.10..0.139
    r = L.iv_rank_percentile(series, 0.139)
    assert r["iv_rank"] is not None
    assert 90.0 <= r["iv_rank"] <= 100.0
    assert L._iv_state_from_rank(r["iv_rank"], r["n"]) == "EXTREME_IV"


def test_iv_history_appends_idempotently(tmp_path):
    hp = tmp_path / "hist.jsonl"
    snaps = [{"symbol": "SPY", "atm_iv_30d": 0.18,
              "skew": {"put_call_skew_25d": 0.05},
              "gamma": {"total_gamma_proxy": 1.2},
              "term_structure": {"term_structure_state": "NORMAL"},
              "data_quality": "ok"}]
    w1 = L.append_history(snaps, path=hp, today_iso="2026-05-28")
    w2 = L.append_history(snaps, path=hp, today_iso="2026-05-28")  # same day
    w3 = L.append_history(snaps, path=hp, today_iso="2026-05-29")  # next day
    assert (w1, w2, w3) == (1, 0, 1)
    rows = L.read_history(hp)
    assert len(rows) == 2
    series = L.history_series_for(rows, "SPY", exclude_date="2026-05-29")
    assert series == [0.18]


def test_history_skips_degraded_snapshot_then_records_good(tmp_path):
    """A feed-unconfigured (null IV) snapshot must not poison the day's slot:
    it is skipped, and a later good run for the same day still records."""
    hp = tmp_path / "hist.jsonl"
    degraded = [{"symbol": "SPY", "atm_iv_30d": None, "data_quality": "NOT_ENOUGH_DATA",
                 "skew": {}, "gamma": {}, "term_structure": {}}]
    good = [{"symbol": "SPY", "atm_iv_30d": 0.18, "data_quality": "ok",
             "skew": {"put_call_skew_25d": 0.05},
             "gamma": {"total_gamma_proxy": 1.2},
             "term_structure": {"term_structure_state": "NORMAL"}}]
    w_bad = L.append_history(degraded, path=hp, today_iso="2026-05-28")
    w_good = L.append_history(good, path=hp, today_iso="2026-05-28")
    assert w_bad == 0          # degraded snapshot skipped
    assert w_good == 1         # good run still records the day
    rows = L.read_history(hp)
    assert len(rows) == 1 and rows[0]["atm_iv"] == 0.18


# ── Task 6 — term structure ────────────────────────────────────────────────────

def test_term_structure_not_enough_data():
    ts = L.term_structure([(7, 0.2)])  # single point
    assert ts["term_structure_state"] == L.NOT_ENOUGH_DATA


def test_term_structure_normal_contango():
    ts = L.term_structure([(7, 0.16), (30, 0.18), (60, 0.20)])
    assert ts["term_structure_state"] == "NORMAL"


def test_term_structure_event_premium():
    ts = L.term_structure([(7, 0.30), (30, 0.18), (60, 0.17)])
    assert ts["term_structure_state"] in ("EVENT_PREMIUM", "BACKWARDATION_LIKE")
    assert ts["front_minus_30d"] > 0


# ── Task 7 — deterministic regime labels ───────────────────────────────────────

def test_regime_fragile_hedging_is_deterministic():
    out1 = L.classify_regime(gamma_regime="negative_gamma_regime",
                             skew_state="PUT_HEDGE_DEMAND",
                             iv_state="HIGH_IV", term_state="NORMAL")
    out2 = L.classify_regime(gamma_regime="negative_gamma_regime",
                             skew_state="PUT_HEDGE_DEMAND",
                             iv_state="HIGH_IV", term_state="NORMAL")
    assert out1 == out2
    assert out1["options_regime"] == L.REGIME_FRAGILE_HEDGING
    assert out1["risk_warning_level"] == "MEDIUM"


def test_regime_calm_range():
    out = L.classify_regime(gamma_regime="positive_gamma_regime",
                            skew_state="BALANCED", iv_state="NORMAL_IV",
                            term_state="NORMAL")
    assert out["options_regime"] == L.REGIME_CALM_RANGE


def test_regime_bullish_stable():
    out = L.classify_regime(gamma_regime="positive_gamma_regime",
                            skew_state="BALANCED", iv_state="LOW_IV",
                            term_state="NORMAL")
    assert out["options_regime"] == L.REGIME_BULLISH_STABLE


def test_regime_high_vol_stress():
    out = L.classify_regime(gamma_regime="negative_gamma_regime",
                            skew_state="PUT_HEDGE_DEMAND", iv_state="EXTREME_IV",
                            term_state="BACKWARDATION_LIKE")
    assert out["options_regime"] == L.REGIME_HIGH_VOL_STRESS
    assert out["risk_warning_level"] == "HIGH"


def test_regime_not_enough_data():
    out = L.classify_regime(gamma_regime=L.NOT_ENOUGH_DATA,
                            skew_state=L.NOT_ENOUGH_DATA,
                            iv_state="NOT_ENOUGH_HISTORY",
                            term_state=L.NOT_ENOUGH_DATA)
    assert out["options_regime"] == L.REGIME_NOT_ENOUGH_DATA


# ── end-to-end with a synthetic feed (no providers) ────────────────────────────

def test_analyze_symbol_end_to_end_fragile(tmp_path):
    spot = 100.0
    today = date(2026, 5, 28)

    def builder(sym, expiry):
        return {"calls": _calls(spot, gamma=0.01, oi=200, base_iv=0.18, slope=0.05),
                "puts": _puts(spot, gamma=0.05, oi=3000, base_iv=0.32, slope=0.22)}

    feed = FakeFeed(builder, today)
    # supply enough history so IV state can be HIGH/EXTREME
    series = [0.10 + 0.0005 * i for i in range(40)]
    res = L.analyze_symbol("SPY", feed=feed, spot=spot,
                           history_series=series, today=today)
    assert res["data_quality"] == "ok"
    assert res["gamma"]["gamma_regime"] == "negative_gamma_regime"
    assert res["skew"]["skew_state"] == "PUT_HEDGE_DEMAND"
    # fragile / stress family given negative gamma + put hedging + high IV
    assert res["options_regime"] in (L.REGIME_FRAGILE_HEDGING, L.REGIME_HIGH_VOL_STRESS)


def test_run_with_feed_none_degrades(tmp_path):
    payload = L.run(["SPY", "QQQ"], feed=None, write=False, today=date(2026, 5, 28))
    assert payload["feed_configured"] is False
    assert payload["market"]["market_options_regime"] == L.REGIME_NOT_ENOUGH_DATA
    for s in payload["per_symbol"]:
        assert s["options_regime"] == L.REGIME_NOT_ENOUGH_DATA


def test_run_writes_artifacts(tmp_path, monkeypatch):
    # redirect outputs into tmp so we don't touch real sidecars
    monkeypatch.setattr(L, "JSON_OUT", tmp_path / "orl.json")
    monkeypatch.setattr(L, "TXT_OUT", tmp_path / "orl.txt")
    hp = tmp_path / "hist.jsonl"
    payload = L.run(["SPY"], feed=None, history_path=hp, write=True,
                    today=date(2026, 5, 28))
    assert (tmp_path / "orl.json").exists()
    assert (tmp_path / "orl.txt").exists()
    # history row written even when feed is None (snapshot going forward)
    assert hp.exists()


# ── research-only invariants ───────────────────────────────────────────────────

def test_no_execution_governance_or_strategy_imports():
    src = (REPO / "research" / "options_regime_lens.py").read_text()
    forbidden = ("from execution", "import execution", "from council", "import council",
                 "paper_governance", "order_manager", "submit_order", "close_position",
                 "from strategies", "import strategies", "decision_logger",
                 "register_strategy", "ALLOW_LIVE_CAPITAL", "PAPER_TRADING",
                 "ALPACA_PAPER")
    for tok in forbidden:
        assert tok not in src, f"options regime lens must not reference {tok!r}"


def test_no_db_mutation_or_paper_signal_writes():
    src = (REPO / "research" / "options_regime_lens.py").read_text().upper()
    for tok in ("INSERT INTO", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
                "ALTER TABLE", "UPDATE ", "INSERT OR REPLACE", "PAPER_SIGNALS",
                "TRADE_PROPOSAL"):
        assert tok not in src, f"options regime lens must not mutate DB / write signals ({tok})"


def test_no_trade_language():
    src = (REPO / "research" / "options_regime_lens.py").read_text()
    for tok in ("BUY ", "SELL ", "SHORT ", "go long", "take profit"):
        assert tok not in src, f"options regime lens must not contain trade language {tok!r}"


def test_dashboard_surfacing_is_cache_only():
    """The dashboard must read the options sidecar via _fetch_risk_sidecar and
    never import the lens or an options provider for this surface."""
    src = (REPO / "dashboards" / "gem_trader_hq.py").read_text()
    assert "options_regime_lens_latest.json" in src
    assert "import research.options_regime_lens" not in src
    assert "from research.options_regime_lens" not in src
    assert "options_feed_factory" not in src


def test_mcp_orchestrator_surfacing_is_cache_only():
    src = (REPO / "research" / "mcp_audit_orchestrator.py").read_text()
    assert "options_regime_lens_latest.json" in src
    # orchestrator must not call providers for this surface
    assert "options_feed_factory" not in src
    assert "get_alpaca" not in src
