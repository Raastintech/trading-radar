"""Tests for Alpha Focus (research/alpha_focus.py).

Alpha Focus is a small, same-day manual-research-prioritization filter over
already-scanned/scored candidates — NOT a new engine. A ticker only reaches
review_now/higher_risk_eo_review if it has at least one positive research
reason; extended-but-strong profiles go to wait_for_reset; everything else
is deprioritized with a specific, non-permanent reason. These tests cover:
bucket-assignment correctness, positive-reason gating, HC/EO overlap
visibility (including the repeat-caution vs. broad-repeat-deprioritization
split), evidence-citation passthrough, dashboard compactness, the journal
section, and the research-only / promote_to_signal / no-scanner-logic-change
invariants.
"""
from __future__ import annotations

import json
from pathlib import Path

from dashboards.research_command_center import journal_digest as jd
from dashboards.research_command_center.data_adapter import (
    ArtifactStore,
    build_alpha_focus,
)
from research import alpha_focus as af
from research import cohort_attribution as ca

ROOT = Path(__file__).resolve().parents[2]
TODAY = "2026-07-25"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _entry(ticker: str, *, program: str, category: str, quality: str,
           extension: str, cap: int, sector: str, date: str = TODAY,
           score: float = 70.0) -> dict:
    return {
        "ticker": ticker,
        "appearance_date": date,
        "research_program": program,
        "category": category,
        "watchlist_label": category.upper(),
        "fundamental_quality": quality,
        "priority_label": extension,
        "earliness_label": extension,
        "market_cap": cap,
        "sector": sector,
        "avg_dollar_volume": 2_000_000 if cap < 300_000_000 else 25_000_000,
        "research_score": score,
    }


def _fixture(root: Path) -> None:
    rows = [
        # WIN: profitable, mid-cap, catalyst -> review_now. Also an EO watch name
        # (LOW risk) so it demonstrates HC/EO overlap surfacing on a review_now row.
        _entry("WIN", program="TACTICAL", category="catalyst_watch",
               quality="PROFITABLE_CASHGEN", extension="RESET_WATCH",
               cap=5_000_000_000, sector="Industrials", score=90.0),
        # HCREPEAT: today's HC shortlist member, extension NORMAL, appears as a
        # REPEAT today -> must stay visible (review_now) with a caution flag,
        # NOT deprioritized, per the HC-repeat rule.
        _entry("HCREPEAT", program="SWING", category="early_accumulation",
               quality="UNKNOWN", extension="NORMAL", cap=2_000_000_000,
               sector="Healthcare", date="2026-07-23", score=84.0),
        _entry("HCREPEAT", program="SWING", category="early_accumulation",
               quality="UNKNOWN", extension="NORMAL", cap=2_000_000_000,
               sector="Healthcare", date=TODAY, score=84.0),
        # BROADREPEAT: a plain broad-scanner repeat, not HC/EO -> deprioritized.
        _entry("BROADREPEAT", program="SWING", category="early_accumulation",
               quality="UNKNOWN", extension="NORMAL", cap=2_500_000_000,
               sector="Healthcare", date="2026-07-23", score=60.0),
        _entry("BROADREPEAT", program="SWING", category="early_accumulation",
               quality="UNKNOWN", extension="NORMAL", cap=2_500_000_000,
               sector="Healthcare", date=TODAY, score=60.0),
        # HCEXTENDED: today's HC shortlist member but EXTENDED -> wait_for_reset,
        # not deprioritized (strong profile, not dropped).
        _entry("HCEXTENDED", program="SWING", category="beaten_down_recovery",
               quality="PROFITABLE_CASHGEN", extension="EXTENDED_CROWDED",
               cap=8_000_000_000, sector="Technology", score=88.0),
        # NOISYEXTENDED: extended, not HC/EO, not profitable -> deprioritized (extended).
        _entry("NOISYEXTENDED", program="SWING", category="rs_momentum_leader",
               quality="UNPROFITABLE_FUNDED", extension="EXTENDED_CROWDED",
               cap=150_000_000, sector="Technology", score=80.0),
        # RSMOM: rs_momentum_leader, not extended, no other positive reason -> deprioritized.
        _entry("RSMOM", program="SWING", category="rs_momentum_leader",
               quality="UNKNOWN", extension="NORMAL", cap=1_000_000_000,
               sector="Technology", score=95.0),
        # PLAIN: not extended, not repeat, not profitable, not HC/EO, not catalyst,
        # not mid/known-cap-qualifying -> no positive reason -> deprioritized.
        _entry("PLAIN", program="TACTICAL", category="social_arb_attention",
               quality="UNKNOWN", extension="NORMAL", cap=100_000_000,
               sector="Consumer Cyclical", score=62.0),
        # EORISKY: EO watch member with HIGH deterioration risk, no other positive
        # reason -> should NOT qualify via the EO reason -> deprioritized.
        _entry("EORISKY", program="SWING", category="early_accumulation",
               quality="UNKNOWN", extension="NORMAL", cap=800_000_000,
               sector="Healthcare", score=55.0),
    ]
    _write_jsonl(root / af.HISTORY_REL, rows)
    _write_json(root / af.HIGH_CONVICTION_REL, {
        "shortlist": [
            {"ticker": "HCREPEAT", "why_selected": ["strong margins"]},
            {"ticker": "HCEXTENDED", "why_selected": ["profitable", "growth"]},
        ],
    })
    _write_json(root / af.EMERGING_OUTLIER_REL, {
        "watch": [
            {"ticker": "WIN", "why_watched": ["accelerating growth"], "business_deterioration_risk": "LOW"},
            {"ticker": "EORISKY", "why_watched": ["speculative"], "business_deterioration_risk": "HIGH"},
        ],
    })


def test_alpha_focus_generates(tmp_path):
    _fixture(tmp_path)

    report = af.build_report(tmp_path)

    assert report["kind"] == "alpha_focus"
    assert not report.get("fallback")
    assert report["market_as_of_date"] == TODAY


def test_review_now_requires_positive_reason(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    review_now_tickers = {r["ticker"] for r in report["review_now"]}
    assert "WIN" not in review_now_tickers  # WIN is EO -> its own bucket
    assert "HCREPEAT" in review_now_tickers
    for row in report["review_now"]:
        assert row["focus_reasons"], f"{row['ticker']} has no positive reason but is in review_now"


def test_plain_candidate_with_no_positive_reason_is_deprioritized(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    deprioritized = {r["ticker"]: r["deprioritized_by_focus_rule"] for r in report["deprioritized_by_focus_rule"]}
    assert deprioritized["PLAIN"] == af.REASON_NO_POSITIVE_REASON


def test_eo_high_risk_without_other_reason_is_deprioritized(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    deprioritized = {r["ticker"]: r["deprioritized_by_focus_rule"] for r in report["deprioritized_by_focus_rule"]}
    higher_risk_tickers = {r["ticker"] for r in report["higher_risk_eo_review"]}
    assert "EORISKY" in deprioritized
    assert "EORISKY" not in higher_risk_tickers


def test_eo_low_risk_lands_in_higher_risk_eo_review_bucket(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    higher_risk_by_ticker = {r["ticker"]: r for r in report["higher_risk_eo_review"]}
    assert "WIN" in higher_risk_by_ticker
    assert higher_risk_by_ticker["WIN"]["business_deterioration_risk"] == "LOW"
    assert af.REASON_EO_NOT_HIGH_RISK in higher_risk_by_ticker["WIN"]["focus_reasons"]


def test_hc_repeat_kept_visible_with_caution_not_deprioritized(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    review_now_by_ticker = {r["ticker"]: r for r in report["review_now"]}
    deprioritized_tickers = {r["ticker"] for r in report["deprioritized_by_focus_rule"]}
    assert "HCREPEAT" in review_now_by_ticker
    assert "HCREPEAT" not in deprioritized_tickers
    assert review_now_by_ticker["HCREPEAT"]["repeat_caution"] == af.REPEAT_CAUTION_TEXT


def test_broad_only_repeat_is_deprioritized(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    deprioritized = {r["ticker"]: r["deprioritized_by_focus_rule"] for r in report["deprioritized_by_focus_rule"]}
    assert deprioritized["BROADREPEAT"] == af.REASON_REPEAT_DECAY


def test_extended_hc_goes_to_wait_for_reset_not_deprioritized(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    wait_tickers = {r["ticker"] for r in report["wait_for_reset"]}
    deprioritized_tickers = {r["ticker"] for r in report["deprioritized_by_focus_rule"]}
    assert "HCEXTENDED" in wait_tickers
    assert "HCEXTENDED" not in deprioritized_tickers


def test_noisy_extended_without_strong_profile_is_deprioritized(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    deprioritized = {r["ticker"]: r["deprioritized_by_focus_rule"] for r in report["deprioritized_by_focus_rule"]}
    wait_tickers = {r["ticker"] for r in report["wait_for_reset"]}
    assert deprioritized["NOISYEXTENDED"] == af.REASON_EXTENDED
    assert "NOISYEXTENDED" not in wait_tickers


def test_rs_momentum_alone_is_deprioritized(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    deprioritized = {r["ticker"]: r["deprioritized_by_focus_rule"] for r in report["deprioritized_by_focus_rule"]}
    assert deprioritized["RSMOM"] == af.REASON_RS_MOMENTUM


def test_counts_reconcile(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    counts = report["counts"]
    # 8 distinct tickers today (each REPEAT ticker's day-1 appearance is not "today").
    assert counts["total_today"] == 8
    assert (counts["review_now"] + counts["higher_risk_eo_review"]
            + counts["wait_for_reset"] + counts["deprioritized"]) == counts["total_today"]


def test_repeat_decay_reason_description_does_not_imply_permanent_rejection(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    desc = af.REASON_DESCRIPTIONS[af.REASON_REPEAT_DECAY]
    assert "repeat-candidate decay was confirmed in the root-cause audit" in desc
    assert "does not mean every repeat candidate is permanently bad" in desc.lower()

    summary = report["deprioritized_by_reason_summary"][af.REASON_REPEAT_DECAY]
    assert summary["description"] == desc


def test_hc_eo_artifacts_are_read_only_not_mutated(tmp_path):
    _fixture(tmp_path)
    hc_before = (tmp_path / af.HIGH_CONVICTION_REL).read_text(encoding="utf-8")
    eo_before = (tmp_path / af.EMERGING_OUTLIER_REL).read_text(encoding="utf-8")

    af.build_report(tmp_path)

    assert (tmp_path / af.HIGH_CONVICTION_REL).read_text(encoding="utf-8") == hc_before
    assert (tmp_path / af.EMERGING_OUTLIER_REL).read_text(encoding="utf-8") == eo_before


def test_hc_shortlist_and_eo_watch_membership_unaffected(tmp_path):
    """The whole point: HC/EO's own selection is untouched no matter which
    Alpha Focus bucket a member lands in."""
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    hc_doc = json.loads((tmp_path / af.HIGH_CONVICTION_REL).read_text(encoding="utf-8"))
    eo_doc = json.loads((tmp_path / af.EMERGING_OUTLIER_REL).read_text(encoding="utf-8"))
    assert {t["ticker"] for t in hc_doc["shortlist"]} == {"HCREPEAT", "HCEXTENDED"}
    assert {t["ticker"] for t in eo_doc["watch"]} == {"WIN", "EORISKY"}
    # Both HC names appear somewhere in Alpha Focus's own bucketing (visible, not hidden).
    all_tickers = {
        r["ticker"] for r in (
            report["review_now"] + report["higher_risk_eo_review"]
            + report["wait_for_reset"] + report["deprioritized_by_focus_rule"]
        )
    }
    assert {"HCREPEAT", "HCEXTENDED", "WIN", "EORISKY"} <= all_tickers


def test_source_reasons_passthrough_from_hc_and_eo(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    review_now_by_ticker = {r["ticker"]: r for r in report["review_now"]}
    higher_risk_by_ticker = {r["ticker"]: r for r in report["higher_risk_eo_review"]}
    assert review_now_by_ticker["HCREPEAT"]["source_reasons"] == ["strong margins"]
    assert higher_risk_by_ticker["WIN"]["source_reasons"] == ["accelerating growth"]


def test_focus_ordered_by_existing_research_score_not_a_new_score(tmp_path):
    root = tmp_path
    rows = [
        _entry("A", program="TACTICAL", category="catalyst_watch",
               quality="PROFITABLE_CASHGEN", extension="NORMAL",
               cap=5_000_000_000, sector="Industrials", score=50.0),
        _entry("B", program="TACTICAL", category="catalyst_watch",
               quality="PROFITABLE_CASHGEN", extension="NORMAL",
               cap=5_000_000_000, sector="Industrials", score=95.0),
    ]
    _write_jsonl(root / af.HISTORY_REL, rows)

    report = af.build_report(root)

    tickers_in_order = [r["ticker"] for r in report["review_now"]]
    assert tickers_in_order == ["B", "A"]  # higher research_score first, unmodified


def test_evidence_citations_are_read_only_passthrough(tmp_path):
    _fixture(tmp_path)
    _write_json(tmp_path / af.COHORT_ATTRIBUTION_REL, {
        "cohorts": [{
            "id": "broad_scanner",
            "primary_result": "no edge",
            "horizons": {"10d": {"mean_return_pct": -4.52, "sample_status": "ROBUST"}},
        }],
    })
    _write_json(tmp_path / af.ROOT_CAUSE_REL, {
        "dashboard_summary": {"top_likely_failure_cause": "remove_extended (test)"},
        "baseline_comparison": {
            "random_same_universe_control": {"mean_return_pct": 1.5},
            "candidate_pool_vs_market_benchmarks": {"excess_vs_spy_pct": -4.68},
        },
    })

    report = af.build_report(tmp_path)
    ev = report["evidence_citations"]

    assert ev["broad_scanner_verdict"] == "no edge"
    assert ev["broad_scanner_mean_10d_pct"] == -4.52
    assert ev["top_ablation_cause"] == "remove_extended (test)"
    assert ev["random_control_beats_candidate_pool"] is True


def test_missing_history_returns_fallback(tmp_path):
    report = af.build_report(tmp_path)

    assert report["fallback"] == "MISSING_ARTIFACT"
    assert report["guardrails"]["research_only"] is True


def test_research_only_and_promote_to_signal_invariants(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    assert report["research_only"] is True
    assert report["promote_to_signal"] is False
    g = report["guardrails"]
    assert g["research_only"] is True
    assert g["promote_to_signal"] is False
    assert g["no_scanner_logic_change"] is True
    assert g["no_score_or_ranking_change"] is True
    assert g["no_routing_change"] is True
    assert g["no_gate_or_threshold_change"] is True
    assert g["no_factor_weight_change"] is True
    assert g["no_high_conviction_rule_change"] is True
    assert g["no_emerging_outlier_rule_change"] is True
    assert g["no_program_verdict_threshold_change"] is True
    assert g["no_candidate_selection_change"] is True
    assert g["no_trade_recommendations"] is True


def test_source_history_row_never_mutated(tmp_path):
    _fixture(tmp_path)
    before = (tmp_path / af.HISTORY_REL).read_text(encoding="utf-8")

    af.build_report(tmp_path)

    after = (tmp_path / af.HISTORY_REL).read_text(encoding="utf-8")
    assert before == after


def test_purpose_statement_present_and_exact(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    expected = (
        "Alpha Focus is a same-day manual research prioritization layer. It does "
        "not delete candidates, change scores, change rankings, change scanner "
        "logic, or create trade signals."
    )
    assert report["purpose_statement"] == expected
    assert expected in report["caveats"]


def test_write_artifacts_produces_json_and_text(tmp_path):
    _fixture(tmp_path)
    report = af.build_report(tmp_path)

    paths = af.write_artifacts(report, tmp_path)

    assert Path(paths["json"]).exists()
    assert Path(paths["text"]).exists()
    text = Path(paths["text"]).read_text(encoding="utf-8")
    assert "ALPHA FOCUS" in text
    assert "Wait for Reset" in text
    assert "Higher-Risk EO Review" in text


def test_dashboard_panel_stays_compact_and_collapsed():
    html = (ROOT / "dashboards/research_command_center/static/index.html").read_text(encoding="utf-8")
    panel = html.split("function alphaFocusPanel", 1)[1].split("function researchOperatingPolicyPanel", 1)[0]

    assert "<details" in panel
    assert "<summary>" in panel
    # Compact: no full bucket list tables rendered inline on the home page.
    assert "deprioritized_by_focus_rule" not in panel


def test_dashboard_reads_compact_alpha_focus_only(tmp_path):
    _write_json(tmp_path / af.OUT_JSON_REL, {
        "generated_at": "2026-07-25T00:00:00+00:00",
        "purpose_statement": "Alpha Focus is a same-day manual research prioritization layer.",
        "market_as_of_date": TODAY,
        "research_only": True,
        "promote_to_signal": False,
        "counts": {"review_now": 1, "higher_risk_eo_review": 1, "wait_for_reset": 1,
                   "deprioritized": 3, "total_today": 6,
                   "high_conviction_overlap": 1, "emerging_outlier_overlap": 1},
        "review_now": [{"ticker": "WIN"}],
        "higher_risk_eo_review": [{"ticker": "RISKY"}],
        "wait_for_reset": [{"ticker": "EXT"}],
        "deprioritized_by_focus_rule": [{"ticker": "LOSE", "deprioritized_by_focus_rule": "extended_top_ablation_drag"}],
        "deprioritized_by_reason_summary": {"extended_top_ablation_drag": {"count": 1, "tickers": ["LOSE"]}},
        "evidence_citations": {},
        "guardrails": {"research_only": True},
    })

    payload = build_alpha_focus(ArtifactStore(root=tmp_path), compact=True)

    assert payload["counts"]["review_now"] == 1
    assert "deprioritized_by_focus_rule" not in payload


def test_journal_digest_includes_alpha_focus_section():
    inputs = {
        "alpha_focus": {
            "purpose_statement": "Alpha Focus is a same-day manual research prioritization layer.",
            "market_as_of_date": TODAY,
            "counts": {"review_now": 1, "higher_risk_eo_review": 1, "wait_for_reset": 1,
                       "deprioritized": 3, "total_today": 6,
                       "high_conviction_overlap": 1, "emerging_outlier_overlap": 1},
            "review_now": [{"ticker": "WIN"}],
            "higher_risk_eo_review": [{"ticker": "RISKY"}],
            "deprioritized_by_reason_summary": {
                "extended_top_ablation_drag": {"count": 1, "tickers": ["LOSE"], "description": "Deprioritized because extended."},
            },
        }
    }

    text = "\n".join(jd._section_alpha_focus(inputs))

    assert "## Alpha Focus" in text
    assert "1 review now" in text
    assert "WIN" in text
    assert "RISKY" in text
    assert "extended_top_ablation_drag" in text
    assert "still appear" in text.lower()


def test_journal_digest_handles_missing_alpha_focus():
    text = "\n".join(jd._section_alpha_focus({}))
    assert "## Alpha Focus" in text
    assert "missing" in text.lower()


def test_no_scanner_scoring_routing_filter_logic_changed_by_alpha_focus_module():
    for rel in (
        "research/research_scanner.py",
        "research/research_scoring.py",
        "research/research_programs.py",
        "research/latest_scan_programs.py",
        "research/high_conviction_alpha.py",
        "research/emerging_outlier_watch.py",
        "research/cohort_attribution.py",
        "research/research_operating_policy.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "alpha_focus" not in text
