import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "trading_performance.db"


class SystemEventLogger:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path

    def _ensure_table(self, cur):
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS system_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                timestamp TEXT NOT NULL,
                component TEXT NOT NULL,
                severity TEXT NOT NULL,
                error_type TEXT,
                message TEXT NOT NULL,
                details_json TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_system_events_run_id ON system_events(run_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_system_events_component_time ON system_events(component, timestamp)"
        )

    def log(
        self,
        component: str,
        severity: str,
        message: str,
        run_id: str = None,
        error_type: str = None,
        details: dict = None,
    ):
        ts = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        self._ensure_table(cur)
        cur.execute(
            """
            INSERT INTO system_events (run_id, timestamp, component, severity, error_type, message, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                ts,
                str(component),
                str(severity).upper(),
                str(error_type) if error_type else None,
                str(message),
                json.dumps(details or {}),
            ),
        )
        conn.commit()
        conn.close()
