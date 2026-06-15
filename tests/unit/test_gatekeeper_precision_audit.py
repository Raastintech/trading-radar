"""
tests/unit/test_gatekeeper_precision_audit.py — Phase 1G.13.

Covers the Gatekeeper precision audit / blocked-winner autopsy: block-reason
normalisation + grouping, immature-forward-window exclusion, cache-depth-artifact
classification, the blocked-vs-not-blocked comparison plumbing, the reason-label
logic, the cache-only MCP / dashboard summary, and the research-only invariants
(no execution/governance imports, no DB mutation, no paper-signal/live-capital
tokens, no provider calls, no trade language).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from research import gatekeeper_precision_audit as G  # noqa: E402
from research import rs_theme_forward_validation as V  # noqa: E402
from research.scanner_truth import dataio  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────────

def _cal(n=320):
    return pd.date_range("2025-03-03", periods=n, freq="B")


def _frame(cal, drift, bars=None):
    """Synthetic OHLCV frame; `bars` (if given) leaves the leading rows NaN so the
    usable bar count is controllable for cache-depth tests."""
    close = 50.0 * np.cumprod(np.full(len(cal), 1.0 + drift))
    df = pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": np.full(len(cal), 5_000_000.0)}, index=cal)
    if bars is not None and bars < len(cal):
        df.iloc[:len(cal) - bars, df.columns.get_loc("close")] = np.nan
    return df


def _install(monkeypatch, drifts, deep_counts=None):
    cal = _cal()
    frames = {t: _frame(cal, d) for t, d in drifts.items()}
    deep_counts = deep_counts or {}

    def load_prices(t, prefer_deep=True):
        return frames.get(t.upper())

    monkeypatch.setattr(dataio, "load_prices", load_prices)
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: sorted(frames))
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    monkeypatch.setattr(dataio, "deep_bar_count", lambda t: deep_counts.get(t.upper(), 0))
    return cal, frames


# ── 1. reason normalisation + grouping ────────────────────────────────────────────

def test_canon_reason_maps_freetext_and_vocab():
    assert G._canon_reason("Too Extended") == "too_extended"
    assert G._canon_reason("too_extended") == "too_extended"
    assert G._canon_reason("price below MA200 floor") == "below_ma200_floor"
    assert G._canon_reason("insufficient_history_260") == "insufficient_history_260"
    assert G._canon_reason("no breakout yet") == "no_breakout"
    assert G._canon_reason("no ATR contraction") == "no_atr_contraction"
    assert G._canon_reason("volume too light") == "volume_insufficient"
    assert G._canon_reason("options_quality=OPTIONS_NO_EDGE") == "options_bearish"
    assert G._canon_reason("") == "unknown"
    assert G._canon_reason("some unmapped phrase") == "unknown"


def test_gk_block_reasons_ignores_missing_subgates():
    """Only the BLOCK-verdict gate + blocking_reasons carry block reasons; MISSING
    sub-gates (data availability) must NOT inject a spurious reason."""
    doc = {
        "final_status": "BLOCK",
        "blocking_reasons": ["[entry_quality → BLOCK] Daily Entry Validator state is 'Too Extended'"],
        "gates": [
            {"name": "entry_quality", "verdict": "BLOCK", "reasons": ["state is 'Too Extended'"]},
            {"name": "institutional_insider", "verdict": "MISSING",
             "reasons": ["no institutional / insider context in cached artefacts"]},
            {"name": "regime_sector", "verdict": "PASS", "reasons": ["fine"]},
        ],
    }
    reasons = G._gk_block_reasons(doc)
    assert "too_extended" in reasons
    # the MISSING gate's free text must not have become a block reason
    assert "data_missing" not in reasons
    assert "unknown" not in reasons


def test_gk_block_reasons_insufficient_data_is_data_missing():
    doc = {"final_status": "INSUFFICIENT_DATA", "blocking_reasons": [], "gates": []}
    assert G._gk_block_reasons(doc) == ["data_missing"]


# ── 2. immature forward windows excluded ──────────────────────────────────────────

def test_basket_stats_excludes_immature_and_missing(monkeypatch):
    cal, _ = _install(monkeypatch, {"SPY": 0.001, "QQQ": 0.001, "AAA": 0.004})
    spy = V._aligned(dataio.load_prices("SPY"), cal)
    qqq = V._aligned(dataio.load_prices("QQQ"), cal)
    i = len(cal) - 25
    last = len(cal) - 1
    matured = G._entry_metrics({"ticker": "AAA", "asof": cal[i].strftime("%Y-%m-%d"),
                                "reasons": ["too_extended"], "source": "x"}, cal, spy, qqq)
    immature = G._entry_metrics({"ticker": "AAA", "asof": cal[last].strftime("%Y-%m-%d"),
                                 "reasons": ["too_extended"], "source": "x"}, cal, spy, qqq)
    missing = G._entry_metrics({"ticker": "ZZZ", "asof": cal[i].strftime("%Y-%m-%d"),
                                "reasons": ["too_extended"], "source": "x"}, cal, spy, qqq)
    stats = G._basket_stats([matured, immature, missing], 5)
    assert stats["n_names"] == 3
    assert stats["n_matured"] == 1   # only the matured AAA entry counts


# ── 3. cache-depth artifact classification ────────────────────────────────────────

def test_cache_depth_artifact_flagged_when_shallow(monkeypatch):
    cal, _ = _install(
        monkeypatch,
        {"SPY": 0.001, "QQQ": 0.001, "SHAL": 0.002, "DEEP": 0.002},
        deep_counts={"DEEP": 300},
    )
    # SHAL has only 100 usable bars and no deep cache → artifact.
    monkeypatch.setattr(dataio, "load_prices",
                        lambda t, prefer_deep=True: {
                            "SPY": _frame(cal, 0.001), "QQQ": _frame(cal, 0.001),
                            "SHAL": _frame(cal, 0.002, bars=100),
                            "DEEP": _frame(cal, 0.002)}.get(t.upper()))
    spy = V._aligned(dataio.load_prices("SPY"), cal)
    qqq = V._aligned(dataio.load_prices("QQQ"), cal)
    asof = cal[len(cal) - 25].strftime("%Y-%m-%d")
    shal = G._entry_metrics({"ticker": "SHAL", "asof": asof,
                             "reasons": ["below_ma200_floor"], "source": "x"}, cal, spy, qqq)
    deep = G._entry_metrics({"ticker": "DEEP", "asof": asof,
                             "reasons": ["below_ma200_floor"], "source": "x"}, cal, spy, qqq)
    assert shal["is_cache_depth_artifact"] is True
    assert deep["is_cache_depth_artifact"] is False
    iso = G._cache_artifact_isolation([shal, deep])
    assert iso["n_blocks_citing_cache_depth_reason"] == 2
    assert iso["n_data_depth_artifact"] == 1
    assert "SHAL" in iso["artifact_tickers"]
    assert "DEEP" in iso["trustworthy_tickers"]


# ── 4. reason-label logic ─────────────────────────────────────────────────────────

def test_reason_label_good_block_when_underperforms():
    # >=5 matured, mean rel-SPY <= 0 ⇒ the block protected capital.
    s5 = {"n_matured": 8, "mean_rel_spy": -0.02, "win_rate_vs_spy": 30.0,
          "mean_mfe": 0.01, "mean_mae": -0.05}
    assert G._reason_label("too_extended", s5, cache_art=0, n=8) == "GOOD_BLOCK"


def test_reason_label_over_blocking_when_outperforms():
    s5 = {"n_matured": 8, "mean_rel_spy": 0.03, "win_rate_vs_spy": 60.0,
          "mean_mfe": 0.06, "mean_mae": -0.01}
    assert G._reason_label("too_extended", s5, cache_art=0, n=8) == "OVER_BLOCKING"


def test_reason_label_data_artifact_for_cache_reason():
    s5 = {"n_matured": 8, "mean_rel_spy": 0.05, "win_rate_vs_spy": 50.0,
          "mean_mfe": 0.1, "mean_mae": -0.02}
    assert G._reason_label("below_ma200_floor", s5, cache_art=6, n=8) == "DATA_ARTIFACT"


def test_reason_label_need_more_data_when_small():
    s5 = {"n_matured": 2, "mean_rel_spy": 0.05, "win_rate_vs_spy": 50.0,
          "mean_mfe": 0.1, "mean_mae": -0.02}
    assert G._reason_label("no_breakout", s5, cache_art=0, n=2) == "NEED_MORE_DATA"


# ── 5. blocked vs not-blocked comparison plumbing ─────────────────────────────────

def test_build_comparison_separates_blocked_and_watch(monkeypatch):
    import random
    drifts = {"SPY": 0.001, "QQQ": 0.001,
              "BLK1": 0.006, "BLK2": 0.005,    # blocked names that kept running
              "WCH1": -0.002, "WCH2": -0.001,  # watch names that lagged
              "L1": 0.0015, "L2": 0.0012, "L3": 0.0018}
    cal, _ = _install(monkeypatch, drifts)
    spy = V._aligned(dataio.load_prices("SPY"), cal)
    qqq = V._aligned(dataio.load_prices("QQQ"), cal)
    asof = cal[len(cal) - 25].strftime("%Y-%m-%d")
    blocked = [G._entry_metrics({"ticker": t, "asof": asof, "reasons": ["too_extended"],
                                 "source": "gatekeeper_snapshot"}, cal, spy, qqq)
               for t in ("BLK1", "BLK2")]
    watch = [G._entry_metrics({"ticker": t, "asof": asof, "source": "gatekeeper_snapshot"},
                              cal, spy, qqq) for t in ("WCH1", "WCH2")]
    by_h, controls = G._build_comparison(blocked, watch, cal, spy, qqq, random.Random(1))
    ph = by_h["5d"]
    assert ph["A_blocked"]["n_matured"] == 2
    assert ph["B_watch_not_blocked"]["n_matured"] == 2
    # blocked (uptrending) should beat watch (downtrending) at 5d here.
    assert ph["A_blocked"]["mean_rel_spy"] > ph["B_watch_not_blocked"]["mean_rel_spy"]
    assert controls["anchor_index"] is not None


# ── 6. cache-only MCP / dashboard summary ─────────────────────────────────────────

def test_mcp_summary_none_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(G, "OUT_JSON", tmp_path / "absent.json")
    assert G.mcp_summary() == {"present": False}


def test_mcp_summary_reads_published_sidecar():
    res = {
        "blocked_cohort": {"n_total": 150, "n_matured_primary": 122},
        "by_horizon": {"5d": {"A_blocked": {"mean_rel_spy": 0.0147},
                              "B_watch_not_blocked": {"mean_rel_spy": 0.0064}}},
        "verdict": {"gatekeeper_verdict": "OVER_BLOCKING_SHORT_HORIZON_ONLY",
                    "recommendation": "B"},
    }
    s = G.mcp_summary(res)
    assert s["present"] and s["n_blocked"] == 150 and s["blocked_5d"] == 0.0147
    assert s["watch_5d"] == 0.0064 and s["recommendation"] == "B"


def test_orchestrator_reads_precision_sidecar_cache_only(monkeypatch, tmp_path):
    from research import mcp_audit_orchestrator as O
    sidecar = tmp_path / "gatekeeper_precision_audit_latest.json"
    sidecar.write_text(json.dumps({
        "blocked_cohort": {"n_total": 12, "n_matured_primary": 9},
        "by_horizon": {"5d": {"A_blocked": {"mean_rel_spy": 0.01},
                              "B_watch_not_blocked": {"mean_rel_spy": -0.01}}},
        "verdict": {"gatekeeper_verdict": "MIXED", "recommendation": "B"}}))
    monkeypatch.setattr(O, "_read_json_sidecar",
                        lambda name: json.loads(sidecar.read_text())
                        if name == "gatekeeper_precision_audit_latest.json" else None)
    out = O._read_gatekeeper_precision()
    assert out["n_blocked"] == 12 and out["verdict"] == "MIXED" and out["recommendation"] == "B"


# ── 7. research-only invariants ───────────────────────────────────────────────────

def test_no_execution_governance_or_strategy_imports():
    src = (REPO / "research" / "gatekeeper_precision_audit.py").read_text()
    forbidden = ("from execution", "import execution", "from council", "import council",
                 "paper_governance", "order_manager", "submit_order", "close_position",
                 "from strategies", "import strategies", "decision_logger",
                 "register_strategy", "ALLOW_LIVE_CAPITAL", "PAPER_TRADING", "ALPACA_PAPER")
    for tok in forbidden:
        assert tok not in src, f"precision audit must not reference {tok!r}"


def test_no_db_mutation_or_paper_signal_writes():
    src = (REPO / "research" / "gatekeeper_precision_audit.py").read_text().upper()
    for tok in ("INSERT INTO", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
                "ALTER TABLE", "UPDATE ", "INSERT OR REPLACE", "PAPER_SIGNALS",
                "TRADE_PROPOSAL"):
        assert tok not in src, f"precision audit must not mutate the DB / write signals ({tok})"


def test_no_provider_calls():
    src = (REPO / "research" / "gatekeeper_precision_audit.py").read_text().lower()
    for tok in ("import alpaca", "from core.alpaca", "get_alpaca", "fmpclient",
                "requests.get", "requests.post", "import yfinance", "options_feed_factory",
                "data_gatekeeper"):
        assert tok not in src, f"precision audit must be cache-only (found {tok!r})"


def test_no_trade_language():
    src = (REPO / "research" / "gatekeeper_precision_audit.py").read_text()
    for tok in ("BUY ", "SELL ", "go long", "take profit"):
        assert tok not in src, f"precision audit must not contain trade language {tok!r}"
