"""
core/fmp_client.py — FMP client (stable API, plan: 750 RPM / 50K monthly).

All endpoints use the FMP /stable/ API. FMP_BASE_URL must be set to
  https://financialmodelingprep.com/stable

Stable API endpoint reference:
  get_vix()              → /stable/quote?symbol=^VIX
  get_quotes_batch(syms) → /stable/batch-quote?symbols=SYM1,SYM2,…  (up to 50/call)
  get_spy_bars(days)     → /stable/historical-price-eod/full?symbol=SPY&from=…&to=…
  get_ticker_bars(tick)  → /stable/historical-price-eod/full?symbol=TICK&from=…&to=…
  get_economic_calendar()→ /stable/economic-calendar?from=…&to=…
  get_treasury_rates()   → /stable/treasury-rates?from=…
  get_news(ticker)       → /stable/news/stock?tickers=…&limit=…
  get_earnings_calendar()→ /stable/earnings-calendar?from=…&to=…
  get_fundamentals(tick) → /stable/income-statement + /balance-sheet + /cash-flow
  get_sector_pe()        → not available on Starter plan — returns [] gracefully

Key stable API differences vs legacy /v3/:
  • Symbol is a query param (?symbol=NVDA), NOT a URL path segment (/NVDA)
  • Batch quotes: comma-separated ?symbol=AAPL,MSFT,NVDA (one call, many results)
  • grossProfitRatio not returned — computed here as grossProfit / revenue
  • historical-price-eod/full uses from=/to= date params, not timeseries=N
  • earnings endpoint is /stable/earnings-calendar (hyphen, not underscore)

Budget model:
  750 RPM rate limit (token bucket).
  50,000 calls/month (monthly enforcement in Gatekeeper).
  All calls go through budget_consume() + log_endpoint() for visibility.

Quote caching strategy:
  Batch quotes via get_quotes_batch() are cached for TTL_QUOTE (20 s) under
  key fmp:quote:{TICKER}. get_vix() and _fetch_etf_quotes() share this cache.
  During after-hours the TTL extends to TTL_QUOTE_AH (5 min) automatically.
"""
from __future__ import annotations
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import requests

import core.config as cfg
from core.data_gatekeeper import (
    Gatekeeper, get_gatekeeper,
    TTL_VIX, TTL_OHLCV, TTL_ECONOMIC_CAL, TTL_TREASURY,
    TTL_NEWS, TTL_EARNINGS_CAL, TTL_FUNDAMENTALS, TTL_QUOTE,
)

QUOTE_BATCH_SIZE = 50          # FMP stable quote supports ≥50 symbols per call
TTL_QUOTE_AH     = 5 * 60      # after-hours quote TTL: 5 min (price moves slowly)

logger = logging.getLogger(__name__)


class _TokenBucket:
    """Thread-safe token bucket — enforces FMP_CALLS_PER_MINUTE."""

    def __init__(self, rate_per_min: int):
        self._rate = rate_per_min
        self._tokens = float(rate_per_min)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, n: int = 1) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self._rate, self._tokens + elapsed * (self._rate / 60.0))
            if self._tokens < n:
                sleep_for = (n - self._tokens) / (self._rate / 60.0)
                time.sleep(sleep_for)
                self._tokens = 0.0
            else:
                self._tokens -= n


class FMPClient:
    """
    All FMP calls go through here.
    Cache-first: hit Gatekeeper before any HTTP request.
    """

    def __init__(self):
        self._gate: Gatekeeper = get_gatekeeper()
        self._bucket = _TokenBucket(cfg.FMP_CALLS_PER_MINUTE)
        self._session = requests.Session()
        self._session.params = {"apikey": cfg.FMP_API_KEY}  # type: ignore[assignment]

    # ── Internal HTTP ─────────────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[Dict] = None, budget_cost: int = 1) -> Any:
        # Hard enforcement: 750 RPM token bucket only.
        # budget_consume() is telemetry-only (always returns True).
        # No monthly/daily call cap is enforced — plan page shows 750 RPM + 50 GB
        # bandwidth, not a call count ceiling.
        self._gate.budget_consume(budget_cost)   # increments counters, never blocks
        self._bucket.consume(budget_cost)         # ← the only real gate
        url = f"{cfg.FMP_BASE_URL}{path}"
        try:
            resp = self._session.get(url, params=params or {}, timeout=15)
            resp.raise_for_status()
            resp_bytes = len(resp.content)
            self._gate.log_endpoint(path, saved=0, resp_bytes=resp_bytes)
            return resp.json()
        except requests.HTTPError as exc:
            logger.error("FMP HTTP error %s %s: %s", path, params, exc)
            raise
        except Exception as exc:
            logger.error("FMP request failed %s: %s", path, exc)
            raise

    @staticmethod
    def _quote_ttl() -> float:
        """Return the appropriate quote TTL based on market hours (ET)."""
        try:
            from zoneinfo import ZoneInfo
            now_et = datetime.now(tz=timezone.utc).astimezone(ZoneInfo("America/New_York"))
            mins = now_et.hour * 60 + now_et.minute
            # Regular market hours: 09:30–16:00 ET Mon–Fri
            if now_et.weekday() < 5 and (9 * 60 + 30) <= mins < 16 * 60:
                return float(TTL_QUOTE)
        except Exception:
            pass
        return float(TTL_QUOTE_AH)

    # ── Batch quotes ──────────────────────────────────────────────────────────

    def get_quotes_batch(self, symbols: Sequence[str]) -> Dict[str, Dict]:
        """
        Fetch quotes for multiple symbols in one FMP call (up to QUOTE_BATCH_SIZE).
        Returns {TICKER: {price, change_pct, prev_close, volume}} for all returned.
        Results are cached per-ticker at TTL_QUOTE (20s) or TTL_QUOTE_AH (5min).
        Skips any symbol already cached.

        Batch size: QUOTE_BATCH_SIZE (50). Larger lists are split automatically.
        One FMP call per batch of 50 — vs. N calls for N symbols without batching.
        """
        if not symbols:
            return {}

        ttl = self._quote_ttl()
        result: Dict[str, Dict] = {}
        need_fetch: List[str] = []

        for sym in symbols:
            key = f"fmp:quote:{sym.upper()}"
            cached = self._gate.get(key, ttl)
            if cached is not None:
                result[sym.upper()] = cached
                self._gate.log_endpoint("/quote", saved=1)
            else:
                need_fetch.append(sym.upper())

        if need_fetch:
            logger.debug("Quote batch fetch: %d symbols (%d already cached)",
                         len(need_fetch), len(symbols) - len(need_fetch))

        # Split into batches of QUOTE_BATCH_SIZE
        # Endpoint: /stable/batch-quote?symbols=SYM1,SYM2,…
        # (NOT /quote?symbol= — that does not support multiple symbols on stable plan)
        for offset in range(0, len(need_fetch), QUOTE_BATCH_SIZE):
            chunk = need_fetch[offset: offset + QUOTE_BATCH_SIZE]
            symbol_str = ",".join(chunk)
            try:
                data = self._get("/batch-quote", params={"symbols": symbol_str})
                if not isinstance(data, list):
                    continue
                for q in data:
                    sym = (q.get("symbol") or "").upper()
                    if not sym:
                        continue
                    entry = {
                        "price":       float(q.get("price")            or 0),
                        "change_pct":  float(q.get("changePercentage") or 0),
                        "prev_close":  float(q.get("previousClose")    or 0),
                        "volume":      int(q.get("volume")             or 0),
                        "day_high":    float(q.get("dayHigh")          or 0),
                        "day_low":     float(q.get("dayLow")           or 0),
                    }
                    self._gate.put(f"fmp:quote:{sym}", entry)
                    result[sym] = entry
            except Exception as exc:
                logger.warning("Quote batch failed for chunk %s: %s", chunk[:3], exc)

        missing = set(sym.upper() for sym in symbols) - set(result)
        if missing:
            logger.debug("Quote batch: %d symbols not returned by FMP: %s",
                         len(missing), sorted(missing)[:5])
        return result

    # ── VIX ───────────────────────────────────────────────────────────────────

    def get_vix(self) -> Optional[float]:
        """Returns current VIX level. Cached 5 min via TTL_VIX."""
        key = "fmp:vix"
        cached = self._gate.get(key, TTL_VIX)
        if cached is not None:
            self._gate.log_endpoint("/quote", saved=1)
            return float(cached)
        data = self._get("/quote", params={"symbol": "^VIX"})
        if data and isinstance(data, list):
            level = float(data[0].get("price", 0) or 0)
            self._gate.put(key, level)
            return level
        return None

    # ── Ticker price bars ─────────────────────────────────────────────────────

    def get_ticker_bars(self, ticker: str, days: int = 60) -> List[Dict]:
        """
        Daily OHLCV bars for an arbitrary ticker. Cached 12 h (TTL_OHLCV).
        Used by Mode 2 analysis to avoid direct _get() cache bypasses.
        """
        sym = ticker.upper()
        key = f"fmp:bars:{sym}:{days}"
        cached = self._gate.get(key, TTL_OHLCV)
        if cached is not None:
            self._gate.log_endpoint("/historical-price-eod/full", saved=1)
            logger.debug("Price bars cache hit: %s (%d days)", sym, days)
            return cached
        to_dt   = date.today().isoformat()
        from_dt = (date.today() - timedelta(days=int(days * 1.5) + 10)).isoformat()
        data = self._get(
            "/historical-price-eod/full",
            params={"symbol": sym, "from": from_dt, "to": to_dt},
        )
        bars = data if isinstance(data, list) else []
        result = sorted(
            [{"date":   b.get("date"),
              "open":   float(b.get("open",  0)),
              "high":   float(b.get("high",  0)),
              "low":    float(b.get("low",   0)),
              "close":  float(b.get("close", 0)),
              "volume": int(b.get("volume",  0))}
             for b in bars],
            key=lambda x: x["date"],
        )
        self._gate.put(key, result)
        return result

    # ── SPY bars (regime detection, washout gate) ─────────────────────────────

    def get_spy_bars(self, days: int = 60) -> List[Dict]:
        """Daily OHLCV bars for SPY. Cached 12 h."""
        key = f"fmp:spy_bars:{days}"
        cached = self._gate.get(key, TTL_OHLCV)
        if cached is not None:
            return cached
        to_dt   = date.today().isoformat()
        from_dt = (date.today() - timedelta(days=int(days * 1.45) + 10)).isoformat()
        data = self._get(
            "/historical-price-eod/full",
            params={"symbol": "SPY", "from": from_dt, "to": to_dt},
        )
        bars = data if isinstance(data, list) else []
        result = [
            {
                "date":   b.get("date"),
                "open":   float(b.get("open",  0)),
                "high":   float(b.get("high",  0)),
                "low":    float(b.get("low",   0)),
                "close":  float(b.get("close", 0)),
                "volume": int(b.get("volume",  0)),
            }
            for b in bars
        ]
        # Stable API returns newest-first; normalize to oldest-first
        result.sort(key=lambda x: x["date"])
        self._gate.put(key, result)
        return result

    # ── Economic calendar ─────────────────────────────────────────────────────

    def get_economic_calendar(self, days_ahead: int = 7) -> List[Dict]:
        """High-impact macro events. Cached 4 h."""
        key = f"fmp:econ_cal:{days_ahead}"
        cached = self._gate.get(key, TTL_ECONOMIC_CAL)
        if cached is not None:
            return cached
        from_dt = date.today().isoformat()
        to_dt   = (date.today() + timedelta(days=days_ahead)).isoformat()
        data = self._get(
            "/economic-calendar",
            params={"from": from_dt, "to": to_dt},
        )
        events = data if isinstance(data, list) else []
        self._gate.put(key, events)
        return events

    # ── Treasury rates ────────────────────────────────────────────────────────

    def get_treasury_rates(self) -> Optional[Dict]:
        """Current treasury yield curve. Cached 4 h."""
        key = "fmp:treasury"
        cached = self._gate.get(key, TTL_TREASURY)
        if cached is not None:
            return cached
        data = self._get("/treasury-rates", params={"from": date.today().isoformat()})
        if data and isinstance(data, list):
            rates = data[0]  # most recent row
            self._gate.put(key, rates)
            return rates
        return None

    # ── News / sentiment ──────────────────────────────────────────────────────

    def get_news(self, ticker: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """Stock or market news headlines. Cached 1 h."""
        key = f"fmp:news:{ticker or 'market'}:{limit}"
        cached = self._gate.get(key, TTL_NEWS)
        if cached is not None:
            self._gate.log_endpoint("/news/stock", saved=1)
            return cached
        params: Dict[str, Any] = {"limit": limit}
        if ticker:
            params["tickers"] = ticker.upper()
        data = self._get("/news/stock", params=params)
        news = data if isinstance(data, list) else []
        self._gate.put(key, news)
        return news

    # ── Usage status (telemetry) ──────────────────────────────────────────────

    def budget_status(self) -> Dict[str, Any]:
        """
        Returns FMP usage telemetry for logging/display.

        Hard enforcement: 750 RPM only (token bucket).
        Monthly/daily call counts and bandwidth are informational — no hard cap
        is assumed unless confirmed from the plan page.
        """
        return {
            "used_today":        self._gate.budget_used_today(),
            "used_month":        self._gate.budget_used_month(),
            "rate_limit_rpm":    cfg.FMP_CALLS_PER_MINUTE,
            "monthly_cap":       "unconfirmed — not enforced",
            "bandwidth_mb_month": self._gate.bandwidth_used_month_mb(),
            "endpoint_summary":  self._gate.endpoint_summary(since_hours=24),
        }

    # ── Earnings calendar ─────────────────────────────────────────────────────

    def get_earnings_calendar(self, days_ahead: int = 14) -> List[Dict]:
        """Upcoming earnings dates. Cached 6 h."""
        key = f"fmp:earnings_cal:{days_ahead}"
        cached = self._gate.get(key, TTL_EARNINGS_CAL)
        if cached is not None:
            return cached
        from_dt = date.today().isoformat()
        to_dt   = (date.today() + timedelta(days=days_ahead)).isoformat()
        data = self._get(
            "/earnings-calendar",
            params={"from": from_dt, "to": to_dt},
        )
        events = data if isinstance(data, list) else []
        self._gate.put(key, events)
        return events

    def get_past_earnings(self, lookback_days: int = 14) -> List[Dict]:
        """
        Recent past earnings announcements (last N days). Cached 6 h.

        Used by SHORT scanner to find recent event triggers.
        Returns list of dicts with keys:
          symbol, date, epsActual, epsEstimated, revenueActual, revenueEstimated

        Note: FMP stable API field is 'epsActual' (not 'eps' as in legacy /v3/ endpoints).
        """
        key = f"fmp:past_earnings:{lookback_days}"
        cached = self._gate.get(key, TTL_EARNINGS_CAL)
        if cached is not None:
            return cached
        from_dt = (date.today() - timedelta(days=lookback_days)).isoformat()
        to_dt   = date.today().isoformat()
        data = self._get(
            "/earnings-calendar",
            params={"from": from_dt, "to": to_dt},
        )
        events = data if isinstance(data, list) else []
        self._gate.put(key, events)
        return events

    # ── Company fundamentals ──────────────────────────────────────────────────

    def get_fundamentals(self, ticker: str) -> Optional[Dict]:
        """
        Income statement + balance sheet + cash flow (last 4 quarters each).
        Cached 24 h.

        Returns dict with keys: ticker, income, balance, cashflow.
        Each is a list of up to 4 quarterly dicts, newest-first.

        Notes on stable API field differences vs legacy /v3/:
          • grossProfitRatio is not returned by the API — computed here as
            grossProfit / revenue (same value, avoids code changes in callers).
          • All other fields used by Voyager's fundamental_score() are present:
            revenue, netIncome, operatingIncome, totalDebt,
            totalStockholdersEquity, cashAndShortTermInvestments, operatingCashFlow.
        """
        cached = self._gate.get_fundamentals(ticker)
        if cached is not None:
            logger.debug("Fundamentals cache hit: %s", ticker)
            return cached

        sym = ticker.upper()
        try:
            income = self._get(
                "/income-statement",
                params={"symbol": sym, "period": "quarter", "limit": 4},
            )
            balance = self._get(
                "/balance-sheet-statement",
                params={"symbol": sym, "period": "quarter", "limit": 4},
            )
            cashflow = self._get(
                "/cash-flow-statement",
                params={"symbol": sym, "period": "quarter", "limit": 4},
            )

            income   = income   if isinstance(income,   list) else []
            balance  = balance  if isinstance(balance,  list) else []
            cashflow = cashflow if isinstance(cashflow, list) else []

            # Compute grossProfitRatio (not returned by stable API)
            for row in income:
                rev = row.get("revenue") or 0
                gp  = row.get("grossProfit") or 0
                row["grossProfitRatio"] = round(gp / rev, 4) if rev else 0.0

            result = {
                "ticker":   sym,
                "income":   income,
                "balance":  balance,
                "cashflow": cashflow,
            }

            if income or balance or cashflow:
                logger.info(
                    "Fundamentals fetched: %s  income=%d qtrs  balance=%d qtrs  "
                    "cashflow=%d qtrs  rev[0]=%s  netInc[0]=%s  ocf[0]=%s",
                    sym, len(income), len(balance), len(cashflow),
                    income[0].get("revenue")          if income   else "N/A",
                    income[0].get("netIncome")         if income   else "N/A",
                    cashflow[0].get("operatingCashFlow") if cashflow else "N/A",
                )
            else:
                logger.warning("Fundamentals empty for %s (all three statements returned [])", sym)

            self._gate.put_fundamentals(ticker, result)
            return result

        except Exception as exc:
            logger.warning("Fundamentals fetch failed %s: %s", ticker, exc)
            return None

    # ── Company profile (market cap, sector) ─────────────────────────────────

    def get_company_profile(self, ticker: str) -> Optional[Dict]:
        """
        Returns basic company profile: marketCap, sector, industry, companyName.
        Cached 24 h. Returns None if unavailable.

        Used by voyager_paper_logger to classify size bucket at signal time.
        """
        key = f"fmp:profile:{ticker.upper()}"
        cached = self._gate.get(key, TTL_FUNDAMENTALS)
        if cached is not None:
            return cached
        try:
            data = self._get("/profile", params={"symbol": ticker.upper()})
            # Stable API returns a list with one element
            profile = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
            if not profile:
                return None
            result = {
                "ticker":      ticker.upper(),
                "companyName": profile.get("companyName") or profile.get("name", ""),
                "sector":      profile.get("sector", ""),
                "industry":    profile.get("industry", ""),
                "marketCap":   profile.get("marketCap") or profile.get("mktCap") or 0,
            }
            self._gate.put(key, result)
            return result
        except Exception as exc:
            logger.debug("get_company_profile failed %s: %s", ticker, exc)
            return None

    # ── Sector P/E ratios ─────────────────────────────────────────────────────

    def get_sector_pe(self) -> List[Dict]:
        """
        Sector-level P/E ratios. Not available on the Starter plan — returns [].
        Cached 24 h to avoid repeated failed requests.
        """
        key = "fmp:sector_pe"
        cached = self._gate.get(key, TTL_FUNDAMENTALS)
        if cached is not None:
            return cached
        logger.debug("get_sector_pe: not available on Starter plan — returning []")
        self._gate.put(key, [])
        return []

    # ── Sentiment convenience ─────────────────────────────────────────────────

    def get_sentiment_score(self, ticker: str) -> float:
        """
        Naive sentiment proxy: ratio of positive-keyword headlines in recent news.
        Returns 0.0 (bearish) to 1.0 (bullish), 0.5 = neutral.
        """
        POSITIVE = {"beat", "surge", "rally", "strong", "growth", "record", "upgrade"}
        NEGATIVE = {"miss", "fall", "drop", "weak", "loss", "downgrade", "concern", "risk"}
        news = self.get_news(ticker, limit=10)
        if not news:
            return 0.5
        pos = neg = 0
        for item in news:
            title = (item.get("title") or "").lower()
            pos += sum(1 for w in POSITIVE if w in title)
            neg += sum(1 for w in NEGATIVE if w in title)
        total = pos + neg
        if total == 0:
            return 0.5
        return round(pos / total, 3)

    # ── Insider trading (additive: research-only) ─────────────────────────────

    def get_insider_trading(self, ticker: str, limit: int = 40) -> List[Dict]:
        """
        Recent insider transactions for a ticker (Form 4 / Form 144).  Hits
        /stable/insider-trading/latest.  Cached 6h via the Gatekeeper since
        insider filings settle slowly.  Returns [] on any error so callers
        never need to defend.  Additive — not used by any production path.
        """
        ticker = ticker.upper()
        key = f"fmp:insider:{ticker}"
        cached = self._gate.get(key, 6 * 3600)
        if cached is not None:
            return cached
        try:
            data = self._get(
                "/insider-trading/latest",
                params={"symbol": ticker, "limit": int(limit)},
            )
            rows = data if isinstance(data, list) else []
            self._gate.put(key, rows)
            return rows
        except Exception as exc:
            logger.debug("get_insider_trading failed %s: %s", ticker, exc)
            self._gate.put(key, [])
            return []

    # ── Analyst grades (additive: research-only) ──────────────────────────────

    def get_analyst_grades(self, ticker: str, limit: int = 40) -> List[Dict]:
        """
        Recent analyst grade actions (upgrades / downgrades / initiations) for
        a ticker.  Hits /stable/grades.  Cached 6h.  Returns [] on any error.
        Additive — not used by any production path.
        """
        ticker = ticker.upper()
        key = f"fmp:grades:{ticker}"
        cached = self._gate.get(key, 6 * 3600)
        if cached is not None:
            return cached
        try:
            data = self._get("/grades", params={"symbol": ticker, "limit": int(limit)})
            rows = data if isinstance(data, list) else []
            self._gate.put(key, rows)
            return rows
        except Exception as exc:
            logger.debug("get_analyst_grades failed %s: %s", ticker, exc)
            self._gate.put(key, [])
            return []


# Module-level singleton
_client: Optional[FMPClient] = None


def get_fmp() -> FMPClient:
    global _client
    if _client is None:
        _client = FMPClient()
    return _client
