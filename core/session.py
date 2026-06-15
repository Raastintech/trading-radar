"""
core/session.py — NYSE session-state management.

SessionState enum + helper functions used by main.py and the dashboard.

Session windows (America/New_York):
  PREMARKET   04:00 – 09:30  (research only — no execution)
  REGULAR     09:30 – 16:00  (full execution window)
  POSTMARKET  16:00 – 20:00  (research only — no execution)
  CLOSED      20:00 – 04:00  (also weekends + NYSE holidays)

Early-close days (Thanksgiving Friday, Christmas Eve, July 3 when weekday):
  REGULAR ends at 13:00, POSTMARKET ends at 17:00.

Only REGULAR allows order execution — is_execution_allowed() enforces this.
"""
from __future__ import annotations

import enum
from datetime import date, datetime, timedelta
from typing import Tuple

from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


# ── NYSE calendar ──────────────────────────────────────────────────────────────

_NYSE_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1,  1),   # New Year's Day
    date(2025, 1, 20),   # MLK Day
    date(2025, 2, 17),   # Presidents' Day
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 26),   # Memorial Day
    date(2025, 6, 19),   # Juneteenth
    date(2025, 7,  4),   # Independence Day
    date(2025, 9,  1),   # Labor Day
    date(2025, 11,27),   # Thanksgiving
    date(2025, 12,25),   # Christmas
    # 2026
    date(2026, 1,  1),   # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents' Day
    date(2026, 4,  3),   # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7,  3),   # Independence Day (observed Fri)
    date(2026, 9,  7),   # Labor Day
    date(2026, 11,26),   # Thanksgiving
    date(2026, 12,25),   # Christmas
}

# Market closes at 13:00 ET on these days
_NYSE_EARLY_CLOSE: set[date] = {
    # 2025
    date(2025,  7,  3),  # Day before Independence Day
    date(2025, 11, 28),  # Day after Thanksgiving
    date(2025, 12, 24),  # Christmas Eve
    # 2026
    date(2026, 11, 27),  # Day after Thanksgiving
    date(2026, 12, 24),  # Christmas Eve
}


# ── SessionState ───────────────────────────────────────────────────────────────

class SessionState(enum.Enum):
    PREMARKET  = "PREMARKET"   # 04:00 – 09:30 ET
    REGULAR    = "REGULAR"     # 09:30 – 16:00 ET  (13:00 on early-close)
    POSTMARKET = "POSTMARKET"  # 16:00 – 20:00 ET  (13:00–17:00 on early-close)
    CLOSED     = "CLOSED"      # overnight, weekends, holidays


# ── Internal helpers ───────────────────────────────────────────────────────────

def _is_trading_day(d: date) -> bool:
    """True if NYSE holds any session on this date (not a holiday or weekend)."""
    return d.weekday() < 5 and d not in _NYSE_HOLIDAYS


def _regular_close_hour(d: date) -> int:
    """13 on early-close days, 16 otherwise."""
    return 13 if d in _NYSE_EARLY_CLOSE else 16


def _postmarket_end_hour(d: date) -> int:
    """17 on early-close days, 20 otherwise."""
    return 17 if d in _NYSE_EARLY_CLOSE else 20


def _at(day: date, h: int, mn: int = 0) -> datetime:
    """Datetime at h:mn ET on the given date."""
    return datetime(day.year, day.month, day.day, h, mn, 0, tzinfo=_ET)


# ── Public API ─────────────────────────────────────────────────────────────────

def get_session_state(now: datetime | None = None) -> SessionState:
    """Return the current NYSE session state for the given time (default: now)."""
    if now is None:
        now = datetime.now(_ET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_ET)

    d = now.date()
    if not _is_trading_day(d):
        return SessionState.CLOSED

    m = now.hour * 60 + now.minute          # minutes since midnight ET

    PREMARKET_START  = 4 * 60               # 04:00
    REGULAR_START    = 9 * 60 + 30          # 09:30
    REGULAR_CLOSE    = _regular_close_hour(d) * 60
    POSTMARKET_END   = _postmarket_end_hour(d) * 60

    if m < PREMARKET_START:
        return SessionState.CLOSED
    if m < REGULAR_START:
        return SessionState.PREMARKET
    if m < REGULAR_CLOSE:
        return SessionState.REGULAR
    if m < POSTMARKET_END:
        return SessionState.POSTMARKET
    return SessionState.CLOSED


def is_execution_allowed(now: datetime | None = None) -> bool:
    """
    Returns True only during the REGULAR session.
    All order-placement code should gate on this before submitting.
    """
    return get_session_state(now) == SessionState.REGULAR


def next_session_change(now: datetime | None = None) -> Tuple[SessionState, datetime]:
    """
    Return (next_state, transition_time) — the state we will enter next and
    the exact datetime of that transition (America/New_York timezone).

    Examples
    --------
    22:00 ET Mon  → (PREMARKET,  Tue 04:00 ET)
    07:00 ET Mon  → (REGULAR,    Mon 09:30 ET)
    11:00 ET Mon  → (POSTMARKET, Mon 16:00 ET)
    17:00 ET Mon  → (CLOSED,     Mon 20:00 ET)
    15:00 ET Fri  → (POSTMARKET, Fri 16:00 ET)
    """
    if now is None:
        now = datetime.now(_ET)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_ET)

    current = get_session_state(now)
    d = now.date()

    if current == SessionState.PREMARKET:
        return SessionState.REGULAR, _at(d, 9, 30)

    if current == SessionState.REGULAR:
        return SessionState.POSTMARKET, _at(d, _regular_close_hour(d))

    if current == SessionState.POSTMARKET:
        return SessionState.CLOSED, _at(d, _postmarket_end_hour(d))

    # CLOSED — find next premarket open
    candidate = d
    # If we're already past premarket start today (or it's a non-trading day), advance
    if not _is_trading_day(d) or now.hour >= 20 or (now.hour == 0 and d not in _NYSE_HOLIDAYS and now.hour * 60 + now.minute < 4 * 60):
        # we may still be before premarket on a trading day
        pass
    if now.hour >= 20:
        candidate = d + timedelta(days=1)
    # Walk forward to next trading day
    while not _is_trading_day(candidate):
        candidate += timedelta(days=1)
    # If candidate == d and we're between midnight and 04:00, next change is today's premarket
    if candidate == d and now.hour * 60 + now.minute < 4 * 60:
        return SessionState.PREMARKET, _at(d, 4)
    if candidate > d:
        return SessionState.PREMARKET, _at(candidate, 4)
    # candidate == d, now >= 20:00 — shouldn't reach here but be safe
    candidate += timedelta(days=1)
    while not _is_trading_day(candidate):
        candidate += timedelta(days=1)
    return SessionState.PREMARKET, _at(candidate, 4)


def fmt_session_badge(state: SessionState) -> str:
    """Short human-readable badge for use in dashboard/logs."""
    return {
        SessionState.PREMARKET:  "PRE-MARKET",
        SessionState.REGULAR:    "REGULAR",
        SessionState.POSTMARKET: "POST-MARKET",
        SessionState.CLOSED:     "CLOSED",
    }[state]
