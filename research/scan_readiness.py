#!/usr/bin/env python3
"""
research/scan_readiness.py — deterministic scan-readiness policy (P0 follow-up).

Single source of truth for READY / DEGRADED / BLOCKED so the manifest
builder, the scanner's scan_integrity block, and every surface apply the
same rule.  Thresholds are configured constants, not hidden assumptions;
they gate *data validity reporting only* and never touch candidate
selection rules.

  READY    — the final validated scan universe is fully same-session
             aligned.  Symbols removed BEFORE the manifest was built under
             approved policies (confirmed-dead tombstones, invalid/
             unsupported securities) do not count against READY.
  DEGRADED — benchmarks aligned, but a small bounded fraction (at most
             MAX_DEGRADED_EXCLUDED_PCT of the universe) remained
             unavailable after the delta refresh and was excluded.
  BLOCKED  — any required benchmark (SPY/QQQ/IWM) is off the required
             session, or the excluded fraction exceeds the bound (aligned
             coverage below MIN_COVERAGE_PCT) — mixed-session data could
             dominate, so the scan must not be trusted.
"""
from __future__ import annotations

from typing import List, Tuple

# Excluded fraction (%) tolerated before DEGRADED escalates to BLOCKED.
MAX_DEGRADED_EXCLUDED_PCT = 5.0
# Equivalent aligned-coverage floor, kept explicit for artifact readability.
MIN_COVERAGE_PCT = 100.0 - MAX_DEGRADED_EXCLUDED_PCT

REQUIRED_BENCHMARKS = ("SPY", "QQQ", "IWM")


def readiness_verdict(
    universe_size: int,
    excluded_count: int,
    benchmarks_aligned: bool,
) -> Tuple[str, List[str]]:
    """Return (readiness, reasons) under the policy above."""
    reasons: List[str] = []
    if universe_size <= 0:
        return "BLOCKED", ["empty scan universe"]
    coverage = 100.0 * (universe_size - excluded_count) / universe_size
    if not benchmarks_aligned:
        return "BLOCKED", ["required benchmark off the required session"]
    if coverage < MIN_COVERAGE_PCT:
        return "BLOCKED", [
            f"aligned coverage {coverage:.1f}% below the "
            f"{MIN_COVERAGE_PCT:.0f}% floor "
            f"({excluded_count}/{universe_size} excluded)"]
    if excluded_count > 0:
        return "DEGRADED", [
            f"{excluded_count}/{universe_size} excluded after delta refresh "
            f"(coverage {coverage:.1f}%, bound "
            f"{MAX_DEGRADED_EXCLUDED_PCT:.0f}%)"]
    return "READY", ["benchmarks aligned; full same-session coverage"]
