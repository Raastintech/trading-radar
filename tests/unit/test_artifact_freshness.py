"""tests/unit/test_artifact_freshness.py — Phase 2B.2 freshness contract.

Pure-stdlib tests for ``core.artifact_freshness``.  Verifies the
threshold matrix, the GATEKEEPER earnings-day tightening, the MCP audit
extra-reason logic, and the earnings_status classifier.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.artifact_freshness import (
    FRESHNESS_THRESHOLDS,
    compute_freshness,
    earnings_status,
    freshness_for_path,
    is_earnings_day,
    mcp_extra_reasons,
)

HOUR = 3600


# ── compute_freshness ────────────────────────────────────────────────────────

def test_gatekeeper_normal_threshold_24h():
    v = compute_freshness(kind="GATEKEEPER", age_seconds=10 * HOUR)
    assert v["stale"] is False
    assert v["warn"] is False
    assert v["threshold_label"] == "normal"
    assert v["stale_threshold"] == 24 * HOUR


def test_gatekeeper_normal_stale_after_24h():
    v = compute_freshness(kind="GATEKEEPER", age_seconds=25 * HOUR)
    assert v["stale"] is True
    assert "age>24h" in v["stale_reasons"]


def test_gatekeeper_earnings_day_tightens_to_6h():
    v = compute_freshness(
        kind="GATEKEEPER",
        age_seconds=7 * HOUR,
        is_earnings_day=True,
    )
    assert v["stale"] is True
    assert v["threshold_label"] == "earnings"
    assert v["stale_threshold"] == 6 * HOUR
    assert "age>6h" in v["stale_reasons"]


def test_gatekeeper_earnings_fresh_under_6h():
    v = compute_freshness(
        kind="GATEKEEPER",
        age_seconds=2 * HOUR,
        is_earnings_day=True,
    )
    assert v["stale"] is False
    assert v["threshold_label"] == "earnings"


def test_gatekeeper_intraday_warn_at_4h():
    v = compute_freshness(
        kind="GATEKEEPER",
        age_seconds=5 * HOUR,
        is_intraday_selected=True,
    )
    # 5h is above the 4h warn but well under the 24h stale.
    assert v["stale"] is False
    assert v["warn"] is True
    assert v["threshold_label"] == "intraday"
    assert "age>4h" in v["stale_reasons"]


def test_earnings_overrides_intraday():
    v = compute_freshness(
        kind="GATEKEEPER",
        age_seconds=7 * HOUR,
        is_earnings_day=True,
        is_intraday_selected=True,
    )
    # Earnings precedence: 6h stale wins, even with intraday flag set.
    assert v["stale"] is True
    assert v["threshold_label"] == "earnings"


def test_missing_artifact_marked_stale():
    v = compute_freshness(kind="GATEKEEPER", age_seconds=None)
    assert v["stale"] is True
    assert v["warn"] is True
    assert v["age_seconds"] is None
    assert "artifact_missing" in v["stale_reasons"]


def test_extra_reasons_force_stale():
    v = compute_freshness(
        kind="MCP_AUDIT",
        age_seconds=1 * HOUR,
        extra_reasons=["session_changed"],
    )
    # 1h is well within the 12h MCP threshold but the extra reason
    # forces the verdict stale anyway.
    assert v["stale"] is True
    assert "session_changed" in v["stale_reasons"]


def test_mcp_audit_normal_threshold_12h():
    v = compute_freshness(kind="MCP_AUDIT", age_seconds=11 * HOUR)
    assert v["stale"] is False
    v = compute_freshness(kind="MCP_AUDIT", age_seconds=13 * HOUR)
    assert v["stale"] is True


def test_thresholds_matrix_shape():
    # Lightweight invariant: every kind has a normal entry with both
    # stale and warn fields.
    for kind, table in FRESHNESS_THRESHOLDS.items():
        assert "normal" in table, kind
        assert "stale" in table["normal"]
        assert "warn" in table["normal"]


# ── freshness_for_path ───────────────────────────────────────────────────────

def test_freshness_for_path_missing(tmp_path):
    v = freshness_for_path(tmp_path / "nope.json", kind="GATEKEEPER")
    assert v["stale"] is True
    assert v["age_seconds"] is None


def test_freshness_for_path_existing(tmp_path):
    p = tmp_path / "art.json"
    p.write_text("{}")
    # Force a 2-hour-old mtime.
    old = time.time() - 2 * HOUR
    os.utime(p, (old, old))
    v = freshness_for_path(p, kind="GATEKEEPER")
    assert v["stale"] is False
    assert v["age_seconds"] is not None
    assert v["age_seconds"] >= 2 * HOUR - 60


# ── mcp_extra_reasons ───────────────────────────────────────────────────────

def test_mcp_extra_reason_session_changed():
    out = mcp_extra_reasons(
        sidecar_session="open",
        current_session="regular",
        sidecar_generated_at=None,
        forecast_built_at=None,
    )
    assert "session_changed" in out


def test_mcp_extra_reason_session_same():
    out = mcp_extra_reasons(
        sidecar_session="regular",
        current_session="regular",
        sidecar_generated_at=None,
        forecast_built_at=None,
    )
    assert "session_changed" not in out


def test_mcp_extra_reason_forecast_newer():
    out = mcp_extra_reasons(
        sidecar_session="regular",
        current_session="regular",
        sidecar_generated_at="2026-05-19T20:00:00+00:00",
        forecast_built_at="2026-05-20T08:00:00+00:00",
    )
    assert "forecast_newer_than_sidecar" in out


def test_mcp_extra_reason_forecast_older_no_signal():
    out = mcp_extra_reasons(
        sidecar_session="regular",
        current_session="regular",
        sidecar_generated_at="2026-05-20T08:00:00+00:00",
        forecast_built_at="2026-05-19T20:00:00+00:00",
    )
    assert "forecast_newer_than_sidecar" not in out


# ── earnings_status ─────────────────────────────────────────────────────────

@pytest.fixture
def today_iso():
    return datetime.now(timezone.utc).date().isoformat()


def _earn_row(symbol: str, days_offset: int, eps: float = 1.0) -> dict:
    d = datetime.now(timezone.utc).date() + timedelta(days=days_offset)
    return {"symbol": symbol, "date": d.isoformat(), "epsEstimated": eps}


def test_earnings_status_today(today_iso):
    rows = [_earn_row("NVDA", 0)]
    assert earnings_status("NVDA", rows, today_iso=today_iso) == "EARNINGS TODAY"


def test_earnings_status_tomorrow(today_iso):
    rows = [_earn_row("NVDA", 1)]
    assert earnings_status("NVDA", rows, today_iso=today_iso) == "EARNINGS TOMORROW"


def test_earnings_status_this_week(today_iso):
    rows = [_earn_row("NVDA", 3)]
    assert earnings_status("NVDA", rows, today_iso=today_iso) == "EARNINGS THIS WEEK"


def test_earnings_status_post_earnings(today_iso):
    rows = [_earn_row("NVDA", -1)]
    assert earnings_status("NVDA", rows, today_iso=today_iso) == "POST-EARNINGS"


def test_earnings_status_none(today_iso):
    rows = [_earn_row("NVDA", 10)]
    assert earnings_status("NVDA", rows, today_iso=today_iso) is None


def test_earnings_status_handles_no_rows(today_iso):
    assert earnings_status("NVDA", [], today_iso=today_iso) is None
    assert earnings_status("NVDA", None, today_iso=today_iso) is None


def test_is_earnings_day_strict(today_iso):
    rows = [_earn_row("NVDA", 1)]
    assert is_earnings_day("NVDA", rows, today_iso=today_iso) is False
    rows = [_earn_row("NVDA", 0)]
    assert is_earnings_day("NVDA", rows, today_iso=today_iso) is True


def test_earnings_status_picks_closest_future(today_iso):
    rows = [_earn_row("NVDA", 4), _earn_row("NVDA", 1)]
    assert earnings_status("NVDA", rows, today_iso=today_iso) == "EARNINGS TOMORROW"
