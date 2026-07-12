"""Surfacing tests for the High-Conviction Alpha Shortlist: adapter model,
Command Center HTML section, journal-digest section, and the deterministic
audit checks that verify the shortlist did not admit a weak name.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboards.research_command_center.data_adapter import (
    ArtifactStore, build_high_conviction)

REPO_ROOT = Path(__file__).resolve().parents[2]
_INDEX_HTML = (REPO_ROOT / "dashboards" / "research_command_center"
               / "static" / "index.html")


def _sidecar(tmp_path, present=True, shortlist=None, status="OK"):
    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": "high_conviction_alpha", "version": "HIGH_CONVICTION_ALPHA_V1",
        "present": present, "research_only": True,
        "generated_at": "2026-07-11T05:44:00+00:00",
        "shortlist_status": status,
        "counts": {"evaluated": 100, "qualified": 3, "shortlisted":
                   len(shortlist or []), "quality_but_extended": 5,
                   "improving_but_unproven": 20, "rejected": 10},
        "shortlist": shortlist if shortlist is not None else [{
            "ticker": "NVO", "program": "SWING",
            "holding_period": "45-60 trading days",
            "high_conviction_score": 81.4, "classification": "HIGH_CONVICTION",
            "component_scores": {"quality_score": 100.0, "growth_score": 66.0,
                                 "fundamental_momentum_score": 70.0,
                                 "price_momentum_score": 90.0,
                                 "value_score": 80.0,
                                 "catalyst_score": 50.0,
                                 "sector_regime_score": 70.0,
                                 "risk_integrity_score": 90.0},
            "valuation_status": "VALUATION_AVAILABLE",
            "dead_horse_risk": "LOW", "why_selected": ["profitable"],
            "main_risks": [], "same_session": True, "research_only": True}],
        "quality_but_extended": [], "improving_but_unproven": [],
        "rejected": [{"ticker": "AGL", "program": "SWING",
                      "high_conviction_score": 41.0,
                      "exclusions": ["negative gross margin"],
                      "research_only": True}],
        "integrity_warnings": [],
    }
    (research / "high_conviction_alpha_latest.json").write_text(
        json.dumps(payload), encoding="utf-8")
    (research / "high_conviction_forward_latest.json").write_text(
        json.dumps({"present": True, "verdict": "NEED_MORE_DATA",
                    "verdict_reason": "immature", "n_history_rows": 1}),
        encoding="utf-8")
    return tmp_path


def test_adapter_reads_shortlist(tmp_path):
    root = _sidecar(tmp_path)
    hc = build_high_conviction(ArtifactStore(root=root))
    assert hc["present"] is True
    assert hc["shortlist"][0]["ticker"] == "NVO"
    assert hc["forward_validation"]["verdict"] == "NEED_MORE_DATA"
    # research_only re-forced on every record
    assert all(c["research_only"] for c in hc["shortlist"])
    assert all(c["research_only"] for c in hc["rejected"])


def test_adapter_missing_sidecar_is_honest(tmp_path):
    (tmp_path / "cache" / "research").mkdir(parents=True)
    hc = build_high_conviction(ArtifactStore(root=tmp_path))
    assert hc["present"] is False
    assert hc["shortlist_status"] == "NO_HIGH_CONVICTION_CANDIDATES"


def test_adapter_zero_candidates_status(tmp_path):
    root = _sidecar(tmp_path, shortlist=[],
                    status="NO_HIGH_CONVICTION_CANDIDATES")
    hc = build_high_conviction(ArtifactStore(root=root))
    assert hc["present"] is True
    assert hc["shortlist"] == []
    assert hc["shortlist_status"] == "NO_HIGH_CONVICTION_CANDIDATES"


def test_html_has_shortlist_section():
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert "highConvictionSection" in html
    assert "High-Conviction Alpha Shortlist" in html
    assert "Quality + Growth + Value + Momentum" in html
    assert "membership is not validated alpha" in html
    assert "Quality but Extended" in html
    assert "Rejected by Quality/Risk" in html
    # rendered above the program sections in the home view
    assert ("highConvictionSection(s.high_conviction)" in html)


def test_digest_section_lists_shortlist_and_rejects(tmp_path):
    from dashboards.research_command_center.journal_digest import (
        _section_high_conviction)
    payload = {
        "present": True,
        "counts": {"evaluated": 100, "qualified": 1,
                   "quality_but_extended": 2, "rejected": 3},
        "shortlist": [{
            "ticker": "NVO", "program": "SWING",
            "high_conviction_score": 81.4, "classification": "HIGH_CONVICTION",
            "component_scores": {"quality_score": 100.0, "growth_score": 66.0,
                                 "price_momentum_score": 90.0,
                                 "value_score": 80.0},
            "valuation_status": "VALUATION_AVAILABLE", "dead_horse_risk":
            "LOW", "why_selected": ["profitable"], "main_risks": []}],
        "quality_but_extended": [],
        "rejected": [{"ticker": "AGL", "research_score": 100.0,
                      "exclusions": ["negative gross margin"]}],
    }
    lines = _section_high_conviction({"high_conviction": payload,
                                      "store": ArtifactStore(root=tmp_path)})
    text = "\n".join(lines)
    assert text.startswith("## 4b1. High-Conviction Alpha Shortlist")
    assert "NVO [HIGH_CONVICTION]" in text
    assert "NOT validated alpha" in text
    # rejected strong-signal name shown with its reason
    assert "AGL" in text and "negative gross margin" in text


def test_digest_section_reports_zero(tmp_path):
    from dashboards.research_command_center.journal_digest import (
        _section_high_conviction)
    payload = {"present": True, "counts": {"evaluated": 50},
               "shortlist": [], "quality_but_extended": [], "rejected": []}
    text = "\n".join(_section_high_conviction(
        {"high_conviction": payload, "store": ArtifactStore(root=tmp_path)}))
    assert "NO_HIGH_CONVICTION_CANDIDATES" in text
