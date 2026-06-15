from __future__ import annotations

import glob
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional


_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"
_VIX_VALUE_RE = re.compile(r"VIX:\s*([0-9]+(?:\.[0-9]+)?)")
_EQUITY_VALUE_RE = re.compile(r"Live account equity:\s*\$([0-9,]+(?:\.[0-9]+)?)")


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def sparkline(values: List[Optional[float]], width: int = 10) -> str:
    width = max(4, int(width or 10))
    series = list(values or [])
    if not series:
        return "·" * width

    if len(series) > width:
        bucketed: List[Optional[float]] = []
        total = len(series)
        for idx in range(width):
            start = int(idx * total / width)
            end = int((idx + 1) * total / width)
            bucket = [v for v in series[start:end] if v is not None]
            bucketed.append(sum(bucket) / len(bucket) if bucket else None)
        series = bucketed
    elif len(series) < width:
        series = ([None] * (width - len(series))) + series

    valid = [v for v in series if v is not None]
    if not valid:
        return "·" * width

    low = min(valid)
    high = max(valid)
    if abs(high - low) < 1e-9:
        return "".join("·" if v is None else "▄" for v in series)

    rendered: List[str] = []
    scale = len(_SPARK_BLOCKS) - 1
    for value in series:
        if value is None:
            rendered.append("·")
            continue
        idx = int(round(((value - low) / (high - low)) * scale))
        idx = max(0, min(scale, idx))
        rendered.append(_SPARK_BLOCKS[idx])
    return "".join(rendered)


def _query_series(db_path: str, query: str, limit: int) -> List[float]:
    if not db_path or not os.path.exists(db_path):
        return []
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(query, (int(limit),)).fetchall()
        values = [_safe_float(row[0]) for row in rows]
        return [v for v in reversed(values) if v is not None]
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def _recent_log_paths(logs_dir: str, limit_files: int = 5) -> List[str]:
    if not logs_dir or not os.path.isdir(logs_dir):
        return []
    paths = sorted(glob.glob(os.path.join(logs_dir, "trader_v3_*.log")))
    return paths[-int(limit_files):]


def _load_vix_series_from_logs(logs_dir: str, limit: int) -> List[float]:
    values: List[float] = []
    for path in _recent_log_paths(logs_dir):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if "VIX:" not in line:
                        continue
                    if "^VIX" not in line and "[LIVE]" not in line:
                        continue
                    match = _VIX_VALUE_RE.search(line)
                    if not match:
                        continue
                    value = _safe_float(match.group(1))
                    if value is not None:
                        values.append(value)
        except Exception:
            continue
    return values[-int(limit):]


def _load_equity_series_from_logs(logs_dir: str, limit: int) -> List[float]:
    values: List[float] = []
    for path in _recent_log_paths(logs_dir):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if "Live account equity:" not in line:
                        continue
                    match = _EQUITY_VALUE_RE.search(line)
                    if not match:
                        continue
                    value = _safe_float(match.group(1).replace(",", ""))
                    if value is not None:
                        values.append(value)
        except Exception:
            continue
    return values[-int(limit):]


def load_terminal_trend_snapshot(
    db_path: str = "trading_performance.db",
    logs_dir: str = "logs",
    *,
    width: int = 10,
) -> Dict[str, Any]:
    breadth_series = _query_series(
        db_path,
        """
        SELECT market_breadth_pct
        FROM daily_context
        WHERE market_breadth_pct IS NOT NULL
        ORDER BY context_date DESC
        LIMIT ?
        """,
        limit=width,
    )
    equity_series = _query_series(
        db_path,
        """
        SELECT account_value
        FROM daily_snapshots
        WHERE account_value IS NOT NULL
        ORDER BY date DESC
        LIMIT ?
        """,
        limit=width,
    )
    equity_source = "daily_snapshots" if equity_series else None
    if not equity_series:
        equity_series = _load_equity_series_from_logs(logs_dir, width)
        equity_source = "daemon_log_startup_equity" if equity_series else None

    vix_series = _load_vix_series_from_logs(logs_dir, width)

    return {
        "vix_series": vix_series,
        "vix_spark": sparkline(vix_series, width=width),
        "vix_source": "daemon_log_live_vix" if vix_series else None,
        "breadth_series": breadth_series,
        "breadth_spark": sparkline(breadth_series, width=width),
        "breadth_source": "daily_context" if breadth_series else None,
        "equity_series": equity_series,
        "equity_spark": sparkline(equity_series, width=width),
        "equity_source": equity_source,
    }


def compact_trend_labels(
    snapshot: Dict[str, Any],
    *,
    current_vix: Optional[float] = None,
    current_breadth: Optional[float] = None,
    current_equity: Optional[float] = None,
) -> Dict[str, str]:
    vix_value = _safe_float(current_vix)
    if vix_value is None:
        series = snapshot.get("vix_series") or []
        vix_value = series[-1] if series else None

    breadth_value = _safe_float(current_breadth)
    if breadth_value is None:
        series = snapshot.get("breadth_series") or []
        breadth_value = series[-1] if series else None

    equity_value = _safe_float(current_equity)
    if equity_value is None:
        series = snapshot.get("equity_series") or []
        equity_value = series[-1] if series else None

    return {
        "vix": f"{snapshot.get('vix_spark') or '··········'} {vix_value:.1f}" if vix_value is not None else f"{snapshot.get('vix_spark') or '··········'} n/a",
        "breadth": f"{snapshot.get('breadth_spark') or '··········'} {breadth_value:.1f}%" if breadth_value is not None else f"{snapshot.get('breadth_spark') or '··········'} n/a",
        "equity": f"{snapshot.get('equity_spark') or '··········'} ${equity_value/1000.0:.1f}k" if equity_value is not None else f"{snapshot.get('equity_spark') or '··········'} n/a",
    }
