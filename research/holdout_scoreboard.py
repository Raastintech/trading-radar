#!/usr/bin/env python3
"""
research/holdout_scoreboard.py - read-only 2026 H2 holdout monitor.

This report is monitoring only. It reads the pre-registered holdout covenant
and current paper evidence, then writes a daily status artifact. It does not
retune, reinterpret, promote, demote, trade, or mutate any evidence table.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.strategy_registry import active_paper_strategies, normalize_strategy, paper_ledger_strategies, registry_rows  # noqa: E402

HOLDOUT_DOC = ROOT / "docs" / "research" / "PRE_REGISTERED_HOLDOUT_2026H2.md"
WINDOW_START = date(2026, 6, 1)
WINDOW_END = date(2026, 12, 1)
DEFAULT_DB = ROOT / "db" / "trading.db"
DEFAULT_TEXT = ROOT / "logs" / "holdout_2026h2_scoreboard_latest.txt"
DEFAULT_JSON = ROOT / "cache" / "research" / "holdout_2026h2_scoreboard_latest.json"

FROZEN_PARAMETERS: Dict[str, Dict[str, Any]] = {
    "governance": {
        "MAX_POSITION_PCT": 0.02,
        "MAX_DAILY_LOSS_PCT": 0.05,
        "ALLOW_SHORTS": True,
        "PAPER_TRADING": "env true at registration",
    },
    "SNIPER": {
        "VOL_SPIKE_THRESH": 1.4,
        "MIN_SCORE": 70,
        "MIN_RRR": 2.5,
        "BARS_NEEDED": 75,
        "MA50_SLOPE_BARS": 20,
        "ATR_CONTRACTION_THRESH": 0.85,
        "STOP_ATR_MULT": 1.5,
        "TARGET_ATR_MULT": 3.75,
        "VIX_REGIME_CEILING": 28.0,
        "SPY_BARS_NEEDED": 220,
        "LARGE_CAP_UNIVERSE": "82-name whitelist sealed 2026-05-07",
    },
    "VOYAGER": {
        "MIN_PRICE": 5.0,
        "MIN_AVG_DOLLAR_VOL": 5_000_000,
        "MAX_EXTENSION_MA50": 0.12,
        "MA200_FLOOR": 0.92,
        "RS_50_WINDOW": 50,
        "RS_130_WINDOW": 130,
        "DVOL_TREND_RATIO": 0.85,
        "EARNINGS_SAFE_DAYS": 15,
        "MIN_FUNDAMENTAL_SCORE": 40,
        "MIN_FUNDAMENTAL_SCORE_EARLY": 55,
        "MIN_SCORE": 65,
        "MIN_RRR": 2.5,
        "BARS_NEEDED": 260,
        "BASE_MAX_PRICE_TIGHT": 0.03,
        "BASE_MAX_DIST_MA50": 0.05,
    },
    "SHORT_A": {
        "REACTION_MIN_PCT": -3.0,
        "VOL_SPIKE": 1.5,
        "MAX_LAG_SESSIONS": 3,
        "MIN_SCORE": 55,
        "MIN_RRR": 2.0,
        "BARS_NEEDED": 30,
    },
    "H3": {
        "H3_SCORE_LO": 80.0,
        "H3_SCORE_HI": 90.0,
        "H3_VIX_LO": 15.0,
        "H3_VIX_HI": 20.0,
        "H3_VOL_RATIO_MAX": 1.5,
        "H3_SECTORS": ["Healthcare", "Communications", "Technology"],
    },
}

ACCEPTANCE_CRITERIA = [
    ">= 30 closed trades inside [2026-06-01, 2026-12-01)",
    "95% bootstrap lower-CI win rate > 50%, frictioned at 0.30% RT",
    "Observed WR beats same-window random-entry control by >= 5pp and lower CI delta > 0",
    "Regime-conditioned WR is not more than 5pp below ungated WR",
    "H3 additionally needs WR >= 55% and >= 5pp lift over same-period anti-cohort",
]


def _iso_date(value: Any) -> Optional[date]:
    if not value:
        return None
    raw = str(value)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return date.fromisoformat(raw[:10])
        except Exception:
            return None


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


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


def _in_window(ts: Any) -> bool:
    d = _iso_date(ts)
    return bool(d and WINDOW_START <= d < WINDOW_END)


def _avg(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [float(v) for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _win_rate(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return None
    return sum(1 for v in clean if v > 0) / len(clean) * 100.0


def _pct(value: Optional[float]) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def _summarize_evidence(db_path: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {
        k: {
            "raw_signals": 0,
            "open": 0,
            "closed": 0,
            "governance_blocked": 0,
            "primary_horizon_closed": 0,
            "primary_horizon_win_rate_pct": None,
            "primary_horizon_avg_return_pct": None,
        }
        for k in paper_ledger_strategies()
    }
    if not db_path.exists():
        return out

    try:
        conn = _connect_readonly(db_path)
    except sqlite3.Error:
        return out
    try:
        generic = [r for r in _rows(conn, "paper_signals") if _in_window(r.get("logged_at"))]
        outcomes = _rows(conn, "paper_signal_outcomes")
        by_signal: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in outcomes:
            by_signal[str(row.get("signal_id"))].append(row)

        primary = {"SNIPER": 10, "SHORT": 3, "VOYAGER": 30}
        for row in generic:
            strategy = normalize_strategy(row.get("strategy"))
            if strategy not in out:
                continue
            status = str(row.get("status") or "open").lower()
            out[strategy]["raw_signals"] += 1
            if status == "open":
                out[strategy]["open"] += 1
            elif status == "governance_blocked":
                out[strategy]["governance_blocked"] += 1
            else:
                out[strategy]["closed"] += 1

            horizon = primary.get(strategy)
            returns = []
            for outcome in by_signal.get(str(row.get("id")), []):
                if int(outcome.get("horizon_days") or 0) != horizon:
                    continue
                if bool(outcome.get("still_open")):
                    continue
                val = outcome.get("adjusted_return_pct")
                returns.append(val if val is not None else outcome.get("return_pct"))
            if returns:
                out[strategy].setdefault("_returns", []).extend(returns)

        voyager = [r for r in _rows(conn, "voyager_paper_signals") if _in_window(r.get("logged_at"))]
        if "VOYAGER" in out:
            for row in voyager:
                status = str(row.get("signal_status") or "open").lower()
                out["VOYAGER"]["raw_signals"] += 1
                if status == "open":
                    out["VOYAGER"]["open"] += 1
                else:
                    out["VOYAGER"]["closed"] += 1
                if row.get("outcome_30d") is not None:
                    out["VOYAGER"].setdefault("_returns", []).append(row.get("outcome_30d"))
    finally:
        conn.close()

    for row in out.values():
        returns = row.pop("_returns", [])
        row["primary_horizon_closed"] = len(returns)
        row["primary_horizon_win_rate_pct"] = _win_rate(returns)
        row["primary_horizon_avg_return_pct"] = _avg(returns)
    return out


def build_report(*, db_path: Path, as_of: date) -> Dict[str, Any]:
    total_days = (WINDOW_END - WINDOW_START).days
    elapsed = 0
    remaining = total_days
    if as_of >= WINDOW_START:
        elapsed = min(total_days, max(0, (min(as_of, WINDOW_END) - WINDOW_START).days))
        remaining = max(0, (WINDOW_END - min(max(as_of, WINDOW_START), WINDOW_END)).days)
    days_until_start = max(0, (WINDOW_START - as_of).days)
    sleeves = [
        {
            "key": row.key,
            "display_name": row.display_name,
            "status": row.status,
            "baseline_tag": row.baseline_tag,
        }
        for row in registry_rows()
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "holdout_doc": str(HOLDOUT_DOC),
        "window_start": WINDOW_START.isoformat(),
        "window_end_exclusive": WINDOW_END.isoformat(),
        "days_total": total_days,
        "days_elapsed": elapsed,
        "days_remaining": remaining,
        "days_until_start": days_until_start,
        "active_sleeves": [r for r in sleeves if r["status"] == "active_paper"],
        "all_registered_sleeves": sleeves,
        "frozen_parameters": FROZEN_PARAMETERS,
        "acceptance_criteria": ACCEPTANCE_CRITERIA,
        "evidence_summary": _summarize_evidence(db_path),
        "monitoring_only": True,
    }


def render_text(report: Dict[str, Any]) -> str:
    lines = [
        "PRE-REGISTERED HOLDOUT 2026 H2 - DAILY STATUS",
        "=" * 72,
        f"Generated: {report['generated_at']}",
        f"As of:     {report['as_of']}",
        f"Window:    {report['window_start']} -> {report['window_end_exclusive']} exclusive",
        f"Days:      elapsed={report['days_elapsed']} remaining={report['days_remaining']} total={report['days_total']}",
        f"Starts in: {report['days_until_start']} day(s)" if report["days_until_start"] else "Starts in: active or complete",
        f"Doc:       {report['holdout_doc']}",
        "",
        "Active holdout sleeves:",
    ]
    for row in report["active_sleeves"]:
        lines.append(f"  {row['key']:<8} {row['status']:<13} baseline={row['baseline_tag']}")

    lines.extend(["", "Frozen parameter snapshot:"])
    for group, params in report["frozen_parameters"].items():
        lines.append(f"  {group}:")
        for key, value in params.items():
            lines.append(f"    {key} = {value}")

    lines.extend(["", "Acceptance criteria (monitoring only; final scoring after 2026-12-01):"])
    for idx, item in enumerate(report["acceptance_criteria"], start=1):
        lines.append(f"  {idx}. {item}")

    lines.extend(["", "Current evidence summary inside holdout window:"])
    for strategy, row in report["evidence_summary"].items():
        lines.append(
            f"  {strategy:<8} raw={row['raw_signals']:<3} open={row['open']:<3} "
            f"closed={row['closed']:<3} governance_blocked={row['governance_blocked']:<3} "
            f"primary_closed={row['primary_horizon_closed']:<3} "
            f"wr={_pct(row['primary_horizon_win_rate_pct'])} "
            f"avg={_pct(row['primary_horizon_avg_return_pct'])}"
        )

    lines.extend([
        "",
        "No retuning, no interpretation changes, no live promotion.",
    ])
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--as-of", type=str, default=date.today().isoformat())
    parser.add_argument("--text-out", type=Path, default=DEFAULT_TEXT)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    as_of = date.fromisoformat(args.as_of)
    report = build_report(db_path=args.db, as_of=as_of)
    text = render_text(report)
    print(text, end="")

    if not args.no_write:
        args.text_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.text_out.write_text(text, encoding="utf-8")
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
