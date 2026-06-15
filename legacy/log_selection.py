import glob
import os
import re
from datetime import datetime, timedelta


def select_active_trader_log(pattern: str = "logs/trader_v3_*.log", recent_days: int = 7):
    """
    Prefer the newest trading-day log by filename date, then by mtime.
    This avoids attaching to an old historical file that is still being touched.
    """
    files = glob.glob(pattern)
    if not files:
        return None

    now = datetime.now()
    candidates = []
    for path in files:
        base = os.path.basename(path)
        m = re.search(r"trader_v3_(\d{8})\.log$", base)
        file_date = None
        if m:
            try:
                file_date = datetime.strptime(m.group(1), "%Y%m%d").date()
            except Exception:
                file_date = None
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
        except Exception:
            continue
        candidates.append((path, file_date, mtime))

    if not candidates:
        return None

    recent_cutoff = now - timedelta(days=recent_days)
    recent = [c for c in candidates if c[2] >= recent_cutoff]
    pool = recent or candidates

    def score(item):
        path, file_date, mtime = item
        dated = 1 if file_date else 0
        return (
            dated,
            file_date or datetime.min.date(),
            mtime,
        )

    return max(pool, key=score)[0]
