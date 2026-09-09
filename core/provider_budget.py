"""
core/provider_budget.py — hard refusal layer for provider (FMP) call spending.

Why this module exists
----------------------
Until 2026-09 the system *described* itself as budget-guarded while enforcing
nothing.  ``Gatekeeper.budget_consume()`` was documented "TELEMETRY ONLY — always
returns True (never blocks)", and ``FMP_MONTHLY_BUDGET`` defaulted to ``0``, which
the config read as "no cap enforced".  The only real limit anywhere was the
750 RPM token bucket, which caps *rate*, not *spend*.  A single manual study
burned 20,260 calls on 2026-09-05, and an unbounded ``--max-calls`` on a
research maintenance job was enough to spend thousands more.

This module turns the counters that already existed into an actual gate:

* a **monthly** ceiling and a **daily** ceiling, both env-overridable;
* ``0`` no longer means "unlimited" — it falls back to the documented default.
  Unlimited is now an explicit, named opt-in
  (``ALLOW_UNLIMITED_PROVIDER_CALLS=true``), so it appears in the environment
  rather than as the silent absence of a value;
* a shared **planned-call** gate so a module that knows it is about to spend a
  lot must say so before the first call, not after the invoice.

Nothing here changes what any research module *decides*.  It only changes
whether the calls behind that decision are allowed to happen.

RESEARCH-ONLY: this is cost control.  It emits no signal, score, ranking or
verdict, and it is not evidence about any strategy.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── documented defaults ─────────────────────────────────────────────────────
#
# Sized from observed spend, not from a confirmed plan ceiling (the plan page
# shows 750 RPM + 50 GB bandwidth, not a call count).  Recent months:
# Jul 107,739 · Aug 88,157 · Sep-to-date 52,199.  Recent days ranged 52 → 20,260,
# with a typical cycle costing ~1,200–6,000.
#
# These are RUNAWAY GUARDS, not targets: they sit above normal operation so the
# daily cycle never trips them, and below the accidents that have actually
# happened.  Raise them deliberately in the environment if the work justifies it.
DEFAULT_MONTHLY_CAP = 120_000
DEFAULT_DAILY_CAP = 12_000

#: A single run planning more calls than this must pass an explicit override.
#: Chosen so ordinary cache-warm deltas (a few dozen) never prompt, while a
#: universe-wide refresh always does.
PLANNED_CALL_CONFIRM_THRESHOLD = 100

_TRUE = {"1", "true", "yes", "on"}


class ProviderBudgetExceeded(RuntimeError):
    """A provider call was refused because a configured ceiling was reached."""


class PlannedCallsNotAuthorised(RuntimeError):
    """A run planning a large number of provider calls has no explicit override."""


def _env(name: str) -> Optional[str]:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None


def allow_unlimited() -> bool:
    """True only when the operator has said so by name, in the environment."""
    v = _env("ALLOW_UNLIMITED_PROVIDER_CALLS")
    return bool(v and v.lower() in _TRUE)


def _cap(env_name: str, cfg_value: Optional[int], default: int) -> Optional[int]:
    """Resolve one ceiling. ``None`` means "no ceiling" (unlimited opt-in only).

    Order: explicit env → value already parsed into ``core.config`` → default.
    A configured ``0`` means "unset", which is the historical spelling; it now
    falls back to the default instead of disabling the gate.
    """
    if allow_unlimited():
        return None
    raw = _env(env_name)
    if raw is not None:
        try:
            n = int(raw)
        except ValueError:
            logger.warning("%s=%r is not an integer; using default %d",
                           env_name, raw, default)
            return default
        return n if n > 0 else default
    if cfg_value:
        return int(cfg_value)
    return default


def monthly_cap() -> Optional[int]:
    try:
        import core.config as cfg  # noqa: PLC0415 — avoids an import cycle at module load
        configured = getattr(cfg, "FMP_MONTHLY_BUDGET", 0)
    except Exception:
        configured = 0
    return _cap("FMP_MONTHLY_BUDGET", configured, DEFAULT_MONTHLY_CAP)


def daily_cap() -> Optional[int]:
    try:
        import core.config as cfg  # noqa: PLC0415
        configured = getattr(cfg, "FMP_DAILY_BUDGET", 0)
    except Exception:
        configured = 0
    return _cap("FMP_DAILY_BUDGET", configured, DEFAULT_DAILY_CAP)


def check_spend(used_month: int, used_today: int, n: int = 1) -> None:
    """Raise ``ProviderBudgetExceeded`` if spending ``n`` more would break a cap.

    Pure: the caller supplies the counters, so this is trivially testable and
    has no opinion about where usage is stored.
    """
    mcap, dcap = monthly_cap(), daily_cap()
    if mcap is not None and used_month + n > mcap:
        raise ProviderBudgetExceeded(
            f"monthly provider budget reached: {used_month} used + {n} requested "
            f"> {mcap} cap. Nothing was fetched. Raise FMP_MONTHLY_BUDGET, or set "
            "ALLOW_UNLIMITED_PROVIDER_CALLS=true to disable the ceiling "
            "deliberately.")
    if dcap is not None and used_today + n > dcap:
        raise ProviderBudgetExceeded(
            f"daily provider budget reached: {used_today} used + {n} requested "
            f"> {dcap} cap. Nothing was fetched. Raise FMP_DAILY_BUDGET, or set "
            "ALLOW_UNLIMITED_PROVIDER_CALLS=true to disable the ceiling "
            "deliberately.")


def assert_planned_calls_authorised(
    planned: int,
    *,
    override: bool = False,
    threshold: int = PLANNED_CALL_CONFIRM_THRESHOLD,
    where: str = "this run",
    override_flag: str = "--allow-large-run",
) -> None:
    """Refuse a run that plans more than ``threshold`` calls without an override.

    The point is that the size of a run is stated *before* it starts, by the
    module that knows it, rather than discovered afterwards in the counters.
    """
    if planned > threshold and not override:
        raise PlannedCallsNotAuthorised(
            f"{where} plans {planned} provider calls, above the {threshold}-call "
            f"confirmation threshold. Nothing was fetched. Pass {override_flag} "
            "to authorise a run this size deliberately.")


def assert_within_hard_cap(
    planned: int,
    *,
    hard_cap: int,
    override: bool = False,
    where: str = "this run",
    override_flag: str = "--allow-huge-run",
) -> None:
    """Refuse a run above a module's own per-run ceiling without an override."""
    if planned > hard_cap and not override:
        raise PlannedCallsNotAuthorised(
            f"{where} plans {planned} provider calls, above its per-run hard cap "
            f"of {hard_cap}. Nothing was fetched. Pass {override_flag} to exceed "
            "the cap deliberately, or split the work across runs.")


def describe() -> dict:
    """Current ceilings, for reports and operator visibility."""
    return {
        "monthly_cap": monthly_cap(),
        "daily_cap": daily_cap(),
        "planned_call_confirm_threshold": PLANNED_CALL_CONFIRM_THRESHOLD,
        "unlimited_opt_in": allow_unlimited(),
        "enforced": not allow_unlimited(),
    }
