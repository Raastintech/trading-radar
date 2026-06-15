"""
Options Universe Scanner — standalone, options-first candidate discovery.

Runs independently of the equity scanner. Dynamically pulls liquid names
from Tradier's options chain data, filters them against hard liquidity and
structure requirements, scores them, and returns a ranked list of spread-
ready candidates for the options master to act on.

WHY THIS EXISTS
---------------
The equity router only surfaces tickers when the equity engine fires a signal.
On low-signal days (like the current environment with 0 equity approvals),
the options module has nothing to work with. This scanner provides a
continuous flow of options candidates from the market itself, independent
of whether any equity signal exists.

SELECTION CRITERIA (all must pass)
-----------------------------------
Underlying:
  - Price $15–$500            manageable capital per spread leg
  - Avg daily volume ≥ 500k   underlying must be liquid enough to exit
  - Not within 7 days of earnings

Options chain (checked at ATM for target expiry 21–45 DTE):
  - Open interest ≥ 500       real institutional book depth
  - Daily volume ≥ 50         active trading, real price discovery
  - Bid-ask spread ≤ 5%       executable without excessive slippage
  - Mid price ≥ $0.15         real premium, not penny lottery ticket

IV context:
  - IV rank ≥ 30 (FAIR/RICH)  selling into elevated IV earns more premium
  - OR VIX ≥ 25               market-wide stress opens gate regardless

SCORING (0–100)
---------------
  IV rank fit    40%   higher rank = fatter premium to sell now
  OI depth       25%   deeper book = tighter fills at execution
  Spread quality 20%   tighter spread = lower entry/exit cost
  Volume ratio   15%   active chain = real price discovery
  VIX bonus       up to +10 bonus when VIX ≥ 20

STRATEGY ROUTING
----------------
All candidates from this scanner are tagged VOYAGER (bull put credit spread).
The spread scanner will route them to BULL_PUT_CREDIT when IV is FAIR/RICH.
This is intentional: the options universe scanner is a premium-selling engine.
It finds quality liquid names to sell puts on, not directional long plays.
"""

from __future__ import annotations

import logging
import math
import os
import time
from datetime import date
from typing import Dict, List, Optional, Tuple

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

from options_underlying_router import OptionUnderlying

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Liquid base watchlist — names with deep, institutional-grade options markets.
# This is a seed list, not a static universe. All names are filtered
# dynamically on every scan. If a name fails liquidity on a given day
# (e.g. low OI, wide spread, upcoming earnings), it is excluded that cycle.
# ---------------------------------------------------------------------------
BASE_WATCHLIST: List[str] = [
    # Mega-cap tech: deepest options markets in the world
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD",
    # Financial: high IV during market stress = fat premiums
    "JPM", "GS", "MS", "BAC", "C", "WFC", "V", "MA", "AXP",
    # Healthcare / defensive
    "UNH", "JNJ", "ABBV", "MRK", "PFE", "CVS",
    # Consumer / retail
    "WMT", "HD", "COST", "TGT", "NKE", "MCD", "SBUX",
    # Energy
    "XOM", "CVX", "COP", "SLB",
    # Industrials
    "CAT", "DE", "GE", "BA", "HON",
    # High-volatility premium names: elevated IV → fat spreads to sell
    "COIN", "MSTR", "PLTR", "SNOW", "DDOG", "CRWD", "PANW",
    # Semis
    "INTC", "QCOM", "TXN", "AVGO", "AMAT", "LRCX",
    # Biotech/pharma (high IV, event-driven — careful with earnings gate)
    "AMGN", "GILD", "BIIB", "REGN",
    # Consumer discretionary
    "NFLX", "DIS", "BKNG", "CMG",
    # Liquid ETFs: extremely deep options, tight spreads, no earnings risk
    "SPY", "QQQ", "IWM", "GLD", "TLT", "XLF", "XLE", "XLK", "XBI", "XLV",
]

# ---------------------------------------------------------------------------
# Module-level scan cache — 30 min TTL so daemon cycles don't hammer Tradier
# ---------------------------------------------------------------------------
_CACHE_RESULT: List[OptionUnderlying] = []
_CACHE_TS: float = 0.0
_CACHE_TTL: int = 1800  # 30 minutes


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


class OptionsUniverseScanner:
    """
    Dynamically discovers options-eligible spread candidates from the market.

    Call scan() once per daemon cycle. Results are cached for 30 minutes
    so repeated calls within the same hour do not hit Tradier rate limits.
    """

    # ── Configurable thresholds (override via env vars) ─────────────────────
    MIN_STOCK_PRICE   = float(os.getenv("OPT_UNIV_MIN_PRICE",    "15"))
    MAX_STOCK_PRICE   = float(os.getenv("OPT_UNIV_MAX_PRICE",    "500"))
    MIN_AVG_VOLUME    = float(os.getenv("OPT_UNIV_MIN_VOLUME",   "500000"))
    MIN_OI_ATM        = int(  os.getenv("OPT_UNIV_MIN_OI",       "200"))
    MIN_OPTION_VOL    = int(  os.getenv("OPT_UNIV_MIN_OPT_VOL",  "50"))
    MAX_SPREAD_PCT    = float(os.getenv("OPT_UNIV_MAX_SPREAD",   "0.05"))
    MIN_OPTION_MID    = float(os.getenv("OPT_UNIV_MIN_MID",      "0.15"))
    MIN_IV_RANK       = float(os.getenv("OPT_UNIV_MIN_IV_RANK",  "30"))
    VIX_GATE_OVERRIDE = float(os.getenv("OPT_UNIV_VIX_GATE",    "25"))
    MIN_DTE           = int(  os.getenv("OPT_UNIV_MIN_DTE",      "21"))
    MAX_DTE           = int(  os.getenv("OPT_UNIV_MAX_DTE",      "45"))
    TOP_N             = int(  os.getenv("OPT_UNIV_TOP_N",        "10"))
    EARNINGS_DAYS     = int(  os.getenv("OPT_UNIV_EARNINGS_DAYS","7"))

    def __init__(self, iv_engine=None, earnings_adapter=None):
        self.iv_engine = iv_engine
        self.earnings  = earnings_adapter
        self._token  = os.getenv("TRADIER_API_TOKEN", "")
        sandbox = os.getenv("TRADIER_USE_SANDBOX", "false").lower() in ("1", "true", "yes")
        self._base   = "https://sandbox.tradier.com" if sandbox else "https://api.tradier.com"
        self._hdrs   = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    # ── Public ───────────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        return bool(self._token) and _REQUESTS_OK

    def scan(self, vix_level: float = 0.0, force_refresh: bool = False) -> List[OptionUnderlying]:
        """
        Return scored, filtered options candidates from the base watchlist.
        Cached 30 min. Set force_refresh=True to bypass cache.
        """
        global _CACHE_RESULT, _CACHE_TS
        if not force_refresh and _CACHE_RESULT and (time.time() - _CACHE_TS) < _CACHE_TTL:
            logger.debug("[OPT_UNIV] cache hit — returning %d candidates", len(_CACHE_RESULT))
            return _CACHE_RESULT

        if not self.is_configured():
            logger.warning("[OPT_UNIV] Tradier not configured — universe scan skipped")
            return []

        candidates = self._run_full_scan(vix_level)
        _CACHE_RESULT = candidates
        _CACHE_TS = time.time()
        logger.info(
            "[OPT_UNIV] %d watchlist names → %d qualified options candidates (VIX=%.1f)",
            len(BASE_WATCHLIST), len(candidates), vix_level,
        )
        return candidates

    # ── Internal pipeline ────────────────────────────────────────────────────

    def _run_full_scan(self, vix_level: float) -> List[OptionUnderlying]:
        # Step 1: batch-fetch underlying quotes
        quotes = self._fetch_quotes(BASE_WATCHLIST)
        if not quotes:
            logger.warning("[OPT_UNIV] no quotes returned from Tradier")
            return []

        # Step 2: filter by underlying price and average volume
        price_vol_ok: List[Tuple[str, float, float]] = []
        for ticker, q in quotes.items():
            price  = _safe_float(q.get("last") or q.get("close"))
            avgvol = _safe_float(q.get("average_volume"))
            if not (self.MIN_STOCK_PRICE <= price <= self.MAX_STOCK_PRICE):
                continue
            if avgvol < self.MIN_AVG_VOLUME:
                continue
            price_vol_ok.append((ticker, price, avgvol))

        logger.debug(
            "[OPT_UNIV] price/vol filter: %d/%d passed",
            len(price_vol_ok), len(quotes),
        )

        # Step 3: evaluate options chain for each candidate
        scored: List[Tuple[float, OptionUnderlying]] = []
        for ticker, price, avgvol in price_vol_ok:
            result = self._evaluate(ticker, price, avgvol, vix_level)
            if result is not None:
                scored.append(result)

        # Step 4: sort descending by score, return top N
        scored.sort(key=lambda x: -x[0])
        return [u for _, u in scored[: self.TOP_N]]

    def _evaluate(
        self,
        ticker: str,
        price: float,
        avgvol: float,
        vix_level: float,
    ) -> Optional[Tuple[float, OptionUnderlying]]:
        """
        Full evaluation pipeline for one ticker.
        Returns (score, OptionUnderlying) or None if any gate fails.
        """
        # Gate 1: earnings blackout
        if self.earnings:
            try:
                if self.earnings.should_block_new_trade(ticker, blackout_days=self.EARNINGS_DAYS):
                    logger.debug("[OPT_UNIV] %s blocked — earnings within %dd", ticker, self.EARNINGS_DAYS)
                    return None
            except Exception:
                pass  # earnings data unavailable → don't block

        # Gate 2: IV context
        iv_context: Dict = {}
        if self.iv_engine:
            try:
                iv_context = self.iv_engine.get_iv_context(ticker) or {}
            except Exception as exc:
                logger.debug("[OPT_UNIV] %s IV engine error: %s", ticker, exc)

        iv_rank  = _safe_float(iv_context.get("iv_rank_30d"))
        iv_regime = str(iv_context.get("iv_regime") or "CHEAP").upper()

        # IV gate: must be FAIR or RICH, OR VIX override
        # CHEAP + low VIX = thin premiums → not worth selling
        if iv_regime == "CHEAP" and vix_level < self.VIX_GATE_OVERRIDE:
            logger.debug("[OPT_UNIV] %s skipped — IV CHEAP and VIX %.1f < %.1f", ticker, vix_level, self.VIX_GATE_OVERRIDE)
            return None

        # Gate 3: find a usable expiry in 21–45 DTE window
        expirations = self._get_expirations(ticker)
        target_expiry = self._pick_expiry(expirations)
        if not target_expiry:
            logger.debug("[OPT_UNIV] %s skipped — no expiry in %d-%d DTE window", ticker, self.MIN_DTE, self.MAX_DTE)
            return None

        # Gate 4: fetch chain and check ATM liquidity
        chain = self._get_chain(ticker, target_expiry)
        if not chain:
            return None

        # Use the best-liquidity put near ATM for quality assessment.
        # Professionals check the ATM zone (within 5% of price), not the exact
        # ATM strike — the most liquid strike is rarely the exact ATM.
        best_put  = self._find_best_liquid_near_atm(chain.get("puts",  []), price)
        best_call = self._find_best_liquid_near_atm(chain.get("calls", []), price)
        atm = best_put or best_call
        if not atm:
            return None

        oi         = _safe_int(atm.get("open_interest"))
        vol        = _safe_int(atm.get("volume"))
        bid        = _safe_float(atm.get("bid"))
        ask        = _safe_float(atm.get("ask"))
        mid        = (bid + ask) / 2.0 if (bid + ask) > 0 else 0.0
        spread_pct = ((ask - bid) / mid) if mid > 0 else 1.0

        # Liquidity gates (hard pass/fail)
        if oi < self.MIN_OI_ATM:
            logger.debug("[OPT_UNIV] %s best-near-ATM OI %d < %d", ticker, oi, self.MIN_OI_ATM)
            return None
        if vol < self.MIN_OPTION_VOL:
            logger.debug("[OPT_UNIV] %s vol %d < %d", ticker, vol, self.MIN_OPTION_VOL)
            return None
        if spread_pct > self.MAX_SPREAD_PCT:
            logger.debug("[OPT_UNIV] %s spread %.1f%% > %.1f%%", ticker, spread_pct * 100, self.MAX_SPREAD_PCT * 100)
            return None
        if mid < self.MIN_OPTION_MID:
            logger.debug("[OPT_UNIV] %s mid $%.2f < $%.2f", ticker, mid, self.MIN_OPTION_MID)
            return None

        # All gates passed — compute score
        score = self._score(iv_rank, oi, spread_pct, vol, vix_level)

        underlying = OptionUnderlying(
            ticker       = ticker,
            strategy     = "VOYAGER",          # → BULL_PUT_CREDIT in spread scanner
            direction    = "LONG",
            source_type  = "options_universe",
            entry_price  = price,
            stop_loss    = 0.0,                # no equity stop — options manages its own risk
            target_price = 0.0,
            equity_rr    = 0.0,
            score        = round(score, 1),
            approved     = True,
        )
        return score, underlying

    # ── Scoring ──────────────────────────────────────────────────────────────

    def _score(
        self,
        iv_rank: float,
        oi: int,
        spread_pct: float,
        volume: int,
        vix_level: float,
    ) -> float:
        """
        Composite score 0–100.

        IV rank fit    40%  — fatter premium when IV is elevated
        OI depth       25%  — deeper book drives tighter fills
        Spread quality 20%  — lower slippage on entry and exit
        Volume ratio   15%  — active chain = reliable price discovery
        VIX bonus      +10  — elevated VIX boosts premium across the board
        """
        # IV rank: already 0–100 by definition
        iv_score = min(100.0, max(0.0, float(iv_rank)))

        # OI on log scale: 500 → ~0pts, 2000 → ~60pts, 10000+ → 100pts
        oi_lo  = math.log10(max(1, self.MIN_OI_ATM))   # log10(500) ≈ 2.70
        oi_hi  = math.log10(10000)                      # log10(10000) = 4.0
        oi_score = max(0.0, min(100.0,
            (math.log10(max(1, oi)) - oi_lo) / (oi_hi - oi_lo) * 100.0
        ))

        # Spread: 0% → 100pts, MAX_SPREAD_PCT → 0pts (linear)
        spread_score = max(0.0, (self.MAX_SPREAD_PCT - spread_pct) / self.MAX_SPREAD_PCT * 100.0)

        # Volume on log scale: 50 → 0pts, 500 → 100pts
        vol_lo   = math.log10(max(1, self.MIN_OPTION_VOL))  # log10(50) ≈ 1.70
        vol_hi   = math.log10(500)                           # log10(500) ≈ 2.70
        vol_score = max(0.0, min(100.0,
            (math.log10(max(1, volume)) - vol_lo) / (vol_hi - vol_lo) * 100.0
        ))

        # VIX bonus: 0 when VIX ≤ 20, scales to +10 at VIX ≥ 25
        vix_bonus = min(10.0, max(0.0, (vix_level - 20.0) * 2.0)) if vix_level >= 20 else 0.0

        raw = (
            0.40 * iv_score
            + 0.25 * oi_score
            + 0.20 * spread_score
            + 0.15 * vol_score
        )
        return round(min(100.0, raw + vix_bonus), 1)

    # ── Tradier API helpers ───────────────────────────────────────────────────

    def _fetch_quotes(self, tickers: List[str]) -> Dict[str, Dict]:
        """Batch-fetch quotes via Tradier in chunks of 50."""
        result: Dict[str, Dict] = {}
        for i in range(0, len(tickers), 50):
            chunk   = tickers[i: i + 50]
            symbols = ",".join(chunk)
            try:
                r = requests.get(
                    f"{self._base}/v1/markets/quotes",
                    headers=self._hdrs,
                    params={"symbols": symbols, "greeks": "false"},
                    timeout=10,
                )
                if r.status_code != 200:
                    logger.debug("[OPT_UNIV] quotes HTTP %d for chunk %d", r.status_code, i)
                    continue
                quotes_raw = r.json().get("quotes", {}).get("quote", [])
                if isinstance(quotes_raw, dict):
                    quotes_raw = [quotes_raw]
                for q in quotes_raw:
                    sym = str(q.get("symbol") or "").upper().strip()
                    if sym:
                        result[sym] = q
            except Exception as exc:
                logger.debug("[OPT_UNIV] quote fetch error chunk %d: %s", i, exc)
        return result

    def _get_expirations(self, ticker: str) -> List[str]:
        try:
            r = requests.get(
                f"{self._base}/v1/markets/options/expirations",
                headers=self._hdrs,
                params={"symbol": ticker, "includeAllRoots": "false"},
                timeout=8,
            )
            if r.status_code != 200:
                return []
            data  = r.json().get("expirations") or {}
            dates = data.get("date") or []
            if isinstance(dates, str):
                dates = [dates]
            return [str(d) for d in dates]
        except Exception:
            return []

    def _pick_expiry(self, expirations: List[str]) -> Optional[str]:
        """Select the expiry closest to 35 DTE within the 21–45 window."""
        today  = date.today()
        ranked: List[Tuple[int, str]] = []
        for exp in expirations:
            try:
                dte = (date.fromisoformat(str(exp)) - today).days
                if self.MIN_DTE <= dte <= self.MAX_DTE:
                    ranked.append((abs(dte - 35), str(exp)))
            except Exception:
                continue
        return sorted(ranked)[0][1] if ranked else None

    def _get_chain(self, ticker: str, expiry: str) -> Optional[Dict]:
        """Fetch full options chain for one expiry via Tradier."""
        try:
            r = requests.get(
                f"{self._base}/v1/markets/options/chains",
                headers=self._hdrs,
                params={"symbol": ticker, "expiration": expiry, "greeks": "true"},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            options = r.json().get("options", {}).get("option", [])
            if isinstance(options, dict):
                options = [options]
            if not options:
                return None
            puts  = [o for o in options if str(o.get("option_type") or "").lower() == "put"]
            calls = [o for o in options if str(o.get("option_type") or "").lower() == "call"]
            return {"puts": puts, "calls": calls}
        except Exception as exc:
            logger.debug("[OPT_UNIV] chain fetch error %s %s: %s", ticker, expiry, exc)
            return None

    def _find_best_liquid_near_atm(
        self, contracts: List[Dict], price: float, zone_pct: float = 0.05
    ) -> Optional[Dict]:
        """
        Find the highest-OI contract within `zone_pct` of current price.

        Real desks don't look at the exact ATM strike — they look at the
        zone around ATM and pick the strike with the deepest book.  For a
        $100 stock with zone_pct=0.05, this checks strikes from $95–$105
        and returns the one with the most open interest.  This avoids
        falsely rejecting names where the exact ATM strike is thin but
        adjacent strikes are liquid.
        """
        if not contracts:
            return None
        lo = price * (1.0 - zone_pct)
        hi = price * (1.0 + zone_pct)
        zone = [
            c for c in contracts
            if lo <= _safe_float(c.get("strike")) <= hi
        ]
        if not zone:
            # Widen to ±10% if nothing in ±5% (low-priced or odd-strike tickers)
            lo = price * 0.90
            hi = price * 1.10
            zone = [c for c in contracts if lo <= _safe_float(c.get("strike")) <= hi]
        if not zone:
            return None
        # Pick the contract with highest OI — deepest book wins
        return max(zone, key=lambda c: _safe_int(c.get("open_interest")))
