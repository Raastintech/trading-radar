"""Tests for research/alpha_heat_radar.py — Alpha Heat Radar V0.

Covers: topic detection (text-keyword + ticker-basket attribution),
topic-level velocity rollup from already-computed ticker metrics,
novelty/history idempotency, noise-flag detection, alpha-fit
cross-referencing, and the full conservative-by-design label ladder.
Everything is exercised through ``build(root=..., now=...)`` against a
tmp_path fixture tree so no real cache/data files are touched.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from research import alpha_heat_radar as ahr

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
ASOF = "2026-08-28"


# ── fixture builders ─────────────────────────────────────────────────────────
def _write(root: Path, rel: Path, obj) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _lead(ticker, *, sample_texts=None, mention_count_24h=10, attention_velocity_score=70.0,
          acceleration_ratio=2.0, best_confidence=0.85, best_mapping_method="explicit_ticker",
          crowd_stage="EARLY_DISCOVERY", history_appearances=3, unique_source_count=2,
          parabolic=False, rel_volume=1.0, noise_score=None):
    lead = {
        "ticker": ticker,
        "sample_texts": sample_texts if sample_texts is not None else [f"${ticker} chip news"],
        "mention_count_24h": mention_count_24h,
        "attention_velocity_score": attention_velocity_score,
        "acceleration_ratio": acceleration_ratio,
        "best_confidence": best_confidence,
        "best_mapping_method": best_mapping_method,
        "crowd_stage": crowd_stage,
        "history_appearances": history_appearances,
        "unique_source_count": unique_source_count,
        "price_overlay": {"available": True, "parabolic": parabolic, "rel_volume": rel_volume},
    }
    if noise_score is not None:
        lead["noise"] = {"noise_score": noise_score}
    return lead


def _sa_doc(leads, asof_date=ASOF, generated_at=None):
    return {"asof_date": asof_date, "generated_at": (generated_at or NOW.isoformat()), "leads": leads}


def _hc_row(ticker, *, bucket="shortlist", classification="HIGH_CONVICTION",
            extension_state="NORMAL", dead_horse_risk="LOW", sector_etf="XLK",
            same_session=True):
    return bucket, {
        "ticker": ticker, "classification": classification, "extension_state": extension_state,
        "dead_horse_risk": dead_horse_risk, "sector_etf": sector_etf, "same_session": same_session,
    }


def _hc_doc(rows):
    doc: dict = {"shortlist": [], "quality_but_extended": [], "improving_but_unproven": [], "rejected": []}
    for bucket, row in rows:
        doc[bucket].append(row)
    return {**doc, "generated_at": NOW.isoformat()}


def _eo_row(ticker, *, v1_classification="IMPROVING_BUT_UNPROVEN",
            business_deterioration_risk="LOW", dilution_3q_pct=0.0, same_session=True):
    return {"ticker": ticker, "v1_classification": v1_classification,
            "business_deterioration_risk": business_deterioration_risk,
            "dilution_3q_pct": dilution_3q_pct, "same_session": same_session}


def _eo_doc(rows):
    return {"watch": rows, "generated_at": NOW.isoformat()}


def _focus_row(ticker, *, bucket="review_now", is_profitable=True,
               is_reset_or_watch_for_entry=False, is_high_conviction=False,
               is_emerging_outlier=False, is_mid_or_known_cap=True, same_session=True):
    return bucket, {
        "ticker": ticker, "is_profitable": is_profitable,
        "is_reset_or_watch_for_entry": is_reset_or_watch_for_entry,
        "is_high_conviction": is_high_conviction, "is_emerging_outlier": is_emerging_outlier,
        "is_mid_or_known_cap": is_mid_or_known_cap, "same_session": same_session,
    }


def _focus_doc(rows):
    doc: dict = {"review_now": [], "higher_risk_eo_review": [], "wait_for_reset": [],
                 "deprioritized_by_focus_rule": []}
    for bucket, row in rows:
        doc[bucket].append(row)
    return {**doc, "generated_at": NOW.isoformat()}


def _board_row(ticker, *, action_label="Buyable Now", gatekeeper_status="PASS",
               validator_state="Buyable now"):
    return {"ticker": ticker, "action_label": action_label,
            "gatekeeper_status": gatekeeper_status, "validator_state": validator_state}


def _board_doc(rows):
    return {"items": rows, "generated_at": NOW.isoformat()}


def _arb_row(ticker, *, bucket="Options/Tape Confirmed", confidence="High"):
    return {"ticker": ticker, "bucket": bucket, "confidence": confidence}


def _arb_doc(rows):
    return {"reviewed_candidates": rows, "generated_at": NOW.isoformat()}


def _setup_min(root: Path, leads, v11_leads=None) -> None:
    """Write only the social_attention artifact (the required minimum)."""
    _write(root, ahr.SOCIAL_ATTENTION_REL, _sa_doc(leads))
    if v11_leads is not None:
        _write(root, ahr.SOCIAL_ATTENTION_V11_REL, _sa_doc(v11_leads))


# ── fallback / missing-source behavior ───────────────────────────────────────
def test_missing_both_primary_sources_returns_fallback(tmp_path):
    res = ahr.build(tmp_path, now=NOW)
    assert res["fallback"] == "MISSING_ARTIFACT"
    assert res["research_only"] is True
    assert res["promote_to_signal"] is False


def test_social_arb_alone_is_sufficient_to_avoid_fallback(tmp_path):
    _write(tmp_path, ahr.SOCIAL_ARB_REL, _arb_doc([_arb_row("NVDA")]))
    res = ahr.build(tmp_path, now=NOW)
    assert "fallback" not in res


# ── topic detection primitives ───────────────────────────────────────────────
def test_detect_topics_matches_known_terms():
    assert "Semiconductors" in ahr.detect_topics("big chip news today")


def test_detect_topics_no_match_returns_empty():
    assert ahr.detect_topics("just a normal day at the mall") == []


def test_ticker_topic_membership_basket():
    assert "AI/Data Center" in ahr.ticker_topic_membership("NVDA")
    assert ahr.ticker_topic_membership("ZZZZ") == []


# ── topic attribution + rollup ───────────────────────────────────────────────
def test_topic_attribution_via_text_keyword_only(tmp_path):
    # AAA is in no basket; only the text keyword should attribute it.
    _setup_min(tmp_path, [_lead("AAA", sample_texts=["$AAA chip news"])])
    res = ahr.build(tmp_path, now=NOW)
    topics = {t["topic"] for t in res["topics"]}
    assert "Semiconductors" in topics


def test_topic_attribution_via_ticker_basket_only(tmp_path):
    # bare cashtag chatter with no descriptive keyword at all.
    _setup_min(tmp_path, [_lead("RKLB", sample_texts=["$RKLB ✅✅✅"])])
    res = ahr.build(tmp_path, now=NOW)
    topics = {t["topic"] for t in res["topics"]}
    assert "Space" in topics


def test_non_actionable_ticker_never_produces_an_alert(tmp_path):
    # SPY text contains a topic keyword but must never be surfaced as a
    # research target ticker.
    _setup_min(tmp_path, [
        _lead("SPY", sample_texts=["$SPY chip rally today"]),
        _lead("AAA", sample_texts=["$AAA chip rally too"]),
    ])
    res = ahr.build(tmp_path, now=NOW)
    tickers = {a["ticker"] for a in res["alerts"]}
    assert "SPY" not in tickers
    assert "AAA" in tickers


def test_topic_velocity_is_volume_weighted_mean(tmp_path):
    leads = [
        _lead("AAA", sample_texts=["$AAA chip"], mention_count_24h=90, attention_velocity_score=10.0,
              acceleration_ratio=1.0),
        _lead("BBB", sample_texts=["$BBB chip"], mention_count_24h=10, attention_velocity_score=90.0,
              acceleration_ratio=1.0),
    ]
    _setup_min(tmp_path, leads)
    res = ahr.build(tmp_path, now=NOW)
    semi = next(t for t in res["topics"] if t["topic"] == "Semiconductors")
    # weighted mean: (90*10 + 10*90) / 100 = 18.0
    assert semi["velocity_score"] == 18.0
    assert semi["contributing_ticker_count"] == 2


def test_a_v11_only_ticker_is_no_longer_ingested(tmp_path):
    """V11 was SUPERSEDED on 2026-09-09 (drift audit): no cadence, 12.6 days
    stale, duplicating the nightly V0 radar. It used to contribute shadow-only
    tickers; now it contributes nothing, so this radar reads one social lane.
    The artifact is still written and still read — just not trusted as current.
    """
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([]))
    _write(tmp_path, ahr.SOCIAL_ATTENTION_V11_REL,
           _sa_doc([_lead("AAA", sample_texts=["$AAA chip"])]))
    res = ahr.build(tmp_path, now=NOW)
    assert not [a for a in res["alerts"] if a["ticker"] == "AAA"]


def test_v11_duplicate_ticker_prefers_prod_not_double_counted(tmp_path):
    prod_lead = _lead("AAA", sample_texts=["$AAA chip"], mention_count_24h=5)
    shadow_lead = _lead("AAA", sample_texts=["$AAA chip"], mention_count_24h=999)
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([prod_lead]))
    _write(tmp_path, ahr.SOCIAL_ATTENTION_V11_REL, _sa_doc([shadow_lead]))
    res = ahr.build(tmp_path, now=NOW)
    semi = next(t for t in res["topics"] if t["topic"] == "Semiconductors")
    assert semi["mention_volume"] == 5
    assert semi["contributing_ticker_count"] == 1


# ── label ladder ─────────────────────────────────────────────────────────────
def test_insufficient_mapping_confidence_beats_everything_else(tmp_path):
    lead = _lead("AAA", best_confidence=0.35, best_mapping_method="sympathy_mapping")
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([lead]))
    _write(tmp_path, ahr.HIGH_CONVICTION_REL, _hc_doc([_hc_row("AAA")]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] == ahr.LABEL_INSUFFICIENT_MAPPING


def test_redflag_noise_from_eo_deterioration_risk(tmp_path):
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([_lead("AAA")]))
    _write(tmp_path, ahr.EMERGING_OUTLIER_REL,
           _eo_doc([_eo_row("AAA", business_deterioration_risk="HIGH")]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] == ahr.LABEL_REDFLAG_NOISE


def test_redflag_noise_from_dilution(tmp_path):
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([_lead("AAA")]))
    _write(tmp_path, ahr.EMERGING_OUTLIER_REL,
           _eo_doc([_eo_row("AAA", dilution_3q_pct=12.0)]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] == ahr.LABEL_REDFLAG_NOISE


def test_exhaustion_risk_from_crowd_stage(tmp_path):
    _setup_min(tmp_path, [_lead("AAA", crowd_stage="VIRAL_CROWDING")])
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] == ahr.LABEL_EXHAUSTION_RISK


def test_exhaustion_risk_from_parabolic_price(tmp_path):
    _setup_min(tmp_path, [_lead("AAA", parabolic=True)])
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] == ahr.LABEL_EXHAUSTION_RISK


def test_a_v11_noise_score_no_longer_changes_the_label(tmp_path):
    """A superseded lane must not move a live label. Before 2026-09-09 a stale
    V11 noise score could flip a name to EXHAUSTION_RISK; the live V0 lead now
    decides on its own.
    """
    prod_lead = _lead("AAA")
    shadow_lead = _lead("AAA", noise_score=85.0)
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([prod_lead]))
    _write(tmp_path, ahr.SOCIAL_ATTENTION_V11_REL, _sa_doc([shadow_lead]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] != ahr.LABEL_EXHAUSTION_RISK
    assert alert.get("shadow_only") is not True


def test_watch_for_reset_when_extended_and_positive_fit(tmp_path):
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([_lead("AAA")]))
    _write(tmp_path, ahr.HIGH_CONVICTION_REL,
           _hc_doc([_hc_row("AAA", extension_state="PARABOLIC")]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] == ahr.LABEL_WATCH_FOR_RESET


def test_research_now_requires_hot_topic_and_positive_fit_and_no_noise(tmp_path):
    hot_lead = _lead("AAA", mention_count_24h=50, attention_velocity_score=90.0,
                     acceleration_ratio=2.0, unique_source_count=5, history_appearances=1)
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([hot_lead]))
    _write(tmp_path, ahr.ALPHA_FOCUS_REL,
           _focus_doc([_focus_row("AAA", is_profitable=True)]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] == ahr.LABEL_RESEARCH_NOW


def test_research_now_blocked_by_a_generic_noise_flag(tmp_path):
    # Hot + positive fit, but single-source spike (no arb corroboration) should
    # prevent RESEARCH_NOW and fall through to CONFIRMATION_ONLY instead.
    hot_lead = _lead("AAA", mention_count_24h=50, attention_velocity_score=90.0,
                     acceleration_ratio=2.0, unique_source_count=1, history_appearances=1)
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([hot_lead]))
    _write(tmp_path, ahr.ALPHA_FOCUS_REL,
           _focus_doc([_focus_row("AAA", is_profitable=True)]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] != ahr.LABEL_RESEARCH_NOW
    assert "SINGLE_SOURCE_SPIKE" in alert["noise_risk_flags"]


def test_confirmation_only_when_fit_positive_but_topic_not_hot(tmp_path):
    cold_lead = _lead("AAA", mention_count_24h=1, attention_velocity_score=5.0,
                      acceleration_ratio=1.0, unique_source_count=5)
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([cold_lead]))
    _write(tmp_path, ahr.ALPHA_FOCUS_REL,
           _focus_doc([_focus_row("AAA", is_profitable=True)]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] == ahr.LABEL_CONFIRMATION_ONLY


def test_confirmation_only_when_arb_confirmed_but_no_fit(tmp_path):
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([_lead("AAA")]))
    _write(tmp_path, ahr.SOCIAL_ARB_REL, _arb_doc([_arb_row("AAA")]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] == ahr.LABEL_CONFIRMATION_ONLY


def test_social_noise_when_no_support_and_no_fit(tmp_path):
    _setup_min(tmp_path, [_lead("AAA")])
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["operator_label"] == ahr.LABEL_SOCIAL_NOISE
    assert "SOCIAL_ONLY_NO_NEWS_OR_FUNDAMENTAL_SUPPORT" in alert["noise_risk_flags"]


# ── alpha fit cross-referencing ──────────────────────────────────────────────
def test_alpha_fit_prefers_alpha_focus_booleans_when_present(tmp_path):
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([_lead("AAA")]))
    _write(tmp_path, ahr.ALPHA_FOCUS_REL,
           _focus_doc([_focus_row("AAA", is_profitable=True, is_high_conviction=True)]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    # 15 profitable + 30 high_conviction + 5 is_mid_or_known_cap (builder default True)
    assert alert["alpha_fit_score"] == 50.0


def test_alpha_fit_falls_back_to_hc_when_no_focus_row(tmp_path):
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([_lead("AAA")]))
    _write(tmp_path, ahr.HIGH_CONVICTION_REL,
           _hc_doc([_hc_row("AAA", classification="HIGH_CONVICTION")]))
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["alpha_fit_score"] == 30.0


def test_sector_leading_true_when_sector_etf_in_leading_list(tmp_path):
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([_lead("AAA")]))
    _write(tmp_path, ahr.HIGH_CONVICTION_REL, _hc_doc([_hc_row("AAA", sector_etf="XLK")]))
    _write(tmp_path, ahr.SECTOR_LEADERSHIP_REL,
           {"leading_sectors_20d": ["XLK"], "generated_at": NOW.isoformat()})
    res = ahr.build(tmp_path, now=NOW)
    alert = next(a for a in res["alerts"] if a["ticker"] == "AAA")
    assert alert["sector_leading"] is True


# ── novelty / history ─────────────────────────────────────────────────────────
def test_novelty_first_run_has_no_prior_history(tmp_path):
    _setup_min(tmp_path, [_lead("AAA")])
    res = ahr.build(tmp_path, now=NOW)
    topic = next(t for t in res["topics"] if t["topic"] == "Semiconductors")
    assert topic["n_prior_sessions"] == 0
    assert topic["history_maturity"] == "INSUFFICIENT_HISTORY"
    assert topic["sudden_change_z"] is None
    assert topic["novelty_score"] > 0


def test_novelty_drops_for_a_topic_recycled_every_session(tmp_path):
    history_rows = [
        {"asof_date": f"2026-08-{d:02d}", "version": ahr.VERSION, "topic": "Semiconductors",
         "mention_volume": 10, "contributing_ticker_count": 1, "velocity_score": 50.0}
        for d in range(10, 28)
    ]
    hist_path = tmp_path / ahr.HISTORY_REL
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text("\n".join(json.dumps(r) for r in history_rows) + "\n")
    _setup_min(tmp_path, [_lead("AAA")])
    res = ahr.build(tmp_path, now=NOW)
    topic = next(t for t in res["topics"] if t["topic"] == "Semiconductors")
    assert topic["is_recycled_topic"] is True
    assert topic["novelty_score"] < 20.0


def test_history_append_is_idempotent_per_day(tmp_path):
    _setup_min(tmp_path, [_lead("AAA")])
    res1 = ahr.build(tmp_path, now=NOW)
    ahr.write_artifacts(res1, tmp_path)
    res2 = ahr.build(tmp_path, now=NOW)
    ahr.write_artifacts(res2, tmp_path)
    rows = ahr._load_jsonl(tmp_path / ahr.HISTORY_REL)
    semi_rows = [r for r in rows if r["topic"] == "Semiconductors" and r["asof_date"] == ASOF]
    assert len(semi_rows) == 1


# ── freshness / guardrails / rendering ───────────────────────────────────────
def test_stale_source_artifact_is_flagged(tmp_path):
    _write(tmp_path, ahr.SOCIAL_ATTENTION_REL, _sa_doc([_lead("AAA")]))
    _write(tmp_path, ahr.HIGH_CONVICTION_REL,
           {**_hc_doc([_hc_row("AAA")]), "generated_at": "2026-01-01T00:00:00+00:00"})
    res = ahr.build(tmp_path, now=NOW)
    assert res["source_dependencies"]["high_conviction_alpha"]["stale"] is True


def test_guardrails_are_all_true_and_research_only(tmp_path):
    _setup_min(tmp_path, [_lead("AAA")])
    res = ahr.build(tmp_path, now=NOW)
    assert res["research_only"] is True
    assert res["promote_to_signal"] is False
    g = dict(res["guardrails"])
    g.pop("promote_to_signal")  # intentionally False — checked above, not part of the all-True set
    assert all(g.values())


def test_render_txt_and_md_do_not_crash_on_fallback():
    res = {"version": ahr.VERSION, "generated_at": NOW.isoformat(),
           "fallback": "MISSING_ARTIFACT", "missing_artifacts": ["x"]}
    assert ahr.render_txt(res)
    assert ahr.render_md(res)


def test_render_txt_and_md_normal_path(tmp_path):
    _setup_min(tmp_path, [_lead("AAA")])
    res = ahr.build(tmp_path, now=NOW)
    assert any("Semiconductors" in line for line in ahr.render_txt(res))
    assert any("Semiconductors" in line for line in ahr.render_md(res))


def test_write_artifacts_creates_json_md_txt(tmp_path):
    _setup_min(tmp_path, [_lead("AAA")])
    res = ahr.build(tmp_path, now=NOW)
    paths = ahr.write_artifacts(res, tmp_path)
    assert Path(paths["json"]).exists()
    assert Path(paths["markdown"]).exists()
    assert Path(paths["text"]).exists()
    written = json.loads(Path(paths["json"]).read_text())
    assert "_new_history_rows" not in written
