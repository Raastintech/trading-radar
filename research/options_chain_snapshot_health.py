#!/usr/bin/env python3
"""
research/options_chain_snapshot_health.py - Phase 1J.2 collection health.

Cache-only deadman check for the daily options chain snapshot collection.
Reads data/options_snapshots/ directories and the quality sidecar only —
never calls providers, never touches the DB, never emits signals.

Status ladder:
  OK            — latest snapshot matches the expected trading day
  MISSING_TODAY — today is a trading day past the collection window and
                  today's snapshot does not exist
  STALE         — the latest snapshot is 2+ trading days behind expectation
  ERROR         — no snapshots exist at all (or the store is unreadable)

Outputs:
  - cache/research/options_chain_snapshot_health_latest.json
  - logs/options_chain_snapshot_health_latest.txt
  - docs/research/OPTIONS_CHAIN_SNAPSHOT_HEALTH.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_ROOT = ROOT / "data" / "options_snapshots"
QUALITY_JSON = ROOT / "cache" / "research" / "options_chain_snapshot_quality_latest.json"
OUT_JSON = ROOT / "cache" / "research" / "options_chain_snapshot_health_latest.json"
OUT_TXT = ROOT / "logs" / "options_chain_snapshot_health_latest.txt"
OUT_DOC = ROOT / "docs" / "research" / "OPTIONS_CHAIN_SNAPSHOT_HEALTH.md"

VERSION = "OPTIONS_CHAIN_SNAPSHOT_HEALTH_V1"
STRATEGY_STATUS = "DATA_COLLECTION_ONLY"

# The timer fires 15:45 ET Mon-Fri (19:45 UTC in June). Before this UTC hour
# we do not expect today's snapshot yet and grade against the prior day.
COLLECTION_DONE_UTC_HOUR = 21

IVR_PARTIAL_DAYS = 60
IVR_FEASIBLE_DAYS = 120


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def trading_calendar() -> Optional[pd.DatetimeIndex]:
    """Real trading calendar from the retained SPY bars (cache-only); falls
    back to business days when the price cache is unavailable."""
    for probe in (ROOT / "cache" / "prices_deep" / "SPY.parquet", ROOT / "cache" / "prices" / "SPY.parquet"):
        if probe.exists():
            try:
                idx = pd.read_parquet(probe).index
                return pd.DatetimeIndex(sorted(pd.Timestamp(x).normalize() for x in idx.unique()))
            except Exception:
                continue
    return None


def _is_trading_day(day: pd.Timestamp, cal: Optional[pd.DatetimeIndex]) -> bool:
    if cal is not None and day <= cal.max():
        return day in cal
    return day.dayofweek < 5


def _prev_trading_day(day: pd.Timestamp, cal: Optional[pd.DatetimeIndex]) -> pd.Timestamp:
    d = day - pd.Timedelta(days=1)
    while not _is_trading_day(d, cal):
        d -= pd.Timedelta(days=1)
    return d


def _trading_days_between(first: pd.Timestamp, last: pd.Timestamp, cal: Optional[pd.DatetimeIndex]) -> int:
    if cal is not None and first >= cal.min() and last <= cal.max():
        return int(((cal >= first) & (cal <= last)).sum())
    return len(pd.bdate_range(first, last))


def snapshot_days() -> List[str]:
    if not SNAPSHOT_ROOT.exists():
        return []
    out = []
    for p in sorted(SNAPSHOT_ROOT.glob("*")):
        if p.is_dir() and any(p.glob("*.parquet")):
            out.append(p.name)
    return out


def build_report(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _utc_now()
    today = pd.Timestamp(now.date())
    cal = trading_calendar()
    days = snapshot_days()

    quality: Dict[str, Any] = {}
    if QUALITY_JSON.exists():
        try:
            q = json.loads(QUALITY_JSON.read_text())
            quality = {
                "generated_at": q.get("generated_at"),
                "symbols": q.get("symbols_collected"),
                "contracts_latest_day": q.get("total_contracts_latest_day"),
                "usable_symbols": [
                    sym for sym, row in (q.get("per_symbol") or {}).items()
                    if (row.get("verdict") or {}).get("usable_for_future_backtesting")
                ],
            }
        except Exception:
            quality = {"error": "quality sidecar unreadable"}

    # Expected snapshot day: today once the collection window has passed on a
    # trading day; otherwise the most recent prior trading day.
    today_is_trading = _is_trading_day(today, cal)
    today_pending = today_is_trading and now.hour < COLLECTION_DONE_UTC_HOUR
    if today_is_trading and not today_pending:
        expected = today
    else:
        expected = _prev_trading_day(today, cal)
    expected_str = str(expected.date())

    if not days:
        status = "ERROR"
        detail = "no snapshots exist under data/options_snapshots/"
        last = None
        missed = None
        collected_days = 0
    else:
        last = days[-1]
        collected_days = len(days)
        last_ts = pd.Timestamp(last)
        first_ts = pd.Timestamp(days[0])
        expected_span = _trading_days_between(first_ts, expected, cal)
        missed = max(0, expected_span - collected_days)
        if last >= expected_str:
            status = "OK"
            detail = "latest snapshot matches the expected trading day"
        elif expected_str == str(today.date()) and last == str(_prev_trading_day(today, cal).date()):
            status = "MISSING_TODAY"
            detail = "today's snapshot has not been written yet (timer missed or failed today)"
        else:
            gap = _trading_days_between(last_ts, expected, cal) - 1
            status = "STALE" if gap >= 2 else "MISSING_TODAY"
            detail = f"latest snapshot {last} is {gap} trading day(s) behind expected {expected_str}"

    symbols_latest: List[str] = []
    contracts_latest = 0
    if days:
        latest_dir = SNAPSHOT_ROOT / days[-1]
        for f in sorted(latest_dir.glob("*.parquet")):
            symbols_latest.append(f.stem.upper())
        contracts_latest = int(quality.get("contracts_latest_day") or 0)

    days_to_partial = max(0, IVR_PARTIAL_DAYS - len(days))
    days_to_feasible = max(0, IVR_FEASIBLE_DAYS - len(days))

    def _eta(n_days: int) -> Optional[str]:
        if n_days <= 0:
            return "unlocked"
        return str((today + pd.tseries.offsets.BDay(n_days)).date())

    return {
        "kind": "options_chain_snapshot_health",
        "version": VERSION,
        "generated_at": now.isoformat(),
        "research_only": True,
        "strategy_status": STRATEGY_STATUS,
        "status": status,
        "detail": detail,
        "last_snapshot_date": last,
        "expected_snapshot_date": expected_str,
        "today_is_trading_day": today_is_trading,
        "today_pending_collection_window": today_pending,
        "todays_snapshot_exists": bool(days and days[-1] == str(today.date())),
        "snapshot_days_collected": collected_days,
        "missed_day_count": missed,
        "symbols_latest_day": symbols_latest,
        "contracts_latest_day": contracts_latest,
        "quality_summary": quality,
        "ivr_gates": {
            "partial_days_required": IVR_PARTIAL_DAYS,
            "feasible_days_required": IVR_FEASIBLE_DAYS,
            "days_until_partial": days_to_partial,
            "days_until_feasible": days_to_feasible,
            "partial_eta": _eta(days_to_partial),
            "feasible_eta": _eta(days_to_feasible),
        },
        "safety": {
            "cache_only": True,
            "no_provider_calls": True,
            "no_paper_signals": True,
            "no_broker_orders": True,
            "no_production_changes": True,
            "short_a_remains_frozen": True,
        },
    }


def render_text(res: Dict[str, Any]) -> List[str]:
    g = res["ivr_gates"]
    return [
        f"OPTIONS SNAPSHOT HEALTH (PHASE 1J.2) - {res['generated_at']}",
        f"status={res['status']} ({res['detail']})",
        f"strategy_status={res['strategy_status']}",
        f"last_snapshot={res['last_snapshot_date']} expected={res['expected_snapshot_date']} "
        f"today_pending={res['today_pending_collection_window']}",
        f"days_collected={res['snapshot_days_collected']} missed={res['missed_day_count']} "
        f"symbols_latest={len(res['symbols_latest_day'])} contracts_latest={res['contracts_latest_day']}",
        f"ivr gates: partial in {g['days_until_partial']}d (~{g['partial_eta']}), "
        f"feasible in {g['days_until_feasible']}d (~{g['feasible_eta']})",
    ]


def render_doc(res: Dict[str, Any]) -> str:
    g = res["ivr_gates"]
    q = res.get("quality_summary") or {}
    return "\n".join([
        "# Options Chain Snapshot Health (Phase 1J.2)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        f"Status: **{res['status']}** — {res['detail']}",
        "",
        f"- Strategy status: `{res['strategy_status']}` (no strategy, no signals, no orders)",
        f"- Last snapshot: `{res['last_snapshot_date']}` (expected `{res['expected_snapshot_date']}`)",
        f"- Snapshot days collected: {res['snapshot_days_collected']} (missed: {res['missed_day_count']})",
        f"- Latest day: {len(res['symbols_latest_day'])} symbols, {res['contracts_latest_day']} contracts "
        f"({', '.join(res['symbols_latest_day']) or 'none'})",
        f"- Usable symbols (per-day quality floors): {', '.join(q.get('usable_symbols') or []) or 'n/a'}",
        f"- IVR PARTIAL gate ({g['partial_days_required']}d): {g['days_until_partial']} trading days away (~{g['partial_eta']})",
        f"- IVR FEASIBLE gate ({g['feasible_days_required']}d): {g['days_until_feasible']} trading days away (~{g['feasible_eta']})",
        "",
        "Cache-only health check; no provider calls. Driven by `options-chain-snapshot[-quality]` runner commands.",
        "",
    ])


def write_outputs(res: Dict[str, Any]) -> None:
    for p in (OUT_JSON, OUT_TXT, OUT_DOC):
        p.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render_doc(res) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1J.2 options snapshot health (cache-only)")
    ap.parse_args(argv)
    res = build_report()
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0 if res["status"] in {"OK", "MISSING_TODAY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
