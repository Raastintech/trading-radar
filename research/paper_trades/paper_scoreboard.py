"""
research/paper_trades/paper_scoreboard.py — Unified paper-validation scoreboard.

Reads:
  - paper_signals / paper_signal_outcomes (generic active-sleeve ledger)
  - voyager_paper_signals (legacy Voyager paper table)

This script reports current paper evidence only. It does not promote strategies
or execute orders.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import core.config as cfg
from core.strategy_registry import (
    active_paper_strategies,
    frozen_strategies,
    is_active_paper_strategy,
    normalize_strategy,
    registry_rows,
    tactical_horizons,
)

ACTIVE_PAPER = {row.key: row.notes for row in registry_rows(active_paper_strategies())}
INACTIVE = {row.key: row.notes for row in registry_rows(frozen_strategies())}

PRIMARY_HORIZON = {
    "SNIPER": 10,
    "SHORT": 3,
    "VOYAGER": 30,
}

TACTICAL_HORIZONS = tactical_horizons()


def _pct(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x:+.2f}%"


def _avg(vals: List[Optional[float]]) -> Optional[float]:
    clean = [float(v) for v in vals if v is not None]
    return statistics.mean(clean) if clean else None


def _win_rate(vals: List[Optional[float]]) -> Optional[float]:
    clean = [float(v) for v in vals if v is not None]
    if not clean:
        return None
    return sum(1 for v in clean if v > 0) / len(clean) * 100


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _rows(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    if not _table_exists(conn, table):
        return []
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    cols = [d[1] for d in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return [dict(zip(cols, row)) for row in rows]


def load_generic(conn: sqlite3.Connection) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    return _rows(conn, "paper_signals"), _rows(conn, "paper_signal_outcomes")


def load_voyager(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = _rows(conn, "voyager_paper_signals")
    converted: List[Dict[str, Any]] = []
    for row in rows:
        converted.append(
            {
                "id": f"voyager:{row.get('id')}",
                "logged_at": row.get("logged_at"),
                "strategy": "VOYAGER",
                "sleeve": "VOYAGER_PAPER",
                "ticker": row.get("ticker"),
                "side": row.get("direction", "LONG"),
                "signal_version": "VOYAGER_PAPER",
                "entry_price": row.get("entry_price"),
                "stop_loss": row.get("stop_loss"),
                "target_price": row.get("target_price"),
                "score": row.get("final_score"),
                "sector": "",
                "status": row.get("signal_status", "open"),
                "outcome_30d": row.get("outcome_30d"),
                "outcome_90d": row.get("outcome_90d"),
                "outcome_180d": row.get("outcome_180d"),
            }
        )
    return converted


def dedupe_open_exposures(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collapse repeated open rows for the same strategy/ticker/side into one
    evidence row. Closed rows are kept as-is.
    """
    result: List[Dict[str, Any]] = []
    seen_open: set[tuple[str, str, str]] = set()
    for row in sorted(rows, key=lambda r: str(r.get("logged_at") or ""), reverse=True):
        status = str(row.get("status", "open")).lower()
        key = (
            normalize_strategy(row.get("strategy", "UNKNOWN")),
            str(row.get("ticker", "UNKNOWN")).upper(),
            str(row.get("side", "")).upper(),
        )
        if status == "open":
            if key in seen_open:
                continue
            seen_open.add(key)
        result.append(row)
    return result


def _outcomes_by_signal(outcomes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in outcomes:
        out[str(row.get("signal_id"))].append(row)
    return out


def _generic_return(signal_id: str, horizon: int, outcomes_by_signal: Dict[str, List[Dict[str, Any]]]) -> Optional[float]:
    for row in outcomes_by_signal.get(signal_id, []):
        if int(row.get("horizon_days") or 0) == horizon:
            if bool(row.get("still_open")):
                return None
            val = row.get("adjusted_return_pct")
            return val if val is not None else row.get("return_pct")
    return None


def _generic_flag(signal_id: str, horizon: int, outcomes_by_signal: Dict[str, List[Dict[str, Any]]], key: str) -> Optional[bool]:
    for row in outcomes_by_signal.get(signal_id, []):
        if int(row.get("horizon_days") or 0) == horizon:
            if bool(row.get("still_open")):
                return None
            val = row.get(key)
            return None if val is None else bool(val)
    return None


def _generic_outcome(signal_id: str, horizon: int, outcomes_by_signal: Dict[str, List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    for row in outcomes_by_signal.get(signal_id, []):
        if int(row.get("horizon_days") or 0) == horizon:
            return row
    return None


def _print_tactical_horizons(
    rows: List[Dict[str, Any]],
    horizons: List[int],
    outcomes_by_signal: Dict[str, List[Dict[str, Any]]],
) -> None:
    for horizon in horizons:
        outcomes = [
            _generic_outcome(str(row.get("id")), horizon, outcomes_by_signal)
            for row in rows
        ]
        outcomes = [o for o in outcomes if o is not None]
        complete = [o for o in outcomes if not bool(o.get("still_open"))]
        adjusted = [
            o.get("adjusted_return_pct") if o.get("adjusted_return_pct") is not None else o.get("return_pct")
            for o in complete
        ]
        stop_hits = [bool(o.get("stop_hit")) for o in complete if o.get("stop_hit") is not None]
        target_hits = [bool(o.get("target_hit")) for o in complete if o.get("target_hit") is not None]
        stop_txt = "N/A" if not stop_hits else f"{sum(stop_hits)}/{len(stop_hits)}"
        target_txt = "N/A" if not target_hits else f"{sum(target_hits)}/{len(target_hits)}"
        print(
            f"  {horizon:>2}d outcomes: completed={len(complete):<4} "
            f"pending={len(outcomes) - len(complete):<4} "
            f"avg_adj={_pct(_avg(adjusted)):<8} "
            f"stop={stop_txt:<7} target={target_txt}"
        )


def _voyager_return(row: Dict[str, Any], horizon: int) -> Optional[float]:
    if horizon == 30:
        return row.get("outcome_30d")
    if horizon == 90:
        return row.get("outcome_90d")
    if horizon == 180:
        return row.get("outcome_180d")
    return None


def _section(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(title)
    print("=" * 72)


def print_scoreboard(db_path: Path) -> None:
    conn = _connect(db_path)
    generic, outcomes = load_generic(conn)
    voyager = load_voyager(conn)
    conn.close()

    outcomes_by_signal = _outcomes_by_signal(outcomes)
    raw_signals = generic + voyager
    signals = dedupe_open_exposures(raw_signals)

    _section("PAPER VALIDATION SCOREBOARD")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"DB: {db_path}")

    print("\nActive paper sleeve set:")
    for strategy, status in ACTIVE_PAPER.items():
        print(f"  {strategy:<10} {status}")
    print("\nInactive / research-only:")
    for strategy, status in INACTIVE.items():
        print(f"  {strategy:<10} {status}")

    if len(raw_signals) != len(signals):
        print(f"\nDuplicate open paper rows collapsed for evidence view: {len(raw_signals)} raw -> {len(signals)} effective")
    print("\nRaw vs effective evidence rows:")
    for strategy in active_paper_strategies():
        raw_count = sum(1 for row in raw_signals if normalize_strategy(row.get("strategy", "")) == strategy)
        effective_count = sum(1 for row in signals if normalize_strategy(row.get("strategy", "")) == strategy)
        print(f"  {strategy:<10} {raw_count:>4} raw / {effective_count:>4} effective")

    if not signals:
        print("\nNo paper signals found yet.")
        return

    by_strategy: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for sig in signals:
        by_strategy[normalize_strategy(sig.get("strategy", "UNKNOWN"))].append(sig)

    _section("PER-STRATEGY VIEW")
    for strategy in active_paper_strategies():
        rows = by_strategy.get(strategy, [])
        horizon = PRIMARY_HORIZON[strategy]
        returns: List[Optional[float]] = []
        stop_hits: List[bool] = []
        target_hits: List[bool] = []
        for row in rows:
            if strategy == "VOYAGER":
                returns.append(_voyager_return(row, horizon))
            else:
                sid = str(row.get("id"))
                returns.append(_generic_return(sid, horizon, outcomes_by_signal))
                sh = _generic_flag(sid, horizon, outcomes_by_signal, "stop_hit")
                th = _generic_flag(sid, horizon, outcomes_by_signal, "target_hit")
                if sh is not None:
                    stop_hits.append(sh)
                if th is not None:
                    target_hits.append(th)

        measured = [r for r in returns if r is not None]
        open_rows = [r for r in rows if str(r.get("status", "open")).lower() == "open"]
        blocked_rows = [r for r in rows if str(r.get("status", "")).lower() == "governance_blocked"]
        observe_rows = [r for r in rows if str(r.get("status", "")).lower() == "observe_only"]
        closed_rows = [
            r for r in rows
            if str(r.get("status", "open")).lower() not in ("open", "governance_blocked", "observe_only")
        ]
        print(f"\n{strategy} ({ACTIVE_PAPER[strategy]})")
        print(f"  Signals:        {len(rows)}")
        print(f"  Open:           {len(open_rows)}")
        print(f"  Closed:         {len(closed_rows)}")
        print(f"  Gov blocked:    {len(blocked_rows)}")
        print(f"  Observe-only:   {len(observe_rows)}")
        print(f"  Primary horizon:{horizon}d")
        print(f"  Measured:       {len(measured)}")
        print(f"  Win rate:       {_pct(_win_rate(returns))}")
        print(f"  Avg return:     {_pct(_avg(returns))}")
        if stop_hits:
            print(f"  Stop-hit:       {sum(stop_hits) / len(stop_hits) * 100:.1f}%")
        else:
            print("  Stop-hit:       N/A")
        if target_hits:
            print(f"  Target-hit:     {sum(target_hits) / len(target_hits) * 100:.1f}%")
        else:
            print("  Target-hit:     N/A")
        if strategy in TACTICAL_HORIZONS:
            _print_tactical_horizons(rows, TACTICAL_HORIZONS[strategy], outcomes_by_signal)

    _section("COMBINED PORTFOLIO VIEW")
    raw_active_rows = [s for s in raw_signals if is_active_paper_strategy(s.get("strategy", ""))]
    active_rows = [s for s in signals if is_active_paper_strategy(s.get("strategy", ""))]
    print(f"Total active-paper signals: {len(active_rows)} effective ({len(raw_active_rows)} raw rows)")
    print(f"Current open paper trades:  {sum(1 for s in active_rows if str(s.get('status', 'open')).lower() == 'open')}")
    print(f"Governance-blocked signals:{sum(1 for s in active_rows if str(s.get('status', '')).lower() == 'governance_blocked')}")
    print(f"Observe-only signals:      {sum(1 for s in active_rows if str(s.get('status', '')).lower() == 'observe_only')}")

    by_ticker: Dict[str, int] = defaultdict(int)
    by_sector: Dict[str, int] = defaultdict(int)
    for row in active_rows:
        by_ticker[str(row.get("ticker", "UNKNOWN")).upper()] += 1
        sector = str(row.get("sector") or "UNKNOWN")
        by_sector[sector] += 1

    print("\nTop tickers:")
    for ticker, count in sorted(by_ticker.items(), key=lambda kv: (-kv[1], kv[0]))[:10]:
        print(f"  {ticker:<8} {count}")

    print("\nTop sectors:")
    for sector, count in sorted(by_sector.items(), key=lambda kv: (-kv[1], kv[0]))[:10]:
        print(f"  {sector:<16} {count}")

    duplicate_keys: Dict[tuple[str, str, str], int] = defaultdict(int)
    for row in active_rows:
        if str(row.get("status", "open")).lower() != "open":
            continue
        key = (
            normalize_strategy(row.get("strategy", "UNKNOWN")),
            str(row.get("ticker", "UNKNOWN")).upper(),
            str(row.get("side", "")).upper(),
        )
        duplicate_keys[key] += 1
    duplicates = [(key, count) for key, count in duplicate_keys.items() if count > 1]
    if duplicates:
        print("\nGovernance warnings:")
        for (strategy, ticker, side), count in sorted(duplicates, key=lambda kv: (-kv[1], kv[0]))[:10]:
            print(f"  duplicate open exposure: {strategy} {ticker} {side} count={count}")

    _section("RECENT OPEN PAPER TRADES")
    for row in sorted(active_rows, key=lambda r: str(r.get("logged_at") or ""), reverse=True)[:12]:
        if str(row.get("status", "open")).lower() != "open":
            continue
        features = row.get("key_features")
        if isinstance(features, str):
            try:
                features = json.loads(features)
            except Exception:
                features = {}
        print(
            f"{str(row.get('logged_at', ''))[:19]}  {row.get('strategy'):<8} "
            f"{row.get('ticker'):<6} {row.get('side', ''):<5} "
            f"entry={row.get('entry_price')} score={row.get('score')} "
            f"version={row.get('signal_version')}"
        )

    _section("RECENT BLOCKED / OBSERVE-ONLY PAPER SIGNALS")
    blocked_or_observe = [
        r for r in active_rows
        if str(r.get("status", "")).lower() in ("governance_blocked", "observe_only")
    ]
    for row in sorted(blocked_or_observe, key=lambda r: str(r.get("logged_at") or ""), reverse=True)[:12]:
        print(
            f"{str(row.get('logged_at') or '')[:19]}  {row.get('strategy'):<8} "
            f"{row.get('ticker'):<6} status={row.get('status')} notes={row.get('notes') or ''}"
        )

    _section("RECENT CLOSED PAPER TRADES")
    closed = [
        r for r in active_rows
        if str(r.get("status", "open")).lower() not in ("open", "governance_blocked", "observe_only")
    ]
    for row in sorted(closed, key=lambda r: str(r.get("exit_date") or r.get("logged_at") or ""), reverse=True)[:12]:
        print(
            f"{str(row.get('exit_date') or '')[:10]}  {row.get('strategy'):<8} "
            f"{row.get('ticker'):<6} status={row.get('status')} exit={row.get('exit_price')}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified paper-validation scoreboard")
    parser.add_argument("--db", type=Path, default=cfg.DB_PATH, help="SQLite DB path")
    args = parser.parse_args()
    print_scoreboard(args.db)


if __name__ == "__main__":
    main()
