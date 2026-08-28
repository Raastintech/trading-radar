"""Tests for research/topic_shock_detector.py — Topic Shock Detector V0.

Covers: template/syndication suppression (never silent), document-frequency
phrase extraction (deduped within a doc, not raw occurrence), cross-ticker
breadth requirements for cluster formation, known-topic cluster merging,
never-invented ticker mapping, phrase-history idempotency + honest cold
start, the full conservative TOPIC_ label ladder in its exact priority
order, and reuse (not duplication) of Alpha Heat Radar's alpha-fit/noise
functions.  Everything runs through ``build(root=..., now=...)`` against a
tmp_path fixture tree — no real cache/data files are touched.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from research import alpha_heat_radar as ahr
from research import topic_shock_detector as tsd

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
ASOF = "2026-08-28"


# ── fixture builders ─────────────────────────────────────────────────────────
def _write(root: Path, rel: Path, obj) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _arb_item(title, *, source="example.com", tickers_mentioned=None, ticker_evidence=None):
    return {"title": title, "source": source, "tickers_mentioned": tickers_mentioned or [],
            "ticker_evidence": ticker_evidence or {}}


def _arb_raw_doc(items, built_at=None):
    return {"version": "x", "built_at": (built_at or NOW.isoformat())[:10], "normalized_items": items}


def _lead(ticker, *, sample_texts=None, best_confidence=0.85,
          crowd_stage="EARLY_DISCOVERY", history_appearances=1, unique_source_count=3,
          parabolic=False, rel_volume=1.0):
    return {
        "ticker": ticker,
        "sample_texts": sample_texts if sample_texts is not None else [f"${ticker} update"],
        "best_confidence": best_confidence,
        "crowd_stage": crowd_stage,
        "history_appearances": history_appearances,
        "unique_source_count": unique_source_count,
        "mention_count_24h": 5,
        "attention_velocity_score": 50.0,
        "acceleration_ratio": 1.0,
        "price_overlay": {"available": True, "parabolic": parabolic, "rel_volume": rel_volume},
    }


def _sa_doc(leads, asof_date=ASOF):
    return {"asof_date": asof_date, "generated_at": NOW.isoformat(), "leads": leads}


def _hc_doc(rows):
    doc: dict = {"shortlist": [], "quality_but_extended": [], "improving_but_unproven": [], "rejected": []}
    for bucket, row in rows:
        doc[bucket].append(row)
    return {**doc, "generated_at": NOW.isoformat()}


def _hc_row(ticker, *, bucket="shortlist", classification="HIGH_CONVICTION",
           extension_state="NORMAL", dead_horse_risk="LOW", sector_etf="XLK"):
    return bucket, {"ticker": ticker, "classification": classification,
                    "extension_state": extension_state, "dead_horse_risk": dead_horse_risk,
                    "sector_etf": sector_etf, "same_session": True}


def _focus_doc(rows):
    doc: dict = {"review_now": [], "higher_risk_eo_review": [], "wait_for_reset": [],
                 "deprioritized_by_focus_rule": []}
    for bucket, row in rows:
        doc[bucket].append(row)
    return {**doc, "generated_at": NOW.isoformat()}


def _focus_row(ticker, *, bucket="review_now", is_profitable=True,
              is_reset_or_watch_for_entry=False, is_high_conviction=False,
              is_emerging_outlier=False, is_mid_or_known_cap=False):
    return bucket, {"ticker": ticker, "is_profitable": is_profitable,
                    "is_reset_or_watch_for_entry": is_reset_or_watch_for_entry,
                    "is_high_conviction": is_high_conviction,
                    "is_emerging_outlier": is_emerging_outlier,
                    "is_mid_or_known_cap": is_mid_or_known_cap, "same_session": True}


def _eo_doc(rows):
    return {"watch": rows, "generated_at": NOW.isoformat()}


def _eo_row(ticker, *, business_deterioration_risk="LOW", dilution_3q_pct=0.0):
    return {"ticker": ticker, "v1_classification": "IMPROVING_BUT_UNPROVEN",
            "business_deterioration_risk": business_deterioration_risk,
            "dilution_3q_pct": dilution_3q_pct, "same_session": True}


def _setup_min(root: Path, arb_items=None, leads=None) -> None:
    if arb_items is not None:
        _write(root, tsd.SOCIAL_ARB_RAW_REL, _arb_raw_doc(arb_items))
    if leads is not None:
        _write(root, ahr.SOCIAL_ATTENTION_REL, _sa_doc(leads))


def _history_row(name, asof_date, document_count, known_topic=None):
    return {"asof_date": asof_date, "version": tsd.VERSION, "name": name,
            "known_topic": known_topic, "document_count": document_count}


def _write_history(root: Path, rows) -> None:
    path = root / tsd.HISTORY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


# A cross-ticker, non-template, non-known-topic headline set used by most
# tests.  Filler text after the shared "widget surge" bigram deliberately
# varies per document so no OTHER bigram/trigram ties it on document
# frequency — that keeps "widget surge" the unambiguous, deterministic
# cluster name instead of leaving it to an arbitrary tie-break.
_FILLERS = ["as trading desks react", "as analysts flag renewed demand", "across regional markets"]


def _emergent_arb_items(n=3, tickers=("AAA", "BBB", "CCC")):
    items = []
    for i in range(n):
        t = tickers[i % len(tickers)]
        filler = _FILLERS[i % len(_FILLERS)]
        items.append(_arb_item(f"{t} widget surge {filler}", tickers_mentioned=[t],
                               ticker_evidence={t: {"direct_symbol": True}}))
    return items


def _find_alert(res, evidence_phrase="widget surge"):
    return next(a for a in res["alerts"] if evidence_phrase in a["evidence_phrases"])


# ── fallback / missing-source behavior ───────────────────────────────────────
def test_missing_both_primary_sources_returns_fallback(tmp_path):
    res = tsd.build(tmp_path, now=NOW)
    assert res["fallback"] == "MISSING_ARTIFACT"
    assert res["research_only"] is True
    assert res["promote_to_signal"] is False
    assert res["no_routing_change"] is True


def test_arb_raw_alone_is_sufficient_to_avoid_fallback(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    res = tsd.build(tmp_path, now=NOW)
    assert "fallback" not in res


def test_no_crash_on_all_suppressed_corpus(tmp_path):
    items = [_arb_item("Rosen Law Firm Encourages XYZ Investors to Inquire About Securities Class Action",
                       tickers_mentioned=["XYZ"])] * 3
    _setup_min(tmp_path, arb_items=items)
    res = tsd.build(tmp_path, now=NOW)
    assert res["alerts"] == []
    assert res["corpus"]["clusters_formed"] == 0
    assert len(res["suppressed_as_template"]) == 1


# ── Step 2 — syndication / template suppression (never silent) ─────────────
def test_template_suppression_class_action_reported_not_dropped(tmp_path):
    items = _emergent_arb_items() + [
        _arb_item("PNR INVESTOR DEADLINE: Robbins Geller Files Class Action Lawsuit Against Pentair",
                  tickers_mentioned=["PNR"]),
        _arb_item("CAPR Investors with Substantial Losses Have Opportunity to Lead Class Action",
                  tickers_mentioned=["CAPR"]),
    ]
    _setup_min(tmp_path, arb_items=items)
    res = tsd.build(tmp_path, now=NOW)
    row = next(s for s in res["suppressed_as_template"] if s["matched_pattern"] == "LAW_FIRM_CLASS_ACTION")
    assert row["document_count"] == 2
    assert "PNR" in row["tickers"] and "CAPR" in row["tickers"]
    assert row["matched_phrase"]
    suppressed_titles_tickers = {t for s in res["suppressed_as_template"] for t in s["tickers"]}
    mapped = {t["ticker"] for a in res["alerts"] for t in a["mapped_tickers"]}
    assert not (suppressed_titles_tickers & mapped)


def test_template_suppression_undiscovered_breakout(tmp_path):
    items = _emergent_arb_items() + [
        _arb_item("XYZ: An Undiscovered Breakout Candidate", tickers_mentioned=["XYZ"]),
        _arb_item("ABC Is An Undiscovered Breakout Stock", tickers_mentioned=["ABC"]),
    ]
    _setup_min(tmp_path, arb_items=items)
    res = tsd.build(tmp_path, now=NOW)
    assert any(s["matched_pattern"] == "UNDISCOVERED_BREAKOUT_NEWSLETTER" for s in res["suppressed_as_template"])


def test_template_suppression_estimize_marketbeat_bot(tmp_path):
    leads = [_lead("AAA", sample_texts=["$AAA historical amp metric name eps www.estimize.com/AAA"])]
    _setup_min(tmp_path, arb_items=_emergent_arb_items(), leads=leads)
    res = tsd.build(tmp_path, now=NOW)
    assert any(s["matched_pattern"] == "ESTIMIZE_MARKETBEAT_BOT" for s in res["suppressed_as_template"])


def test_template_suppression_generic_listicle(tmp_path):
    items = _emergent_arb_items() + [_arb_item("Stock Market Today: Dow Futures Mixed Before The Bell")]
    _setup_min(tmp_path, arb_items=items)
    res = tsd.build(tmp_path, now=NOW)
    assert any(s["matched_pattern"] == "GENERIC_LISTICLE_ROUNDUP" for s in res["suppressed_as_template"])


def test_template_suppression_institutional_holdings_boilerplate(tmp_path):
    items = _emergent_arb_items() + [
        _arb_item("Acme Wealth Management LLC Boosted Its Stake in AAPL", tickers_mentioned=["AAPL"]),
    ]
    _setup_min(tmp_path, arb_items=items)
    res = tsd.build(tmp_path, now=NOW)
    assert any(s["matched_pattern"] == "INSTITUTIONAL_HOLDINGS_BOILERPLATE" for s in res["suppressed_as_template"])


# ── Step 3 — phrase extraction / document frequency ─────────────────────────
def test_phrase_extraction_dedupes_within_one_document():
    toks = tsd._tokens("chip chip chip demand demand")
    grams = tsd._ngrams(toks, 2)
    assert "chip demand" in grams  # a set: repetition inside one doc collapses


def test_html_entities_are_unescaped_before_tokenizing():
    cleaned = tsd._strip_urls("Nvidia &amp; AMD chip demand surges")
    assert "amp" not in tsd._tokens(cleaned)


def test_document_frequency_counts_documents_not_occurrences():
    doc_index = {
        "d1": {"chip demand", "gpu supply"},   # phrase appears many times in d1's raw text,
        "d2": {"chip demand"},                 # but the set already dedups that to one hit
    }
    df = tsd.document_frequency(doc_index)
    assert df["chip demand"] == 2
    assert df["gpu supply"] == 1


# ── Step 4 — cluster formation ───────────────────────────────────────────────
def test_cluster_requires_minimum_ticker_breadth(tmp_path):
    # Same phrase repeated across 3 docs but only ONE distinct ticker: not a topic.
    items = [_arb_item(f"AAA widget surge day {i}", tickers_mentioned=["AAA"]) for i in range(4)]
    _setup_min(tmp_path, arb_items=items)
    res = tsd.build(tmp_path, now=NOW)
    assert res["corpus"]["clusters_formed"] == 0


def test_cluster_forms_with_sufficient_breadth_and_frequency(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items(n=3))
    res = tsd.build(tmp_path, now=NOW)
    assert res["corpus"]["clusters_formed"] >= 1
    names = {a["topic"] for a in res["alerts"]}
    assert "widget surge" in names


def test_known_topic_clusters_are_merged_not_duplicated(tmp_path):
    # Two independently-seeded phrase groups that each separately detect as
    # "Semiconductors" via ahr.detect_topics must collapse into ONE alert,
    # not two.  Each group needs >=3 docs to clear MIN_CLUSTER_DOCUMENT_FREQUENCY.
    chip_group = [
        _arb_item(f"{t} chip demand accelerates", tickers_mentioned=[t],
                  ticker_evidence={t: {"direct_symbol": True}})
        for t in ("AAA", "BBB", "CCC")
    ]
    foundry_group = [
        _arb_item(f"{t} semiconductor foundry capacity shifts", tickers_mentioned=[t],
                  ticker_evidence={t: {"direct_symbol": True}})
        for t in ("DDD", "EEE", "FFF")
    ]
    _setup_min(tmp_path, arb_items=chip_group + foundry_group)
    res = tsd.build(tmp_path, now=NOW)
    semi_alerts = [a for a in res["alerts"] if a["topic"] == "Semiconductors"]
    assert len(semi_alerts) == 1
    mapped = {t["ticker"] for t in semi_alerts[0]["mapped_tickers"]}
    assert {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF"} <= mapped


# ── Step 6 — ticker mapping (never invented) ────────────────────────────────
def test_non_actionable_ticker_never_mapped(tmp_path):
    items = [
        _arb_item("SPY AAA widget surge", tickers_mentioned=["SPY", "AAA"],
                  ticker_evidence={"SPY": {"direct_symbol": True}, "AAA": {"direct_symbol": True}}),
        _arb_item("BBB widget surge continues", tickers_mentioned=["BBB"],
                  ticker_evidence={"BBB": {"direct_symbol": True}}),
        _arb_item("CCC widget surge broadens", tickers_mentioned=["CCC"],
                  ticker_evidence={"CCC": {"direct_symbol": True}}),
    ]
    _setup_min(tmp_path, arb_items=items)
    res = tsd.build(tmp_path, now=NOW)
    mapped = {t["ticker"] for a in res["alerts"] for t in a["mapped_tickers"]}
    assert "SPY" not in mapped
    assert "AAA" in mapped


def test_ticker_evidence_confidence_tiers():
    cluster = {
        "known_topic": None,
        "documents": [
            {"pipeline": "social_arb_raw", "tickers_hint": ["AAA"], "ticker_confidence": {"AAA": 0.85}},
            {"pipeline": "social_arb_raw", "tickers_hint": ["BBB"], "ticker_confidence": {"BBB": 0.20}},
        ],
    }
    mapped = {m["ticker"]: m for m in tsd.map_cluster_tickers(cluster)}
    assert mapped["AAA"]["confidence"] == 0.85 and mapped["AAA"]["qualifies"] is True
    assert mapped["BBB"]["confidence"] == 0.20 and mapped["BBB"]["qualifies"] is False


def test_sector_basket_enrichment_is_lower_confidence_and_does_not_override_explicit():
    cluster = {
        "known_topic": "AI/Data Center",
        "documents": [
            {"pipeline": "social_arb_raw", "tickers_hint": ["NVDA"], "ticker_confidence": {"NVDA": 0.85}},
        ],
    }
    mapped = {m["ticker"]: m for m in tsd.map_cluster_tickers(cluster)}
    assert mapped["NVDA"]["confidence"] == 0.85  # explicit evidence wins, basket doesn't downgrade it
    # AMD is in the AI/Data Center basket but was never explicitly mentioned:
    assert mapped["AMD"]["method"] == "sector_basket"
    assert mapped["AMD"]["confidence"] < tsd.MIN_MAPPING_CONFIDENCE
    assert mapped["AMD"]["qualifies"] is False


def test_topic_mapping_weak_when_nothing_clears_the_floor(tmp_path):
    items = [
        _arb_item("AAA widget surge", tickers_mentioned=["AAA"], ticker_evidence={"AAA": {"ambiguous_hit": True}}),
        _arb_item("BBB widget surge", tickers_mentioned=["BBB"], ticker_evidence={"BBB": {"ambiguous_hit": True}}),
        _arb_item("CCC widget surge", tickers_mentioned=["CCC"], ticker_evidence={"CCC": {"ambiguous_hit": True}}),
    ]
    _setup_min(tmp_path, arb_items=items)
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    assert alert["operator_label"] == tsd.LABEL_MAPPING_WEAK


# ── label ladder (fixed, most-conservative-wins priority) ───────────────────
def test_redflag_noise_beats_positive_fit(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    _write(tmp_path, ahr.EMERGING_OUTLIER_REL,
           _eo_doc([_eo_row("AAA", business_deterioration_risk="HIGH")]))
    _write(tmp_path, ahr.ALPHA_FOCUS_REL, _focus_doc([_focus_row("AAA", is_profitable=True)]))
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    assert alert["operator_label"] == tsd.LABEL_REDFLAG_NOISE


def test_exhaustion_risk_beats_positive_fit(tmp_path):
    leads = [_lead("AAA", crowd_stage="VIRAL_CROWDING", sample_texts=["$AAA widget surge"])]
    _setup_min(tmp_path, arb_items=_emergent_arb_items(), leads=leads)
    _write(tmp_path, ahr.ALPHA_FOCUS_REL, _focus_doc([_focus_row("AAA", is_profitable=True)]))
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    assert alert["operator_label"] == tsd.LABEL_EXHAUSTION_RISK


def test_old_rechurn_beats_watch_for_reset_and_research_now(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    _write(tmp_path, ahr.ALPHA_FOCUS_REL, _focus_doc([_focus_row("AAA", is_profitable=True)]))
    history = [_history_row("widget surge", f"2026-08-{d:02d}", 5) for d in range(10, 28)]
    _write_history(tmp_path, history)
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    assert alert["operator_label"] == tsd.LABEL_OLD_RECHURN


def test_watch_for_reset_when_best_ticker_extended(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    _write(tmp_path, ahr.HIGH_CONVICTION_REL, _hc_doc([_hc_row("AAA", extension_state="PARABOLIC")]))
    _write(tmp_path, ahr.ALPHA_FOCUS_REL, _focus_doc([_focus_row("AAA", is_profitable=True)]))
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    assert alert["operator_label"] == tsd.LABEL_WATCH_FOR_RESET


def test_research_now_blocked_on_first_run_with_no_prior_history(tmp_path):
    # Even with a hot cluster and positive fit, no phrase history means no
    # confirmed acceleration -> must NOT claim RESEARCH_NOW on a cold start.
    items = _emergent_arb_items(n=6)
    _setup_min(tmp_path, arb_items=items)
    _write(tmp_path, ahr.ALPHA_FOCUS_REL, _focus_doc([_focus_row("AAA", is_profitable=True)]))
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    assert alert["operator_label"] != tsd.LABEL_RESEARCH_NOW
    assert alert["history_maturity"] == "INSUFFICIENT_HISTORY"
    assert alert["acceleration_ratio"] is None


def test_research_now_requires_hot_accelerating_confident_and_clean(tmp_path):
    items = _emergent_arb_items(n=6)
    _setup_min(tmp_path, arb_items=items)
    _write(tmp_path, ahr.ALPHA_FOCUS_REL, _focus_doc([_focus_row("AAA", is_profitable=True)]))
    history = [_history_row("widget surge", f"2026-08-{d:02d}", 1) for d in range(24, 28)]
    _write_history(tmp_path, history)
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    assert alert["operator_label"] == tsd.LABEL_RESEARCH_NOW


def test_confirmation_only_is_the_conservative_catch_all(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    assert alert["operator_label"] == tsd.LABEL_CONFIRMATION_ONLY


# ── phrase history idempotency + honest cold start ───────────────────────────
def test_cold_start_history_maturity_is_insufficient_not_ok(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    assert alert["history_maturity"] == "INSUFFICIENT_HISTORY"


def test_history_append_is_idempotent_per_day(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    res1 = tsd.build(tmp_path, now=NOW)
    tsd.write_artifacts(res1, tmp_path)
    res2 = tsd.build(tmp_path, now=NOW)
    tsd.write_artifacts(res2, tmp_path)
    rows = ahr._load_jsonl(tmp_path / tsd.HISTORY_REL)
    todays = [r for r in rows if r["name"] == "widget surge" and r["asof_date"] == ASOF]
    assert len(todays) == 1


# ── reuse, not duplication, of Alpha Heat Radar's alpha-fit/noise logic ─────
def test_reuses_alpha_heat_radar_compute_alpha_fit(tmp_path, monkeypatch):
    calls = []
    original = ahr.compute_alpha_fit

    def _spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(ahr, "compute_alpha_fit", _spy)
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    tsd.build(tmp_path, now=NOW)
    assert calls, "topic_shock_detector must call alpha_heat_radar.compute_alpha_fit, not reimplement it"


def test_reuses_alpha_heat_radar_compute_noise_flags(tmp_path, monkeypatch):
    calls = []
    original = ahr.compute_noise_flags

    def _spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(ahr, "compute_noise_flags", _spy)
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    tsd.build(tmp_path, now=NOW)
    assert calls, "topic_shock_detector must call alpha_heat_radar.compute_noise_flags, not reimplement it"


# ── output shape / guardrails / rendering ────────────────────────────────────
def test_guardrails_and_top_level_flags(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    res = tsd.build(tmp_path, now=NOW)
    assert res["research_only"] is True
    assert res["promote_to_signal"] is False
    assert res["no_routing_change"] is True
    assert res["hot_does_not_mean_buy"] is True
    g = dict(res["guardrails"])
    g.pop("promote_to_signal")
    assert all(g.values())


def test_alert_contains_all_requested_fields(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    for field in ("topic", "thesis", "evidence_phrases", "source_domains", "source_diversity",
                 "novelty_score", "heat_score", "mapped_tickers", "alpha_fit_overlays",
                 "noise_risk_flags", "scanner_overlaps", "operator_label", "reason",
                 "research_only", "disclaimer"):
        assert field in alert, f"missing field {field}"


def test_price_confirmation_is_opt_in_and_never_in_default_output(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    res = tsd.build(tmp_path, now=NOW)
    alert = _find_alert(res)
    assert alert["price_confirmation"] is None


def test_render_txt_and_md_do_not_crash_on_fallback():
    res = {"version": tsd.VERSION, "generated_at": NOW.isoformat(),
           "fallback": "MISSING_ARTIFACT", "missing_artifacts": ["x"]}
    assert tsd.render_txt(res)
    assert tsd.render_md(res)


def test_render_txt_and_md_normal_path(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    res = tsd.build(tmp_path, now=NOW)
    assert any("widget surge" in line for line in tsd.render_txt(res))
    assert any("widget surge" in line for line in tsd.render_md(res))


def test_write_artifacts_creates_json_md_txt(tmp_path):
    _setup_min(tmp_path, arb_items=_emergent_arb_items())
    res = tsd.build(tmp_path, now=NOW)
    paths = tsd.write_artifacts(res, tmp_path)
    assert Path(paths["json"]).exists()
    assert Path(paths["markdown"]).exists()
    assert Path(paths["text"]).exists()
    written = json.loads(Path(paths["json"]).read_text())
    assert "_new_history_rows" not in written
