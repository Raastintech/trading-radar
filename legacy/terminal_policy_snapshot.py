from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict

try:
    from market_breadth_context import (
        DEFAULT_MIN_SYMBOLS_WITH_DATA,
        has_sufficient_market_breadth_coverage,
    )
except Exception:
    DEFAULT_MIN_SYMBOLS_WITH_DATA = 100

    def has_sufficient_market_breadth_coverage(payload, *, min_symbols_with_data=100):
        if not payload:
            return False
        try:
            breadth_pct = payload.get("market_breadth_pct")
            symbols_with_data = int(payload.get("symbols_with_data") or 0)
        except Exception:
            return False
        if breadth_pct is None:
            return False
        return symbols_with_data >= int(min_symbols_with_data)

try:
    from candidate_ranking import MIN_FORECAST_CLOSED_TRADES
except Exception:
    MIN_FORECAST_CLOSED_TRADES = 200

try:
    from shadow_rate_forecaster import MODEL_VERSION as SHADOW_FORECAST_MODEL_VERSION
except Exception:
    SHADOW_FORECAST_MODEL_VERSION = "shadow_rcbr_v2"


DISPLAY_STRATEGIES = ("VOYAGER", "SHORT", "SNIPER", "REMORA", "CONTRARIAN")


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _normalize_strategy(raw: Any) -> str:
    value = str(raw or "").strip().upper()
    if value == "REAPER":
        return "CONTRARIAN"
    return value or "UNKNOWN"


def _load_latest_daily_context(db_path: str) -> Dict[str, Any]:
    if not os.path.exists(db_path):
        return {}
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM daily_context ORDER BY context_date DESC LIMIT 1"
        )
        row = cur.fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}
    finally:
        if conn:
            conn.close()


def _load_closed_trade_counts(db_path: str) -> Dict[str, int]:
    counts: Dict[str, int] = {strategy: 0 for strategy in DISPLAY_STRATEGIES}
    if not os.path.exists(db_path):
        return counts
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT UPPER(COALESCE(strategy, 'UNKNOWN')) AS strategy, COUNT(*) AS cnt
            FROM trades
            WHERE UPPER(COALESCE(status, '')) = 'CLOSED'
            GROUP BY UPPER(COALESCE(strategy, 'UNKNOWN'))
            """
        )
        for row in cur.fetchall():
            strategy = _normalize_strategy(row["strategy"])
            if strategy in counts:
                counts[strategy] += int(row["cnt"] or 0)
    except Exception:
        return counts
    finally:
        if conn:
            conn.close()
    return counts


def load_terminal_policy_snapshot(db_path: str = "trading_performance.db") -> Dict[str, Any]:
    short_live_enabled = _env_enabled("SHORT_LIVE_ENABLED", True)
    closed_trade_counts = _load_closed_trade_counts(db_path)
    breadth_context = _load_latest_daily_context(db_path)

    if has_sufficient_market_breadth_coverage(
        breadth_context,
        min_symbols_with_data=DEFAULT_MIN_SYMBOLS_WITH_DATA,
    ):
        breadth_pct = breadth_context.get("market_breadth_pct")
        try:
            breadth_pct = float(breadth_pct) if breadth_pct is not None else None
        except Exception:
            breadth_pct = None
    else:
        breadth_pct = None

    if breadth_pct is None:
        voyager_breadth_mode = "UNKNOWN"
    elif breadth_pct < 40.0:
        voyager_breadth_mode = "SUPPRESSED"
    elif breadth_pct < 60.0:
        voyager_breadth_mode = "HALF_SIZE"
    else:
        voyager_breadth_mode = "FULL_SIZE"

    forecast_active = [
        strategy for strategy in DISPLAY_STRATEGIES
        if int(closed_trade_counts.get(strategy, 0) or 0) >= int(MIN_FORECAST_CLOSED_TRADES)
    ]
    forecast_suppressed = [
        strategy for strategy in DISPLAY_STRATEGIES
        if strategy not in forecast_active
    ]

    return {
        "short_live_enabled": short_live_enabled,
        "short_execution_mode": "LIVE" if short_live_enabled else "SHADOW_ONLY",
        "short_execution_note": (
            "short execution enabled"
            if short_live_enabled else
            "scan, rank, and log only"
        ),
        "voyager_breadth_pct": breadth_pct,
        "voyager_stock_breadth_pct": breadth_context.get("stock_breadth_pct"),
        "voyager_sector_breadth_pct": breadth_context.get("sector_breadth_pct"),
        "voyager_breadth_mode": voyager_breadth_mode,
        "voyager_breadth_note": (
            "no daily breadth context"
            if breadth_pct is None else
            (
                "Voyager long scan suppressed"
                if breadth_pct < 40.0 else
                "Voyager longs throttled to 50% size"
                if breadth_pct < 60.0 else
                "Voyager breadth gate clear"
            )
        ),
        "daily_context_date": breadth_context.get("context_date"),
        "daily_context": breadth_context,
        "forecast_model_version": SHADOW_FORECAST_MODEL_VERSION,
        "forecast_sample_threshold": int(MIN_FORECAST_CLOSED_TRADES),
        "closed_trade_counts": closed_trade_counts,
        "forecast_active_strategies": forecast_active,
        "forecast_suppressed_strategies": forecast_suppressed,
        "forecast_prior_enabled": bool(forecast_active),
    }
