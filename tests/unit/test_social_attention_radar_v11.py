"""Tests for research/social_attention_radar_v11.py — Social Attention v1.1
shadow mode (Phase 1G.15 repair plan, Step B). Research-only, parallel to
the production radar; these tests check the new experiments in isolation
(noise heuristic, recalibrated stage classifier, percentile ranks, source
reliability weighting, expanded-universe union, Top Market Attention
merge) and that no production artifact/import is touched."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import research.social_attention_radar_v11 as v11
from research import social_attention_radar as base

REPO = Path(__file__).resolve().parents[2]


def _item(text="", author=None, source_type="stocktwits", ticker="AAPL",
         timestamp="2026-08-28T00:00:00+00:00"):
    return base.SocialItem(
        source="StockTwits", source_type=source_type, timestamp=timestamp,
        text=text, url="", ticker_candidates=[ticker], theme_candidates=[],
        author_hash=author, source_id=None, engagement=None, comments=None,
        reposts=None, confidence=0.9, mapping_method="explicit_ticker",
        raw_source_ref="",
    )


# ── module guardrails ────────────────────────────────────────────────────────


def test_no_forbidden_imports_or_production_writes():
    src = (REPO / "research" / "social_attention_radar_v11.py").read_text()
    for token in ("import research_scanner", "from research.research_scanner",
                  "import latest_scan_programs", "from research.latest_scan_programs",
                  "execution.order_manager", "execution.paper_governance",
                  "paper_governance", "submit_market_order", "submit_limit_order",
                  "council.veto_council", "ALLOW_LIVE_CAPITAL", "close_position(",
                  "DecisionLogger", "log_voyager_paper_signal", "INSERT INTO",
                  "sqlite3"):
        assert token not in src, f"forbidden token found: {token!r}"
    assert "research-only" in src.lower() or "research_only" in src.lower()


def test_never_writes_production_artifact_paths():
    assert v11.OUT_JSON != base.OUT_JSON
    assert v11.HISTORY != base.HISTORY
    assert v11.OUT_TXT != base.OUT_TXT
    assert "v11" in v11.OUT_JSON.name
    assert "v11" in v11.HISTORY.name


def test_result_invariants_always_hold():
    args = v11.parse_args(["--offline-sample", "--skip-google-trends",
                           "--no-stocktwits"])
    res = v11.build(args)
    assert res["research_only"] is True
    assert res["promote_to_signal"] is False
    assert res["version"] == v11.VERSION


# ── bot/spam/noise heuristic ─────────────────────────────────────────────────


def test_noise_score_high_for_single_author_duplicate_spam():
    spam = [_item(text="buy the dip now!!!", author="hashA") for _ in range(10)]
    scores = v11.compute_noise_scores(spam)
    assert scores["AAPL"]["noise_score"] >= 60
    assert scores["AAPL"]["unique_authors"] == 1
    assert scores["AAPL"]["near_duplicate_ratio"] > 0.5


def test_noise_score_low_for_diverse_organic_messages():
    organic = [_item(text=f"thinking about earnings angle {i}", author=f"hash{i}")
              for i in range(10)]
    scores = v11.compute_noise_scores(organic)
    assert scores["AAPL"]["noise_score"] < 20
    assert scores["AAPL"]["unique_authors"] == 10


def test_noise_ignores_non_stocktwits_sources():
    items = [_item(text="x", author="a", source_type="google_trends")]
    scores = v11.compute_noise_scores(items)
    assert scores == {}


def test_normalize_text_for_dup_strips_urls_and_punctuation():
    a = v11._normalize_text_for_dup("BUY NOW!! https://x.co/abc")
    b = v11._normalize_text_for_dup("buy now https://y.co/def")
    assert a == b == "buy now"


# ── percentile ranks ──────────────────────────────────────────────────────────


def test_percentile_ranks_monotonic_and_bounded():
    ranks = v11._percentile_ranks([10.0, 50.0, 90.0, 30.0])
    assert min(ranks) == 0.0
    assert max(ranks) == 1.0
    assert all(0.0 <= r <= 1.0 for r in ranks)
    # highest value gets the highest rank
    assert ranks[2] == 1.0


def test_percentile_ranks_single_value():
    assert v11._percentile_ranks([42.0]) == [1.0]


# ── recalibrated crowd-stage classifier ──────────────────────────────────────


def test_classify_stage_v11_matches_production_for_exhaustion_and_viral():
    metrics = {"mention_count_24h": 100, "acceleration_ratio": 1.0, "meme_hits": 1}
    price = {"parabolic": True, "price_moving": True}
    options = {"speculative": True}
    assert base.classify_stage(metrics, price, options) == "EXHAUSTION_RISK"
    assert v11.classify_stage_v11(metrics, price, options, 0.9) == "EXHAUSTION_RISK"

    metrics2 = {"mention_count_24h": 80, "acceleration_ratio": 1.0, "meme_hits": 1}
    price2 = {"parabolic": False, "price_moving": True}
    assert base.classify_stage(metrics2, price2, {}) == "VIRAL_CROWDING"
    assert v11.classify_stage_v11(metrics2, price2, {}, 0.9) == "VIRAL_CROWDING"


def test_classify_stage_v11_broadening_via_percentile_not_diversity():
    """The production classifier requires source_diversity>=2 for
    BROADENING_ATTENTION -- structurally unreachable with a single active
    source (Reddit disabled, Trends near-zero yield). v1.1 reaches it via
    a high same-run percentile rank instead, with the same underlying
    metrics that keep production at NO_SIGNAL/EARLY_DISCOVERY."""
    metrics = {"mention_count_24h": 40, "acceleration_ratio": 1.0,
              "meme_hits": 0, "attention_velocity_score": 70.0}
    price = {"parabolic": False, "price_moving": True}
    options = {}
    prod = base.classify_stage(metrics, price, options)
    assert prod != "BROADENING_ATTENTION"  # diversity gate blocks it
    v11_stage = v11.classify_stage_v11(metrics, price, options,
                                       velocity_percentile=0.95)
    assert v11_stage == "BROADENING_ATTENTION"


def test_classify_stage_v11_low_percentile_stays_no_signal():
    metrics = {"mention_count_24h": 40, "acceleration_ratio": 1.0,
              "meme_hits": 0, "attention_velocity_score": 30.0}
    price = {"parabolic": False, "price_moving": False}
    stage = v11.classify_stage_v11(metrics, price, {}, velocity_percentile=0.1)
    assert stage == "NO_SIGNAL"


# ── source reliability weighting ─────────────────────────────────────────────


def test_source_weights_insufficient_history_below_floor():
    rows = [{"primary_source_type": "stocktwits", "crowd_stage": "STEALTH_ATTENTION"}] * 5
    res = v11.compute_source_reliability_weights(rows)
    assert res["status"] == "INSUFFICIENT_HISTORY"
    assert res["weights"] == {}
    assert res["rows_seen"] == 5


def test_source_weights_computed_once_floor_met():
    rows = ([{"primary_source_type": "stocktwits", "crowd_stage": "STEALTH_ATTENTION"}] * 100
           + [{"primary_source_type": "stocktwits", "crowd_stage": "NO_SIGNAL"}] * 100)
    res = v11.compute_source_reliability_weights(rows)
    assert res["status"] == "COMPUTED"
    assert res["weights"]["stocktwits"] == pytest.approx(0.5, abs=0.01)


# ── expanded watch universe ───────────────────────────────────────────────────


def test_expanded_universe_unions_production_and_extra_bucket(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "RECALL_SHADOW_LANE", tmp_path / "none1.json")
    monkeypatch.setattr(base, "RS_THEME_TRIAGE", tmp_path / "none2.json")
    monkeypatch.setattr(base, "ALPHA_BOARD", tmp_path / "none3.json")
    monkeypatch.setattr(dataio_module(), "RESEARCH_CACHE", tmp_path)

    scanner_path = tmp_path / "research_scanner_latest.json"
    scanner_path.write_text(json.dumps({"watchlist": [
        {"ticker": "ZZZZ"}, {"ticker": "YYYY"}, {"ticker": "AAPL"},
    ]}))

    universe = v11.build_expanded_watch_universe(cap=50, hard_max=50)
    assert "ZZZZ" in universe["universe"]
    assert "YYYY" in universe["universe"]
    # AAPL already present via MEGA_CAP bucket -> not double-counted
    assert universe["universe"].count("AAPL") == 1
    assert universe["source_counts"]["scanner_watchlist_extra"] >= 1


def dataio_module():
    from research.scanner_truth import dataio
    return dataio


# ── Top Market Attention merge ───────────────────────────────────────────────


# ── cohort join tags (Step C) ─────────────────────────────────────────────────


def _alpha_focus_fixture():
    def row(ticker, **kw):
        base_row = {"ticker": ticker, "is_high_conviction": False,
                   "is_profitable": True, "is_emerging_outlier": False,
                   "business_deterioration_risk": None}
        base_row.update(kw)
        return base_row
    return {
        "review_now": [row("TPG", is_high_conviction=True)],
        "higher_risk_eo_review": [row("WGS", is_profitable=False,
                                      is_emerging_outlier=True,
                                      business_deterioration_risk="LOW")],
        "wait_for_reset": [row("MGNI", is_high_conviction=True)],
        "deprioritized_by_focus_rule": [row("ABSI", is_profitable=False,
                                            business_deterioration_risk="HIGH")],
    }


def test_cohort_tag_index_and_lookup(tmp_path, monkeypatch):
    fixture_path = tmp_path / "alpha_focus_latest.json"
    fixture_path.write_text(json.dumps(_alpha_focus_fixture()))
    monkeypatch.setattr(v11, "ALPHA_FOCUS_JSON", fixture_path)

    idx = v11.build_cohort_tag_index()

    tpg = v11.cohort_tags_for("TPG", idx)
    assert tpg["is_high_conviction"] is True
    assert tpg["is_profitable_quality"] is True
    assert tpg["is_alpha_focus_review_now"] is True
    assert tpg["is_red_flag"] is False

    wgs = v11.cohort_tags_for("WGS", idx)
    assert wgs["is_emerging_outlier"] is True
    assert wgs["is_profitable_quality"] is False
    assert wgs["is_red_flag"] is True  # unprofitable -> red flag proxy

    absi = v11.cohort_tags_for("ABSI", idx)
    assert absi["is_red_flag"] is True  # HIGH business-deterioration-risk

    unknown = v11.cohort_tags_for("ZZZZ_NOT_PRESENT", idx)
    assert unknown == {
        "is_high_conviction": False, "is_emerging_outlier": False,
        "is_alpha_focus_review_now": False, "is_wait_for_reset": False,
        "is_profitable_quality": False, "is_red_flag": True,
    }  # absent ticker defaults to "not profitable" -> red-flag proxy fires


def test_cohort_tag_index_missing_artifact_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(v11, "ALPHA_FOCUS_JSON", tmp_path / "does_not_exist.json")
    assert v11.build_cohort_tag_index() == {}


def test_history_rows_include_join_tags_and_appearances(monkeypatch):
    monkeypatch.setattr(v11, "build_cohort_tag_index", lambda: {
        "TPG": {"is_high_conviction": True, "is_profitable": True,
               "is_emerging_outlier": False, "in_review_now": True,
               "in_wait_for_reset": False, "in_deprioritized": False,
               "business_deterioration_risk": None},
    })
    res = {"asof_date": "2026-08-28", "leads": [{
        "ticker": "TPG", "crowd_stage": "EARLY_DISCOVERY",
        "crowd_stage_v11": "EARLY_DISCOVERY", "stage_changed_v11": False,
        "lead_type": "UNKNOWN", "primary_source_type": "stocktwits",
        "velocity_percentile": 0.9, "noise": {"noise_score": 5.0},
        "history_appearances": 3, "mention_count_24h": 10,
        "attention_velocity_score": 60.0, "source_diversity_score": 28.0,
    }]}
    rows = v11._history_rows(res)
    assert rows[0]["history_appearances"] == 3
    assert rows[0]["is_high_conviction"] is True
    assert rows[0]["is_alpha_focus_review_now"] is True
    assert rows[0]["is_red_flag"] is False


def test_top_market_attention_tags_origin_and_merges_both():
    v11_res = {
        "leads": [
            {"ticker": "ABSI", "attention_velocity_score": 70.0,
             "crowd_stage": "STEALTH_ATTENTION", "crowd_stage_v11": "STEALTH_ATTENTION",
             "lead_type": "UNKNOWN", "label": "SOCIAL_ATTENTION_LEAD", "noise": {"noise_score": 10.0}},
            {"ticker": "MSFT", "attention_velocity_score": 60.0,
             "crowd_stage": "EARLY_DISCOVERY", "crowd_stage_v11": "EARLY_DISCOVERY",
             "lead_type": "UNKNOWN", "label": "SOCIAL_ATTENTION_LEAD", "noise": {"noise_score": 5.0}},
        ],
    }
    fake_news = {"items": [{"ticker": "MSFT", "deterministic_score": 75.7},
                           {"ticker": "AAPL", "deterministic_score": 72.7}]}
    with patch.object(json, "loads", wraps=json.loads):
        with patch("pathlib.Path.read_text", return_value=json.dumps(fake_news)):
            top = v11.build_top_market_attention(v11_res, cap=10)
    origins = {r["ticker"]: r["origin"] for r in top["rows"]}
    assert origins["MSFT"] == "BOTH"
    assert origins["ABSI"] == "SOCIAL_ATTENTION_V11"
    assert origins["AAPL"] == "NEWS_CATALYST"
    assert top["promote_to_signal"] is False
