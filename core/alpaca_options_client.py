"""
core/alpaca_options_client.py — Alpaca options chain feed.

Drop-in replacement for ``legacy/tradier_options_feed.py`` exposing the
same 3-method contract so the chain wrapper in
``core/options_feed_chain.py`` can rotate between feeds:

    is_configured() -> bool
    get_expirations(ticker)  -> tuple[str, ...]     # ISO date strings
    get_chain(ticker, expiry) -> Optional[dict]     # {"calls": DataFrame, "puts": DataFrame}

Endpoints (Alpaca Pro+ market-data access required):

  contracts (trading API metadata)
      GET {ALPACA_BASE_URL}/v2/options/contracts
          ?underlying_symbols=TICKER
          &status=active
          &expiration_date_gte=YYYY-MM-DD
          &expiration_date_lte=YYYY-MM-DD
          &limit=10000

  chain snapshots (market-data API)
      GET {ALPACA_DATA_URL}/v1beta1/options/snapshots/{TICKER}
          ?feed=opra
          [&expiration_date=YYYY-MM-DD]

Both endpoints authenticate with the existing
``APCA-API-KEY-ID`` / ``APCA-API-SECRET-KEY`` headers — no new credential
is introduced. The client mirrors Tradier's row schema so downstream
code (``research/stock_lens_runner.py:_per_expiry_features``) is feed-
agnostic.

Doctrine:

  - Data-only. This module does not place orders, does not call the
    trading endpoints, does not import ``execution.*`` or
    ``decision_logger``, and does not touch the live-capital gate.
  - Fail-soft. Every network or parse error returns the same empty
    value the caller would have seen from Tradier — never raises.
  - Same row schema as Tradier. ``_per_expiry_features`` requires
    ``strike, bid, ask, volume, openInterest, impliedVolatility,
    inTheMoney``; we also fill the Tradier-extra fields (greeks,
    lastPrice, contractSymbol, bid_iv/ask_iv) so any consumer that
    reads them sees a populated value rather than KeyError.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import requests
    _REQUESTS_OK = True
except ImportError:  # pragma: no cover — requests is a test/runtime dep
    _REQUESTS_OK = False

try:
    import pandas as pd
    _PANDAS_OK = True
except ImportError:  # pragma: no cover
    _PANDAS_OK = False


# ── caching (mirrors Tradier TTL policy) ──────────────────────────────

_CHAIN_CACHE:  Dict[str, Dict[str, Any]] = {}
_EXPIRY_CACHE: Dict[str, Dict[str, Any]] = {}
_CHAIN_TTL  = 60
_EXPIRY_TTL = 300


def _cache_get(store: Dict[str, Dict[str, Any]], key: str, ttl: float) -> Any:
    entry = store.get(key)
    if entry and (time.time() - entry["_ts"]) < ttl:
        return entry["data"]
    return None


def _cache_set(store: Dict[str, Dict[str, Any]], key: str, data: Any) -> None:
    store[key] = {"data": data, "_ts": time.time()}


# ── OCC symbol parser ─────────────────────────────────────────────────

# OCC option symbol: ROOT (1-6 alphanumerics) + YYMMDD + C|P + 8-digit
# strike in thousandths (e.g. AAPL250620C00200000 → AAPL, 2025-06-20,
# CALL, strike 200.00). Some roots are padded with trailing whitespace
# in canonical OPRA format; the API returns the trimmed form.
_OCC_RE = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<ymd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


def parse_occ_symbol(sym: str) -> Optional[Dict[str, Any]]:
    """Return {"root","expiration","option_type","strike"} or None."""
    if not isinstance(sym, str):
        return None
    m = _OCC_RE.match(sym.strip().upper())
    if not m:
        return None
    try:
        ymd = m.group("ymd")
        exp = date(2000 + int(ymd[0:2]), int(ymd[2:4]), int(ymd[4:6]))
    except ValueError:
        return None
    return {
        "root":        m.group("root"),
        "expiration":  exp.isoformat(),
        "option_type": "CALL" if m.group("cp") == "C" else "PUT",
        "strike":      int(m.group("strike")) / 1000.0,
    }


# ── Client ────────────────────────────────────────────────────────────

DEFAULT_BASE_URL  = "https://api.alpaca.markets"
DEFAULT_DATA_URL  = "https://data.alpaca.markets"
DEFAULT_CHAIN_DTE = 120     # cap how far out we ask for contracts


class AlpacaOptionsFeed:
    """Minimal Alpaca options client with Tradier-compatible interface."""

    NAME = "alpaca"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        data_url: Optional[str] = None,
        max_dte: int = DEFAULT_CHAIN_DTE,
        timeout: float = 10.0,
    ):
        """Construct.

        Reads ``cfg`` lazily so tests can construct the feed without
        booting core.config (which raises on missing creds at import
        time). If ``api_key``/``secret_key`` are not provided we read
        ``cfg.ALPACA_API_KEY/SECRET_KEY``.
        """
        if api_key is None or secret_key is None:
            try:
                import core.config as cfg  # noqa: WPS433
                api_key    = api_key    or cfg.ALPACA_API_KEY
                secret_key = secret_key or cfg.ALPACA_SECRET_KEY
                base_url   = base_url   or cfg.ALPACA_BASE_URL
                data_url   = data_url   or cfg.ALPACA_DATA_URL
            except Exception as exc:
                logger.debug("Alpaca options feed: cfg unavailable: %s", exc)

        self._key      = (api_key or "").strip()
        self._secret   = (secret_key or "").strip()
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._data_url = (data_url or DEFAULT_DATA_URL).rstrip("/")
        self._timeout  = float(timeout)
        self._max_dte  = int(max_dte)
        self._configured = bool(self._key and self._secret
                                and _REQUESTS_OK and _PANDAS_OK)

    # ── status ────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        return self._configured

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID":     self._key,
            "APCA-API-SECRET-KEY": self._secret,
            "Accept":              "application/json",
        }

    # ── HTTP helpers ──────────────────────────────────────────────────

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if not self._configured:
            return None
        try:
            resp = requests.get(
                url, headers=self._headers(),
                params=params or {}, timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.info("Alpaca options GET failed (%s): %s", url, exc)
            return None

    # ── expirations ───────────────────────────────────────────────────

    def get_expirations(self, ticker: str) -> Tuple[str, ...]:
        """Return sorted ISO expiration dates for ``ticker`` within
        ``max_dte`` calendar days. Cached for 5 minutes."""
        sym = (ticker or "").strip().upper()
        if not sym:
            return ()
        key = f"exp|{sym}"
        cached = _cache_get(_EXPIRY_CACHE, key, _EXPIRY_TTL)
        if cached is not None:
            return tuple(cached)

        today = date.today()
        end   = today + timedelta(days=self._max_dte)
        url   = f"{self._base_url}/v2/options/contracts"
        params = {
            "underlying_symbols":  sym,
            "status":              "active",
            "expiration_date_gte": today.isoformat(),
            "expiration_date_lte": end.isoformat(),
            "limit":               10000,
        }
        seen: set = set()
        page_token: Optional[str] = None
        # Cap pages defensively — Alpaca returns ≤ ``limit`` per page;
        # 10 pages × 10k = 100k contracts is well past anything sane.
        for _ in range(10):
            p = dict(params)
            if page_token:
                p["page_token"] = page_token
            data = self._get(url, p)
            if not data:
                break
            contracts = data.get("option_contracts") or []
            for c in contracts:
                exp = c.get("expiration_date")
                if isinstance(exp, str) and len(exp) >= 10:
                    seen.add(exp[:10])
            page_token = data.get("next_page_token")
            if not page_token:
                break

        result = tuple(sorted(seen))
        _cache_set(_EXPIRY_CACHE, key, list(result))
        return result

    # ── contract metadata (open_interest, close_price) ───────────────

    def _fetch_contract_metadata(
        self, ticker: str, expiry: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Return ``{occ_symbol: {open_interest, close_price}}`` for the
        given ticker/expiry via the /v2/options/contracts trading API
        endpoint. Open interest is *not* present on the snapshot
        endpoint — without this lookup every row's OI would degrade to 0
        and ``_filter_liquid_strikes`` would discard all but the highest-
        volume strikes (the bug that produced ``OPTIONS_NO_EDGE`` on
        TSLA-class tickers)."""
        if not self._configured or not ticker or not expiry:
            return {}
        url = f"{self._base_url}/v2/options/contracts"
        out: Dict[str, Dict[str, Any]] = {}
        page_token: Optional[str] = None
        # Cap pages defensively: a single expiry rarely exceeds 5k strikes.
        for _ in range(10):
            params: Dict[str, Any] = {
                "underlying_symbols":  ticker,
                "status":              "active",
                "expiration_date_gte": expiry,
                "expiration_date_lte": expiry,
                "limit":               10000,
            }
            if page_token:
                params["page_token"] = page_token
            data = self._get(url, params)
            if not data:
                break
            for c in data.get("option_contracts") or []:
                sym = c.get("symbol")
                if not isinstance(sym, str):
                    continue
                # Alpaca returns numeric fields as JSON strings; coerce.
                out[sym] = {
                    "open_interest":      _i(c.get("open_interest")),
                    "open_interest_date": c.get("open_interest_date"),
                    "close_price":        _f(c.get("close_price")),
                    "close_price_date":   c.get("close_price_date"),
                }
            page_token = data.get("next_page_token")
            if not page_token:
                break
        return out

    # ── chain ─────────────────────────────────────────────────────────

    def get_chain(self, ticker: str, expiry: str) -> Optional[Dict[str, Any]]:
        """Return ``{"calls": DataFrame, "puts": DataFrame}`` for the
        given ticker/expiry. ``None`` on missing data so the caller's
        OPTIONS_MISSING path triggers cleanly.

        Two endpoints are merged:
          - /v1beta1/options/snapshots/{underlying}  → quotes, trades, bars
          - /v2/options/contracts                    → open_interest, close_price

        Without the contracts merge every row's openInterest would be 0
        and the downstream liquidity filter would strip the chain to a
        handful of high-volume strikes — degrading even very-liquid
        names (TSLA, SPY, AAPL) to OPTIONS_NO_EDGE."""
        sym = (ticker or "").strip().upper()
        exp = (expiry or "").strip()
        if not sym or not exp:
            return None
        key = f"{sym}|{exp}"
        cached = _cache_get(_CHAIN_CACHE, key, _CHAIN_TTL)
        if cached is not None:
            return cached

        url = f"{self._data_url}/v1beta1/options/snapshots/{sym}"
        snapshots: Dict[str, Any] = {}
        page_token: Optional[str] = None
        for _ in range(10):
            params: Dict[str, Any] = {
                "feed":            "opra",
                "limit":           1000,
                "expiration_date": exp,
            }
            if page_token:
                params["page_token"] = page_token
            data = self._get(url, params)
            if not data:
                break
            page_snaps = data.get("snapshots") or {}
            if isinstance(page_snaps, dict):
                snapshots.update(page_snaps)
            page_token = data.get("next_page_token")
            if not page_token:
                break

        if not snapshots:
            return None

        # Merge contracts metadata. If this call fails the chain still
        # builds — OI just falls back to 0 (the pre-patch behavior).
        contract_meta = self._fetch_contract_metadata(sym, exp)

        rows_calls: List[Dict[str, Any]] = []
        rows_puts:  List[Dict[str, Any]] = []
        for occ_sym, snap in snapshots.items():
            parsed = parse_occ_symbol(occ_sym)
            if parsed is None or parsed["expiration"] != exp:
                continue
            row = _snapshot_to_row(
                occ_sym, parsed, snap,
                contract_meta=contract_meta.get(occ_sym),
            )
            if parsed["option_type"] == "CALL":
                rows_calls.append(row)
            else:
                rows_puts.append(row)

        if not rows_calls and not rows_puts:
            return None

        result = {
            "calls": pd.DataFrame(rows_calls) if rows_calls else pd.DataFrame(),
            "puts":  pd.DataFrame(rows_puts)  if rows_puts  else pd.DataFrame(),
        }
        _cache_set(_CHAIN_CACHE, key, result)
        return result

    # ── status dict for diagnostics ───────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "name":       self.NAME,
            "configured": self._configured,
            "base_url":   self._base_url,
            "data_url":   self._data_url,
            "max_dte":    self._max_dte,
            "key_set":    bool(self._key),
            "requests":   _REQUESTS_OK,
            "pandas":     _PANDAS_OK,
        }


# ── snapshot → row adapter ────────────────────────────────────────────

def _f(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _snapshot_to_row(
    occ_sym: str,
    parsed: Dict[str, Any],
    snap: Dict[str, Any],
    *,
    contract_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a Tradier-shaped row dict from one Alpaca snapshot, plus
    contract metadata (open_interest, close_price) when supplied.

    Required columns (consumed by ``_per_expiry_features``):
      strike, bid, ask, volume, openInterest, impliedVolatility, inTheMoney
    Compatibility columns (consumed by other paths / safe to populate):
      lastPrice, delta, gamma, theta, vega, rho, bid_iv, ask_iv, contractSymbol
    """
    snap   = snap or {}
    meta   = contract_meta or {}
    quote  = snap.get("latestQuote") or {}
    trade  = snap.get("latestTrade") or {}
    daily  = snap.get("dailyBar") or snap.get("minuteBar") or {}
    greeks = snap.get("greeks") or {}

    # Alpaca's daily bar uses "v" for volume; the snapshot does not
    # carry OI at all, so OI comes from the contracts endpoint (passed
    # in via ``contract_meta``). Snapshot-level OI keys are kept as
    # defensive fallbacks for future API changes.
    volume = _i(daily.get("v") or daily.get("volume") or snap.get("volume"))
    open_interest = _i(
        meta.get("open_interest")
        or snap.get("openInterest")
        or snap.get("open_interest")
        or daily.get("openInterest")
    )

    iv = _f(snap.get("impliedVolatility") or greeks.get("iv"))

    bid = _f(quote.get("bp") or quote.get("bid"))
    ask = _f(quote.get("ap") or quote.get("ask"))
    # latestTrade.p is the most recent execution print; close_price from
    # the contracts endpoint is the previous session's close — used only
    # when no latestTrade exists (illiquid strike with no fills today).
    last = _f(trade.get("p")
              or trade.get("price")
              or snap.get("lastPrice")
              or meta.get("close_price"))

    # inTheMoney needs a spot reference. Alpaca doesn't return spot in
    # the chain snapshot; rather than guess, leave a sentinel boolean
    # the caller's ATM-IV fallback only uses when spot is missing. The
    # primary code path passes a spot price, so this value is rarely
    # touched — we conservatively report False to avoid biasing the
    # fallback toward any particular strike.
    return {
        "strike":            float(parsed["strike"]),
        "lastPrice":         last,
        "bid":               bid,
        "ask":               ask,
        "volume":            volume,
        "openInterest":      open_interest,
        "impliedVolatility": iv,
        "inTheMoney":        False,
        "delta":             _f(greeks.get("delta")),
        "gamma":             _f(greeks.get("gamma")),
        "theta":             _f(greeks.get("theta")),
        "vega":              _f(greeks.get("vega")),
        "rho":               _f(greeks.get("rho")),
        "bid_iv":            _f(greeks.get("bid_iv") or iv),
        "ask_iv":            _f(greeks.get("ask_iv") or iv),
        "contractSymbol":    occ_sym,
    }


# ── test/inspection helper ────────────────────────────────────────────

def _reset_cache_for_tests() -> None:
    """Clear module-level caches so unit tests are independent."""
    _CHAIN_CACHE.clear()
    _EXPIRY_CACHE.clear()
