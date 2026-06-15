"""
Tradier Options Feed — Drop-in replacement for yfinance options chain data.

HOW TO ACTIVATE
───────────────
1. Open a Tradier brokerage or developer account at tradier.com
2. Get your API token from Account → API Access
3. Add to your environment or config:

       TRADIER_API_TOKEN=your_token_here
       TRADIER_USE_SANDBOX=false        # true = sandbox (no real data)

4. In options_intelligence.py, swap the feed:

       # At the top of options_intelligence.py:
       from tradier_options_feed import TradierOptionsFeed
       _TRADIER_FEED = TradierOptionsFeed()        # one shared instance

       # Inside OptionsIntelligence._get_chain():
       if _TRADIER_FEED.is_configured():
           return _TRADIER_FEED.get_chain(ticker, expiry)
       # ... existing yfinance fallback below

That is the only change needed. All math downstream is feed-agnostic.

WHAT TRADIER ADDS vs yfinance
──────────────────────────────
  yfinance (current):   Real chain data, ~15-20 min delayed, free
  Tradier (this file):  Real-time chain data, live quotes, greeks (delta/gamma/
                        theta/vega), true sweep detection via streaming endpoint

TRADIER ENDPOINTS USED
──────────────────────
  Expirations : GET /v1/markets/options/expirations
  Chain       : GET /v1/markets/options/chains  (greeks=true)
  Strikes     : GET /v1/markets/options/strikes
  Quote       : GET /v1/markets/quotes
  Streaming   : POST /v1/markets/events  (real-time, for sweep detection)

All endpoints require Bearer token in Authorization header.
Sandbox base: https://sandbox.tradier.com
Live base:    https://api.tradier.com
"""

import os
import time
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

# ── cache (same TTL policy as yfinance path) ──────────────────────────────
_CHAIN_CACHE:  Dict[str, dict] = {}
_EXPIRY_CACHE: Dict[str, dict] = {}
_CHAIN_TTL   = 60     # seconds — real-time feed, shorter TTL
_EXPIRY_TTL  = 300


def _cache_get(store: dict, key: str, ttl: float) -> Optional[dict]:
    entry = store.get(key)
    if entry and (time.time() - entry["_ts"]) < ttl:
        return entry["data"]
    return None


def _cache_set(store: dict, key: str, data) -> None:
    store[key] = {"data": data, "_ts": time.time()}


class TradierOptionsFeed:
    """
    Real-time options chain data from Tradier brokerage API.

    Returns data in the same dict structure as OptionsIntelligence._get_chain()
    so it is a drop-in replacement with zero downstream changes.

    The chain dict returned by get_chain() contains two pandas DataFrames
    (calls, puts) with the same columns as yfinance plus extra Tradier fields:
      delta, gamma, theta, vega, rho, mid_iv, bid_iv, ask_iv

    Usage
    ─────
        feed = TradierOptionsFeed()
        if feed.is_configured():
            chain = feed.get_chain("AAPL", "2026-03-21")
            exps  = feed.get_expirations("AAPL")
    """

    LIVE_BASE    = "https://api.tradier.com"
    SANDBOX_BASE = "https://sandbox.tradier.com"

    def __init__(self):
        self._token     = os.environ.get("TRADIER_API_TOKEN", "").strip()
        self._sandbox   = os.environ.get("TRADIER_USE_SANDBOX", "false").lower() == "true"
        self._base      = self.SANDBOX_BASE if self._sandbox else self.LIVE_BASE
        self._configured = bool(self._token) and _REQUESTS_OK
        if self._configured:
            env = "SANDBOX" if self._sandbox else "LIVE"
            print(f"✅ Tradier Options Feed initialized ({env})")
        else:
            if not _REQUESTS_OK:
                print("⚠️  Tradier: 'requests' library not installed — pip install requests")
            else:
                print("⚠️  Tradier: TRADIER_API_TOKEN not set — using yfinance fallback")

    def is_configured(self) -> bool:
        return self._configured

    # ── HTTP helper ────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        if not self._configured:
            return None
        url = f"{self._base}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept":        "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, params=params or {}, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            print(f"⚠️  Tradier API error ({path}): {exc}")
            return None

    # ── expirations ────────────────────────────────────────────────────────

    def get_expirations(self, ticker: str) -> Tuple[str, ...]:
        """
        Return available expiration dates for ticker as a sorted tuple of
        ISO date strings, same format as yfinance ticker.options.
        """
        key = f"exp|{ticker.upper()}"
        cached = _cache_get(_EXPIRY_CACHE, key, _EXPIRY_TTL)
        if cached is not None:
            return tuple(cached)

        data = self._get("/v1/markets/options/expirations", {
            "symbol":          ticker.upper(),
            "includeAllRoots": "true",
            "strikes":         "false",
        })
        if not data:
            return ()
        try:
            dates = data.get("expirations", {}).get("date", [])
            if isinstance(dates, str):
                dates = [dates]
            sorted_dates = tuple(sorted(dates))
            _cache_set(_EXPIRY_CACHE, key, list(sorted_dates))
            return sorted_dates
        except Exception:
            return ()

    # ── options chain ──────────────────────────────────────────────────────

    def get_chain(self, ticker: str, expiry: str) -> Optional[dict]:
        """
        Return {"calls": DataFrame, "puts": DataFrame} for ticker/expiry.
        DataFrames include all yfinance columns plus Tradier greeks.
        Cached for _CHAIN_TTL seconds.
        """
        key = f"{ticker.upper()}|{expiry}"
        cached = _cache_get(_CHAIN_CACHE, key, _CHAIN_TTL)
        if cached is not None:
            return cached

        data = self._get("/v1/markets/options/chains", {
            "symbol":     ticker.upper(),
            "expiration": expiry,
            "greeks":     "true",
        })
        if not data:
            return None

        try:
            options = data.get("options", {}).get("option", [])
            if not options:
                return None
            if isinstance(options, dict):
                options = [options]

            import pandas as pd

            rows_calls = []
            rows_puts  = []

            for o in options:
                greeks = o.get("greeks") or {}
                row = {
                    # yfinance-compatible columns
                    "strike":          float(o.get("strike", 0)),
                    "lastPrice":       float(o.get("last", 0) or 0),
                    "bid":             float(o.get("bid",  0) or 0),
                    "ask":             float(o.get("ask",  0) or 0),
                    "volume":          int(o.get("volume", 0) or 0),
                    "openInterest":    int(o.get("open_interest", 0) or 0),
                    "impliedVolatility": float(o.get("mid_iv", 0) or
                                               greeks.get("mid_iv", 0) or 0),
                    "inTheMoney":      o.get("in_the_money", False),
                    # Tradier-only greeks
                    "delta":           float(greeks.get("delta", 0) or 0),
                    "gamma":           float(greeks.get("gamma", 0) or 0),
                    "theta":           float(greeks.get("theta", 0) or 0),
                    "vega":            float(greeks.get("vega",  0) or 0),
                    "rho":             float(greeks.get("rho",   0) or 0),
                    "bid_iv":          float(greeks.get("bid_iv", 0) or 0),
                    "ask_iv":          float(greeks.get("ask_iv", 0) or 0),
                    "contractSymbol":  o.get("symbol", ""),
                }
                if o.get("option_type", "").upper() == "CALL":
                    rows_calls.append(row)
                else:
                    rows_puts.append(row)

            result = {
                "calls": pd.DataFrame(rows_calls) if rows_calls else pd.DataFrame(),
                "puts":  pd.DataFrame(rows_puts)  if rows_puts  else pd.DataFrame(),
            }
            _cache_set(_CHAIN_CACHE, key, result)
            return result

        except Exception as exc:
            print(f"⚠️  Tradier chain parse error ({ticker}/{expiry}): {exc}")
            return None

    # ── real-time quote ────────────────────────────────────────────────────

    def get_quote(self, ticker: str) -> Optional[dict]:
        """
        Real-time equity quote from Tradier.
        Returns dict with: last, bid, ask, volume, change_pct.
        """
        data = self._get("/v1/markets/quotes", {
            "symbols": ticker.upper(),
            "greeks":  "false",
        })
        if not data:
            return None
        try:
            q = data.get("quotes", {}).get("quote", {})
            if isinstance(q, list):
                q = q[0]
            return {
                "ticker":     ticker.upper(),
                "last":       float(q.get("last", 0) or 0),
                "bid":        float(q.get("bid",  0) or 0),
                "ask":        float(q.get("ask",  0) or 0),
                "volume":     int(q.get("volume", 0) or 0),
                "change_pct": float(q.get("change_percentage", 0) or 0),
            }
        except Exception:
            return None

    # ── sweep detection (streaming stub) ──────────────────────────────────

    def stream_option_sweeps(self, tickers: List[str], callback) -> None:
        """
        Subscribe to Tradier streaming market events for option sweep detection.

        This is a STUB — implement with a background thread using
        Tradier's POST /v1/markets/events endpoint (chunked HTTP).

        When implemented, callback receives dicts:
            {
              "ticker":      str,
              "strike":      float,
              "expiry":      str,
              "side":        "CALL" | "PUT",
              "size":        int,
              "price":       float,
              "notional":    float,
              "is_sweep":    bool,   # aggressive cross-exchange order
              "timestamp":   str,
            }

        Tradier streaming docs:
        https://documentation.tradier.com/brokerage-api/markets/streaming

        Example implementation skeleton:
        ─────────────────────────────────────────────────────────────
        import threading, requests, json

        def _stream_worker():
            session_resp = requests.post(
                f"{self._base}/v1/markets/events/session",
                headers={"Authorization": f"Bearer {self._token}",
                         "Accept": "application/json"},
            )
            session_id = session_resp.json()["stream"]["sessionid"]

            with requests.post(
                f"{self._base}/v1/markets/events",
                headers={"Authorization": f"Bearer {self._token}",
                         "Accept": "application/json"},
                stream=True,
                json={"sessionid": session_id,
                      "symbols": tickers,
                      "filter": ["option"]},
            ) as resp:
                for line in resp.iter_lines():
                    if line:
                        event = json.loads(line)
                        # parse, detect sweeps, call callback(event)

        threading.Thread(target=_stream_worker, daemon=True).start()
        ─────────────────────────────────────────────────────────────
        """
        if not self._configured:
            print("⚠️  Tradier sweep streaming: not configured (no API token)")
            return
        # TODO: implement streaming worker when account is verified
        print("ℹ️  Tradier sweep streaming: stub ready — implement _stream_worker()")

    # ── status ─────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "configured":  self._configured,
            "sandbox":     self._sandbox,
            "base_url":    self._base,
            "token_set":   bool(self._token),
        }


# ── singleton ──────────────────────────────────────────────────────────────
# Import and use this one instance everywhere.
# It is safe to import even if the token is not yet set.
tradier_feed = TradierOptionsFeed()
