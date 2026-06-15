#!/usr/bin/env python3
"""
startup_readiness_check.py

Fail-closed startup gate for the canonical V3 runtime. Verifies:
1) security baseline controls
2) required telemetry schema for decisions/trades
3) report artifact directory writability

Exit code:
  0 = ready
  1 = blocked
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from typing import Dict, List, Set, Tuple

from secure_env import load_runtime_env
from security_baseline import startup_security_report

load_runtime_env("startup_readiness_check")


def _flag(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def _load_columns(cur: sqlite3.Cursor, table: str) -> Set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _check_db_schema(db_path: str) -> Tuple[List[str], List[str]]:
    failures: List[str] = []
    warnings: List[str] = []
    if not os.path.exists(db_path):
        failures.append(f"Database not found: {db_path}")
        return failures, warnings

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}

    required_tables: Dict[str, Set[str]] = {
        "decisions": {
            "strategy",
            "direction",
            "shares",
            "regime_status",
            "regime_volatility",
            "regime_overall",
            "regime_vix",
            "options_pcr",
            "options_gamma",
        },
        "trades": {
            "strategy",
            "direction",
            "scanner_rr",
            "circuit_breaker_triggered",
            "trail_stop_activated",
        },
    }

    for table, needed in required_tables.items():
        if table not in tables:
            failures.append(f"Missing required table: {table}")
            continue
        cols = _load_columns(cur, table)
        missing = sorted(needed - cols)
        if missing:
            failures.append(f"{table} missing columns: {', '.join(missing)}")

    # Informational quality checks (non-blocking): existing historical rows may predate schema.
    try:
        cur.execute(
            """
            SELECT
              COUNT(*) AS n,
              SUM(CASE WHEN regime_overall IS NULL OR regime_overall='' THEN 1 ELSE 0 END) AS missing_overall,
              SUM(CASE WHEN regime_vix IS NULL OR regime_vix='' THEN 1 ELSE 0 END) AS missing_vix
            FROM decisions
            WHERE date(timestamp) >= date('now', '-1 day')
            """
        )
        row = cur.fetchone()
        if row and int(row[0] or 0) > 0:
            n = int(row[0] or 0)
            missing_o = int(row[1] or 0)
            missing_v = int(row[2] or 0)
            if missing_o > 0 or missing_v > 0:
                warnings.append(
                    f"Recent decision regime coverage: overall missing {missing_o}/{n}, vix missing {missing_v}/{n}"
                )
    except Exception:
        pass

    try:
        cur.execute(
            """
            SELECT
              COUNT(*) AS n,
              SUM(CASE WHEN scanner_rr IS NULL THEN 1 ELSE 0 END) AS missing_scanner_rr
            FROM trades
            WHERE date(entry_date) >= date('now', '-7 day')
            """
        )
        row = cur.fetchone()
        if row and int(row[0] or 0) > 0:
            n = int(row[0] or 0)
            missing = int(row[1] or 0)
            if missing > 0:
                warnings.append(f"Recent trades with NULL scanner_rr: {missing}/{n}")
    except Exception:
        pass

    conn.close()
    return failures, warnings


def _check_reports_writable(reports_dir: str = "reports") -> Tuple[bool, str]:
    try:
        os.makedirs(reports_dir, exist_ok=True)
        test_path = os.path.join(reports_dir, ".readiness_write_test")
        with open(test_path, "w", encoding="utf-8") as f:
            f.write("ok\n")
        os.remove(test_path)
        return True, f"{reports_dir} writable"
    except Exception as exc:
        return False, f"{reports_dir} not writable: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail-closed startup readiness gate.")
    parser.add_argument("--db", default="trading_performance.db", help="SQLite DB path")
    parser.add_argument(
        "--mode",
        default=os.getenv("TRADING_MODE", "PAPER").upper(),
        help="Execution mode for security checks (default from TRADING_MODE)",
    )
    args = parser.parse_args()

    mode = str(args.mode or "PAPER").upper()
    failures: List[str] = []
    warnings: List[str] = []

    sec = startup_security_report(
        component="startup_readiness_check",
        execution_mode=mode,
    )

    if not sec.get("keys_present", False):
        failures.append("Alpaca API keys missing from environment.")
    if not sec.get("env_file_secure", False):
        failures.append(str(sec.get("env_file_message") or ".env permissions not secure"))
    if mode == "LIVE" and not sec.get("live_armed", False):
        failures.append(str(sec.get("live_mode_message") or "LIVE mode not armed"))

    # Kill-switch is a valid operational state; never a startup failure.
    if sec.get("killswitch_active", False):
        warnings.append(str(sec.get("killswitch_reason") or "Entry kill-switch active"))
    if not sec.get("repo_env_ok", True):
        warnings.append(str(sec.get("repo_env_message") or "Repo-local .env detected"))

    schema_failures, schema_warnings = _check_db_schema(args.db)
    failures.extend(schema_failures)
    warnings.extend(schema_warnings)

    reports_ok, reports_msg = _check_reports_writable("reports")
    if not reports_ok:
        failures.append(reports_msg)

    print("\n" + "=" * 72)
    print("STARTUP READINESS CHECK")
    print("=" * 72)
    print(f"Mode: {mode}")
    print(f"Keys present: {_flag(bool(sec.get('keys_present')))}")
    print(f".env permissions: {_flag(bool(sec.get('env_file_secure')))} | {sec.get('env_file_message')}")
    print(f"Repo-local env: {'PASS' if sec.get('repo_env_ok') else 'WARN'} | {sec.get('repo_env_message')}")
    print(f"Live arming: {_flag(bool(sec.get('live_armed')))} | {sec.get('live_mode_message')}")
    print(
        f"Kill-switch: {'ACTIVE' if sec.get('killswitch_active') else 'INACTIVE'}"
        f" | {sec.get('killswitch_reason')}"
    )
    print(f"Reports dir: {_flag(reports_ok)} | {reports_msg}")

    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print(f" - {w}")
    if failures:
        print("\nBLOCKERS:")
        for f in failures:
            print(f" - {f}")
        print("=" * 72 + "\n")
        return 1

    print("\nReadiness gate: PASS")
    print("=" * 72 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
