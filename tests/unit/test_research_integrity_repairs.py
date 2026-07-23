"""Evidence/cadence integrity repairs.

These tests cover additive reporting only. They guard against stale downstream
artifacts being shown as current, weak forward-resolution horizons being
treated as clean, and diagnostic code leaking into scanner/scoring/routing.
"""
from __future__ import annotations

import json
from pathlib import Path

from dashboards.research_command_center.data_adapter import (
    ArtifactStore,
    build_emerging_outlier,
    build_high_conviction,
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

    monkeypatch.setattr(fwd, "_load_closes_with_dates", lambda ticker: [])
    report = fwd._resolution_coverage(history, spy)
    h5 = report["5d"]

    assert h5["coverage_pct"] == 50.0
    assert h5["resolution_status"] == "WARN"
    assert h5["warning"]
    assert h5["unresolved_tickers"] == ["MISSW"]
    detail = h5["unresolved_detail"][0]
    assert detail["reason"] == "price_parquet_missing_or_empty"
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
