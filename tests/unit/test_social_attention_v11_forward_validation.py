"""Tests for research/social_attention_v11_forward_validation.py —
Social Attention v1.1 shadow forward-validation gate (Phase 1G.15 repair
plan, Step C). Research-only, parallel to and independent of the
production gate; mirrors the style of test_social_attention_radar.py's
forward-validation tests (real cache-only price data, no calendar
mocking) since today's date can never have matured forward returns
regardless of what's cached."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import research.social_attention_v11_forward_validation as v11fwd
import research.social_attention_radar_v11 as v11

REPO = Path(__file__).resolve().parents[2]


# ── empty / immature history never fabricates a verdict ─────────────────────


def test_empty_history_stays_need_more_data(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    res = v11fwd.build(history_path=empty, top_history_path=empty)
    assert res["verdict"] == "NEED_MORE_DATA"
    assert "no v1.1 history" in res["error"]
    assert res["research_only"] is True
    assert res["promote_to_signal"] is False


def test_todays_row_never_matures(tmp_path):
    """A row dated today can never have a matured 5d forward return
    regardless of what's cached -- this must always stay NEED_MORE_DATA."""
    hist = tmp_path / "hist.jsonl"
    top = tmp_path / "top.jsonl"
    today = datetime.now(timezone.utc).date().isoformat()
    rows = [{"asof_date": today, "version": v11.VERSION, "ticker": "NVDA",
            "crowd_stage": "EARLY_DISCOVERY", "crowd_stage_v11": "EARLY_DISCOVERY",
            "stage_changed_v11": False, "lead_type": "UNKNOWN",
            "primary_source_type": "stocktwits", "velocity_percentile": 0.8,
            "noise_score": 10.0, "history_appearances": 1,
            "is_high_conviction": False, "is_emerging_outlier": False,
            "is_alpha_focus_review_now": False, "is_wait_for_reset": False,
            "is_profitable_quality": True, "is_red_flag": False,
            "metrics": {"mention_count_24h": 10, "attention_velocity_score": 65.0,
                        "source_diversity_score": 28.0}}]
    hist.write_text("\n".join(json.dumps(r) for r in rows))
    top.write_text("")
    res = v11fwd.build(history_path=hist, top_history_path=top)
    assert res["verdict"] == "NEED_MORE_DATA"
    assert res["decision_gates"]["matured_primary_met"] is False


# ── _summ extends the production gate with median ───────────────────────────


def test_summ_includes_median_alongside_mean():
    acc = v11fwd._empty_acc()
    # (fwd_end, spy_h) pairs -> rel_spy = fwd_end - spy_h
    for end, spy_h in [(0.03, 0.01), (0.15, 0.05), (-0.03, -0.02)]:
        v11fwd._push(acc, {"fwd_end": end, "mfe": abs(end), "mae": -abs(end)},
                     spy_h=spy_h, qqq_h=None)
    s = v11fwd._summ(acc)
    assert s["n"] == 3
    assert s["median_rel_spy"] == 0.02  # median of [0.02, 0.10, -0.01]
    assert s["mean_rel_spy"] is not None
    assert s["mae_worst"] == min(acc["mae"])


def test_summ_handles_empty_accumulator():
    s = v11fwd._summ(v11fwd._empty_acc())
    assert s["n"] == 0
    assert s["mean_end"] is None
    assert s["median_end"] is None
    assert s["win_rate"] is None


# ── cohort classification ─────────────────────────────────────────────────────


def test_row_cohorts_social_only_by_default():
    row = {"asof_date": "2026-08-28", "ticker": "ZZZZ",
          "is_high_conviction": False, "is_alpha_focus_review_now": False,
          "is_wait_for_reset": False, "is_emerging_outlier": False,
          "is_profitable_quality": False, "is_red_flag": False,
          "crowd_stage_v11": "EARLY_DISCOVERY"}
    cohorts = v11fwd._row_cohorts(row, origin_by_ticker_date={})
    assert cohorts == ["social_only"]


def test_row_cohorts_multi_membership_and_exhaustion_risk():
    row = {"asof_date": "2026-08-28", "ticker": "ABSI",
          "is_high_conviction": False, "is_alpha_focus_review_now": False,
          "is_wait_for_reset": False, "is_emerging_outlier": False,
          "is_profitable_quality": False, "is_red_flag": True,
          "crowd_stage_v11": "EXHAUSTION_RISK"}
    cohorts = v11fwd._row_cohorts(row, origin_by_ticker_date={})
    assert "social_plus_red_flag" in cohorts
    assert "social_plus_exhaustion_risk_v11" in cohorts
    assert "social_only" not in cohorts  # has real tags -> not the default bucket


def test_row_cohorts_true_social_acceleration_via_origin_index():
    row = {"asof_date": "2026-08-28", "ticker": "PD",
          "is_high_conviction": False, "is_alpha_focus_review_now": False,
          "is_wait_for_reset": False, "is_emerging_outlier": False,
          "is_profitable_quality": False, "is_red_flag": False,
          "crowd_stage_v11": "STEALTH_ATTENTION"}
    origin_idx = {("2026-08-28", "PD"): "SOCIAL_ATTENTION_V11"}
    cohorts = v11fwd._row_cohorts(row, origin_by_ticker_date=origin_idx)
    assert "true_social_acceleration_only" in cohorts


def test_row_cohorts_both_origin_excluded_from_true_social_only():
    row = {"asof_date": "2026-08-28", "ticker": "MSFT",
          "is_high_conviction": False, "is_alpha_focus_review_now": False,
          "is_wait_for_reset": False, "is_emerging_outlier": False,
          "is_profitable_quality": True, "is_red_flag": False,
          "crowd_stage_v11": "EARLY_DISCOVERY"}
    origin_idx = {("2026-08-28", "MSFT"): "BOTH"}
    cohorts = v11fwd._row_cohorts(row, origin_by_ticker_date=origin_idx)
    assert "true_social_acceleration_only" not in cohorts


# ── verdict is bounded and honest ─────────────────────────────────────────────


def test_verdict_bounded_to_two_states():
    v1, _ = v11fwd._verdict({}, history_days=0, matured_primary=0)
    assert v1 == "NEED_MORE_DATA"
    v2, _ = v11fwd._verdict({}, history_days=15, matured_primary=30)
    assert v2 == "PRELIMINARY_MEASUREMENT"
    assert v2 != "SOCIAL_EDGE_DETECTED"  # never borrows the production ladder's enum


def test_verdict_never_says_ready_or_ships_routing():
    """This module must never emit a routing/promotion verdict itself --
    that decision is explicitly deferred to a human per the Step C
    mandate, unlike the production gate's own ladder."""
    v, reason = v11fwd._verdict({"anything": "here"}, history_days=999, matured_primary=999)
    assert v in ("NEED_MORE_DATA", "PRELIMINARY_MEASUREMENT")
    assert "routing" in reason.lower() or "human" in reason.lower() \
        or "must not drive" in reason.lower()


# ── guardrails ────────────────────────────────────────────────────────────────


def test_no_forbidden_imports_or_production_writes():
    src = (REPO / "research" / "social_attention_v11_forward_validation.py").read_text()
    for token in ("import research_scanner", "from research.research_scanner",
                  "import latest_scan_programs", "execution.order_manager",
                  "execution.paper_governance", "paper_governance",
                  "submit_market_order", "submit_limit_order",
                  "council.veto_council", "ALLOW_LIVE_CAPITAL", "close_position(",
                  "DecisionLogger", "log_voyager_paper_signal", "INSERT INTO",
                  "sqlite3"):
        assert token not in src, f"forbidden token found: {token!r}"
    assert "research-only" in src.lower() or "research_only" in src.lower()


def test_never_writes_production_forward_artifact():
    from research import social_attention_forward_validation as prod_fwd
    assert v11fwd.FWD_JSON != prod_fwd.FWD_JSON
    assert v11fwd.OUT_MD != prod_fwd.OUT_MD
    assert "v11" in v11fwd.FWD_JSON.name
