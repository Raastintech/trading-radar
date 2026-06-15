"""
resolve_tactical_outcomes.py - Automated paper outcome resolver for tactical sleeves.

This script reads the unified paper ledger and upserts horizon outcomes for the
active tactical paper sleeves only:

  - SNIPER / SNIPER_V6: 1d, 3d, 5d, 10d
  - SHORT / SHORT_A:    3d, 5d, 10d

It does not alter scanner logic, governance limits, or baseline tags.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import core.config as cfg
from core.alpaca_client import get_alpaca
from core.paper_validation import (
    ensure_schema,
    fetch_paper_signals,
    mark_paper_signal_closed,
    record_paper_outcome,
)
# Phase 1G.3: resolve outcomes for all paper-ledger sleeves, INCLUDING frozen
# ones (e.g. SHORT_A after the 2026-05-24 freeze), so already-logged historical
# rows keep maturing. New-signal emission is gated elsewhere (active scanners +
# paper governance), so this resolution path never creates new signals.
from core.strategy_registry import normalize_strategy, paper_ledger_horizons, paper_ledger_tags

TACTICAL_HORIZONS: Dict[str, List[int]] = paper_ledger_horizons()
TACTICAL_TAGS: Dict[str, str] = {
    strategy: tag
    for strategy, tag in paper_ledger_tags().items()
    if strategy in TACTICAL_HORIZONS
}

ROUND_TRIP_FRICTION_PCT = 0.30  # 0.05% commission + 0.10% slippage each way.
NON_EVIDENCE_STATUSES = {"governance_blocked", "observe_only", "duplicate"}


@dataclass
class OutcomeResult:
    horizon_days: int
    outcome_date: Optional[str]
    return_pct: Optional[float]
    adjusted_return_pct: Optional[float]
    stop_hit: bool
    target_hit: bool
    still_open: bool
    hold_complete: bool
    mae_pct: Optional[float]
    mfe_pct: Optional[float]
    path: str
    exit_price: Optional[float]


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if hasattr(value, "date") and callable(value.date):
        try:
            return value.date()
        except Exception:
            pass
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _bar_date(bar: Dict[str, Any]) -> Optional[date]:
    return _parse_date(bar.get("date") or bar.get("timestamp"))


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _signal_date(signal: Dict[str, Any]) -> Optional[date]:
    return _parse_date(signal.get("logged_at"))


def _normalise_bars(bars: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean = []
    for bar in bars or []:
        dt = _bar_date(bar)
        if dt is None:
            continue
        row = dict(bar)
        row["_date"] = dt
        clean.append(row)
    clean.sort(key=lambda r: r["_date"])
    return clean


def _future_bars(signal: Dict[str, Any], bars: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sig_date = _signal_date(signal)
    if sig_date is None:
        return []
    return [bar for bar in _normalise_bars(bars) if bar["_date"] > sig_date]


def _raw_return(side: str, entry: float, exit_price: float) -> float:
    """Return value is in PERCENT (e.g. +14.19 means +14.19%), matching
    the paper_signal_outcomes.return_pct schema convention. Do NOT confuse
    with decisions.pnl_pct which stores the same idea as a FRACTION."""
    if side.upper() == "SHORT":
        return (entry - exit_price) / entry * 100.0
    return (exit_price - entry) / entry * 100.0


def _mae_mfe(side: str, entry: float, bars: List[Dict[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    if not bars:
        return None, None
    lows = [_as_float(b.get("low")) for b in bars]
    highs = [_as_float(b.get("high")) for b in bars]
    lows = [v for v in lows if v is not None]
    highs = [v for v in highs if v is not None]
    if not lows or not highs:
        return None, None
    if side.upper() == "SHORT":
        mae = (entry - max(highs)) / entry * 100.0
        mfe = (entry - min(lows)) / entry * 100.0
    else:
        mae = (min(lows) - entry) / entry * 100.0
        mfe = (max(highs) - entry) / entry * 100.0
    return mae, mfe


def _first_stop_or_target(
    *,
    side: str,
    bars: List[Dict[str, Any]],
    stop_loss: Optional[float],
    target_price: Optional[float],
) -> Optional[tuple[int, str, float, str]]:
    for idx, bar in enumerate(bars, start=1):
        high = _as_float(bar.get("high"))
        low = _as_float(bar.get("low"))
        if high is None or low is None:
            continue

        dt = bar["_date"].isoformat()
        if side.upper() == "SHORT":
            stop = stop_loss is not None and high >= stop_loss
            target = target_price is not None and low <= target_price
        else:
            stop = stop_loss is not None and low <= stop_loss
            target = target_price is not None and high >= target_price

        if stop:
            return idx, "stop", float(stop_loss), dt
        if target:
            return idx, "target", float(target_price), dt
    return None


def resolve_signal_outcomes(signal: Dict[str, Any], bars: Iterable[Dict[str, Any]]) -> List[OutcomeResult]:
    strategy = normalize_strategy(signal.get("strategy", ""))
    horizons = TACTICAL_HORIZONS.get(strategy, [])
    entry = _as_float(signal.get("entry_price"))
    if not horizons or entry is None or entry <= 0:
        return []

    side = str(signal.get("side", "LONG")).upper()
    stop_loss = _as_float(signal.get("stop_loss"))
    target_price = _as_float(signal.get("target_price"))
    future = _future_bars(signal, bars)
    first_hit = _first_stop_or_target(
        side=side,
        bars=future[: max(horizons)] if future else [],
        stop_loss=stop_loss,
        target_price=target_price,
    )

    results: List[OutcomeResult] = []
    for horizon in horizons:
        path = "still_open"
        outcome_date = None
        exit_price = None
        stop_hit = False
        target_hit = False
        still_open = True
        hold_complete = False

        if first_hit and first_hit[0] <= horizon:
            hit_idx, hit_path, hit_price, hit_date = first_hit
            path = hit_path
            outcome_date = hit_date
            exit_price = hit_price
            stop_hit = hit_path == "stop"
            target_hit = hit_path == "target"
            still_open = False
            bars_for_path = future[:hit_idx]
        elif len(future) >= horizon:
            horizon_bar = future[horizon - 1]
            close = _as_float(horizon_bar.get("close"))
            path = "timeout"
            outcome_date = horizon_bar["_date"].isoformat()
            exit_price = close
            still_open = False
            hold_complete = True
            bars_for_path = future[:horizon]
        else:
            bars_for_path = future

        mae, mfe = _mae_mfe(side, entry, bars_for_path)
        if exit_price is not None:
            raw = _raw_return(side, entry, exit_price)
            adjusted = raw - ROUND_TRIP_FRICTION_PCT
        else:
            raw = None
            adjusted = None

        results.append(
            OutcomeResult(
                horizon_days=horizon,
                outcome_date=outcome_date,
                return_pct=round(raw, 4) if raw is not None else None,
                adjusted_return_pct=round(adjusted, 4) if adjusted is not None else None,
                stop_hit=stop_hit,
                target_hit=target_hit,
                still_open=still_open,
                hold_complete=hold_complete,
                mae_pct=round(mae, 4) if mae is not None else None,
                mfe_pct=round(mfe, 4) if mfe is not None else None,
                path=path,
                exit_price=exit_price,
            )
        )
    return results


def _is_tactical_evidence(signal: Dict[str, Any]) -> bool:
    strategy = normalize_strategy(signal.get("strategy", ""))
    status = str(signal.get("status", "open")).lower()
    expected_tag = TACTICAL_TAGS.get(strategy)
    if expected_tag is None:
        return False
    if status in NON_EVIDENCE_STATUSES:
        return False
    return str(signal.get("signal_version") or signal.get("sleeve")) == expected_tag


def resolve_tactical_outcomes(
    *,
    price_loader: Optional[Callable[[str, int], List[Dict[str, Any]]]] = None,
    max_signals: Optional[int] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    """
    Resolve all eligible tactical paper outcomes.

    `price_loader` exists for tests/smoke checks. Production uses Alpaca daily
    bars, which are cache-first via `core.alpaca_client`.
    """
    ensure_schema()
    signals = [sig for sig in fetch_paper_signals() if _is_tactical_evidence(sig)]
    if max_signals is not None:
        signals = signals[: max(0, int(max_signals))]

    if price_loader is None:
        alpaca = get_alpaca()
        price_loader = lambda ticker, days: alpaca.get_daily_bars(ticker, days=days)

    updated = 0
    still_open = 0
    closed = 0
    skipped = 0

    for signal in signals:
        strategy = str(signal.get("strategy", "")).upper()
        ticker = str(signal.get("ticker", "")).upper()
        horizons = TACTICAL_HORIZONS[strategy]
        days_needed = max(horizons) + 8
        bars = price_loader(ticker, days_needed)
        outcomes = resolve_signal_outcomes(signal, bars)
        if not outcomes:
            skipped += 1
            continue

        if not dry_run:
            for outcome in outcomes:
                record_paper_outcome(
                    str(signal["id"]),
                    outcome.horizon_days,
                    outcome_date=outcome.outcome_date,
                    return_pct=outcome.return_pct,
                    adjusted_return_pct=outcome.adjusted_return_pct,
                    stop_hit=outcome.stop_hit,
                    target_hit=outcome.target_hit,
                    still_open=outcome.still_open,
                    hold_complete=outcome.hold_complete,
                    mae_pct=outcome.mae_pct,
                    mfe_pct=outcome.mfe_pct,
                    path=outcome.path,
                    notes=f"auto_resolved; friction={ROUND_TRIP_FRICTION_PCT:.2f}pct_round_trip",
                )
            final = outcomes[-1]
            if not final.still_open and str(signal.get("status", "open")).lower() == "open":
                mark_paper_signal_closed(
                    str(signal["id"]),
                    exit_price=final.exit_price,
                    exit_reason=final.path,
                    exit_date=final.outcome_date,
                )

        updated += len(outcomes)
        if outcomes[-1].still_open:
            still_open += 1
        else:
            closed += 1

    return {
        "signals_seen": len(signals),
        "outcomes_updated": updated,
        "signals_still_open": still_open,
        "signals_closed": closed,
        "signals_skipped": skipped,
        "dry_run": int(dry_run),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve SNIPER_V6 and SHORT_A paper outcomes")
    parser.add_argument("--db", type=Path, default=None, help="SQLite DB path override")
    parser.add_argument("--max-signals", type=int, default=None, help="Optional cap for local checks")
    parser.add_argument("--dry-run", action="store_true", help="Compute outcomes without writing rows")
    args = parser.parse_args()

    if args.db is not None:
        cfg.DB_PATH = args.db

    result = resolve_tactical_outcomes(max_signals=args.max_signals, dry_run=args.dry_run)
    print("Tactical paper outcome resolver")
    for key, value in result.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
