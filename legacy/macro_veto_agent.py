# macro_veto_agent.py
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Dict

DB_PATH = "trading_performance.db"

class MacroVetoAgent:
    """
    Macro / Event risk agent.
    - VETO if HIGH impact event within veto_window_minutes
    - CAUTION if HIGH impact event within caution_window_minutes
    - APPROVE otherwise
    """
    def __init__(self, veto_window_minutes=90, caution_window_minutes=240):
        self.veto_window_minutes = int(veto_window_minutes)
        self.caution_window_minutes = int(caution_window_minutes)

    def _now_utc(self):
        return datetime.now(timezone.utc)

    def _minutes_to(self, t_utc):
        delta = t_utc - self._now_utc()
        return int(delta.total_seconds() // 60)

    def _get_next_event(self) -> Optional[Dict]:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        now = self._now_utc().isoformat()
        cur.execute("""
          SELECT event_time_utc, event_name, impact, source, notes
          FROM macro_events
          WHERE event_time_utc >= ?
          ORDER BY event_time_utc ASC
          LIMIT 1
        """, (now,))
        row = cur.fetchone()
        con.close()
        if not row:
            return None
        return dict(row)

    def vote_on_trade(self, ticker: str) -> Dict:
        nxt = self._get_next_event()
        if not nxt:
            return {
                "vote": "APPROVE",
                "reason": "No upcoming macro events in DB",
                "score": 70
            }

        # Parse time
        try:
            t = datetime.fromisoformat(nxt["event_time_utc"])
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
        except Exception:
            return {
                "vote": "CAUTION",
                "reason": "Macro event time parse error",
                "score": 50
            }

        mins = self._minutes_to(t)
        impact = (nxt.get("impact") or "").upper()
        name = nxt.get("event_name", "Unknown")

        # Only treat HIGH events as trade blockers initially
        if impact == "HIGH":
            if 0 <= mins <= self.veto_window_minutes:
                return {
                    "vote": "VETO",
                    "reason": f"HIGH impact event soon: {name} in {mins}m",
                    "score": 20,
                    "macro": {"event_name": name, "impact": impact, "minutes_to_event": mins}
                }
            if 0 <= mins <= self.caution_window_minutes:
                return {
                    "vote": "CAUTION",
                    "reason": f"HIGH impact event upcoming: {name} in {mins}m",
                    "score": 45,
                    "macro": {"event_name": name, "impact": impact, "minutes_to_event": mins}
                }

        return {
            "vote": "APPROVE",
            "reason": f"No blocking macro risk (next: {name} in {mins}m, impact={impact})",
            "score": 70,
            "macro": {"event_name": name, "impact": impact, "minutes_to_event": mins}
        }
