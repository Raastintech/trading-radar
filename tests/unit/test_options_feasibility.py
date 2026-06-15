"""Phase 1J.0 options feasibility audit guardrail tests.

The earnings-blackout and clustering-throttle tests from the task list apply
to the Task 7 skeleton (research/options_premium_research_lab.py), which was
NOT built because the decision gate returned NO. They become applicable only
if the gate ever returns YES / strong PARTIAL.
"""
from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import options_data_inventory as inv  # noqa: E402


# ---------------------------------------------------------------------------
# Inventory parser
# ---------------------------------------------------------------------------

def test_jsonl_parser_counts_bad_lines(tmp_path):
    p = tmp_path / "iv.jsonl"
    p.write_text(
        '{"ticker": "SPY", "date": "2026-06-01", "bucket": "front", "atm_iv_blend": 0.18}\n'
        "THIS IS NOT JSON\n"
        '{"ticker": "SPY", "date": "2026-06-02", "bucket": "front", "atm_iv_blend": 0.19}\n'
        "\n",
        encoding="utf-8",
    )
    out = inv._read_jsonl(p)
    assert out["exists"] is True
    assert len(out["rows"]) == 2
    assert out["bad_lines"] == 1

    missing = inv._read_jsonl(tmp_path / "absent.jsonl")
    assert missing["exists"] is False
    assert missing["rows"] == []


def test_iv_history_inventory_reads_synthetic_file(monkeypatch, tmp_path):
    p = tmp_path / "iv.jsonl"
    rows = [
        {"ticker": "SPY", "date": f"2026-05-{d:02d}", "bucket": "front",
         "atm_call_iv": 0.2, "atm_put_iv": 0.21, "atm_iv_blend": 0.205, "iv_skew": 1.05}
        for d in range(1, 11)
    ] + [{"ticker": "AAPL", "date": "2026-05-01", "bucket": "swing", "atm_iv_blend": 0.3}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(inv, "IV_HISTORY_PATH", p)

    out = inv.iv_history_inventory()

    assert out["row_count"] == 11
    assert out["symbols_covered"] == 2
    assert out["deepest_symbols"][0] == {"symbol": "SPY", "iv_days": 10}
    assert out["has_bid_ask"] is False
    assert out["has_greeks"] is False
    assert out["has_strikes"] is False


# ---------------------------------------------------------------------------
# Point-in-time check
# ---------------------------------------------------------------------------

def test_point_in_time_check_flags_future_dates():
    rows = [{"date": "2026-06-01"}, {"date": "2026-06-02"}]
    ok = inv.point_in_time_check(rows, date_key="date", today="2026-06-12")
    assert ok["point_in_time_forward_accumulating"] is True
    assert ok["future_dated_rows"] == 0
    assert ok["historical_backfill_available"] is False

    rows.append({"date": "2027-01-01"})
    bad = inv.point_in_time_check(rows, date_key="date", today="2026-06-12")
    assert bad["future_dated_rows"] == 1
    assert bad["point_in_time_forward_accumulating"] is False


# ---------------------------------------------------------------------------
# IVR feasibility labels
# ---------------------------------------------------------------------------

def _iv_inv(days, first="2026-01-02", last="2026-06-12"):
    return {
        "per_ticker_days": {"SPY": days},
        "per_ticker_span": {"SPY": (first, last)},
    }


def test_ivr_labels_at_thresholds():
    not_valid = inv.ivr_feasibility(_iv_inv(24, first="2026-05-08"))
    assert not_valid["overall_verdict"] == inv.IVR_NOT_VALID
    assert not_valid["rows"]["SPY"]["verdict"] == inv.IVR_NOT_VALID

    partial = inv.ivr_feasibility(_iv_inv(60, first="2026-03-20"))
    assert partial["overall_verdict"] == inv.IVR_PARTIAL

    feasible = inv.ivr_feasibility(_iv_inv(120, first="2026-01-02"))
    assert feasible["rows"]["SPY"]["iv_days"] == 120
    assert feasible["overall_verdict"] == inv.IVR_FEASIBLE


def test_ivr_feasible_requires_low_missing_rate():
    # 120 observed days over a much longer span -> high missing rate -> PARTIAL.
    sparse = inv.ivr_feasibility(_iv_inv(120, first="2025-01-02", last="2026-06-12"))
    assert sparse["rows"]["SPY"]["verdict"] == inv.IVR_PARTIAL


def test_ivr_never_fabricates_history():
    empty = inv.ivr_feasibility({"per_ticker_days": {}, "per_ticker_span": {}})
    assert empty["overall_verdict"] == inv.IVR_NOT_VALID
    assert empty["best_history_days"] == 0


# ---------------------------------------------------------------------------
# Decision rules
# ---------------------------------------------------------------------------

def test_decision_is_no_without_chains_and_young_iv():
    chains = {"persisted_chain_snapshot_count": 0}
    ivr = {"overall_verdict": inv.IVR_NOT_VALID, "best_history_days": 24}
    out = inv.final_decision({}, chains, ivr)
    assert out["answer"] == "NO"
    assert out["skeleton_built"] is False


def test_decision_partial_when_iv_history_matures_but_no_chains():
    chains = {"persisted_chain_snapshot_count": 0}
    ivr = {"overall_verdict": inv.IVR_PARTIAL, "best_history_days": 80}
    out = inv.final_decision({}, chains, ivr)
    assert out["answer"] == "PARTIAL"
    assert out["partial_conditions"]["trade_level_backtest_possible"] is False


def test_strategy_map_never_claims_trade_level_feasibility_without_chains():
    chains = {"persisted_chain_snapshot_count": 0}
    ivr = {"overall_verdict": inv.IVR_NOT_VALID, "best_history_days": 24}
    fmap = inv.strategy_feasibility_map({}, chains, ivr)
    for key in ("A_iron_condor_30_60_dte", "B_put_credit_spread_30_60_dte", "C_cash_secured_put", "D_covered_call"):
        assert fmap[key]["verdict"] == inv.NOT_FEASIBLE
    assert fmap["dte_backtesting"]["30_60_dte_possible"] is False
    assert fmap["dte_backtesting"]["45_dte_possible"] is False


def test_skeleton_module_was_not_built():
    assert not (Path(inv.ROOT) / "research" / "options_premium_research_lab.py").exists()


# ---------------------------------------------------------------------------
# Safety guardrails
# ---------------------------------------------------------------------------

def test_no_execution_or_provider_imports_in_inventory_module():
    forbidden_import_roots = {"execution", "governance", "broker", "live_capital", "council", "core"}
    forbidden_calls = (
        "create_paper_signal(",
        "emit_paper_signal(",
        "insert_paper_signal(",
        "create_trade_proposal(",
        "emit_trade_proposal(",
        "submit_buy_order",
        "submit_sell_order",
        "close_position(",
        "strategy_registry.register",
        "load_options_feed(",
        "get_alpaca(",
    )
    text = Path(inv.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
            assert roots.isdisjoint(forbidden_import_roots)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in forbidden_import_roots
    for needle in forbidden_calls:
        assert needle not in text, needle


def test_production_registry_unchanged_and_short_a_frozen_after_phase_1j0():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False
    assert "OPTIONS_PREMIUM" not in reg.SLEEVE_REGISTRY
