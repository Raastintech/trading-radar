"""
tests/unit/test_rs_theme_lens_triage.py — Phase 1G.9.

Covers candidate dedupe/overlap, the triage-label decision tree (NEEDS_LENS,
NEEDS_GATEKEEPER, TOO_EXTENDED, BLOCKED), Voyager/Sniper rejection counting +
root-cause decomposition, idempotent forward historizing, the MCP/dashboard
cache-only summary, and the research-only invariants (no execution/governance
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

from research import rs_theme_lens_triage as T  # noqa: E402
from research.scanner_truth import dataio  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _feat(bars=300, r5=0.0, ext_ema20=0.02):
    return {"bars": bars, "r5": r5, "ext_ema20": ext_ema20, "rs20_vs_spy": 0.05,
            "pullback_from_high": 0.0, "theme": "semiconductors", "price": 50.0}


def _lens(exists=True, stale=False, constructive=True, label="Bullish", oq="ok"):
    return {"exists": exists, "age_hours": 1.0, "label": label, "stale": stale,
            "constructive": constructive, "options_quality": oq}


def _gk(exists=True, stale=False, blocked=False, status="WATCH"):
    return {"exists": exists, "age_hours": 1.0, "status": status, "stale": stale,
            "blocked": blocked}


# ── 1. candidate dedupe / overlap ──────────────────────────────────────────────

def test_candidates_dedupe_to_overlap():
    rs_doc = {"live": {"top_rs_leaders": [{"ticker": "NVDA", "rs20": 0.4, "theme": "semiconductors"},
                                          {"ticker": "AAA", "rs20": 0.1, "theme": "other"}]}}
    theme_doc = {"themes": {"semiconductors": {"theme_state": "LEADING",
                                               "top_leaders": ["NVDA", "WOLF"]}}}
    proposed = [{"ticker": "NVDA", "stage_label": "EMERGING_MOMENTUM", "early_leader_score": 80,
                 "theme": "semiconductors"}]
    cand = T._assemble_candidates(rs_doc, theme_doc, proposed)
    # NVDA appears in all three sources → exactly one entry, marked overlap.
    assert "NVDA" in cand and len(cand["NVDA"]["sources"]) == 3
    assert T._source_label(cand["NVDA"]["sources"]) == "overlap"
    assert T._source_label(cand["AAA"]["sources"]) == "RS"
    assert T._source_label(cand["WOLF"]["sources"]) == "theme"


def test_benchmarks_excluded_from_candidates():
    rs_doc = {"live": {"top_rs_leaders": [{"ticker": "SPY", "rs20": 0.0}]}}
    cand = T._assemble_candidates(rs_doc, {}, [])
    assert "SPY" not in cand


# ── 2. triage-label decision tree ──────────────────────────────────────────────

def test_missing_lens_becomes_needs_lens():
    label = T._triage_label(_feat(), "EMERGING_MOMENTUM", {"reason_codes": []},
                            _lens(exists=False), _gk(exists=False))
    assert label == "NEEDS_LENS"


def test_stale_lens_becomes_needs_lens():
    label = T._triage_label(_feat(), "EMERGING_MOMENTUM", {"reason_codes": []},
                            _lens(exists=True, stale=True), _gk(exists=False))
    assert label == "NEEDS_LENS"


def test_fresh_constructive_lens_missing_gatekeeper_needs_gatekeeper():
    label = T._triage_label(_feat(), "EMERGING_MOMENTUM", {"reason_codes": []},
                            _lens(), _gk(exists=False))
    assert label == "NEEDS_GATEKEEPER"


def test_stale_gatekeeper_becomes_needs_gatekeeper():
    # Lens fresh+constructive, gatekeeper exists but stale ⇒ route to gatekeeper.
    label = T._triage_label(_feat(), "EMERGING_MOMENTUM", {"reason_codes": []},
                            _lens(), _gk(exists=True, stale=True))
    assert label == "NEEDS_GATEKEEPER"


def test_both_fresh_constructive_unblocked_lens_ready():
    label = T._triage_label(_feat(), "BREAKOUT_CONFIRMED", {"reason_codes": []},
                            _lens(), _gk())
    assert label == "LENS_READY"


def test_too_extended_by_stage():
    label = T._triage_label(_feat(), "PARABOLIC", {"reason_codes": []},
                            _lens(), _gk())
    assert label == "TOO_EXTENDED"


def test_too_extended_by_extension_score():
    sc = {"reason_codes": ["EXTENDED_EMA20"], "late_extension_score": 90.0}
    label = T._triage_label(_feat(ext_ema20=0.25), "EMERGING_MOMENTUM", sc,
                            _lens(exists=False), _gk(exists=False))
    assert label == "TOO_EXTENDED"


def test_blocked_gatekeeper_becomes_blocked():
    label = T._triage_label(_feat(), "EMERGING_MOMENTUM", {"reason_codes": []},
                            _lens(), _gk(blocked=True, status="BLOCK"))
    assert label == "BLOCKED"


def test_not_enough_data_when_shallow():
    label = T._triage_label(_feat(bars=30), "EMERGING_MOMENTUM", {"reason_codes": []},
                            _lens(), _gk())
    assert label == "NOT_ENOUGH_DATA"
    assert T._triage_label(None, None, {}, _lens(exists=False), _gk(exists=False)) == "NOT_ENOUGH_DATA"


def test_nonconstructive_fresh_lens_becomes_noise():
    # Lens evaluated and found no edge ⇒ triage rightly drops it as noise.
    label = T._triage_label(_feat(), "EMERGING_MOMENTUM", {"reason_codes": []},
                            _lens(constructive=False, label="Neutral"), _gk(exists=False))
    assert label == "LOW_QUALITY_NOISE"


# ── 3. lens / gatekeeper info parsing (freshness) ──────────────────────────────

def test_lens_info_missing_and_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "RC", tmp_path)
    now = pd.Timestamp("2026-05-27T00:00:00+00:00").to_pydatetime()
    # missing
    info = T._lens_info("ZZZ", now)
    assert info["exists"] is False and info["stale"] is True
    # stale (built 30 days ago)
    (tmp_path / "stock_lens_OLD_latest.json").write_text(json.dumps(
        {"built_at": "2026-04-27T00:00:00", "label": "Bullish", "layers": {}}))
    info = T._lens_info("OLD", now)
    assert info["exists"] is True and info["stale"] is True
    # fresh + constructive + options
    (tmp_path / "stock_lens_NEW_latest.json").write_text(json.dumps(
        {"built_at": "2026-05-26T12:00:00", "label": "Bullish but not buyable yet",
         "layers": {"options": {"available": True, "spread_quality": "ok"}}}))
    info = T._lens_info("NEW", now)
    assert info["exists"] and not info["stale"] and info["constructive"]
    assert info["options_quality"] == "ok"


def test_gatekeeper_info_block_and_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "RC", tmp_path)
    now = pd.Timestamp("2026-05-27T00:00:00+00:00").to_pydatetime()
    (tmp_path / "executive_gatekeeper_BLK_latest.json").write_text(json.dumps(
        {"generated_at": "2026-05-26T12:00:00", "final_status": "BLOCK",
         "blocking_reasons": ["x"]}))
    info = T._gatekeeper_info("BLK", now)
    assert info["exists"] and info["blocked"] and not info["stale"]
    (tmp_path / "executive_gatekeeper_STALE_latest.json").write_text(json.dumps(
        {"generated_at": "2026-05-01T00:00:00", "final_status": "WATCH"}))
    info = T._gatekeeper_info("STALE", now)
    assert info["exists"] and info["stale"] and not info["blocked"]


# ── 4. Voyager/Sniper rejection counting + decomposition ───────────────────────

def _synth_df(n=120, trend=0.0, vol=1e6):
    cal = pd.date_range("2025-06-01", periods=n, freq="B")
    close = np.linspace(20, 20 * (1 + trend), n)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": [vol] * n}, index=cal)


def test_gate_eval_counts_rejections_and_classifies(monkeypatch):
    # Shallow 120-bar history ⇒ fails voyager 260-bar gate; flat trend ⇒ no breakout.
    df = _synth_df(n=120, trend=0.0)
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: df)
    res = T._gate_eval("AAA", df.index[-1])
    assert res["evaluable"] is True
    assert res["killed"] is True
    assert res["reasons"]  # at least one rejection reason recorded
    assert res["root_cause"] in ("cache_artifact", "gate_design_mismatch",
                                 "real_quality", "unknown")


def test_gate_eval_unevaluable_when_no_prices(monkeypatch):
    monkeypatch.setattr(dataio, "load_prices", lambda t, prefer_deep=True: None)
    assert T._gate_eval("ZZZ", pd.Timestamp("2026-05-26"))["evaluable"] is False


def test_gate_decomposition_buckets_and_possibly_valid():
    rows = [
        {"killed_by_gates": True, "gate_rejection_reasons": ["insufficient_history_260"],
         "gate_root_cause": "cache_artifact"},
        {"killed_by_gates": True, "gate_rejection_reasons": ["no_breakout", "no_atr_contraction"],
         "gate_root_cause": "gate_design_mismatch"},
        {"killed_by_gates": True, "gate_rejection_reasons": ["too_extended"],
         "gate_root_cause": "real_quality"},
        {"killed_by_gates": False, "gate_rejection_reasons": [], "gate_root_cause": "passes_a_gate"},
    ]
    d = T._gate_decomposition(rows)
    assert d["n_killed_by_both_gates"] == 3
    assert d["root_cause_counts"]["cache_or_data_depth_artifact"] == 1
    assert d["root_cause_counts"]["gate_design_mismatch"] == 1
    assert d["root_cause_counts"]["real_quality_rejection"] == 1
    # cache + gate-design = possibly-valid early candidates
    assert d["possibly_valid_early_candidates"] == 2
    assert d["bucketed_rejection_counts"]["insufficient_history_260"] == 1
    assert d["bucketed_rejection_counts"]["no_breakout"] == 1


# ── 5. forward historizer is idempotent per date/ticker ────────────────────────

def test_historizer_idempotent(monkeypatch, tmp_path):
    hist = tmp_path / "rs_theme_lens_triage_history.jsonl"
    monkeypatch.setattr(T, "TRIAGE_HISTORY", hist)
    rows = [{"ticker": "AAA", "triage_label": "NEEDS_LENS", "source": "RS",
             "stage_label": "EMERGING_MOMENTUM", "early_leader_score": 70,
             "rs_score": 20, "theme": "semiconductors", "lens_state": None,
             "gatekeeper_status": None, "options_quality": None,
             "gate_rejection_reasons": ["no_breakout"], "price": 12.0}]
    r1 = T._historize(rows, "2026-05-26", "2026-05-26T00:00:00+00:00")
    assert r1["rows_written"] == 1 and r1["already_present"] is False
    # second run, same date ⇒ no new rows
    r2 = T._historize(rows, "2026-05-26", "2026-05-26T01:00:00+00:00")
    assert r2["rows_written"] == 0 and r2["already_present"] is True
    # new date ⇒ writes again
    r3 = T._historize(rows, "2026-05-27", "2026-05-27T00:00:00+00:00")
    assert r3["rows_written"] == 1
    lines = [json.loads(x) for x in hist.read_text().splitlines() if x.strip()]
    assert len(lines) == 2
    # no future-outcome fields leak into the ledger
    assert all("forward" not in k and "outcome" not in k for row in lines for k in row)


# ── 6. MCP / dashboard cache-only summary ──────────────────────────────────────

def test_mcp_summary_none_safe(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "RC", tmp_path)
    assert T.mcp_summary()["present"] is False
    (tmp_path / "rs_theme_lens_triage_latest.json").write_text(json.dumps(
        {"verdict": "NEED_MORE_DATA",
         "summary": {"candidates_evaluated": 5, "research_watch": 1, "needs_lens": 2,
                     "needs_gatekeeper": 1, "too_extended": 0, "blocked": 0}}))
    s = T.mcp_summary()
    assert s["present"] and s["evaluated"] == 5 and s["verdict"] == "NEED_MORE_DATA"


def test_orchestrator_reads_triage_sidecar_cache_only(monkeypatch, tmp_path):
    from research import mcp_audit_orchestrator as ORCH
    monkeypatch.setattr(ORCH, "_repo_root", lambda: tmp_path)
    (tmp_path / "cache" / "research").mkdir(parents=True)
    assert ORCH._read_rs_theme_triage() is None  # missing ⇒ None-safe
    (tmp_path / "cache" / "research" / "rs_theme_lens_triage_latest.json").write_text(
        json.dumps({"verdict": "PROMISING_RESEARCH_SURFACE",
                    "summary": {"candidates_evaluated": 30, "research_watch": 0,
                                "needs_lens": 22, "needs_gatekeeper": 5,
                                "too_extended": 0, "blocked": 1}}))
    out = ORCH._read_rs_theme_triage()
    assert out["evaluated"] == 30 and out["verdict"] == "PROMISING_RESEARCH_SURFACE"


# ── 7. research-only invariants ────────────────────────────────────────────────

def test_no_execution_governance_or_strategy_imports():
    src = (REPO / "research" / "rs_theme_lens_triage.py").read_text()
    forbidden = ("from execution", "import execution", "from council", "import council",
                 "paper_governance", "order_manager", "submit_order", "close_position",
                 "from strategies", "import strategies", "decision_logger",
                 "ALLOW_LIVE_CAPITAL", "PAPER_TRADING", "ALPACA_PAPER")
    for tok in forbidden:
        assert tok not in src, f"triage must not reference {tok!r}"


def test_no_db_mutation_or_paper_signal_writes():
    src = (REPO / "research" / "rs_theme_lens_triage.py").read_text().upper()
    for tok in ("INSERT INTO", "DELETE FROM", "DROP TABLE", "CREATE TABLE",
                "ALTER TABLE", "UPDATE ", "INSERT OR REPLACE", "PAPER_SIGNALS"):
        assert tok not in src, f"triage must not mutate the DB / write signals ({tok})"


def test_no_trade_language():
    # Routing verbs only — no buy/sell/short order language anywhere in output paths.
    src = (REPO / "research" / "rs_theme_lens_triage.py").read_text()
    # Whole-word checks so substrings like 'BUYABLE' (a lens label) don't false-positive
    # is not needed here: we forbid the explicit trade tokens outright.
    for tok in ("BUY ", "SELL ", "SHORT ", "go long", "take profit"):
        assert tok not in src, f"triage must not contain trade language {tok!r}"
