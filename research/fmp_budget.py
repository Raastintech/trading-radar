#!/usr/bin/env python3
"""
research/fmp_budget.py — shared FMP budget honesty + enforcement (P0 follow-up).

Every provider-calling research step (universe price refresh, scan-universe
manifest delta refresh, discovery bootstrap) reports the same budget picture
and enforces the same cap policy:

  * calls used today / month-to-date come from the Gatekeeper's telemetry
    tables (fmp_budget / fmp_budget_monthly).
  * the configured monthly limit is ``core.config.FMP_MONTHLY_BUDGET``;
    0 means the premium plan's cap is NOT confirmed — this is surfaced as a
    warning in every artifact, never silently treated as unlimited.
  * a benchmark reserve (RESERVED_BENCHMARK_CALLS) is always held back so
    SPY/QQQ/IWM + sector ETFs can refresh even when a confirmed cap is
    nearly exhausted.
  * when a confirmed cap would be exceeded, callers must truncate to
    ``allowed_calls`` and mark the run ``budget_limited`` — degrade
    explicitly, never mix sessions to save calls.

RESEARCH-ONLY: read-only on the DB; no signals, no execution.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import core.config as cfg

logger = logging.getLogger("fmp_budget")

# Calls always held back for benchmark + sector-ETF same-session refresh
# (3 benchmarks + 11 sector ETFs + headroom).
RESERVED_BENCHMARK_CALLS = 40


def _usage() -> Dict[str, Optional[int]]:
    try:
        from core.data_gatekeeper import get_gatekeeper
        gk = get_gatekeeper()
        return {"today": gk.budget_used_today(), "month": gk.budget_used_month()}
    except Exception as exc:  # offline / stub creds — telemetry unavailable
        logger.debug("budget usage unavailable: %s", exc)
        return {"today": None, "month": None}


def budget_report(planned_calls: int = 0, pipeline: str = "") -> Dict[str, Any]:
    """Budget picture + verdict for a run that wants ``planned_calls``.

    Keys: configured_monthly_limit, budget_cap_status, calls_used_today,
    calls_used_month, remaining_estimated, pct_consumed,
    reserved_benchmark_calls, planned_calls, allowed_calls, budget_limited,
    pipeline.
    """
    configured = int(getattr(cfg, "FMP_MONTHLY_BUDGET", 0) or 0)
    usage = _usage()
    month = usage["month"]

    remaining: Optional[int] = None
    pct: Optional[float] = None
    allowed = planned_calls
    limited = False
    if configured > 0:
        status = "configured"
        if month is not None:
            remaining = max(0, configured - month)
            pct = round(100.0 * month / configured, 1)
            usable = max(0, remaining - RESERVED_BENCHMARK_CALLS)
            if planned_calls > usable:
                allowed = usable
                limited = True
    else:
        status = ("UNCONFIRMED — set FMP_MONTHLY_BUDGET "
                  "(see core/config.py TODO); cap NOT assumed unlimited, "
                  "but cannot be enforced until confirmed")

    return {
        "pipeline": pipeline,
        "configured_monthly_limit": configured,
        "budget_cap_status": status,
        "calls_used_today": usage["today"],
        "calls_used_month": month,
        "remaining_estimated": remaining,
        "pct_consumed": pct,
        "reserved_benchmark_calls": RESERVED_BENCHMARK_CALLS,
        "planned_calls": planned_calls,
        "allowed_calls": allowed,
        "budget_limited": limited,
    }
