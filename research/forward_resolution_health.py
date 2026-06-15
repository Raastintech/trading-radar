#!/usr/bin/env python3
"""
research/forward_resolution_health.py — Phase 1G.3 T4

Diagnostic for the forward-outcome resolvers (regime forecast + stock lens).

The Strategy Truth Review flagged "0 of 58 forecast snapshots matured" and many
open lens snapshots. This report determines WHETHER the resolver is broken or the
snapshots are simply too young, and surfaces the next maturity date plus any rows
blocked on missing price data.

Maturation contract (from core.forecast_forward_tracker): a snapshot is "matured"
only once its LONGEST horizon completes, which needs
``int(max_horizon * 1.5) + 1`` calendar days AND the final-horizon return present.
Short-horizon (1d/5d/10d) outcomes are filled before maturation; they are visible
here via the per-horizon fill counts so a "0 matured" headline is not misread as
"broken".

Reads (cache-only):
  - data/state/regime_forecast_forward_log.jsonl
  - data/state/stock_lens_forward_log.jsonl
  - cache/prices/*.parquet (via the tracker's resolvers; no provider calls)

Writes:
  - cache/research/forward_resolution_health_latest.json
  - logs/forward_resolution_health_latest.txt

Research-only. Does NOT call providers, touch governance/execution, create paper
signals, fabricate outcomes, or change maturation semantics. By default it runs
the existing resolvers (their designed in-place fill of outcome fields); pass
``--no-resolve`` to inspect the logs without running them.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python research/forward_resolution_health.py [--no-resolve] [--print]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import forecast_forward_tracker as fft

JSON_OUT = ROOT / "cache" / "research" / "forward_resolution_health_latest.json"
TXT_OUT = ROOT / "logs" / "forward_resolution_health_latest.txt"

_TRADING_TO_CALENDAR = fft._TRADING_TO_CALENDAR  # keep in sync with the resolver


def _need_calendar(horizon: int) -> int:
    return int(horizon * _TRADING_TO_CALENDAR) + 1


def _parse_anchor(row: Dict[str, Any]) -> Optional[date]:
    raw = row.get("anchor_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _classify_log(
    rows: List[Dict[str, Any]],
    horizons: List[int],
    final_return_key,
    today: date,
) -> Dict[str, Any]:
    """Bucket every snapshot row into matured / not-mature-yet / missing-price /
    error-or-unparseable, and compute per-horizon fill coverage."""
    final_h = max(horizons)
    open_n = matured_n = not_mature_yet = missing_price = bad_rows = 0
    per_horizon_filled = {h: 0 for h in horizons}
    next_due: Optional[date] = None
    oldest_open_anchor: Optional[date] = None

    for row in rows:
        anchor = _parse_anchor(row)
        if anchor is None:
            bad_rows += 1
            continue
        outcomes = row.get("outcomes") or {}
        for h in horizons:
            if outcomes.get(final_return_key(h)) is not None:
                per_horizon_filled[h] += 1

        status = str(row.get("status") or "open").lower()
        if status == "matured":
            matured_n += 1
            continue

        open_n += 1
        age = (today - anchor).days
        final_ready_age = age >= _need_calendar(final_h)
        final_present = outcomes.get(final_return_key(final_h)) is not None

        if final_ready_age and not final_present:
            # Old enough for the final horizon but the price bar never filled.
            missing_price += 1
        else:
            not_mature_yet += 1
            due = anchor.fromordinal(anchor.toordinal() + _need_calendar(final_h))
            if next_due is None or due < next_due:
                next_due = due
            if oldest_open_anchor is None or anchor < oldest_open_anchor:
                oldest_open_anchor = anchor

    return {
        "rows": len(rows),
        "open": open_n,
        "matured": matured_n,
        "unresolved_not_mature_yet": not_mature_yet,
        "unresolved_missing_price": missing_price,
        "unresolved_error": bad_rows,
        "per_horizon_filled": {f"{h}d": per_horizon_filled[h] for h in horizons},
        "final_horizon_days": final_h,
        "final_horizon_calendar_days_needed": _need_calendar(final_h),
        "oldest_open_anchor": oldest_open_anchor.isoformat() if oldest_open_anchor else None,
        "next_maturity_due": next_due.isoformat() if next_due else None,
    }


def build_health(*, resolve: bool = True, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()

    resolver_runs: Dict[str, Any] = {}
    if resolve:
        # Designed in-place fill of outcome fields from cached price parquets.
        resolver_runs["forecast"] = fft.resolve_forecast_outcomes(today=today)
        resolver_runs["stock_lens"] = fft.resolve_stock_lens_outcomes(today=today)

    fc_rows = fft.load_forecast_log()
    lens_rows = fft.load_stock_lens_log()

    fc = _classify_log(
        fc_rows,
        list(fft.FORECAST_HORIZONS_DAYS),
        lambda h: f"spy_{h}d_return_pct",
        today,
    )
    lens = _classify_log(
        lens_rows,
        list(fft.STOCK_LENS_HORIZONS_DAYS),
        lambda h: f"return_{h}d_pct",
        today,
    )

    # Resolver verdict.
    errors = fc["unresolved_error"] + lens["unresolved_error"]
    missing = fc["unresolved_missing_price"] + lens["unresolved_missing_price"]
    short_horizon_filling = (
        sum(fc["per_horizon_filled"].values()) + sum(lens["per_horizon_filled"].values())
    ) > 0

    if errors > 0:
        status = "FAIL"
        status_reason = f"{errors} snapshot rows raised resolver errors or could not be parsed."
    elif missing > 0 and not short_horizon_filling:
        status = "FAIL"
        status_reason = (
            f"{missing} snapshots are old enough to mature but their final-horizon "
            "price bars never filled, and no short-horizon outcomes are filling — "
            "price cache or resolver is likely broken."
        )
    elif missing > 0:
        status = "WARN"
        status_reason = (
            f"{missing} snapshots are old enough for their final horizon but the "
            "price bar is missing (likely a gap in cache/prices/*.parquet for those "
            "symbols). Short-horizon outcomes are otherwise filling normally."
        )
    elif fc["matured"] + lens["matured"] == 0 and short_horizon_filling:
        status = "PASS"
        status_reason = (
            "Resolver is healthy. 0 matured is expected: no snapshot has yet reached "
            f"the {fc['final_horizon_calendar_days_needed']}-calendar-day window for the "
            f"{fc['final_horizon_days']}d final horizon. Short-horizon (1/5/10d) outcomes "
            "are filling on cadence."
        )
    else:
        status = "PASS"
        status_reason = "Resolver is healthy; snapshots are maturing on cadence."

    # Earliest maturity across both logs.
    due_dates = [d for d in (fc["next_maturity_due"], lens["next_maturity_due"]) if d]
    next_maturity_due = min(due_dates) if due_dates else None

    refresh_cmd = (
        "SNIPER_ENV_PATH=/home/gem/secure/trading.env "
        ".venv/bin/python research/forecast_forward_report.py   # forecast resolve + summary\n"
        "  ./scripts/run_research_cycle.sh resolve   # cache-only resolve of forward outcomes\n"
        "  SNIPER_ENV_PATH=/home/gem/secure/trading.env "
        ".venv/bin/python research/forward_resolution_health.py --print"
    )

    return {
        "kind": "forward_resolution_health",
        "version": "FORWARD_RESOLUTION_HEALTH_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": today.isoformat(),
        "resolver_ran": resolve,
        "resolver_runs": resolver_runs,
        "trading_to_calendar_factor": _TRADING_TO_CALENDAR,
        "forecast_open": fc["open"],
        "forecast_matured": fc["matured"],
        "lens_open": lens["open"],
        "lens_matured": lens["matured"],
        "unresolved_missing_price": missing,
        "unresolved_not_mature_yet": fc["unresolved_not_mature_yet"] + lens["unresolved_not_mature_yet"],
        "unresolved_error": errors,
        "next_maturity_due": next_maturity_due,
        "resolver_status": status,
        "resolver_status_reason": status_reason,
        "refresh_command": refresh_cmd,
        "forecast_detail": fc,
        "lens_detail": lens,
    }


def render_text(h: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("=" * 64)
    L.append(f"FORWARD RESOLUTION HEALTH — {h['as_of_date']}  (research-only)")
    L.append("=" * 64)
    L.append(f"resolver_status: {h['resolver_status']}")
    L.append(f"  {h['resolver_status_reason']}")
    L.append("")
    fc, lens = h["forecast_detail"], h["lens_detail"]
    L.append(f"FORECAST log: {fc['rows']} rows | open {fc['open']} | matured {fc['matured']}")
    L.append(f"  per-horizon final-return filled: {fc['per_horizon_filled']}")
    L.append(f"  final horizon {fc['final_horizon_days']}d needs "
             f"{fc['final_horizon_calendar_days_needed']} calendar days")
    L.append(f"  oldest open anchor: {fc['oldest_open_anchor']} | next maturity due: {fc['next_maturity_due']}")
    L.append("")
    L.append(f"STOCK LENS log: {lens['rows']} rows | open {lens['open']} | matured {lens['matured']}")
    L.append(f"  per-horizon final-return filled: {lens['per_horizon_filled']}")
    L.append(f"  oldest open anchor: {lens['oldest_open_anchor']} | next maturity due: {lens['next_maturity_due']}")
    L.append("")
    L.append(f"unresolved_not_mature_yet: {h['unresolved_not_mature_yet']}")
    L.append(f"unresolved_missing_price:  {h['unresolved_missing_price']}")
    L.append(f"unresolved_error:          {h['unresolved_error']}")
    L.append(f"next_maturity_due (both):  {h['next_maturity_due']}")
    L.append("")
    L.append("Refresh / resolve command:")
    L.append("  " + h["refresh_command"])
    L.append("=" * 64)
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Forward-resolution health diagnostic")
    p.add_argument("--no-resolve", action="store_true",
                   help="inspect logs without running the resolvers")
    p.add_argument("--print", dest="do_print", action="store_true",
                   help="print the text summary after writing")
    args = p.parse_args(argv)

    health = build_health(resolve=not args.no_resolve)

    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    TXT_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(health, indent=2, default=str))
    text = render_text(health)
    TXT_OUT.write_text(text + "\n")

    if args.do_print:
        print(text)
    else:
        print(f"forward_resolution_health: {health['resolver_status']} "
              f"(forecast open {health['forecast_open']}/matured {health['forecast_matured']}, "
              f"lens open {health['lens_open']}/matured {health['lens_matured']}, "
              f"next due {health['next_maturity_due']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
