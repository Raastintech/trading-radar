"""
research/backtests/pathfinder_backtest.py - PATHFINDER_V1 baseline validation.

This is the first honest baseline scaffold for the future PATHFINDER sleeve:
early sponsorship / emerging leader.

Important data rule:
  Do not use current FMP fundamentals as historical fundamentals. That would be
  lookahead. This baseline uses cache-first Alpaca OHLCV and price/tape gates by
  default. A future pass may add a point-in-time local fundamentals cache via
  --fundamentals-json.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
      .venv/bin/python research/backtests/pathfinder_backtest.py [--quick]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


BASELINE_TAG = "PATHFINDER_V1"
PIT_PROXY_TAG = "PATHFINDER_V1_PIT_FUNDAMENTALS_PROXY"

MIN_PRICE = 8.0
MIN_AVG_DOLLAR_VOL_20 = 5_000_000
MAX_AVG_DOLLAR_VOL_20 = 150_000_000
BARS_NEEDED = 220
RS_20_MIN = 0.03
RS_60_MIN = 0.00
MAX_EXTENSION_20D_LOW = 0.25
MAX_EXTENSION_MA50 = 0.12
MIN_DVOL_RATIO = 1.05
MAX_VOLUME_SPIKE = 2.50
MAX_PRIOR_HIGH_DISTANCE = 0.08
MIN_FUND_SCORE = 55
MIN_SCORE = 65
STOP_ATR_MULT = 1.8
TARGET_ATR_MULT = 4.5
MIN_RR = 2.5

COMMISSION = 0.0005
SLIPPAGE = 0.0010
FRICTION_RT = (COMMISSION + SLIPPAGE) * 2

FULL_START = date(2020, 1, 1)
FULL_END = date(2024, 12, 31)
QUICK_START = date(2022, 1, 1)
QUICK_END = date(2023, 12, 31)
HORIZONS = [5, 10, 20, 60]
PRIMARY_HORIZON = 20

PATHFINDER_UNIVERSE = [
    "PLTR", "CRWD", "DDOG", "NET", "SNOW", "ZS", "MDB", "DUOL", "APP",
    "HIMS", "TMDX", "AXON", "CELH", "ELF", "CAVA", "SMCI", "FOUR", "BILL",
    "TOST", "AFRM", "UPST", "RBLX", "ROKU", "U", "PATH", "IOT", "ESTC",
    "FROG", "S", "GTLB", "CFLT", "DOCN", "ASAN", "NCNO", "GLBE", "ONON",
    "BROS", "SHAK", "WING", "BOOT", "TGTX", "HALO", "BEAM", "RXRX", "INSP",
    "NTRA", "PEN", "ENPH", "RUN", "FSLR", "AEHR", "ALGM", "LSCC", "WOLF",
    "SPY", "QQQ", "IWM",
]

QUICK_UNIVERSE = [
    "PLTR", "CRWD", "NET", "DDOG", "APP", "HIMS", "AXON", "CELH", "ELF",
    "SMCI", "DUOL", "ONON", "SPY", "QQQ", "IWM",
]

CONTROLS = {"SPY", "QQQ", "IWM"}
PIT_CACHE_DIR = Path(__file__).resolve().parents[2] / "cache" / "pathfinder_fundamentals"
PIT_SCORE_PATH = PIT_CACHE_DIR / "pathfinder_v1_pit_scores.json"
PIT_DEFAULT_LAG_DAYS = 60

SECTOR = {
    "PLTR": "Software", "CRWD": "Software", "DDOG": "Software", "NET": "Software",
    "SNOW": "Software", "ZS": "Software", "MDB": "Software", "DUOL": "Software",
    "APP": "Software", "HIMS": "Healthcare", "TMDX": "Healthcare",
    "AXON": "Industrials", "CELH": "Consumer", "ELF": "Consumer",
    "CAVA": "Consumer", "SMCI": "Tech", "FOUR": "Fintech", "BILL": "Software",
    "TOST": "Fintech", "AFRM": "Fintech", "UPST": "Fintech", "RBLX": "Consumer",
    "ROKU": "Consumer", "U": "Software", "PATH": "Software", "IOT": "Software",
    "ESTC": "Software", "FROG": "Software", "S": "Software", "GTLB": "Software",
    "CFLT": "Software", "DOCN": "Software", "ASAN": "Software",
    "NCNO": "Software", "GLBE": "Software", "ONON": "Consumer",
    "BROS": "Consumer", "SHAK": "Consumer", "WING": "Consumer",
    "BOOT": "Consumer", "TGTX": "Biotech", "HALO": "Biotech",
    "BEAM": "Biotech", "RXRX": "Biotech", "INSP": "Healthcare",
    "NTRA": "Healthcare", "PEN": "Healthcare", "ENPH": "Energy",
    "RUN": "Energy", "FSLR": "Energy", "AEHR": "Semis", "ALGM": "Semis",
    "LSCC": "Semis", "WOLF": "Semis", "SPY": "Control", "QQQ": "Control",
    "IWM": "Control",
}

loader = BacktestDataLoader()


def atr(bars: List[Dict]) -> float:
    trs = []
    for i in range(1, len(bars)):
        h, low, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    return statistics.mean(trs) if trs else 0.0


def rs(closes: List[float], spy_closes: List[float], idx: int, window: int) -> Optional[float]:
    if idx < window or len(spy_closes) <= idx:
        return None
    stock_ret = closes[idx] / closes[idx - window] - 1
    spy_ret = spy_closes[idx] / spy_closes[idx - window] - 1
    return stock_ret - spy_ret


def load_fundamentals(path: Optional[Path]) -> Dict[str, Dict[str, int]]:
    if not path:
        return {}
    data = json.loads(path.read_text())
    return {
        str(ticker).upper(): {str(k): int(v) for k, v in values.items()}
        for ticker, values in data.items()
        if isinstance(values, dict)
    }


def parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def statement_availability_date(row: Dict[str, Any]) -> Optional[date]:
    """
    Best available point-in-time approximation for when a quarterly statement
    became usable. FMP rows often include filingDate/acceptedDate. If they do
    not, use fiscal period date + 60 calendar days as a conservative lag.
    """
    for field in ("acceptedDate", "filingDate", "fillingDate", "reportedDate"):
        parsed = parse_date(row.get(field))
        if parsed:
            return parsed
    fiscal_date = parse_date(row.get("date"))
    return fiscal_date + timedelta(days=PIT_DEFAULT_LAG_DAYS) if fiscal_date else None


def normalize_income_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    rev = float(out.get("revenue") or 0)
    gp = float(out.get("grossProfit") or 0)
    if "grossProfitRatio" not in out:
        out["grossProfitRatio"] = round(gp / rev, 4) if rev else 0.0
    return out


def fundamental_inflection_score(income: List[Dict[str, Any]], cashflow: List[Dict[str, Any]]) -> int:
    if len(income) < 4:
        return 0

    rev = [float(q.get("revenue") or 0) for q in income[:4]]
    op_income = [float(q.get("operatingIncome") or 0) for q in income[:4]]
    gp_ratio = [float(q.get("grossProfitRatio") or 0) for q in income[:4]]
    ocf = [float(q.get("operatingCashFlow") or 0) for q in cashflow[:4]] if len(cashflow) >= 4 else []

    if min(rev) <= 0:
        return 0

    latest_growth = rev[0] / rev[1] - 1
    prior_growth = rev[1] / rev[2] - 1
    two_q_growth = rev[0] / rev[2] - 1
    margin_now = op_income[0] / rev[0]
    margin_prev = op_income[1] / rev[1]
    gross_margin_delta = gp_ratio[0] - gp_ratio[1]

    score = 0
    if latest_growth > 0.08:
        score += 25
    elif latest_growth > 0.03:
        score += 15
    if latest_growth > prior_growth + 0.03:
        score += 20
    if two_q_growth > 0.12:
        score += 15
    if margin_now > margin_prev:
        score += 15
    if gross_margin_delta > 0:
        score += 10
    if ocf and ocf[0] > 0:
        score += 10
    if op_income[0] > op_income[1]:
        score += 10

    return max(0, min(100, score))


def raw_fundamental_cache_path(ticker: str) -> Path:
    return PIT_CACHE_DIR / f"{ticker.upper()}.json"


def fetch_or_load_statement_history(ticker: str, refresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetch quarterly statement history once per ticker and cache it locally.
    Backtest evaluation reads the derived score map only, so FMP is not called
    inside tight historical loops.
    """
    cache_path = raw_fundamental_cache_path(ticker)
    if cache_path.exists() and not refresh:
        try:
            data = json.loads(cache_path.read_text())
            return {
                "income": data.get("income") if isinstance(data.get("income"), list) else [],
                "cashflow": data.get("cashflow") if isinstance(data.get("cashflow"), list) else [],
            }
        except Exception:
            pass

    from core.fmp_client import get_fmp

    sym = ticker.upper()
    fmp = get_fmp()
    income = fmp._get("/income-statement", params={"symbol": sym, "period": "quarter", "limit": 80})
    cashflow = fmp._get("/cash-flow-statement", params={"symbol": sym, "period": "quarter", "limit": 80})
    payload = {
        "ticker": sym,
        "source": "FMP stable quarterly statements",
        "pit_method": "filingDate/acceptedDate when available; otherwise fiscal date + 60 calendar days",
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "income": income if isinstance(income, list) else [],
        "cashflow": cashflow if isinstance(cashflow, list) else [],
    }
    PIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return {"income": payload["income"], "cashflow": payload["cashflow"]}


def score_map_from_statement_history(history: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
    income_rows = [normalize_income_row(row) for row in history.get("income", []) if isinstance(row, dict)]
    cashflow_rows = [row for row in history.get("cashflow", []) if isinstance(row, dict)]

    income_available = []
    for row in income_rows:
        avail = statement_availability_date(row)
        fiscal = parse_date(row.get("date"))
        if avail and fiscal:
            income_available.append((avail, fiscal, row))

    cashflow_available = []
    for row in cashflow_rows:
        avail = statement_availability_date(row)
        fiscal = parse_date(row.get("date"))
        if avail and fiscal:
            cashflow_available.append((avail, fiscal, row))

    score_map: Dict[str, int] = {}
    for as_of in sorted({item[0] for item in income_available}):
        known_income = [row for avail, fiscal, row in income_available if avail <= as_of]
        known_cashflow = [row for avail, fiscal, row in cashflow_available if avail <= as_of]
        known_income.sort(key=lambda row: parse_date(row.get("date")) or date.min, reverse=True)
        known_cashflow.sort(key=lambda row: parse_date(row.get("date")) or date.min, reverse=True)
        if len(known_income) >= 4:
            score_map[as_of.isoformat()] = fundamental_inflection_score(known_income, known_cashflow)
    return score_map


def build_pit_fundamentals(universe: List[str], refresh: bool = False) -> Dict[str, Dict[str, int]]:
    PIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    result: Dict[str, Dict[str, int]] = {}
    for ticker in universe:
        if ticker in CONTROLS:
            continue
        history = fetch_or_load_statement_history(ticker, refresh=refresh)
        scores = score_map_from_statement_history(history)
        if scores:
            result[ticker.upper()] = scores
    PIT_SCORE_PATH.write_text(json.dumps({
        "tag": PIT_PROXY_TAG,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "method": "Quarterly FMP statement history keyed by filingDate/acceptedDate; fiscal date + 60d fallback.",
        "limitation": "Proxy is not a full SEC point-in-time database; survivorship/current-universe bias remains.",
        "scores": result,
    }, indent=2, sort_keys=True))
    return result


def load_or_build_pit_fundamentals(universe: List[str], refresh: bool = False) -> Dict[str, Dict[str, int]]:
    if PIT_SCORE_PATH.exists() and not refresh:
        data = json.loads(PIT_SCORE_PATH.read_text())
        scores = data.get("scores") if isinstance(data, dict) else data
        if isinstance(scores, dict):
            return {
                str(ticker).upper(): {str(k): int(v) for k, v in values.items()}
                for ticker, values in scores.items()
                if isinstance(values, dict)
            }
    return build_pit_fundamentals(universe, refresh=refresh)


def fund_score_for(fundamentals: Dict[str, Dict[str, int]], ticker: str, d: date) -> Optional[int]:
    rows = fundamentals.get(ticker.upper())
    if not rows:
        return None
    candidates = [key for key in rows if key <= d.isoformat()]
    if not candidates:
        return None
    return rows[max(candidates)]


def score_signal(fund_score: Optional[int], rs20: float, rs60: float, dvol_ratio: float, extension_ma50: float) -> int:
    score = 25
    if fund_score is not None:
        score += min(25, fund_score * 0.25)
    else:
        # Price/tape-only baseline gets no hidden fundamental credit.
        score += 0
    score += min(20, max(0, rs20) * 250)
    score += min(10, max(0, rs60) * 100)
    score += min(15, max(0, dvol_ratio - 1.0) * 75)
    if extension_ma50 <= 0.08:
        score += 10
    return max(0, min(100, int(score)))


def evaluate_bar(
    bars: List[Dict],
    spy_bars: List[Dict],
    idx: int,
    ticker: str,
    fundamentals: Dict[str, Dict[str, int]],
) -> Tuple[Optional[Dict], str]:
    if idx < BARS_NEEDED or len(spy_bars) <= idx:
        return None, "stale_bars"

    hist = bars[: idx + 1]
    closes = [float(b["close"]) for b in bars[: idx + 1]]
    volumes = [int(b["volume"]) for b in bars[: idx + 1]]
    spy_closes = [float(b["close"]) for b in spy_bars[: idx + 1]]
    today = hist[-1]
    today_close = closes[-1]

    if today_close < MIN_PRICE:
        return None, "price_too_low"

    avg_dvol_20 = statistics.mean(closes[i] * volumes[i] for i in range(-20, 0))
    if avg_dvol_20 < MIN_AVG_DOLLAR_VOL_20:
        return None, "low_dollar_vol"
    if avg_dvol_20 > MAX_AVG_DOLLAR_VOL_20:
        return None, "too_crowded_dvol"

    fscore = fund_score_for(fundamentals, ticker, today["date"])
    if fundamentals and (fscore is None or fscore < MIN_FUND_SCORE):
        return None, "fundamental_score_too_low"

    ma50 = statistics.mean(closes[-50:])
    ma150 = statistics.mean(closes[-150:])
    if today_close < ma50:
        return None, "below_ma50"
    if ma50 < ma150 * 0.97:
        return None, "ma_structure_declining"

    low_60 = min(closes[-60:])
    high_60_prior = max(closes[-61:-1])
    if (today_close - low_60) / low_60 > MAX_EXTENSION_20D_LOW:
        return None, "too_far_from_base_low"
    if (today_close - ma50) / ma50 > MAX_EXTENSION_MA50:
        return None, "too_extended_ma50"
    if (high_60_prior - today_close) / high_60_prior > MAX_PRIOR_HIGH_DISTANCE:
        return None, "not_near_base_high"
    if min(closes[-20:]) <= min(closes[-60:-20]):
        return None, "no_higher_low"

    dvol_20 = statistics.mean(closes[i] * volumes[i] for i in range(-20, 0))
    dvol_60_prev = statistics.mean(closes[i] * volumes[i] for i in range(-80, -20))
    dvol_ratio = dvol_20 / dvol_60_prev if dvol_60_prev > 0 else 0.0
    if dvol_ratio < MIN_DVOL_RATIO:
        return None, "dvol_not_improving"

    avg_vol_20 = statistics.mean(volumes[-20:])
    vol_ratio = volumes[-1] / avg_vol_20 if avg_vol_20 > 0 else 0.0
    if vol_ratio > MAX_VOLUME_SPIKE:
        return None, "volume_spike_chase"

    rs20 = rs(closes, spy_closes, len(closes) - 1, 20)
    rs60 = rs(closes, spy_closes, len(closes) - 1, 60)
    if rs20 is None or rs20 < RS_20_MIN:
        return None, "rs_20_weak"
    if rs60 is None or rs60 < RS_60_MIN:
        return None, "rs_60_weak"

    trigger = today_close > max(closes[-11:-1]) and closes[-2] <= max(closes[-12:-2])
    if not trigger:
        return None, "no_trigger"

    a = atr(hist[-14:])
    if a <= 0:
        return None, "atr_unavailable"
    stop = max(today_close - STOP_ATR_MULT * a, ma50 * 0.96)
    if stop >= today_close:
        return None, "poor_geometry"
    target = today_close + TARGET_ATR_MULT * a
    rr = (target - today_close) / (today_close - stop)
    if rr < MIN_RR:
        return None, "poor_geometry"

    sc = score_signal(fscore, rs20, rs60, dvol_ratio, (today_close - ma50) / ma50)
    if sc < MIN_SCORE:
        return None, "score_too_low"

    return {
        "ticker": ticker,
        "date": today["date"],
        "year": today["date"].year,
        "entry": today_close,
        "stop": stop,
        "target": target,
        "rr": rr,
        "score": sc,
        "fund_score": fscore,
        "rs20": rs20,
        "rs60": rs60,
        "dvol_ratio": dvol_ratio,
        "vol_ratio": vol_ratio,
        "extension_ma50": (today_close - ma50) / ma50,
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

    for b in future:
        if b["low"] <= stop:
            exit_price = stop
            exit_type = "stop"
            stop_hit = True
            break
        if b["high"] >= target:
            exit_price = target
            exit_type = "target"
            target_hit = True
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
    }


def run(universe: List[str], start: date, end: date, fundamentals: Dict[str, Dict[str, int]]) -> Tuple[List[Dict], Counter]:
    data_start = date(start.year - 1, start.month, start.day)
    data = {t: loader.get_bars(t, data_start, end) for t in universe}
    spy = data.get("SPY") or loader.get_bars("SPY", data_start, end)
    signals: List[Dict] = []
    rejects: Counter = Counter()

    for ticker, bars in data.items():
        if ticker in CONTROLS:
            continue
        if not bars:
            rejects["no_data"] += 1
            continue
        for idx, bar in enumerate(bars):
            d = bar["date"]
            if d < start or d > end:
                continue
            sig, reason = evaluate_bar(bars, spy, idx, ticker, fundamentals)
            if sig:
                sig["outcomes"] = {h: simulate_outcome(bars, idx, sig, h) for h in HORIZONS}
                signals.append(sig)
            else:
                rejects[reason] += 1
    signals.sort(key=lambda s: (s["date"], s["ticker"]))
    return signals, rejects


def pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def print_horizons(signals: List[Dict]) -> None:
    for h in HORIZONS:
        rows = [s["outcomes"][h] for s in signals]
        if not rows:
            continue
        wr = sum(r["win"] for r in rows) / len(rows)
        avg_raw = statistics.mean(r["raw"] for r in rows)
        avg_adj = statistics.mean(r["adj"] for r in rows)
        med_adj = statistics.median(r["adj"] for r in rows)
        stop_rate = sum(r["stop_hit"] for r in rows) / len(rows)
        target_rate = sum(r["target_hit"] for r in rows) / len(rows)
        print(
            f"  {h:>2}d: n={len(rows):>3} WR={wr*100:5.1f}% "
            f"avgRaw={pct(avg_raw):>8} avgAdj={pct(avg_adj):>8} "
            f"medAdj={pct(med_adj):>8} stop={stop_rate*100:5.1f}% target={target_rate*100:5.1f}%"
        )


def summarize(signals: List[Dict], rejects: Counter, fundamentals_loaded: bool) -> None:
    print("\nPATHFINDER_V1 BASELINE BACKTEST")
    print("=" * 72)
    print("Doctrine checklist:")
    print("  Doctrine / mandate clarity: PASS")
    print("  Scanner / council fit: PARTIAL (scanner-only baseline; no PATHFINDER council profile yet)")
    print("  Data integrity: PARTIAL" + (" (local point-in-time fundamentals supplied)" if fundamentals_loaded else " (OHLCV only; fundamentals not simulated to avoid lookahead)"))
    print("  Execution realism: PASS baseline friction/ATR stop-target defined")
    print("  Output expectations: PASS\n")
    print(f"Signals: {len(signals)}")
    print(f"Friction: {FRICTION_RT * 100:.2f}% round trip")
    print(f"Fundamentals: {'local point-in-time JSON applied' if fundamentals_loaded else 'not applied; use this as price/tape baseline only'}")

    print("\nRejection funnel (top 14):")
    for reason, count in rejects.most_common(14):
        print(f"  {reason:<28} {count:>8}")

    print("\nHorizon results:")
    print_horizons(signals)

    print(f"\nYear-by-year ({PRIMARY_HORIZON}d adjusted):")
    by_year: Dict[int, List[Dict]] = defaultdict(list)
    for sig in signals:
        by_year[sig["year"]].append(sig["outcomes"][PRIMARY_HORIZON])
    for y in sorted(by_year):
        rows = by_year[y]
        wr = sum(r["win"] for r in rows) / len(rows)
        avg_adj = statistics.mean(r["adj"] for r in rows)
        stop_rate = sum(r["stop_hit"] for r in rows) / len(rows)
        print(f"  {y}: n={len(rows):>3} WR={wr*100:5.1f}% avgAdj={pct(avg_adj):>8} stop={stop_rate*100:5.1f}%")

    print("\nSector concentration:")
    for sector, count in Counter(s["sector"] for s in signals).most_common():
        print(f"  {sector:<14} {count:>3} ({count / len(signals) * 100 if signals else 0:4.1f}%)")

    print("\nTicker concentration (top 10):")
    for ticker, count in Counter(s["ticker"] for s in signals).most_common(10):
        print(f"  {ticker:<6} {count:>3} ({count / len(signals) * 100 if signals else 0:4.1f}%)")

    print("\nRepresentative signals:")
    for sig in signals[:8]:
        out = sig["outcomes"][PRIMARY_HORIZON]
        print(
            f"  {sig['date']} {sig['ticker']:<5} score={sig['score']:>2} "
            f"rs20={sig['rs20']*100:+.1f}% dvol={sig['dvol_ratio']:.2f} "
            f"ext50={sig['extension_ma50']*100:+.1f}% {PRIMARY_HORIZON}dAdj={pct(out['adj'])} exit={out['exit_type']}"
        )

    primary = [s["outcomes"][PRIMARY_HORIZON] for s in signals]
    avg_adj = statistics.mean(r["adj"] for r in primary) if primary else 0.0
    stop_rate = sum(r["stop_hit"] for r in primary) / len(primary) if primary else 1.0
    if len(signals) >= 50 and avg_adj > 0.01 and stop_rate < 0.45:
        verdict = "PAPER-WORTHY CANDIDATE, after point-in-time fundamentals validation"
    elif len(signals) >= 30 and avg_adj > 0:
        verdict = "ONE NARROW RESEARCH PASS JUSTIFIED"
    else:
        verdict = "RESEARCH-ONLY"
    print(f"\nVerdict: {verdict}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PATHFINDER_V1 baseline backtest")
    parser.add_argument("--quick", action="store_true", help="Small universe/window smoke run")
    parser.add_argument("--fundamentals-json", type=Path, default=None, help="Optional point-in-time local fundamentals score JSON")
    parser.add_argument("--pit-fundamentals", action="store_true", help="Build/load PATHFINDER PIT fundamentals proxy and apply it")
    parser.add_argument("--refresh-pit-fundamentals", action="store_true", help="Refresh raw FMP quarterly statement cache before scoring")
    args = parser.parse_args()

    universe = QUICK_UNIVERSE if args.quick else PATHFINDER_UNIVERSE
    start = QUICK_START if args.quick else FULL_START
    end = QUICK_END if args.quick else FULL_END
    if args.pit_fundamentals:
        fundamentals = load_or_build_pit_fundamentals(universe, refresh=args.refresh_pit_fundamentals)
    else:
        fundamentals = load_fundamentals(args.fundamentals_json)

    print(f"Universe={len(universe)} Window={start} -> {end} Baseline={BASELINE_TAG}")
    if args.pit_fundamentals:
        print(f"PIT fundamentals proxy={PIT_SCORE_PATH}")
    signals, rejects = run(universe, start, end, fundamentals)
    summarize(signals, rejects, bool(fundamentals))


if __name__ == "__main__":
    main()
