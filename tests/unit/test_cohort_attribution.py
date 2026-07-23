from __future__ import annotations

import json
from pathlib import Path

from dashboards.research_command_center.data_adapter import (
    ArtifactStore,
    build_cohort_attribution,
)
from dashboards.research_command_center import journal_digest as jd
from research import cohort_attribution as ca

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


def _fixture(root: Path) -> list[dict]:
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
    for i in range(3):
        rows.append(_entry(
            f"WARR{i}W", -5.0, program="SWING",
            category="beaten_down_recovery", quality="UNKNOWN",
            extension="LATE", cap=50_000_000,
            sector="Healthcare"))
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
        "overall": {"verdict": "NO_FORWARD_EDGE", "mean_ret_10d": -1.67},
        "research_only": True,
    })
    _write_json(root / ca.SUMMARY_REL, {
        "forward_evidence": {"alpha_proven": False},
        "research_only": True,
    })
    return rows


def _by_id(report: dict, cohort_id: str) -> dict:
    return next(c for c in report["cohorts"] if c["id"] == cohort_id)


def test_cohort_report_generates_from_repaired_forward_artifact(tmp_path):
    _fixture(tmp_path)

    report = ca.build_report(tmp_path)

    assert report["kind"] == "cohort_attribution"
    assert report["overall_forward"]["verdict"] == "NO_FORWARD_EDGE"
    assert report["alpha_proven"] is False
    assert report["guardrails"]["research_only"] is True
    assert report["source_artifacts"]["forward"] == str(ca.FORWARD_REL)


def test_counts_by_cohort_reconcile_to_source_rows(tmp_path):
    rows = _fixture(tmp_path)

    report = ca.build_report(tmp_path)
    broad = _by_id(report, "broad_scanner")

    assert broad["count"] == len(rows)
    assert broad["horizons"]["10d"]["matured_count"] == len(rows)


def test_small_hc_and_eo_samples_are_immature_and_not_overstated(tmp_path):
    _fixture(tmp_path)

    report = ca.build_report(tmp_path)
    hc = _by_id(report, "high_conviction_alpha")
    eo = _by_id(report, "emerging_outlier_watch")

    assert hc["primary_result_text"] == "Too early to evaluate."
    assert eo["primary_result_text"] == "Too early to evaluate."
    assert report["dashboard_summary"]["high_conviction"]["status"] == "immature"
    assert report["dashboard_summary"]["emerging_outlier"]["status"] == "immature"
    assert "Too early to evaluate" in report["dashboard_summary"]["sample_maturity_warning"]


def test_worst_and_best_cohorts_are_deterministic(tmp_path):
    _fixture(tmp_path)

    report = ca.build_report(tmp_path)

    assert report["dashboard_summary"]["biggest_drag_cohort"]["id"] in {
        "program_swing", "rs_momentum_leaders", "extension_extended",
        "profitability_unprofitable", "quality_low_quality",
    }
    assert report["dashboard_summary"]["best_current_cohort"]["id"] in {
        "program_tactical", "catalyst_names", "profitability_profitable",
        "quality_high_quality", "extension_reset_or_watch_for_entry",
        "market_cap_mid",
    }


def test_required_split_cohorts_are_separated(tmp_path):
    _fixture(tmp_path)

    report = ca.build_report(tmp_path)

    assert _by_id(report, "security_non_common")["count"] == 3
    assert _by_id(report, "security_common")["count"] == 24
    assert _by_id(report, "extension_extended")["count"] == 15
    assert _by_id(report, "extension_reset_or_watch_for_entry")["count"] == 12
    assert _by_id(report, "profitability_profitable")["count"] == 12
    assert _by_id(report, "profitability_unprofitable")["count"] == 12


def test_dashboard_reads_compact_attribution_only(tmp_path):
    _write_json(tmp_path / ca.OUT_JSON_REL, {
        "generated_at": "2026-01-25T00:00:00+00:00",
        "dashboard_summary": {"broad_scanner": {"status": "no edge"}},
        "guardrails": {"research_only": True},
        "cohorts": [{"id": "large-detail"}],
        "research_only": True,
    })

    payload = build_cohort_attribution(ArtifactStore(root=tmp_path), compact=True)
    html = (ROOT / "dashboards/research_command_center/static/index.html").read_text(encoding="utf-8")
    panel = html.split("function cohortAttributionPanel", 1)[1].split("function cohortAttributionDetail", 1)[0]

    assert payload["dashboard_summary"]["broad_scanner"]["status"] == "no edge"
    assert "cohorts" not in payload
    assert "Cohort Attribution" in panel
    assert ".cohorts" not in panel


def test_journal_digest_includes_concise_cohort_attribution():
    inputs = {
        "cohort_attribution": {
            "dashboard_summary": {
                "biggest_drag_cohort": {"label": "Swing Research", "mean_return_pct": -5.0, "win_rate": 0.35},
                "best_current_cohort": {"label": "Reset", "mean_return_pct": 1.0, "win_rate": 0.65},
                "high_conviction": {"matured_count": 0, "result": "Too early to evaluate."},
                "emerging_outlier": {"matured_count": 0, "result": "Too early to evaluate."},
                "sample_maturity_warning": "High Conviction: Too early to evaluate.",
            }
        }
    }

    text = "\n".join(jd._section_cohort_attribution(inputs))

    assert "## Cohort Attribution" in text
    assert "Dragging performance: Swing Research" in text
    assert "Too early to evaluate" in text
    assert "Do not conclude HC/EO alpha" in text


def test_no_scanner_scoring_routing_filter_logic_changed_by_attribution_module():
    for rel in (
        "research/research_scanner.py",
        "research/research_scoring.py",
        "research/research_programs.py",
        "research/latest_scan_programs.py",
        "research/high_conviction_alpha.py",
        "research/emerging_outlier_watch.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "cohort_attribution" not in text


def test_research_only_invariants_preserved(tmp_path):
    _fixture(tmp_path)

    report = ca.build_report(tmp_path)

    assert report["research_only"] is True
    assert report["guardrails"]["no_selection_change"] is True
    assert report["guardrails"]["no_gate_threshold_or_factor_weight_change"] is True
    assert report["guardrails"]["no_high_conviction_rule_change"] is True
    assert report["guardrails"]["no_emerging_outlier_rule_change"] is True
