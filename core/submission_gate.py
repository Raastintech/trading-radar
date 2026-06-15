"""
core/submission_gate.py — last-mile gate re-check at order submission time.

Why this exists
---------------
The Veto Council evaluates a signal at scan time.  Between scan and order
submission, several things can change:

  - the market may have closed (early-close days, intraday halts);
  - the regime forecast may have flipped or gone stale (refresh between cycles);
  - the daily-loss circuit breaker may have tripped;
  - book-level exposure may have moved (a fill from another scan in
    flight, or an external position adjustment);
  - the council itself may have re-evaluated and flipped to VETOED.

The Phase 0 audit found that ``OrderManager`` only inspected
``council_result['verdict']`` and submitted otherwise.  This module
performs the **same** gates again, immediately before the broker call,
so a signal that was approved at scan time is re-validated against the
state of the system at submission time.

Design
------
- Cache-only.  No provider calls.  All inputs are passed in by the
  caller (``OrderManager``); this module never reaches over the wire.
- Fail-closed for the gates that have authoritative state available
  (session, council verdict, circuit-breaker, portfolio risk).
- Regime freshness is explicit: paper warns or blocks based on config; live
  always blocks on missing/malformed/stale regime artifacts.
- ``ctx`` is a flat dict with the optional gate dependencies; missing
  dependencies are skipped (allows incremental rollout).

The function returns ``(allowed: bool, reason: str, gate: str)``.
``gate`` names the gate that blocked, or ``""`` when allowed.  Callers
should log the block via ``DecisionLogger.log_veto(...)`` so the gate
trip is auditable.

Phase 0 scope: paper / simulation only.  Live trading remains disabled
upstream; this gate runs uniformly regardless of paper-vs-live so
turning on live later inherits the protection automatically.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import core.config as cfg

logger = logging.getLogger(__name__)


# Stances from regime_forecaster.map_strategy_favorability that should NOT
# pass the submission gate.  "selective" and "allowed" pass; "favored"
# obviously passes.  "avoid" is the only blocking stance.
_BLOCKING_REGIME_STANCES = {"avoid"}

# Strategy → favorability key.  The forecaster uses the live ticker names
# for the sleeves we care about; "REMORA" / "CONTRARIAN" are not yet on
# the forecaster's mapping, so they degrade to fail-open.
_STRATEGY_TO_FAVOR_KEY = {
    "VOYAGER":   "VOYAGER",
    "SNIPER":    "SNIPER_V6",
    "SHORT":     "SHORT_A",
}


def _regime_path() -> Path:
    return Path(cfg.CACHE_DIR) / "research" / "regime_forecast_latest.json"


def _normalize_stale_behavior(raw: Any, *, fallback: str) -> str:
    value = str(raw or "").strip().lower()
    return value if value in {"warn", "block"} else fallback


def _regime_stale_behavior() -> str:
    # Treat disagreement between PAPER_TRADING and ALPACA_PAPER as live-like
    # for fail-closed safety. The broker live gate will also refuse later.
    if bool(cfg.PAPER_TRADING) and bool(cfg.ALPACA_PAPER):
        return _normalize_stale_behavior(
            getattr(cfg, "REGIME_STALE_BEHAVIOR_PAPER", "warn"),
            fallback="warn",
        )
    return _normalize_stale_behavior(
        getattr(cfg, "REGIME_STALE_BEHAVIOR_LIVE", "block"),
        fallback="block",
    )


def _regime_freshness_issue(now: Optional[datetime] = None) -> Optional[str]:
    """Return a clear issue string when the regime artifact is unavailable,
    malformed, or older than the configured SLA. Returns None when fresh."""
    path = _regime_path()
    max_minutes = max(0, int(getattr(cfg, "REGIME_FRESHNESS_MAX_MINUTES", 1440)))
    if not path.exists():
        return f"missing regime artifact at {path}"

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        return f"malformed regime artifact at {path}: {exc}"
    if not isinstance(data, dict):
        return f"malformed regime artifact at {path}: top-level JSON is not an object"
    if not isinstance(data.get("strategy_favorability"), dict):
        return (
            f"malformed regime artifact at {path}: "
            "missing non-object strategy_favorability"
        )

    if max_minutes > 0:
        now_dt = now or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            age_minutes = (now_dt - mtime).total_seconds() / 60.0
        except Exception as exc:
            return f"could not stat regime artifact at {path}: {exc}"
        if age_minutes > max_minutes:
            return (
                f"stale regime artifact at {path}: "
                f"age={age_minutes:.1f}m max={max_minutes}m"
            )

    return None


def _read_regime_favorability() -> Optional[Dict[str, Dict[str, str]]]:
    """Read cache/research/regime_forecast_latest.json and return its
    ``strategy_favorability`` block.  Returns None on any failure so the
    caller can degrade to fail-open."""
    try:
        path = _regime_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        sf = data.get("strategy_favorability")
        if not isinstance(sf, dict):
            return None
        return sf
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("submission_gate: regime artefact read failed: %s", exc)
        return None


def evaluate(
    signal: Dict[str, Any],
    council_result: Dict[str, Any],
    *,
    portfolio_state: Optional[Dict[str, Any]] = None,
    circuit_breakers: Any = None,
    portfolio_risk: Any = None,
    open_positions: Optional[List[Dict[str, Any]]] = None,
    equity: Optional[float] = None,
    is_execution_allowed: Optional[Any] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, str, str]:
    """Re-run the gates immediately before order submission.

    Parameters
    ----------
    signal             : the scan-time signal dict (must carry ``strategy``,
                         ``ticker``, ``direction``).
    council_result     : the dict the Veto Council returned at scan time.
    portfolio_state    : as produced by ``PositionMonitor.portfolio_state()``.
                         Required for the circuit-breaker check.
    circuit_breakers   : instance of ``execution.circuit_breakers.CircuitBreakers``.
                         Optional; if absent, the breaker check is skipped.
    portfolio_risk     : instance of ``execution.portfolio_risk.PortfolioRisk``.
                         Optional; if absent, book-risk re-check is skipped.
    open_positions     : current Alpaca positions (for portfolio_risk check).
    equity             : account equity (for portfolio_risk check).
    is_execution_allowed : optional callable matching ``core.session``'s
                         ``is_execution_allowed(now)`` signature.  When
                         omitted we fall back to the canonical helper.
    now                : optional datetime for deterministic tests.

    Returns
    -------
    (allowed, reason, gate_name)
        ``gate_name`` is one of: "session", "council", "circuit_breaker",
        "regime", "duplicate_position", "portfolio_risk", or "" when
        allowed.
    """
    strategy  = str(signal.get("strategy", "")).upper()
    ticker    = str(signal.get("ticker", "")).upper()
    direction = str(signal.get("direction", "")).upper()

    # ── 1. Session state ────────────────────────────────────────────────
    if is_execution_allowed is None:
        try:
            from core.session import is_execution_allowed as _iea
            is_execution_allowed = _iea
        except Exception:  # pragma: no cover - core.session always present
            is_execution_allowed = None
    if is_execution_allowed is not None:
        try:
            if not is_execution_allowed(now):
                return (False,
                        f"Execution not allowed for current session "
                        f"(strategy={strategy}, ticker={ticker})",
                        "session")
        except TypeError:
            # Some implementations take no args
            if not is_execution_allowed():
                return (False,
                        "Execution not allowed for current session",
                        "session")

    # ── 2. Council verdict still APPROVED ──────────────────────────────
    verdict = str(council_result.get("verdict", "")).upper()
    if verdict != "APPROVED":
        agent  = council_result.get("agent") or "council"
        reason = council_result.get("reason") or "council not APPROVED"
        return (False, f"Council verdict={verdict}: {reason}",
                "council")

    # ── 3. Circuit breaker (if wired in) ───────────────────────────────
    if circuit_breakers is not None and portfolio_state is not None:
        try:
            allowed_cb, cb_reason = circuit_breakers.gate(
                strategy_name=strategy, portfolio_state=portfolio_state,
            )
            if not allowed_cb:
                return (False, cb_reason or "circuit breaker tripped",
                        "circuit_breaker")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("submission_gate: circuit_breakers.gate raised: %s", exc)
            # Fail-closed on circuit breaker — better to skip a trade
            # than ignore a halt because of a code error.
            return (False, f"circuit_breakers.gate error: {exc}",
                    "circuit_breaker")

    # ── 4. Regime gate (cache-only; freshness-aware) ───────────────────
    fav_key = _STRATEGY_TO_FAVOR_KEY.get(strategy)
    if fav_key is not None:
        freshness_issue = _regime_freshness_issue(now)
        if freshness_issue:
            behavior = _regime_stale_behavior()
            msg = (
                f"Regime forecast freshness issue for {fav_key}: "
                f"{freshness_issue}; behavior={behavior}; "
                f"PAPER_TRADING={cfg.PAPER_TRADING} ALPACA_PAPER={cfg.ALPACA_PAPER}"
            )
            if behavior == "block":
                logger.error("SUBMISSION_GATE_REGIME_FRESHNESS_BLOCK %s", msg)
                return (False, msg, "regime_freshness")
            logger.warning("SUBMISSION_GATE_REGIME_FRESHNESS_WARN %s", msg)

        favors = _read_regime_favorability()
        if favors is not None:
            row = favors.get(fav_key) or {}
            stance = str(row.get("stance", "")).lower()
            if stance in _BLOCKING_REGIME_STANCES:
                return (False,
                        f"Regime stance for {fav_key} is {stance!r}: "
                        f"{row.get('reason') or 'no reason given'}",
                        "regime")

    # ── 5. Duplicate-exposure guard ────────────────────────────────────
    # Defense-in-depth: even if portfolio_risk fails open or open_positions
    # is missing, never re-enter a ticker we already hold a live position
    # in.  PortfolioRisk's MAX_POSITIONS_PER_STRAT covers per-strategy
    # count, but a same-ticker re-entry from a different strategy would
    # still slip through, so check the ticker explicitly here.
    if open_positions:
        for pos in open_positions:
            try:
                pt = str(pos.get("ticker", "")).upper()
            except Exception:
                continue
            if pt and pt == ticker:
                return (False,
                        f"position already open in {ticker} "
                        f"(qty={pos.get('qty', '?')})",
                        "duplicate_position")

    # ── 6. Portfolio risk re-check (if wired in) ───────────────────────
    if (portfolio_risk is not None
            and open_positions is not None
            and equity is not None):
        try:
            allowed_pr, pr_reason = portfolio_risk.check(
                signal=signal,
                positions=open_positions,
                equity=equity,
            )
            if not allowed_pr:
                return (False, pr_reason or "portfolio risk limits hit",
                        "portfolio_risk")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("submission_gate: portfolio_risk.check raised: %s", exc)
            return (False, f"portfolio_risk.check error: {exc}",
                    "portfolio_risk")

    return (True, "", "")
