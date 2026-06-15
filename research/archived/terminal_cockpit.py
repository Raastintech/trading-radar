from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


_STRATEGY_ALIASES = {
    "VOY": "VOYAGER",
    "VOYAGER": "VOYAGER",
    "SNP": "SNIPER",
    "SNIPER": "SNIPER",
    "REM": "REMORA",
    "REMORA": "REMORA",
    "SHRT": "SHORT",
    "SHORT": "SHORT",
    "RPR": "CONTRARIAN",
    "REAPER": "CONTRARIAN",
    "CONTRARIAN": "CONTRARIAN",
}


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _normalize_strategy(raw: object) -> str:
    value = str(raw or "").strip().upper()
    return _STRATEGY_ALIASES.get(value, value or "UNK")


def compact_reason(reason: object, max_len: int = 34) -> str:
    text = str(reason or "").strip()
    if not text:
        return "—"
    for token in (
        "DUPLICATE_TICKER_OPEN:",
        "CIRCUIT_BREAKER:",
        "ALLOCATION_ZERO:",
        "failed_fatal_check_",
    ):
        if text.startswith(token):
            text = text[len(token):].strip()
    text = text.replace("_", " ")
    return text[:max_len]


def meter_bar(value: Optional[float], maximum: Optional[float], width: int = 10) -> str:
    width = max(4, int(width or 10))
    value_f = max(0.0, _safe_float(value, 0.0))
    maximum_f = max(0.0, _safe_float(maximum, 0.0))
    if maximum_f <= 0:
        return "░" * width
    ratio = min(1.0, value_f / maximum_f)
    filled = max(0, min(width, int(round(ratio * width))))
    return ("█" * filled) + ("░" * (width - filled))


def percent_meter(value_pct: Optional[float], width: int = 10) -> str:
    return meter_bar(_safe_float(value_pct, 0.0), 100.0, width=width)


def fetch_recent_blocked_decisions(
    db_path: str,
    *,
    lookback_hours: int = 24,
    limit: int = 6,
) -> List[Dict[str, object]]:
    if not db_path:
        return []
    cutoff = (datetime.now() - timedelta(hours=int(lookback_hours))).isoformat()
    conn = None
    rows: List[Dict[str, object]] = []
    seen: set[Tuple[str, str]] = set()
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        query = """
            SELECT
                timestamp,
                ticker,
                strategy,
                council_decision,
                COALESCE(execution_deny_reason, council_reason, '') AS reason,
                avg_score,
                rr
            FROM decisions
            WHERE timestamp >= ?
              AND (
                    execution_denied = 1
                 OR council_decision = 'PORTFOLIO_DENIED'
              )
            ORDER BY timestamp DESC, id DESC
            LIMIT 200
        """
        for row in conn.execute(query, (cutoff,)).fetchall():
            ticker = str(row["ticker"] or "").upper().strip()
            reason = compact_reason(row["reason"])
            key = (ticker, reason)
            if not ticker or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "timestamp": row["timestamp"],
                    "ticker": ticker,
                    "strategy": _normalize_strategy(row["strategy"]),
                    "decision": str(row["council_decision"] or ""),
                    "reason": reason,
                    "score": _safe_float(row["avg_score"], 0.0),
                    "rr": _safe_float(row["rr"], 0.0),
                }
            )
            if len(rows) >= int(limit):
                break
    except Exception:
        return []
    finally:
        if conn:
            conn.close()
    return rows


def fetch_recent_watch_decisions(
    db_path: str,
    *,
    lookback_hours: int = 24,
    limit: int = 6,
) -> List[Dict[str, object]]:
    if not db_path:
        return []
    cutoff = (datetime.now() - timedelta(hours=int(lookback_hours))).isoformat()
    conn = None
    rows: List[Dict[str, object]] = []
    seen: set[Tuple[str, str]] = set()
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        query = """
            SELECT
                timestamp,
                ticker,
                strategy,
                council_decision,
                COALESCE(execution_deny_reason, council_reason, '') AS reason,
                avg_score,
                rr
            FROM decisions
            WHERE timestamp >= ?
              AND (
                    council_decision IN ('PORTFOLIO_DENIED', 'EXECUTION_DENIED')
                 OR (council_decision = 'SCANNER_REJECT' AND LOWER(COALESCE(council_reason, '')) = 'consider')
              )
            ORDER BY
                CASE council_decision
                    WHEN 'PORTFOLIO_DENIED' THEN 0
                    WHEN 'EXECUTION_DENIED' THEN 1
                    ELSE 2
                END,
                avg_score DESC,
                rr DESC,
                timestamp DESC,
                id DESC
            LIMIT 200
        """
        for row in conn.execute(query, (cutoff,)).fetchall():
            ticker = str(row["ticker"] or "").upper().strip()
            strategy = _normalize_strategy(row["strategy"])
            key = (ticker, strategy)
            if not ticker or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "timestamp": row["timestamp"],
                    "ticker": ticker,
                    "strategy": strategy,
                    "decision": str(row["council_decision"] or ""),
                    "reason": compact_reason(row["reason"]),
                    "score": _safe_float(row["avg_score"], 0.0),
                    "rr": _safe_float(row["rr"], 0.0),
                }
            )
            if len(rows) >= int(limit):
                break
    except Exception:
        return []
    finally:
        if conn:
            conn.close()
    return rows


def build_action_buckets(
    opportunities: Sequence[Dict[str, object]],
    *,
    blocked_rows: Optional[Sequence[Dict[str, object]]] = None,
    stale_cutoff_ts: Optional[float] = None,
    ready_limit: int = 4,
    watch_limit: int = 4,
    blocked_limit: int = 4,
) -> Dict[str, List[Dict[str, object]]]:
    ready: List[Dict[str, object]] = []
    watch: List[Dict[str, object]] = []
    blocked = list(blocked_rows or [])
    seen: set[Tuple[str, str, str]] = set()

    for raw in list(opportunities or []):
        ticker = str(raw.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        ts = raw.get("_ts")
        if stale_cutoff_ts is not None and ts not in (None, ""):
            try:
                if float(ts) < float(stale_cutoff_ts):
                    continue
            except Exception:
                pass

        signal = str(raw.get("signal") or "").strip()
        strategy = _normalize_strategy(raw.get("strategy") or raw.get("source_strategy") or "")
        score = _safe_float(raw.get("score"), 0.0)
        rr = _safe_float(raw.get("rr") or raw.get("risk_reward"), 0.0)
        approved = bool(raw.get("approved")) or str(raw.get("conviction") or "").upper() == "MAXIMUM"
        detail = signal or compact_reason(raw.get("detail") or raw.get("reason") or raw.get("conviction") or "watch")
        key = (ticker, strategy or "UNK", detail)
        if key in seen:
            continue
        seen.add(key)
        payload = {
            "ticker": ticker,
            "strategy": strategy or "UNK",
            "score": score,
            "rr": rr,
            "detail": detail[:40],
            "approved": approved,
            "time": str(raw.get("time") or "")[:8],
        }
        if approved:
            ready.append(payload)
        else:
            watch.append(payload)

    sort_key = lambda item: (
        -_safe_float(item.get("score"), 0.0),
        -_safe_float(item.get("rr"), 0.0),
        str(item.get("ticker") or ""),
    )
    ready = sorted(ready, key=sort_key)[: int(ready_limit)]
    watch = sorted(watch, key=sort_key)[: int(watch_limit)]
    blocked = list(blocked)[: int(blocked_limit)]
    return {"ready": ready, "watch": watch, "blocked": blocked}


def compute_scan_delta(
    opportunities: Sequence[Dict[str, object]],
    previous_snapshot: Optional[Dict[Tuple[str, str], Dict[str, float]]] = None,
    *,
    stale_cutoff_ts: Optional[float] = None,
    limit: int = 4,
) -> Tuple[List[Dict[str, str]], Dict[Tuple[str, str], Dict[str, float]]]:
    current: Dict[Tuple[str, str], Dict[str, float]] = {}
    for raw in list(opportunities or []):
        ticker = str(raw.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        ts = raw.get("_ts")
        if stale_cutoff_ts is not None and ts not in (None, ""):
            try:
                if float(ts) < float(stale_cutoff_ts):
                    continue
            except Exception:
                pass
        strategy = _normalize_strategy(raw.get("strategy") or raw.get("source_strategy") or "")
        key = (ticker, strategy or "UNK")
        score = _safe_float(raw.get("score"), 0.0)
        rr = _safe_float(raw.get("rr") or raw.get("risk_reward"), 0.0)
        current[key] = {"score": score, "rr": rr}

    previous = previous_snapshot or {}
    rows: List[Dict[str, str]] = []

    for key, payload in current.items():
        if key not in previous:
            rows.append({"kind": "NEW", "label": f"{key[0]} {key[1][:4]}", "detail": f"s{payload['score']:.0f} rr{payload['rr']:.1f}"})

    for key in previous:
        if key not in current:
            rows.append({"kind": "DROP", "label": f"{key[0]} {key[1][:4]}", "detail": "left board"})

    for key, payload in current.items():
        if key not in previous:
            continue
        prev_score = _safe_float(previous[key].get("score"), 0.0)
        delta_score = payload["score"] - prev_score
        if delta_score >= 2.0:
            rows.append({"kind": "UP", "label": f"{key[0]} {key[1][:4]}", "detail": f"+{delta_score:.0f} pts"})
        elif delta_score <= -2.0:
            rows.append({"kind": "DOWN", "label": f"{key[0]} {key[1][:4]}", "detail": f"{delta_score:.0f} pts"})

    priority = {"NEW": 0, "UP": 1, "DOWN": 2, "DROP": 3}
    rows = sorted(rows, key=lambda row: (priority.get(row["kind"], 9), row["label"]))[: int(limit)]
    return rows, current
