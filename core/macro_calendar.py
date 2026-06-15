"""
core/macro_calendar.py — Static macro event calendar and window-state logic.

Provides:
  • Upcoming macro event lookup (hardcoded schedule, updated quarterly)
  • Strategy gate matrix per event window (CLEAR / CAUTION / DENY)
  • Market holiday list for 2026

Note: Live economic calendar data comes from FMP via fmp_client.get_economic_calendar().
This module provides the strategy-gate logic and a hardcoded fallback event schedule.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class MacroCalendar:
    """
    Macro event window detection and strategy gate matrix.

    Window types:
        CLEAR          — no active event influence
        PRE_EVENT      — event approaching; caution mode
        EVENT_LOCKOUT  — event just released; deny sensitive entries
        POST_EVENT     — stabilizing period; selective caution

    Gate values per strategy: CLEAR | CAUTION | DENY
    """

    # ── Hardcoded schedule (update quarterly) ─────────────────────────────────
    EVENTS: List[Dict] = [
        # March 2026
        {"date": "2026-03-06", "time": "08:30", "event": "NFP Jobs Report",          "impact": "CRITICAL"},
        {"date": "2026-03-11", "time": "08:30", "event": "CPI Inflation",             "impact": "CRITICAL"},
        {"date": "2026-03-17", "time": "14:00", "event": "FOMC Decision",             "impact": "CRITICAL"},
        {"date": "2026-03-17", "time": "14:30", "event": "Powell Press Conference",   "impact": "CRITICAL"},
        {"date": "2026-03-26", "time": "08:30", "event": "GDP Report",                "impact": "HIGH"},
        {"date": "2026-03-28", "time": "08:30", "event": "PCE Inflation",             "impact": "HIGH"},
        # April 2026
        {"date": "2026-04-03", "time": "08:30", "event": "NFP Jobs Report",          "impact": "CRITICAL"},
        {"date": "2026-04-10", "time": "08:30", "event": "CPI Inflation",             "impact": "CRITICAL"},
        {"date": "2026-04-29", "time": "14:00", "event": "FOMC Decision",             "impact": "CRITICAL"},
        # May 2026
        {"date": "2026-05-01", "time": "08:30", "event": "NFP Jobs Report",          "impact": "CRITICAL"},
        {"date": "2026-05-13", "time": "08:30", "event": "CPI Inflation",             "impact": "CRITICAL"},
        # June 2026
        {"date": "2026-06-05", "time": "08:30", "event": "NFP Jobs Report",          "impact": "CRITICAL"},
        {"date": "2026-06-10", "time": "08:30", "event": "CPI Inflation",             "impact": "CRITICAL"},
        {"date": "2026-06-17", "time": "14:00", "event": "FOMC Decision",             "impact": "CRITICAL"},
        {"date": "2026-06-17", "time": "14:30", "event": "Powell Press Conference",   "impact": "CRITICAL"},
    ]

    # ── Market holidays 2026 ──────────────────────────────────────────────────
    HOLIDAYS: List[Dict] = [
        {"date": "2026-01-01", "event": "New Year's Day"},
        {"date": "2026-01-20", "event": "MLK Day"},
        {"date": "2026-02-17", "event": "Presidents Day"},
        {"date": "2026-04-03", "event": "Good Friday"},
        {"date": "2026-05-25", "event": "Memorial Day"},
        {"date": "2026-07-03", "event": "Independence Day (Observed)"},
        {"date": "2026-09-07", "event": "Labor Day"},
        {"date": "2026-11-26", "event": "Thanksgiving"},
        {"date": "2026-12-25", "event": "Christmas"},
    ]

    # ── Window durations in minutes per impact level ──────────────────────────
    _WINDOWS: Dict[str, Dict] = {
        "CRITICAL": {"pre": 60, "lockout": 30, "post": 120},
        "HIGH":     {"pre": 30, "lockout": 30, "post":  60},
    }

    # ── Gate matrix: window → strategy → action ───────────────────────────────
    _GATES: Dict[str, Dict] = {
        "CLEAR":         {"VOYAGER": "CLEAR",   "SNIPER": "CLEAR",   "REMORA": "CLEAR",   "SHORT": "CLEAR",   "CONTRARIAN": "CLEAR"},
        "PRE_EVENT":     {"VOYAGER": "CAUTION", "SNIPER": "CAUTION", "REMORA": "CAUTION", "SHORT": "CAUTION", "CONTRARIAN": "CLEAR"},
        "EVENT_LOCKOUT": {"VOYAGER": "CAUTION", "SNIPER": "DENY",    "REMORA": "DENY",    "SHORT": "CAUTION", "CONTRARIAN": "CAUTION"},
        "POST_EVENT":    {"VOYAGER": "CLEAR",   "SNIPER": "CAUTION", "REMORA": "CAUTION", "SHORT": "CLEAR",   "CONTRARIAN": "CAUTION"},
    }

    _LABELS: Dict[str, str] = {
        "CLEAR":         "CLEAR",
        "PRE_EVENT":     "CAUTION WINDOW",
        "EVENT_LOCKOUT": "EVENT LOCKOUT",
        "POST_EVENT":    "POST-EVENT STABILIZING",
    }

    _PRIORITY: Dict[str, int] = {
        "EVENT_LOCKOUT": 3, "PRE_EVENT": 2, "POST_EVENT": 1, "CLEAR": 0
    }

    # ── Public API ────────────────────────────────────────────────────────────

    def get_macro_window_state(self, now: Optional[datetime] = None) -> Dict:
        """
        Returns the current macro event window and per-strategy gates.

        Returns:
            {
                'window':           'CLEAR' | 'PRE_EVENT' | 'EVENT_LOCKOUT' | 'POST_EVENT',
                'event_name':       str,
                'impact':           str,
                'minutes_to_event': int | None,
                'strategy_gates':   {'VOYAGER': 'CLEAR'|'CAUTION'|'DENY', ...},
                'display_label':    str,
            }
        """
        if now is None:
            now = datetime.now()

        best_window     = "CLEAR"
        best_event_name = ""
        best_impact     = ""
        best_minutes: Optional[float] = None

        for event in self.EVENTS:
            impact = event.get("impact", "MEDIUM").upper()
            if impact not in self._WINDOWS:
                continue
            event_dt   = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M")
            minutes_to = (event_dt - now).total_seconds() / 60
            w          = self._WINDOWS[impact]

            if 0 < minutes_to <= w["pre"]:
                ew = "PRE_EVENT"
            elif -w["lockout"] <= minutes_to <= 0:
                ew = "EVENT_LOCKOUT"
            elif -(w["lockout"] + w["post"]) < minutes_to < -w["lockout"]:
                ew = "POST_EVENT"
            else:
                continue

            if self._PRIORITY[ew] > self._PRIORITY[best_window]:
                best_window     = ew
                best_event_name = event["event"]
                best_impact     = impact
                best_minutes    = minutes_to

        if best_window == "CLEAR":
            for event in sorted(self.EVENTS, key=lambda e: e["date"] + e["time"]):
                impact = event.get("impact", "MEDIUM").upper()
                if impact not in self._WINDOWS:
                    continue
                event_dt   = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M")
                minutes_to = (event_dt - now).total_seconds() / 60
                if minutes_to > 0:
                    best_event_name = event["event"]
                    best_impact     = impact
                    best_minutes    = minutes_to
                    break

        return {
            "window":           best_window,
            "event_name":       best_event_name,
            "impact":           best_impact,
            "minutes_to_event": int(best_minutes) if best_minutes is not None else None,
            "strategy_gates":   self._GATES[best_window],
            "display_label":    self._LABELS[best_window],
        }

    def get_upcoming_events(self, days_ahead: int = 14) -> List[Dict]:
        """Events within the next N days, sorted by datetime."""
        now    = datetime.now()
        cutoff = now + timedelta(days=days_ahead)
        result = []
        for event in self.EVENTS:
            event_dt = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M")
            if now < event_dt <= cutoff:
                time_until = event_dt - now
                if time_until.days > 0:
                    countdown = f"{time_until.days}d {time_until.seconds // 3600}h"
                elif time_until.seconds >= 3600:
                    countdown = f"{time_until.seconds // 3600}h {(time_until.seconds % 3600) // 60}m"
                else:
                    countdown = f"{time_until.seconds // 60}m"
                result.append({**event, "countdown": countdown, "days_away": time_until.days, "datetime": event_dt})
        result.sort(key=lambda x: x["datetime"])
        return result

    def get_next_critical_event(self) -> Optional[Dict]:
        """Next CRITICAL impact event within 30 days."""
        for event in self.get_upcoming_events(days_ahead=30):
            if event["impact"] == "CRITICAL":
                return event
        return None

    def is_market_holiday(self, date_str: Optional[str] = None) -> bool:
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        return any(h["date"] == date_str for h in self.HOLIDAYS)


# ── Module-level singleton ────────────────────────────────────────────────────
_calendar: Optional[MacroCalendar] = None


def get_macro_calendar() -> MacroCalendar:
    global _calendar
    if _calendar is None:
        _calendar = MacroCalendar()
    return _calendar
