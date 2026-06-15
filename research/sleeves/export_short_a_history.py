"""
research/sleeves/export_short_a_history.py — historical SHORT_A trade export.

The default `research/sleeves/export_backtest_trades.py` produces SHORT_A.csv
from live `paper_signals` + outcomes only. That sample is currently very thin
(n=4 closed). For real evidence on SHORT Sleeve A, the heavy historical path
through `research/sleeves/short_backtester.py` must run.

This script wraps that heavy path and emits the standardized Phase 9B schema
into `research/sleeves/trades/SHORT_A.csv`, merging in any live-paper rows
that already exist so the audit doesn't lose the live signal when the
historical export overwrites the file.

Why a separate script
---------------------
- short_backtester.py needs FMP credentials and a working price/fundamentals
  data path. The plain `export_backtest_trades.py` runs cache-only and would
  always fail SHORT_A. Putting the heavyweight path behind its own runner
  keeps the regular export reproducible offline.

Required environment
--------------------
- `FMP_API_KEY`       (Financial Modeling Prep — fundamentals + earnings)
- `ALPACA_API_KEY`    (Alpaca — daily price bars)
- `ALPACA_SECRET_KEY`
- Optional: `TRADIER_API_KEY`, `POLYGON_API_KEY` for fallback price/options data
- Sourced via:
    set -a && source /home/gem/secure/trading.env && set +a
  before invoking this script.

Usage
-----
  cd /home/gem/trading-production
  set -a && source /home/gem/secure/trading.env && set +a
  .venv/bin/python research/sleeves/export_short_a_history.py \\
      [--lookback 504] [--universe-size 200] \\
      [--borrow-fee-annual-pct 1.0] [--slippage-bps 5.0] [--spread-bps 5.0] \\
      [--halt-gap-penalty-pct 0.5]

Defaults are conservative (matches the SHORT_DOCTRINE friction stack). The run
will take 5–30 minutes depending on network and FMP plan tier.

Output
------
- `research/sleeves/trades/SHORT_A.csv` — Phase 9B standard schema, with both
  historical backtest rows (`baseline_tag` starting with `short_history_v1`)
  and live-paper rows (`baseline_tag = live_paper_db`). The audit script reads
  the merged file.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parent.parent.parent
TRADES_DIR = REPO / "research" / "sleeves" / "trades"
OUT_PATH = TRADES_DIR / "SHORT_A.csv"

REQUIRED_ENV = ("FMP_API_KEY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY")


def _bootstrap_env_from_sniper_path() -> None:
    """Mirror the sniper_backtest pattern: if `SNIPER_ENV_PATH` is set, parse
    that file and inject KEY=VALUE pairs into os.environ. This lets the user
    run the script with a single env-var prefix instead of having to source
    the file manually first."""
    env_path = os.environ.get("SNIPER_ENV_PATH", "").strip()
    if not env_path:
        return
    if os.environ.get("GEM_TRADER_SKIP_DOTENV", "").lower() in ("1", "true", "yes"):
        return
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # Don't clobber values already in env (CLI/parent shell wins).
                os.environ.setdefault(key, value)
    except Exception:
        # Same forgiving posture as sniper_backtest: a parse failure should
        # not crash the runner; missing keys will still be reported below.
        pass


_bootstrap_env_from_sniper_path()
STD_FIELDS = [
    "strategy", "baseline_tag", "ticker", "side",
    "entry_date", "exit_date", "horizon",
    "entry_price", "exit_price",
    "raw_return_pct", "adjusted_return_pct",
    "stop_hit", "target_hit",
    "sector", "source_backtest", "friction_model", "notes",
]


def _missing_env() -> List[str]:
    return [k for k in REQUIRED_ENV if not os.environ.get(k)]


def _fmt_num(v, places: int = 4) -> str:
    if v is None:
        return ""
    try:
        return f"{float(v):.{places}f}"
    except Exception:
        return ""


def _fmt_bool(v) -> str:
    if v is None:
        return ""
    return "1" if v else "0"


def _fmt_date(d) -> str:
    if d is None:
        return ""
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    try:
        import pandas as pd  # type: ignore
        return pd.Timestamp(d).date().isoformat()
    except Exception:
        return ""


def _load_existing(path: Path) -> List[Dict[str, Any]]:
    """Returns existing rows so live-paper data is preserved."""
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


def _dedup_key(row: Dict[str, Any]) -> str:
    return f"{row.get('ticker','')}|{row.get('entry_date','')}|{row.get('horizon','')}|{row.get('baseline_tag','')}"


def _trade_to_std_rows(trade: Dict[str, Any], horizons: Sequence[int]) -> List[Dict[str, Any]]:
    """Map a short_backtester trade dict (after friction is applied) into one
    or more standard-schema rows. The backtester returns one row per closed
    trade with `hold_days` already settled; we emit a single row at the
    actual hold_days as the trade horizon."""
    ticker = (trade.get("ticker") or "").upper()
    side   = "SHORT"
    entry_date = trade.get("entry_date") or trade.get("signal_date")
    exit_date  = trade.get("exit_date")
    entry_price = trade.get("entry_price")
    exit_price  = trade.get("exit_price")
    raw  = trade.get("gross_return_pct")
    adj  = trade.get("effective_return_pct")
    if adj is None:
        adj = trade.get("net_return_pct")
    stop_hit = (str(trade.get("exit_reason") or "").upper() == "STOP")
    tgt_hit  = (str(trade.get("exit_reason") or "").upper() == "TARGET")
    sector   = trade.get("sector")
    note_parts = [
        f"pathway={trade.get('pathway','')}",
        f"score={_fmt_num(trade.get('score'), 1)}",
        f"borrow_pct={_fmt_num(trade.get('borrow_cost_pct'))}",
        f"gap_risk_pct={_fmt_num(trade.get('gap_risk_max_up_20d_pct'))}",
        f"intraday_range_pct={_fmt_num(trade.get('intraday_range_max_20d_pct'))}",
        f"stop_price={_fmt_num(trade.get('stop_price'))}",
        f"target_price={_fmt_num(trade.get('target_price'))}",
    ]
    horizon = int(trade.get("hold_days") or 0)
    return [{
        "strategy":           "SHORT_SLEEVE_A",
        "baseline_tag":       "short_history_v1",
        "ticker":             ticker,
        "side":               side,
        "entry_date":         _fmt_date(entry_date),
        "exit_date":          _fmt_date(exit_date),
        "horizon":            horizon,
        "entry_price":        _fmt_num(entry_price),
        "exit_price":         _fmt_num(exit_price),
        "raw_return_pct":     _fmt_num(raw, places=6) if raw is not None else "",
        "adjusted_return_pct":_fmt_num(adj, places=6) if adj is not None else "",
        "stop_hit":           _fmt_bool(stop_hit),
        "target_hit":         _fmt_bool(tgt_hit),
        "sector":             sector or "",
        "source_backtest":    "research/sleeves/short_backtester.py",
        "friction_model":     "borrow+slippage+spread+halt_gap (CLI args, see notes)",
        "notes":              "; ".join(note_parts),
    }]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Heavyweight SHORT_A historical export.")
    parser.add_argument("--lookback", type=int, default=504,
                        help="Trading-day lookback for historical scan (default 504 = ~2 years).")
    parser.add_argument("--universe-size", type=int, default=200,
                        help="Top-N candidates from the discovery screen (default 200, range 50-300).")
    parser.add_argument("--score-threshold", type=float, default=60.0,
                        help="Minimum SHORT score (default 60).")
    parser.add_argument("--min-rr", type=float, default=2.5,
                        help="Minimum R:R (default 2.5).")
    parser.add_argument("--borrow-fee-annual-pct", type=float, default=1.0,
                        help="Borrow fee assumption (default 1.0%/year).")
    parser.add_argument("--slippage-bps", type=float, default=5.0,
                        help="Slippage each side (default 5 bps each side, 10 bps RT).")
    parser.add_argument("--spread-bps", type=float, default=5.0,
                        help="Spread each side (default 5 bps each side, 10 bps RT).")
    parser.add_argument("--halt-gap-penalty-pct", type=float, default=0.5,
                        help="Penalty for squeeze-like exit (default 0.5%).")
    parser.add_argument("--allow-missing-env", action="store_true",
                        help="Skip the env-var check (for diagnosis only — will likely fail downstream).")
    args = parser.parse_args(argv)

    missing = _missing_env()
    if missing and not args.allow_missing_env:
        print("✗ Missing required env vars:", ", ".join(missing), file=sys.stderr)
        print("", file=sys.stderr)
        print("Either set SNIPER_ENV_PATH to your env file (recommended):", file=sys.stderr)
        print("  SNIPER_ENV_PATH=/home/gem/secure/trading.env "
              ".venv/bin/python research/sleeves/export_short_a_history.py", file=sys.stderr)
        print("or source the file in the shell first:", file=sys.stderr)
        print("  set -a && source /home/gem/secure/trading.env && set +a", file=sys.stderr)
        return 2

    # short_backtester pulls helpers (enhanced_strategy_scoring,
    # short_scanner_v1, research_data_provider, fundamental_data_fetcher,
    # strategy_check_mapper) from research/archived. Add both directories to
    # sys.path so direct module imports resolve.
    for sub in ("research/sleeves", "research/archived"):
        p = str(REPO / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        import short_backtester as sbt  # type: ignore
    except Exception as exc:
        print(f"✗ Could not import short_backtester: {exc!r}", file=sys.stderr)
        return 3

    print(f"Running SHORT_A backtest — lookback={args.lookback}, universe={args.universe_size}, "
          f"borrow={args.borrow_fee_annual_pct}%, slippage={args.slippage_bps}bps each, "
          f"spread={args.spread_bps}bps each, halt={args.halt_gap_penalty_pct}%")
    try:
        result = sbt.run_backtest(
            lookback=args.lookback,
            score_threshold=args.score_threshold,
            min_rr=args.min_rr,
            universe_size=args.universe_size,
            borrow_fee_annual_pct=args.borrow_fee_annual_pct,
            slippage_bps=args.slippage_bps,
            spread_bps=args.spread_bps,
            halt_gap_penalty_pct=args.halt_gap_penalty_pct,
        )
    except Exception as exc:
        print(f"✗ short_backtester.run_backtest failed: {exc!r}", file=sys.stderr)
        return 4

    trades = result.get("friction_trades") or result.get("sized_trades") or []
    print(f"  short_backtester returned {len(trades)} trades")

    new_rows: List[Dict[str, Any]] = []
    for t in trades:
        new_rows.extend(_trade_to_std_rows(t, horizons=[]))

    # Merge with existing live-paper rows so they aren't lost
    existing_rows = _load_existing(OUT_PATH)
    merged: Dict[str, Dict[str, Any]] = {}
    for row in existing_rows:
        merged[_dedup_key(row)] = row
    for row in new_rows:
        merged[_dedup_key(row)] = row

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=STD_FIELDS)
        w.writeheader()
        for row in merged.values():
            out = {k: ("" if row.get(k) is None else row.get(k)) for k in STD_FIELDS}
            w.writerow(out)
    print(f"→ {OUT_PATH} ({len(merged)} rows total: {len(new_rows)} historical, "
          f"{len(existing_rows)} pre-existing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
