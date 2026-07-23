"""Evidence/cadence integrity repairs.

These tests cover additive reporting only. They guard against stale downstream
artifacts being shown as current, weak forward-resolution horizons being
treated as clean, and diagnostic code leaking into scanner/scoring/routing.
"""
from __future__ import annotations

import json
from pathlib import Path

from dashboards.research_command_center import journal_digest as jd
from dashboards.research_command_center.data_adapter import (
    ArtifactStore,
    build_emerging_outlier,
    build_forward_cohorts,
    build_high_conviction,
    build_research_confidence,
)
from research import options_coverage_report as ocr
from research import research_watchlist_forward_tracker as fwd
from research import scan_exclusion_impact_report as sei

ROOT = Path(__file__).resolve().parents[2]


def _research_dir(root: Path) -> Path:
    path = root / "cache" / "research"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_scan_sources(root: Path, *, scanner_ts: str, routing_ts: str) -> None:
    research = _research_dir(root)
    _write_json(research / "research_scanner_latest.json", {
        "generated_at": scanner_ts,
        "scan_universe_manifest_hash": "scan-hash-1",
        "watchlist": [],
    })
    _write_json(research / "latest_scan_programs_latest.json", {
        "kind": "latest_scan_programs",
        "present": True,
        "generated_at": routing_ts,
        "scan_timestamp": scanner_ts,
        "scanner_manifest_hash": "scan-hash-1",
        "programs": {},
    })


def test_hc_artifact_older_than_latest_scan_marks_stale(tmp_path):
    _write_scan_sources(
        tmp_path,
        scanner_ts="2026-07-23T12:00:00+00:00",
        routing_ts="2026-07-23T12:02:00+00:00",
    )
    _write_json(tmp_path / "cache" / "research" / "high_conviction_alpha_latest.json", {
        "kind": "high_conviction_alpha",
        "present": True,
        "generated_at": "2026-07-23T00:40:00+00:00",
        "research_only": True,
        "shortlist": [],
        "counts": {},
    })

    payload = build_high_conviction(ArtifactStore(root=tmp_path))

    assert payload["stale_for_latest_scan"] is True
    assert payload["cadence_status"] == "STALE_FOR_LATEST_SCAN"
    assert payload["cadence_warning"] == "High-Conviction list stale vs latest scan"


def test_hc_artifact_newer_than_latest_scan_is_current(tmp_path):
    _write_scan_sources(
        tmp_path,
        scanner_ts="2026-07-23T12:00:00+00:00",
        routing_ts="2026-07-23T12:02:00+00:00",
    )
    _write_json(tmp_path / "cache" / "research" / "high_conviction_alpha_latest.json", {
        "kind": "high_conviction_alpha",
        "present": True,
        "generated_at": "2026-07-23T12:03:00+00:00",
        "research_only": True,
        "shortlist": [],
        "counts": {},
    })

    payload = build_high_conviction(ArtifactStore(root=tmp_path))

    assert payload["stale_for_latest_scan"] is False
    assert payload["cadence_status"] == "CURRENT_FOR_LATEST_SCAN"
    assert payload["cadence_warning"] is None


def test_eo_artifact_older_than_latest_scan_marks_stale(tmp_path):
    _write_scan_sources(
        tmp_path,
        scanner_ts="2026-07-23T12:00:00+00:00",
        routing_ts="2026-07-23T12:02:00+00:00",
    )
    _write_json(tmp_path / "cache" / "research" / "emerging_outlier_watch_latest.json", {
        "kind": "emerging_outlier_watch",
        "present": True,
        "generated_at": "2026-07-23T00:41:00+00:00",
        "research_only": True,
        "watch": [],
        "counts": {},
    })

    payload = build_emerging_outlier(ArtifactStore(root=tmp_path))

    assert payload["stale_for_latest_scan"] is True
    assert payload["cadence_status"] == "STALE_FOR_LATEST_SCAN"
    assert payload["cadence_warning"] == "Emerging Outlier list stale vs latest scan"


def test_forward_horizon_below_resolution_threshold_warns(monkeypatch):
    spy = [(f"2026-01-{day:02d}", 100.0 + day) for day in range(1, 15)]
    history = {
        "GOOD|2026-01-01": {
            "ticker": "GOOD",
            "appearance_date": "2026-01-01",
            "ret_5d": 1.2,
            "market_cap": 5_000_000_000,
            "sector": "Technology",
        },
        "MISSW|2026-01-01": {
            "ticker": "MISSW",
            "appearance_date": "2026-01-01",
            "ret_5d": None,
            "market_cap": 50_000_000,
            "sector": "Health Care",
        },
    }

    empty_cache = {
        "closes": [], "cache_source": "none", "bar_count": 0,
        "first_date": None, "last_date": None,
        "shallow_last_date": None, "deep_last_date": None,
    }
    report = fwd._resolution_coverage(
        history, spy, price_cache_loader=lambda ticker: empty_cache)
    h5 = report["5d"]

    assert h5["coverage_pct"] == 50.0
    assert h5["resolution_status"] == "WARN"
    assert h5["warning"]
    assert h5["unresolved_tickers"] == ["MISSW"]
    detail = h5["unresolved_detail"][0]
    assert detail["reason"] == "price_parquet_missing_or_empty"
    assert detail["reason_category"] == "warrant / unit / non-common security"
    assert detail["repairability"] == "NON_COMMON_SECURITY"
    assert detail["security_type"] == "POSSIBLE_WARRANT"
    assert detail["market_cap_bucket"] == "MICRO_CAP"
    assert detail["source_artifact"] == "data/research/research_watchlist_history.jsonl"


def test_scan_excluded_names_preserved_in_separate_report(tmp_path):
    research = _research_dir(tmp_path)
    _write_json(research / "scan_universe_manifest_latest.json", {
        "generated_at": "2026-07-23T12:00:00+00:00",
        "required_market_session": "2026-07-22",
        "scan_readiness": "DEGRADED",
        "readiness_reasons": ["1/1000 excluded after delta refresh"],
        "universe_hash": "hash-1",
        "excluded_by_reason": {"no_bar_for_session": ["MISSW"]},
    })
    _write_json(research / "research_universe_build_latest.json", {
        "universe_full": [{"ticker": "MISSW", "source": "ranked_fill"}],
    })
    _write_json(research / "research_scanner_latest.json", {"watchlist": []})
    _write_json(research / "latest_scan_programs_latest.json", {
        "present": True,
        "programs": {"TACTICAL": {"candidates": []}},
    })

    report = sei.build_report(tmp_path)

    assert report["excluded_tickers"] == ["MISSW"]
    rec = report["excluded"][0]
    assert rec["exclusion_reason"] == "no_bar_for_session"
    assert rec["universe_source"] == "ranked_fill"
    assert rec["present_downstream"]["scanner_watchlist"] is False
    assert rec["pipeline_impact"] == "DATA_VALIDITY_EXCLUDED_BEFORE_SCANNER_SCORING"
    assert rec["would_have_entered_pipeline_assessment"] == (
        "NOT_MEASURABLE_WITHOUT_RERUNNING_SCANNER_LOGIC"
    )
    assert report["guardrails"]["no_selection_change"] is True


def test_options_disabled_text_states_not_used_in_selection(tmp_path):
    research = _research_dir(tmp_path)
    _write_json(research / "research_scanner_latest.json", {
        "watchlist": [{"ticker": "AAPL"}, {"ticker": "SDGR"}, {"ticker": "BLMN"}],
    })
    snap = tmp_path / "data" / "options_snapshots" / "2026-07-09"
    snap.mkdir(parents=True)
    (snap / "AAPL.parquet").write_bytes(b"x")

    report = ocr.build_report(tmp_path)

    assert report["overlay_state"] == "DISABLED"
    assert report["selection_status"] == "NOT_USED_IN_SELECTION"
    assert report["used_in_selection"] is False
    assert report["selection_note"] == (
        "Options overlay disabled — insufficient coverage; not used in selection."
    )


def test_command_center_sources_remain_cache_only():
    for rel in (
        "dashboards/research_command_center/data_adapter.py",
        "dashboards/research_command_center/server.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for forbidden in ("get_fmp(", "get_alpaca(", "requests.", "httpx.",
                          "options_feed_factory", "import core.config"):
            assert forbidden not in text, f"{forbidden} leaked into {rel}"


def test_integrity_repairs_do_not_touch_scanner_scoring_routing_filters():
    for rel in (
        "research/research_scanner.py",
        "research/research_scoring.py",
        "research/research_programs.py",
        "research/high_conviction_alpha.py",
        "research/emerging_outlier_watch.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "scan_exclusion_impact_report" not in text
    for rel in (
        "research/research_scanner.py",
        "research/research_scoring.py",
        "research/research_programs.py",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "artifact_dependency_audit" not in text


def _price_cache(closes):
    return {
        "closes": closes,
        "cache_source": "test",
        "bar_count": len(closes),
        "first_date": closes[0][0] if closes else None,
        "last_date": closes[-1][0] if closes else None,
        "shallow_last_date": closes[-1][0] if closes else None,
        "deep_last_date": None,
    }


def test_unresolved_mature_episodes_are_classified_by_reason_category():
    spy = [(f"2026-01-{day:02d}", 100.0 + day) for day in range(1, 26)]
    history = {
        "WARRW|2026-01-01": {"ticker": "WARRW", "appearance_date": "2026-01-01", "ret_10d": None},
        "MAP.B|2026-01-01": {"ticker": "MAP.B", "appearance_date": "2026-01-01", "ret_10d": None},
        "DEADQ|2026-01-01": {"ticker": "DEADQ", "appearance_date": "2026-01-01", "ret_10d": None},
        "GAP|2026-01-01": {"ticker": "GAP", "appearance_date": "2026-01-01", "ret_10d": None},
        "STALE|2026-01-01": {"ticker": "STALE", "appearance_date": "2026-01-01", "ret_10d": None},
        "UNKNOWNX|2026-01-01": {"ticker": "UNKNOWNX", "appearance_date": "2026-01-01", "ret_10d": None},
    }

    def loader(ticker):
        if ticker == "STALE":
            return _price_cache([("2026-01-01", 10.0), ("2026-01-02", 10.5)])
        if ticker == "UNKNOWNX":
            return _price_cache([(f"2026-01-{day:02d}", 10.0 + day) for day in range(1, 20)])
        return _price_cache([])

    report = fwd._resolution_coverage(history, spy, price_cache_loader=loader)
    cats = report["10d"]["reason_category_counts"]
    repairs = report["10d"]["repairability_counts"]

    assert cats["warrant / unit / non-common security"] == 1
    assert cats["ticker mapping issue"] == 1
    assert cats["delisted / corporate action"] == 1
    assert cats["provider gap"] == 1
    assert cats["stale price history"] == 1
    assert cats["unresolved unknown"] == 1
    assert repairs["NON_COMMON_SECURITY"] == 1
    assert repairs["NEEDS_MAPPING"] == 1
    assert repairs["INVALID_OR_DELISTED"] == 1
    assert repairs["PROVIDER_GAP"] == 1
    assert repairs["REPAIRABLE"] == 1
    assert repairs["UNKNOWN"] == 1
    detail = report["10d"]["unresolved_detail"][0]
    assert {"first_seen_date", "expected_maturity_date",
            "last_available_price_date", "source_artifact",
            "reason_category", "repairability"} <= set(detail)


def test_non_common_security_adjusted_evidence_is_separate():
    spy = [(f"2026-01-{day:02d}", 100.0 + day) for day in range(1, 20)]
    entries = [
        {"ticker": "COMMON", "appearance_date": "2026-01-01", "ret_10d": 2.0},
        {"ticker": "WARRW", "appearance_date": "2026-01-01", "ret_10d": 40.0},
    ]
    views = fwd._evidence_views(entries, spy)

    raw = views["raw_evidence"]["horizons"]["10d"]
    common = views["non_common_security_adjusted_evidence"]["horizons"]["10d"]
    assert raw["resolved_count"] == 2
    assert raw["mean_return_pct"] == 21.0
    assert common["resolved_count"] == 1
    assert common["mean_return_pct"] == 2.0
    assert views["non_common_security_adjusted_evidence"]["excluded_unique_tickers"] == ["WARRW"]


def test_repairable_missing_bars_are_planned_through_existing_backfill(monkeypatch):
    from research import targeted_price_backfill as tpb

    called = {}

    def fake_build_plan(**kwargs):
        called.update(kwargs)
        return {
            "selected_for_backfill": 1,
            "provider_calls_planned": 1,
            "provider_calls_used": 0,
            "successes": 0,
            "failures": 0,
            "entries": [{"ticker": "REPA", "action": "backfill"}],
        }

    monkeypatch.setattr(tpb, "build_plan", fake_build_plan)
    coverage = {
        "10d": {"unresolved_detail": [
            {"ticker": "REPA", "repairability": "REPAIRABLE"},
            {"ticker": "WARRW", "repairability": "NON_COMMON_SECURITY"},
        ]}
    }

    plan = fwd._build_forward_backfill_plan(coverage, execute=False)

    assert called["tickers"] == ["REPA"]
    assert called["force_refresh"] is True
    assert plan["mode"] == "DRY_RUN_PLAN"
    assert plan["source"] == "research.targeted_price_backfill.build_plan"


def test_no_prices_are_fabricated_or_forward_filled_in_primary_evidence():
    spy = [(f"2026-01-{day:02d}", 100.0 + day) for day in range(1, 20)]
    entries = [
        {"ticker": "GOOD", "appearance_date": "2026-01-01", "ret_10d": 1.0},
        {"ticker": "MISS", "appearance_date": "2026-01-01", "ret_10d": None},
    ]
    views = fwd._evidence_views(entries, spy)
    raw = views["raw_evidence"]["horizons"]["10d"]
    sens = views["unresolved_impact_sensitivity"]["10d"]

    assert raw["resolved_count"] == 1
    assert raw["unresolved_count"] == 1
    assert raw["mean_return_pct"] == 1.0
    assert raw["unresolved_not_filled"] is True
    assert sens["mean_return_sensitivity"] == "not_estimated_without_prices"
    assert views["guardrails"]["no_fabricated_prices"] is True
    assert views["guardrails"]["no_forward_fill_primary_evidence"] is True


def test_dashboard_surfaces_incomplete_forward_evidence_warning(tmp_path):
    research = _research_dir(tmp_path)
    warning = "Forward evidence incomplete — 20d resolution 67.0%; unresolved cohort may bias results."
    _write_json(research / "research_forward_latest.json", {
        "generated_at": "2026-07-23T14:00:00+00:00",
        "research_only": True,
        "verdicts_by_label": [],
        "resolution_coverage": {
            "20d": {
                "calendar_mature": 458,
                "expected_mature_count": 458,
                "resolved": 307,
                "resolved_count": 307,
                "unresolved": 151,
                "unresolved_count": 151,
                "coverage_pct": 67.0,
                "resolution_pct": 67.0,
                "resolution_status": "WARN",
                "incomplete_evidence_warning": warning,
            }
        },
        "resolution_warnings": ["20d weak"],
        "incomplete_evidence_warnings": [warning],
        "incomplete_evidence_warning": warning,
    })
    _write_json(research / "research_program_validation_latest.json", {
        "present": True,
        "programs": {},
    })

    cohorts = build_forward_cohorts(ArtifactStore(root=tmp_path))
    confidence = build_research_confidence(ArtifactStore(root=tmp_path))

    assert warning in cohorts["incomplete_evidence_warnings"]
    assert confidence["forward_tracking"]["incomplete_evidence_warning"] == warning
    html = (ROOT / "dashboards" / "research_command_center" / "static" / "index.html").read_text(encoding="utf-8")
    assert "incomplete_evidence_warning" in html

def test_journal_surfaces_incomplete_forward_evidence_warning():
    warning = "Forward evidence incomplete — 20d resolution 67.0%; unresolved cohort may bias results."
    inputs = {
        "status": {
            "total_tracked": 10,
            "matured_5d": 8,
            "matured_10d": 6,
            "benchmark_readiness": "READY",
            "tracker_verdict": "MIXED",
            "phase_4b": {"status": "WATCH", "reason": "n/a"},
        },
        "truth": {},
        "forward": {
            "overall": {
                "sample_status": "ROBUST",
                "matured_by_horizon": {"20d": 3, "45d": 0, "60d": 0},
            },
            "incomplete_evidence_warnings": [warning],
            "incomplete_evidence_warning": warning,
        },
        "store": None,
        "summary": {},
        "scanner": {},
        "options_coverage": {},
        "forward_milestones": None,
    }

    forward_section = "\n".join(jd._section_forward(inputs))
    final_section = "\n".join(
        jd._section_final_finding(inputs, "READY", [], [], [])
    )

    assert warning in forward_section
    assert "forward evidence is incomplete" in final_section
    assert "forward evidence supports the current research board" not in final_section
