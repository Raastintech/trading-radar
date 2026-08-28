"""Tests for research/topic_shock_review.py — Topic Shock Review CLI.

Covers: missing-file handling, the verdict/suggested-action ladder on a
no-research-now day and a one-research-now day, the Watch-for-Reset
summary (including its >8 cap), the Suppressed Templates summary, the
generic/broad/mega-cap/basket-mismatch quality-warning heuristics,
--format compact vs --format full, and — the hard requirement for a
"read-only reporting" tool — that running it never writes any file
anywhere.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research import topic_shock_review as rev


# ── fixture builders ─────────────────────────────────────────────────────────
def _mapped(ticker, confidence=0.85, method="explicit_evidence", qualifies=True):
    return {"ticker": ticker, "confidence": confidence, "method": method, "qualifies": qualifies}


def _overlay(score=20.0, positive_reasons=None, is_extended_or_blocked=False,
            flags=None, exhaustion_risk=False, redflag=False, no_fundamental_support=False):
    return {
        "alpha_fit": {"score": score, "positive_reasons": positive_reasons or ["profitable_quality"],
                     "is_extended_or_blocked": is_extended_or_blocked, "same_session_clean": True,
                     "sector_etf": None, "sector_leading": False, "current_regime": None},
        "noise": {"flags": flags or [], "exhaustion_risk": exhaustion_risk, "redflag": redflag,
                 "no_fundamental_support": no_fundamental_support},
    }


def _alert(topic, label, *, known_topic=None, thesis="thesis text", evidence_phrases=None,
          source_diversity=2, novelty_score=50.0, heat_score=50.0, acceleration_ratio=None,
          is_recycled_topic=False, mapped_tickers=None, alpha_fit_overlays=None,
          noise_risk_flags=None, reason="reason text"):
    mapped_tickers = mapped_tickers if mapped_tickers is not None else [_mapped("AAA")]
    alpha_fit_overlays = (alpha_fit_overlays if alpha_fit_overlays is not None
                          else {"AAA": _overlay()})
    return {
        "topic": topic, "known_topic": known_topic, "thesis": thesis,
        "evidence_phrases": evidence_phrases if evidence_phrases is not None else [topic.lower()],
        "source_domains": ["example.com"], "source_diversity": source_diversity,
        "novelty_score": novelty_score, "heat_score": heat_score,
        "acceleration_ratio": acceleration_ratio, "history_maturity": "INSUFFICIENT_HISTORY",
        "is_recycled_topic": is_recycled_topic, "mapped_tickers": mapped_tickers,
        "alpha_fit_overlays": alpha_fit_overlays, "noise_risk_flags": noise_risk_flags or [],
        "scanner_overlaps": [], "price_confirmation": None, "operator_label": label,
        "reason": reason, "research_only": True, "disclaimer": "disclaimer text",
    }


def _suppressed(pattern, *, phrase="example phrase", docs=5, tickers=None):
    return {"matched_pattern": pattern, "matched_phrase": phrase,
            "example_matched_phrases": [phrase], "source_domains": ["example.com"],
            "tickers": tickers or ["XYZ"], "document_count": docs}


def _report(alerts, suppressed=None, corpus=None):
    counts = {"alerts": len(alerts)}
    for a in alerts:
        counts[a["operator_label"]] = counts.get(a["operator_label"], 0) + 1
    return {
        "kind": "topic_shock_detector", "version": "TOPIC_SHOCK_DETECTOR_V0",
        "generated_at": "2026-08-28T12:00:00+00:00", "asof_date": "2026-08-28",
        "research_only": True, "promote_to_signal": False, "no_routing_change": True,
        "hot_does_not_mean_buy": True, "disclaimer": "disclaimer text", "guardrails": {},
        "source_dependencies": {},
        "corpus": corpus or {"raw_snippets": 20, "surviving_snippets": 15,
                             "suppressed_snippets": 5, "clusters_formed": len(alerts)},
        "suppressed_as_template": suppressed or [],
        "alerts": alerts, "counts": counts,
    }


def _write_report(tmp_path: Path, data) -> Path:
    path = tmp_path / "topic_shock_detector_latest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run(tmp_path: Path, data, argv=None, capsys=None):
    path = _write_report(tmp_path, data)
    code = rev.main([*(argv or []), "--path", str(path)])
    out = capsys.readouterr().out if capsys else None
    return code, out


# ── missing / invalid file ───────────────────────────────────────────────────
def test_missing_json_file_returns_friendly_message_and_exit_1(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    code = rev.main(["--path", str(missing)])
    out = capsys.readouterr().out
    assert code == 1
    assert "No Topic Shock Detector artifact found" in out
    assert "python -m research.topic_shock_detector" in out


def test_invalid_json_file_does_not_crash(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    code = rev.main(["--path", str(path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "Could not parse" in out


def test_detector_fallback_is_handled_gracefully(tmp_path, capsys):
    data = {"fallback": "MISSING_ARTIFACT", "missing_artifacts": ["x.json"]}
    code, out = _run(tmp_path, data, capsys=capsys)
    assert code == 0
    assert "Nothing to review" in out


# ── no-research-now day / one-research-now day (verdict + section) ──────────
def test_no_research_now_day_verdict_and_section(tmp_path, capsys):
    data = _report([_alert("earnings call", rev.tsd.LABEL_WATCH_FOR_RESET)])
    code, out = _run(tmp_path, data, capsys=capsys)
    assert code == 0
    assert "no topic cleared the bar for manual research today" in out
    assert "VERDICT: " in out
    assert "requires manual research now" not in out


def test_one_research_now_alert_verdict_and_fields(tmp_path, capsys):
    alert = _alert(
        "widget shortage", rev.tsd.LABEL_RESEARCH_NOW, thesis="widget makers co-mentioned",
        heat_score=88.0, novelty_score=90.0, source_diversity=4,
        mapped_tickers=[_mapped("WWW")],
        alpha_fit_overlays={"WWW": _overlay(score=45.0, positive_reasons=["profitable_quality", "reset_or_watch_for_entry"])},
    )
    data = _report([alert])
    code, out = _run(tmp_path, data, capsys=capsys)
    assert code == 0
    assert "VERDICT: 1 topic requires manual research now." in out
    assert "widget shortage" in out
    assert "WWW" in out
    assert "heat=88" in out
    assert "novelty=90" in out
    assert "source_diversity=4" in out
    assert "widget makers co-mentioned" in out
    assert "profitable_quality, reset_or_watch_for_entry" in out
    assert "review manually" in out.lower()


def test_multiple_research_now_alerts_use_plural_verdict(tmp_path, capsys):
    alerts = [
        _alert("topic a", rev.tsd.LABEL_RESEARCH_NOW, mapped_tickers=[_mapped("AAA")],
              alpha_fit_overlays={"AAA": _overlay()}),
        _alert("topic b", rev.tsd.LABEL_RESEARCH_NOW, mapped_tickers=[_mapped("BBB")],
              alpha_fit_overlays={"BBB": _overlay()}),
    ]
    data = _report(alerts)
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "VERDICT: 2 topics require manual research now." in out


# ── Watch for Reset summary ──────────────────────────────────────────────────
def test_watch_for_reset_summary_shows_reason_and_hint(tmp_path, capsys):
    alert = _alert("sector rotation", rev.tsd.LABEL_WATCH_FOR_RESET,
                   reason="AAA has a positive research profile but is extended today.")
    data = _report([alert])
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "WATCH FOR RESET" in out
    assert "sector rotation" in out
    assert "AAA has a positive research profile but is extended today." in out
    assert "becomes researchable if" in out


def test_watch_for_reset_caps_at_eight_in_plain_format(tmp_path, capsys):
    alerts = [_alert(f"topic {i}", rev.tsd.LABEL_WATCH_FOR_RESET,
                     mapped_tickers=[_mapped(f"T{i}")], alpha_fit_overlays={f"T{i}": _overlay()})
             for i in range(12)]
    data = _report(alerts)
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "... and 4 more (use --format full to see all)" in out


def test_watch_for_reset_full_format_shows_all(tmp_path, capsys):
    alerts = [_alert(f"topic {i}", rev.tsd.LABEL_WATCH_FOR_RESET,
                     mapped_tickers=[_mapped(f"T{i}")], alpha_fit_overlays={f"T{i}": _overlay()})
             for i in range(12)]
    data = _report(alerts)
    code, out = _run(tmp_path, data, ["--format", "full"], capsys=capsys)
    assert "... and" not in out
    for i in range(12):
        assert f"topic {i}" in out


def test_cited_ticker_always_appears_in_displayed_ticker_list(tmp_path, capsys):
    # Best alpha-fit ticker (ZZZ) is NOT the highest-confidence ticker (AAA) —
    # the display must still surface ZZZ since the reason text names it.
    alert = _alert(
        "mixed cluster", rev.tsd.LABEL_WATCH_FOR_RESET,
        mapped_tickers=[_mapped("AAA", confidence=0.95), _mapped("ZZZ", confidence=0.50)],
        alpha_fit_overlays={"AAA": _overlay(score=0.0), "ZZZ": _overlay(score=30.0, is_extended_or_blocked=True)},
        reason="ZZZ has a positive research profile but is extended today.",
    )
    data = _report([alert])
    code, out = _run(tmp_path, data, capsys=capsys)
    line = next(l for l in out.splitlines() if "mixed cluster" in l)
    assert "ZZZ" in line


# ── Confirmation Only ─────────────────────────────────────────────────────────
def test_confirmation_only_section(tmp_path, capsys):
    alert = _alert("price action", rev.tsd.LABEL_CONFIRMATION_ONLY,
                   reason="already known, nothing fresh today.")
    data = _report([alert])
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "CONFIRMATION ONLY" in out
    assert "price action" in out
    assert "already known, nothing fresh today." in out


# ── Risk / Noise ──────────────────────────────────────────────────────────────
def test_risk_noise_section_empty(tmp_path, capsys):
    data = _report([_alert("x", rev.tsd.LABEL_WATCH_FOR_RESET)])
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "no red flags, exhaustion, recycled, or weak-mapping topics today" in out


def test_risk_noise_section_populated(tmp_path, capsys):
    alerts = [
        _alert("bad news co", rev.tsd.LABEL_REDFLAG_NOISE, reason="deterioration risk"),
        _alert("blown off top", rev.tsd.LABEL_EXHAUSTION_RISK, reason="already parabolic"),
        _alert("same old story", rev.tsd.LABEL_OLD_RECHURN, reason="recycled chatter"),
        _alert("mystery ticker", rev.tsd.LABEL_MAPPING_WEAK, reason="mapping too weak",
              mapped_tickers=[_mapped("QQ", confidence=0.2, qualifies=False)],
              alpha_fit_overlays={}),
    ]
    data = _report(alerts)
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "Redflag Noise" in out and "bad news co" in out and "deterioration risk" in out
    assert "Exhaustion Risk" in out and "blown off top" in out
    assert "Old Rechurn" in out and "same old story" in out
    assert "Mapping Weak" in out and "mystery ticker" in out


# ── Suppressed Templates ──────────────────────────────────────────────────────
def test_suppressed_template_summary(tmp_path, capsys):
    suppressed = [_suppressed("LAW_FIRM_CLASS_ACTION", phrase="investor deadline",
                              docs=10, tickers=["CAPR", "PLAB"])]
    data = _report([], suppressed=suppressed)
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "SUPPRESSED TEMPLATES" in out
    assert "LAW_FIRM_CLASS_ACTION" in out
    assert "docs=10" in out
    assert "investor deadline" in out
    assert "CAPR" in out and "PLAB" in out
    assert "correctly suppressed" in out.lower()


def test_no_suppressed_templates_says_so(tmp_path, capsys):
    data = _report([_alert("x", rev.tsd.LABEL_WATCH_FOR_RESET)])
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "no syndicated/template boilerplate detected today" in out


# ── Quality Warnings (generic-topic detection) ───────────────────────────────
def test_generic_topic_warning_broad_known_category():
    alert = _alert("Crypto", rev.tsd.LABEL_WATCH_FOR_RESET, known_topic="Crypto",
                  evidence_phrases=["crypto rally"], mapped_tickers=[_mapped("COIN")],
                  alpha_fit_overlays={"COIN": _overlay()})
    flags = rev.quality_flags(alert)
    assert any("Broad known category" in f for f in flags)


def test_generic_topic_warning_generic_phrase():
    alert = _alert("wall street", rev.tsd.LABEL_WATCH_FOR_RESET,
                  evidence_phrases=["wall street"])
    flags = rev.quality_flags(alert)
    assert any("Generic cluster" in f for f in flags)


def test_generic_topic_warning_ticker_soup_phrase():
    alert = _alert("aapl msft", rev.tsd.LABEL_WATCH_FOR_RESET,
                  evidence_phrases=["aapl msft"],
                  mapped_tickers=[_mapped("AAPL"), _mapped("MSFT")],
                  alpha_fit_overlays={"AAPL": _overlay(), "MSFT": _overlay()})
    flags = rev.quality_flags(alert)
    assert any("mega-cap ticker names" in f for f in flags)
    assert any("Mega-cap chatter" in f for f in flags)


def test_generic_topic_warning_single_source():
    alert = _alert("obscure cluster", rev.tsd.LABEL_WATCH_FOR_RESET, source_diversity=1)
    flags = rev.quality_flags(alert)
    assert any("Single-source" in f for f in flags)


def test_generic_topic_warning_basket_mismatch():
    # "Crypto"-labeled cluster whose only qualifying evidence ticker is AAPL —
    # not a member of Crypto's own sympathy basket.
    alert = _alert("Crypto", rev.tsd.LABEL_WATCH_FOR_RESET, known_topic="Crypto",
                  evidence_phrases=["spy qqq"], mapped_tickers=[_mapped("AAPL")],
                  alpha_fit_overlays={"AAPL": _overlay()})
    flags = rev.quality_flags(alert)
    assert any("unrelated to" in f for f in flags)


def test_clean_specific_topic_has_no_quality_flags():
    alert = _alert("uranium supply shock", rev.tsd.LABEL_WATCH_FOR_RESET,
                  evidence_phrases=["uranium supply disruption"], source_diversity=4,
                  mapped_tickers=[_mapped("CCJ")], alpha_fit_overlays={"CCJ": _overlay()})
    assert rev.quality_flags(alert) == []


def test_quality_warnings_section_lists_flagged_topics_only(tmp_path, capsys):
    clean = _alert("uranium supply shock", rev.tsd.LABEL_WATCH_FOR_RESET,
                   evidence_phrases=["uranium supply disruption"], source_diversity=4,
                   mapped_tickers=[_mapped("CCJ")], alpha_fit_overlays={"CCJ": _overlay()})
    generic = _alert("wall street", rev.tsd.LABEL_WATCH_FOR_RESET, evidence_phrases=["wall street"])
    data = _report([clean, generic])
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "QUALITY WARNINGS" in out
    section = out.split("QUALITY WARNINGS")[1]
    assert "wall street" in section
    assert "uranium supply shock" not in section.split("OPERATOR SUMMARY")[0]


# ── --format compact / --format full ─────────────────────────────────────────
def test_compact_format_omits_quality_warnings_section(tmp_path, capsys):
    generic = _alert("wall street", rev.tsd.LABEL_WATCH_FOR_RESET, evidence_phrases=["wall street"])
    data = _report([generic])
    code, out = _run(tmp_path, data, ["--format", "compact"], capsys=capsys)
    assert "QUALITY WARNINGS" not in out


def test_full_format_shows_all_mapped_tickers_for_research_now(tmp_path, capsys):
    alert = _alert(
        "widget shortage", rev.tsd.LABEL_RESEARCH_NOW,
        mapped_tickers=[_mapped("WWW", confidence=0.9), _mapped("VVV", confidence=0.5, qualifies=False)],
        alpha_fit_overlays={"WWW": _overlay(score=40.0)},
    )
    data = _report([alert])
    code, out = _run(tmp_path, data, ["--format", "full"], capsys=capsys)
    assert "all mapped tickers:" in out
    assert "WWW(0.90)" in out and "VVV(0.50)" in out


def test_plain_format_does_not_show_all_mapped_tickers_line(tmp_path, capsys):
    alert = _alert("widget shortage", rev.tsd.LABEL_RESEARCH_NOW, mapped_tickers=[_mapped("WWW")],
                  alpha_fit_overlays={"WWW": _overlay(score=40.0)})
    data = _report([alert])
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "all mapped tickers:" not in out


# ── --only filter ─────────────────────────────────────────────────────────────
def test_only_research_now_omits_other_sections(tmp_path, capsys):
    data = _report([
        _alert("a", rev.tsd.LABEL_WATCH_FOR_RESET),
        _alert("b", rev.tsd.LABEL_CONFIRMATION_ONLY),
    ])
    code, out = _run(tmp_path, data, ["--only", "research-now"], capsys=capsys)
    assert "RESEARCH NOW" in out
    assert "WATCH FOR RESET" not in out
    assert "CONFIRMATION ONLY" not in out
    assert "OPERATOR SUMMARY" in out  # always present


def test_only_suppressed_shows_suppressed_section(tmp_path, capsys):
    data = _report([_alert("a", rev.tsd.LABEL_WATCH_FOR_RESET)],
                   suppressed=[_suppressed("LAW_FIRM_CLASS_ACTION")])
    code, out = _run(tmp_path, data, ["--only", "suppressed"], capsys=capsys)
    assert "SUPPRESSED TEMPLATES" in out
    assert "WATCH FOR RESET" not in out


# ── markdown format ───────────────────────────────────────────────────────────
def test_markdown_format_uses_headers_and_bullets(tmp_path, capsys):
    data = _report([_alert("a", rev.tsd.LABEL_WATCH_FOR_RESET)])
    code, out = _run(tmp_path, data, ["--format", "markdown"], capsys=capsys)
    assert "## TOPIC SHOCK REVIEW" in out
    assert "## WATCH FOR RESET" in out
    assert "----------------------------------------" not in out


# ── suggested_action / operator summary ──────────────────────────────────────
def test_operator_summary_suggests_review_now():
    data = _report([_alert("a", rev.tsd.LABEL_RESEARCH_NOW, mapped_tickers=[_mapped("AAA")],
                          alpha_fit_overlays={"AAA": _overlay()})])
    assert rev.suggested_action(data) == "Review now"


def test_operator_summary_suggests_wait_for_reset():
    data = _report([_alert("a", rev.tsd.LABEL_WATCH_FOR_RESET)])
    assert rev.suggested_action(data) == "Wait for reset"


def test_clean_watch_reset_day_still_recommends_wait_for_reset():
    # Specific, non-generic, non-mega-cap, multi-source cluster — a real
    # topic shock that just happens to be extended/gate-blocked today.
    alerts = [
        _alert("uranium supply shock", rev.tsd.LABEL_WATCH_FOR_RESET,
              evidence_phrases=["uranium supply disruption"], source_diversity=4,
              mapped_tickers=[_mapped("CCJ")], alpha_fit_overlays={"CCJ": _overlay()}),
        _alert("cybersecurity breach wave", rev.tsd.LABEL_WATCH_FOR_RESET,
              evidence_phrases=["ransomware breach wave"], source_diversity=3,
              mapped_tickers=[_mapped("CRWD")], alpha_fit_overlays={"CRWD": _overlay()}),
    ]
    data = _report(alerts)
    assert rev.suggested_action(data) == "Wait for reset"


def test_generic_heavy_watch_reset_day_recommends_no_true_shock_action():
    # Majority of today's Watch-for-Reset clusters are quality-flagged
    # (broad known category / generic phrase / mega-cap co-mention) — the
    # kind of day that produced the original wording complaint.
    alerts = [
        _alert("Crypto", rev.tsd.LABEL_WATCH_FOR_RESET, known_topic="Crypto",
              evidence_phrases=["spy qqq"], mapped_tickers=[_mapped("AAPL")],
              alpha_fit_overlays={"AAPL": _overlay()}),
        _alert("wall street", rev.tsd.LABEL_WATCH_FOR_RESET, evidence_phrases=["wall street"]),
        _alert("aapl msft", rev.tsd.LABEL_WATCH_FOR_RESET, evidence_phrases=["aapl msft"],
              mapped_tickers=[_mapped("AAPL"), _mapped("MSFT")],
              alpha_fit_overlays={"AAPL": _overlay(), "MSFT": _overlay()}),
        _alert("uranium supply shock", rev.tsd.LABEL_WATCH_FOR_RESET,
              evidence_phrases=["uranium supply disruption"], source_diversity=4,
              mapped_tickers=[_mapped("CCJ")], alpha_fit_overlays={"CCJ": _overlay()}),
    ]
    data = _report(alerts)
    assert rev.suggested_action(data) == rev.NO_TRUE_SHOCK_ACTION


def test_generic_heavy_day_shows_no_true_shock_action_in_full_report(tmp_path, capsys):
    alerts = [
        _alert("Crypto", rev.tsd.LABEL_WATCH_FOR_RESET, known_topic="Crypto",
              evidence_phrases=["spy qqq"], mapped_tickers=[_mapped("AAPL")],
              alpha_fit_overlays={"AAPL": _overlay()}),
        _alert("wall street", rev.tsd.LABEL_WATCH_FOR_RESET, evidence_phrases=["wall street"]),
    ]
    data = _report(alerts)
    code, out = _run(tmp_path, data, capsys=capsys)
    assert "No true topic-shock action today" in out
    assert "use normal Alpha Focus / High-Conviction workflow" in out


def test_operator_summary_suggests_ignore_as_noise():
    data = _report([
        _alert("a", rev.tsd.LABEL_REDFLAG_NOISE),
        _alert("b", rev.tsd.LABEL_EXHAUSTION_RISK),
    ])
    assert rev.suggested_action(data) == "Ignore as noise"


def test_operator_summary_no_action_today_when_empty():
    data = _report([])
    assert rev.suggested_action(data) == "No action today"


# ── never writes anything, anywhere ──────────────────────────────────────────
def test_no_writes_to_cache_data_or_logs(tmp_path, capsys):
    data = _report([
        _alert("a", rev.tsd.LABEL_RESEARCH_NOW, mapped_tickers=[_mapped("AAA")],
              alpha_fit_overlays={"AAA": _overlay()}),
        _alert("b", rev.tsd.LABEL_WATCH_FOR_RESET),
    ], suppressed=[_suppressed("LAW_FIRM_CLASS_ACTION")])
    path = _write_report(tmp_path, data)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    for fmt in ("plain", "compact", "full", "markdown"):
        rev.main(["--path", str(path), "--format", fmt])
    for only in ("all", "research-now", "watch-reset", "confirmation-only", "risk", "suppressed"):
        rev.main(["--path", str(path), "--only", only])
    capsys.readouterr()
    after = sorted(str(p) for p in tmp_path.rglob("*"))
    assert before == after


def test_real_repo_cache_and_logs_untouched_by_import_and_run(tmp_path, capsys, monkeypatch):
    # Defensive check against accidental use of the real DEFAULT_PATH / cwd
    # side effects: run against a tmp fixture and confirm the real repo's
    # cache/logs/data trees are unaffected (mtimes unchanged for anything
    # this module could plausibly touch).
    data = _report([_alert("a", rev.tsd.LABEL_WATCH_FOR_RESET)])
    path = _write_report(tmp_path, data)
    real_cache = rev.REPO_ROOT / "cache" / "research" / "topic_shock_detector_latest.json"
    before_mtime = real_cache.stat().st_mtime if real_cache.exists() else None
    rev.main(["--path", str(path)])
    capsys.readouterr()
    after_mtime = real_cache.stat().st_mtime if real_cache.exists() else None
    assert before_mtime == after_mtime
