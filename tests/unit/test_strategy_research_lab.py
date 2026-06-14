"""Phase 1H Strategy Research Lab guardrail tests.

Synthetic where possible so the tests verify mechanics without depending on
the current market cache contents.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import mcp_audit_orchestrator as mcp  # noqa: E402
from research import strategy_lab_data as data  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402
from research import strategy_threshold_sweep as sweep  # noqa: E402
from research import strategy_walk_forward as wf  # noqa: E402


def _bars(start: str, n: int, *, close0: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=n)
    closes = [close0 + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1_000_000 for _ in closes],
        },
        index=idx,
    )


def test_load_price_frame_asof_excludes_future_bars(monkeypatch):
    full = _bars("2026-01-01", 10)
    monkeypatch.setattr(data, "_load_merged_prices", lambda ticker: full.copy())

    frame = data.load_price_frame_asof("TEST", "2026-01-07")

    assert frame is not None
    assert frame.index.max() <= pd.Timestamp("2026-01-07")
    assert data.validate_no_future_bars(frame, "2026-01-07")["ok"] is True


def test_compute_features_asof_uses_past_bars_only(monkeypatch):
    full = _bars("2026-01-01", 90)
    monkeypatch.setattr(data, "_load_merged_prices", lambda ticker: full.copy())
    monkeypatch.setattr(data, "metadata_for_ticker", lambda ticker: {
        "sector": "Technology",
        "industry": "Software",
        "theme": "semiconductors",
        "market_cap": None,
        "metadata_reliability": data.CURRENT_METADATA_APPROXIMATION,
    })
    data.compute_features_asof.cache_clear()

    features = data.compute_features_asof("TEST", "2026-03-02")

    assert features is not None
    assert features["bar_date"] <= "2026-03-02"
    assert features["data_reliability"]["price"] == data.TRUE_POINT_IN_TIME
    assert features["data_reliability"]["stock_lens"] == data.NOT_RETAINED
    assert features["data_reliability"]["gatekeeper"] == data.NOT_RETAINED


def test_forward_window_is_strictly_after_anchor(monkeypatch):
    full = _bars("2026-01-01", 15)
    monkeypatch.setattr(data, "_full_frame", lambda ticker: full.copy())

    fw = data.get_forward_window("TEST", "2026-01-09", 5)

    assert not fw.empty
    assert fw.index.min() > pd.Timestamp("2026-01-09")
    assert len(fw) <= 6


def test_transaction_costs_reduce_net_return(monkeypatch):
    future = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.0, 102.0, 103.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [101.0, 102.0, 103.0, 104.0, 105.0],
            "volume": [1_000_000] * 5,
        },
        index=pd.bdate_range("2026-01-05", periods=5),
    )
    monkeypatch.setattr(lab.d, "get_forward_window", lambda ticker, asof, horizon: future.copy())
    sig = lab.Signal("TEST", "AAA", "2026-01-02", "long", 1.0, [], {})
    params = lab.StrategyParams(max_hold_days=5, stop_loss_pct=0.50, profit_target_pct=0.50)

    no_cost = lab.simulate_trade(sig, params=params, cost_model=lab.NO_COST)
    base_cost = lab.simulate_trade(sig, params=params, cost_model=lab.BASE_COST)

    assert no_cost is not None and base_cost is not None
    assert base_cost["cost_return"] > 0
    assert base_cost["net_return"] < no_cost["net_return"]


def test_random_baseline_reproducible():
    features = [
        {"ticker": f"T{i}", "asof": "2026-01-02", "price": 10.0, "avg_dvol20": 10_000_000}
        for i in range(10)
    ]

    a = lab.select_random_liquid(features, "2026-01-02", 4, 123)
    b = lab.select_random_liquid(features, "2026-01-02", 4, 123)

    assert [s.ticker for s in a] == [s.ticker for s in b]


def test_short_returns_are_signed_correctly(monkeypatch):
    future = pd.DataFrame(
        {
            "open": [100.0, 97.0, 95.0, 94.0, 93.0],
            "high": [101.0, 98.0, 96.0, 95.0, 94.0],
            "low": [99.0, 96.0, 94.0, 93.0, 92.0],
            "close": [97.0, 95.0, 94.0, 93.0, 92.0],
            "volume": [1_000_000] * 5,
        },
        index=pd.bdate_range("2026-01-05", periods=5),
    )
    monkeypatch.setattr(lab.d, "get_forward_window", lambda ticker, asof, horizon: future.copy())
    sig = lab.Signal("QQQ_TECH_TACTICAL_SHORT", "AAA", "2026-01-02", "short", 1.0, [], {})
    params = lab.StrategyParams(max_hold_days=5, stop_loss_pct=0.50, profit_target_pct=0.50)

    trade = lab.simulate_trade(sig, params=params, cost_model=lab.NO_COST)

    assert trade is not None
    assert trade["raw_return"] > 0
    assert trade["net_return"] > 0


def test_walk_forward_split_has_distinct_test_block(monkeypatch):
    cal = pd.bdate_range("2025-01-01", periods=180)
    monkeypatch.setattr(wf.d, "benchmark_calendar", lambda: cal)
    monkeypatch.setattr(wf.d, "trading_dates_between", lambda start, end: list(pd.bdate_range(start, end)))

    splits = wf.make_walk_forward_splits(start="2025-01-01", end="2025-09-09", min_block_days=30)

    assert splits["splits"]["train"]["end"] < splits["splits"]["validation"]["start"]
    assert splits["splits"]["validation"]["end"] < splits["splits"]["test"]["start"]


def test_threshold_sweep_does_not_select_on_test(monkeypatch):
    splits = {
        "train": {"start": "2025-01-01", "end": "2025-01-31"},
        "validation": {"start": "2025-02-01", "end": "2025-02-28"},
        "test": {"start": "2025-03-01", "end": "2025-03-31"},
    }
    monkeypatch.setattr(sweep.wf, "make_walk_forward_splits", lambda: {"mode": "test", "splits": splits})
    monkeypatch.setattr(sweep, "limited_grid", lambda: [lab.StrategyParams(), lab.StrategyParams(sector_rs_threshold=0.12)])

    def fake_run(split_name, split, params, config):
        pid_bonus = 1.0 if params.sector_rs_threshold > 0.1 else 0.0
        exp = 0.01 + pid_bonus if split_name == "validation" else 0.01
        if split_name == "test":
            exp = -0.05 if pid_bonus else 0.20
        metrics = {
            "trade_count": 30,
            "expectancy": exp,
            "rel_spy": exp,
            "rel_qqq": exp,
            "max_drawdown": -0.01,
        }
        return {"by_cost": {"base_cost": {v: dict(metrics) for v in sweep.SWEEP_VARIANTS}}}

    monkeypatch.setattr(sweep, "_run", fake_run)

    result = sweep.build_threshold_sweep(config=lab.BacktestConfig(universe_cap=1))

    row = result["selected_by_variant"]["RECALL_SHADOW_RS_MOMENTUM"]
    assert row["selected_on"] == "validation_score_after_complexity_penalty"
    assert row["test_used_for_selection"] is False
    assert result["production_thresholds_mutated"] is False


def test_strategy_modules_do_not_import_execution_or_create_signals():
    forbidden_import_roots = {"execution", "governance", "broker", "live_capital"}
    forbidden_activation_calls = (
        "create_paper_signal(",
        "emit_paper_signal(",
        "insert_paper_signal(",
        "create_trade_proposal(",
        "emit_trade_proposal(",
        "strategy_registry.register",
    )
    for module in (data, lab, wf, sweep):
        text = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
                assert roots.isdisjoint(forbidden_import_roots)
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in forbidden_import_roots
        for needle in forbidden_activation_calls:
            assert needle not in text


def test_mcp_strategy_lab_reads_cache_only(monkeypatch):
    lab_sidecar = {
        "status": "RESEARCH_ONLY",
        "best_variant": "RECALL_SHADOW_RS_MOMENTUM",
        "best_variant_verdict": "NEED_MORE_DATA",
        "ranked_variants": [{"variant": "RECALL_SHADOW_RS_MOMENTUM", "trade_count": 22}],
        "windows": {
            "recent": {
                "by_cost": {
                    "base_cost": {
                        "RECALL_SHADOW_RS_MOMENTUM": {
                            "trade_count": 22,
                            "rel_spy": 0.01,
                        }
                    }
                }
            }
        },
        "paper_shadow": {"status": "NO_VARIANT_READY_FOR_PAPER_SHADOW"},
    }

    def fake_read(name):
        if name == "strategy_research_lab_latest.json":
            return lab_sidecar
        if name == "strategy_walk_forward_latest.json":
            return {"verdict": "NEED_MORE_DATA"}
        return None

    monkeypatch.setattr(mcp, "_read_json_sidecar", fake_read)

    out = mcp._read_strategy_lab()

    assert out["status"] == "RESEARCH_ONLY"
    assert out["paper_active"] is False
    assert out["walk_forward"] == "NEED_MORE_DATA"


def test_dashboard_strategy_lab_line_renders_cache_summary():
    from dashboards.gem_trader_hq import PB
    from tests.smoke.test_dashboard_render_phase2b2 import _Stub, _mcp, _render

    payload = _mcp(1.0)
    payload["strategy_lab"] = {
        "best_variant": "RECALL_SHADOW_RS_MOMENTUM",
        "verdict": "NEED_MORE_DATA",
        "mode": "exact_full",
        "sampled": False,
        "paper_active": False,
    }

    text = _render(PB.mcp_audit_summary(_Stub(mcp_audit_session=payload)))

    assert "Strategy Lab: exact=exact_full" in text
    assert "best=RECALL_SHADOW_RS_MOMENTUM" in text
    assert "no paper active" in text


def test_production_strategy_state_unchanged():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert getattr(filters, "SNI_ATR_CONTRACTION_THRESH") == 0.85
    assert getattr(filters, "SNI_VOL_SPIKE_THRESH") == 1.4
    assert getattr(filters, "VOY_MAX_EXTENSION_MA50") == 0.12
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False
    # Phase 5: SNIPER and VOYAGER decommissioned (2026-06-14); no active scanner keys.\n    assert set(reg.active_scanner_keys()) == set()
