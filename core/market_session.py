"""
core/market_session.py — canonical "required completed market session" helper.

Single source of truth for the question every freshness gate must answer:
*which US-equities trading session should a daily bar exist for right now?*

Built on the project's exchange-calendar mechanism (core/session.py:
NYSE holidays + early closes).  Cred-free: importable without any
provider credentials (no core.config import).

Semantics
---------
A session counts as the *required* session only once its EOD bar can
reasonably exist at the provider: the regular close (16:00 ET, 13:00 ET
on early-close days) plus a publication buffer (EOD_PUBLISH_BUFFER_MIN).
Before that cutoff the required session is the prior trading day.

This is deliberately aligned with the systemd cadence:
  premarket 08:00 ET  → required = previous session
  midday    12:30 ET  → required = previous session
  nightly   20:30 ET  → required = today (close + 4.5h)

Calendar-age heuristics (e.g. "within 2 calendar days") must NOT be used
as the primary validity rule — they silently accept bars one completed
session behind the benchmark (2026-07-10 pipeline review, P0).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from zoneinfo import ZoneInfo

from core.session import _is_trading_day, _regular_close_hour

_ET = ZoneInfo("America/New_York")

# Minutes after the regular close before we require the session's EOD bar
# to exist at the provider.  4h keeps the 20:30 ET nightly run anchored on
# today's close while premarket/midday runs anchor on the prior session.
EOD_PUBLISH_BUFFER_MIN = 240


def _now_et(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(_ET)
    if now.tzinfo is None:
        return now.replace(tzinfo=_ET)
    return now.astimezone(_ET)


def previous_trading_day(d: date) -> date:
    """Most recent trading day strictly before ``d``."""
    cand = d - timedelta(days=1)
    for _ in range(10):
        if _is_trading_day(cand):
            return cand
        cand -= timedelta(days=1)
    return cand  # unreachable in practice


def required_market_session(now: Optional[datetime] = None) -> date:
    """The most recent completed US trading session whose EOD daily bar is
    expected to be available from the provider at ``now``.

    Holiday- and early-close-aware via core.session.  Today qualifies only
    when it is a trading day and ``now`` is past the regular close plus
    ``EOD_PUBLISH_BUFFER_MIN``; otherwise the prior trading day is required.
    """
    n = _now_et(now)
    d = n.date()
    if _is_trading_day(d):
        cutoff_min = _regular_close_hour(d) * 60 + EOD_PUBLISH_BUFFER_MIN
        if n.hour * 60 + n.minute >= cutoff_min:
            return d
    return previous_trading_day(d)


def sessions_behind(last_bar: Optional[date], now: Optional[datetime] = None) -> Optional[int]:
    """How many completed trading sessions ``last_bar`` lags the required
    session.  0 = aligned, 1 = one session behind, …  ``None`` when there
    is no bar at all.  A bar dated after the required session (bad data /
    future-dated) returns 0 — alignment gates should treat only positive
    lags as stale, and data-suspect checks handle future dates.
    """
    if last_bar is None:
        return None
    req = required_market_session(now)
    if last_bar >= req:
        return 0
    behind = 0
    d = req
    for _ in range(400):  # bound the walk; anything >400 sessions is "very stale"
        d = previous_trading_day(d)
        behind += 1
        if d <= last_bar:
            return behind
    return behind


def is_session_aligned(last_bar: Optional[date], now: Optional[datetime] = None) -> bool:
    """True when the last bar date equals (or exceeds) the required session."""
    return last_bar is not None and last_bar >= required_market_session(now)


def source_staleness(generated_at_iso: Optional[str],
                     now: Optional[datetime] = None) -> dict:
    """Session-based staleness verdict for a cached artifact/source.

    Returns ``{"source_as_of": "YYYY-MM-DD"|None,
               "source_age_sessions": int|None,
               "stale": bool}``.

    A source is *stale* when it predates the required completed session —
    i.e. at least one full trading session has closed since it was written.
    Unparseable / missing timestamps are stale (fail closed).
    """
    as_of: Optional[date] = None
    if generated_at_iso:
        s = str(generated_at_iso).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            as_of = datetime.fromisoformat(s).date()
        except ValueError:
            try:
                as_of = datetime.strptime(s[:10], "%Y-%m-%d").date()
            except ValueError:
                as_of = None
    age = sessions_behind(as_of, now)
    return {
        "source_as_of": str(as_of) if as_of else None,
        "source_age_sessions": age,
        "stale": age is None or age >= 1,
    }
