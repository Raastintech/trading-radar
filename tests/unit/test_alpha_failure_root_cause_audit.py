"""Tests for the alpha failure root-cause audit (diagnostic, cache-only).

Covers the required checklist from the audit task: report generation,
each required section present, dashboard panel stays compact, no
scanner/scoring/routing/filter logic touched, and research-only
invariants preserved.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dashboards.research_command_center import journal_digest as jd
from dashboards.research_command_center.data_adapter import (
    ArtifactStore,
    build_alpha_root_cause,
)
from research import alpha_failure_root_cause_audit as rca
from research import cohort_attribution as ca

ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _calendar(n: int = 80, start: str = "2025-01-01") -> list[str]:
    return [d.date().isoformat() for d in pd.bdate_range(start=start, periods=n)]


def _write_price(root: Path, ticker: str, dates: list[str], closes: list[float]) -> None:
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    df = pd.DataFrame({"close": closes}, index=idx)
    path = root / "cache" / "prices" / f"{ticker}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


def _entry(ticker: str, ret: float, *, program: str, category: str,
           quality: str, extension: str, cap: int, sector: str,
           date: str) -> dict:
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


def _fixture(root: Path) -> tuple[list[dict], list[str]]:
    cal = _calendar(80)
    mid = cal[40]

    def _series(bias: float, wiggle: float = 0.0, pullback_at: int | None = None) -> list[float]:
        vals = []
        p = 100.0
        for i in range(len(cal)):
            if pullback_at is not None and i == pullback_at:
                p *= 0.95
            else:
                p *= (1.0 + bias + (wiggle if i % 2 == 0 else -wiggle))
            vals.append(round(p, 4))
        return vals

    _write_price(root, "SPY", cal, _series(0.0005))

    rows: list[dict] = []
    win_tickers = [f"WIN{i}" for i in range(6)]
    for i, t in enumerate(win_tickers):
        _write_price(root, t, cal, _series(0.004, pullback_at=42))
        rows.append(_entry(
            t, 5.0, program="TACTICAL", category="catalyst_watch",
            quality="PROFITABLE_CASHGEN", extension="RESET_WATCH",
            cap=5_000_000_000, sector="Industrials", date=mid))

    lose_tickers = [f"LOSE{i}" for i in range(12)]
    for i, t in enumerate(lose_tickers):
        _write_price(root, t, cal, _series(-0.004))
        rows.append(_entry(
            t, -10.0, program="SWING", category="rs_momentum_leader",
            quality="UNPROFITABLE_FUNDED", extension="EXTENDED_CROWDED",
            cap=150_000_000, sector="Technology", date=mid))

    # POOLX is only in the universe pool (never selected) — random control fodder.
    _write_price(root, "POOLX", cal, _series(0.001))

    # REPT appears 3 times with a declining return sequence.
    rept_dates = [cal[20], cal[40], cal[55]]
    _write_price(root, "REPT", cal, _series(0.0))
    for i, (date, ret) in enumerate(zip(rept_dates, (4.0, 1.0, -3.0))):
        rows.append(_entry(
            "REPT", ret, program="SWING", category="beaten_down_recovery",
            quality="UNKNOWN", extension="LATE", cap=50_000_000,
            sector="Healthcare", date=date))

    _write_jsonl(root / ca.HISTORY_REL, rows)
    _write_jsonl(root / ca.HC_HISTORY_REL, [])
    _write_jsonl(root / ca.EO_HISTORY_REL, [])
    _write_json(root / ca.FORWARD_REL, {
        "generated_at": "2025-04-01T00:00:00+00:00",
        "overall": {"verdict": "NO_FORWARD_EDGE", "mean_ret_10d": -1.67},
        "research_only": True,
    })
    _write_json(root / ca.SUMMARY_REL, {
        "forward_evidence": {"alpha_proven": False},
        "research_only": True,
    })

    universe_full = [{"ticker": t, "source": "fixture"} for t in win_tickers + lose_tickers + ["POOLX", "REPT"]]
    _write_json(root / rca.UNIVERSE_BUILD_REL, {
        "generated_at": "2025-04-01T00:00:00+00:00",
        "universe_full": universe_full,
    })

    # Build+write the cohort_attribution sidecar from the SAME history so the
    # audit's rankings/cohort references match the raw rows exactly.
    cohort_report = ca.build_report(root)
    ca.write_artifacts(cohort_report, root)

    return rows, cal


@pytest.fixture(autouse=True)
def _stub_regime_labels(monkeypatch):
    """Regime labels are reconstructed from the REAL repo's price cache
    (research/strategy_lab_regime.py hardcodes its own ROOT), so unit tests
    must stub it out rather than depend on real market data."""
    def _fake(start=None, end=None):
        return {
            "kind": "strategy_lab_regime_labels",
            "start": start,
            "end": end,
            "rows": [
                {"date": "2025-01-02", "label": "BULL_TREND"},
                {"date": "2025-02-01", "label": "CHOP"},
                {"date": "2025-03-01", "label": "RISK_OFF"},
            ],
        }
    monkeypatch.setattr(rca, "build_regime_labels", _fake)
    yield


def test_root_cause_report_generates(tmp_path):
    _fixture(tmp_path)

    report = rca.build_report(tmp_path)

    assert report["kind"] == "alpha_failure_root_cause_audit"
    assert report["research_only"] is True
    assert not report.get("fallback")


def test_signal_family_attribution_exists(tmp_path):
    _fixture(tmp_path)
    report = rca.build_report(tmp_path)

    families = report["signal_family_attribution"]["families"]
    assert "rs_momentum" in families
    assert "catalyst" in families
    assert families["rs_momentum"]["mean_return_pct"] < 0


def test_entry_timing_audit_exists(tmp_path):
    _fixture(tmp_path)
    report = rca.build_report(tmp_path)

    et = report["entry_timing_audit"]
    variant_names = {v["variant"] for v in et["variants"]}
    assert variant_names == set(rca.ENTRY_VARIANTS)
    assert et["candidates_considered"] > 0
    # Non-mutating: no trade recommendation language / gate change fields.
    assert "conclusion" in et


def test_regime_conditioned_split_exists(tmp_path):
    _fixture(tmp_path)
    report = rca.build_report(tmp_path)

    regime = report["regime_conditioned_evidence"]
    assert regime["present"] is True
    assert isinstance(regime["regimes"], list)


def test_factor_ablation_exists_and_flags_extension_as_a_drag(tmp_path):
    _fixture(tmp_path)
    report = rca.build_report(tmp_path)

    ablation = report["factor_ablation"]
    rules = {a["rule"] for a in ablation["ablations"]}
    assert {
        "remove_unprofitable", "remove_extended", "remove_extreme_rs_momentum",
        "remove_technology", "remove_small_micro_cap", "remove_repeat_candidates",
    } <= rules
    remove_extended = next(a for a in ablation["ablations"] if a["rule"] == "remove_extended")
    # LOSE* rows are EXTENDED and universally negative; removing them must help.
    assert remove_extended["delta_vs_baseline_mean_pct"] > 0


def test_baseline_comparison_exists(tmp_path):
    _fixture(tmp_path)
    report = rca.build_report(tmp_path)

    baseline = report["baseline_comparison"]
    assert "random_same_universe_control" in baseline
    assert "sector_matched_control" in baseline
    assert "market_cap_matched_control" in baseline
    assert "candidate_pool_vs_market_benchmarks" in baseline


def test_repeat_candidate_audit_exists_and_detects_decay(tmp_path):
    _fixture(tmp_path)
    report = rca.build_report(tmp_path)

    repeat = report["repeat_candidate_audit"]
    assert repeat["repeated_tickers"] >= 1
    assert repeat["repeated_tickers_with_declining_return"] >= 1


def test_stop_continue_gates_exist(tmp_path):
    _fixture(tmp_path)
    report = rca.build_report(tmp_path)

    sc = report["stop_continue_framework"]
    dates = [d["date"] for d in sc["decision_dates"]]
    assert dates == ["2026-08-17", "2026-09-07", "2026-09-30"]
    assert sc["current_recommendation"] in {"CONTINUE", "NARROW", "FREEZE", "SUNSET"}
    assert set(sc["gate_definitions"].keys()) == {"CONTINUE", "NARROW", "FREEZE", "SUNSET"}


def test_continue_rationale_promises_no_promotion():
    """Under a permanent research-only posture there is no promotion path,
    so the CONTINUE rationale must not point at one — the gate's next step
    is another review, never a signal or live capital."""
    cohort_report = {
        "primary_horizon": "10d",
        "cohorts": [{
            "id": "high_conviction_alpha",
            "label": "High-Conviction Alpha",
            "horizons": {"10d": {"matured_count": 400,
                                 "sample_status": "ROBUST",
                                 "mean_return_pct": 2.5,
                                 "win_rate": 0.55}},
        }],
    }
    sc = rca._evaluate_stop_continue(
        cohort_report, {"top_likely_failure_cause": {}}, {})

    assert sc["recommendation"] == "CONTINUE"
    text = sc["rationale"].lower()
    assert "promotion review" not in text
    assert "research-only" in text and "not validated" in text
    assert "no promotion path" in text


def test_dashboard_summary_fields_present(tmp_path):
    _fixture(tmp_path)
    report = rca.build_report(tmp_path)

    dash = report["dashboard_summary"]
    for key in ("top_likely_failure_cause", "best_surviving_cohort",
                "worst_harmful_cohort", "next_decision_date", "current_recommendation"):
        assert key in dash


def test_research_only_invariants_preserved(tmp_path):
    _fixture(tmp_path)
    report = rca.build_report(tmp_path)

    g = report["guardrails"]
    assert g["research_only"] is True
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


def test_missing_cohort_attribution_returns_fallback(tmp_path):
    report = rca.build_report(tmp_path)

    assert report["fallback"] == "MISSING_ARTIFACT"
    assert report["guardrails"]["research_only"] is True


def test_write_artifacts_produces_json_and_text(tmp_path):
    _fixture(tmp_path)
    report = rca.build_report(tmp_path)

    paths = rca.write_artifacts(report, tmp_path)

    assert Path(paths["json"]).exists()
    assert Path(paths["text"]).exists()
    text = Path(paths["text"]).read_text(encoding="utf-8")
    assert "ALPHA FAILURE ROOT-CAUSE AUDIT" in text


def test_dashboard_panel_stays_compact_and_collapsed():
    html = (ROOT / "dashboards/research_command_center/static/index.html").read_text(encoding="utf-8")
    panel = html.split("function alphaRootCausePanel", 1)[1].split("function researchOperatingPolicyPanel", 1)[0]

    assert "<details" in panel
    assert "<summary>" in panel
    # Compact: no full cohort/ablation tables rendered inline on the home page.
    assert "factor_ablation" not in panel
    assert "entry_timing_audit" not in panel


def test_dashboard_reads_compact_root_cause_only(tmp_path):
    _write_json(tmp_path / rca.OUT_JSON_REL, {
        "generated_at": "2025-04-01T00:00:00+00:00",
        "alpha_proven": False,
        "overall_forward_verdict": "NO_FORWARD_EDGE",
        "dashboard_summary": {"current_recommendation": "FREEZE"},
        "guardrails": {"research_only": True},
        "root_cause_findings": {"huge": "detail" * 1000},
        "research_only": True,
    })

    payload = build_alpha_root_cause(ArtifactStore(root=tmp_path), compact=True)

    assert payload["dashboard_summary"]["current_recommendation"] == "FREEZE"
    assert "root_cause_findings" not in payload


def test_journal_digest_includes_alpha_root_cause_section():
    inputs = {
        "alpha_root_cause": {
            "dashboard_summary": {
                "top_likely_failure_cause": "remove_extended",
                "best_surviving_cohort": "Mid Cap",
                "worst_harmful_cohort": "Repeat candidates",
                "next_decision_date": "2026-08-17",
                "current_recommendation": "CONTINUE",
            }
        }
    }

    text = "\n".join(jd._section_alpha_root_cause(inputs))

    assert "## Alpha Root-Cause" in text
    assert "remove_extended" in text
    assert "2026-08-17" in text
    assert "CONTINUE" in text


def test_journal_digest_handles_missing_alpha_root_cause():
    text = "\n".join(jd._section_alpha_root_cause({}))
    assert "## Alpha Root-Cause" in text
    assert "missing" in text.lower()


def test_no_scanner_scoring_routing_filter_logic_changed_by_root_cause_module():
    for rel in (
        "research/research_scanner.py",
        "research/research_scoring.py",
        "research/research_programs.py",
        "research/latest_scan_programs.py",
        "research/high_conviction_alpha.py",
        "research/emerging_outlier_watch.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "alpha_failure_root_cause" not in text


# ── decision-date selection (2026-09-11: passed dates were shown as "next") ──


@pytest.mark.parametrize("today, expected", [
    ("2026-08-10", "2026-08-17"),
    ("2026-08-17", "2026-08-17"),  # current through its own day
    ("2026-08-18", "2026-09-07"),
    ("2026-09-11", "2026-09-30"),
    ("2026-09-30", "2026-09-30"),
    ("2026-10-01", None),          # never falls back to a passed date
])
def test_next_decision_date_skips_passed_dates(today, expected):
    assert rca.select_next_decision_date(today) == expected


def test_decision_dates_follow_the_et_session_not_utc():
    from datetime import datetime, timezone
    # the nightly for the 2026-09-30 session runs 00:40 UTC on 10-01
    nightly = datetime(2026, 10, 1, 0, 40, tzinfo=timezone.utc)
    assert rca.decision_as_of_date(nightly) == "2026-09-30"
    assert rca.select_next_decision_date(
        rca.decision_as_of_date(nightly)) == "2026-09-30"


def test_report_names_the_next_valid_date_and_the_sunset_gate(tmp_path):
    from datetime import datetime, timezone
    _fixture(tmp_path)
    report = rca.build_report(
        tmp_path, now=datetime(2026, 9, 11, 0, 41, tzinfo=timezone.utc))

    dash = report["dashboard_summary"]
    assert dash["next_decision_date"] == "2026-09-30"
    assert "SUNSET" in dash["next_decision_gate"]
    assert dash["passed_decision_dates"] == ["2026-08-17", "2026-09-07"]
    statuses = {d["date"]: d["status"]
                for d in report["stop_continue_framework"]["decision_dates"]}
    assert statuses == {"2026-08-17": "PASSED", "2026-09-07": "PASSED",
                        "2026-09-30": "NEXT"}

    text = rca.render_text(report)
    assert "Next decision date: 2026-09-30" in text
    assert "Next decision date: 2026-08-17" not in text
    assert "| 2026-08-17 | PASSED |" in rca.render_markdown(report)


def test_report_after_the_final_gate_names_no_date(tmp_path):
    from datetime import datetime, timezone
    _fixture(tmp_path)
    report = rca.build_report(
        tmp_path, now=datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc))
    dash = report["dashboard_summary"]
    assert dash["next_decision_date"] is None
    assert dash["next_decision_gate"] == rca.NO_DATES_REMAINING_LABEL
    assert dash["passed_decision_dates"] == list(rca.DECISION_DATES)


def _decision_line(inputs) -> str:
    return next(line for line in jd._section_alpha_root_cause(inputs)
                if "Next decision date" in line)


def test_digest_never_shows_a_passed_decision_date_as_current():
    # a sidecar written before the fix still names the first date
    line = _decision_line({
        "as_of_date": "2026-09-11",
        "alpha_root_cause": {"dashboard_summary": {
            "next_decision_date": "2026-08-17",
            "current_recommendation": "CONTINUE"}}})
    assert not line.startswith("- Next decision date: 2026-08-17")
    assert "has passed" in line


def test_digest_shows_the_final_sunset_gate():
    line = _decision_line({
        "as_of_date": "2026-09-11",
        "alpha_root_cause": {"dashboard_summary": {
            "next_decision_date": "2026-09-30",
            "next_decision_gate": rca.FINAL_GATE_LABEL,
            "current_recommendation": "CONTINUE"}}})
    assert line.startswith("- Next decision date: 2026-09-30")
    assert "SUNSET" in line


def test_digest_reports_when_no_decision_date_remains():
    line = _decision_line({
        "as_of_date": "2026-10-02",
        "alpha_root_cause": {"dashboard_summary": {
            "next_decision_date": None,
            "next_decision_gate": rca.NO_DATES_REMAINING_LABEL,
            "current_recommendation": "CONTINUE"}}})
    assert "none remaining" in line


def test_decision_labels_carry_no_trade_or_rank_language():
    import re
    lang = re.compile(r"\bbuy\b|\bsell\b|top pick|price target|"
                      r"\brank(s|ed|ing)?\b|position size", re.IGNORECASE)
    for label in (rca.FINAL_GATE_LABEL, rca.INTERIM_GATE_LABEL,
                  rca.NO_DATES_REMAINING_LABEL):
        assert not lang.search(label)
