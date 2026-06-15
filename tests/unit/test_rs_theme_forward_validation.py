"""
tests/unit/test_rs_theme_forward_validation.py — Phase 1G.11.

Covers the immutable cohort freeze (write-once), immature-window exclusion, the
LENS_READY vs BLOCKED / Alpha-board / random-control comparison plumbing, the
Gatekeeper-precision audit, the options-quality audit, the cache-only MCP /
dashboard summary, and the research-only invariants (no execution/governance
imports, no DB mutation, no paper-signal/live-capital tokens, no trade language).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from research import rs_theme_forward_validation as V  # noqa: E402
from research.scanner_truth import dataio  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────────

def _cal(n=320):
    return pd.date_range("2025-03-03", periods=n, freq="B")


def _frame(cal, drift):
    """Synthetic OHLCV frame: geometric drift per bar, liquid by construction."""
    close = 50.0 * np.cumprod(np.full(len(cal), 1.0 + drift))
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": np.full(len(cal), 5_000_000.0)}, index=cal)


def _install_prices(monkeypatch, drifts):
    """Monkeypatch dataio price access with a synthetic universe.
    `drifts` maps ticker→per-bar drift; SPY/QQQ included."""
    cal = _cal()
    frames = {t: _frame(cal, d) for t, d in drifts.items()}

    def load_prices(t, prefer_deep=True):
        return frames.get(t.upper())

    monkeypatch.setattr(dataio, "load_prices", load_prices)
    monkeypatch.setattr(dataio, "all_price_tickers", lambda: sorted(frames))
    monkeypatch.setattr(dataio, "benchmark_calendar", lambda: cal)
    return cal, frames


# ── 1. immutable cohort freeze ────────────────────────────────────────────────────

def test_freeze_cohort_is_write_once(monkeypatch, tmp_path):
    triage = tmp_path / "rs_theme_lens_triage_latest.json"
    cohort = tmp_path / "rs_theme_lens_ready_cohort_1g10.json"
    cohort_txt = tmp_path / "cohort.txt"
    triage.write_text(json.dumps({
        "asof_date": "2026-05-26", "generated_at": "2026-05-27T00:00:00+00:00",
        "market_regime": {"regime": "Bull"},
        "candidates": [
            {"ticker": "AAA", "triage_label": "LENS_READY", "source": "RS",
             "sources": ["RS"], "stage_label": "EMERGING_MOMENTUM", "theme": "other",
             "early_leader_score": 70, "rs_score": 20, "lens_state": "Bullish",
             "gatekeeper_status": "WATCH", "options_quality": "ok",
             "gate_root_cause": "passes_a_gate", "gate_rejection_reasons": [], "price": 10.0},
            {"ticker": "BBB", "triage_label": "BLOCKED", "source": "theme",
             "sources": ["theme"], "stage_label": "BREAKOUT_CONFIRMED", "theme": "other",
             "early_leader_score": 40, "rs_score": 12, "lens_state": "Neutral",
             "gatekeeper_status": "BLOCK", "options_quality": "unusable",
             "gate_root_cause": "real_quality", "gate_rejection_reasons": ["too_extended"],
             "price": 60.0}]}))
    monkeypatch.setattr(V, "TRIAGE_LATEST", triage)
    monkeypatch.setattr(V, "COHORT_PATH", cohort)
    monkeypatch.setattr(V, "COHORT_TXT", cohort_txt)

    r1 = V.freeze_cohort()
    assert r1["status"] == "frozen" and r1["n"] == 2
    first_frozen_at = json.loads(cohort.read_text())["frozen_at"]

    # Second freeze must NOT overwrite (immutable checkpoint).
    r2 = V.freeze_cohort()
    assert r2["status"] == "already_frozen"
    assert json.loads(cohort.read_text())["frozen_at"] == first_frozen_at
    # Forced re-freeze is the only escape hatch.
    r3 = V.freeze_cohort(force=True)
    assert r3["status"] == "frozen"


def test_frozen_cohort_marked_immutable(monkeypatch, tmp_path):
    triage = tmp_path / "t.json"
    cohort = tmp_path / "c.json"
    triage.write_text(json.dumps({"asof_date": "2026-05-26", "generated_at": "x",
                                  "candidates": []}))
    monkeypatch.setattr(V, "TRIAGE_LATEST", triage)
    monkeypatch.setattr(V, "COHORT_PATH", cohort)
    monkeypatch.setattr(V, "COHORT_TXT", tmp_path / "c.txt")
    V.freeze_cohort()
    payload = json.loads(cohort.read_text())
    assert payload["immutable"] is True
    assert payload["cohort_tag"] == "1G10"


# ── 2. immature forward windows excluded ──────────────────────────────────────────

def test_immature_window_returns_none(monkeypatch):
    cal, _ = _install_prices(monkeypatch, {"SPY": 0.001, "QQQ": 0.001, "AAA": 0.003})
    spy = V._aligned(dataio.load_prices("SPY"), cal)
    qqq = V._aligned(dataio.load_prices("QQQ"), cal)
    last = len(cal) - 1
    # as-of on the final bar ⇒ no forward bars ⇒ immature for every horizon.
    assert V._is_matured(cal, last, 1) is False
    assert V._fwd_metrics("AAA", cal, last, 5, spy, qqq) is None
    # as-of well before the end ⇒ matured.
    i = len(cal) - 25
    assert V._is_matured(cal, i, 20) is True
    m = V._fwd_metrics("AAA", cal, i, 5, spy, qqq)
    assert m is not None and m["fwd_return"] > 0


def test_cohort_stats_skips_immature_names(monkeypatch):
    cal, _ = _install_prices(monkeypatch, {"SPY": 0.001, "QQQ": 0.001,
                                           "AAA": 0.004, "MISSING": 0.0})
    spy = V._aligned(dataio.load_prices("SPY"), cal)
    qqq = V._aligned(dataio.load_prices("QQQ"), cal)
    i = len(cal) - 25
    # ZZZ has no price frame ⇒ excluded; AAA matures.
    stats = V._cohort_stats(["AAA", "ZZZ"], cal, i, 5, spy, qqq)
    assert stats["n_names"] == 2 and stats["n_matured"] == 1


# ── 3. comparison plumbing (LENS_READY vs BLOCKED / alpha / random) ───────────────

def _cohort_doc():
    return {
        "cohort_tag": "1G10", "asof_date": "2026-05-26", "frozen_at": "x",
        "label_distribution": {"LENS_READY": 2, "BLOCKED": 2},
        "cohort": [
            {"ticker": "WIN1", "triage_label": "LENS_READY", "options_quality": "ok",
             "gate_root_cause": "passes_a_gate"},
            {"ticker": "WIN2", "triage_label": "LENS_READY", "options_quality": "ok",
             "gate_root_cause": "passes_a_gate"},
            {"ticker": "LOSE1", "triage_label": "BLOCKED", "options_quality": "unusable",
             "gate_root_cause": "real_quality"},
            {"ticker": "LOSE2", "triage_label": "BLOCKED", "options_quality": "unusable",
             "gate_root_cause": "gate_design_mismatch"}],
    }


def test_build_by_horizon_lens_ready_beats_blocked(monkeypatch, tmp_path):
    drifts = {"SPY": 0.001, "QQQ": 0.001, "WIN1": 0.006, "WIN2": 0.005,
              "LOSE1": -0.004, "LOSE2": -0.003,
              "BRD1": 0.002, "BRD2": 0.0015, "NOISE": 0.001}
    cal, _ = _install_prices(monkeypatch, drifts)
    # Alpha board fixture (current artifact; point-in-time caveat applies).
    board = tmp_path / "alpha.json"
    board.write_text(json.dumps({"items": [{"ticker": "BRD1"}, {"ticker": "BRD2"}]}))
    monkeypatch.setattr(V, "ALPHA_BOARD", board)
    # as-of placed so 5d/10d/20d all mature.
    asof = cal[len(cal) - 25].strftime("%Y-%m-%d")
    doc = _cohort_doc(); doc["asof_date"] = asof
    spy = V._aligned(dataio.load_prices("SPY"), cal)
    qqq = V._aligned(dataio.load_prices("QQQ"), cal)
    import random
    by_h, used = V._build_by_horizon(doc, cal, spy, qqq, random.Random(1))
    ph = by_h["5d"]
    assert ph["matured"] is True
    a = ph["A_lens_ready"]["mean_rel_spy"]
    b = ph["B_blocked"]["mean_rel_spy"]
    c = ph["C_alpha_board"]["mean_rel_spy"]
    assert a is not None and b is not None and c is not None
    assert a > b                       # LENS_READY outperforms blocked
    assert used["random_control"]      # a random control cohort was drawn
    # comparison question wiring
    gk = V._gatekeeper_precision(doc, by_h)
    q = V._comparison_questions(by_h, gk)
    assert q["q1_lens_ready_vs_blocked"] == "outperform"
    assert q["q2_lens_ready_vs_alpha_board"] in ("outperform", "underperform", "tie")
    assert q["q3_lens_ready_vs_random"] in ("outperform", "underperform", "tie")


def test_comparison_questions_need_more_data_when_immature():
    by_h = {"5d": {"matured": False}}
    q = V._comparison_questions(by_h, {})
    assert q["primary_horizon_matured"] is False
    assert q["q1_lens_ready_vs_blocked"].startswith("NEED_MORE_DATA")


# ── 4. Gatekeeper precision audit ─────────────────────────────────────────────────

def test_gatekeeper_precision_blocks_look_correct():
    doc = _cohort_doc()
    by_h = {"5d": {"matured": True,
                   "A_lens_ready": {"n_matured": 2, "mean_rel_spy": 0.03, "mean_mae": -0.01},
                   "B_blocked": {"n_matured": 2, "mean_rel_spy": -0.02, "mean_mae": -0.05}}}
    gk = V._gatekeeper_precision(doc, by_h)
    assert gk["precision_verdict"] == "BLOCKS_LOOK_CORRECT"
    assert gk["blocked_underperforms_lens_ready"] is True
    assert gk["block_reason_buckets"]["real_quality_rejection"] == 1
    assert gk["block_reason_buckets"]["gate_design_mismatch"] == 1


def test_gatekeeper_precision_may_reject_winners():
    doc = _cohort_doc()
    by_h = {"5d": {"matured": True,
                   "A_lens_ready": {"n_matured": 2, "mean_rel_spy": 0.01, "mean_mae": -0.02},
                   "B_blocked": {"n_matured": 2, "mean_rel_spy": 0.05, "mean_mae": -0.01}}}
    gk = V._gatekeeper_precision(doc, by_h)
    assert gk["precision_verdict"] == "BLOCKS_MAY_REJECT_WINNERS"


def test_gatekeeper_precision_need_more_data_when_immature():
    doc = _cohort_doc()
    gk = V._gatekeeper_precision(doc, {"5d": {"matured": False}})
    assert gk["precision_verdict"] == "NEED_MORE_DATA"


# ── 5. options-quality audit ──────────────────────────────────────────────────────

def test_options_audit_groups_by_label(monkeypatch):
    drifts = {"SPY": 0.001, "QQQ": 0.001, "WIN1": 0.006, "WIN2": 0.005,
              "LOSE1": -0.004, "LOSE2": -0.003}
    cal, _ = _install_prices(monkeypatch, drifts)
    doc = _cohort_doc()
    doc["asof_date"] = cal[len(cal) - 25].strftime("%Y-%m-%d")
    spy = V._aligned(dataio.load_prices("SPY"), cal)
    qqq = V._aligned(dataio.load_prices("QQQ"), cal)
    out = V._options_quality_audit(doc, cal, spy, qqq)
    assert out["p_l_modeled"] is False
    assert out["n_candidates_with_options_label"] == 4
    assert "ok" in out["by_label"] and out["by_label"]["ok"]["n_matured"] == 2


# ── 6. verdict + maintenance recommendation ───────────────────────────────────────

def test_verdict_need_more_data_when_immature():
    doc = {"asof_date": "2026-05-26"}
    v, _ = V._verdict({"5d": {"matured": False}}, doc)
    assert v == "NEED_MORE_DATA"


def test_maintenance_recommendation_immature_recommends_nightly_triage():
    mr = V._maintenance_recommendation("NEED_MORE_DATA", {}, {"asof_date": "x"})
    assert mr["primary_recommendation"] == "B"
    assert {o["id"] for o in mr["options"]} == {"A", "B", "C", "D", "E"}
    for o in mr["options"]:
        assert all(k in o for k in
                   ("evidence", "provider_cost", "false_positive_risk",
                    "overfitting_risk", "expected_benefit"))


# ── 7. MCP / dashboard cache-only summary ─────────────────────────────────────────

def test_mcp_summary_none_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(V, "OUT_JSON", tmp_path / "absent.json")
    assert V.mcp_summary()["present"] is False
    out = tmp_path / "fwd.json"
    out.write_text(json.dumps({
        "cohort_tag": "1G10", "verdict": "NEED_MORE_DATA",
        "n_matured_lens_ready_primary": 0,
        "by_horizon": {"5d": {"A_lens_ready": {"mean_rel_spy": None}}},
        "comparison": {"values": {"alpha_board_rel_spy": None, "random_rel_spy": None}}}))
    monkeypatch.setattr(V, "OUT_JSON", out)
    s = V.mcp_summary()
    assert s["present"] and s["cohort"] == "1G10" and s["verdict"] == "NEED_MORE_DATA"


def test_orchestrator_reads_forward_sidecar_cache_only(monkeypatch, tmp_path):
    from research import mcp_audit_orchestrator as ORCH
    monkeypatch.setattr(ORCH, "_repo_root", lambda: tmp_path)
    (tmp_path / "cache" / "research").mkdir(parents=True)
    assert ORCH._read_rs_theme_forward() is None  # missing ⇒ None-safe
    (tmp_path / "cache" / "research" / "rs_theme_forward_validation_latest.json").write_text(
        json.dumps({"cohort_tag": "1G10", "verdict": "NEED_MORE_DATA",
                    "n_matured_lens_ready_primary": 0,
                    "by_horizon": {"5d": {"A_lens_ready": {"mean_rel_spy": None}}},
                    "comparison": {"values": {}}}))
    out = ORCH._read_rs_theme_forward()
    assert out["cohort"] == "1G10" and out["verdict"] == "NEED_MORE_DATA"
    # an errored sidecar is treated as absent
    (tmp_path / "cache" / "research" / "rs_theme_forward_validation_latest.json").write_text(
        json.dumps({"error": "cohort_not_frozen"}))
    assert ORCH._read_rs_theme_forward() is None


# ── 8. research-only invariants ────────────────────────────────────────────────────

def test_no_execution_governance_or_strategy_imports():
    src = (REPO / "research" / "rs_theme_forward_validation.py").read_text()
    forbidden = ("from execution", "import execution", "from council", "import council",
                 "paper_governance", "order_manager", "submit_order", "close_position",
                 "from strategies", "import strategies", "decision_logger",
                 "register_strategy", "ALLOW_LIVE_CAPITAL", "PAPER_TRADING", "ALPACA_PAPER")
    for tok in forbidden:
        assert tok not in src, f"forward validation must not reference {tok!r}"


def test_no_db_mutation_or_paper_signal_writes():
    src = (REPO / "research" / "rs_theme_forward_validation.py").read_text().upper()
    for tok in ("INSERT INTO", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
                "ALTER TABLE", "UPDATE ", "INSERT OR REPLACE", "PAPER_SIGNALS",
                "TRADE_PROPOSAL"):
        assert tok not in src, f"forward validation must not mutate the DB / write signals ({tok})"


def test_no_provider_calls():
    # Target real call/import patterns, not prose mentions of provider cost.
    src = (REPO / "research" / "rs_theme_forward_validation.py").read_text().lower()
    for tok in ("import alpaca", "from core.alpaca", "get_alpaca", "fmpclient",
                "requests.get", "requests.post", "import yfinance", "options_feed_factory",
                "data_gatekeeper"):
        assert tok not in src, f"forward validation must be cache-only (found {tok!r})"


def test_no_trade_language():
    src = (REPO / "research" / "rs_theme_forward_validation.py").read_text()
    for tok in ("BUY ", "SELL ", "SHORT ", "go long", "take profit"):
        assert tok not in src, f"forward validation must not contain trade language {tok!r}"
