#!/usr/bin/env python3
"""
scripts/prefetch_backtest_data.py — Warm the backtest price cache before a run.

Fetches OHLCV history from Alpaca for the requested universe + date range and
writes it to cache/backtest_prices/{TICKER}.parquet.  After running this script,
all three backtest scripts (sniper, voyager, short) will read from the local
parquet cache with zero Alpaca calls.

Usage
-----
    # Warm sniper universe for the standard 5-year window
    python scripts/prefetch_backtest_data.py --universe sniper

    # Warm voyager universe, custom date range
    python scripts/prefetch_backtest_data.py --universe voyager \\
        --start 2019-01-01 --end 2024-12-31

    # Warm a one-off list of tickers
    python scripts/prefetch_backtest_data.py --tickers AAPL MSFT NVDA \\
        --start 2020-01-01 --end 2024-12-31

    # Warm all known universes
    python scripts/prefetch_backtest_data.py --universe all

Available universes: sniper, voyager, short, short_b, all
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# ── Bootstrap path and credentials ───────────────────────────────────────────
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))

_cred = os.environ.get("SNIPER_ENV_PATH", "/home/gem/secure/trading.env")
if Path(_cred).exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_cred, override=True)
    except ImportError:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("prefetch")

# ── Universe definitions (kept in sync with individual backtest scripts) ──────

SNIPER_UNIVERSE = [
    "NVDA","AMD","META","TSLA","SHOP","PLTR","CRWD","DDOG","NET","MDB",
    "SNOW","ZS","TWLO","MELI","AVGO","QCOM","AMAT","LRCX","MRVL",
    "AAPL","MSFT","GOOGL","AMZN","NFLX",
    "JPM","GS","V","MA","PYPL","SQ",
    "LLY","ABBV","REGN","MRNA",
    "NKE","LULU","DECK","TGT","HD","LOW",
    "XOM","CVX","OXY","SLB",
    "CRM","NOW","WDAY","ADBE","PANW",
    "UBER","ABNB","DASH","RBLX",
    "QQQ","IWM","XLK","XLF","XLE","XLY","XLV","XLI",
    "TLT","GLD","XLU","XLP","AGG","WMT","COST","PG",
    "SPY",  # regime benchmark
]

VOYAGER_UNIVERSE = [
    # Core accumulation candidates + broad market benchmarks
    "NVDA","AMD","META","AAPL","MSFT","GOOGL","AMZN","NFLX","AVGO",
    "QCOM","AMAT","LRCX","MRVL","TSLA",
    "JPM","GS","V","MA","BAC","BRK-B",
    "LLY","ABBV","REGN","UNH","JNJ",
    "NKE","LULU","HD","LOW","COST","WMT","TGT",
    "XOM","CVX","OXY",
    "CRM","NOW","WDAY","ADBE","PANW","CRWD","SNOW","DDOG","NET","ZS",
    "SHOP","MELI","UBER","ABNB","BKNG",
    "QQQ","IWM","SPY","XLK","XLF","XLE","XLY","XLV","XLI","XLU","XLP",
    "TLT","GLD","AGG",
]

SHORT_UNIVERSE = [
    # High-beta growth — frequent earnings misses 2022-2024
    "NFLX","SNAP","ROKU","PYPL","SQ","SHOP","DOCU","ZM","COIN",
    "RIVN","PLTR","HOOD","AFRM","UPST",
    # Consumer / retail — cyclical earnings risk
    "LULU","NKE","ETSY","W","BBWI","ANF","M","KSS","PTON",
    "CHWY","DG","FIVE","DLTR",
    # Semis / tech hardware
    "INTC","DELL","WDC","NTAP",
    # Controls
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META",
    # Benchmarks
    "SPY","QQQ",
]

SHORT_B_UNIVERSE = [
    # Prior leaders that broke 2021-2024
    "PLTR","NET","SNOW","MDB","TWLO","DDOG","ZS","RBLX",
    "SHOP","COIN","AFRM","UPST","HOOD","RIVN","LCID",
    "ZM","DOCU","PTON","SNAP","ROKU",
    "PYPL","SQ","ETSY","W",
    "LULU","NKE","BBWI","ANF",
    "NFLX","META","GOOGL","AMZN",
    # Controls
    "AAPL","MSFT","NVDA","JPM","V",
    # Benchmarks
    "SPY","QQQ","IWM",
]

UNIVERSE_MAP = {
    "sniper":  SNIPER_UNIVERSE,
    "voyager": VOYAGER_UNIVERSE,
    "short":   SHORT_UNIVERSE,
    "short_b": SHORT_B_UNIVERSE,
}

# Default date range: 2020-01-01 → yesterday
_DEFAULT_START = date(2020, 1, 1)
_DEFAULT_END   = date.today() - timedelta(days=1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Warm backtest price cache (cache/backtest_prices/) from Alpaca.",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--universe", "-u",
        choices=list(UNIVERSE_MAP) + ["all"],
        help="Named universe to prefetch (or 'all' for every universe).",
    )
    src.add_argument(
        "--tickers", "-t",
        nargs="+",
        metavar="TICKER",
        help="Explicit list of tickers to prefetch.",
    )
    parser.add_argument(
        "--start", "-s",
        default=_DEFAULT_START.isoformat(),
        help=f"Start date YYYY-MM-DD (default: {_DEFAULT_START})",
    )
    parser.add_argument(
        "--end", "-e",
        default=_DEFAULT_END.isoformat(),
        help=f"End date YYYY-MM-DD (default: {_DEFAULT_END})",
    )
    # Extra lookback: backtests use bars[-N:] relative to the first signal date,
    # so we fetch further back than the nominal start to give the scanner enough
    # historical context.  Default: 250 extra calendar days (~175 trading days).
    parser.add_argument(
        "--lookback-days", "-l",
        type=int,
        default=500,
        metavar="N",
        help="Extra calendar days to fetch before --start (default: 500). "
             "Must cover max SPY lookback used by any backtest — sniper uses "
             "400 calendar days before start_date for the 200d MA gate.",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Re-fetch even if cache already covers the range.",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start) - timedelta(days=args.lookback_days)
    end   = date.fromisoformat(args.end)

    if args.tickers:
        tickers = list(dict.fromkeys(t.upper() for t in args.tickers))  # dedup, preserve order
        label   = f"{len(tickers)} explicit tickers"
    elif args.universe == "all":
        merged: dict = {}
        for u in UNIVERSE_MAP.values():
            for t in u:
                merged[t] = None
        tickers = list(merged)
        label   = f"all universes ({len(tickers)} unique tickers)"
    else:
        tickers = list(dict.fromkeys(UNIVERSE_MAP[args.universe]))
        label   = f"'{args.universe}' universe ({len(tickers)} tickers)"

    logger.info("Prefetching %s  [%s → %s]", label, start, end)
    logger.info("Cache: cache/backtest_prices/")
    if args.lookback_days:
        logger.info("Extra lookback: %d calendar days before %s", args.lookback_days,
                    date.fromisoformat(args.start))

    # Import here so path is set up first
    sys.path.insert(0, str(_repo_root / "research" / "backtests"))
    from backtest_data_loader import BacktestDataLoader

    loader = BacktestDataLoader()

    if args.force:
        # Clear cached files so get_bars_batch treats everything as a miss.
        cleared = 0
        for ticker in tickers:
            path = loader._cache_path(ticker)
            if path.exists():
                path.unlink()
                cleared += 1
        if cleared:
            logger.info("--force: removed %d existing cache files", cleared)

    t0 = time.time()
    result = loader.get_bars_batch(tickers, start, end)
    elapsed = time.time() - t0

    # ── Report ────────────────────────────────────────────────────────────────
    missing = [t for t in tickers if t not in result]
    logger.info(
        "Done in %.1fs — %d/%d tickers cached  |  %d missing",
        elapsed, len(result), len(tickers), len(missing),
    )

    if missing:
        logger.warning("No data for: %s", ", ".join(missing))

    # Bar count summary
    counts = sorted(((t, len(df)) for t, df in result.items()), key=lambda x: x[1])
    if counts:
        min_t, min_n = counts[0]
        max_t, max_n = counts[-1]
        avg_n = sum(n for _, n in counts) // len(counts)
        logger.info(
            "Bar counts — min: %s=%d  avg: %d  max: %s=%d",
            min_t, min_n, avg_n, max_t, max_n,
        )

    # Warn if any ticker has fewer bars than a standard backtest needs
    BARS_WARN_THRESHOLD = 200
    thin = [(t, n) for t, n in counts if n < BARS_WARN_THRESHOLD]
    if thin:
        logger.warning(
            "%d tickers have < %d bars (may cause stale_bars rejections): %s",
            len(thin), BARS_WARN_THRESHOLD,
            ", ".join(f"{t}={n}" for t, n in thin),
        )


if __name__ == "__main__":
    main()
