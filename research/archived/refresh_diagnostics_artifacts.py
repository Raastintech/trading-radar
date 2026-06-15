#!/usr/bin/env python3
"""
refresh_diagnostics_artifacts.py

Daily artifact refresh for operator diagnostics:
  - reports/scan_quality_latest.json
  - reports/rr_shadow_latest.json

Runs scan diagnostics in analysis mode only (no execution changes).
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timezone
from typing import Optional


SCAN_REPORT_PATH = os.path.join("reports", "scan_quality_latest.json")
SHADOW_REPORT_PATH = os.path.join("reports", "rr_shadow_latest.json")


def _parse_iso_date(ts: str) -> Optional[date]:
    if not ts:
        return None
    try:
        normalized = str(ts).strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        return datetime.fromisoformat(normalized).date()
    except Exception:
        return None


def _artifact_day(path: str) -> Optional[date]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        artifact_day = _parse_iso_date(str(payload.get("generated_at") or ""))
        if artifact_day:
            return artifact_day
    except Exception:
        pass

    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).date()
    except Exception:
        return None


def _artifacts_fresh_today() -> bool:
    today_utc = datetime.now(timezone.utc).date()
    scan_day = _artifact_day(SCAN_REPORT_PATH)
    shadow_day = _artifact_day(SHADOW_REPORT_PATH)
    return scan_day == today_utc and shadow_day == today_utc


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh daily diagnostics artifacts.")
    parser.add_argument("--db", default="trading_performance.db", help="SQLite DB path")
    parser.add_argument("--days", type=int, default=7, choices=[1, 3, 7, 14, 30])
    parser.add_argument("--min-watchlist", type=int, default=5, dest="min_watchlist")
    parser.add_argument("--force", action="store_true", help="Refresh even if artifacts are from today")
    args = parser.parse_args()

    if not args.force and _artifacts_fresh_today():
        print("[DIAGNOSTICS] Artifacts already refreshed today (UTC) — skipping.")
        return 0

    try:
        from scan_diagnostics import ScanDiagnosticsEngine
    except Exception as exc:
        print(f"[DIAGNOSTICS] ERROR: cannot import scan_diagnostics: {exc}")
        return 1

    try:
        engine = ScanDiagnosticsEngine(db_path=args.db)
        report = engine.run_diagnostics(
            days=args.days,
            mode=None,
            min_watchlist=args.min_watchlist,
            with_rr_shadow=True,
            with_shadow_outcomes=True,
            with_pilot=True,
            with_rr_gap=True,
        )
    except Exception as exc:
        print(f"[DIAGNOSTICS] ERROR: refresh failed: {exc}")
        return 1

    if not os.path.exists(SCAN_REPORT_PATH):
        print(f"[DIAGNOSTICS] ERROR: missing artifact {SCAN_REPORT_PATH}")
        return 1
    if not os.path.exists(SHADOW_REPORT_PATH):
        print(f"[DIAGNOSTICS] ERROR: missing artifact {SHADOW_REPORT_PATH}")
        return 1

    print(
        "[DIAGNOSTICS] Refreshed artifacts: "
        f"scan_quality_latest.json + rr_shadow_latest.json "
        f"(generated_at={report.get('generated_at')})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
