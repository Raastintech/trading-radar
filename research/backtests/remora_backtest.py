"""
research/backtests/remora_backtest.py — REMORA quiet-flow baseline validation.

This is the first clean baseline for the locked REMORA identity:
stealth institutional accumulation / quiet flow.

No catalyst/news logic. No breakout-confirmation logic. No tuning pass.

Historical limitation:
  The production scanner uses live bid/ask spread. Historical quote data is not
  available in the OHLCV backtest cache, so this baseline tests the production
  price/volume/geometry logic and reports the spread gate as not historically
  testable. Do not treat this as paper-ready spread validation.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
      .venv/bin/python research/backtests/remora_backtest.py [--quick] [--single-stock-only]
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_env = os.environ.get("SNIPER_ENV_PATH", "")
if _env and os.path.exists(_env):
    with open(_env) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

sys.path.insert(0, str(Path(__file__).parent))

from backtest_data_loader import BacktestDataLoader


# Production REMORA constants.
STEALTH_PRICE_CHANGE = 0.005
VOL_FLOOR_RATIO = 1.20
VOL_CEIL_RATIO = 1.60
MIN_DOLLAR_VOL = 25_000_000
PCT_FROM_52W_HIGH = 0.02
MIN_SCORE = 55
BARS_NEEDED = 252
STOP_ATR_MULT = 1.2
TARGET_ATR_MULT = 3.0
MIN_RR = 2.0

COMMISSION = 0.0005
SLIPPAGE = 0.0010
FRICTION_RT = (COMMISSION + SLIPPAGE) * 2

FULL_START = date(2020, 1, 1)
FULL_END = date(2024, 12, 31)
QUICK_START = date(2022, 1, 1)
QUICK_END = date(2023, 12, 31)

HORIZONS = [1, 3, 5]

REMORA_UNIVERSE = [
    # Mega/liquid leaders
    "AAPL", "MSFT", "NVDA", "AMD", "META", "GOOGL", "AMZN", "NFLX", "AVGO",
    "QCOM", "AMAT", "LRCX", "MRVL", "CRM", "ADBE", "PANW", "NOW", "SHOP",
    "MELI", "UBER", "ABNB", "DASH",
    # Financials / payments
    "JPM", "GS", "BAC", "MS", "V", "MA", "AXP", "PYPL", "SQ",
    # Healthcare / quality
    "LLY", "ABBV", "REGN", "TMO", "ISRG", "UNH", "MRNA",
    # Consumer / industrial / energy
    "NKE", "LULU", "DECK", "HD", "LOW", "COST", "WMT", "XOM", "CVX",
    "CAT", "GE", "LMT", "RTX",
    # Mid-vol / activity names
    "PLTR", "CRWD", "DDOG", "NET", "SNOW", "ZS", "TWLO", "MDB", "ROKU",
    "SNAP", "MU", "FCX", "NEM", "AXON", "TTWO", "DUOL", "ELF", "HIMS",
    "SMCI", "CELH", "CAVA", "APP", "TMDX",
    # ETFs / controls and regime markers
    "SPY", "QQQ", "IWM", "XLK", "XLF", "XLE", "XLY", "XLV", "XLI",
    "TLT", "GLD", "XLU", "XLP", "AGG",
]

QUICK_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "SHOP", "PLTR", "CRWD",
    "DDOG", "NET", "SNOW", "JPM", "V", "LLY", "NKE", "LULU", "XOM",
    "SPY", "QQQ", "TLT", "GLD", "XLU", "XLP",
]

CONTROLS = {
    "SPY", "QQQ", "IWM", "TLT", "GLD", "XLU", "XLP", "AGG",
    "XLK", "XLF", "XLE", "XLY", "XLV", "XLI",
}

SECTOR = {
    "AAPL": "Tech", "MSFT": "Tech", "NVDA": "Semis", "AMD": "Semis",
    "META": "Comm", "GOOGL": "Comm", "AMZN": "Consumer", "NFLX": "Comm",
    "AVGO": "Semis", "QCOM": "Semis", "AMAT": "Semis", "LRCX": "Semis",
    "MRVL": "Semis", "CRM": "Software", "ADBE": "Software", "PANW": "Software",
    "NOW": "Software", "SHOP": "Software", "MELI": "Consumer", "UBER": "Consumer",
    "ABNB": "Consumer", "DASH": "Consumer", "JPM": "Financials",
    "GS": "Financials", "BAC": "Financials", "MS": "Financials",
    "V": "Financials", "MA": "Financials", "AXP": "Financials",
    "PYPL": "Fintech", "SQ": "Fintech", "LLY": "Healthcare",
    "ABBV": "Healthcare", "REGN": "Healthcare", "TMO": "Healthcare",
    "ISRG": "Healthcare", "UNH": "Healthcare", "MRNA": "Biotech",
    "NKE": "Consumer", "LULU": "Consumer", "DECK": "Consumer",
    "HD": "Consumer", "LOW": "Consumer", "COST": "Defensive",
    "WMT": "Defensive", "XOM": "Energy", "CVX": "Energy",
    "CAT": "Industrials", "GE": "Industrials", "LMT": "Industrials",
    "RTX": "Industrials", "PLTR": "Software", "CRWD": "Software",
    "DDOG": "Software", "NET": "Software", "SNOW": "Software",
    "ZS": "Software", "TWLO": "Software", "MDB": "Software",
    "ROKU": "Consumer", "SNAP": "Comm", "MU": "Semis", "FCX": "Materials",
    "NEM": "Materials", "AXON": "Industrials", "TTWO": "Comm",
    "DUOL": "Software", "ELF": "Consumer", "HIMS": "Healthcare",
    "SMCI": "Tech", "CELH": "Consumer", "CAVA": "Consumer",
    "APP": "Software", "TMDX": "Healthcare",
    "SPY": "Control", "QQQ": "Control", "IWM": "Control", "TLT": "Control",
    "GLD": "Control", "XLU": "Control", "XLP": "Control", "AGG": "Control",
    "XLK": "Control", "XLF": "Control", "XLE": "Control", "XLY": "Control",
    "XLV": "Control", "XLI": "Control",
}


loader = BacktestDataLoader()


def atr(bars: List[Dict]) -> float:
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return statistics.mean(trs) if trs else 0.0


def score(vol_ratio: float, pct_from_high: float, dollar_vol: float) -> int:
    s = 40
    s += max(0, 15 - abs(vol_ratio - 1.4) * 30)
    s += max(0, 15 - pct_from_high * 500)
    # Historical quote spread unavailable; omit spread points from the baseline.
    if dollar_vol > 50_000_000:
        s += 10
    elif dollar_vol > 25_000_000:
        s += 5
    return max(0, min(100, int(s)))


def evaluate_bar(bars: List[Dict], idx: int, ticker: str) -> Tuple[Optional[Dict], str]:
    if idx < BARS_NEEDED:
        return None, "stale_bars"

    hist = bars[: idx + 1]
    today = hist[-1]
    prev = hist[-2]
    closes = [b["close"] for b in hist]
    volumes = [b["volume"] for b in hist]

    today_close = float(today["close"])
    prev_close = float(prev["close"])
    today_vol = int(today["volume"])
    avg_vol_20 = statistics.mean(volumes[-20:])
    vol_ratio = today_vol / avg_vol_20 if avg_vol_20 > 0 else 0.0
    price_chg = abs(today_close - prev_close) / prev_close if prev_close > 0 else 1.0
    dollar_vol = today_close * today_vol
    high_52w = max(closes[-252:])
    pct_from_high = (high_52w - today_close) / high_52w if high_52w > 0 else 1.0

    if price_chg >= STEALTH_PRICE_CHANGE:
        return None, "price_moved"
    if vol_ratio < VOL_FLOOR_RATIO:
        return None, "vol_too_low"
    if vol_ratio > VOL_CEIL_RATIO:
        return None, "vol_too_high"
    if dollar_vol < MIN_DOLLAR_VOL:
        return None, "low_dollar_vol"
    if pct_from_high > PCT_FROM_52W_HIGH:
        return None, "not_near_52w_high"

    a = atr(hist[-14:])
    stop = today_close - a * STOP_ATR_MULT
    target = today_close + a * TARGET_ATR_MULT
    if stop <= 0 or today_close <= stop:
        return None, "poor_geometry"
    rr = (target - today_close) / (today_close - stop)
    if rr < MIN_RR:
        return None, "poor_geometry"

    sc = score(vol_ratio, pct_from_high, dollar_vol)
    if sc < MIN_SCORE:
        return None, "low_score"

    return {
        "ticker": ticker,
        "date": today["date"],
        "year": today["date"].year,
        "entry": today_close,
        "stop": stop,
        "target": target,
        "rr": rr,
        "score": sc,
        "vol_ratio": vol_ratio,
        "price_chg": price_chg,
        "dollar_vol": dollar_vol,
        "pct_from_high": pct_from_high,
        "sector": SECTOR.get(ticker, "Unknown"),
        "control": ticker in CONTROLS,
    }, ""


def simulate_outcome(bars: List[Dict], idx: int, signal: Dict, horizon: int) -> Dict:
    entry = signal["entry"]
    stop = signal["stop"]
    target = signal["target"]
    future = bars[idx + 1 : idx + 1 + horizon]
    exit_price = future[-1]["close"] if future else entry
    exit_type = "time"
    stop_hit = False
    target_hit = False
    days_held = len(future)

    for day_num, b in enumerate(future, 1):
        if b["low"] <= stop:
            exit_price = stop
            exit_type = "stop"
            stop_hit = True
            days_held = day_num
            break
        if b["high"] >= target:
            exit_price = target
            exit_type = "target"
            target_hit = True
            days_held = day_num
            break

    raw = (exit_price - entry) / entry
    adj = raw - FRICTION_RT
    return {
        "horizon": horizon,
        "raw": raw,
        "adj": adj,
        "win": adj > 0,
        "stop_hit": stop_hit,
        "target_hit": target_hit,
        "exit_type": exit_type,
        "days_held": days_held,
    }


def run(universe: List[str], start: date, end: date) -> Tuple[List[Dict], Counter]:
    data_start = date(start.year - 1, start.month, start.day)
    data = {t: loader.get_bars(t, data_start, end) for t in universe}
    signals: List[Dict] = []
    rejects: Counter = Counter()

    for ticker, bars in data.items():
        if not bars:
            rejects["no_data"] += 1
            continue
        for idx, bar in enumerate(bars):
            d = bar["date"]
            if d < start or d > end:
                continue
            sig, reason = evaluate_bar(bars, idx, ticker)
            if sig:
                outcomes = {h: simulate_outcome(bars, idx, sig, h) for h in HORIZONS}
                sig["outcomes"] = outcomes
                signals.append(sig)
            else:
                rejects[reason] += 1

    signals.sort(key=lambda s: (s["date"], s["ticker"]))
    return signals, rejects


def pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def horizon_rows(signals: List[Dict], horizon: int) -> List[Dict]:
    return [s["outcomes"][horizon] for s in signals]


def print_horizon_results(signals: List[Dict]) -> None:
    for h in HORIZONS:
        rows = horizon_rows(signals, h)
        if not rows:
            continue
        wr = sum(r["win"] for r in rows) / len(rows)
        avg_raw = statistics.mean(r["raw"] for r in rows)
        avg_adj = statistics.mean(r["adj"] for r in rows)
        med_adj = statistics.median(r["adj"] for r in rows)
        stop_rate = sum(r["stop_hit"] for r in rows) / len(rows)
        target_rate = sum(r["target_hit"] for r in rows) / len(rows)
        print(
            f"  {h}d: n={len(rows):>3}  WR={wr*100:5.1f}%  "
            f"avgRaw={pct(avg_raw):>8}  avgAdj={pct(avg_adj):>8}  "
            f"medAdj={pct(med_adj):>8}  stopHit={stop_rate*100:5.1f}%  "
            f"targetHit={target_rate*100:5.1f}%"
        )


def summarize_control_comparison(signals: List[Dict]) -> None:
    print("\nControl comparison (reporting only, not tradable in single-stock mode):")
    print(f"  Control signals: {len(signals)}")
    if not signals:
        return
    print_horizon_results(signals)
    by_year: Dict[int, List[Dict]] = defaultdict(list)
    for s in signals:
        by_year[s["year"]].append(s["outcomes"][3])
    print("  Year-by-year control behavior (3d adjusted):")
    for y in sorted(by_year):
        rows = by_year[y]
        wr = sum(r["win"] for r in rows) / len(rows)
        avg_adj = statistics.mean(r["adj"] for r in rows)
        stop_rate = sum(r["stop_hit"] for r in rows) / len(rows)
        print(f"    {y}: n={len(rows):>3}  WR={wr*100:5.1f}%  avgAdj={pct(avg_adj):>8}  stopHit={stop_rate*100:5.1f}%")


def summarize(signals: List[Dict], rejects: Counter, title: str) -> None:
    print(f"\n{title}")
    print("=" * 72)
    print("Checklist:")
    print("  Doctrine / mandate clarity: PASS")
    print("  Scanner / council fit: PARTIAL (scanner-only baseline; council historical comparison not practical)")
    print("  Data integrity: PARTIAL (OHLCV clean; historical quote spread unavailable and explicitly not simulated)")
    print("  Execution realism: PASS baseline friction/ATR stop-target defined")
    print("  Output expectations: PASS\n")

    print(f"Signals: {len(signals)}")
    print(f"Friction: {FRICTION_RT * 100:.2f}% round trip")
    print("Spread gate: not historically tested (no quote history); production still fails closed on no_quote")

    print("\nRejection funnel (top 12):")
    for reason, count in rejects.most_common(12):
        print(f"  {reason:<24} {count:>8}")

    print("\nHorizon results:")
    print_horizon_results(signals)

    print("\nYear-by-year (3d adjusted, primary tactical horizon):")
    by_year: Dict[int, List[Dict]] = defaultdict(list)
    for s in signals:
        by_year[s["year"]].append(s["outcomes"][3])
    for y in sorted(by_year):
        rows = by_year[y]
        wr = sum(r["win"] for r in rows) / len(rows)
        avg_adj = statistics.mean(r["adj"] for r in rows)
        stop_rate = sum(r["stop_hit"] for r in rows) / len(rows)
        print(f"  {y}: n={len(rows):>3}  WR={wr*100:5.1f}%  avgAdj={pct(avg_adj):>8}  stopHit={stop_rate*100:5.1f}%")

    controls = [s for s in signals if s["control"]]
    print(f"\nControls: {len(controls)}/{len(signals)} ({(len(controls)/len(signals)*100 if signals else 0):.1f}%)")

    ticker_counts = Counter(s["ticker"] for s in signals)
    sector_counts = Counter(s["sector"] for s in signals)
    print("\nTicker concentration (top 10):")
    for t, c in ticker_counts.most_common(10):
        print(f"  {t:<6} {c:>3}  ({c / len(signals) * 100:4.1f}%)")
    print("\nSector concentration:")
    for sec, c in sector_counts.most_common():
        print(f"  {sec:<12} {c:>3}  ({c / len(signals) * 100:4.1f}%)")

    print("\nRepresentative signals:")
    for s in signals[:8]:
        o3 = s["outcomes"][3]
        print(
            f"  {s['date']} {s['ticker']:<5} score={s['score']:>2} "
            f"vol={s['vol_ratio']:.2f}x priceChg={s['price_chg']*100:.2f}% "
            f"fromHigh={s['pct_from_high']*100:.2f}% 3dAdj={pct(o3['adj'])} exit={o3['exit_type']}"
        )

    print("\nIdentity check:")
    print("  Selected signals satisfy quiet price, moderate abnormal volume, and near-high gates.")
    print("  No catalyst/news fields are used. No breakout trigger is used.")
    print("  Distinct from SNIPER: volume capped at 1.60x and price move must stay <0.5%.")
    print("  Distinct from VOYAGER: single-day signal with 1d/3d/5d horizons, not 6-18 months.")

    print("\nScanner + council comparison:")
    print("  Not run historically. Current council requires live intraday bars, live quote,")
    print("  FMP sentiment, and same-day macro/portfolio state. Running it on historical")
    print("  daily bars would fabricate inputs. Use scanner-only baseline now; add")
    print("  scanner+council replay only if historical intraday/quote snapshots are available.")

    # Verdict
    primary = [s["outcomes"][3] for s in signals]
    enough_n = len(signals) >= 40
    avg_adj = statistics.mean(r["adj"] for r in primary) if primary else 0.0
    wr = sum(r["win"] for r in primary) / len(primary) if primary else 0.0
    stop_rate = sum(r["stop_hit"] for r in primary) / len(primary) if primary else 1.0
    control_rate = len(controls) / len(signals) if signals else 1.0
    if enough_n and avg_adj > 0 and wr >= 0.50 and stop_rate < 0.45 and control_rate < 0.20:
        verdict = "PAPER-READY, subject to live spread/council replay caveat"
    elif enough_n and avg_adj > 0:
        verdict = "ONE NARROW RESEARCH PASS JUSTIFIED"
    elif enough_n:
        verdict = "RESEARCH-ONLY"
    else:
        verdict = "RESEARCH-ONLY: insufficient sample"
    print(f"\nVerdict: {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(description="REMORA quiet-flow baseline backtest")
    parser.add_argument("--quick", action="store_true", help="Small universe/window smoke run")
    parser.add_argument(
        "--single-stock-only",
        action="store_true",
        help="Exclude ETF/control instruments from the tradable universe and report controls separately",
    )
    args = parser.parse_args()
    universe = QUICK_UNIVERSE if args.quick else REMORA_UNIVERSE
    start = QUICK_START if args.quick else FULL_START
    end = QUICK_END if args.quick else FULL_END
    if args.single_stock_only:
        trade_universe = [t for t in universe if t not in CONTROLS]
        control_universe = [t for t in universe if t in CONTROLS]
        print(
            f"Mode=single-stock-only  Tradable={len(trade_universe)}  "
            f"Controls={len(control_universe)}  Window={start} -> {end}"
        )
        signals, rejects = run(trade_universe, start, end)
        summarize(signals, rejects, "REMORA SINGLE-STOCK-ONLY QUIET-FLOW BACKTEST")
        control_signals, _control_rejects = run(control_universe, start, end)
        summarize_control_comparison(control_signals)
    else:
        print(f"Universe={len(universe)}  Window={start} -> {end}")
        signals, rejects = run(universe, start, end)
        summarize(signals, rejects, "REMORA BASELINE QUIET-FLOW BACKTEST")


if __name__ == "__main__":
    main()
