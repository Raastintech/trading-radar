"""
Macro Events Calendar - Institutional Grade

Tracks upcoming macro events that move markets:
- Fed meetings (FOMC)
- Economic data (CPI, NFP, GDP)
- Earnings season
- Market holidays
- Major central bank decisions
"""

from datetime import datetime, timedelta
from typing import List, Dict


class MacroCalendar:
    """
    Track macro events and their market impact
    
    Events categorized by importance:
    - CRITICAL: Fed meetings, NFP, CPI
    - HIGH: GDP, Retail Sales, Fed speeches
    - MEDIUM: Housing data, PMI
    """
    
    # Define upcoming events (update quarterly)
    EVENTS = [
        # February 2026
        {"date": "2026-02-28", "time": "08:30", "event": "PCE Inflation", "impact": "HIGH"},
        
        # March 2026
        {"date": "2026-03-06", "time": "08:30", "event": "NFP Jobs Report", "impact": "CRITICAL"},
        {"date": "2026-03-11", "time": "08:30", "event": "CPI Inflation", "impact": "CRITICAL"},
        {"date": "2026-03-17", "time": "14:00", "event": "FOMC Decision", "impact": "CRITICAL"},
        {"date": "2026-03-17", "time": "14:30", "event": "Powell Press Conference", "impact": "CRITICAL"},
        {"date": "2026-03-26", "time": "08:30", "event": "GDP Report", "impact": "HIGH"},
        {"date": "2026-03-28", "time": "08:30", "event": "PCE Inflation", "impact": "HIGH"},
        
        # April 2026
        {"date": "2026-04-03", "time": "08:30", "event": "NFP Jobs Report", "impact": "CRITICAL"},
        {"date": "2026-04-10", "time": "08:30", "event": "CPI Inflation", "impact": "CRITICAL"},
        {"date": "2026-04-29", "time": "14:00", "event": "FOMC Decision", "impact": "CRITICAL"},
        
        # May 2026
        {"date": "2026-05-01", "time": "08:30", "event": "NFP Jobs Report", "impact": "CRITICAL"},
        {"date": "2026-05-13", "time": "08:30", "event": "CPI Inflation", "impact": "CRITICAL"},
        
        # June 2026
        {"date": "2026-06-05", "time": "08:30", "event": "NFP Jobs Report", "impact": "CRITICAL"},
        {"date": "2026-06-10", "time": "08:30", "event": "CPI Inflation", "impact": "CRITICAL"},
        {"date": "2026-06-17", "time": "14:00", "event": "FOMC Decision", "impact": "CRITICAL"},
        {"date": "2026-06-17", "time": "14:30", "event": "Powell Press Conference", "impact": "CRITICAL"},
    ]
    
    # Market holidays 2026
    HOLIDAYS = [
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
    
    def __init__(self):
        """Initialize macro calendar"""
        pass
    
    def get_upcoming_events(self, days_ahead: int = 14) -> List[Dict]:
        """
        Get upcoming macro events
        
        Args:
            days_ahead: Number of days to look ahead
        
        Returns:
            List of upcoming events with countdown
        """
        
        now = datetime.now()
        cutoff = now + timedelta(days=days_ahead)
        
        upcoming = []
        
        for event in self.EVENTS:
            event_dt = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M")
            
            # Only future events within window
            if now < event_dt <= cutoff:
                # Calculate time until
                time_until = event_dt - now
                
                # Format countdown
                if time_until.days > 0:
                    countdown = f"{time_until.days}d {time_until.seconds // 3600}h"
                elif time_until.seconds >= 3600:
                    countdown = f"{time_until.seconds // 3600}h {(time_until.seconds % 3600) // 60}m"
                else:
                    countdown = f"{time_until.seconds // 60}m"
                
                upcoming.append({
                    'date': event['date'],
                    'time': event['time'],
                    'event': event['event'],
                    'impact': event['impact'],
                    'countdown': countdown,
                    'days_away': time_until.days,
                    'datetime': event_dt
                })
        
        # Sort by datetime
        upcoming.sort(key=lambda x: x['datetime'])
        
        return upcoming
    
    def get_next_critical_event(self) -> Dict:
        """Get next CRITICAL macro event"""
        
        upcoming = self.get_upcoming_events(days_ahead=30)
        
        for event in upcoming:
            if event['impact'] == 'CRITICAL':
                return event
        
        return None
    
    def get_todays_events(self) -> List[Dict]:
        """Get today's macro events"""
        
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        
        todays_events = []
        
        for event in self.EVENTS:
            if event['date'] == today:
                todays_events.append(event)
        
        return todays_events
    
    def is_market_holiday(self, date_str: str = None) -> bool:
        """Check if date is market holiday"""
        
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        
        for holiday in self.HOLIDAYS:
            if holiday['date'] == date_str:
                return True
        
        return False
    
    def get_next_fomc(self) -> Dict:
        """Get next FOMC meeting"""

        upcoming = self.get_upcoming_events(days_ahead=90)

        for event in upcoming:
            if 'FOMC' in event['event']:
                return event

        return None

    def get_macro_window_state(self, now: datetime = None) -> Dict:
        """
        Determine the current macro event window state based on time proximity.

        For CRITICAL events the windows are:
            PRE_EVENT:      T-60 to T0     (event imminent, caution)
            EVENT_LOCKOUT:  T0 to T+30     (release chaos, deny sensitive strategies)
            POST_EVENT:     T+30 to T+120  (stabilizing, caution)
            CLEAR:          all other times

        For HIGH events the windows are narrower:
            PRE_EVENT:      T-30 to T0
            EVENT_LOCKOUT:  T0 to T+30
            POST_EVENT:     T+30 to T+60

        Strategy gate matrix per window:
                        CLEAR    PRE_EVENT  EVENT_LOCKOUT  POST_EVENT
            VOYAGER     CLEAR    CAUTION    CAUTION        CLEAR
            SNIPER      CLEAR    CAUTION    DENY           CAUTION
            REMORA      CLEAR    CAUTION    DENY           CAUTION
            SHORT       CLEAR    CAUTION    CAUTION        CLEAR
            CONTRARIAN  CLEAR    CLEAR      CAUTION        CAUTION

        Returns:
            {
                'window':          'CLEAR' | 'PRE_EVENT' | 'EVENT_LOCKOUT' | 'POST_EVENT',
                'event_name':      str,
                'impact':          str,
                'minutes_to_event': int | None,  # negative = event already released
                'strategy_gates':  {'VOYAGER': 'CLEAR'|'CAUTION'|'DENY', ...},
                'display_label':   str,           # human-readable for UI
            }
        """
        if now is None:
            now = datetime.now()

        # Window durations in minutes per impact level
        _windows = {
            'CRITICAL': {'pre': 60, 'lockout': 30, 'post': 120},
            'HIGH':     {'pre': 30, 'lockout': 30, 'post':  60},
        }

        # Gate matrix: window → strategy → action
        _gates = {
            'CLEAR':         {'VOYAGER': 'CLEAR',   'SNIPER': 'CLEAR',   'REMORA': 'CLEAR',   'SHORT': 'CLEAR',   'CONTRARIAN': 'CLEAR'},
            'PRE_EVENT':     {'VOYAGER': 'CAUTION',  'SNIPER': 'CAUTION', 'REMORA': 'CAUTION', 'SHORT': 'CAUTION', 'CONTRARIAN': 'CLEAR'},
            'EVENT_LOCKOUT': {'VOYAGER': 'CAUTION',  'SNIPER': 'DENY',    'REMORA': 'DENY',    'SHORT': 'CAUTION', 'CONTRARIAN': 'CAUTION'},
            'POST_EVENT':    {'VOYAGER': 'CLEAR',    'SNIPER': 'CAUTION', 'REMORA': 'CAUTION', 'SHORT': 'CLEAR',   'CONTRARIAN': 'CAUTION'},
        }

        _labels = {
            'CLEAR':         'CLEAR',
            'PRE_EVENT':     'CAUTION WINDOW',
            'EVENT_LOCKOUT': 'EVENT LOCKOUT',
            'POST_EVENT':    'POST-EVENT STABILIZING',
        }

        # Priority: EVENT_LOCKOUT beats PRE_EVENT beats POST_EVENT beats CLEAR
        _priority = {'EVENT_LOCKOUT': 3, 'PRE_EVENT': 2, 'POST_EVENT': 1, 'CLEAR': 0}

        best_window = 'CLEAR'
        best_event_name = ''
        best_impact = ''
        best_minutes = None

        for event in self.EVENTS:
            impact = event.get('impact', 'MEDIUM').upper()
            if impact not in _windows:
                continue

            event_dt = datetime.strptime(
                f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M"
            )
            # minutes_to: positive = event is in the future, negative = already released
            minutes_to = (event_dt - now).total_seconds() / 60
            w = _windows[impact]

            if 0 < minutes_to <= w['pre']:
                ew = 'PRE_EVENT'
            elif -w['lockout'] <= minutes_to <= 0:
                ew = 'EVENT_LOCKOUT'
            elif -(w['lockout'] + w['post']) < minutes_to < -w['lockout']:
                ew = 'POST_EVENT'
            else:
                continue  # outside all windows for this event

            if _priority[ew] > _priority[best_window]:
                best_window = ew
                best_event_name = event['event']
                best_impact = impact
                best_minutes = minutes_to

        # When CLEAR, find next upcoming CRITICAL/HIGH event for context display
        if best_window == 'CLEAR':
            sorted_events = sorted(
                self.EVENTS,
                key=lambda e: datetime.strptime(f"{e['date']} {e['time']}", "%Y-%m-%d %H:%M")
            )
            for event in sorted_events:
                impact = event.get('impact', 'MEDIUM').upper()
                if impact not in _windows:
                    continue
                event_dt = datetime.strptime(f"{event['date']} {event['time']}", "%Y-%m-%d %H:%M")
                minutes_to = (event_dt - now).total_seconds() / 60
                if minutes_to > 0:
                    best_event_name = event['event']
                    best_impact = impact
                    best_minutes = minutes_to
                    break

        return {
            'window': best_window,
            'event_name': best_event_name,
            'impact': best_impact,
            'minutes_to_event': int(best_minutes) if best_minutes is not None else None,
            'strategy_gates': _gates[best_window],
            'display_label': _labels[best_window],
        }


# Quick test
if __name__ == "__main__":
    cal = MacroCalendar()
    
    print("\n📅 MACRO CALENDAR TEST")
    print("="*60)
    
    # Upcoming events
    print("\n🔔 Upcoming Events (14 days):")
    events = cal.get_upcoming_events(14)
    
    if events:
        for event in events:
            print(f"  {event['date']} {event['time']} - {event['event']}")
            print(f"    Impact: {event['impact']} | Countdown: {event['countdown']}")
    else:
        print("  No major events in next 14 days")
    
    # Next critical
    print("\n⚠️  Next CRITICAL Event:")
    critical = cal.get_next_critical_event()
    if critical:
        print(f"  {critical['event']} - {critical['countdown']}")
    
    # Next FOMC
    print("\n🏛️  Next FOMC Meeting:")
    fomc = cal.get_next_fomc()
    if fomc:
        print(f"  {fomc['date']} - {fomc['countdown']}")
    
    print("\n" + "="*60 + "\n")
