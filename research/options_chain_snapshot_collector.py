#!/usr/bin/env python3
"""
research/options_chain_snapshot_collector.py - Phase 1J.1 chain snapshots.

Point-in-time options chain snapshot collector. Pulls CURRENT chains from the
existing shared feed (core.options_feed_factory: Tradier, research-only
since Phase 3A) and persists them append-only so that strike-level
options history starts existing from today forward. This is the data
foundation Phase 1J.0 found missing — nothing more.

DATA INFRASTRUCTURE ONLY:
  - no strategy, no backtest, no signals, no proposals, no orders;
  - never pretends current chains are historical (rows carry the collection
    timestamp; files are per-day and never overwritten);
  - hard provider-budget guards; dry-run mode makes zero provider calls;
  - provider calls happen only from this CLI — never the dashboard.

Outputs:
  - data/options_snapshots/YYYY-MM-DD/<UNDERLYING>.parquet  (append-only)
  - cache/research/options_chain_snapshot_collector_latest.json
  - logs/options_chain_snapshot_collector_latest.txt

Schema: docs/research/OPTIONS_CHAIN_SNAPSHOT_SCHEMA.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Credential loading mirrors research/options_regime_lens.py: prefer
# SNIPER_ENV_PATH, allow GEM_TRADER_SKIP_DOTENV=true for offline tests, and
# fall back to placeholders so the module is importable without creds (real
# provider calls fail-soft and are reported as provider_not_configured).
try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None and os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    _env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if _env_path and Path(_env_path).exists():
        load_dotenv(_env_path)
    elif (ROOT / ".env").exists():  # pragma: no cover
        load_dotenv(ROOT / ".env")
for _k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FMP_API_KEY"):
    os.environ.setdefault(_k, "offline-placeholder")

CACHE = ROOT / "cache" / "research"
LOGS = ROOT / "logs"
SNAPSHOT_ROOT = ROOT / "data" / "options_snapshots"

OUT_JSON = CACHE / "options_chain_snapshot_collector_latest.json"
OUT_TXT = LOGS / "options_chain_snapshot_collector_latest.txt"
IV_HISTORY_PATH = CACHE / "options_iv_history.jsonl"

VERSION = "OPTIONS_CHAIN_SNAPSHOT_COLLECTOR_V1"

# Task 6 — explicit status marker: this module is data collection only.
OPTIONS_PREMIUM_STRATEGY_STATUS = "DATA_COLLECTION_ONLY"

CORE_UNIVERSE: Tuple[str, ...] = ("SPY", "QQQ", "IWM", "SMH", "XLK")

SCHEMA_COLUMNS: Tuple[str, ...] = (
    "as_of_date", "as_of_timestamp_utc", "provider", "underlying", "underlying_price",
    "expiration", "dte", "option_type", "strike", "bid", "ask", "mid", "last",
    "volume", "open_interest", "implied_volatility",
    "delta", "gamma", "theta", "vega", "rho",
    "quote_timestamp", "earnings_date", "days_to_earnings",
    "raw_provider_fields", "data_quality_flags",
)

WIDE_SPREAD_PCT = 0.25


@dataclass(frozen=True)
class BudgetGuard:
    """Task 5 hard guardrails. Every provider touch is counted."""
    max_symbols_per_run: int = 20
    max_expiries_per_symbol: int = 4
    max_provider_calls_per_run: int = 150
    dte_min: int = 20
    dte_max: int = 70
    dte_target: int = 45
    strike_band_pct: float = 0.30


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _opt_f(value: Any) -> Optional[float]:
    try:
        x = float(value)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def default_universe(*, cap: int) -> List[str]:
    """Core ETFs + the most options-covered Stock Lens names (by retained
    IV-history depth — those are the liquid names the lens already pulls)."""
    out = list(CORE_UNIVERSE)
    depth: Dict[str, set] = defaultdict(set)
    if IV_HISTORY_PATH.exists():
        for line in IV_HISTORY_PATH.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("ticker") and row.get("date"):
                depth[str(row["ticker"])].add(str(row["date"]))
    ranked = sorted(depth.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for sym, _days in ranked:
        if sym not in out:
            out.append(sym)
        if len(out) >= cap:
            break
    return out[:cap]


def quality_flags(row: Dict[str, Any], *, dte: int, guard: BudgetGuard) -> List[str]:
    flags: List[str] = []
    bid, ask, mid = row.get("bid"), row.get("ask"), row.get("mid")
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        flags.append("missing_bid_ask")
    elif bid > ask:
        flags.append("crossed_market")
    elif mid and mid > 0 and (ask - bid) > WIDE_SPREAD_PCT * mid:
        flags.append("wide_spread")
    if not row.get("implied_volatility"):
        flags.append("missing_iv")
    if row.get("delta") is None:
        flags.append("missing_greeks")
    if row.get("open_interest") is None:
        flags.append("missing_oi")
    vol = row.get("volume")
    if vol is not None and vol == 0:
        flags.append("zero_volume")
        if bid is None or bid <= 0:
            flags.append("stale_quote")
    if not (guard.dte_min <= dte <= guard.dte_max):
        flags.append("expiration_out_of_range")
    return flags


def _dte(expiry: str, *, today: date) -> Optional[int]:
    try:
        return (datetime.strptime(str(expiry)[:10], "%Y-%m-%d").date() - today).days
    except Exception:
        return None


def select_expirations(expirations: Sequence[str], *, today: date, guard: BudgetGuard) -> List[str]:
    """20-70 DTE window, prioritized by closeness to the 45 DTE target."""
    in_window = []
    for exp in expirations:
        d = _dte(exp, today=today)
        if d is not None and guard.dte_min <= d <= guard.dte_max:
            in_window.append((abs(d - guard.dte_target), d, str(exp)[:10]))
    in_window.sort()
    return [exp for _, _, exp in in_window[: guard.max_expiries_per_symbol]]


def rows_from_chain_side(
    df: Any,
    *,
    option_type: str,
    underlying: str,
    spot: Optional[float],
    expiry: str,
    dte: int,
    provider: str,
    as_of_date: str,
    as_of_ts: str,
    guard: BudgetGuard,
) -> List[Dict[str, Any]]:
    if df is None or getattr(df, "empty", True) or "strike" not in getattr(df, "columns", []):
        return []
    out: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        strike = _opt_f(r.get("strike"))
        if strike is None or strike <= 0:
            continue
        if spot and spot > 0 and abs(strike - spot) > guard.strike_band_pct * spot:
            continue
        bid = _opt_f(r.get("bid"))
        ask = _opt_f(r.get("ask"))
        mid = round((bid + ask) / 2.0, 6) if bid is not None and ask is not None and bid > 0 and ask > 0 else None
        iv = _opt_f(r.get("impliedVolatility"))
        raw = {
            "contractSymbol": r.get("contractSymbol"),
            "inTheMoney": bool(r.get("inTheMoney")) if r.get("inTheMoney") is not None else None,
            "bid_iv": _opt_f(r.get("bid_iv")),
            "ask_iv": _opt_f(r.get("ask_iv")),
            "close_price": _opt_f(r.get("close_price")),
        }
        row = {
            "as_of_date": as_of_date,
            "as_of_timestamp_utc": as_of_ts,
            "provider": provider,
            "underlying": underlying,
            "underlying_price": spot,
            "expiration": str(expiry)[:10],
            "dte": int(dte),
            "option_type": option_type,
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "last": _opt_f(r.get("lastPrice")),
            "volume": _opt_f(r.get("volume")),
            "open_interest": _opt_f(r.get("openInterest")),
            "implied_volatility": iv if iv and iv > 0 else None,
            "delta": _opt_f(r.get("delta")),
            "gamma": _opt_f(r.get("gamma")),
            "theta": _opt_f(r.get("theta")),
            "vega": _opt_f(r.get("vega")),
            "rho": _opt_f(r.get("rho")),
            "quote_timestamp": str(r.get("quote_timestamp")) if r.get("quote_timestamp") is not None else None,
            "earnings_date": None,
            "days_to_earnings": None,
        }
        row["data_quality_flags"] = json.dumps(quality_flags(row, dte=dte, guard=guard))
        row["raw_provider_fields"] = json.dumps(raw, default=str)
        out.append(row)
    return out


def snapshot_path(as_of_date: str, underlying: str) -> Path:
    return SNAPSHOT_ROOT / as_of_date / f"{underlying.upper()}.parquet"


def write_snapshot(rows: List[Dict[str, Any]], *, as_of_date: str, underlying: str) -> Dict[str, Any]:
    """Append-only, idempotent per (symbol, date): existing files are never
    overwritten — a second run the same day skips the symbol."""
    path = snapshot_path(as_of_date, underlying)
    if path.exists():
        return {"path": str(path), "written": False, "reason": "exists_idempotent_skip", "rows": 0}
    if not rows:
        return {"path": str(path), "written": False, "reason": "no_rows", "rows": 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=list(SCHEMA_COLUMNS))
    tmp = path.with_suffix(".tmp.parquet")
    frame.to_parquet(tmp, index=False)
    tmp.rename(path)
    return {"path": str(path), "written": True, "reason": "ok", "rows": len(frame)}


class ProviderBudget:
    def __init__(self, guard: BudgetGuard):
        self.guard = guard
        self.calls = 0

    def spend(self, n: int = 1) -> bool:
        if self.calls + n > self.guard.max_provider_calls_per_run:
            return False
        self.calls += n
        return True


def _load_feed():
    try:
        from core.options_feed_factory import load_options_feed
        return load_options_feed()
    except Exception:
        return None


def _get_spot(sym: str) -> Optional[float]:
    # Phase 3A: Alpaca removed. Spot price from local price cache.
    try:
        cache_path = ROOT / "cache" / "prices" / f"{sym}.parquet"
        if cache_path.exists():
            df = pd.read_parquet(cache_path, columns=["close"])
            if not df.empty:
                c = df["close"].iloc[-1]
                if c and float(c) > 0:
                    return float(c)
    except Exception:
        pass
    return None


def collect(
    *,
    symbols: Optional[Sequence[str]] = None,
    guard: BudgetGuard = BudgetGuard(),
    dry_run: bool = False,
    feed: Any = None,
    spot_fn: Any = None,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    as_of_date = str(today)
    as_of_ts = _utc_now()
    universe = [s.upper() for s in (symbols or default_universe(cap=guard.max_symbols_per_run))]
    universe = universe[: guard.max_symbols_per_run]
    budget = ProviderBudget(guard)

    result: Dict[str, Any] = {
        "kind": "options_chain_snapshot_collector",
        "version": VERSION,
        "strategy_status": OPTIONS_PREMIUM_STRATEGY_STATUS,
        "generated_at": as_of_ts,
        "research_only": True,
        "dry_run": dry_run,
        "as_of_date": as_of_date,
        "guard": guard.__dict__,
        "universe": universe,
        "symbols": {},
        "provider_calls_used": 0,
        "provider_configured": None,
        "safety": {
            "no_live_trading": True, "no_broker_orders": True, "no_paper_signals": True,
            "no_trade_proposals": True, "no_strategy_activation": True,
            "no_production_changes": True, "short_a_remains_frozen": True,
            "current_chains_marked_with_collection_timestamp_only": True,
        },
    }
    if dry_run:
        result["provider_configured"] = "not_checked_in_dry_run"
        result["plan"] = {
            "would_write_to": str(SNAPSHOT_ROOT / as_of_date),
            "dte_window": [guard.dte_min, guard.dte_max],
            "dte_target": guard.dte_target,
            "strike_band_pct": guard.strike_band_pct,
            "max_provider_calls": guard.max_provider_calls_per_run,
            "already_collected_today": [
                s for s in universe if snapshot_path(as_of_date, s).exists()
            ],
        }
        return result

    feed = feed if feed is not None else _load_feed()
    if feed is None:
        result["provider_configured"] = False
        result["error"] = (
            "options feed not configured (core.options_feed_factory.load_options_feed returned None); "
            "set Alpaca/Tradier credentials via SNIPER_ENV_PATH and rerun"
        )
        return result
    result["provider_configured"] = True
    spot_fn = spot_fn or _get_spot

    for sym in universe:
        sym_row: Dict[str, Any] = {"status": "ok"}
        result["symbols"][sym] = sym_row
        if snapshot_path(as_of_date, sym).exists():
            sym_row.update({"status": "skipped_idempotent", "rows": 0})
            continue
        if not budget.spend(2):  # spot + expirations
            sym_row["status"] = "skipped_budget_exhausted"
            continue
        spot = spot_fn(sym)
        try:
            expirations = list(feed.get_expirations(sym))
        except Exception as exc:
            sym_row.update({"status": "expirations_error", "error": str(exc)[:200]})
            continue
        picks = select_expirations(expirations, today=today, guard=guard)
        sym_row["expirations_selected"] = picks
        if not picks:
            sym_row["status"] = "no_expirations_in_dte_window"
            continue
        rows: List[Dict[str, Any]] = []
        for expiry in picks:
            if not budget.spend(1):
                sym_row["status"] = "partial_budget_exhausted"
                break
            try:
                chain = feed.get_chain(sym, expiry)
            except Exception:
                chain = None
            if not chain:
                continue
            d = _dte(expiry, today=today) or 0
            provider = str(getattr(feed, "last_served", None) or "unknown")
            enriched = getattr(feed, "last_enriched_by", None)
            if enriched:
                provider = f"{provider}+{enriched}"
            for side, df in (("call", chain.get("calls")), ("put", chain.get("puts"))):
                rows.extend(rows_from_chain_side(
                    df, option_type=side, underlying=sym, spot=spot, expiry=expiry,
                    dte=d, provider=provider, as_of_date=as_of_date, as_of_ts=as_of_ts, guard=guard,
                ))
        write_res = write_snapshot(rows, as_of_date=as_of_date, underlying=sym)
        sym_row.update({"rows": write_res["rows"], "write": write_res, "spot": spot})

    result["provider_calls_used"] = budget.calls
    return result


def render_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"OPTIONS CHAIN SNAPSHOT COLLECTOR (PHASE 1J.1) - {res['generated_at']}",
        f"status={res['strategy_status']} dry_run={res['dry_run']} as_of={res['as_of_date']}",
        f"provider_configured={res.get('provider_configured')} provider_calls_used={res.get('provider_calls_used')}",
        f"universe({len(res['universe'])}): {', '.join(res['universe'])}",
    ]
    if res.get("error"):
        lines.append(f"ERROR: {res['error']}")
    if res.get("plan"):
        lines.append(f"plan: {json.dumps(res['plan'])}")
    for sym, row in (res.get("symbols") or {}).items():
        lines.append(f"  {sym}: {row.get('status')} rows={row.get('rows', 0)} expiries={row.get('expirations_selected')}")
    return lines


def write_outputs(res: Dict[str, Any]) -> None:
    for p in (OUT_JSON, OUT_TXT):
        p.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1J.1 point-in-time options chain snapshot collector (data only)")
    ap.add_argument("--symbols", default=None, help="comma-separated override (default: core ETFs + lens-covered liquid names)")
    ap.add_argument("--max-symbols", type=int, default=BudgetGuard.max_symbols_per_run)
    ap.add_argument("--max-expiries", type=int, default=BudgetGuard.max_expiries_per_symbol)
    ap.add_argument("--max-calls", type=int, default=BudgetGuard.max_provider_calls_per_run)
    ap.add_argument("--strike-band", type=float, default=BudgetGuard.strike_band_pct)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    guard = BudgetGuard(
        max_symbols_per_run=args.max_symbols,
        max_expiries_per_symbol=args.max_expiries,
        max_provider_calls_per_run=args.max_calls,
        strike_band_pct=args.strike_band,
    )
    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    res = collect(symbols=symbols, guard=guard, dry_run=args.dry_run)
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0 if not res.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
