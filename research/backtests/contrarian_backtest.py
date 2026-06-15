"""
research/backtests/contrarian_backtest.py — CONTRARIAN scanner-only baseline.

First clean baseline for the locked CONTRARIAN identity:
panic / dislocation / forced-selling rebound, LONG only.

No council redesign. No threshold tuning. No news/catalyst filter.

Historical limitation:
  The production scanner uses live FMP VIX. This backtest has no local historical
  VIX cache, so it uses the existing platform convention from sniper_backtest.py:
  a 20-day annualized SPY realized-volatility proxy. Treat VIX bucket results as
  proxy-regime diagnostics, not exact historical FMP VIX replay.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
      .venv/bin/python research/backtests/contrarian_backtest.py [--quick] [--variants]
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
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


# Production CONTRARIAN constants.
VIX_ACTIVE_TRIGGER = 28.0
VIX_WATCH_TRIGGER = 22.0
SPY_EXTENSION_THRESH = -0.03
SPY_RSI_THRESH = 38.0
RSI_OVERSOLD = 42
MIN_SCORE = 60
MIN_RR = 1.5
STOP_ATR_MULT = 1.5
TARGET_ATR_MULT = 2.25
BARS_NEEDED = 55

COMMISSION = 0.0005
SLIPPAGE = 0.0010
FRICTION_RT = (COMMISSION + SLIPPAGE) * 2

FULL_START = date(2020, 1, 1)
FULL_END = date(2024, 12, 31)
QUICK_START = date(2022, 1, 1)
QUICK_END = date(2023, 12, 31)

HORIZONS = [1, 3, 5, 10]

TRADE_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "META", "GOOGL", "AMZN", "NFLX", "AVGO",
    "QCOM", "AMAT", "LRCX", "MRVL", "CRM", "ADBE", "PANW", "NOW", "SHOP",
    "MELI", "UBER", "ABNB", "DASH", "TSLA", "ORCL",
    "JPM", "GS", "BAC", "MS", "V", "MA", "AXP", "PYPL", "SQ",
    "LLY", "ABBV", "REGN", "TMO", "ISRG", "UNH", "MRNA", "RXRX",
    "NKE", "LULU", "DECK", "TGT", "HD", "LOW", "COST", "WMT", "PG",
    "XOM", "CVX", "OXY", "SLB", "CAT", "GE", "LMT", "RTX",
    "PLTR", "CRWD", "DDOG", "NET", "SNOW", "ZS", "TWLO", "MDB", "ROKU",
    "SNAP", "MU", "FCX", "NEM", "AXON", "TTWO", "DUOL", "ELF", "HIMS",
    "SMCI", "CELH", "CAVA", "APP", "TMDX", "RBLX",
]

ETF_CONTROLS = {
    "SPY", "QQQ", "IWM", "TLT", "GLD", "XLU", "XLP", "AGG",
    "XLK", "XLF", "XLE", "XLY", "XLV", "XLI",
}

CONTROL_UNIVERSE = ["SPY", "QQQ", "IWM", "XLK", "XLF", "XLE", "XLY", "XLV", "XLI", "TLT", "GLD", "XLU", "XLP", "AGG"]
SINGLE_STOCK_CONTROLS = {"WMT", "COST", "PG"}

QUICK_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "TSLA", "SHOP", "PLTR",
    "CRWD", "DDOG", "NET", "SNOW", "JPM", "V", "LLY", "NKE", "LULU",
    "XOM", "WMT", "COST", "PG",
]

SECTOR = {
    "AAPL": "Tech", "MSFT": "Tech", "NVDA": "Semis", "AMD": "Semis",
    "META": "Comm", "GOOGL": "Comm", "AMZN": "Consumer", "NFLX": "Comm",
    "AVGO": "Semis", "QCOM": "Semis", "AMAT": "Semis", "LRCX": "Semis",
    "MRVL": "Semis", "CRM": "Software", "ADBE": "Software", "PANW": "Software",
    "NOW": "Software", "SHOP": "Software", "MELI": "Consumer", "UBER": "Consumer",
    "ABNB": "Consumer", "DASH": "Consumer", "TSLA": "Consumer", "ORCL": "Software",
    "JPM": "Financials", "GS": "Financials", "BAC": "Financials", "MS": "Financials",
    "V": "Financials", "MA": "Financials", "AXP": "Financials",
    "PYPL": "Fintech", "SQ": "Fintech", "LLY": "Healthcare",
    "ABBV": "Healthcare", "REGN": "Healthcare", "TMO": "Healthcare",
    "ISRG": "Healthcare", "UNH": "Healthcare", "MRNA": "Biotech", "RXRX": "Biotech",
    "NKE": "Consumer", "LULU": "Consumer", "DECK": "Consumer", "TGT": "Consumer",
    "HD": "Consumer", "LOW": "Consumer", "COST": "Defensive",
    "WMT": "Defensive", "PG": "Defensive", "XOM": "Energy", "CVX": "Energy",
    "OXY": "Energy", "SLB": "Energy", "CAT": "Industrials", "GE": "Industrials",
    "LMT": "Industrials", "RTX": "Industrials", "PLTR": "Software",
    "CRWD": "Software", "DDOG": "Software", "NET": "Software",
    "SNOW": "Software", "ZS": "Software", "TWLO": "Software",
    "MDB": "Software", "ROKU": "Consumer", "SNAP": "Comm", "MU": "Semis",
    "FCX": "Materials", "NEM": "Materials", "AXON": "Industrials",
    "TTWO": "Comm", "DUOL": "Software", "ELF": "Consumer",
    "HIMS": "Healthcare", "SMCI": "Tech", "CELH": "Consumer",
    "CAVA": "Consumer", "APP": "Software", "TMDX": "Healthcare",
    "RBLX": "Consumer",
    "SPY": "ETF Control", "QQQ": "ETF Control", "IWM": "ETF Control",
    "TLT": "ETF Control", "GLD": "ETF Control", "XLU": "ETF Control",
    "XLP": "ETF Control", "AGG": "ETF Control", "XLK": "ETF Control",
    "XLF": "ETF Control", "XLE": "ETF Control", "XLY": "ETF Control",
    "XLV": "ETF Control", "XLI": "ETF Control",
}

loader = BacktestDataLoader()


def rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = statistics.mean(gains[-period:])
    al = statistics.mean(losses[-period:])
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)


def atr(bars: List[Dict]) -> float:
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return statistics.mean(trs) if trs else 0.0


def vix_mode(vix: float) -> Optional[str]:
    if vix >= VIX_ACTIVE_TRIGGER:
        return "active"
    if vix >= VIX_WATCH_TRIGGER:
        return "watch"
    return None


def rsi_gate_threshold(vix: float) -> float:
    if vix >= 35:
        return 35.0
    if vix >= 30:
        return 38.0
    return RSI_OVERSOLD


def strong_close(bars: List[Dict]) -> bool:
    if not bars:
        return False
    b = bars[-1]
    rng = b["high"] - b["low"]
    if rng <= 0:
        return False
    return (b["close"] - b["low"]) / rng >= 0.70


def reversal_candle(bars: List[Dict]) -> bool:
    if len(bars) < 2:
        return False
    prev, today = bars[-2], bars[-1]
    body = abs(today["close"] - today["open"])
    if today["close"] >= today["open"]:
        lower_wick = today["open"] - today["low"]
    else:
        lower_wick = today["close"] - today["low"]
    hammer = lower_wick >= body * 2 and body > 0
    engulf = (
        prev["close"] < prev["open"]
        and today["close"] > today["open"]
        and today["close"] > prev["open"]
        and today["open"] < prev["close"]
    )
    return hammer or engulf


def higher_low(bars: List[Dict]) -> bool:
    return len(bars) >= 2 and bars[-1]["low"] > bars[-2]["low"]


def reversal_quality(bars: List[Dict]) -> Dict:
    sc = strong_close(bars)
    rc = reversal_candle(bars)
    hl = higher_low(bars)
    count = sum([sc, rc, hl])
    return {
        "any": count >= 1,
        "strong_close": sc,
        "reversal_candle": rc,
        "higher_low": hl,
        "count": count,
    }


def score_signal(stock_rsi: float, price: float, ma50: float, reversal: Dict, vix: float) -> int:
    s = 30
    s += max(0, min(20, int((RSI_OVERSOLD - stock_rsi) * 2)))
    s += reversal["count"] * 10
    pct_from_ma = (price - ma50) / ma50
    if pct_from_ma > -0.10:
        s += 10
    if vix >= VIX_ACTIVE_TRIGGER:
        s += 10
    return max(0, min(100, s))


def build_regime_maps(spy_bars: List[Dict]) -> Tuple[Dict[date, Dict], Counter]:
    by_date: Dict[date, Dict] = {}
    rejects = Counter()
    closes = [float(b["close"]) for b in spy_bars]
    dates = [b["date"] for b in spy_bars]
    for i in range(len(spy_bars)):
        d = dates[i]
        if i < 21:
            rejects["spy_or_vix_stale"] += 1
            continue
        rets = [closes[j] / closes[j - 1] - 1 for j in range(i - 19, i + 1)]
        vix_proxy = statistics.stdev(rets) * math.sqrt(252) * 100
        mode = vix_mode(vix_proxy)
        if mode is None:
            by_date[d] = {"passed": False, "reason": "vix_inactive", "vix": vix_proxy, "mode": "inactive"}
            continue
        if i < 20:
            by_date[d] = {"passed": False, "reason": "spy_washout_stale", "vix": vix_proxy, "mode": mode}
            continue
        high_10d = max(closes[max(0, i - 9) : i + 1])
        extension = (closes[i] - high_10d) / high_10d if high_10d > 0 else 0.0
        spy_rsi = rsi(closes[max(0, i - 20) : i + 1])
        washout = extension <= SPY_EXTENSION_THRESH or spy_rsi < SPY_RSI_THRESH
        by_date[d] = {
            "passed": washout,
            "reason": "passed" if washout else "spy_washout_failed",
            "vix": vix_proxy,
            "mode": mode,
            "spy_extension": extension,
            "spy_rsi": spy_rsi,
        }
    return by_date, rejects


def evaluate_bar(bars: List[Dict], idx: int, ticker: str, regime: Dict, spy_return_20d: float) -> Tuple[Optional[Dict], str]:
    if idx < BARS_NEEDED:
        return None, "stale_bars"

    hist = bars[: idx + 1]
    closes = [float(b["close"]) for b in hist]
    today = hist[-1]
    price = float(today["close"])
    stock_rsi = rsi(closes[-20:])
    vix = float(regime["vix"])
    rsi_gate = rsi_gate_threshold(vix)
    if stock_rsi > rsi_gate:
        return None, "rsi_not_oversold"

    ma50 = statistics.mean(closes[-50:])
    if price < ma50 * 0.80:
        return None, "freefall_guard"

    reversal = reversal_quality(hist[-5:])
    if not reversal["any"]:
        return None, "no_reversal_quality"

    sc = score_signal(stock_rsi, price, ma50, reversal, vix)
    if sc < MIN_SCORE:
        return None, "low_score"

    a = atr(hist[-14:])
    stop = price - a * STOP_ATR_MULT
    target = price + a * TARGET_ATR_MULT
    if stop <= 0 or price <= stop:
        return None, "poor_geometry"
    rr = (target - price) / (price - stop)
    if rr < MIN_RR:
        return None, "poor_geometry"

    stock_return_20d = closes[-1] / closes[-21] - 1 if len(closes) >= 21 and closes[-21] > 0 else 0.0
    idio_drawdown_proxy = stock_return_20d - spy_return_20d

    return {
        "ticker": ticker,
        "date": today["date"],
        "signal_date": today["date"],
        "entry_date": today["date"],
        "bar_index": idx,
        "year": today["date"].year,
        "entry": price,
        "stop": stop,
        "target": target,
        "rr": rr,
        "score": sc,
        "rsi": stock_rsi,
        "rsi_gate": rsi_gate,
        "ma50": ma50,
        "reversal": reversal,
        "vix": vix,
        "vix_mode": regime["mode"],
        "spy_extension": regime.get("spy_extension", 0.0),
        "spy_rsi": regime.get("spy_rsi", 50.0),
        "stock_return_20d": stock_return_20d,
        "spy_return_20d": spy_return_20d,
        "idio_drawdown_proxy": idio_drawdown_proxy,
        "bad_news_trap_proxy": idio_drawdown_proxy <= -0.10,
        "sector": SECTOR.get(ticker, "Unknown"),
        "etf_control": ticker in ETF_CONTROLS,
        "single_stock_control": ticker in SINGLE_STOCK_CONTROLS,
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
    risk = entry - stop
    r_mult = (exit_price - entry) / risk if risk > 0 else 0.0
    return {
        "horizon": horizon,
        "raw": raw,
        "adj": adj,
        "r_mult": r_mult,
        "win": adj > 0,
        "stop_hit": stop_hit,
        "target_hit": target_hit,
        "exit_type": exit_type,
        "days_held": days_held,
    }


def stabilize_extreme_signal(bars: List[Dict], idx: int, signal: Dict) -> Optional[Dict]:
    """
    Variant B rule for VIX-proxy >= 35 only.

    Enter next day only if selling pressure stops making a lower low and price
    closes above the original signal close. This tests stabilization without
    requiring a breakout above the signal-day high.
    """
    if signal["vix"] < 35:
        return dict(signal)
    if idx + 1 >= len(bars):
        return None
    signal_day = bars[idx]
    confirm_day = bars[idx + 1]
    if confirm_day["close"] <= signal_day["close"]:
        return None
    if confirm_day["low"] < signal_day["low"]:
        return None

    entry = float(confirm_day["close"])
    a = atr(bars[max(0, idx + 2 - 14) : idx + 2])
    stop = entry - a * STOP_ATR_MULT
    target = entry + a * TARGET_ATR_MULT
    if stop <= 0 or entry <= stop:
        return None
    rr = (target - entry) / (entry - stop)
    if rr < MIN_RR:
        return None

    stabilized = dict(signal)
    stabilized["date"] = confirm_day["date"]
    stabilized["entry_date"] = confirm_day["date"]
    stabilized["year"] = confirm_day["date"].year
    stabilized["entry"] = entry
    stabilized["stop"] = stop
    stabilized["target"] = target
    stabilized["rr"] = rr
    stabilized["stabilized_extreme"] = True
    stabilized["outcomes"] = {h: simulate_outcome(bars, idx + 1, stabilized, h) for h in HORIZONS}
    return stabilized


def vix_bucket(vix: float) -> str:
    if vix >= 35:
        return "extreme_35_plus"
    if vix >= 28:
        return "active_28_35"
    if vix >= 22:
        return "watch_22_28"
    return "inactive_lt_22"


def pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def horizon_stats(signals: List[Dict], horizon: int) -> Optional[Dict]:
    rows = [s["outcomes"][horizon] for s in signals]
    if not rows:
        return None
    return {
        "n": len(rows),
        "wr": sum(r["win"] for r in rows) / len(rows),
        "avg_raw": statistics.mean(r["raw"] for r in rows),
        "avg_adj": statistics.mean(r["adj"] for r in rows),
        "med_adj": statistics.median(r["adj"] for r in rows),
        "stop": sum(r["stop_hit"] for r in rows) / len(rows),
        "target": sum(r["target_hit"] for r in rows) / len(rows),
        "expectancy_r": statistics.mean(r["r_mult"] for r in rows),
    }


def print_horizon_results(signals: List[Dict]) -> None:
    for h in HORIZONS:
        st = horizon_stats(signals, h)
        if not st:
            continue
        print(
            f"  {h:>2}d: n={st['n']:>3}  WR={st['wr']*100:5.1f}%  "
            f"avgRaw={pct(st['avg_raw']):>8}  avgAdj={pct(st['avg_adj']):>8}  "
            f"medAdj={pct(st['med_adj']):>8}  stopHit={st['stop']*100:5.1f}%  "
            f"targetHit={st['target']*100:5.1f}%  expR={st['expectancy_r']:+.2f}"
        )


def run(universe: List[str], start: date, end: date) -> Tuple[List[Dict], Counter, List[Dict]]:
    data_start = start - timedelta(days=450)
    spy_bars = loader.get_bars("SPY", data_start, end)
    if len(spy_bars) < 80:
        raise RuntimeError("SPY data insufficient for CONTRARIAN regime map")
    regime_by_date, rejects = build_regime_maps(spy_bars)
    spy_closes_by_date = {b["date"]: float(b["close"]) for b in spy_bars}
    spy_dates = [b["date"] for b in spy_bars]
    spy_20d_return: Dict[date, float] = {}
    for i, d in enumerate(spy_dates):
        if i >= 20 and spy_closes_by_date[spy_dates[i - 20]] > 0:
            spy_20d_return[d] = spy_closes_by_date[d] / spy_closes_by_date[spy_dates[i - 20]] - 1

    signals: List[Dict] = []
    stabilized_signals: List[Dict] = []
    data = {t: loader.get_bars(t, data_start, end) for t in universe}
    for ticker, bars in data.items():
        if not bars:
            rejects["missing_data"] += 1
            continue
        for idx, bar in enumerate(bars):
            d = bar["date"]
            if d < start or d > end:
                continue
            regime = regime_by_date.get(d)
            if not regime:
                rejects["missing_regime"] += 1
                continue
            if not regime["passed"]:
                rejects[regime["reason"]] += 1
                continue
            sig, reason = evaluate_bar(bars, idx, ticker, regime, spy_20d_return.get(d, 0.0))
            if not sig:
                rejects[reason] += 1
                continue
            sig["outcomes"] = {h: simulate_outcome(bars, idx, sig, h) for h in HORIZONS}
            signals.append(sig)
            stabilized = stabilize_extreme_signal(bars, idx, sig)
            if stabilized:
                stabilized_signals.append(stabilized)
            elif sig["vix"] >= 35:
                rejects["extreme_stabilization_failed"] += 1

    signals.sort(key=lambda s: (s["date"], s["ticker"]))
    stabilized_signals.sort(key=lambda s: (s["date"], s["ticker"]))
    return signals, rejects, stabilized_signals


def report_group(title: str, signals: List[Dict]) -> None:
    print(f"\n{title}")
    print("-" * 72)
    print(f"Signals: {len(signals)}")
    print_horizon_results(signals)


def print_year_summary(signals: List[Dict]) -> None:
    by_year: Dict[int, List[Dict]] = defaultdict(list)
    for s in signals:
        by_year[s["year"]].append(s)
    for y in sorted(by_year):
        rows = by_year[y]
        st3 = horizon_stats(rows, 3)
        st5 = horizon_stats(rows, 5)
        if not st3 or not st5:
            continue
        print(
            f"  {y}: n={len(rows):>3}  3dWR={st3['wr']*100:5.1f}%  "
            f"3dAdj={pct(st3['avg_adj']):>8}  5dWR={st5['wr']*100:5.1f}%  "
            f"5dAdj={pct(st5['avg_adj']):>8}  stop3={st3['stop']*100:5.1f}%"
        )


def print_vix_bucket_summary(signals: List[Dict]) -> None:
    by_vix: Dict[str, List[Dict]] = defaultdict(list)
    for s in signals:
        by_vix[vix_bucket(s["vix"])].append(s)
    for bucket in ["watch_22_28", "active_28_35", "extreme_35_plus"]:
        rows = by_vix.get(bucket, [])
        st = horizon_stats(rows, 3)
        if not st:
            print(f"  {bucket:<16} n=  0")
            continue
        print(
            f"  {bucket:<16} n={len(rows):>3}  WR={st['wr']*100:5.1f}%  "
            f"avgAdj={pct(st['avg_adj']):>8}  stopHit={st['stop']*100:5.1f}%"
        )


def print_concentration(signals: List[Dict]) -> None:
    ticker_counts = Counter(s["ticker"] for s in signals)
    sector_counts = Counter(s["sector"] for s in signals)
    print("  Top tickers:")
    for ticker, count in ticker_counts.most_common(8):
        print(f"    {ticker:<6} {count:>3}  ({count / len(signals) * 100 if signals else 0:4.1f}%)")
    print("  Top sectors:")
    for sector, count in sector_counts.most_common(8):
        print(f"    {sector:<14} {count:>3}  ({count / len(signals) * 100 if signals else 0:4.1f}%)")


def summarize_variants(baseline: List[Dict], stabilized: List[Dict]) -> None:
    no_extreme = [s for s in baseline if s["vix"] < 35]

    print("\nCONTRARIAN EXTREME-PANIC STRUCTURAL VARIANTS")
    print("=" * 72)
    print("Research question:")
    print("  Does CONTRARIAN fail mainly because it enters extreme panic too early?")
    print("Stabilization rule for Variant B:")
    print("  For VIX proxy >= 35 only, enter next day at close if next-day close >")
    print("  signal-day close and next-day low does not undercut signal-day low.")
    print("  This tests stabilization without requiring a breakout above signal-day high.\n")

    for title, rows in [
        ("Canonical baseline", baseline),
        ("Variant A — exclude VIX proxy >= 35", no_extreme),
        ("Variant B — next-day stabilization only for VIX proxy >= 35", stabilized),
    ]:
        print(f"\n{title}")
        print("-" * 72)
        print(f"Signals: {len(rows)}")
        print_horizon_results(rows)
        print("Year-by-year:")
        print_year_summary(rows)
        print("VIX bucket behavior (3d adjusted):")
        print_vix_bucket_summary(rows)
        print("Concentration:")
        print_concentration(rows)

    base3 = horizon_stats(baseline, 3)
    no_ext3 = horizon_stats(no_extreme, 3)
    stab3 = horizon_stats(stabilized, 3)
    base5 = horizon_stats(baseline, 5)
    no_ext5 = horizon_stats(no_extreme, 5)
    stab5 = horizon_stats(stabilized, 5)
    if base3 and no_ext3 and stab3 and base5 and no_ext5 and stab5:
        print("\nPrimary comparison:")
        print(
            f"  Baseline 3d/5d avgAdj: {pct(base3['avg_adj'])} / {pct(base5['avg_adj'])}"
        )
        print(
            f"  Variant A 3d/5d avgAdj: {pct(no_ext3['avg_adj'])} / {pct(no_ext5['avg_adj'])}"
        )
        print(
            f"  Variant B 3d/5d avgAdj: {pct(stab3['avg_adj'])} / {pct(stab5['avg_adj'])}"
        )


def summarize(signals: List[Dict], rejects: Counter, controls: List[Dict], start: date, end: date) -> None:
    print("\nCONTRARIAN SCANNER-ONLY BASELINE")
    print("=" * 72)
    print("Checklist:")
    print("  Doctrine / mandate clarity: PASS")
    print("  Scanner / council fit: PARTIAL (scanner-only; generic council not replayed)")
    print("  Data integrity: PARTIAL (daily OHLCV clean; VIX is SPY realized-vol proxy, not FMP VIX history)")
    print("  Execution realism: PASS baseline friction/ATR stop-target/half-size documented")
    print("  Output expectations: PASS\n")

    print(f"Window: {start} -> {end}")
    print(f"Signals: {len(signals)}")
    print(f"Friction: {FRICTION_RT * 100:.2f}% round trip")
    print("Entry: signal-date close")
    print("Position model: half-size documented; returns shown unlevered per signal")
    print("Historical VIX: 20d annualized SPY realized-vol proxy")

    print("\nRejection funnel (top 14):")
    for reason, count in rejects.most_common(14):
        print(f"  {reason:<24} {count:>8}")

    print("\nHorizon results:")
    print_horizon_results(signals)

    print("\nYear-by-year (3d and 5d adjusted):")
    by_year: Dict[int, List[Dict]] = defaultdict(list)
    for s in signals:
        by_year[s["year"]].append(s)
    for y in sorted(by_year):
        rows = by_year[y]
        st3 = horizon_stats(rows, 3)
        st5 = horizon_stats(rows, 5)
        if not st3 or not st5:
            continue
        print(
            f"  {y}: n={len(rows):>3}  3dWR={st3['wr']*100:5.1f}%  "
            f"3dAdj={pct(st3['avg_adj']):>8}  5dWR={st5['wr']*100:5.1f}%  "
            f"5dAdj={pct(st5['avg_adj']):>8}  stop3={st3['stop']*100:5.1f}%"
        )

    print("\nVIX proxy bucket behavior (3d adjusted):")
    by_vix: Dict[str, List[Dict]] = defaultdict(list)
    for s in signals:
        by_vix[vix_bucket(s["vix"])].append(s)
    for bucket in ["watch_22_28", "active_28_35", "extreme_35_plus"]:
        rows = by_vix.get(bucket, [])
        st = horizon_stats(rows, 3)
        if not st:
            print(f"  {bucket:<16} n=  0")
            continue
        print(
            f"  {bucket:<16} n={len(rows):>3}  WR={st['wr']*100:5.1f}%  "
            f"avgAdj={pct(st['avg_adj']):>8}  stopHit={st['stop']*100:5.1f}%"
        )

    ticker_counts = Counter(s["ticker"] for s in signals)
    sector_counts = Counter(s["sector"] for s in signals)
    print("\nSingle-name concentration (top 12):")
    for ticker, count in ticker_counts.most_common(12):
        print(f"  {ticker:<6} {count:>3}  ({count / len(signals) * 100 if signals else 0:4.1f}%)")
    print("\nSector concentration:")
    for sector, count in sector_counts.most_common():
        print(f"  {sector:<14} {count:>3}  ({count / len(signals) * 100 if signals else 0:4.1f}%)")

    single_stock_controls = [s for s in signals if s["single_stock_control"]]
    print(f"\nSingle-stock controls: {len(single_stock_controls)}/{len(signals)} ({len(single_stock_controls)/len(signals)*100 if signals else 0:.1f}%)")
    if single_stock_controls:
        print_horizon_results(single_stock_controls)

    bad_news_proxy = [s for s in signals if s["bad_news_trap_proxy"]]
    print(f"\nIdiosyncratic bad-news trap proxy: {len(bad_news_proxy)}/{len(signals)} ({len(bad_news_proxy)/len(signals)*100 if signals else 0:.1f}%)")
    print("  Proxy definition: stock 20d return trails SPY 20d return by at least 10 percentage points.")

    print("\nRepresentative signals:")
    samples = signals[:4] + sorted(signals, key=lambda s: s["score"], reverse=True)[:4]
    seen = set()
    for s in samples:
        key = (s["date"], s["ticker"])
        if key in seen:
            continue
        seen.add(key)
        o3 = s["outcomes"][3]
        rev = s["reversal"]
        rev_flags = ",".join(k for k in ("strong_close", "reversal_candle", "higher_low") if rev.get(k)) or "none"
        print(
            f"  {s['date']} {s['ticker']:<5} score={s['score']:>2} "
            f"vixProxy={s['vix']:4.1f} mode={s['vix_mode']:<6} rsi={s['rsi']:4.1f}/{s['rsi_gate']:4.1f} "
            f"spyExt={s['spy_extension']*100:+.1f}% spyRSI={s['spy_rsi']:4.1f} "
            f"rev={rev_flags:<37} 3dAdj={pct(o3['adj'])} exit={o3['exit_type']} "
            f"idioProxy={s['idio_drawdown_proxy']*100:+.1f}%"
        )

    report_group("ETF controls only (not tradable baseline)", controls)

    print("\nIdentity check:")
    print("  Signals require fear regime, SPY washout, stock oversold, and reversal-quality evidence.")
    print("  No breakout, catalyst/news, quiet-flow, or short-continuation trigger is used.")
    print("  Company-specific bad-news cannot be proven from OHLCV; the idiosyncratic drawdown proxy is diagnostic only.")

    print("\nScanner + council note:")
    print("  Council replay was not run. The known generic LONG council mismatch still matters conceptually,")
    print("  but council work should wait until this scanner-only baseline is diagnosed.")

    primary3 = horizon_stats(signals, 3)
    primary5 = horizon_stats(signals, 5)
    enough_n = len(signals) >= 40
    stable_years = sum(1 for rows in by_year.values() if horizon_stats(rows, 3) and horizon_stats(rows, 3)["avg_adj"] > 0)
    if enough_n and primary3 and primary5 and primary3["avg_adj"] > 0 and primary5["avg_adj"] > 0 and stable_years >= 3:
        verdict = "PAPER-READY CANDIDATE, subject to real VIX replay and paper validation"
    elif enough_n and primary5 and primary5["avg_adj"] > 0:
        verdict = "ONE NARROW RESEARCH PASS JUSTIFIED"
    elif enough_n:
        verdict = "RESEARCH-ONLY"
    else:
        verdict = "RESEARCH-ONLY: insufficient sample"
    print(f"\nVerdict: {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(description="CONTRARIAN scanner-only baseline backtest")
    parser.add_argument("--quick", action="store_true", help="Small universe/window smoke run")
    parser.add_argument("--variants", action="store_true", help="Report extreme-panic structural variants")
    args = parser.parse_args()

    start = QUICK_START if args.quick else FULL_START
    end = QUICK_END if args.quick else FULL_END
    universe = QUICK_UNIVERSE if args.quick else TRADE_UNIVERSE

    print(f"Tradable single-stock universe={len(universe)}  ETF controls={len(CONTROL_UNIVERSE)}  Window={start} -> {end}")
    signals, rejects, stabilized = run(universe, start, end)
    controls, control_rejects, _control_stabilized = run(CONTROL_UNIVERSE, start, end)
    for reason, count in control_rejects.items():
        rejects[f"control_{reason}"] += count
    if args.variants:
        summarize_variants(signals, stabilized)
    else:
        summarize(signals, rejects, controls, start, end)


if __name__ == "__main__":
    main()
