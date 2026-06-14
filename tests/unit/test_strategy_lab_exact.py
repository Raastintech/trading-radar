"""Phase 1H.1 Strategy Lab exact-mode + performance-hardening guardrail tests.

Covers: exact mode does not sample; quick/sampled runs are non-promotable and
can never feed a paper-shadow proposal; the vectorized feature-table cache
preserves legacy results on a fixture; no-lookahead still holds after caching;
walk-forward/test isolation; missing-window reporting; forbidden imports;
production thresholds and SHORT_A freeze unchanged.
"""
from __future__ import annotations

import ast
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import strategy_lab_data as data  # noqa: E402
from research import strategy_lab_decision as decision  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402
from research import strategy_threshold_sweep as sweep  # noqa: E402
from research import strategy_walk_forward as wf  # noqa: E402


def _random_bars(seed: int, start: str, n: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    close = 50.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, n)))
    volume = rng.integers(500_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


@pytest.fixture
def fixture_cache(tmp_path, monkeypatch):
    """Synthetic parquet price caches wired into the data layer."""
    backtest = tmp_path / "backtest_prices"
    deep = tmp_path / "prices_deep"
    shallow = tmp_path / "prices"
    for p in (backtest, deep, shallow):
        p.mkdir()
    tickers = ["SPY", "QQQ", "SMH", "XLK", "AAA", "BBB", "CCC", "DDD"]
    for i, t in enumerate(tickers):
        _random_bars(100 + i, "2025-01-02", 320).to_parquet(backtest / f"{t}.parquet")
    # One ticker with a NaN bar must fall back to the legacy path.
    nan_bars = _random_bars(999, "2025-01-02", 320)
    nan_bars.iloc[5, nan_bars.columns.get_loc("close")] = float("nan")
    nan_bars.to_parquet(backtest / "EEE.parquet")

    monkeypatch.setattr(data, "BACKTEST_PRICES_DIR", backtest)
    monkeypatch.setattr(data, "DEEP_PRICES_DIR", deep)
    monkeypatch.setattr(data, "SHALLOW_PRICES_DIR", shallow)
    monkeypatch.setattr(data, "UNIVERSE_SNAPSHOT", tmp_path / "missing_universe.json")
    monkeypatch.setattr(data, "metadata_for_ticker", lambda ticker: {
        "sector": "Technology",
        "industry": "Software",
        "theme": "semiconductors",
        "market_cap": 1e9,
        "company_name": ticker,
        "metadata_reliability": data.CURRENT_METADATA_APPROXIMATION,
    })
    lab.clear_caches_for_tests()
    yield tickers + ["EEE"]
    lab.clear_caches_for_tests()


def _feature_diffs(a, b, tol=1e-9):
    if (a is None) != (b is None):
        return ["presence mismatch"]
    if a is None:
        return []
    diffs = []
    for k in set(a) | set(b):
        va, vb = a.get(k), b.get(k)
        if isinstance(va, float) and isinstance(vb, float):
            if not math.isclose(va, vb, rel_tol=tol, abs_tol=1e-12):
                diffs.append(f"{k}: {va!r} != {vb!r}")
        elif va != vb:
            diffs.append(f"{k}: {va!r} != {vb!r}")
        if isinstance(vb, bool) and va is not None and not isinstance(va, bool):
            diffs.append(f"{k}: non-python-bool {type(va)}")
    return diffs


def test_performance_cache_preserves_identical_fixture_results(fixture_cache):
    dates = ["2025-03-03", "2025-08-15", "2026-01-09", "2026-03-20"]
    for t in fixture_cache:
        for asof in dates:
            fast = data._compute_features_asof_fast(t, asof)
            legacy = data.compute_features_asof_legacy(t, asof)
            assert _feature_diffs(fast, legacy) == [], f"{t}@{asof}"


def test_fast_window_results_identical_to_legacy(fixture_cache):
    cfg = lab.BacktestConfig(universe_mode="backtest_cache", universe_cap=10, min_bars=60)
    params = lab.StrategyParams(min_avg_dvol=1_000.0)

    w_fast = lab.run_backtest_window(
        "w", "2026-02-02", "2026-02-13", params=params, config=cfg, cost_models=(lab.BASE_COST,)
    )
    lab.clear_caches_for_tests()
    data.FAST_FEATURES = False
    try:
        w_legacy = lab.run_backtest_window(
            "w", "2026-02-02", "2026-02-13", params=params, config=cfg, cost_models=(lab.BASE_COST,)
        )
    finally:
        data.FAST_FEATURES = True
        lab.clear_caches_for_tests()

    assert json.dumps(w_fast, sort_keys=True, default=str) == json.dumps(w_legacy, sort_keys=True, default=str)


def test_no_lookahead_still_holds_after_caching(fixture_cache):
    """Feature rows for a date must not change when future bars are appended."""
    asof = "2025-09-10"
    before = data.compute_features_asof("AAA", asof)
    assert before is not None
    full = data._load_merged_prices("AAA")
    extended = pd.concat([
        full[["open", "high", "low", "close", "volume"]],
        _random_bars(7, "2026-06-15", 30) * 3.0,
    ])
    extended.attrs["sources"] = list(full.attrs.get("sources") or [])
    lab.clear_caches_for_tests()
    data._load_merged_prices.cache_clear()
    orig = data._load_merged_prices
    data._load_merged_prices = lambda ticker, _ext=extended, _orig=orig: (
        _ext if ticker == "AAA" else _orig(ticker)
    )
    try:
        after = data.compute_features_asof("AAA", asof)
    finally:
        data._load_merged_prices = orig
        lab.clear_caches_for_tests()
    assert after is not None
    assert _feature_diffs(after, before) == []
    assert after["bar_date"] <= asof


def test_exact_mode_does_not_sample(fixture_cache):
    cfg = lab.BacktestConfig(universe_mode="backtest_cache", universe_cap=5, min_bars=60, date_stride=1)
    params = lab.StrategyParams(min_avg_dvol=1_000.0)
    w = lab.run_backtest_window("w", "2026-02-02", "2026-02-27", params=params, config=cfg, cost_models=(lab.BASE_COST,))
    assert w["sampled"] is False
    assert w["dates_evaluated"] == w["dates_total_in_window"]
    assert w["dates_skipped_by_stride"] == 0
    assert w["ticker_days_evaluated"] > 0


def test_quick_mode_marked_sampled_and_non_promotable(fixture_cache):
    cfg = lab.BacktestConfig(universe_mode="backtest_cache", universe_cap=5, min_bars=60, date_stride=4)
    params = lab.StrategyParams(min_avg_dvol=1_000.0)
    result = lab.build_lab_result(
        windows=[{"name": "w", "start": "2026-01-05", "end": "2026-03-31"}],
        params=params, config=cfg, run_mode="quick",
    )
    assert result["sampled"] is True
    assert "SAMPLED RUN" in result["paper_shadow"]["reason"]
    for v in result["variant_verdicts"].values():
        assert v["promotable_from_this_run"] is False
        assert "sampled_run_not_decision_grade" in v["blockers"]
        assert v["verdict"] != lab.VERDICT_EDGE or v["sampled"] is False


def test_sampled_results_cannot_produce_paper_shadow_proposal(tmp_path, monkeypatch):
    sampled_artifact = {
        "sampled": True,
        "generated_at": "x",
        "variant_verdicts": {v: {"verdict": lab.VERDICT_EDGE} for v in lab.DEFAULT_VARIANTS},
        "windows": {},
    }
    exact_wf = {"sampled": False, "final_by_variant": {
        v: {"verdict": lab.VERDICT_EDGE, "overfit_risk": "MODERATE_NEEDS_PAPER_SHADOW_GATE"}
        for v in lab.DEFAULT_VARIANTS
    }}
    paths = {}
    for name in decision.SOURCES:
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(exact_wf if name == "walk_forward_exact" else sampled_artifact))
        paths[name] = p
    monkeypatch.setattr(decision, "SOURCES", paths)
    res = decision.build_decision()
    assert res["decision_grade"] is False
    assert res["sampled_sources_refused"]
    assert res["paper_shadow"]["status"] == "NO_VARIANT_READY_FOR_PAPER_SHADOW"
    assert res["ready_variants"] == []
    assert decision.write_proposal_doc(res) is None
    for row in res["table"]:
        assert row["final_verdict"] != lab.VERDICT_READY


def test_exact_walk_forward_uses_untouched_test_data(monkeypatch):
    splits = {
        "train": {"start": "2025-01-01", "end": "2025-03-31"},
        "validation": {"start": "2025-04-01", "end": "2025-06-30"},
        "test": {"start": "2025-07-01", "end": "2025-09-30"},
    }
    monkeypatch.setattr(wf, "make_walk_forward_splits", lambda **kw: {"mode": "t", "splits": splits})
    monkeypatch.setattr(wf, "candidate_params", lambda: [
        lab.StrategyParams(), lab.StrategyParams(sector_rs_threshold=0.12),
    ])
    calls = []

    def fake_run(split_name, split, params, config):
        calls.append(split_name)
        # Param 1 looks great on test but worse on validation; selection must
        # still pick by validation only.
        boosted = params.sector_rs_threshold > 0.1
        exp = {"train": 0.02, "validation": 0.01 if boosted else 0.03, "test": 0.5 if boosted else 0.02}[split_name]
        metrics = {"trade_count": 30, "expectancy": exp, "rel_spy": exp, "rel_qqq": exp, "max_drawdown": -0.01}
        return {"by_cost": {"base_cost": {v: dict(metrics) for v in lab.DEFAULT_VARIANTS}}}

    monkeypatch.setattr(wf, "_run_split", fake_run)
    res = wf.build_walk_forward(config=lab.BacktestConfig(universe_cap=5, date_stride=1))
    assert res["exact"] is True and res["sampled"] is False
    assert res["test_used_for_selection"] is False
    for row in res["final_by_variant"].values():
        assert row["param_id"] == 0  # validation winner, not the test-boosted param
        assert "test_decay" in row and "parameter_stability" in row


def test_threshold_sweep_exact_does_not_tune_on_test(monkeypatch):
    splits = {
        "train": {"start": "2025-01-01", "end": "2025-01-31"},
        "validation": {"start": "2025-02-01", "end": "2025-02-28"},
        "test": {"start": "2025-03-01", "end": "2025-03-31"},
    }
    monkeypatch.setattr(sweep.wf, "make_walk_forward_splits", lambda: {"mode": "t", "splits": splits})
    monkeypatch.setattr(sweep, "limited_grid", lambda: [lab.StrategyParams(), lab.StrategyParams(extension_cap=0.30)])

    def fake_run(split_name, split, params, config):
        test_bait = params.extension_cap > 0.27
        exp = 0.30 if (split_name == "test" and test_bait) else (0.02 if not test_bait else 0.01)
        metrics = {"trade_count": 30, "expectancy": exp, "rel_spy": exp, "rel_qqq": exp, "max_drawdown": -0.01}
        return {"by_cost": {"base_cost": {v: dict(metrics) for v in sweep.SWEEP_VARIANTS}}}

    monkeypatch.setattr(sweep, "_run", fake_run)
    res = sweep.build_threshold_sweep(config=lab.BacktestConfig(universe_cap=5, date_stride=1))
    assert res["exact"] is True and res["sampled"] is False
    for row in res["selected_by_variant"].values():
        assert row["param_id"] == 0
        assert row["test_used_for_selection"] is False
    assert res["production_thresholds_mutated"] is False


def test_full_window_missing_data_is_reported_not_fabricated(monkeypatch):
    cal = pd.bdate_range("2026-01-02", periods=80)
    monkeypatch.setattr(lab.d, "benchmark_calendar", lambda: pd.DatetimeIndex(cal))
    monkeypatch.setattr(
        lab.d, "trading_dates_between",
        lambda start, end: [x for x in cal if pd.Timestamp(start) <= x <= pd.Timestamp(end)],
    )
    windows, unavailable = lab.full_windows()
    names = {w["name"] for w in windows}
    missing = {u["name"] for u in unavailable}
    assert "2024_available" in missing and "2025_available" in missing
    assert all("retained" in u["reason"] for u in unavailable)
    assert "2026_ytd" in names


def test_decision_promotion_rule_requires_exact_backtest_and_walk_forward(tmp_path, monkeypatch):
    exact_lab = {
        "sampled": False,
        "generated_at": "x",
        "variant_verdicts": {v: {"verdict": lab.VERDICT_NEED_MORE} for v in lab.DEFAULT_VARIANTS},
        "windows": {
            "w": {"by_cost": {"base_cost": {
                v: {"trade_count": 50, "expectancy": 0.01, "rel_spy": 0.01, "rel_qqq": 0.01, "max_drawdown": -0.05}
                for v in lab.DEFAULT_VARIANTS
            }}},
        },
    }
    exact_lab["variant_verdicts"]["RECALL_SHADOW_RS_MOMENTUM"] = {"verdict": lab.VERDICT_EDGE}
    wf_art = {"sampled": False, "final_by_variant": {
        # Backtest edge but walk-forward NOT confirmed -> never READY.
        "RECALL_SHADOW_RS_MOMENTUM": {"verdict": lab.VERDICT_OVERFIT_RISK, "overfit_risk": "HIGH_TEST_DECAY"},
    }}
    sweep_art = {"sampled": False, "selected_by_variant": {}}
    paths = {}
    for name in decision.SOURCES:
        p = tmp_path / f"{name}.json"
        content = {"exact_full": exact_lab, "exact_recent60": exact_lab, "exact_2026ytd": exact_lab,
                   "walk_forward_exact": wf_art, "threshold_sweep_exact": sweep_art}[name]
        p.write_text(json.dumps(content))
        paths[name] = p
    monkeypatch.setattr(decision, "SOURCES", paths)
    res = decision.build_decision()
    assert res["decision_grade"] is True
    row = next(r for r in res["table"] if r["variant"] == "RECALL_SHADOW_RS_MOMENTUM")
    assert row["final_verdict"] == lab.VERDICT_OVERFIT_RISK
    assert res["paper_shadow"]["status"] == "NO_VARIANT_READY_FOR_PAPER_SHADOW"

    # Now let walk-forward confirm -> READY is allowed and proposal doc only.
    wf_art["final_by_variant"]["RECALL_SHADOW_RS_MOMENTUM"] = {
        "verdict": lab.VERDICT_EDGE, "overfit_risk": "MODERATE_NEEDS_PAPER_SHADOW_GATE",
        "train": {}, "validation": {}, "test": {},
    }
    paths["walk_forward_exact"].write_text(json.dumps(wf_art))
    res2 = decision.build_decision()
    row2 = next(r for r in res2["table"] if r["variant"] == "RECALL_SHADOW_RS_MOMENTUM")
    assert row2["final_verdict"] == lab.VERDICT_READY
    assert res2["paper_shadow"]["proposal_created"] is True


def test_no_paper_signals_or_execution_imports_in_1h1_modules():
    from research import strategy_lab_profile as profile
    forbidden_import_roots = {"execution", "governance", "broker", "live_capital", "council"}
    forbidden_calls = (
        "create_paper_signal(", "emit_paper_signal(", "insert_paper_signal(",
        "create_trade_proposal(", "emit_trade_proposal(", "strategy_registry.register",
        "submit_buy_order", "submit_sell_order", "close_position(",
    )
    for module in (data, lab, wf, sweep, decision, profile):
        text = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
                assert roots.isdisjoint(forbidden_import_roots), module.__name__
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in forbidden_import_roots, module.__name__
        for needle in forbidden_calls:
            assert needle not in text, f"{module.__name__}: {needle}"


def test_production_thresholds_unchanged_and_short_a_frozen():
    import importlib
    filters = importlib.import_module("research.scanner_truth.filters")
    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False
    # Phase 5: SNIPER and VOYAGER decommissioned (2026-06-14); no active scanner keys.
    assert set(reg.active_scanner_keys()) == set()
