"""
core/artifact_freshness.py — Phase 2B.2 freshness contract for research artifacts.

Pure stdlib helper.  No provider calls, no DB access, no governance.
Produces a uniform freshness verdict (age, threshold, stale reasons) so the
dashboard and the gatekeeper-refresh workflow apply the same rules.

Conventions
-----------
Each artifact is graded with one of these kinds:

  GATEKEEPER         — Executive Gatekeeper per-ticker JSON.  Defaults to a
                       24h stale window; tightens to 6h when the ticker has
                       earnings today / earlier today, and warns (soft)
                       above 4h for the currently-selected ticker.

  MCP_AUDIT          — cache/research/mcp_analysis_latest.json.  Stale if
                       older than 12h, or if the market session changed
                       since generated_at, or if generated_at precedes the
                       latest regime_forecast_latest.json mtime/built_at.

  STOCK_LENS         — per-ticker Stock Lens JSON.  Defaults to 24h.

  MARKET_FORECAST    — regime_forecast_latest.json.  Defaults to 24h.

Returned verdict (a dict, so it round-trips through JSON cleanly):

  {
    "kind":            str,             # one of the kinds above
    "age_seconds":     int,
    "stale_threshold": int,             # seconds
    "warn_threshold":  int,             # seconds (<= stale_threshold)
    "stale":           bool,
    "warn":            bool,            # >= warn_threshold but < stale
    "stale_reasons":   list[str],       # ordered, deduped
    "generated_at":    str | None,      # ISO8601 if known
    "threshold_label": str,             # "normal" / "earnings" / "intraday"
  }
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# ── Threshold constants (seconds) ────────────────────────────────────────────

_HOUR = 3600
_DAY = 24 * _HOUR

# Public so tests and callers can introspect.  Treat as read-only.
FRESHNESS_THRESHOLDS = {
    "GATEKEEPER": {
        "normal":   {"stale": 24 * _HOUR, "warn": 24 * _HOUR},
        "intraday": {"stale": 24 * _HOUR, "warn":  4 * _HOUR},
        "earnings": {"stale":  6 * _HOUR, "warn":  6 * _HOUR},
    },
    "MCP_AUDIT": {
        "normal":   {"stale": 12 * _HOUR, "warn": 12 * _HOUR},
    },
    "STOCK_LENS": {
        "normal":   {"stale": 24 * _HOUR, "warn": 24 * _HOUR},
    },
    "MARKET_FORECAST": {
        "normal":   {"stale": 24 * _HOUR, "warn": 24 * _HOUR},
    },
}


# ── ISO helpers (mirror dashboards/_age_from_iso_short to avoid import) ──────

def _parse_iso_utc(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now_ts(now: Optional[float] = None) -> float:
    return float(now) if now is not None else time.time()


# ── Core verdict ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Thresholds:
    stale: int
    warn: int
    label: str


def _pick_thresholds(kind: str, *, is_earnings_day: bool = False,
                     is_intraday_selected: bool = False) -> _Thresholds:
    table = FRESHNESS_THRESHOLDS.get(kind)
    if not table:
        # Unknown kind: degrade to 24h stale, no warn.
        return _Thresholds(stale=_DAY, warn=_DAY, label="normal")
    if is_earnings_day and "earnings" in table:
        t = table["earnings"]
        return _Thresholds(stale=int(t["stale"]), warn=int(t["warn"]), label="earnings")
    if is_intraday_selected and "intraday" in table:
        t = table["intraday"]
        return _Thresholds(stale=int(t["stale"]), warn=int(t["warn"]), label="intraday")
    t = table["normal"]
    return _Thresholds(stale=int(t["stale"]), warn=int(t["warn"]), label="normal")


def compute_freshness(
    *,
    kind: str,
    age_seconds: Optional[int],
    generated_at: Optional[str] = None,
    is_earnings_day: bool = False,
    is_intraday_selected: bool = False,
    extra_reasons: Optional[Iterable[str]] = None,
) -> dict:
    """Stateless verdict builder.  No filesystem or clock side-effects.

    ``age_seconds`` may be None (artifact missing); the verdict is then
    marked stale with reason ``"artifact_missing"``.

    ``extra_reasons`` lets callers attach kind-specific staleness signals
    (e.g. ``"session_changed"``, ``"forecast_newer_than_sidecar"``).  Any
    truthy extra reason forces ``stale=True``.
    """
    thresholds = _pick_thresholds(
        kind,
        is_earnings_day=is_earnings_day,
        is_intraday_selected=is_intraday_selected,
    )
    reasons: list[str] = []

    if age_seconds is None:
        reasons.append("artifact_missing")
        return {
            "kind":            kind,
            "age_seconds":     None,
            "stale_threshold": thresholds.stale,
            "warn_threshold":  thresholds.warn,
            "stale":           True,
            "warn":            True,
            "stale_reasons":   reasons,
            "generated_at":    generated_at,
            "threshold_label": thresholds.label,
        }

    age_seconds = int(age_seconds)
    stale = age_seconds > thresholds.stale
    warn = age_seconds > thresholds.warn and not stale

    if stale:
        reasons.append(f"age>{thresholds.stale // _HOUR}h")
    elif warn:
        reasons.append(f"age>{thresholds.warn // _HOUR}h")

    if extra_reasons:
        for r in extra_reasons:
            if not r:
                continue
            text = str(r).strip()
            if text and text not in reasons:
                reasons.append(text)
                stale = True  # any kind-specific signal forces stale

    return {
        "kind":            kind,
        "age_seconds":     age_seconds,
        "stale_threshold": thresholds.stale,
        "warn_threshold":  thresholds.warn,
        "stale":           bool(stale),
        "warn":            bool(warn),
        "stale_reasons":   reasons,
        "generated_at":    generated_at,
        "threshold_label": thresholds.label,
    }


# ── Filesystem convenience ───────────────────────────────────────────────────

def freshness_for_path(
    path: Path,
    *,
    kind: str,
    is_earnings_day: bool = False,
    is_intraday_selected: bool = False,
    generated_at: Optional[str] = None,
    extra_reasons: Optional[Iterable[str]] = None,
    now: Optional[float] = None,
) -> dict:
    """Convenience: turn a Path + kind into a verdict.

    Returns the missing-artifact verdict if the file does not exist.  Does
    not parse JSON — the dashboard already has its own readers.
    """
    p = Path(path)
    if not p.exists():
        return compute_freshness(
            kind=kind,
            age_seconds=None,
            generated_at=generated_at,
            is_earnings_day=is_earnings_day,
            is_intraday_selected=is_intraday_selected,
            extra_reasons=extra_reasons,
        )
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return compute_freshness(
            kind=kind,
            age_seconds=None,
            generated_at=generated_at,
            is_earnings_day=is_earnings_day,
            is_intraday_selected=is_intraday_selected,
            extra_reasons=extra_reasons,
        )
    return compute_freshness(
        kind=kind,
        age_seconds=int(_now_ts(now) - mtime),
        generated_at=generated_at,
        is_earnings_day=is_earnings_day,
        is_intraday_selected=is_intraday_selected,
        extra_reasons=extra_reasons,
    )


# ── MCP audit extra-reason helpers ───────────────────────────────────────────

def mcp_extra_reasons(
    *,
    sidecar_session: Optional[str],
    current_session: Optional[str],
    sidecar_generated_at: Optional[str],
    forecast_built_at: Optional[str],
) -> list[str]:
    """Build the MCP-audit-specific extra reasons.

    Returns:
      - "session_changed"               if sidecar_session != current_session
      - "forecast_newer_than_sidecar"   if forecast built/anchor is newer
                                        than the sidecar generated_at

    Both inputs are tolerant of None / unparseable values — they simply
    do not contribute a reason in that case.
    """
    out: list[str] = []

    if sidecar_session and current_session:
        if str(sidecar_session).strip().lower() != str(current_session).strip().lower():
            out.append("session_changed")

    sidecar_dt = _parse_iso_utc(sidecar_generated_at)
    forecast_dt = _parse_iso_utc(forecast_built_at)
    if sidecar_dt and forecast_dt and forecast_dt > sidecar_dt:
        out.append("forecast_newer_than_sidecar")

    return out


# ── Earnings-day classification ──────────────────────────────────────────────

def earnings_status(
    ticker: str,
    earnings_rows: Optional[Iterable[dict]],
    *,
    today_iso: Optional[str] = None,
) -> Optional[str]:
    """Classify a ticker against an earnings calendar already loaded by the
    dashboard.  No provider calls — caller passes the list.

    Returns one of:
      - "EARNINGS TODAY"       — calendar entry for today
      - "EARNINGS TOMORROW"    — calendar entry for today+1
      - "EARNINGS THIS WEEK"   — within next 5 calendar days
      - "POST-EARNINGS"        — within last 2 calendar days
      - None                   — no match
    """
    if not ticker or not earnings_rows:
        return None

    today = today_iso or datetime.now(timezone.utc).date().isoformat()
    try:
        today_date = datetime.fromisoformat(today).date()
    except ValueError:
        return None

    tk = ticker.upper()
    closest_future: Optional[int] = None  # days from today, >= 0
    closest_past: Optional[int] = None    # days from today, > 0

    for row in earnings_rows:
        sym = str((row or {}).get("symbol") or "").upper()
        if sym != tk:
            continue
        date_text = str((row or {}).get("date") or "")[:10]
        try:
            row_date = datetime.fromisoformat(date_text).date()
        except ValueError:
            continue
        delta = (row_date - today_date).days
        if delta >= 0:
            if closest_future is None or delta < closest_future:
                closest_future = delta
        else:
            past = -delta
            if closest_past is None or past < closest_past:
                closest_past = past

    if closest_future is not None:
        if closest_future == 0:
            return "EARNINGS TODAY"
        if closest_future == 1:
            return "EARNINGS TOMORROW"
        if closest_future <= 5:
            return "EARNINGS THIS WEEK"
    if closest_past is not None and closest_past <= 2:
        return "POST-EARNINGS"
    return None


def is_earnings_day(ticker: str, earnings_rows: Optional[Iterable[dict]],
                    *, today_iso: Optional[str] = None) -> bool:
    """Tight helper for the dashboard: only TODAY counts as an earnings day
    for the purposes of tightening the Gatekeeper stale threshold.
    """
    return earnings_status(ticker, earnings_rows, today_iso=today_iso) == "EARNINGS TODAY"


# ── Repo paths ───────────────────────────────────────────────────────────────

def repo_root() -> Path:
    # core/artifact_freshness.py → repo root is parents[1]
    return Path(__file__).resolve().parents[1]


def gatekeeper_artifact_path(ticker: str) -> Path:
    return (repo_root() / "cache" / "research"
            / f"executive_gatekeeper_{ticker.upper()}_latest.json")


def mcp_audit_artifact_path() -> Path:
    return repo_root() / "cache" / "research" / "mcp_analysis_latest.json"


def forecast_artifact_path() -> Path:
    return repo_root() / "cache" / "research" / "regime_forecast_latest.json"
