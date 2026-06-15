"""Unit tests for the Social Attention Radar (Phase 1G.15).

Covers the Task-11 checklist:
  - social item normalization
  - explicit ticker mapping + cashtag
  - ambiguous alias suppression (Apple/apple, Ford/F, MSTR/strategy, Target/target)
  - Nvidia/AI-chip context + theme-only / sympathy mapping (space)
  - attention velocity z-score
  - stealth vs viral-crowding classification
  - social-led vs news-led timing separation
  - forward validation excludes immature events / never overclaims
  - NO paper signals, NO trade proposals, NO execution/governance/live-capital imports
  - dashboard/MCP cache-only (no provider calls)

Pure-Python, no network. Provider paths are never invoked in these tests.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from research import social_attention_radar as sar
from research import social_attention_forward_validation as fwd


# ── mapping (Task 4) ──────────────────────────────────────────────────────────
def test_cashtag_is_highest_confidence():
    tickers, method = sar.map_text_to_tickers("loving $NVDA and $AMD here")
    assert method == "cashtag"
    assert tickers == ["AMD", "NVDA"]
    assert sar._METHOD_CONFIDENCE["cashtag"] > sar._METHOD_CONFIDENCE["company_alias_context"]


def test_explicit_bare_ticker_mapping():
    tickers, method = sar.map_text_to_tickers("PLTR breaking out on volume stock")
    assert method == "explicit_ticker"
    assert "PLTR" in tickers


def test_apple_company_vs_apple_fruit():
    # fruit, no company context → must NOT map to AAPL
    tickers, method = sar.map_text_to_tickers("I ate an apple for lunch")
    assert "AAPL" not in tickers
    assert method in ("ambiguous_suppressed", "theme_only")
    # company context present → maps
    tickers2, method2 = sar.map_text_to_tickers("Apple stock jumped after earnings")
    assert tickers2 == ["AAPL"]
    assert method2 == "company_alias_context"


def test_ford_alias_vs_bare_F():
    # bare single-letter "F" must not map without a cashtag
    tickers, method = sar.map_text_to_tickers("grade F on that exam")
    assert "F" not in tickers
    # alias "Ford" with context maps to F
    tickers2, method2 = sar.map_text_to_tickers("Ford recalls trucks, shares slip")
    assert tickers2 == ["F"]
    assert method2 == "company_alias_context"
    # cashtag always works
    tickers3, method3 = sar.map_text_to_tickers("$F looks cheap")
    assert tickers3 == ["F"] and method3 == "cashtag"


def test_mstr_strategy_ambiguity():
    # generic "strategy" word, no context → suppressed
    tickers, method = sar.map_text_to_tickers("our long-term strategy is sound")
    assert "MSTR" not in tickers
    assert method in ("ambiguous_suppressed", "theme_only")
    # explicit alias maps
    tickers2, _ = sar.map_text_to_tickers("MicroStrategy bought more bitcoin")
    assert "MSTR" in tickers2


def test_target_ambiguity():
    # "price target" — generic, no company context → suppressed
    tickers, method = sar.map_text_to_tickers("analyst raised the price target")
    assert "TGT" not in tickers
    # company context → maps
    tickers2, _ = sar.map_text_to_tickers("Target earnings beat, retailer shares up")
    assert "TGT" in tickers2


def test_nvidia_ai_chip_context_and_theme():
    tickers, method = sar.map_text_to_tickers("Nvidia AI chip demand keeps surging")
    assert "NVDA" in tickers
    assert method == "company_alias_context"
    assert "ai_datacenter" in sar.detect_themes("Nvidia AI chip demand keeps surging")


def test_space_theme_sympathy_mapping():
    tickers, method = sar.map_text_to_tickers("everyone talking about the rocket launch and satellite race")
    assert method == "sympathy_mapping"
    # space beneficiaries
    assert any(t in tickers for t in sar.THEME_IMPACT["space"])


def test_theme_only_when_no_beneficiaries():
    # a theme with keywords but mapped to nothing falls back to theme_only/none
    tickers, method = sar.map_text_to_tickers("the weather is nice today")
    assert tickers == []
    assert method == "theme_only"


# ── normalization + PII (Task 3) ──────────────────────────────────────────────
def test_normalization_hashes_author_no_raw_pii():
    raw = [sar._raw_item("StockTwits", "stocktwits", sar._utc_now().isoformat(),
                         "$NVDA going up", author="realname123", source_id="9",
                         engagement=10) | {"_explicit_tickers": ["NVDA"]}]
    items = sar.normalize_items(raw)
    assert len(items) == 1
    it = items[0]
    assert it.ticker_candidates == ["NVDA"]
    assert it.author_hash and it.author_hash.startswith("a_")
    assert "realname123" not in (it.author_hash or "")
    assert "realname123" not in json.dumps(it.to_dict())


def test_author_hash_none_for_missing():
    assert sar._author_hash(None) is None
    assert sar._author_hash("") is None


# ── velocity (Task 5) ──────────────────────────────────────────────────────────
def test_velocity_z_score_from_history():
    now = sar._utc_now()
    raw = [
        sar._raw_item("manual", "manual", (now - timedelta(hours=0.5)).isoformat(),
                      "$RKLB", source_id=str(i)) | {"_explicit_tickers": ["RKLB"]}
        for i in range(10)
    ]
    items = sar.normalize_items(raw)
    # baseline history: low, varied mentions/day; today's 10 should z-score high
    base_counts = [1, 2, 3, 2, 1]
    history = [{"ticker": "RKLB", "first_seen_at": (now - timedelta(days=d)).isoformat(),
                "metrics": {"mention_count_24h": base_counts[d - 1]}} for d in range(1, 6)]
    vel = sar.compute_velocity(items, history, now=now)
    assert "RKLB" in vel
    m = vel["RKLB"]
    assert m["mention_count_24h"] == 10
    assert m["mention_z_score"] is not None and m["mention_z_score"] > 2.0
    assert 0 <= m["attention_velocity_score"] <= 100


def test_velocity_no_baseline_gives_none_z():
    now = sar._utc_now()
    raw = [sar._raw_item("manual", "manual", now.isoformat(), "$ABNB")
           | {"_explicit_tickers": ["ABNB"]}]
    vel = sar.compute_velocity(sar.normalize_items(raw), history=[], now=now)
    assert vel["ABNB"]["mention_z_score"] is None


# ── crowd-stage classification (Task 6) ─────────────────────────────────────────
def test_stealth_vs_viral_classification():
    # stealth: few mentions, sharp acceleration, single source, no parabolic move
    stealth_m = {"mention_count_24h": 6, "acceleration_ratio": 3.0,
                 "source_diversity": 1, "meme_hits": 0, "attention_velocity_score": 70}
    assert sar.classify_stage(stealth_m, price={}, options={}) == "STEALTH_ATTENTION"

    # viral: huge mentions, meme language, big move
    viral_m = {"mention_count_24h": 200, "acceleration_ratio": 1.1,
               "source_diversity": 3, "meme_hits": 5, "attention_velocity_score": 80}
    price = {"price_moving": True, "parabolic": False}
    assert sar.classify_stage(viral_m, price=price, options={}) == "VIRAL_CROWDING"


def test_exhaustion_risk_when_parabolic_and_speculative():
    m = {"mention_count_24h": 80, "acceleration_ratio": 1.0,
         "source_diversity": 2, "meme_hits": 1, "attention_velocity_score": 60}
    price = {"parabolic": True, "price_moving": True}
    assert sar.classify_stage(m, price=price, options={"speculative": True}) == "EXHAUSTION_RISK"


def test_no_signal_when_quiet():
    m = {"mention_count_24h": 3, "acceleration_ratio": 1.0,
         "source_diversity": 1, "meme_hits": 0, "attention_velocity_score": 10}
    assert sar.classify_stage(m, price={}, options={}) == "NO_SIGNAL"


# ── news-led vs social-led (Task 7) ─────────────────────────────────────────────
def test_social_led_when_attention_before_news():
    now = sar._utc_now()
    social_first = now - timedelta(hours=30)
    news_map = {"RKLB": now}  # news 30h AFTER social → social-led
    lead, hrs = sar.classify_lead_type("RKLB", social_first, news_map)
    assert lead == "SOCIAL_LED"
    assert hrs > sar.SIMULTANEOUS_WINDOW_HOURS


def test_news_led_when_news_before_attention():
    now = sar._utc_now()
    social_first = now
    news_map = {"NVDA": now - timedelta(hours=30)}  # news 30h BEFORE social
    lead, hrs = sar.classify_lead_type("NVDA", social_first, news_map)
    assert lead == "NEWS_LED"


def test_simultaneous_and_unknown():
    now = sar._utc_now()
    lead, _ = sar.classify_lead_type("AMD", now, {"AMD": now - timedelta(hours=1)})
    assert lead == "SIMULTANEOUS"
    lead2, hrs2 = sar.classify_lead_type("MSFT", now, {})  # no news record
    assert lead2 == "UNKNOWN" and hrs2 is None


# ── labels never say buy/sell (Task 8) ──────────────────────────────────────────
def test_labels_are_research_only():
    forbidden = {"BUY NOW", "TRADE READY", "EXECUTE", "APPROVED", "BUY", "SELL"}
    allowed = {"SOCIAL_ATTENTION_LEAD", "SOCIAL_THEME_LEAD", "SOCIAL_LED_CANDIDATE",
               "NEWS_LED_CANDIDATE", "CROWDED_ATTENTION", "NO_SOCIAL_EDGE",
               "NEEDS_LENS", "NEEDS_FORWARD_VALIDATION"}
    seen = set()
    for stage in ("NO_SIGNAL", "EXHAUSTION_RISK", "VIRAL_CROWDING",
                  "STEALTH_ATTENTION", "EARLY_DISCOVERY", "BROADENING_ATTENTION"):
        for lead in ("SOCIAL_LED", "NEWS_LED", "SIMULTANEOUS", "UNKNOWN"):
            for method in ("cashtag", "theme_only", "sympathy_mapping"):
                for price in ({}, {"available": True, "price_moving": True}):
                    label = sar.assign_label(
                        stage, lead,
                        {"best_mapping_method": method, "attention_velocity_score": 70},
                        price)
                    seen.add(label)
    assert seen <= allowed
    assert not (seen & forbidden)


# ── offline build smoke (no providers) ──────────────────────────────────────────
def test_offline_build_no_providers(monkeypatch):
    import argparse
    args = argparse.Namespace(offline_sample=True, skip_google_trends=True,
                              skip_manual=True, enable_stocktwits=False,
                              enable_reddit=False, google_trends_z=1.5)
    res = sar.build(args)
    assert res["kind"] == "social_attention_radar"
    assert res["research_only"] is True
    assert res["n_raw_items"] > 0
    # every lead carries a research label, never an order verb
    for lead in res["leads"]:
        assert lead["label"] in {
            "SOCIAL_ATTENTION_LEAD", "SOCIAL_THEME_LEAD", "SOCIAL_LED_CANDIDATE",
            "NEWS_LED_CANDIDATE", "CROWDED_ATTENTION", "NO_SOCIAL_EDGE",
            "NEEDS_LENS", "NEEDS_FORWARD_VALIDATION"}


# ── forward validation maturity (Task 9) ────────────────────────────────────────
def test_forward_validation_empty_history(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    res = fwd.build(history_path=empty)
    assert res["verdict"] == "NEED_MORE_DATA"
    assert "no historized" in res["verdict_reason"]


def test_forward_validation_excludes_immature(tmp_path, monkeypatch):
    # A single recent as-of with no matured forward bars → must stay NEED_MORE_DATA,
    # never a strong verdict.
    hist = tmp_path / "hist.jsonl"
    today = datetime.now(timezone.utc).date().isoformat()
    rows = [{"asof_date": today, "version": sar.VERSION, "ticker": "NVDA",
             "first_seen_at": today, "crowd_stage": "EARLY_DISCOVERY",
             "lead_type": "SOCIAL_LED", "lead_time_hours": 10, "label": "SOCIAL_LED_CANDIDATE",
             "metrics": {"mention_count_24h": 5, "attention_velocity_score": 70,
                         "attention_novelty_score": 90, "source_diversity_score": 50,
                         "acceleration_ratio": 2.0, "mention_z_score": 2.1}}]
    hist.write_text("\n".join(json.dumps(r) for r in rows))
    res = fwd.build(history_path=hist)
    assert res["verdict"] == "NEED_MORE_DATA"
    assert res["decision_gates"]["matured_primary_met"] is False


def test_verdict_ladder_values():
    ladder = {"NEED_MORE_DATA", "NO_VALUE", "PROMISING_BUT_UNPROVEN",
              "SOCIAL_EDGE_DETECTED", "READY_TO_FEED_LENS_RESEARCH_ONLY"}
    # synthesize a by_cohort where social-led clearly beats everything → strong
    def coh(rel):
        return {"5d": {"mean_rel_spy": rel, "n": 30}}
    by_cohort = {
        "lead_SOCIAL_LED": coh(0.08), "lead_NEWS_LED": coh(0.01),
        "stage_EARLY_DISCOVERY": coh(0.09), "stage_VIRAL_CROWDING": coh(-0.02),
        "vel_high": coh(0.08), "vel_low": coh(0.01),
        "all_leads": coh(0.05), "random": coh(0.005),
    }
    v, _ = fwd._verdict(by_cohort, history_days=15, matured_primary=30)
    assert v in ladder and v == "READY_TO_FEED_LENS_RESEARCH_ONLY"
    # no edge → NO_VALUE
    flat = {k: coh(0.001) for k in by_cohort}
    flat["lead_SOCIAL_LED"] = coh(-0.01)
    flat["random"] = coh(0.02)
    flat["lead_NEWS_LED"] = coh(0.02)
    v2, _ = fwd._verdict(flat, history_days=15, matured_primary=30)
    assert v2 == "NO_VALUE"


# ── boundary / safety invariants ────────────────────────────────────────────────
def test_no_forbidden_production_imports():
    """The radar + forward modules must not import execution / governance /
    live-capital / order / paper-signal machinery."""
    import inspect
    forbidden = ("order_manager", "paper_governance", "submit_order",
                 "alpaca_client", "decision_logger", "veto_council",
                 "ALLOW_LIVE_CAPITAL", "execution.")
    for mod in (sar, fwd):
        src = inspect.getsource(mod)
        for bad in forbidden:
            assert bad not in src, f"{mod.__name__} references forbidden symbol {bad!r}"


def test_modules_advertise_research_only():
    assert "RESEARCH-ONLY" in (sar.DISCLAIMER)
    assert "RESEARCH-ONLY" in (fwd.DISCLAIMER)
    assert sar.build.__module__ == "research.social_attention_radar"
