"""
tests/unit/test_scan_integrity_surfacing.py

Command-center surfacing proofs for the 2026-07-10/11 pipeline repair:
a scan artifact must never be labeled FRESH on recency alone — the combined
label requires same-session market data (scan integrity READY).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import os
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")


def test_command_center_never_labels_mixed_session_scan_fresh(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        ArtifactStore, build_status)

    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True)
    now_iso = datetime.now(tz=ZoneInfo("UTC")).isoformat()
    (research / "research_scanner_latest.json").write_text(json.dumps({
        "generated_at": now_iso,     # artifact is brand new …
        "universe_size": 100,
        "scan_integrity": {          # … but the market data is mixed-session
            "readiness": "BLOCKED",
            "required_market_session": "2026-07-09",
            "benchmarks_aligned": False,
            "benchmark_session": {"SPY": "2026-07-08"},
            "session_mismatch_excluded": 0,
            "same_session_coverage_pct": 100.0,
            "readiness_reasons": ["benchmark session mismatch"],
        },
    }))
    status = build_status(ArtifactStore(root=tmp_path))
    assert status["artifact_freshness"] == "FRESH"      # recency axis
    assert status["data_freshness"] == "BLOCKED"        # combined label
    assert status["data_freshness"] != "FRESH"


def test_command_center_degraded_when_mismatches_excluded(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        ArtifactStore, build_status)

    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True)
    now_iso = datetime.now(tz=ZoneInfo("UTC")).isoformat()
    (research / "research_scanner_latest.json").write_text(json.dumps({
        "generated_at": now_iso,
        "universe_size": 100,
        "scan_integrity": {"readiness": "DEGRADED",
                           "required_market_session": "2026-07-09",
                           "benchmarks_aligned": True,
                           "benchmark_session": {},
                           "session_mismatch_excluded": 5,
                           "same_session_coverage_pct": 95.0,
                           "readiness_reasons": []},
    }))
    status = build_status(ArtifactStore(root=tmp_path))
    assert status["data_freshness"] == "DEGRADED"


def test_scan_integrity_model_reports_manifest_and_stale_sources(tmp_path):
    from dashboards.research_command_center.data_adapter import (
        ArtifactStore, build_scan_integrity)

    research = tmp_path / "cache" / "research"
    research.mkdir(parents=True)
    (research / "research_scanner_latest.json").write_text(json.dumps({
        "generated_at": "2026-07-11T00:00:00+00:00",
        "scan_universe_manifest_hash": "abc123",
        "scan_integrity": {"readiness": "READY",
                           "required_market_session": "2026-07-10",
                           "manifest_hash": "abc123"},
    }))
    (research / "scan_universe_manifest_latest.json").write_text(json.dumps({
        "scan_readiness": "READY", "universe_hash": "abc123",
        "delta_refresh_needed": 18, "delta_refresh_succeeded": 18,
        "aligned_final_universe_count": 985,
        "excluded_by_reason": {"no_bar_for_session": ["X"]},
    }))
    model = build_scan_integrity(ArtifactStore(root=tmp_path))
    assert model["manifest"]["hash"] == "abc123"
    assert model["manifest"]["delta_refresh_needed"] == 18
    assert model["manifest"]["excluded_by_reason"] == {"no_bar_for_session": 1}
    # absent legacy snapshot = nothing to leak, not stale
    legacy = model["stale_source_consumers"]["legacy_universe_snapshot"]
    assert legacy["stale"] is None
