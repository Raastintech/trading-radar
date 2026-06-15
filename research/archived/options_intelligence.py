"""
Options Intelligence — Real yfinance chain data.


Replaces the previous simulation-only implementation with live options chain
data from yfinance (15-20 min delayed, free tier).

Public interface is preserved so all callers continue to work unchanged.

When Tradier is configured, import tradier_options_feed and swap the
_get_chain() call — the rest of the math is feed-agnostic.

Key capabilities:
  - Real max pain from actual OI at every strike
  - Real implied-move from front-month ATM IV
  - Real IV skew (put premium over call)
  - Real volume/OI anomaly detection (fresh positioning)
  - Real put/call ratios (volume + OI)
  - Gamma score rebuilt from real chain signals
"""

import math
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

# yfinance removed. Options chains will be available via FMP Ultimate plan.
# Until then, all options calls return graceful no-data stubs so the rest
# of the strategy stack continues to function without options signals.
try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    yf = None  # type: ignore
    _HAS_YFINANCE = False

from alpaca_data import AlpacaDataFeed

# ── cache ──────────────────────────────────────────────────────────────────
_CHAIN_CACHE: Dict[str, dict] = {}
_CHAIN_CACHE_TTL = 300
_INFO_CACHE:  Dict[str, dict] = {}
_INFO_CACHE_TTL = 3600


def _cache_get(store: dict, key: str, ttl: float) -> Optional[dict]:
    entry = store.get(key)
    if entry and (time.time() - entry["_ts"]) < ttl:
        return entry["data"]
    return None


def _cache_set(store: dict, key: str, data) -> None:
    store[key] = {"data": data, "_ts": time.time()}


# ── helpers ────────────────────────────────────────────────────────────────

def _dte(expiry_str: str) -> int:
    """Calendar days to expiration (minimum 0)."""
    try:
        exp = date.fromisoformat(expiry_str)
        return max(0, (exp - date.today()).days)
    except Exception:
        return 0


def _front_month(expirations: Tuple[str, ...], min_dte: int = 7) -> Optional[str]:
    """
    Return the nearest expiration with at least min_dte calendar days remaining.
    Falls back to the nearest available if all are within min_dte.
    """
    eligible = [e for e in expirations if _dte(e) >= min_dte]
    return eligible[0] if eligible else (expirations[0] if expirations else None)


def _safe_float(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


class OptionsIntelligence:
    """
    Real-data options intelligence.

    Chain data: yfinance (delayed ~15 min, free).
    Swap _get_chain() for TradierOptionsFeed once API key is configured.
    """

    def __init__(self):
        self.data_feed = AlpacaDataFeed()
        print("✅ Options Intelligence initialized (yfinance real chain data)")

    # ── chain fetch ────────────────────────────────────────────────────────

    def _get_ticker_obj(self, ticker: str):
        return yf.Ticker(ticker)

    def _get_chain(self, ticker: str, expiry: str) -> Optional[dict]:
        """
        Return {"calls": DataFrame, "puts": DataFrame} for ticker/expiry.
        Tries Tradier first when configured (real-time + greeks),
        falls back to yfinance (~15 min delayed).
        Cached: 60s for Tradier, 5 min for yfinance.
        """
        key = f"{ticker}|{expiry}"
        # TTL differs by source: Tradier is real-time so cache shorter
        try:
            from tradier_options_feed import tradier_feed
            if tradier_feed.is_configured():
                ttl = 60
                cached = _cache_get(_CHAIN_CACHE, key, ttl)
                if cached is not None:
                    return cached
                data = tradier_feed.get_chain(ticker, expiry)
                if data:
                    _cache_set(_CHAIN_CACHE, key, data)
                    return data
                # Tradier returned None — fall through to yfinance
        except Exception:
            pass

        # yfinance fallback (or primary when Tradier not configured)
        cached = _cache_get(_CHAIN_CACHE, key, _CHAIN_CACHE_TTL)
        if cached is not None:
            return cached
        try:
            t = self._get_ticker_obj(ticker)
            chain = t.option_chain(expiry)
            data = {"calls": chain.calls, "puts": chain.puts}
            _cache_set(_CHAIN_CACHE, key, data)
            return data
        except Exception:
            return None

    def get_current_price(self, ticker: str) -> Optional[float]:
        """Public wrapper — live price from Alpaca SIP, fallback to yfinance."""
        return self._get_current_price(ticker)

    def _get_current_price(self, ticker: str) -> Optional[float]:
        """Live price from Alpaca SIP, fallback to yfinance."""
        try:
            q = self.data_feed.get_real_time_quote(ticker)
            if q:
                p = _safe_float(q.get("mid_price") or q.get("last") or 0)
                if p > 0:
                    return p
        except Exception:
            pass
        # yfinance fallback
        cached = _cache_get(_INFO_CACHE, ticker, _INFO_CACHE_TTL)
        if cached:
            return cached.get("price")
        try:
            info = yf.Ticker(ticker).fast_info
            p = _safe_float(getattr(info, "last_price", 0) or 0)
            if p > 0:
                _cache_set(_INFO_CACHE, ticker, {"price": p})
                return p
        except Exception:
            pass
        return None

    # ── public: chain data snapshot ────────────────────────────────────────

    def get_options_chain_data(self, ticker: str) -> Optional[dict]:
        """
        Return a real options chain snapshot for ticker (front-month expiry).
        Replaces previous price-history proxy.
        """
        try:
            t_obj = self._get_ticker_obj(ticker)
            expirations = t_obj.options
            if not expirations:
                return None
            expiry = _front_month(expirations)
            if not expiry:
                return None
            chain = self._get_chain(ticker, expiry)
            if not chain:
                return None
            price = self._get_current_price(ticker)
            return {
                "ticker":      ticker,
                "current_price": price,
                "expiry":      expiry,
                "dte":         _dte(expiry),
                "calls":       chain["calls"],
                "puts":        chain["puts"],
                "timestamp":   datetime.now(),
            }
        except Exception:
            return None

    # ── public: max pain ───────────────────────────────────────────────────

    def calculate_real_max_pain(self, ticker: str, expiry: str = None) -> Optional[dict]:
        """
        True max-pain: candidate price where total intrinsic value paid out to
        option buyers is minimised (= maximum worthless expiry for holders).

        Returns dict with price, distance_pct, bias.
        """
        try:
            t_obj = self._get_ticker_obj(ticker)
            expirations = t_obj.options
            if not expirations:
                return None
            target_expiry = expiry or _front_month(expirations)
            if not target_expiry:
                return None
            chain = self._get_chain(ticker, target_expiry)
            if not chain:
                return None

            calls = chain["calls"][["strike", "openInterest"]].copy()
            puts  = chain["puts"][["strike",  "openInterest"]].copy()
            calls["openInterest"] = calls["openInterest"].fillna(0).astype(float)
            puts["openInterest"]  = puts["openInterest"].fillna(0).astype(float)

            # All unique strikes are candidate prices
            all_strikes = sorted(
                set(calls["strike"].tolist()) | set(puts["strike"].tolist())
            )
            if not all_strikes:
                return None

            min_pain = float("inf")
            max_pain_price = all_strikes[len(all_strikes) // 2]  # fallback midpoint

            for candidate in all_strikes:
                # Call holders gain when candidate > strike  (ITM call)
                call_pain = float(
                    ((candidate - calls["strike"]).clip(lower=0) * calls["openInterest"]).sum()
                ) * 100
                # Put holders gain when candidate < strike (ITM put)
                put_pain = float(
                    ((puts["strike"] - candidate).clip(lower=0) * puts["openInterest"]).sum()
                ) * 100
                total = call_pain + put_pain
                if total < min_pain:
                    min_pain = total
                    max_pain_price = candidate

            current_price = self._get_current_price(ticker) or max_pain_price
            distance_pct = ((current_price - max_pain_price) / max_pain_price) * 100

            # Magnet strength by day-of-week (strongest on expiry Friday)
            dow = datetime.now().weekday()
            exp_date = date.fromisoformat(target_expiry)
            days_to_exp = _dte(target_expiry)
            if days_to_exp <= 1 and exp_date.weekday() == 4:
                strength = "STRONG"
            elif days_to_exp <= 3:
                strength = "MODERATE"
            else:
                strength = "WEAK"

            return {
                "price":          round(max_pain_price, 2),
                "distance_pct":   round(distance_pct, 2),
                "bias":           "DOWNWARD" if distance_pct > 0.5 else
                                  "UPWARD"   if distance_pct < -0.5 else "NEUTRAL",
                "magnet_strength": strength,
                "expiry":         target_expiry,
                "dte":            days_to_exp,
            }
        except Exception:
            return None

    # ── public: implied move ───────────────────────────────────────────────

    def get_implied_move(self, ticker: str) -> Optional[dict]:
        """
        Options-market expected move for front-month expiry.
        Uses ATM call IV (yfinance returns IV as decimal, e.g. 0.30 = 30%).

        Returns: implied_move_pct, implied_move_dollar, atm_iv, dte.
        """
        try:
            t_obj = self._get_ticker_obj(ticker)
            expirations = t_obj.options
            if not expirations:
                return None
            expiry = _front_month(expirations)
            if not expiry:
                return None
            chain = self._get_chain(ticker, expiry)
            if not chain:
                return None

            current_price = self._get_current_price(ticker)
            if not current_price:
                return None

            calls = chain["calls"].copy()
            calls = calls[calls["impliedVolatility"] > 0]
            if calls.empty:
                return None

            # ATM strike = closest to current price with positive OI
            calls["dist"] = (calls["strike"] - current_price).abs()
            atm_row = calls.nsmallest(1, "dist").iloc[0]
            atm_iv  = _safe_float(atm_row["impliedVolatility"])  # already decimal
            dte     = _dte(expiry)

            if atm_iv <= 0 or dte <= 0:
                return None

            implied_move_pct    = atm_iv * math.sqrt(dte / 365) * 100
            implied_move_dollar = current_price * (implied_move_pct / 100)

            return {
                "ticker":              ticker,
                "current_price":       round(current_price, 2),
                "atm_iv":              round(atm_iv * 100, 1),      # display as %
                "atm_strike":          _safe_float(atm_row["strike"]),
                "dte":                 dte,
                "expiry":              expiry,
                "implied_move_pct":    round(implied_move_pct, 2),
                "implied_move_dollar": round(implied_move_dollar, 2),
                "upside_target":       round(current_price + implied_move_dollar, 2),
                "downside_target":     round(current_price - implied_move_dollar, 2),
            }
        except Exception:
            return None

    # ── public: IV skew ────────────────────────────────────────────────────

    def get_iv_skew(self, ticker: str) -> Optional[dict]:
        """
        Put/call IV skew.
        Compares IV of 10%-OTM puts vs 10%-OTM calls.
        Positive skew_pct = put IV > call IV = bearish fear premium.
        """
        try:
            t_obj = self._get_ticker_obj(ticker)
            expirations = t_obj.options
            if not expirations:
                return None
            expiry = _front_month(expirations)
            if not expiry:
                return None
            chain = self._get_chain(ticker, expiry)
            if not chain:
                return None

            price = self._get_current_price(ticker)
            if not price:
                return None

            calls = chain["calls"].copy()
            puts  = chain["puts"].copy()
            calls = calls[calls["impliedVolatility"] > 0]
            puts  = puts[puts["impliedVolatility"]  > 0]

            otm_target = price * 0.90   # 10% OTM put strike
            otm_call_t = price * 1.10   # 10% OTM call strike

            def nearest_iv(df, target_strike: float) -> Optional[float]:
                if df.empty:
                    return None
                df = df.copy()
                df["dist"] = (df["strike"] - target_strike).abs()
                row = df.nsmallest(1, "dist").iloc[0]
                iv = _safe_float(row["impliedVolatility"])
                return iv if iv > 0 else None

            put_iv  = nearest_iv(puts,  otm_target)
            call_iv = nearest_iv(calls, otm_call_t)
            atm_iv_row = calls.copy()
            atm_iv_row["dist"] = (atm_iv_row["strike"] - price).abs()
            atm_iv = _safe_float(atm_iv_row.nsmallest(1, "dist").iloc[0]["impliedVolatility"]) if not atm_iv_row.empty else None

            if put_iv is None or call_iv is None:
                return None

            skew_pct = (put_iv - call_iv) * 100   # in percentage points

            sentiment = (
                "VERY_BEARISH" if skew_pct > 10 else
                "BEARISH"      if skew_pct > 5  else
                "NEUTRAL"      if abs(skew_pct) <= 5 else
                "BULLISH"      if skew_pct > -10 else
                "VERY_BULLISH"
            )

            return {
                "ticker":       ticker,
                "put_iv_pct":   round(put_iv  * 100, 1),
                "call_iv_pct":  round(call_iv * 100, 1),
                "atm_iv_pct":   round(atm_iv  * 100, 1) if atm_iv else None,
                "skew_pct":     round(skew_pct, 2),
                "sentiment":    sentiment,
                "expiry":       expiry,
            }
        except Exception:
            return None

    # ── public: volume/OI anomaly detection ───────────────────────────────

    def get_volume_oi_anomalies(
        self,
        ticker: str,
        vol_oi_threshold: float = 2.0,
        min_volume: int = 200,
    ) -> List[dict]:
        """
        Strikes where volume/OI > threshold AND volume >= min_volume.
        High volume vs open interest = fresh directional positioning.
        Sorted by vol/OI ratio descending.
        """
        try:
            t_obj = self._get_ticker_obj(ticker)
            expirations = t_obj.options
            if not expirations:
                return []
            expiry = _front_month(expirations)
            if not expiry:
                return []
            chain = self._get_chain(ticker, expiry)
            if not chain:
                return []

            price = self._get_current_price(ticker)
            anomalies = []

            for side, df in (("CALL", chain["calls"]), ("PUT", chain["puts"])):
                for _, row in df.iterrows():
                    vol = _safe_float(row.get("volume"), 0)
                    oi  = _safe_float(row.get("openInterest"), 0)
                    if oi <= 0 or vol < min_volume:
                        continue
                    ratio = vol / oi
                    if ratio < vol_oi_threshold:
                        continue
                    strike = _safe_float(row.get("strike"))
                    iv     = _safe_float(row.get("impliedVolatility"))
                    moneyness = (
                        "ITM" if (side == "CALL" and strike < (price or 0)) or
                                 (side == "PUT"  and strike > (price or 0))
                        else "OTM"
                    ) if price else "UNKNOWN"
                    anomalies.append({
                        "ticker":    ticker,
                        "side":      side,
                        "strike":    strike,
                        "volume":    int(vol),
                        "open_interest": int(oi),
                        "vol_oi_ratio":  round(ratio, 2),
                        "iv_pct":    round(iv * 100, 1),
                        "moneyness": moneyness,
                        "expiry":    expiry,
                    })

            return sorted(anomalies, key=lambda x: -x["vol_oi_ratio"])
        except Exception:
            return []

    # ── public: put/call ratios ────────────────────────────────────────────

    def get_put_call_ratios(self, ticker: str) -> Optional[dict]:
        """
        Real put/call ratios from actual options chain volume and OI.
        Replaces previous VIX-arithmetic estimates.
        """
        try:
            t_obj = self._get_ticker_obj(ticker)
            expirations = t_obj.options
            if not expirations:
                return None
            expiry = _front_month(expirations)
            if not expiry:
                return None
            chain = self._get_chain(ticker, expiry)
            if not chain:
                return None

            call_vol = _safe_float(chain["calls"]["volume"].fillna(0).sum())
            put_vol  = _safe_float(chain["puts"]["volume"].fillna(0).sum())
            call_oi  = _safe_float(chain["calls"]["openInterest"].fillna(0).sum())
            put_oi   = _safe_float(chain["puts"]["openInterest"].fillna(0).sum())

            pcr_vol = (put_vol / call_vol)  if call_vol > 0 else None
            pcr_oi  = (put_oi  / call_oi)   if call_oi  > 0 else None

            def sentiment(ratio: Optional[float]) -> str:
                if ratio is None:
                    return "UNKNOWN"
                if ratio > 1.5:  return "VERY_DEFENSIVE"
                if ratio > 1.2:  return "DEFENSIVE"
                if ratio > 0.9:  return "NEUTRAL"
                if ratio > 0.7:  return "BULLISH"
                return "VERY_BULLISH"

            return {
                "ticker":          ticker,
                "pcr_volume":      round(pcr_vol, 3) if pcr_vol is not None else None,
                "pcr_oi":          round(pcr_oi,  3) if pcr_oi  is not None else None,
                "sentiment_vol":   sentiment(pcr_vol),
                "sentiment_oi":    sentiment(pcr_oi),
                "total_call_vol":  int(call_vol),
                "total_put_vol":   int(put_vol),
                "total_call_oi":   int(call_oi),
                "total_put_oi":    int(put_oi),
                "expiry":          expiry,
            }
        except Exception:
            return None

    # ── public: gamma score (rebuilt from real signals) ───────────────────

    def estimate_gamma_exposure(self, ticker: str) -> Optional[dict]:
        """
        Gamma exposure estimate derived from real chain signals:
        PCR volume, IV skew, and distance from max pain.
        """
        try:
            pcr   = self.get_put_call_ratios(ticker)
            skew  = self.get_iv_skew(ticker)
            pain  = self.calculate_real_max_pain(ticker)
            price = self._get_current_price(ticker)

            if not pcr or not price:
                return None

            pcr_vol = pcr.get("pcr_volume") or 1.0
            skew_pct = (skew or {}).get("skew_pct", 0.0)
            dist = (pain or {}).get("distance_pct", 0.0)

            # Positive GEX: low PCR + low skew + price near pain = dealers stabilise
            # Negative GEX: high PCR + high skew + price far from pain = dealers amplify
            if pcr_vol < 0.8 and skew_pct < 3:
                gex_type   = "POSITIVE"
                gex_strength = min(100, (1.0 - pcr_vol) * 60 + max(0, 3 - skew_pct) * 5)
                effect = "Dealers long gamma — stabilising force"
            elif pcr_vol > 1.3 or skew_pct > 8:
                gex_type   = "NEGATIVE"
                gex_strength = min(100, (pcr_vol - 1.0) * 50 + max(0, skew_pct - 3) * 4)
                effect = "Dealers short gamma — amplifying moves"
            else:
                gex_type   = "NEUTRAL"
                gex_strength = 50.0
                effect = "Balanced gamma — no strong bias"

            return {
                "ticker":                  ticker,
                "current_price":           round(price, 2),
                "gex_type":                gex_type,
                "gex_strength":            round(gex_strength, 1),
                "effect":                  effect,
                "pcr_volume":              round(pcr_vol, 3),
                "skew_pct":                round(skew_pct, 2),
                "dist_from_max_pain_pct":  round(dist, 2),
            }
        except Exception:
            return None

    def find_gamma_flip_level(self, ticker: str) -> Optional[dict]:
        """
        Gamma flip = max pain level. Price below = acceleration zone.
        Uses real max pain from OI, not price-range proxy.
        """
        pain  = self.calculate_real_max_pain(ticker)
        price = self._get_current_price(ticker)
        if not pain or not price:
            return None

        flip  = pain["price"]
        dist  = ((price - flip) / flip) * 100

        if price > flip:
            regime   = "ABOVE FLIP"
            hedging  = "Dealers sell into rallies (suppression)"
            strategy = "Expect resistance, fade rallies near flip"
        elif price < flip:
            regime   = "BELOW FLIP"
            hedging  = "Dealers buy on dips (acceleration)"
            strategy = "Momentum trades work, breakouts amplified"
        else:
            regime   = "AT FLIP"
            hedging  = "Neutral zone — choppy action likely"
            strategy = "Wait for clear break above/below"

        return {
            "ticker":                ticker,
            "current_price":         round(price, 2),
            "gamma_flip_level":      flip,
            "regime":                regime,
            "dealer_hedging":        hedging,
            "trading_strategy":      strategy,
            "distance_from_flip_pct": round(dist, 2),
            "max_pain_expiry":       pain.get("expiry"),
        }

    def calculate_gamma_score(self, ticker: str) -> int:
        """
        0-100 score: high = gamma environment favours long positions.
        Now derived from real chain signals.
        """
        gex  = self.estimate_gamma_exposure(ticker)
        flip = self.find_gamma_flip_level(ticker)

        if not gex or not flip:
            return 50

        score = 50

        if flip["regime"] == "BELOW FLIP":
            score += 20
        elif flip["regime"] == "ABOVE FLIP":
            score -= 20

        if gex["gex_type"] == "NEGATIVE":
            score += 15
        elif gex["gex_type"] == "POSITIVE":
            score -= 10

        if gex.get("pcr_volume", 1.0) < 0.8:
            score += 10
        elif gex.get("pcr_volume", 1.0) > 1.3:
            score -= 10

        if abs(flip.get("distance_from_flip_pct", 5)) < 2:
            score -= 5   # at flip = choppy

        return max(0, min(100, score))

    def display_gamma_analysis(self, ticker: str) -> dict:
        """Display complete gamma analysis."""
        print(f"\n{'='*80}")
        print(f"⚡ GAMMA ANALYSIS (real chain): {ticker}")
        print(f"{'='*80}")

        gex = self.estimate_gamma_exposure(ticker)
        if gex:
            print(f"\n📊 GAMMA EXPOSURE:")
            print(f"  Type:          {gex['gex_type']}")
            print(f"  Strength:      {gex['gex_strength']:.1f}/100")
            print(f"  Effect:        {gex['effect']}")
            print(f"  PCR (volume):  {gex['pcr_volume']}")
            print(f"  IV Skew:       {gex['skew_pct']:+.2f}pp")
            print(f"  Dist Max Pain: {gex['dist_from_max_pain_pct']:+.2f}%")

        flip = self.find_gamma_flip_level(ticker)
        if flip:
            print(f"\n🔄 GAMMA FLIP (max pain level):")
            print(f"  Flip Level:  ${flip['gamma_flip_level']}")
            print(f"  Current:     ${flip['current_price']}")
            print(f"  Regime:      {flip['regime']}")
            print(f"  Distance:    {flip['distance_from_flip_pct']:+.2f}%")
            print(f"  Expiry:      {flip['max_pain_expiry']}")

        mv = self.get_implied_move(ticker)
        if mv:
            print(f"\n📐 IMPLIED MOVE (front month):")
            print(f"  ATM IV:      {mv['atm_iv']}%")
            print(f"  Expected ±:  {mv['implied_move_pct']:.2f}%  (${mv['implied_move_dollar']:.2f})")
            print(f"  Range:       ${mv['downside_target']} — ${mv['upside_target']}")
            print(f"  DTE:         {mv['dte']} days ({mv['expiry']})")

        gamma_score = self.calculate_gamma_score(ticker)
        verdict = (
            "GAMMA FAVORABLE — Long positions favoured"  if gamma_score >= 70 else
            "GAMMA NEUTRAL — No strong bias"             if gamma_score >= 50 else
            "GAMMA UNFAVORABLE — Caution on longs"
        )
        emoji = "🟢" if gamma_score >= 70 else "🟡" if gamma_score >= 50 else "🔴"
        print(f"\n{emoji} GAMMA SCORE: {gamma_score}/100  {verdict}")
        print(f"\n{'='*80}\n")

        return {"gex": gex, "flip": flip, "implied_move": mv, "gamma_score": gamma_score}


# ── Module-level shared instance (avoids re-creating per ticker call) ─────────
_shared_intel: "OptionsIntelligence | None" = None


def _get_shared_intel() -> "OptionsIntelligence":
    global _shared_intel
    if _shared_intel is None:
        _shared_intel = OptionsIntelligence()
    return _shared_intel


def get_options_score_adj(ticker: str) -> dict:
    """
    Lightweight per-ticker options signal for score boost/penalty.

    Returns:
        {
          "adj":    float  — score adjustment (-5 to +5),  0.0 on fallback
          "pcr":    float | None  — front-month put/call ratio (volume)
          "gamma":  str   — "POSITIVE" | "NEGATIVE" | "NEUTRAL"
          "source": str   — "tradier" | "real_chain" | "unavailable"
          "note":   str   — human-readable summary for logging
        }

    PCR contribution  (-3 → +3):
      < 0.70  → +3  (call-dominated, bullish sentiment)
      < 0.90  → +1
      ≤ 1.10  →  0  (neutral)
      ≤ 1.30  → -1
      > 1.30  → -3  (put-dominated, bearish sentiment)

    GEX contribution  (-2 → +2):
      POSITIVE → +2  (dealers long gamma, stable price action)
      NEGATIVE → -2  (dealers short gamma, amplified moves = higher risk)
      NEUTRAL  →  0

    Total capped at [-5, +5]. Returns neutral dict on any data failure.
    """
    _NEUTRAL = {
        "adj": 0.0, "pcr": None, "gamma": "NEUTRAL",
        "source": "unavailable", "note": "options data unavailable — neutral",
    }
    try:
        intel = _get_shared_intel()

        pcr_data = intel.get_put_call_ratios(ticker)
        gex_data = intel.estimate_gamma_exposure(ticker)

        if not pcr_data and not gex_data:
            return _NEUTRAL

        pcr   = pcr_data.get("pcr_volume") if pcr_data else None
        gamma = (gex_data.get("gex_type") or "NEUTRAL") if gex_data else "NEUTRAL"

        # PCR component
        if pcr is None:
            pcr_adj = 0.0
        elif pcr < 0.70:
            pcr_adj = 3.0
        elif pcr < 0.90:
            pcr_adj = 1.0
        elif pcr <= 1.10:
            pcr_adj = 0.0
        elif pcr <= 1.30:
            pcr_adj = -1.0
        else:
            pcr_adj = -3.0

        # GEX component
        gamma_adj = 2.0 if gamma == "POSITIVE" else (-2.0 if gamma == "NEGATIVE" else 0.0)

        adj = round(max(-5.0, min(5.0, pcr_adj + gamma_adj)), 1)

        note_parts = []
        if pcr is not None:
            note_parts.append(f"PCR={pcr:.2f}")
        note_parts.append(f"GEX={gamma}")
        note_parts.append(f"adj={adj:+.1f}")

        return {
            "adj":    adj,
            "pcr":    pcr,
            "gamma":  gamma,
            "source": "real_chain",
            "note":   " ".join(note_parts),
        }
    except Exception:
        return _NEUTRAL


# ── Diagnostic function ───────────────────────────────────────────────────────

def diagnose_options_feed(ticker: str = "SPY") -> dict:
    """
    Returns a dict describing the health of the options data pipeline.

    Keys:
        tradier_configured  bool  — tradier_options_feed module importable
        tradier_token_set   bool  — TRADIER_API_TOKEN env var is non-empty
        chain_fetch_ok      bool  — at least one chain fetch succeeded
        pcr_computed        bool  — PCR ratio was computed (not None)
        score_adj_source    str   — "tradier" | "real_chain" | "unavailable"
        sample_pcr          float | None
        sample_adj          float
        error               str | None
    """
    result: dict = {
        "tradier_configured": False,
        "tradier_token_set": False,
        "chain_fetch_ok": False,
        "pcr_computed": False,
        "score_adj_source": "unavailable",
        "sample_pcr": None,
        "sample_adj": 0.0,
        "error": None,
    }

    # 1. Check Tradier availability
    try:
        from tradier_options_feed import tradier_feed as _tf
        result["tradier_configured"] = True
        result["tradier_token_set"] = _tf.is_configured()
    except Exception:
        pass  # Tradier not configured — that's fine, yfinance is the fallback

    # If token is explicitly set in env (even without module)
    import os as _os
    if _os.getenv("TRADIER_API_TOKEN", "").strip():
        result["tradier_token_set"] = True

    # 2. Try a real chain fetch via the shared intel object
    try:
        intel = _get_shared_intel()
        pcr_data = intel.get_put_call_ratios(ticker)

        if pcr_data is not None:
            result["chain_fetch_ok"] = True
            pcr_val = pcr_data.get("pcr_volume")
            if pcr_val is not None:
                result["pcr_computed"] = True
                result["sample_pcr"] = float(pcr_val)
        else:
            result["error"] = "get_put_call_ratios returned None"
    except Exception as exc:
        result["error"] = str(exc)

    # 3. Determine source via get_options_score_adj
    try:
        score_data = get_options_score_adj(ticker)
        result["score_adj_source"] = score_data.get("source", "unavailable")
        result["sample_adj"] = float(score_data.get("adj", 0.0))
        if result["score_adj_source"] != "unavailable":
            result["chain_fetch_ok"] = True
            if score_data.get("pcr") is not None:
                result["pcr_computed"] = True
                result["sample_pcr"] = float(score_data["pcr"])
    except Exception as exc:
        if result["error"] is None:
            result["error"] = str(exc)

    return result


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="Options Intelligence CLI")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Run options feed diagnostic and print results as JSON",
    )
    parser.add_argument(
        "--ticker",
        type=str,
        default="SPY",
        help="Ticker to use for diagnostics (default: SPY)",
    )
    args = parser.parse_args()

    if args.diagnose:
        diag = diagnose_options_feed(args.ticker)
        print(_json.dumps(diag, indent=2))
