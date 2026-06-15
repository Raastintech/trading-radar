"""tests/unit/test_recall_repair_shadow_lane.py — Path B shadow lane + forward.

RESEARCH-ONLY / CACHE-ONLY guarantees and core logic:
  - sector_rs / mom_20_60 / theme candidates label + rank correctly
  - late/parabolic names are MARKED (SHADOW_LATE_EXTENDED), never auto-traded,
    and sink in the ranking
  - the historizer is append-only + idempotent per (asof_date, ticker, version)
  - immature forward windows are excluded
  - the forward random-control gate works (verdict ladder)
  - no execution / governance / live-capital imports; no signal/proposal language
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import recall_repair_shadow_lane as lane  # noqa: E402
from research import recall_repair_shadow_forward as fwd  # noqa: E402


def _feat(**over: Any) -> Dict[str, Any]:
    base = {
        "sector_rs": 0.0, "r5": 0.0, "r20": 0.0, "r60": 0.0,
        "theme": "semiconductors", "theme_leader": False, "theme_score": 0.0,
        "vol_ratio": 1.0, "extension_label": "NORMAL", "bar_depth_status": "DEEP",
    }
    base.update(over)
    return base


# ── classification ───────────────────────────────────────────────────────────

def test_sector_rs_leader_labeled():
    label, reasons = lane._classify(_feat(sector_rs=0.15, r20=0.05))
    assert label == "SHADOW_RS_LEADER"
    assert any("sector_rs" in r for r in reasons)


def test_momentum_leader_labeled():
    label, reasons = lane._classify(_feat(r20=0.12, r60=0.25))
    assert label == "SHADOW_MOMENTUM_LEADER"
    assert "mom_20_60" in reasons


def test_theme_leader_labeled():
    label, reasons = lane._classify(
        _feat(theme_leader=True, sector_rs=0.12, r20=0.15, theme="semiconductors"))
    assert label == "SHADOW_THEME_LEADER"
    assert any("theme_leader" in r for r in reasons)


def test_no_edge_when_nothing_fires():
    label, _ = lane._classify(_feat(sector_rs=0.0, r20=0.0, r60=0.0))
    assert label == "SHADOW_NO_EDGE"


# ── late/parabolic marked, not chased ────────────────────────────────────────

def test_parabolic_leader_marked_late_not_momentum():
    label, reasons = lane._classify(
        _feat(r20=1.20, r60=2.00, sector_rs=1.0, extension_label="PARABOLIC"))
    assert label == "SHADOW_LATE_EXTENDED"   # marked, not SHADOW_MOMENTUM_LEADER
    assert "parabolic" in reasons


def test_parabolic_ranks_below_clean_early_leader():
    clean = _feat(sector_rs=0.25, r20=0.30, r60=0.50, theme_score=0.9,
                  extension_label="NORMAL", bar_depth_status="DEEP")
    parabolic = _feat(sector_rs=1.0, r20=1.20, r60=2.0, theme_score=0.9,
                      extension_label="PARABOLIC", bar_depth_status="DEEP")
    assert lane._rank_score(clean) > lane._rank_score(parabolic)


def test_extension_labeling():
    assert lane._extension_label(0.50, 0.10) == "PARABOLIC"
    assert lane._extension_label(0.10, 0.90) == "PARABOLIC"   # r20 ≥ 0.80
    assert lane._extension_label(0.25, 0.10) == "EXTENDED"
    assert lane._extension_label(0.05, 0.05) == "NORMAL"


# ── historizer: append-only + idempotent ─────────────────────────────────────

def _fake_result(asof: str, tickers) -> Dict[str, Any]:
    return {
        "generated_at": "2026-06-07T00:00:00+00:00",
        "asof_date": asof,
        "version": lane.VERSION,
        "alpha_board_overlap": 0,
        "scanner_seen_available": True,
        "candidates": [{"ticker": t, "rank": i + 1, "label": "SHADOW_RS_LEADER"}
                       for i, t in enumerate(tickers)],
    }


def test_historizer_idempotent(tmp_path):
    hp = tmp_path / "hist.jsonl"
    res = _fake_result("2026-06-05", ["AAA", "BBB", "CCC"])
    n1 = lane.historize(res, path=hp)
    n2 = lane.historize(res, path=hp)          # same date/tickers/version
    assert n1 == 3
    assert n2 == 0
    assert len(lane.dataio.read_jsonl(hp)) == 3
    # A new date appends; prior lines untouched (append-only).
    n3 = lane.historize(_fake_result("2026-06-08", ["AAA", "DDD"]), path=hp)
    assert n3 == 2
    assert len(lane.dataio.read_jsonl(hp)) == 5


# ── forward: immature windows excluded ───────────────────────────────────────

def test_forward_metrics_excludes_immature():
    import pandas as pd
    s = pd.Series([10.0, 11.0, 12.0])           # only 3 bars
    assert fwd._fwd_metrics(s, asof_i=0, h=2) is not None   # matured (0→2)
    assert fwd._fwd_metrics(s, asof_i=2, h=1) is None       # beyond series → None
    assert fwd._fwd_metrics(s, asof_i=1, h=5) is None       # not enough forward


def test_forward_winrate_and_mean():
    assert fwd._winrate([0.1, -0.2, 0.3]) == round(100 * 2 / 3, 1)
    assert fwd._mean([1.0, 3.0]) == 2.0
    assert fwd._winrate([]) is None


# ── forward verdict ladder (random-control gate works) ───────────────────────

def _by_h(shadow5, random5, shadow10, random10):
    return {
        "5": {"shadow_rel_spy_avg": shadow5, "random_rel_spy_avg": random5},
        "10": {"shadow_rel_spy_avg": shadow10, "random_rel_spy_avg": random10},
    }


def _recall(shadow_recall, fp=60.0):
    return {"+20pct": {"shadow_recall_pct": shadow_recall,
                       "shadow_false_positive_pct": fp}}


def test_verdict_need_more_data_when_thin():
    v, _ = fwd._verdict(_by_h(0.05, -0.01, 0.05, -0.01), _recall(20.0),
                        {"shadow_leading_theme_winners": 5, "alpha_leading_theme_winners": 1},
                        history_days=7, mature_ticker_days=900)
    assert v == "NEED_MORE_DATA"


def test_verdict_no_value_when_no_edge():
    v, _ = fwd._verdict(_by_h(-0.01, 0.02, -0.01, 0.02), _recall(1.0),
                        {"shadow_leading_theme_winners": 0, "alpha_leading_theme_winners": 0},
                        history_days=12, mature_ticker_days=900)
    assert v == "NO_VALUE"


def test_verdict_recall_edge_when_strong():
    v, _ = fwd._verdict(_by_h(0.02, -0.01, 0.03, -0.01), _recall(10.0, fp=60.0),
                        {"shadow_leading_theme_winners": 1, "alpha_leading_theme_winners": 1},
                        history_days=12, mature_ticker_days=900)
    assert v == "RECALL_EDGE_DETECTED"


def test_verdict_ready_to_feed_lens_when_themes_earlier():
    v, _ = fwd._verdict(_by_h(0.02, -0.01, 0.03, -0.01), _recall(10.0, fp=60.0),
                        {"shadow_leading_theme_winners": 8, "alpha_leading_theme_winners": 1},
                        history_days=12, mature_ticker_days=900)
    assert v == "READY_TO_FEED_LENS_RESEARCH_ONLY"


# ── no signals / no forbidden imports ────────────────────────────────────────

def test_no_execution_or_signal_imports():
    for mod in ("recall_repair_shadow_lane.py", "recall_repair_shadow_forward.py"):
        src = (Path(__file__).resolve().parents[2] / "research" / mod).read_text()
        for token in ("execution.order_manager", "execution.paper_governance",
                      "paper_governance", "submit_market_order", "submit_limit_order",
                      "council.veto_council", "ALLOW_LIVE_CAPITAL", "close_position("):
            assert token not in src, f"{mod} must stay research-only: found {token!r}"
        # research-only framing present.
        assert "research-only" in src.lower() or "research only" in src.lower()


def test_lane_candidate_rows_are_research_watch_only():
    # The per-candidate note must never imply an order.
    label, _ = lane._classify(_feat(sector_rs=0.2, r20=0.1))
    assert label.startswith("SHADOW_")
    # No buy/sell verbs leak into the label vocabulary.
    for bad in ("BUY", "SELL", "ENTER", "LONG_NOW", "SHORT_NOW"):
        assert bad not in label
