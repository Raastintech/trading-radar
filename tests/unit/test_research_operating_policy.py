from __future__ import annotations

import json
from pathlib import Path

from dashboards.research_command_center import journal_digest as jd
from dashboards.research_command_center.data_adapter import (
    ArtifactStore,
    build_research_operating_policy,
)
from research import cohort_attribution as ca
from research import research_operating_policy as policy

ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _entry(ticker: str, ret: float, *, program: str, category: str,
           quality: str, extension: str, cap: int, sector: str,
           date: str = "2026-01-02") -> dict:
    row = {
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
    }
    for h in (5, 10, 15, 20):
        row[f"ret_{h}d"] = ret
        row[f"ret_{h}d_vs_spy"] = ret - 1.0
        row[f"ret_{h}d_vs_qqq"] = ret - 2.0
        row[f"ret_{h}d_vs_iwm"] = ret - 3.0
        row[f"ret_{h}d_vs_sector"] = ret - 0.5
        row[f"mae_{h}d"] = min(ret, 0.0)
    for h in (30, 45, 60):
        row[f"ret_{h}d"] = None
    return row


def _fixture(root: Path) -> dict:
    rows = []
    for i in range(12):
        rows.append(_entry(
            f"LOSE{i}", -10.0, program="SWING",
            category="rs_momentum_leader", quality="UNPROFITABLE_FUNDED",
            extension="EXTENDED_CROWDED", cap=150_000_000,
            sector="Technology"))
    for i in range(12):
        rows.append(_entry(
            f"WIN{i}", 5.0, program="TACTICAL",
            category="catalyst_watch", quality="PROFITABLE_CASHGEN",
            extension="RESET_WATCH", cap=5_000_000_000,
            sector="Industrials"))
    _write_jsonl(root / ca.HISTORY_REL, rows)
    _write_jsonl(root / ca.HC_HISTORY_REL, [{
        "ticker": "HCNEW", "appearance_date": "2026-01-20",
        "classification": "HIGH_CONVICTION", "research_only": True,
    }])
    _write_jsonl(root / ca.EO_HISTORY_REL, [{
        "ticker": "EONEW", "appearance_date": "2026-01-20",
        "lane": "EMERGING_OUTLIER_WATCH", "research_only": True,
    }])
    _write_json(root / ca.FORWARD_REL, {
        "generated_at": "2026-01-25T00:00:00+00:00",
        "overall": {"verdict": "NO_FORWARD_EDGE"},
        "research_only": True,
    })
    _write_json(root / ca.SUMMARY_REL, {
        "forward_evidence": {"alpha_proven": False},
        "research_only": True,
    })
    report = ca.build_report(root)
    _write_json(root / policy.COHORT_ATTRIBUTION_REL, report)
    return report


def _labels(report: dict, group: str) -> list[str]:
    return [i["label"] for i in report["policy_groups"][group]]


def _cohort_ids(report: dict, group: str) -> set[str]:
    return {i["affected_cohort"] for i in report["policy_groups"][group]}


def test_policy_artifact_generates_from_cohort_attribution(tmp_path):
    _fixture(tmp_path)

    report = policy.build_report(tmp_path)
    paths = policy.write_artifacts(report, tmp_path)

    written = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert written["kind"] == "research_operating_policy"
    assert written["source_artifacts"]["cohort_attribution"] == str(policy.COHORT_ATTRIBUTION_REL)
    assert written["guardrails"]["research_only"] is True
    assert written["promote_to_signal"] is False


def test_broad_scanner_weak_routes_to_discovery_only_not_focus_now(tmp_path):
    _fixture(tmp_path)

    report = policy.build_report(tmp_path)

    assert "broad_scanner" in _cohort_ids(report, "discovery_only")
    assert "broad_scanner" not in _cohort_ids(report, "focus_now")
    assert report["guardrails"]["broad_scanner_not_focus_now"] is True


def test_reset_watch_for_entry_can_appear_in_focus_now(tmp_path):
    _fixture(tmp_path)

    report = policy.build_report(tmp_path)

    assert "Reset/watch-for-entry candidates" in _labels(report, "focus_now")


def test_extended_names_appear_in_use_caution_when_weak(tmp_path):
    _fixture(tmp_path)

    report = policy.build_report(tmp_path)

    assert "Extended names" in _labels(report, "use_caution")


def test_hc_and_eo_are_too_early_below_matured_floor(tmp_path):
    _fixture(tmp_path)

    report = policy.build_report(tmp_path)
    labels = _labels(report, "do_not_conclude_yet")

    assert "High-Conviction Alpha" in labels
    assert "Emerging Outlier Watch" in labels
    for item in report["policy_groups"]["do_not_conclude_yet"][:2]:
        assert item["maturity_status"] == "TOO_EARLY"
        assert item["confidence"] == "VERY_LOW"


def test_policy_does_not_mutate_candidate_artifacts(tmp_path):
    _fixture(tmp_path)
    candidate_path = tmp_path / "cache/research/latest_scan_programs_latest.json"
    _write_json(candidate_path, {"present": True, "programs": {"SWING": {"candidates": [{"ticker": "LOSE0"}]}}})
    before = candidate_path.read_text(encoding="utf-8")

    report = policy.build_report(tmp_path)
    policy.write_artifacts(report, tmp_path)

    assert candidate_path.read_text(encoding="utf-8") == before


def test_dashboard_reads_compact_policy_only(tmp_path):
    _write_json(tmp_path / policy.OUT_JSON_REL, {
        "generated_at": "2026-01-25T00:00:00+00:00",
        "primary_horizon": "10d",
        "dashboard_summary": {
            "focus_now": [{"label": "Reset/watch-for-entry candidates"}],
            "use_caution": [{"label": "Extended names"}],
            "do_not_conclude_yet": [{"label": "High-Conviction Alpha"}],
        },
        "policy_groups": {"focus_now": [{"full": "detail"}]},
        "guardrails": {"research_only": True},
        "research_only": True,
    })

    payload = build_research_operating_policy(ArtifactStore(root=tmp_path), compact=True)
    html = (ROOT / "dashboards/research_command_center/static/index.html").read_text(encoding="utf-8")
    panel = html.split("function researchOperatingPolicyPanel", 1)[1].split("/* ── System Trust", 1)[0]

    assert payload["dashboard_summary"]["focus_now"][0]["label"] == "Reset/watch-for-entry candidates"
    assert "policy_groups" not in payload
    assert "Research Operating Policy" in panel
    assert ".policy_groups" not in panel


def test_journal_includes_concise_operating_policy():
    inputs = {
        "research_operating_policy": {
            "dashboard_summary": {
                "focus_now": [{"label": "Reset/watch-for-entry candidates"}],
                "use_caution": [{"label": "Extended names"}],
                "discovery_only": [{"label": "Broad scanner"}],
                "do_not_conclude_yet": [{"label": "High-Conviction Alpha"}],
                "immature_reminder": "High-Conviction Alpha has fewer than 10 matured 10d primary samples.",
            }
        }
    }

    text = "\n".join(jd._section_research_operating_policy(inputs))

    assert "## Research Operating Policy" in text
    assert "Operator attention today: Reset/watch-for-entry candidates" in text
    assert "Use caution: Extended names" in text
    assert "Do not conclude yet: High-Conviction Alpha" in text
    assert "does not remove candidates" in text


def test_no_scanner_scoring_routing_filter_logic_changed_by_policy_module():
    for rel in (
        "research/research_scanner.py",
        "research/research_scoring.py",
        "research/research_programs.py",
        "research/latest_scan_programs.py",
        "research/high_conviction_alpha.py",
        "research/emerging_outlier_watch.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "research_operating_policy" not in text


def test_policy_research_only_invariants_preserved(tmp_path):
    _fixture(tmp_path)

    report = policy.build_report(tmp_path)

    assert report["research_only"] is True
    assert report["promote_to_signal"] is False
    assert report["guardrails"]["no_selection_change"] is True
    assert report["guardrails"]["no_gate_threshold_or_factor_weight_change"] is True
    assert report["guardrails"]["no_high_conviction_rule_change"] is True
    assert report["guardrails"]["no_emerging_outlier_rule_change"] is True
