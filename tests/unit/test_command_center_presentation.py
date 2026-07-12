"""Tests for the Command Center cosmetic simplification layer.

Covers the twelve required behaviors of the presentation layer:
top-five default, existing-score ordering primacy, full set still
reachable, data-quarantine separation, canonical verdict preservation,
cleaner verdict labels, compact evidence label, cross-horizon badges,
presentation filters not touching artifacts, concise terminal default
with verbose preserved, journal top-candidates + full detail, and no
scanner/score/routing/validation logic change.

All of it is display-only: the module under test is a set of pure
functions over dicts (no provider, DB, scanner, or scoring imports).
"""
from __future__ import annotations

import ast
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dashboards.research_command_center import presentation as pres
from research import latest_scan_programs as lsp


NOW = datetime(2026, 7, 10, 6, 0, tzinfo=timezone.utc)


def _cand(ticker, score, status="REPEAT", priority="WATCHLIST_RESEARCH",
          label="RS_MOMENTUM_LEADER", program="SWING",
          secondary=None, lifecycle="UNDER_OBSERVATION",
          verdict="INSUFFICIENT_MATURE_EVIDENCE", evidence=None,
          same_session=True, data_confidence="HIGH",
          quality="PROFITABLE_CASHGEN", reason=None):
    return {
        "ticker": ticker, "research_score": score, "scan_status": status,
        "priority": priority, "label": label, "program": program,
        "secondary_programs": secondary or [],
        "lifecycle_stage": lifecycle, "program_verdict": verdict,
        "evidence_maturity": evidence
        or {"5d": "PENDING", "10d": "PENDING", "60d": "PENDING"},
        "same_session": same_session, "data_confidence": data_confidence,
        "fundamental_quality": quality,
        "detection_reason": reason or f"reason for {ticker}",
        "research_hypothesis": "hypothesis", "holding_period": "45-60 td",
        "bar_as_of_date": "2026-07-10", "benchmark_as_of_date": "2026-07-10",
        "routing_reason": "primary label wins", "confidence": "high",
        "current_benchmark": "SPY/QQQ", "research_only": True,
    }


def _block(cands, **counts):
    return {"candidate_count": len(cands), "candidates": list(cands),
            "exited": [], "new_count": 0, "repeat_count": 0,
            "returning_count": 0, "exited_count": 0, **counts}


def _payload(block):
    return {"kind": "latest_scan_programs", "present": True,
            "research_only": True,
            "programs": {"TACTICAL": _block([]), "SWING": block,
                         "LONG_TERM": _block([])}}


# ── 1. only top five display by default ──────────────────────────────────────


def test_only_top_five_by_default():
    cands = [_cand(f"T{i}", 90 - i) for i in range(9)]
    block = _block(cands)
    top, rest, issues = pres.split_candidates(block)
    assert len(top) == 5
    assert len(rest) == 4
    assert not issues
    assert [c["ticker"] for c in top] == ["T0", "T1", "T2", "T3", "T4"]


# ── 2. existing score is the ordering source ─────────────────────────────────


def test_existing_score_drives_order_over_status():
    # a NEW low-score candidate must NOT jump a stronger REPEAT
    strong = _cand("STRONG", 99, status="REPEAT")
    weak_new = _cand("WEAKNEW", 40, status="NEW")
    block = _block([weak_new, strong])
    top, _, _ = pres.split_candidates(block)
    assert [c["ticker"] for c in top] == ["STRONG", "WEAKNEW"]


def test_status_only_breaks_score_ties():
    a = _cand("AAA", 80, status="REPEAT")
    b = _cand("NEWB", 80, status="NEW")
    top, _, _ = pres.split_candidates(_block([a, b]))
    # equal score -> NEW ranked ahead of REPEAT by the tie-break ladder
    assert [c["ticker"] for c in top] == ["NEWB", "AAA"]


# ── 3. full candidate set stays reachable ────────────────────────────────────


def test_full_set_recovered_by_split():
    cands = ([_cand(f"C{i}", 90 - i) for i in range(7)]
             + [_cand("QUAR", 100, priority="DATA_QUARANTINE")])
    block = _block(cands)
    top, rest, issues = pres.split_candidates(block)
    recovered = {c["ticker"] for c in top + rest + issues}
    assert recovered == {c["ticker"] for c in cands}
    assert len(top) + len(rest) + len(issues) == len(cands)


# ── 4. data-quarantine separated from normal top candidates ──────────────────


def test_data_quarantine_separated():
    clean = _cand("CLEAN", 70)
    quar = _cand("HCC", 95, priority="DATA_QUARANTINE")
    block = _block([clean, quar])
    top, rest, issues = pres.split_candidates(block)
    assert [c["ticker"] for c in top] == ["CLEAN"]
    assert [c["ticker"] for c in issues] == ["HCC"]
    # even though HCC has the higher score it never enters the top list
    assert all(c["ticker"] != "HCC" for c in top + rest)


def test_session_mismatch_and_missing_bar_are_data_issues():
    mismatch = _cand("MM", 80, same_session=False)
    nobar = _cand("NB", 80)
    nobar["bar_as_of_date"] = None
    assert pres.is_data_issue(mismatch)
    assert "SESSION_MISMATCH" in pres.data_issue_reasons(mismatch)
    assert pres.is_data_issue(nobar)
    assert "MISSING_BAR" in pres.data_issue_reasons(nobar)


# ── 5. canonical verdict values unchanged ────────────────────────────────────


def test_canonical_verdicts_preserved_after_decoration():
    block = _block([_cand("X", 80, verdict="INSUFFICIENT_MATURE_EVIDENCE")])
    payload = _payload(block)
    pres.decorate_scan_payload(payload)
    c = payload["programs"]["SWING"]["candidates"][0]
    assert c["program_verdict"] == "INSUFFICIENT_MATURE_EVIDENCE"
    assert c["display"]["verdict_label"] == "Insufficient evidence"


# ── 6. cleaner verdict display labels ────────────────────────────────────────


def test_verdict_display_labels():
    assert pres.verdict_display("INSUFFICIENT_MATURE_EVIDENCE") \
        == "Insufficient evidence"
    assert pres.verdict_display("PROMISING_BUT_UNPROVEN") \
        == "Promising, unproven"
    assert pres.verdict_display("VALIDATED_EDGE") == "Validated edge"
    assert pres.verdict_display("NO_EVIDENCE_OF_EDGE") == "No evidence of edge"
    # unknown values pass through unchanged (never silently relabelled)
    assert pres.verdict_display("SOMETHING_NEW") == "SOMETHING_NEW"


# ── 7. compact evidence label replaces per-horizon string ────────────────────


def test_evidence_summary_compact():
    assert pres.evidence_summary(
        {"5d": "PENDING", "10d": "PENDING"}) == "Evidence: pending"
    assert pres.evidence_summary(
        {"5d": "MATURED", "10d": "PENDING"}) == "Evidence: 5d matured"
    assert pres.evidence_summary(
        {"5d": "MATURED", "10d": "MATURED"}) \
        == "Evidence: all horizons matured (5d/10d)"
    assert pres.evidence_summary(
        {"126d": "NOT_COLLECTED"}) == "Evidence: none tracked"


def test_evidence_never_asserts_a_conclusion():
    # missing evidence must not become a positive or negative verdict
    s = pres.evidence_summary({"5d": "PENDING"})
    assert "positive" not in s.lower() and "negative" not in s.lower()


# ── 8. cross-horizon badges ──────────────────────────────────────────────────


def test_cross_horizon_badges():
    c = _cand("RXT", 100, secondary=["TACTICAL"])
    badges = pres.candidate_badges(c)
    texts = [b["text"] for b in badges]
    assert "+TACTICAL" in texts
    assert any(b["tone"] == "cross" for b in badges)


def test_positive_and_caution_badges_from_existing_fields():
    reset = _cand("A", 80, priority="RESET_WATCH", label="SOCIAL_ARB")
    texts = [b["text"] for b in pres.candidate_badges(reset)]
    assert "RESET WATCH" in texts and "SOCIAL ATTENTION" in texts
    ext = _cand("B", 80, priority="EXTENDED_CROWDED",
                quality="UNPROFITABLE_FUNDED")
    etexts = {b["text"]: b["tone"] for b in pres.candidate_badges(ext)}
    assert etexts.get("EXTENDED") == "caution"
    assert etexts.get("UNPROFITABLE") == "caution"


# ── 9. presentation does not mutate artifacts or ranking ─────────────────────


def test_decoration_is_additive_only():
    block = _block([_cand("A", 90), _cand("B", 80), _cand("C", 70)])
    payload = _payload(block)
    before_order = [c["ticker"] for c in payload["programs"]["SWING"]["candidates"]]
    before_scores = [c["research_score"]
                     for c in payload["programs"]["SWING"]["candidates"]]
    snapshot = copy.deepcopy({
        pid: [{k: v for k, v in c.items()} for c in b["candidates"]]
        for pid, b in payload["programs"].items()})
    pres.decorate_scan_payload(payload)
    after = payload["programs"]["SWING"]["candidates"]
    assert [c["ticker"] for c in after] == before_order  # order untouched
    assert [c["research_score"] for c in after] == before_scores
    # every original key/value survives unchanged; only `display` is added
    for pid, b in payload["programs"].items():
        for orig, now in zip(snapshot[pid], b["candidates"]):
            for k, v in orig.items():
                assert now[k] == v
            assert set(now) - set(orig) == {"display"}


def test_filters_do_not_change_source_file(tmp_path):
    # the sidecar on disk is never rewritten by any display path
    root = _scan_root(tmp_path)
    before = (root / lsp.SIDECAR_REL).read_bytes()
    payload = lsp.build_latest_scan(root, now=NOW)
    pres.decorate_scan_payload(payload)
    for f in ("all", "new", "high"):
        pass  # filtering happens client-side; server file must be intact
    assert (root / lsp.SIDECAR_REL).read_bytes() == before


# ── 10. terminal default concise; verbose still available ────────────────────


def _scan_root(tmp_path):
    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True)
    scanner = {
        "generated_at": "2026-07-10T00:36:00+00:00",
        "scan_universe_manifest_hash": "hash0", "market_as_of_date":
        "2026-07-09", "scan_integrity": {"readiness": "READY"},
        "watchlist": [
            {"ticker": "MEI", "watchlist_label": "RS_MOMENTUM_LEADER",
             "category": "rs_momentum_leader",
             "all_categories": ["rs_momentum_leader"],
             "why_appeared": "Strong RS vs SPY", "research_score": 94.0,
             "data_confidence": "HIGH", "bar_as_of_date": "2026-07-09",
             "benchmark_as_of_date": "2026-07-09", "same_session": True},
            {"ticker": "HCC", "watchlist_label": "RS_MOMENTUM_LEADER",
             "category": "rs_momentum_leader",
             "all_categories": ["rs_momentum_leader"],
             "why_appeared": "quarantined", "research_score": 99.0,
             "data_confidence": "MEDIUM", "bar_as_of_date": "2026-07-09",
             "benchmark_as_of_date": "2026-07-09", "same_session": True},
        ],
    }
    (research / "research_scanner_latest.json").write_text(
        json.dumps(scanner), encoding="utf-8")
    (research / "scan_universe_manifest_latest.json").write_text(
        json.dumps({"universe_hash": "hash0",
                    "required_market_session": "2026-07-09"}),
        encoding="utf-8")
    ledger = tmp_path / lsp.LEDGER_REL
    ledger.parent.mkdir(parents=True)
    ledger.write_text("", encoding="utf-8")
    payload = lsp.build_latest_scan(tmp_path, now=NOW)
    lsp.write_outputs(payload, tmp_path)
    return tmp_path


def test_terminal_default_is_concise():
    block = _block([_cand(f"T{i}", 90 - i) for i in range(8)])
    txt = lsp.render_terminal(_payload(block))
    assert txt.startswith("=== TOP RESEARCH CANDIDATES ===")
    assert "score=" in txt
    assert "Evidence:" in txt
    assert "Use --all or --program-details" in txt
    # verbose-only per-candidate fields absent from the concise view
    assert "lifecycle=" not in txt
    assert "... and 3 more" in txt  # 8 candidates, top 5 shown


def test_terminal_verbose_still_available():
    block = _block([_cand("T0", 90)])
    txt = lsp.render_terminal(_payload(block), verbose=True)
    assert "LATEST RESEARCH CANDIDATES BY PROGRAM" in txt
    assert "lifecycle=" in txt
    assert "verdict=INSUFFICIENT_MATURE_EVIDENCE" in txt


def test_log_twin_is_verbose(tmp_path):
    root = _scan_root(tmp_path)
    log = (root / lsp.LOG_REL).read_text(encoding="utf-8")
    assert "LATEST RESEARCH CANDIDATES BY PROGRAM" in log
    assert "lifecycle=" in log


def test_terminal_separates_data_issues():
    block = _block([_cand("CLEAN", 70),
                    _cand("HCC", 99, priority="DATA_QUARANTINE")])
    txt = lsp.render_terminal(_payload(block))
    assert "data issues (1)" in txt and "HCC" in txt
    # the quarantined name is not shown as a ranked top candidate
    assert "1. HCC" not in txt


# ── 11. journal digest: concise top + full detail both present ───────────────


def test_journal_has_concise_top_and_full_detail():
    from dashboards.research_command_center.journal_digest import (
        _section_top_candidates, _section_latest_scan_programs)
    block = _block([_cand("MEI", 94), _cand("UMC", 91),
                    _cand("HCC", 99, priority="DATA_QUARANTINE")],
                   candidate_count=3)
    payload = _payload(block)
    top = "\n".join(_section_top_candidates({"latest_scan": payload}))
    full = "\n".join(_section_latest_scan_programs({"latest_scan": payload}))
    assert top.startswith("## 4c. Top Candidates by Program")
    assert "MEI (score 94" in top
    assert "HCC" in top and "data issues" in top  # quarantine surfaced apart
    # the detailed section remains, after, with full records
    assert full.startswith("## 4d. Latest Scan by Research Program")
    assert "verdict INSUFFICIENT_MATURE_EVIDENCE" in full


# ── 12. no scanner/score/routing/validation logic imported here ──────────────


def test_presentation_imports_no_engine_logic():
    source = Path(pres.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("research_scanner", "research_scoring", "research_programs",
                 "alpaca", "tradier", "execution", "broker", "core.config",
                 "order", "strategies", "council", "requests", "anthropic")
    for mod in imported:
        assert not any(f in mod for f in forbidden), mod


def test_page_note_constant_present():
    payload = _payload(_block([_cand("A", 80)]))
    pres.decorate_scan_payload(payload)
    assert payload["display"]["page_note"] == pres.PAGE_RESEARCH_NOTE
    assert "research-only" in pres.PAGE_RESEARCH_NOTE
