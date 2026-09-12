"""Tests for the latest-scan-by-program visibility layer.

Covers the twelve required behaviors: program grouping, single primary
program, secondary preservation, latest-artifact-only sourcing,
NEW/REPEAT/RETURNING/EXITED classification, empty-program visibility,
terminal completeness, adapter payload, digest separation, no
selection-logic change, research_only invariant, and stale-scan warning.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research import latest_scan_programs as lsp

NOW = datetime(2026, 7, 10, 6, 0, tzinfo=timezone.utc)


def _scanner(tickers, generated="2026-07-10T00:36:00+00:00",
             manifest_hash="deadbeefcafe0000", market_as_of="2026-07-09",
             readiness="READY"):
    return {
        "generated_at": generated,
        "stale_price_skipped_count": 3,
        "data_suspect_skipped_count": 1,
        "scan_universe_manifest_hash": manifest_hash,
        "market_as_of_date": market_as_of,
        "scan_integrity": {"readiness": readiness,
                           "readiness_reasons": ["fixture"]},
        "watchlist": [
            {"ticker": t, "watchlist_label": lbl, "category": cat,
             "all_categories": cats, "why_appeared": f"reason for {t}",
             "research_score": score, "data_confidence": "HIGH",
             "bar_as_of_date": market_as_of,
             "benchmark_as_of_date": market_as_of,
             "same_session": True}
            for t, lbl, cat, cats, score in tickers
        ],
    }


def _root(tmp_path, scanner_doc, ledger_rows,
          manifest_hash="deadbeefcafe0000", required_session="2026-07-09"):
    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True)
    (research / "research_scanner_latest.json").write_text(
        json.dumps(scanner_doc), encoding="utf-8")
    (research / "scan_universe_manifest_latest.json").write_text(
        json.dumps({"universe_hash": manifest_hash,
                    "required_market_session": required_session}),
        encoding="utf-8")
    ledger = tmp_path / lsp.LEDGER_REL
    ledger.parent.mkdir(parents=True)
    ledger.write_text("\n".join(json.dumps(r) for r in ledger_rows) + "\n",
                      encoding="utf-8")
    return tmp_path


TICKERS = [
    ("FBIN", "EARLY_ACCUMULATION", "early_accumulation",
     ["early_accumulation", "catalyst_watch"], 90.0),   # SWING + sec TACTICAL
    ("RH", "SOCIAL_ARB", "social_arb_attention",
     ["social_arb_attention"], 70.0),                    # TACTICAL
    ("XYZ", "SPECULATIVE_10X", "long_term_asymmetric",
     ["long_term_asymmetric"], 60.0),                    # LONG_TERM
]

LEDGER = [
    # FBIN seen in the previous scan (REPEAT), with matured 5d/10d
    {"ticker": "FBIN", "watchlist_label": "EARLY_ACCUMULATION",
     "appearance_date": "2026-07-09", "ret_5d": 1.0, "ret_10d": 2.0},
    # XYZ seen long ago but not in the previous scan (RETURNING)
    {"ticker": "XYZ", "watchlist_label": "SPECULATIVE_10X",
     "appearance_date": "2026-07-01", "ret_5d": 3.0},
    # GONE was in the previous scan, absent now (EXITED)
    {"ticker": "GONE", "watchlist_label": "CATALYST",
     "appearance_date": "2026-07-09"},
    # second ticker on 07-01 so 07-09 is unambiguously "previous"
    {"ticker": "GONE", "watchlist_label": "CATALYST",
     "appearance_date": "2026-07-01"},
]


@pytest.fixture
def payload(tmp_path):
    root = _root(tmp_path, _scanner(TICKERS), LEDGER)
    return lsp.build_latest_scan(root, now=NOW)


# 1 + 6. grouping + empty programs stay visible ------------------------------


def test_candidates_grouped_by_program_and_empty_visible(payload):
    progs = payload["programs"]
    assert set(progs) == {"TACTICAL", "SWING", "LONG_TERM"}
    assert [c["ticker"] for c in progs["SWING"]["candidates"]] == ["FBIN"]
    assert [c["ticker"] for c in progs["TACTICAL"]["candidates"]] == ["RH"]
    assert [c["ticker"] for c in progs["LONG_TERM"]["candidates"]] == ["XYZ"]
    # empty program still rendered in terminal output
    empty_payload = dict(payload)
    empty_payload["programs"] = {**payload["programs"],
                                 "LONG_TERM": {"candidate_count": 0,
                                               "new_count": 0,
                                               "repeat_count": 0,
                                               "returning_count": 0,
                                               "exited_count": 0,
                                               "candidates": [],
                                               "exited": []}}
    txt = lsp.render_terminal(empty_payload)
    assert "No candidates detected in the latest scan." in txt
    assert "LONG_TERM" in txt


# 2 + 3. single primary + secondary preserved --------------------------------


def test_single_primary_with_secondary_programs(payload):
    fbin = payload["programs"]["SWING"]["candidates"][0]
    assert fbin["program"] == "SWING"
    assert fbin["secondary_programs"] == ["TACTICAL"]
    assert "primary watchlist label wins" in fbin["routing_reason"]
    # FBIN appears in exactly one program section
    appearances = [pid for pid, b in payload["programs"].items()
                   if any(c["ticker"] == "FBIN"
                          for c in b["candidates"])]
    assert appearances == ["SWING"]


# 4. latest artifact only -----------------------------------------------------


def test_candidates_come_from_latest_artifact_not_aggregates(payload):
    # GONE has ledger history but is absent from the scanner artifact —
    # it must not appear as a candidate anywhere
    for block in payload["programs"].values():
        assert all(c["ticker"] != "GONE" for c in block["candidates"])
    assert payload["source_artifact"].endswith(
        "research_scanner_latest.json")
    assert payload["candidate_count"] == 3


# 5. NEW / REPEAT / RETURNING / EXITED ----------------------------------------


def test_scan_status_classification(payload):
    progs = payload["programs"]
    assert progs["SWING"]["candidates"][0]["scan_status"] == "REPEAT"
    assert progs["TACTICAL"]["candidates"][0]["scan_status"] == "NEW"
    assert progs["LONG_TERM"]["candidates"][0]["scan_status"] == "RETURNING"
    # GONE exited; its label CATALYST routes it to TACTICAL's exited list
    assert progs["TACTICAL"]["exited_count"] == 1
    assert progs["TACTICAL"]["exited"][0]["ticker"] == "GONE"
    assert progs["SWING"]["repeat_count"] == 1
    assert progs["LONG_TERM"]["returning_count"] == 1


# 7. terminal completeness ----------------------------------------------------


def test_terminal_output_carries_required_fields(payload):
    # verbose mode preserves the full per-candidate metadata block
    txt = lsp.render_terminal(payload, verbose=True)
    assert "SWING — 1 candidate" in txt
    assert "hold=45-60td" in txt
    assert "reason=reason for FBIN" in txt
    assert "lifecycle=" in txt
    assert "verdict=INSUFFICIENT_MATURE_EVIDENCE" in txt


# 8. adapter payload ----------------------------------------------------------


def test_adapter_payload_includes_candidate_metadata(tmp_path):
    root = _root(tmp_path, _scanner(TICKERS), LEDGER)
    payload = lsp.build_latest_scan(root, now=NOW)
    lsp.write_outputs(payload, root)
    from dashboards.research_command_center.data_adapter import (
        ArtifactStore, build_latest_research_scan)
    ls = build_latest_research_scan(ArtifactStore(root=root))
    assert ls["present"] is True
    c = ls["programs"]["SWING"]["candidates"][0]
    for key in ("ticker", "scan_status", "program", "secondary_programs",
                "holding_period", "label", "research_hypothesis",
                "detection_reason", "confidence", "lifecycle_stage",
                "rank", "evidence_maturity", "current_benchmark",
                "program_verdict", "research_only"):
        assert key in c, key


# 9. digest separation --------------------------------------------------------


def test_digest_summarises_latest_scan_without_repeating_records(payload):
    """The digest carries the per-program counts and points at the sidecar
    for the records themselves — repeating three candidate rows per program
    was the largest block of duplicated candidate detail in the note."""
    from dashboards.research_command_center.journal_digest import (
        _latest_scan_lines)
    text = "\n".join(_latest_scan_lines({"latest_scan": payload}))
    assert text.startswith("- Latest scan (as-of ")
    for pid in ("TACTICAL", "SWING", "LONG_TERM"):
        assert pid in text
    assert "per-candidate records in the latest-scan sidecar" in text
    # the records themselves are not reprinted here
    assert "FBIN [REPEAT]" not in text
    assert "Exited since previous scan" not in text
    # forward-evidence style aggregates do not belong here either
    assert "matured 5d:" not in text and "win rate" not in text.lower()


# 10. no selection-logic change ----------------------------------------------


def test_no_scanner_or_gate_modules_imported():
    source = Path(lsp.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("research_scanner", "scanner_truth", "alpaca", "tradier",
                 "execution", "broker", "core.config", "order",
                 "strategies", "council", "requests", "anthropic")
    for mod in imported:
        assert not any(f in mod for f in forbidden), mod


def test_build_is_read_only(tmp_path):
    root = _root(tmp_path, _scanner(TICKERS), LEDGER)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    lsp.build_latest_scan(root, now=NOW)
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after


# 11. research_only invariant --------------------------------------------------


def test_every_candidate_is_research_only(payload):
    for block in payload["programs"].values():
        for c in block["candidates"]:
            assert c["research_only"] is True
    assert payload["research_only"] is True
    assert payload["guardrails"]["priority_is_not_validated_alpha"] is True


# 12. stale scan warning -------------------------------------------------------


def test_stale_scan_produces_visible_warning(tmp_path):
    old = _scanner(TICKERS, generated="2026-07-08T00:36:00+00:00")
    root = _root(tmp_path, old, LEDGER)
    payload = lsp.build_latest_scan(root, now=NOW)
    assert payload["scan_stale"] is True
    txt = lsp.render_terminal(payload)
    assert "SCAN STALE" in txt
    fresh = lsp.build_latest_scan(
        _root(tmp_path / "b", _scanner(TICKERS), LEDGER), now=NOW)
    assert fresh["scan_stale"] is False


# 13. scan-integrity linkage (manifest hash + market session) -----------------


def test_manifest_hash_and_session_match_are_verified(payload):
    assert payload["manifest_hash_match"] is True
    assert payload["market_session_match"] is True
    assert payload["market_as_of_date"] == "2026-07-09"
    assert payload["scan_readiness"] == "READY"
    assert payload["integrity_warnings"] == []
    txt = lsp.render_terminal(payload)
    assert "market as-of 2026-07-09" in txt
    assert "universe hash MATCH" in txt


def test_manifest_hash_mismatch_produces_visible_warning(tmp_path):
    doc = _scanner(TICKERS, manifest_hash="1111111111111111")
    root = _root(tmp_path, doc, LEDGER, manifest_hash="2222222222222222")
    payload = lsp.build_latest_scan(root, now=NOW)
    assert payload["manifest_hash_match"] is False
    assert any("does not match the current manifest" in w
               for w in payload["integrity_warnings"])
    assert "universe hash MISMATCH" in lsp.render_terminal(payload)


def test_market_session_mismatch_produces_visible_warning(tmp_path):
    doc = _scanner(TICKERS, market_as_of="2026-07-08")
    root = _root(tmp_path, doc, LEDGER, required_session="2026-07-09")
    payload = lsp.build_latest_scan(root, now=NOW)
    assert payload["market_session_match"] is False
    assert any("does not match the manifest required session" in w
               for w in payload["integrity_warnings"])


def test_degraded_scan_readiness_is_a_visible_warning(tmp_path):
    doc = _scanner(TICKERS, readiness="DEGRADED")
    root = _root(tmp_path, doc, LEDGER)
    payload = lsp.build_latest_scan(root, now=NOW)
    assert any(w.startswith("scan integrity DEGRADED")
               for w in payload["integrity_warnings"])


# 14. per-candidate completeness (required display fields) --------------------


REQUIRED_CANDIDATE_FIELDS = (
    "ticker", "program", "holding_period", "detection_reason",
    "research_hypothesis", "priority", "confidence", "lifecycle_stage",
    "scan_status", "evidence_maturity", "program_verdict",
    "bar_as_of_date", "benchmark_as_of_date", "same_session",
    "source_scan_generated_at", "research_only",
)


def test_every_candidate_carries_all_required_fields(payload):
    seen = 0
    for block in payload["programs"].values():
        for c in block["candidates"]:
            seen += 1
            for field in REQUIRED_CANDIDATE_FIELDS:
                assert field in c, f"{c['ticker']} missing {field}"
            assert c["research_only"] is True
            assert c["same_session"] is True
            assert c["bar_as_of_date"] == c["benchmark_as_of_date"]
    assert seen == len(TICKERS)


def test_terminal_shows_candidate_as_of_dates(payload):
    # as-of dates are part of the verbose per-candidate block
    txt = lsp.render_terminal(payload, verbose=True)
    assert "bar=2026-07-09" in txt
    assert "bench=2026-07-09" in txt
    assert "same-session" in txt
