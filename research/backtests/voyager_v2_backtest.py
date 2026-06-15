"""
research/backtests/voyager_v2_backtest.py — Voyager v2 historical validation

Runs two modes on the same universe and date range:
  Mode A: Voyager base scanner (price + technical + fundamental gates, no 13F)
  Mode B: Voyager base scanner + 13F overlay (margin-based scoring)

Anti-lookahead enforcement:
  • Price / MA / RS / dvol: bars sliced to [oldest, scan_date) — no future bars
  • 13F: uses filing.filing_date (actual SEC submission date), NOT period_of_report.
    Only filings where filing_date <= scan_date are used. This matters: Q4 Dec 31
    data is typically filed in early February, not available on January scans.
  • Fundamentals: FMP latest-4-quarters data. Mild lookahead risk for scans in
    2022–2023 (later reported quarters are in the dataset). Fundamental scores are
    stable Q/Q for large-cap names; this introduces negligible signal bias. A
    rigorous v3 would filter by FMP `fillingDate <= scan_date`.

Known limitations:
  • Survivorship: universe excludes stocks delisted during window — flatters returns.
  • 13F historical prefetch: ~360 unique filings × ~2s each ≈ 12 min offline.
    Run with --skip-13f to get Mode A results quickly. Mode B requires --include-13f.
  • Forward returns at 180d/365d are unavailable for signals near the end of the
    data window (i.e., late 2024 signals can only measure 30d/90d).
  • Size buckets use approximate 2022-2024 market cap ranges — not historical point-
    in-time. Tickers that grew significantly (SMCI, CELH) are classified by their
    early-window size, which is the more meaningful bias test.

Usage:
  cd /home/gem/trading-production
  source .venv/bin/activate
  set -a && source /home/gem/secure/trading.env && set +a
  python research/backtests/voyager_v2_backtest.py [--skip-13f] [--quick] [--bias]

  --skip-13f   : Mode A only (fast, ~5 min)
  --include-13f: Mode A + B  (slow, ~20 min — prefetches all historical 13F filings)
  --quick      : Small universe (20 tickers) + 4 scan dates for rapid smoke test
  --bias       : Expanded universe (78 tickers) with mid/emerging additions + size-bucket report
"""
from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# ── Load credentials via SNIPER_ENV_PATH (same pattern as dashboard) ─────────
_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    from pathlib import Path as _Path
    try:
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_Path(_env_path), override=True)
    except ImportError:
        pass

# ── Imports ───────────────────────────────────────────────────────────────────
try:
    import pandas as pd
    from backtest_data_loader import BacktestDataLoader
except ImportError as e:
    print(f"Missing dependency: {e}. Activate the trading venv.", file=sys.stderr)
    sys.exit(1)

try:
    from edgar import Company, set_identity
    _EDGAR_AVAILABLE = True
except ImportError:
    _EDGAR_AVAILABLE = False

# Production imports — fundamentals only (not the scanner itself)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
try:
    from core.fmp_client import get_fmp
    _FMP_AVAILABLE = True
except Exception:
    _FMP_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Universe ──────────────────────────────────────────────────────────────────
# ~60 liquid US equities. Mix of: likely-passing (leadership names), likely-
# failing in certain periods (drawdown names), sector diversity.
FULL_UNIVERSE = [
    # Tech megacap
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
    # Semiconductors
    "AMD", "AVGO", "QCOM", "AMAT", "MU",
    # High-growth tech (some will fail gates during 2022 bear)
    "CRWD", "NET", "DDOG", "ZS", "PLTR", "SNOW",
    # Software / SaaS
    "CRM", "ADBE", "ORCL", "NOW",
    # Beaten-down names (test rejection)
    "ROKU", "PYPL", "SNAP", "UBER",
    # Healthcare
    "LLY", "UNH", "ABBV", "TMO", "ISRG",
    # Financials
    "V", "MA", "JPM",
    # Industrials / Defense
    "CAT", "GE", "LMT", "RTX",
    # Energy
    "XOM", "CVX",
    # Consumer staples / Retail
    "COST", "WMT", "HD",
    # Biotech (likely fails fundamental)
    "MRNA", "RXRX",
    # Mid-cap growth
    "AXON", "TTWO", "DUOL",
    # Commodity / Materials
    "FCX", "NEM",
]

QUICK_UNIVERSE = [
    "NVDA", "MSFT", "AAPL", "CRWD", "LLY",
    "AMZN", "META", "AVGO", "UNH", "V",
    "ROKU", "PYPL", "SNAP", "RXRX", "MU",
    "COST", "XOM", "CAT", "GE", "AXON",
]

# Expanded universe for size-bias validation (--bias flag).
# Adds ~29 mid-cap and emerging-growth names to the base 49.
EXPANDED_ADDITIONS = [
    # Cybersecurity mid-cap (not in FULL_UNIVERSE)
    "PANW", "FTNT", "S",
    # High-growth SaaS mid-cap
    "MNDY", "TOST", "DOCS", "PAYC", "TTD",
    # Consumer growth mid-cap
    "ONON", "DECK", "BOOT", "WING",
    # Medtech mid-cap
    "PODD", "INSP",
    # Housing / consumer staples mid-cap
    "BLD", "SFM",
    # Entertainment / gaming mid-cap
    "DKNG",
    # Emerging growth (typically <$5B in 2022, fast-growing)
    "CELH", "ELF", "APP", "TMDX", "SMCI", "HIMS", "GLBE", "IRTC", "CAVA",
    # Global fintech
    "MELI", "NU",
]
EXPANDED_UNIVERSE = FULL_UNIVERSE + EXPANDED_ADDITIONS

# ── Size-bucket classification (approximate 2022–2024 market cap) ─────────────
# large   = >$50B  |  mid = $5B–$50B  |  emerging = $500M–$5B
# Stocks that grew dramatically are classified by their EARLY-window size (the
# more informative test: can Voyager find them before they are large?).
SIZE_BUCKET: Dict[str, str] = {
    # LARGE-CAP (>$50B consistently through 2022-2024)
    "AAPL": "large", "MSFT": "large", "NVDA": "large", "GOOGL": "large",
    "AMZN": "large", "META": "large", "AVGO": "large", "QCOM": "large",
    "AMD":  "large", "CRM":  "large", "ADBE": "large", "ORCL": "large",
    "NOW":  "large", "V":    "large", "MA":   "large", "JPM":  "large",
    "UNH":  "large", "LLY":  "large", "ABBV": "large", "TMO":  "large",
    "ISRG": "large", "COST": "large", "WMT":  "large", "HD":   "large",
    "CAT":  "large", "GE":   "large", "LMT":  "large", "RTX":  "large",
    "XOM":  "large", "CVX":  "large",
    # MID-CAP ($5B–$50B during test window)
    "AMAT": "mid",  "MU":   "mid",  "CRWD": "mid",  "NET":  "mid",
    "DDOG": "mid",  "ZS":   "mid",  "PLTR": "mid",  "SNOW": "mid",
    "UBER": "mid",  "PYPL": "mid",  "SNAP": "mid",  "ROKU": "mid",
    "MRNA": "mid",  "TTWO": "mid",  "FCX":  "mid",  "NEM":  "mid",
    "AXON": "mid",  "PANW": "mid",  "FTNT": "mid",  "ONON": "mid",
    "DECK": "mid",  "PAYC": "mid",  "MNDY": "mid",  "PODD": "mid",
    "INSP": "mid",  "BLD":  "mid",  "DOCS": "mid",  "TOST": "mid",
    "BOOT": "mid",  "SFM":  "mid",  "WING": "mid",  "DKNG": "mid",
    "TTD":  "mid",  "MELI": "mid",  "NU":   "mid",  "S":    "mid",
    # EMERGING-GROWTH (<$5B in 2022, often pre-profitability or early-stage)
    "DUOL": "emerging", "RXRX": "emerging", "CELH": "emerging",
    "ELF":  "emerging", "APP":  "emerging", "TMDX": "emerging",
    "SMCI": "emerging", "HIMS": "emerging", "GLBE": "emerging",
    "IRTC": "emerging", "CAVA": "emerging",
}

# Quarterly scan dates: first trading day of each quarter 2022–2024
# (Approximate; weekends shift to Monday in practice)
FULL_SCAN_DATES: List[date] = [
    date(2022, 1, 3),  date(2022, 4, 1),  date(2022, 7, 1),  date(2022, 10, 3),
    date(2023, 1, 3),  date(2023, 4, 3),  date(2023, 7, 3),  date(2023, 10, 2),
    date(2024, 1, 2),  date(2024, 4, 1),  date(2024, 7, 1),  date(2024, 10, 1),
]

QUICK_SCAN_DATES: List[date] = [
    date(2022, 7, 1),
    date(2023, 1, 3),
    date(2023, 7, 3),
    date(2024, 1, 2),
]

# Forward return horizons (trading days)
FWD_WINDOWS = {"30d": 30, "90d": 90, "180d": 130, "365d": 252}

# ── Voyager constants (mirror strategies/voyager.py exactly) ──────────────────
MIN_PRICE              = 5.0
MIN_AVG_DOLLAR_VOL     = 5_000_000
MAX_EXTENSION_MA50     = 0.12
MA200_FLOOR            = 0.92
RS_50_WINDOW           = 50
RS_130_WINDOW          = 130
DVOL_TREND_RATIO       = 0.85
MIN_FUNDAMENTAL_SCORE        = 40
MIN_FUNDAMENTAL_SCORE_EARLY  = 55
MIN_SCORE              = 65
MIN_RRR                = 2.5
BARS_NEEDED            = 260
BASE_MAX_PRICE_TIGHT   = 0.03
BASE_MAX_DIST_MA50     = 0.05
PULLBACK_MIN_DIST      = 0.02
PULLBACK_MAX_DIST      = 0.10
EARLY_ACCUM_MA50_GAP   = 0.03
EARLY_ACCUM_MIN_DVOL   = 1.15


# ════════════════════════════════════════════════════════════════════════════════
# Historical price data
# ════════════════════════════════════════════════════════════════════════════════

_backtest_loader = BacktestDataLoader()


def fetch_all_price_data(
    tickers: List[str],
    start: date,
    end: date,
) -> Dict[str, pd.DataFrame]:
    """
    Fetch full OHLCV history for all tickers.
    Returns dict of ticker → DataFrame(date_idx, open/high/low/close/volume).
    Adds SPY automatically if not in the list.
    Reads from cache/backtest_prices/ first; falls back to Alpaca on miss.
    """
    all_tickers = list(set(tickers + ["SPY"]))
    return _backtest_loader.get_bars_batch(all_tickers, start, end)


def get_bars_as_of(
    price_data: Dict[str, pd.DataFrame],
    ticker: str,
    as_of: date,
    n_bars: int,
) -> List[Dict]:
    """
    Returns the n_bars most recent daily bars for ticker strictly before as_of.
    Enforces anti-lookahead: no bar with date >= as_of is included.
    """
    df = price_data.get(ticker)
    if df is None or df.empty:
        return []
    as_of_ts = pd.Timestamp(as_of)
    subset = df[df.index < as_of_ts].tail(n_bars)
    return subset.reset_index().rename(columns={"date": "date"}).to_dict("records")


def get_forward_return(
    price_data: Dict[str, pd.DataFrame],
    ticker: str,
    signal_date: date,
    n_trading_days: int,
) -> Optional[float]:
    """
    Forward return from signal_date over n_trading_days.
    Returns % return or None if insufficient future data.
    Anti-lookahead: uses signal_date close as entry, measures n days later.
    """
    df = price_data.get(ticker)
    if df is None or df.empty:
        return None
    signal_ts = pd.Timestamp(signal_date)
    future = df[df.index >= signal_ts]
    if len(future) < n_trading_days + 1:
        return None
    entry_price = float(future.iloc[0]["close"])
    exit_price  = float(future.iloc[n_trading_days]["close"])
    if entry_price <= 0:
        return None
    return round((exit_price - entry_price) / entry_price * 100, 2)


# ════════════════════════════════════════════════════════════════════════════════
# Historical 13F with filing_date anti-lookahead
# ════════════════════════════════════════════════════════════════════════════════

TRACKED_INSTITUTIONS = {
    "Vanguard":              "0000102909",
    "State Street":          "0000093751",
    "Fidelity":              "0000315066",
    "Berkshire Hathaway":    "0001067983",
    "ARK Investment":        "0001697748",
    "Renaissance Tech":      "0001037389",
    "Citadel":               "0001423053",
    "DE Shaw":               "0001009207",
    "Bridgewater":           "0001350694",
    "Tiger Global":          "0001167483",
    "Point72":               "0001603466",
    "Viking Global":         "0001103804",
    "Third Point":           "0001040273",
    "Lone Pine":             "0001061165",
    "Soros Fund Management": "0001029160",
}


class HistoricalWhaleTracker:
    """
    Anti-lookahead 13F tracker for historical backtesting.

    Key principle: uses filing.filing_date (actual SEC submission date) as the
    availability date, NOT filing.period_of_report (the quarter-end date).

    Example: Q4 2024 (period=2024-12-31) was filed 2025-02-11.
    A scan on 2025-01-15 would NOT have this data available — period-only
    logic would incorrectly assume it was available from Jan 1.

    Prefetch:
      Call prefetch_all() once before the backtest loop. It fetches filing
      metadata (period + filing_date) for each institution and caches in memory.
      Holdings are parsed lazily on first access and cached by accession_no.
    """

    def __init__(self):
        if not _EDGAR_AVAILABLE:
            raise ImportError("edgartools required: pip install edgartools")
        set_identity("hedayat.raastin@gmail.com")
        # institution_name -> sorted list of (filing_date_str, period_str, accession_no, filing_obj)
        self._institution_filings: Dict[str, List[Tuple]] = {}
        # accession_no -> List[Dict]  (parsed holdings, cached)
        self._holdings_cache: Dict[str, List[Dict]] = {}
        self._prefetched = False

    def prefetch_all(self, n_quarters: int = 20) -> None:
        """
        Pre-fetch filing metadata for all institutions.
        n_quarters=20 covers ~5 years. Only metadata is fetched here; holdings
        are parsed lazily on demand and cached per accession.
        """
        logger.info("Prefetching 13F filing metadata for %d institutions...", len(TRACKED_INSTITUTIONS))
        for name, cik in TRACKED_INSTITUTIONS.items():
            try:
                company = Company(cik)
                filings = company.get_filings(form="13F-HR").latest(n_quarters)
                if filings is None:
                    continue
                filing_list = []
                for i in range(min(n_quarters, len(filings))):
                    try:
                        f = filings.get_filing_at(i)
                        filed_str  = str(f.filing_date or "")[:10]
                        period_str = str(f.period_of_report or "")[:10]
                        accession  = f.accession_no
                        if filed_str and period_str:
                            filing_list.append((filed_str, period_str, accession, f))
                    except Exception:
                        continue
                # Sort newest-first by filing_date
                filing_list.sort(key=lambda x: x[0], reverse=True)
                self._institution_filings[name] = filing_list
                logger.info("  %s: %d filings (%s → %s)",
                            name, len(filing_list),
                            filing_list[-1][0] if filing_list else "?",
                            filing_list[0][0]  if filing_list else "?")
            except Exception as exc:
                logger.warning("  %s: prefetch failed — %s", name, exc)
        self._prefetched = True

    def get_activity_as_of(self, ticker: str, as_of: date) -> Optional[Dict]:
        """
        Returns 13F institutional activity using only filings available on as_of.

        Anti-lookahead: for each institution, the most recent TWO filings where
        filing_date <= as_of are used for the Q-over-Q comparison. If fewer than
        two such filings exist, the institution is skipped.
        """
        if not self._prefetched:
            raise RuntimeError("Call prefetch_all() before get_activity_as_of()")
        as_of_str = as_of.isoformat()

        buyers, sellers, holders = [], [], []
        institutions_checked = 0
        last_quarter: Optional[str] = None

        for name, filing_list in self._institution_filings.items():
            # Filter to filings available on as_of (using filing_date, not period)
            available = [(fd, period, acc, f) for fd, period, acc, f in filing_list
                         if fd <= as_of_str]
            if len(available) < 2:
                continue  # not enough quarterly comparisons available yet

            curr_fd, curr_period, curr_acc, curr_f   = available[0]
            prev_fd, prev_period, prev_acc, prev_f   = available[1]

            curr_holdings = self._get_holdings(curr_acc, curr_f)
            prev_holdings = self._get_holdings(prev_acc, prev_f)

            curr_pos = self._find_ticker(curr_holdings, ticker)
            prev_pos = self._find_ticker(prev_holdings, ticker)

            if curr_pos is None and prev_pos is None:
                continue  # institution doesn't hold this name

            institutions_checked += 1
            if last_quarter is None or curr_period > last_quarter:
                last_quarter = curr_period

            if curr_pos and prev_pos:
                shares_change = curr_pos["shares"] - prev_pos["shares"]
                change_pct = (shares_change / prev_pos["shares"] * 100
                              if prev_pos["shares"] > 0 else 0.0)
                if abs(shares_change) < 1000:
                    holders.append(name)
                elif shares_change > 0:
                    buyers.append({"name": name, "shares_added": shares_change,
                                   "change_pct": round(change_pct, 1)})
                else:
                    sellers.append({"name": name, "shares_removed": abs(shares_change),
                                    "change_pct": round(abs(change_pct), 1)})
            elif curr_pos and not prev_pos:
                buyers.append({"name": name, "shares_added": curr_pos["shares"],
                               "change_pct": 100.0})
            elif prev_pos and not curr_pos:
                sellers.append({"name": name, "shares_removed": prev_pos["shares"],
                                "change_pct": 100.0})

        if institutions_checked == 0:
            return {"ticker": ticker, "net_flow": "UNKNOWN", "confidence": "UNKNOWN",
                    "whales_buying": 0, "whales_selling": 0, "whales_holding": 0,
                    "total_tracked": 0, "top_buyers": [], "top_sellers": [],
                    "last_quarter": None, "institutions_checked": 0}

        n_buy, n_sell = len(buyers), len(sellers)
        if n_buy > n_sell and n_buy > 0:     net_flow = "BUYING"
        elif n_sell > n_buy and n_sell > 0:  net_flow = "SELLING"
        elif n_buy > 0 or n_sell > 0:        net_flow = "MIXED"
        else:                                 net_flow = "NEUTRAL"

        total_active = n_buy + n_sell + len(holders)
        if total_active >= 5:    confidence = "HIGH"
        elif total_active >= 3:  confidence = "MODERATE"
        elif total_active >= 1:  confidence = "LOW"
        else:                    confidence = "UNKNOWN"

        buyers.sort(key=lambda x: x["shares_added"], reverse=True)
        sellers.sort(key=lambda x: x["shares_removed"], reverse=True)
        return {
            "ticker":               ticker,
            "net_flow":             net_flow,
            "confidence":           confidence,
            "whales_buying":        n_buy,
            "whales_selling":       n_sell,
            "whales_holding":       len(holders),
            "total_tracked":        total_active,
            "top_buyers":           buyers[:3],
            "top_sellers":          sellers[:3],
            "last_quarter":         last_quarter,
            "institutions_checked": institutions_checked,
        }

    def _get_holdings(self, accession: str, filing: Any) -> List[Dict]:
        if accession not in self._holdings_cache:
            self._holdings_cache[accession] = self._parse_holdings(filing)
        return self._holdings_cache[accession]

    def _parse_holdings(self, filing: Any) -> List[Dict]:
        try:
            thirteenf   = filing.obj()
            holdings_df = thirteenf.holdings
            if not isinstance(holdings_df, pd.DataFrame) or holdings_df.empty:
                return []
            result = []
            for _, row in holdings_df.iterrows():
                try:
                    result.append({
                        "name":   str(row.get("Issuer", "") or "").upper(),
                        "ticker": str(row.get("Ticker", "") or "").upper(),
                        "shares": int(row.get("SharesPrnAmount", 0) or 0),
                        "value":  int(row.get("Value", 0) or 0),
                    })
                except Exception:
                    continue
            return result
        except Exception:
            return []

    def _find_ticker(self, holdings: List[Dict], ticker: str) -> Optional[Dict]:
        ticker_upper = ticker.upper()
        for h in holdings:
            if h.get("ticker") == ticker_upper:
                return h
        for h in holdings:
            if not h.get("ticker") and ticker_upper in h.get("name", ""):
                return h
        return None


# ════════════════════════════════════════════════════════════════════════════════
# Voyager evaluation logic (mirrored from strategies/voyager.py)
# ════════════════════════════════════════════════════════════════════════════════

def _rs(ticker_closes: List[float], spy_closes: List[float], window: int) -> Optional[float]:
    if len(ticker_closes) < window + 1 or len(spy_closes) < window + 1:
        return None
    t_ret = ticker_closes[-1] / ticker_closes[-(window + 1)] - 1
    s_ret = spy_closes[-1]    / spy_closes[-(window + 1)]    - 1
    return t_ret - s_ret


def _detect_archetype(
    closes: List[float], ma50: float, ma200: float, dvol_ratio: float
) -> Optional[str]:
    today        = closes[-1]
    dist_ma50    = (today - ma50) / ma50
    golden_cross = ma50 > ma200

    if golden_cross:
        if abs(dist_ma50) <= BASE_MAX_DIST_MA50:
            recent    = closes[-20:]
            tightness = statistics.stdev(recent) / statistics.mean(recent) if len(recent) > 1 else 1
            if tightness <= BASE_MAX_PRICE_TIGHT:
                return "BASE_ACCUMULATION"
        if PULLBACK_MIN_DIST <= abs(dist_ma50) <= PULLBACK_MAX_DIST and dist_ma50 < 0:
            if len(closes) >= 80:
                ma50_30d_ago = statistics.mean(closes[-80:-30])
                if ma50 > ma50_30d_ago:
                    return "TREND_PULLBACK"
    else:
        ma50_gap = (ma200 - ma50) / ma200
        if ma50_gap <= EARLY_ACCUM_MA50_GAP:
            if len(closes) >= 70:
                ma50_20d_ago = statistics.mean(closes[-70:-20])
                if ma50 > ma50_20d_ago and dvol_ratio >= EARLY_ACCUM_MIN_DVOL:
                    return "EARLY_ACCUMULATION"
    return None


def _calc_atr(bars: List[Dict], period: int = 14) -> float:
    if len(bars) < period + 1:
        return float(bars[-1]["close"]) * 0.02
    true_ranges = []
    for i in range(-period, 0):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        true_ranges.append(max(h - l, abs(h - pc), abs(l - pc)))
    return statistics.mean(true_ranges)


def _fundamental_score_from_data(data: Optional[Dict]) -> Tuple[int, str]:
    """Simplified fundamental score (mirrors voyager.py._fundamental_score)."""
    if not data:
        return 50, "no_data"
    income  = data.get("income",  [])
    balance = data.get("balance", [])
    cashflow = data.get("cashflow", [])
    if not income:
        return 50, "no_income_data"

    score  = 0
    notes: List[str] = []
    latest = income[0] if income else {}

    if len(income) >= 4:
        recent_rev = (income[0].get("revenue") or 0) + (income[1].get("revenue") or 0)
        prior_rev  = (income[2].get("revenue") or 0) + (income[3].get("revenue") or 0)
        if prior_rev > 0:
            rev_growth = (recent_rev - prior_rev) / prior_rev
            if rev_growth > 0.10:    score += 30; notes.append("rev+")
            elif rev_growth > 0.03:  score += 20; notes.append("rev~")
            elif rev_growth > -0.05: score += 10
            else:                    notes.append("rev-")
        else:
            score += 10
    else:
        score += 10

    net_inc = latest.get("netIncome") or 0
    op_inc  = latest.get("operatingIncome") or 0
    if net_inc > 0:    score += 25; notes.append("profitable")
    elif op_inc > 0:   score += 15; notes.append("op_profitable")
    else:              notes.append("unprofitable")

    if balance:
        b = balance[0]
        debt   = b.get("totalDebt") or 0
        equity = b.get("totalStockholdersEquity") or 0
        cash   = b.get("cashAndShortTermInvestments") or 0
        if equity > 0:
            de = debt / equity
            if de < 0.5:    score += 25
            elif de < 1.5:  score += 18
            elif de < 3.0:  score += 10; notes.append("leveraged")
            else:           notes.append("high_leverage")
        elif equity < 0:   notes.append("negative_equity")
        else:              score += 8
        if cash > 0:       score += 5
    else:
        score += 12

    gm = latest.get("grossProfitRatio") or 0
    if gm > 0.40:    score += 15
    elif gm > 0.20:  score += 9
    elif gm > 0.10:  score += 4
    else:            notes.append("low_margin")

    if cashflow:
        ocf = cashflow[0].get("operatingCashFlow") or 0
        if ocf > 0: score += 5; notes.append("ocf+")

    return min(100, score), (" ".join(notes) or "ok")


def _base_score(
    rs_50: float, rs_130: Optional[float], dvol_ratio: float,
    up_vol_ratio: float, fund_score: int, archetype: str, extension_ma50: float
) -> int:
    s = 30
    if rs_50 > 0.10:   s += 15
    elif rs_50 > 0.05: s += 10
    elif rs_50 > 0.02: s += 5
    if rs_130 is not None:
        if rs_130 > 0.10:   s += 8
        elif rs_130 > 0.05: s += 5
        elif rs_130 > 0:    s += 2
    if dvol_ratio > 1.20:     s += 10
    elif dvol_ratio > 1.05:   s += 7
    elif dvol_ratio >= 0.90:  s += 4
    if up_vol_ratio > 1.5:    s += 8
    elif up_vol_ratio > 1.2:  s += 5
    elif up_vol_ratio >= 1.0: s += 2
    s += int(fund_score / 100 * 14)
    if archetype == "BASE_ACCUMULATION":  s += 8
    elif archetype == "TREND_PULLBACK":   s += 5
    elif archetype == "EARLY_ACCUMULATION": s += 4
    abs_ext = abs(extension_ma50)
    if abs_ext < 0.02:   s += 5
    elif abs_ext < 0.05: s += 3
    return max(0, min(100, s))


def score_thirteen_f(activity: Optional[Dict]) -> int:
    """Margin-based 13F scoring (current production version from core/whale_tracker.py)."""
    if activity is None:
        return 0
    net_flow   = activity.get("net_flow",   "UNKNOWN")
    confidence = activity.get("confidence", "UNKNOWN")
    n_buy      = activity.get("whales_buying",  0)
    n_sell     = activity.get("whales_selling", 0)
    if net_flow == "BUYING":
        margin = n_buy - n_sell
        if margin >= 4 and confidence == "HIGH": return  8
        if margin >= 3:                          return  5
        if margin >= 2:                          return  3
        return  1
    if net_flow == "SELLING":
        margin = n_sell - n_buy
        if margin >= 3 and confidence == "HIGH": return -5
        if margin >= 2:                          return -3
        return -2
    if net_flow == "MIXED":
        return 1 if n_buy >= n_sell else 0
    return 0


def evaluate_as_of(
    ticker: str,
    bars: List[Dict],
    spy_closes: List[float],
    fund_data: Optional[Dict],
    thirteen_f_activity: Optional[Dict],
    include_13f: bool,
) -> Tuple[Optional[Dict], str]:
    """
    Runs all Voyager gates against bars sliced to scan_date.
    Returns (signal_dict, "") on pass or (None, rejection_reason) on fail.
    """
    if len(bars) < 60:
        return None, "stale_bars"

    closes  = [b["close"]  for b in bars]
    volumes = [b["volume"] for b in bars]
    opens   = [b["open"]   for b in bars]
    today   = closes[-1]

    if today < MIN_PRICE:
        return None, "price_too_low"

    avg_dvol_20 = statistics.mean(closes[i] * volumes[i] for i in range(-20, 0))
    if avg_dvol_20 < MIN_AVG_DOLLAR_VOL:
        return None, "low_dollar_vol"

    if len(closes) < 200:
        return None, "stale_bars"

    ma50  = statistics.mean(closes[-50:])
    ma200 = statistics.mean(closes[-200:])

    if today < ma200 * MA200_FLOOR:
        return None, "below_ma200_floor"

    extension_ma50 = (today - ma50) / ma50
    if extension_ma50 > MAX_EXTENSION_MA50:
        return None, "too_extended"

    rs_50 = _rs(closes, spy_closes, RS_50_WINDOW)
    if rs_50 is None or rs_50 <= 0:
        return None, "weak_rs_50d"

    if len(closes) < 60:
        return None, "stale_bars"
    dvol_bars   = [closes[i] * volumes[i] for i in range(-60, 0)]
    avg_dvol_60 = statistics.mean(dvol_bars[:-20] if len(dvol_bars) > 20 else dvol_bars)
    dvol_ratio  = avg_dvol_20 / avg_dvol_60 if avg_dvol_60 > 0 else 0.0
    if dvol_ratio < DVOL_TREND_RATIO:
        return None, "dvol_fading"

    up_vols, dn_vols = [], []
    for i in range(-20, 0):
        if closes[i] >= opens[i]: up_vols.append(volumes[i])
        else:                     dn_vols.append(volumes[i])
    avg_up_vol   = statistics.mean(up_vols) if up_vols else 0
    avg_dn_vol   = statistics.mean(dn_vols) if dn_vols else 1
    up_vol_ratio = avg_up_vol / avg_dn_vol if avg_dn_vol > 0 else 1.0

    # Archetype first — must precede selling_dominates (mirrors voyager.py fix)
    archetype = _detect_archetype(closes, ma50, ma200, dvol_ratio)
    if archetype is None:
        return None, "no_archetype"

    # Volume dominance — conditional on archetype
    if archetype == "TREND_PULLBACK":
        if up_vol_ratio < 0.8:
            return None, "selling_dominates"
    else:
        if up_vol_ratio < 1.0:
            return None, "selling_dominates"

    fund_score, fund_note = _fundamental_score_from_data(fund_data)
    min_fund = MIN_FUNDAMENTAL_SCORE_EARLY if archetype == "EARLY_ACCUMULATION" else MIN_FUNDAMENTAL_SCORE
    if fund_score < min_fund:
        return None, "low_fundamental_quality"

    atr        = _calc_atr(bars)
    stop_atr   = today - atr * 1.5
    stop_ma200 = ma200 * 0.97
    stop       = min(stop_atr, stop_ma200)
    if stop <= 0 or today <= stop:
        return None, "poor_geometry"
    target = today + (today - stop) * MIN_RRR
    rr     = round((target - today) / (today - stop), 2)
    if rr < MIN_RRR:
        return None, "poor_geometry"

    # 13F overlay (Mode B only)
    thirteen_f_pts = score_thirteen_f(thirteen_f_activity) if include_13f else 0
    rs_130 = _rs(closes, spy_closes, RS_130_WINDOW)
    bscore = _base_score(rs_50, rs_130, dvol_ratio, up_vol_ratio,
                         fund_score, archetype, extension_ma50)
    score  = max(0, min(100, bscore + thirteen_f_pts))

    if score < MIN_SCORE:
        return None, "low_score"

    signal = {
        "ticker":          ticker,
        "archetype":       archetype,
        "score":           score,
        "base_score":      bscore,
        "thirteen_f_pts":  thirteen_f_pts,
        "rs_50d":          round(rs_50 * 100, 2),
        "dvol_ratio":      round(dvol_ratio, 2),
        "up_vol_ratio":    round(up_vol_ratio, 2),
        "extension_ma50":  round(extension_ma50 * 100, 1),
        "fund_score":      fund_score,
        "thirteen_f_flow": (thirteen_f_activity or {}).get("net_flow", "N/A"),
        "thirteen_f_confidence": (thirteen_f_activity or {}).get("confidence", "N/A"),
        "thirteen_f_buying":  (thirteen_f_activity or {}).get("whales_buying",  0),
        "thirteen_f_selling": (thirteen_f_activity or {}).get("whales_selling", 0),
        "ma50":            round(ma50, 2),
        "ma200":           round(ma200, 2),
        "entry_price":     round(today, 2),
        "stop":            round(stop, 2),
        "target":          round(target, 2),
        "rr":              rr,
    }
    return signal, ""


# ════════════════════════════════════════════════════════════════════════════════
# Main backtest runner
# ════════════════════════════════════════════════════════════════════════════════

def run_backtest(
    universe: List[str],
    scan_dates: List[date],
    include_13f: bool,
) -> Dict:
    """
    Runs the full backtest.
    Returns dict with all raw results for reporting.
    """
    # ── Fetch price data (full window: 18 months before first scan + 18 months after last) ──
    data_start = scan_dates[0]  - timedelta(days=int(BARS_NEEDED * 1.5))
    data_end   = scan_dates[-1] + timedelta(days=400)  # enough for 365d forward
    price_data = fetch_all_price_data(universe, data_start, data_end)

    # ── Fetch fundamentals (one call per ticker, cached by FMP client) ──
    fund_cache: Dict[str, Optional[Dict]] = {}
    if _FMP_AVAILABLE:
        logger.info("Fetching fundamentals for %d tickers...", len(universe))
        fmp = get_fmp()
        for ticker in universe:
            try:
                fund_cache[ticker] = fmp.get_fundamentals(ticker)
            except Exception:
                fund_cache[ticker] = None
    else:
        logger.warning("FMP not available — using fund_score=50 (no_data) for all tickers")
        for ticker in universe:
            fund_cache[ticker] = None

    # ── 13F historical tracker ──
    whale_tracker: Optional[HistoricalWhaleTracker] = None
    if include_13f:
        if not _EDGAR_AVAILABLE:
            logger.error("edgartools not installed — cannot run Mode B")
            include_13f = False
        else:
            whale_tracker = HistoricalWhaleTracker()
            whale_tracker.prefetch_all(n_quarters=20)

    # ── Main scan loop ──
    signals_a: List[Dict] = []  # Mode A: base only
    signals_b: List[Dict] = []  # Mode B: base + 13F
    rejection_counts: Dict[str, int] = defaultdict(int)
    # rejection_details: (ticker, reason) — used for per-bucket bias breakdown
    rejection_details: List[Tuple[str, str]] = []
    threshold_cross_up = 0    # passed only because of +13F
    threshold_cross_down = 0  # would have passed but 13F pushed below 65

    total_evals = 0
    logger.info("Running backtest: %d tickers × %d scan dates", len(universe), len(scan_dates))

    for scan_date in scan_dates:
        logger.info("  Scan date: %s", scan_date)

        spy_bars   = get_bars_as_of(price_data, "SPY", scan_date, BARS_NEEDED)
        spy_closes = [b["close"] for b in spy_bars] if spy_bars else []

        for ticker in universe:
            if ticker not in price_data:
                continue
            total_evals += 1

            bars = get_bars_as_of(price_data, ticker, scan_date, BARS_NEEDED)
            if not bars:
                rejection_counts["no_price_data"] += 1
                rejection_details.append((ticker, "no_price_data"))
                continue

            fund_data = fund_cache.get(ticker)

            # ── Mode A: base only ──
            sig_a, rej_a = evaluate_as_of(
                ticker, bars, spy_closes, fund_data,
                thirteen_f_activity=None, include_13f=False
            )
            if sig_a:
                fwd = {k: get_forward_return(price_data, ticker, scan_date, v)
                       for k, v in FWD_WINDOWS.items()}
                sig_a.update({
                    "scan_date":   scan_date.isoformat(),
                    "size_bucket": SIZE_BUCKET.get(ticker, "mid"),
                    **fwd,
                })
                signals_a.append(sig_a)
            else:
                rejection_counts[rej_a] += 1
                rejection_details.append((ticker, rej_a))

            # ── Mode B: base + 13F ──
            if include_13f and whale_tracker is not None:
                activity = None
                try:
                    activity = whale_tracker.get_activity_as_of(ticker, scan_date)
                except Exception as exc:
                    logger.debug("13F lookup failed %s/%s: %s", ticker, scan_date, exc)

                sig_b, rej_b = evaluate_as_of(
                    ticker, bars, spy_closes, fund_data,
                    thirteen_f_activity=activity, include_13f=True
                )
                if sig_b:
                    fwd = {k: get_forward_return(price_data, ticker, scan_date, v)
                           for k, v in FWD_WINDOWS.items()}
                    sig_b.update({
                        "scan_date":   scan_date.isoformat(),
                        "size_bucket": SIZE_BUCKET.get(ticker, "mid"),
                        **fwd,
                    })
                    signals_b.append(sig_b)

                # Threshold-crossing analysis
                if sig_b and not sig_a:
                    threshold_cross_up += 1   # 13F pushed above MIN_SCORE
                if sig_a and not sig_b:
                    threshold_cross_down += 1  # 13F pushed below MIN_SCORE

    logger.info("Backtest complete: %d evaluations", total_evals)
    return {
        "signals_a":          signals_a,
        "signals_b":          signals_b,
        "rejection_counts":   dict(rejection_counts),
        "rejection_details":  rejection_details,
        "threshold_cross_up":   threshold_cross_up,
        "threshold_cross_down": threshold_cross_down,
        "total_evals":          total_evals,
        "scan_dates":           [d.isoformat() for d in scan_dates],
        "universe":             universe,
        "include_13f":          include_13f,
    }


# ════════════════════════════════════════════════════════════════════════════════
# Reporting
# ════════════════════════════════════════════════════════════════════════════════

def _pct(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x:+.1f}%"


def _avg(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return round(statistics.mean(clean), 1) if clean else None


def _win_rate(vals: List[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return round(sum(1 for v in clean if v > 0) / len(clean) * 100, 1)


def print_report(results: Dict) -> None:
    signals_a = results["signals_a"]
    signals_b = results["signals_b"]
    include_13f = results["include_13f"]

    def section(title: str):
        print(f"\n{'═'*70}")
        print(f"  {title}")
        print(f"{'═'*70}")

    def archetype_breakdown(signals: List[Dict], label: str):
        by_arch: Dict[str, List[Dict]] = defaultdict(list)
        for s in signals:
            by_arch[s["archetype"]].append(s)
        print(f"\n  {label} — Archetype Breakdown:")
        print(f"  {'Archetype':<22} {'Count':>6} {'AvgScore':>9} {'Avg13F':>8} "
              f"{'Ret30d':>8} {'Ret90d':>8} {'Ret180d':>9} {'WR90d':>7}")
        print(f"  {'-'*82}")
        for arch in ["BASE_ACCUMULATION", "TREND_PULLBACK", "EARLY_ACCUMULATION"]:
            sigs = by_arch.get(arch, [])
            if not sigs:
                print(f"  {arch:<22} {'0':>6}")
                continue
            scores = [s["score"] for s in sigs]
            pts13f = [s["thirteen_f_pts"] for s in sigs]
            r30    = [s.get("30d") for s in sigs]
            r90    = [s.get("90d") for s in sigs]
            r180   = [s.get("180d") for s in sigs]
            print(f"  {arch:<22} {len(sigs):>6} {statistics.mean(scores):>9.1f} "
                  f"{statistics.mean(pts13f):>8.1f} "
                  f"{_pct(_avg(r30)):>8} {_pct(_avg(r90)):>8} {_pct(_avg(r180)):>9} "
                  f"{_win_rate(r90) or 'N/A':>7}")

    section("VOYAGER v2 VALIDATION BACKTEST")
    print(f"\n  Universe:    {len(results['universe'])} tickers")
    print(f"  Scan dates:  {results['scan_dates'][0]} → {results['scan_dates'][-1]} "
          f"({len(results['scan_dates'])} dates)")
    print(f"  Total evals: {results['total_evals']}")
    print(f"  Mode B (13F): {'ENABLED (historical, anti-lookahead)' if include_13f else 'DISABLED (run with --include-13f)'}")

    section("1. SIGNAL COUNTS")
    print(f"\n  Mode A (base only):    {len(signals_a)} signals  "
          f"({len(signals_a)/results['total_evals']*100:.1f}% signal rate)")
    if include_13f:
        print(f"  Mode B (base + 13F):   {len(signals_b)} signals  "
              f"({len(signals_b)/results['total_evals']*100:.1f}% signal rate)")
        print(f"\n  Threshold cross-UP   (13F pushed above 65): {results['threshold_cross_up']}")
        print(f"  Threshold cross-DOWN (13F pushed below 65): {results['threshold_cross_down']}")
        pct_influenced = (results['threshold_cross_up'] + results['threshold_cross_down'])
        print(f"  Total 13F-influenced threshold events:      {pct_influenced}")

    section("2. REJECTIONS (Mode A)")
    rej = sorted(results["rejection_counts"].items(), key=lambda x: -x[1])
    print(f"\n  {'Reason':<30} {'Count':>6} {'%':>7}")
    print(f"  {'-'*45}")
    total_rej = sum(results["rejection_counts"].values())
    for reason, count in rej[:12]:
        print(f"  {reason:<30} {count:>6} {count/total_rej*100:>6.1f}%")

    section("3. ARCHETYPE BREAKDOWN — MODE A")
    archetype_breakdown(signals_a, "Mode A")
    if include_13f:
        section("4. ARCHETYPE BREAKDOWN — MODE B")
        archetype_breakdown(signals_b, "Mode B")

    section("5. 13F SCORE DISTRIBUTION (Mode B)" if include_13f else "5. 13F N/A (Mode A only)")
    if include_13f and signals_b:
        pts_dist: Dict[int, int] = defaultdict(int)
        for s in signals_b:
            pts_dist[s["thirteen_f_pts"]] += 1
        print(f"\n  {'13F pts':<10} {'Count':>6} {'%':>7}")
        print(f"  {'-'*25}")
        for pts in sorted(pts_dist.keys(), reverse=True):
            n = pts_dist[pts]
            print(f"  {pts:>+4}       {n:>6} {n/len(signals_b)*100:>6.1f}%")
        pts_vals = [s["thirteen_f_pts"] for s in signals_b]
        print(f"\n  Average 13F pts: {statistics.mean(pts_vals):.2f}")
    elif not include_13f:
        print("\n  Mode B not run. Use --include-13f to enable historical 13F comparison.")

    section("6. FORWARD RETURNS — MODE A (all signals)")
    if signals_a:
        print(f"\n  {'Horizon':<10} {'N':>5} {'AvgRet':>9} {'WinRate':>9} {'Med':>9}")
        print(f"  {'-'*45}")
        for lbl in ["30d", "90d", "180d", "365d"]:
            vals = [s.get(lbl) for s in signals_a]
            n = sum(1 for v in vals if v is not None)
            print(f"  {lbl:<10} {n:>5} {_pct(_avg(vals)):>9} "
                  f"{_win_rate(vals) or 'N/A':>9} "
                  f"{_pct(sorted([v for v in vals if v is not None])[n//2] if n else None):>9}")
    else:
        print("\n  No signals generated.")

    if include_13f and signals_b:
        section("7. FORWARD RETURNS — MODE B (base + 13F)")
        print(f"\n  {'Horizon':<10} {'N':>5} {'AvgRet':>9} {'WinRate':>9} {'Med':>9}")
        print(f"  {'-'*45}")
        for lbl in ["30d", "90d", "180d", "365d"]:
            vals = [s.get(lbl) for s in signals_b]
            n = sum(1 for v in vals if v is not None)
            print(f"  {lbl:<10} {n:>5} {_pct(_avg(vals)):>9} "
                  f"{_win_rate(vals) or 'N/A':>9} "
                  f"{_pct(sorted([v for v in vals if v is not None])[n//2] if n else None):>9}")

    section("8. SAMPLE SIGNALS — STRATEGY IDENTITY AUDIT")
    if signals_a:
        # Group by archetype, show top 3 per archetype
        by_arch: Dict[str, List[Dict]] = defaultdict(list)
        for s in signals_a:
            by_arch[s["archetype"]].append(s)
        for arch in ["BASE_ACCUMULATION", "TREND_PULLBACK", "EARLY_ACCUMULATION"]:
            sigs = sorted(by_arch.get(arch, []), key=lambda x: -x["score"])[:4]
            if not sigs:
                continue
            print(f"\n  {arch}:")
            for s in sigs:
                fwd_str = ""
                if s.get("180d") is not None:
                    fwd_str = f"  180d={_pct(s['180d'])}"
                elif s.get("90d") is not None:
                    fwd_str = f"  90d={_pct(s['90d'])}"
                print(f"    {s['scan_date']}  {s['ticker']:<6}  "
                      f"score={s['score']:>3}  fund={s['fund_score']:>3}  "
                      f"rs50={s['rs_50d']:>+6.1f}%  dvol={s['dvol_ratio']:.2f}"
                      f"{fwd_str}")

    section("9. CONCLUSIONS")
    _print_conclusions(results)

    # Section 10 only prints when the expanded universe was used (--bias flag)
    univ = results["universe"]
    has_emerging = any(SIZE_BUCKET.get(t) == "emerging" for t in univ)
    if has_emerging:
        section("10. SIZE-BUCKET BIAS ANALYSIS")
        _print_size_bias(results)


def _print_conclusions(results: Dict):
    signals_a = results["signals_a"]
    signals_b = results["signals_b"]
    include_13f = results["include_13f"]

    total = results["total_evals"]
    sig_rate_a = len(signals_a) / total * 100 if total else 0

    # Forward return analysis
    r90_a = [s.get("90d") for s in signals_a if s.get("90d") is not None]
    r180_a = [s.get("180d") for s in signals_a if s.get("180d") is not None]
    wr90_a = _win_rate(r90_a)
    avg90_a = _avg(r90_a)
    avg180_a = _avg(r180_a)

    # Archetype signal rate
    arch_counts = defaultdict(int)
    for s in signals_a:
        arch_counts[s["archetype"]] += 1

    print(f"""
  MODE A (base scanner only):
    Signal rate:     {sig_rate_a:.1f}% ({len(signals_a)}/{total} evals)
    90d avg return:  {_pct(avg90_a)}  |  win rate: {wr90_a or 'N/A'}%
    180d avg return: {_pct(avg180_a)}
    Archetype mix:   BASE={arch_counts['BASE_ACCUMULATION']}  "
             PULLBACK={arch_counts['TREND_PULLBACK']}  EARLY={arch_counts['EARLY_ACCUMULATION']}""")

    if include_13f and signals_b:
        r90_b   = [s.get("90d")  for s in signals_b if s.get("90d")  is not None]
        r180_b  = [s.get("180d") for s in signals_b if s.get("180d") is not None]
        wr90_b  = _win_rate(r90_b)
        avg90_b = _avg(r90_b)
        avg180_b = _avg(r180_b)
        pts_vals = [s["thirteen_f_pts"] for s in signals_b]
        avg_pts  = statistics.mean(pts_vals) if pts_vals else 0.0

        cross_pct = (results["threshold_cross_up"] + results["threshold_cross_down"])
        print(f"""
  MODE B (base + 13F, anti-lookahead):
    Signal rate:     {len(signals_b)/total*100:.1f}% ({len(signals_b)}/{total} evals)
    90d avg return:  {_pct(avg90_b)}  |  win rate: {wr90_b or 'N/A'}%
    180d avg return: {_pct(avg180_b)}
    Avg 13F pts:     {avg_pts:.2f}
    Threshold events: {cross_pct} signals changed pass/fail due to 13F

  13F OVERLAY VERDICT:
    Adds value if avg 13F pts correlates with forward return improvement.
    A → B 90d return delta: {_pct(round(avg90_b - avg90_a, 1)) if avg90_a and avg90_b else 'N/A'}
    A → B 180d return delta: {_pct(round(avg180_b - avg180_a, 1)) if avg180_a and avg180_b else 'N/A'}""")

    # Sniper overlap check
    rej_counts = results["rejection_counts"]
    no_archetype = rej_counts.get("no_archetype", 0)
    too_extended = rej_counts.get("too_extended", 0)
    print(f"""
  DOCTRINE CHECK (Voyager vs Sniper overlap):
    'no_archetype' rejections: {no_archetype}  (expected to be common — tight filter)
    'too_extended' rejections: {too_extended}  (stocks breaking out → Sniper territory)
    If 'too_extended' >> 'no_archetype', scanner may drift toward chasing breakouts.
    Healthy ratio: too_extended < no_archetype.

  RECOMMENDATION:""")

    if sig_rate_a > 20:
        print("    Signal rate > 20% is too high — Voyager should be selective (target 5–10%).")
        print("    Consider tightening archetype thresholds.")
    elif sig_rate_a < 1:
        print("    Signal rate < 1% — scanner may be too restrictive for the universe tested.")
        print("    Check dvol_fading and selling_dominates rejection rates.")
    else:
        print(f"    Signal rate {sig_rate_a:.1f}% is within expected range for Voyager.")

    if wr90_a and wr90_a > 55:
        print(f"    90d win rate {wr90_a}% > 55% — setup quality looks promising for long-horizon holds.")
    elif wr90_a and wr90_a < 45:
        print(f"    90d win rate {wr90_a}% < 45% — review gate thresholds or expand sample.")

    if not include_13f:
        print("\n  Mode B not run. Rerun with --include-13f for 13F overlay comparison.")
        print("  Expected runtime: 15–25 minutes (13F filing prefetch for all institutions).")


def _print_size_bias(results: Dict) -> None:
    """
    Size-bucket breakdown:
    • How many tickers per bucket in universe / how many produced signals
    • Signal rate per bucket
    • Top rejection reasons per bucket — identifies structural gates
    • Average score and forward returns per bucket
    """
    signals_a   = results["signals_a"]
    rej_details = results.get("rejection_details", [])
    universe    = results["universe"]
    scan_dates  = results["scan_dates"]
    n_dates     = len(scan_dates)

    BUCKETS = ["large", "mid", "emerging"]

    # Universe composition
    bucket_tickers: Dict[str, List[str]] = {b: [] for b in BUCKETS}
    for t in universe:
        b = SIZE_BUCKET.get(t, "mid")
        bucket_tickers[b].append(t)

    print(f"\n  Universe composition:")
    for b in BUCKETS:
        print(f"    {b:<12} {len(bucket_tickers[b]):>3} tickers — {', '.join(sorted(bucket_tickers[b]))}")

    # Total evaluations per bucket (tickers × scan dates, excluding missing data)
    # Use rejection_details + signals to reconstruct total evaluated per bucket
    bucket_evals: Dict[str, int] = defaultdict(int)
    for t, _ in rej_details:
        bucket_evals[SIZE_BUCKET.get(t, "mid")] += 1
    for s in signals_a:
        bucket_evals[s.get("size_bucket", "mid")] += 1

    # Signal counts and rates
    bucket_signals: Dict[str, List[Dict]] = defaultdict(list)
    for s in signals_a:
        bucket_signals[s.get("size_bucket", "mid")].append(s)

    print(f"\n  Signal rate by bucket (Mode A):")
    print(f"  {'Bucket':<12} {'Tickers':>8} {'Evals':>7} {'Signals':>8} {'Rate':>7} "
          f"{'AvgScore':>9} {'Avg90d':>8} {'WR90d':>7} {'Avg180d':>8}")
    print(f"  {'-'*75}")
    for b in BUCKETS:
        sigs  = bucket_signals[b]
        n_ev  = bucket_evals[b]
        rate  = len(sigs) / n_ev * 100 if n_ev else 0
        scores = [s["score"] for s in sigs]
        r90    = [s.get("90d")  for s in sigs]
        r180   = [s.get("180d") for s in sigs]
        avg_score_str = f"{statistics.mean(scores):.1f}" if scores else "—"
        print(f"  {b:<12} {len(bucket_tickers[b]):>8} {n_ev:>7} {len(sigs):>8} "
              f"{rate:>6.1f}% {avg_score_str:>9} "
              f"{_pct(_avg(r90)):>8} {_win_rate(r90) or '—':>7} {_pct(_avg(r180)):>8}")

    # Top rejection reasons per bucket
    print(f"\n  Top rejection reasons by bucket (Mode A):")
    for b in BUCKETS:
        rej_for_bucket = [reason for ticker, reason in rej_details
                          if SIZE_BUCKET.get(ticker, "mid") == b]
        if not rej_for_bucket:
            print(f"\n  {b.upper()}: no rejections recorded")
            continue
        total_rej = len(rej_for_bucket)
        counter: Dict[str, int] = defaultdict(int)
        for r in rej_for_bucket:
            counter[r] += 1
        top = sorted(counter.items(), key=lambda x: -x[1])[:6]
        print(f"\n  {b.upper()} ({total_rej} rejections across all scan dates):")
        for reason, cnt in top:
            print(f"    {reason:<30} {cnt:>5}  ({cnt/total_rej*100:.0f}%)")

    # Archetype mix by bucket
    print(f"\n  Archetype distribution by bucket (Mode A signals only):")
    print(f"  {'Bucket':<12} {'BASE':>6} {'PULLBACK':>9} {'EARLY':>7} {'Total':>7}")
    print(f"  {'-'*42}")
    for b in BUCKETS:
        sigs = bucket_signals[b]
        n_base = sum(1 for s in sigs if s["archetype"] == "BASE_ACCUMULATION")
        n_pull = sum(1 for s in sigs if s["archetype"] == "TREND_PULLBACK")
        n_earl = sum(1 for s in sigs if s["archetype"] == "EARLY_ACCUMULATION")
        print(f"  {b:<12} {n_base:>6} {n_pull:>9} {n_earl:>7} {len(sigs):>7}")

    # Sample emerging signals (key finding for the thesis question)
    emerging_sigs = sorted(bucket_signals.get("emerging", []), key=lambda x: -x["score"])
    if emerging_sigs:
        print(f"\n  EMERGING-GROWTH signals ({len(emerging_sigs)} total):")
        for s in emerging_sigs[:8]:
            fwd_str = ""
            if s.get("180d") is not None:
                fwd_str = f"  180d={_pct(s['180d'])}"
            elif s.get("90d") is not None:
                fwd_str = f"  90d={_pct(s['90d'])}"
            print(f"    {s['scan_date']}  {s['ticker']:<6}  "
                  f"score={s['score']:>3}  fund={s['fund_score']:>3}  "
                  f"arch={s['archetype']:<22}  rs50={s['rs_50d']:>+6.1f}%"
                  f"{fwd_str}")
    else:
        print(f"\n  EMERGING-GROWTH signals: 0")
        print(f"  Interpretation: review top rejection reasons above to identify")
        print(f"  which structural gates are filtering all emerging-growth candidates.")

    # Bias verdict
    print(f"\n  BIAS VERDICT:")
    n_large   = len(bucket_signals["large"])
    n_mid     = len(bucket_signals["mid"])
    n_emerg   = len(bucket_signals["emerging"])
    rate_large = n_large / bucket_evals["large"]  * 100 if bucket_evals["large"]  else 0
    rate_mid   = n_mid   / bucket_evals["mid"]    * 100 if bucket_evals["mid"]    else 0
    rate_emerg = n_emerg / bucket_evals["emerging"]* 100 if bucket_evals["emerging"] else 0

    print(f"    Signal rate — large: {rate_large:.1f}%  mid: {rate_mid:.1f}%  emerging: {rate_emerg:.1f}%")
    if rate_emerg >= rate_mid * 0.5:
        print(f"    Emerging-growth signal rate is ≥50% of mid-cap rate.")
        print(f"    Voyager is NOT strongly biased toward large established names.")
        print(f"    It can identify smaller accumulating names when they meet structure gates.")
    elif rate_emerg > 0:
        print(f"    Emerging-growth signal rate is below 50% of mid-cap rate.")
        print(f"    Moderate size bias. Check top rejection reasons above.")
        print(f"    Primary filters for smaller names are likely fundamental floor and golden cross.")
    else:
        print(f"    Emerging-growth signal rate = 0%. Strong size bias exists.")
        print(f"    Voyager is currently an institutional-quality large/mid leader finder.")
        print(f"    For emerging-growth coverage, the primary blockers are above.")


# ════════════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Voyager v2 backtest")
    parser.add_argument("--quick",       action="store_true", help="Small universe + 4 dates")
    parser.add_argument("--skip-13f",    action="store_true", help="Mode A only (fast)")
    parser.add_argument("--include-13f", dest="include_13f", action="store_true",
                        help="Mode A + B (slow — prefetches historical 13F filings)")
    parser.add_argument("--bias",        action="store_true",
                        help="Expanded universe (78 tickers) + size-bucket bias report")
    args = parser.parse_args()

    if args.bias:
        universe   = EXPANDED_UNIVERSE
        scan_dates = FULL_SCAN_DATES
    elif args.quick:
        universe   = QUICK_UNIVERSE
        scan_dates = QUICK_SCAN_DATES
    else:
        universe   = FULL_UNIVERSE
        scan_dates = FULL_SCAN_DATES
    include_13f = args.include_13f and not args.skip_13f

    t0 = time.time()
    results = run_backtest(universe, scan_dates, include_13f=include_13f)
    elapsed = time.time() - t0

    print_report(results)
    print(f"\n  Total runtime: {elapsed/60:.1f} minutes")
