"""Phase 1I.2 entry clustering / sizing guardrail tests."""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import lrr_clustered_portfolio as clus  # noqa: E402
from research import strategy_lab_portfolio as portfolio  # noqa: E402
from research import strategy_lab_regime as regime  # noqa: E402
from research import strategy_research_lab as lab  # noqa: E402

DATES = [f"2026-03-{d:02d}" for d in (2, 3, 4, 5, 6, 9, 10, 11, 12, 13)]


def _trade(ticker, signal_date, exit_date, net=0.02, score=80.0, sector="Technology",
           theme="semiconductors", rs60=0.10, vol_ratio=1.2, stop=0.06, atr=0.02):
    return {
        "ticker": ticker,
        "signal_date": signal_date,
        "entry_date": signal_date,
        "exit_date": exit_date,
        "entry_price": 100.0,
        "exit_price": 100.0 * (1 + net),
        "net_return": net,
        "raw_return": net,
        "cost_return": 0.0025,
        "score": score,
        "side": "long",
        "sector": sector,
        "theme": theme,
        "rank_rs60": rs60,
        "rank_volume_ratio": vol_ratio,
        "rank_stop_pct": stop,
        "rank_atr_pct": atr,
    }


# ---------------------------------------------------------------------------
# Entry filters
# ---------------------------------------------------------------------------

def test_max_one_entry_per_day_takes_highest_score():
    cfg = clus.CONFIGS[1]  # C1_MAX_1_PER_DAY
    trades = [
        _trade("AAA", DATES[0], DATES[4], score=70.0),
        _trade("BBB", DATES[0], DATES[4], score=90.0),
        _trade("CCC", DATES[1], DATES[5], score=60.0),
    ]
    out = clus.apply_entry_filters(trades, cfg, DATES)
    assert [t["ticker"] for t in out] == ["BBB", "CCC"]


def test_max_two_entries_per_day():
    cfg = clus.CONFIGS[2]  # C2_MAX_2_PER_DAY
    trades = [_trade(t, DATES[0], DATES[4], score=s) for t, s in (("AAA", 70), ("BBB", 90), ("CCC", 80))]
    out = clus.apply_entry_filters(trades, cfg, DATES)
    assert sorted(t["ticker"] for t in out) == ["BBB", "CCC"]


def test_top_ranked_only_uses_composite_rank_not_score():
    cfg = clus.CONFIGS[3]  # C3_TOP_RANKED_ONLY
    low_score_high_rank = _trade("AAA", DATES[0], DATES[4], score=60.0, rs60=0.30, stop=0.04)
    high_score_low_rank = _trade("BBB", DATES[0], DATES[4], score=95.0, rs60=0.01, stop=0.10)
    out = clus.apply_entry_filters([low_score_high_rank, high_score_low_rank], cfg, DATES)
    assert [t["ticker"] for t in out] == ["AAA"]
    assert clus.composite_rank(low_score_high_rank) > clus.composite_rank(high_score_low_rank)


def test_cooldown_blocks_entries_after_losing_exit_only():
    cfg = clus.CONFIGS[8]  # C8_COOLDOWN_3D_AFTER_LOSS
    loser = _trade("AAA", DATES[0], DATES[2], net=-0.05)
    within_cooldown = _trade("BBB", DATES[3], DATES[6])
    after_cooldown = _trade("CCC", DATES[6], DATES[9])
    out = clus.apply_entry_filters([loser, within_cooldown, after_cooldown], cfg, DATES)
    assert [t["ticker"] for t in out] == ["AAA", "CCC"]

    winner = _trade("AAA", DATES[0], DATES[2], net=0.05)
    out = clus.apply_entry_filters([winner, within_cooldown, after_cooldown], cfg, DATES)
    assert [t["ticker"] for t in out] == ["AAA", "BBB", "CCC"]


def test_cooldown_uses_only_past_exits_no_lookahead():
    """A loss exiting AFTER the candidate's signal date must not block it."""
    cfg = clus.CONFIGS[8]
    future_loser = _trade("AAA", DATES[0], DATES[8], net=-0.05)  # exits later
    candidate = _trade("BBB", DATES[2], DATES[6])
    out = clus.apply_entry_filters([future_loser, candidate], cfg, DATES)
    assert [t["ticker"] for t in out] == ["AAA", "BBB"]


def test_skip_duplicate_sector_while_open():
    cfg = clus.CONFIGS[9]  # C9_SKIP_DUP_SECTOR_OPEN
    first = _trade("AAA", DATES[0], DATES[4], sector="Technology")
    blocked = _trade("BBB", DATES[1], DATES[5], sector="Technology")
    other_sector = _trade("CCC", DATES[1], DATES[5], sector="Energy")
    after_exit = _trade("DDD", DATES[5], DATES[8], sector="Technology")
    out = clus.apply_entry_filters([first, blocked, other_sector, after_exit], cfg, DATES)
    assert [t["ticker"] for t in out] == ["AAA", "CCC", "DDD"]


def test_skip_duplicate_theme_while_open():
    cfg = clus.CONFIGS[7]  # C7_SECTOR_THEME_CAP
    first = _trade("AAA", DATES[0], DATES[4], theme="semiconductors")
    blocked = _trade("BBB", DATES[1], DATES[5], theme="semiconductors")
    other = _trade("CCC", DATES[1], DATES[5], theme="defense", sector="Industrials")
    out = clus.apply_entry_filters([first, blocked, other], cfg, DATES)
    assert [t["ticker"] for t in out] == ["AAA", "CCC"]


def test_baseline_config_passes_everything_through():
    cfg = clus.CONFIGS[0]
    trades = [_trade(t, DATES[0], DATES[4]) for t in ("AAA", "BBB", "CCC")]
    out = clus.apply_entry_filters(trades, cfg, DATES)
    assert len(out) == 3
    assert all("size_weight" not in t for t in out)


# ---------------------------------------------------------------------------
# Vol-scaled sizing
# ---------------------------------------------------------------------------

def test_vol_size_weight_bounds():
    assert clus.vol_size_weight(_trade("A", DATES[0], DATES[1], atr=0.02)) == pytest.approx(1.0)
    assert clus.vol_size_weight(_trade("A", DATES[0], DATES[1], atr=0.04)) == pytest.approx(0.5)
    assert clus.vol_size_weight(_trade("A", DATES[0], DATES[1], atr=0.20)) == pytest.approx(clus.SIZING_MIN_WEIGHT)
    assert clus.vol_size_weight(_trade("A", DATES[0], DATES[1], atr=0.005)) == pytest.approx(1.0)  # capped at 1
    assert clus.vol_size_weight(_trade("A", DATES[0], DATES[1], atr=0.0)) == pytest.approx(1.0)  # missing -> full


def test_vol_scaled_config_attaches_size_weight():
    cfg = clus.CONFIGS[6]  # C6_VOL_SCALED
    out = clus.apply_entry_filters([_trade("AAA", DATES[0], DATES[4], atr=0.04)], cfg, DATES)
    assert out[0]["size_weight"] == pytest.approx(0.5)


def test_portfolio_engine_honors_size_weight_and_defaults_to_full():
    half = _trade("AAA", "2026-03-02", "2026-03-06", net=0.10)
    half["size_weight"] = 0.5
    full = _trade("BBB", "2026-03-02", "2026-03-06", net=0.10)
    res = portfolio.realistic_portfolio_metrics([half, full], start="2026-03-01", end="2026-03-10")
    sized = {row["ticker"]: row["notional_pct_at_entry"] for row in res["accepted_sample"]}
    assert sized["AAA"] == pytest.approx(0.05, abs=1e-6)
    assert sized["BBB"] == pytest.approx(0.10, abs=1e-6)


# ---------------------------------------------------------------------------
# Configs and gates are pre-registered
# ---------------------------------------------------------------------------

def test_config_set_matches_task_list():
    ids = [c.config_id for c in clus.CONFIGS]
    assert ids == [
        "BASELINE_GATED", "C1_MAX_1_PER_DAY", "C2_MAX_2_PER_DAY", "C3_TOP_RANKED_ONLY",
        "C4_MAX_3_OPEN", "C5_MAX_5_OPEN", "C6_VOL_SCALED", "C7_SECTOR_THEME_CAP",
        "C8_COOLDOWN_3D_AFTER_LOSS", "C9_SKIP_DUP_SECTOR_OPEN", "C10_COMBO_DEFENSIVE",
    ]
    assert clus.MIN_INDEPENDENT_DD == -0.35
    assert clus.MAX_REALISTIC_DD == -0.15


def test_signal_rules_and_regimes_remain_frozen():
    assert lab.LRR_RS60_MIN == 0.05
    assert lab.LRR_R60_MIN == 0.15
    assert lab.LRR_RESET_MIN == 0.05
    assert lab.LRR_RESET_MAX == 0.18
    assert lab.LRR_CRASH_R10 == -0.18
    assert lab.LRR_PARABOLIC_R20 == 0.60
    assert lab.LRR_ALLOWED_REGIMES == frozenset(
        {regime.TECH_LED_CORRECTION, regime.RECOVERY_RECLAIM, regime.HIGH_VOLATILITY}
    )


def test_proposal_doc_written_only_when_a_config_passes(tmp_path, monkeypatch):
    for name in ("OUT_JSON", "OUT_TXT", "OUT_DOC", "PROPOSAL_DOC"):
        monkeypatch.setattr(clus, name, tmp_path / f"{name}.out")
    monkeypatch.setattr(clus, "render_text", lambda res: ["x"])
    monkeypatch.setattr(clus, "render_doc", lambda res: "x")
    monkeypatch.setattr(clus, "render_proposal_doc", lambda res: "proposal")

    clus.write_outputs({"paper_shadow": {"proposal_created": False}})
    assert not (tmp_path / "PROPOSAL_DOC.out").exists()

    clus.write_outputs({"paper_shadow": {"proposal_created": True}})
    assert (tmp_path / "PROPOSAL_DOC.out").read_text() == "proposal\n"


# ---------------------------------------------------------------------------
# Safety guardrails
# ---------------------------------------------------------------------------

def test_no_paper_trade_or_execution_governance_imports_for_phase_1i2():
    forbidden_import_roots = {"execution", "governance", "broker", "live_capital", "council"}
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
    )
    text = Path(clus.__file__).read_text(encoding="utf-8")
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


def test_production_thresholds_unchanged_and_short_a_frozen_after_phase_1i2():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False
    assert "LRR_REGIME_GATED" not in reg.SLEEVE_REGISTRY
