"""
core/startup_checks.py — Research-stack startup self-test.

Phase 3A (2026-06-13): Converted from trading-daemon checks to research-stack
checks. Alpaca auth/bar checks removed (broker disabled). FMP is now the
critical data provider; Tradier handles options research.

Critical checks (HALT on failure):
  - FMP auth (primary research data provider)
  - Database writable
  - Timezone (zoneinfo) available

Non-critical checks (DEGRADED on failure, research continues):
  - Price cache presence (cache/prices/*.parquet)
  - FMP economic calendar
  - Cache directory writable
  - Tradier configured (options data)

Usage:
    from core.startup_checks import run_startup_checks, StartupState
    state = run_startup_checks()
    if state.halted:
        sys.exit(1)
    if state.degraded:
        logger.warning("Running in degraded mode: %s", state.degraded_reasons)
"""
from __future__ import annotations
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import core.config as cfg

logger = logging.getLogger(__name__)


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    passed: bool
    critical: bool
    message: str
    elapsed_ms: float = 0.0

    def __str__(self) -> str:
        icon = "✓" if self.passed else ("✗" if self.critical else "⚠")
        tag  = "PASS" if self.passed else ("HALT" if self.critical else "DEGRADED")
        return f"  [{tag:8s}] {icon} {self.name}: {self.message} ({self.elapsed_ms:.0f}ms)"


@dataclass
class StartupState:
    results: List[CheckResult] = field(default_factory=list)

    @property
    def halted(self) -> bool:
        return any(not r.passed and r.critical for r in self.results)

    @property
    def degraded(self) -> bool:
        return not self.halted and any(not r.passed for r in self.results)

    @property
    def degraded_reasons(self) -> List[str]:
        return [r.name for r in self.results if not r.passed and not r.critical]

    def log_summary(self) -> None:
        status = "HALTED" if self.halted else ("DEGRADED" if self.degraded else "OK")
        logger.info("=== Research startup checks: %s ===", status)
        for r in self.results:
            fn = logger.error if (not r.passed and r.critical) else (
                 logger.warning if not r.passed else logger.info)
            fn(str(r))
        logger.info("=" * 40)


# ── Individual checks ─────────────────────────────────────────────────────────

def _check(name: str, critical: bool, fn) -> CheckResult:
    t0 = time.monotonic()
    try:
        fn()
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(name=name, passed=True, critical=critical,
                           message="ok", elapsed_ms=elapsed)
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        return CheckResult(name=name, passed=False, critical=critical,
                           message=str(exc)[:120], elapsed_ms=elapsed)


def _check_fmp_auth() -> None:
    from core.fmp_client import get_fmp
    vix = get_fmp().get_vix()
    if vix is None or vix <= 0:
        raise RuntimeError(f"VIX returned {vix!r} — check FMP_API_KEY")


def _check_price_cache() -> None:
    cache_dir = Path(cfg.CACHE_DIR) / "prices"
    if not cache_dir.exists():
        raise RuntimeError(
            "cache/prices/ not found — run scripts/nightly_refresh.py"
        )
    parquets = list(cache_dir.glob("*.parquet"))
    if len(parquets) < 5:
        raise RuntimeError(
            f"only {len(parquets)} parquets in cache/prices/ — run nightly_refresh"
        )
    spy = cache_dir / "SPY.parquet"
    if not spy.exists():
        raise RuntimeError("SPY.parquet missing — run scripts/nightly_refresh.py")
    import pandas as pd
    df = pd.read_parquet(spy)
    if df.empty or len(df) < 5:
        raise RuntimeError(f"SPY cache has only {len(df)} bars — run nightly_refresh")


def _check_fmp_calendar() -> None:
    from core.fmp_client import get_fmp
    events = get_fmp().get_economic_calendar(days_ahead=7)
    if not isinstance(events, list):
        raise RuntimeError(
            f"economic calendar returned {type(events).__name__}, expected list"
        )


def _check_database() -> None:
    db_path = Path(cfg.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS _startup_probe (ts TEXT)")
    conn.execute("INSERT INTO _startup_probe VALUES (datetime('now'))")
    conn.commit()
    conn.close()


def _check_cache_dir() -> None:
    cache_dir = Path(cfg.CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    probe = cache_dir / "_startup_probe.tmp"
    probe.write_text("ok")
    probe.unlink()


def _check_timezone() -> None:
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone
    tz = ZoneInfo("America/New_York")
    _ = datetime.now(tz)


def _check_tradier_configured() -> None:
    token = os.getenv("TRADIER_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "TRADIER_API_TOKEN not set — options data unavailable"
        )


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_startup_checks() -> StartupState:
    """
    Run all research-stack startup checks and return a StartupState.
    Caller decides whether to halt or continue based on state.halted.
    """
    state = StartupState()

    # Critical checks — halt if any fail
    state.results.append(_check("timezone",    critical=True,  fn=_check_timezone))
    state.results.append(_check("database",    critical=True,  fn=_check_database))
    state.results.append(_check("fmp_auth",    critical=True,  fn=_check_fmp_auth))

    # Non-critical — degraded mode if fail, research continues
    state.results.append(_check("price_cache",   critical=False, fn=_check_price_cache))
    state.results.append(_check("fmp_calendar",  critical=False, fn=_check_fmp_calendar))
    state.results.append(_check("cache_dir",     critical=False, fn=_check_cache_dir))
    state.results.append(_check("tradier",       critical=False, fn=_check_tradier_configured))

    state.log_summary()
    return state
