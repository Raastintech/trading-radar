"""
tests/unit/test_power_trend_extension_study.py — Phase 1G.14.

Covers the power-trend extension study: the A/B/C/D extension classifier
(POWER_TREND / CLIMAX_CHASE / WAIT_FOR_RESET / LOW_QUALITY), immature-forward-window
exclusion + pullback metrics, the cache-only MCP / dashboard summary, and the
research-only invariants (no execution/governance imports, no DB mutation, no
paper-signal/live-capital tokens, no provider calls).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from research import power_trend_extension_study as P  # noqa: E402
from research.scanner_truth import dataio  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────────

def _feat(ext_ma20=0.25, r10=0.10, rs20_spy=0.12, climax_vol=1.2,
          ma50_rising=True, vol_expansion=1.3):
    return {"ext_ma20": ext_ma20, "r10": r10, "rs20_spy": rs20_spy,
            "climax_vol": climax_vol, "ma50_rising": ma50_rising,
            "vol_expansion": vol_expansion}


def _cal(n=320):
    return pd.date_range("2025-03-03", periods=n, freq="B")


def _frame(cal, drift):
    close = 50.0 * np.cumprod(np.full(len(cal), 1.0 + drift))
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": np.full(len(cal), 5_000_000.0)}, index=cal)


def _install(monkeypatch, drifts):
    cal = _cal()
    frames = {t: _frame(cal, d) for t, d in drifts.items()}
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: frames.get(t.upper()))
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    return cal, frames


# ── 1. classification ─────────────────────────────────────────────────────────────

def test_classify_power_trend_extension():
    # leading theme + positive RS + rising MA50 + calm volume + no chase ⇒ A.
    label, _why = P._classify(_feat(), "semiconductors", options_quality=None, call_chase=False)
    assert label == "POWER_TREND_EXTENSION"


def test_classify_climax_chase_extension():
    # extreme extension + blow-off (climax) volume ⇒ B, even in a power theme.
    f = _feat(ext_ma20=0.55, r10=0.60, climax_vol=4.0)
    label, _why = P._classify(f, "semiconductors", options_quality=None, call_chase=False)
    assert label == "CLIMAX_CHASE_EXTENSION"


def test_classify_climax_on_speculative_call_chase():
    f = _feat(ext_ma20=0.55, r10=0.20, climax_vol=1.5)
    label, _why = P._classify(f, "hardware", options_quality="SPECULATIVE_CALL_CHASE", call_chase=True)
    assert label == "CLIMAX_CHASE_EXTENSION"


def test_classify_wait_for_reset():
    # quality name (power theme) but MA50 not rising and not climax ⇒ C.
    f = _feat(ext_ma20=0.30, r10=0.05, rs20_spy=0.06, climax_vol=1.1, ma50_rising=False)
    label, _why = P._classify(f, "space_aerospace", options_quality=None, call_chase=False)
    assert label == "EXTENDED_BUT_WAIT_FOR_RESET"


def test_classify_low_quality_extension():
    # non-theme AND weak RS ⇒ D.
    f = _feat(ext_ma20=0.25, r10=0.05, rs20_spy=-0.02)
    label, _why = P._classify(f, "other", options_quality=None, call_chase=False)
    assert label == "LOW_QUALITY_EXTENSION"


def test_power_themes_are_the_documented_clusters():
    for t in ("semiconductors", "hardware", "memory_storage", "space_aerospace",
              "nuclear_energy", "quantum", "crypto_blockchain"):
        assert t in P.POWER_THEMES
    assert "other" not in P.POWER_THEMES
    assert "biotech_healthcare" not in P.POWER_THEMES


# ── 2. immature forward windows excluded + pullback metrics ───────────────────────

def test_fwd_with_pullback_immature_returns_none(monkeypatch):
    cal, _ = _install(monkeypatch, {"SPY": 0.001, "QQQ": 0.001, "AAA": 0.004})
    spy = P._aligned(dataio.load_prices("SPY"), cal)
    qqq = P._aligned(dataio.load_prices("QQQ"), cal)
    last = len(cal) - 1
    assert P._fwd_with_pullback("AAA", cal, last, 5, spy, qqq) is None
    i = len(cal) - 25
    m = P._fwd_with_pullback("AAA", cal, i, 5, spy, qqq)
    assert m is not None and "time_to_first_pullback" in m and "reset_improved_entry" in m


def test_label_basket_stats_counts_only_matured(monkeypatch):
    cal, _ = _install(monkeypatch, {"SPY": 0.001, "QQQ": 0.001, "AAA": 0.004})
    spy = P._aligned(dataio.load_prices("SPY"), cal)
    qqq = P._aligned(dataio.load_prices("QQQ"), cal)
    i = len(cal) - 25
    matured = {"by_horizon": {"5d": P._fwd_with_pullback("AAA", cal, i, 5, spy, qqq)}}
    immature = {"by_horizon": {"5d": None}}
    stats = P._label_basket_stats([matured, immature], 5)
    assert stats["n_names"] == 2 and stats["n_matured"] == 1


# ── 3. cache-only MCP / dashboard summary ─────────────────────────────────────────

def test_mcp_summary_none_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "OUT_JSON", tmp_path / "absent.json")
    assert P.mcp_summary() == {"present": False}


def test_mcp_summary_reads_published_sidecar():
    res = {
        "cohort": {"n_total": 115},
        "by_horizon_label": {"5d": {
            "ALL_TOO_EXTENDED": {"mean_rel_spy": 0.0199},
            "POWER_TREND_EXTENSION": {"mean_rel_spy": 0.0526},
            "CLIMAX_CHASE_EXTENSION": {"mean_rel_spy": 0.1383}}},
        "verdict": {"power_trend_verdict": "NEED_MORE_DATA", "recommendation": "D"},
    }
    s = P.mcp_summary(res)
    assert s["present"] and s["n_cohort"] == 115
    assert s["power_trend_5d"] == 0.0526 and s["too_extended_5d"] == 0.0199
    assert s["verdict"] == "NEED_MORE_DATA" and s["recommendation"] == "D"


def test_orchestrator_reads_power_trend_sidecar_cache_only(monkeypatch, tmp_path):
    from research import mcp_audit_orchestrator as O
    sidecar = tmp_path / "power_trend_extension_latest.json"
    sidecar.write_text(json.dumps({
        "cohort": {"n_total": 12},
        "by_horizon_label": {"5d": {"ALL_TOO_EXTENDED": {"mean_rel_spy": 0.01},
                                    "POWER_TREND_EXTENSION": {"mean_rel_spy": 0.02},
                                    "CLIMAX_CHASE_EXTENSION": {"mean_rel_spy": -0.01}}},
        "verdict": {"power_trend_verdict": "KEEP_BLOCK", "recommendation": "A"}}))
    monkeypatch.setattr(O, "_read_json_sidecar",
                        lambda name: json.loads(sidecar.read_text())
                        if name == "power_trend_extension_latest.json" else None)
    out = O._read_power_trend_extension()
    assert out["n_cohort"] == 12 and out["verdict"] == "KEEP_BLOCK"
    assert out["recommendation"] == "A" and out["power_trend_5d"] == 0.02


# ── 4. research-only invariants ───────────────────────────────────────────────────

def test_no_execution_governance_or_strategy_imports():
    src = (REPO / "research" / "power_trend_extension_study.py").read_text()
    forbidden = ("from execution", "import execution", "from council", "import council",
                 "paper_governance", "order_manager", "submit_order", "close_position",
                 "from strategies", "import strategies", "decision_logger",
                 "register_strategy", "strategy_registry", "ALLOW_LIVE_CAPITAL",
                 "PAPER_TRADING", "ALPACA_PAPER")
    for tok in forbidden:
        assert tok not in src, f"power-trend study must not reference {tok!r}"


def test_no_db_mutation_or_paper_signal_writes():
    src = (REPO / "research" / "power_trend_extension_study.py").read_text().upper()
    for tok in ("INSERT INTO", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
                "ALTER TABLE", "UPDATE ", "INSERT OR REPLACE", "PAPER_SIGNALS",
                "TRADE_PROPOSAL"):
        assert tok not in src, f"power-trend study must not mutate the DB / write signals ({tok})"


def test_no_provider_calls():
    src = (REPO / "research" / "power_trend_extension_study.py").read_text().lower()
    for tok in ("import alpaca", "from core.alpaca", "get_alpaca", "fmpclient",
                "requests.get", "requests.post", "import yfinance", "options_feed_factory",
                "data_gatekeeper"):
        assert tok not in src, f"power-trend study must be cache-only (found {tok!r})"


def test_no_trade_language():
    src = (REPO / "research" / "power_trend_extension_study.py").read_text()
    for tok in ("BUY ", "SELL ", "go long", "take profit"):
        assert tok not in src, f"power-trend study must not contain trade language {tok!r}"
