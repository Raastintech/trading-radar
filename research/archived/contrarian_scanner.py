"""
Contrarian Scanner — Fear Opportunity Strategy (v2)

Activation:  VIX >= 28.0, plus watch mode in 22.0-27.9 after a recent panic peak
Direction:   LONG only (buy oversold quality into institutional accumulation)
RRR:         1.5 minimum (lower R:R justified by historically elevated base rate)
Size:        50% of normal (half-size scale-in — not catching falling knives full)

v2 refinements:
  1. Market washout context gate — VIX alone is not enough. Requires evidence
     that the index has already flushed (SPY extension from 10d high, or SPY RSI<38).
     Filters out Day 1 of a selloff where there is still significant downside ahead.

  2. Reversal quality confirmation — three explicit price-action signals replace
     the old structural bottoming check. Oversold alone is not a buy signal.
     Evidence of stabilization or buyer re-entry is required.

  3. Sector diversity control — one pick per GICS sector per cycle (second allowed
     only at EXCEPTIONAL_SCORE). Prevents three correlated panic names from
     masquerading as three separate trades.
"""

import logging
import statistics
from typing import Dict, List, Optional

import yfinance as yf

from alpaca_data import AlpacaDataFeed

try:
    from options_intelligence import get_options_score_adj as _get_options_score_adj
    _HAS_OPTIONS_SIGNAL = True
except ImportError:
    _get_options_score_adj = None
    _HAS_OPTIONS_SIGNAL = False

logger = logging.getLogger(__name__)

# Module-level sector cache — persists for process lifetime.
# Sectors don't change intraday so stale risk is negligible.
_SECTOR_CACHE: Dict[str, str] = {}


class ContrarianScanner:
    """
    Specialized fear-regime scanner. Zero overhead when VIX < watch trigger.

    Pipeline per scan cycle:
      1. VIX gate          — fast exit if not in fear regime
      2. Washout context   — index must have already flushed, not just started
      3. Per-ticker eval   — pattern + RSI + trend proximity + reversal quality
      4. Scoring           — weighted, reversal signals carry real weight
      5. Sector diversity  — deduplicate concentrated sector exposure
      6. Pick cap          — hard limit, returns top MAX_PICKS
    """

    VIX_ACTIVE_TRIGGER = 28.0  # Full scan — active panic / washout regime
    VIX_WATCH_TRIGGER  = 22.0  # Watch scan — post-peak recovery phase
    WATCH_LOOKBACK_DAYS = 7
    VIX_TRIGGER       = VIX_ACTIVE_TRIGGER  # Backward-compatible alias
    MIN_SCORE         = 60     # Raised from 55; reversal signals now available to earn points
    MIN_RRR           = 1.5    # Lower than Sniper's 2.5 — base rate compensates
    SIZE_MULT         = 0.5    # Half-size: scale in, not full conviction
    RSI_OVERSOLD      = 42     # Ceiling; effective gate tightens as fear rises
    BARS_NEEDED       = 55     # 50MA + RSI(14) + 20d patterns

    # Washout gate thresholds
    SPY_EXTENSION_THRESH = -0.03   # SPY must be >= 3% below its 10-day high
    SPY_RSI_THRESH       = 38.0    # OR SPY RSI(14) < 38

    # Diversity / pick limits
    MAX_PICKS         = 4     # Hard cap on contrarian picks per scan cycle
    EXCEPTIONAL_SCORE = 75    # Score threshold to allow a 2nd pick from the same sector

    def __init__(self):
        self.data_feed = AlpacaDataFeed()
        self._scan_rejects: List[Dict] = []
        self._cycle_status: Dict = {}
        logger.info(
            "ContrarianScanner v2 initialized (active VIX trigger: %.1f, watch trigger: %.1f)",
            self.VIX_ACTIVE_TRIGGER,
            self.VIX_WATCH_TRIGGER,
        )

    # ------------------------------------------------------------------
    # Public API — interface unchanged from v1
    # ------------------------------------------------------------------

    def scan(self, tickers: List[str], vix_level: float) -> List[Dict]:
        """
        Scan tickers for contrarian setups.

        Args:
            tickers:   Universe (same pool as Sniper/Remora).
            vix_level: Current VIX reading.

        Returns:
            Opportunity dicts sorted by quality_score, after diversity filter.
            Empty list if VIX < trigger or market not in washout state.
        """
        self._scan_rejects = []
        self._cycle_status = {
            "vix_level": float(vix_level or 0.0),
            "tickers_considered": len(tickers or []),
            "state": "STARTED",
        }
        progress = getattr(self, "_progress_callback", None)

        if vix_level < self.VIX_WATCH_TRIGGER:
            self._cycle_status.update({
                "state": "STANDBY",
                "reason": "vix_below_watch_trigger",
            })
            return []

        # --- Washout context: computed ONCE per cycle, passed to all evals ---
        washout_ctx = self._market_washout_context()
        if not washout_ctx.get("context_valid", False):
            self._cycle_status.update({
                "state": "STANDBY",
                "reason": "washout_context_invalid",
                "washout_context": washout_ctx,
            })
            logger.warning(
                "ContrarianScanner: washout context unavailable (%s) — skipping cycle",
                washout_ctx.get("reason", "unknown"),
            )
            return []
        if not washout_ctx.get("washout", False):
            self._cycle_status.update({
                "state": "STANDBY",
                "reason": "market_not_in_washout",
                "washout_context": washout_ctx,
            })
            logger.info(
                "ContrarianScanner: market not in washout state "
                "(SPY ext=%.1f%%, SPY RSI=%.1f) — standing down",
                washout_ctx.get("spy_extension_pct", 0.0),
                washout_ctx.get("spy_rsi", 50.0),
            )
            return []

        if vix_level < self.VIX_ACTIVE_TRIGGER:
            recent_peak = self._recent_vix_peaked(
                threshold=self.VIX_ACTIVE_TRIGGER,
                days=self.WATCH_LOOKBACK_DAYS,
            )
            if not recent_peak:
                self._cycle_status.update({
                    "state": "STANDBY",
                    "reason": "vix_in_watch_band_no_recent_peak",
                    "washout_context": washout_ctx,
                })
                return []
            self._cycle_status.update({
                "state": "WATCH",
                "reason": "post_peak_recovery_watch",
                "washout_context": washout_ctx,
            })
            return self._run_scan(
                tickers,
                vix_level,
                washout_ctx,
                progress=progress,
                state="WATCH",
                reason="post_peak_recovery_watch",
                min_reversal_count=2,
                rsi_override=38.0,
            )

        return self._run_scan(
            tickers,
            vix_level,
            washout_ctx,
            progress=progress,
            state="SCANNED",
            reason="ok",
        )

    def _run_scan(
        self,
        tickers: List[str],
        vix_level: float,
        washout_ctx: Dict,
        *,
        progress=None,
        state: str,
        reason: str,
        min_reversal_count: int = 0,
        rsi_override: Optional[float] = None,
    ) -> List[Dict]:
        opps: List[Dict] = []
        total = len(tickers)
        for idx, ticker in enumerate(tickers, start=1):
            if callable(progress):
                try:
                    progress(f"ticker:{idx}/{total}:{ticker}")
                except Exception:
                    pass
            try:
                opp = self._evaluate(
                    ticker,
                    vix_level,
                    washout_ctx,
                    min_reversal_count=min_reversal_count,
                    rsi_override=rsi_override,
                )
                if opp:
                    opps.append(opp)
            except Exception as e:
                logger.debug("ContrarianScanner eval error on %s: %s", ticker, e)

        opps.sort(key=lambda x: x["quality_score"], reverse=True)
        opps = self._apply_diversity(opps)
        self._cycle_status.update({
            "state": state,
            "reason": reason,
            "washout_context": washout_ctx,
            "approved_count": len(opps),
            "min_reversal_count": min_reversal_count,
            "rsi_override": rsi_override,
        })

        logger.info(
            "ContrarianScanner[%s]: %d setups (post-diversity) from %d tickers "
            "(VIX %.1f, SPY ext=%.1f%%, SPY RSI=%.1f)",
            state,
            len(opps), len(tickers), vix_level,
            washout_ctx.get("spy_extension_pct", 0.0),
            washout_ctx.get("spy_rsi", 50.0),
        )
        return opps

    # ------------------------------------------------------------------
    # 1. Market washout / capitulation context
    # ------------------------------------------------------------------

    def _market_washout_context(self) -> Dict:
        """
        Check whether the broad market has actually flushed, not just started to sell.

        Two conditions (either is sufficient):
          a) SPY is 3%+ below its 10-day high — a meaningful pullback has occurred.
          b) SPY RSI(14) < 38 — the index itself is in oversold territory.

        Rationale:
          VIX 28 on Day 1 of a breakdown is very different from VIX 28 after the
          market has already dropped 5–8%. The former is often the beginning; the
          latter is the panic washout where contrarian timing improves materially.

        Returns context_valid=True only when SPY data was successfully fetched and
        computed. Returns context_valid=False (fail-closed) on any data or
        infrastructure failure — the caller skips the scan cycle rather than
        allowing trades approved by missing data.

        Fails closed: returns context_valid=False on any data or infrastructure
        failure. The caller treats that as "skip this cycle" rather than "proceed".
        Infrastructure problems must not silently activate contrarian trades.
        """
        try:
            bars = self.data_feed.get_daily_bars("SPY", days_back=22)
            if not bars or len(bars) < 12:
                bars = self._fetch_spy_bars_yfinance()
            if not bars or len(bars) < 12:
                return {"washout": False, "context_valid": False, "reason": "insufficient_spy_data"}

            spy_price     = float(bars[-1]["close"])
            spy_10d_high  = max(float(b["high"]) for b in bars[-10:])
            spy_extension = (spy_price - spy_10d_high) / spy_10d_high   # negative = below peak

            spy_rsi = self._calc_rsi(bars)

            washout = (
                spy_extension <= self.SPY_EXTENSION_THRESH
                or spy_rsi < self.SPY_RSI_THRESH
            )

            return {
                "washout":           washout,
                "context_valid":     True,
                "spy_extension_pct": round(spy_extension * 100, 1),
                "spy_rsi":           round(spy_rsi, 1),
            }

        except Exception as e:
            logger.debug("ContrarianScanner washout context error: %s", e)
            return {"washout": False, "context_valid": False, "reason": f"error: {e}"}

    def _fetch_spy_bars_yfinance(self) -> List[Dict]:
        """
        Fallback for washout context when Alpaca daily bars are unavailable.

        Contrarian activation is too important to hinge on one data source. We
        only need a small recent daily series for SPY regime context, so a
        direct Yahoo fallback is acceptable here. Any failure still returns an
        empty list and the scanner remains fail-closed.
        """
        try:
            hist = yf.Ticker("SPY").history(period="2mo", interval="1d")
            if hist is None or hist.empty:
                return []
            rows: List[Dict] = []
            for _, row in hist.tail(22).iterrows():
                rows.append({
                    "open": float(row.get("Open", 0.0) or 0.0),
                    "high": float(row.get("High", 0.0) or 0.0),
                    "low": float(row.get("Low", 0.0) or 0.0),
                    "close": float(row.get("Close", 0.0) or 0.0),
                    "volume": float(row.get("Volume", 0.0) or 0.0),
                })
            return rows
        except Exception as exc:
            logger.debug("ContrarianScanner yfinance SPY fallback error: %s", exc)
            return []

    def _recent_vix_peaked(self, threshold: float, days: int) -> bool:
        """Check whether VIX recently traded above the active panic threshold."""
        try:
            hist = yf.Ticker("^VIX").history(period="1mo", interval="1d")
            if hist is None or hist.empty:
                return False
            closes = hist["Close"].tail(max(int(days), 1))
            if closes is None or closes.empty:
                return False
            return any(float(value or 0.0) >= float(threshold) for value in closes.tolist())
        except Exception as exc:
            logger.debug("ContrarianScanner yfinance VIX watch fallback error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        ticker: str,
        vix_level: float,
        washout_ctx: Dict,
        *,
        min_reversal_count: int = 0,
        rsi_override: Optional[float] = None,
    ) -> Optional[Dict]:
        bars = self.data_feed.get_daily_bars(ticker, days_back=self.BARS_NEEDED + 5)
        if not bars or len(bars) < self.BARS_NEEDED:
            return None

        price = float(bars[-1]["close"])
        if price <= 0:
            return None

        # --- Structural patterns (at least one required to proceed) ---
        bottoming    = self._is_bottoming(bars)
        accumulation = self._is_accumulation(bars)
        if not bottoming and not accumulation:
            return None

        # --- RSI gate (hard, regime-aware) ---
        rsi = self._calc_rsi(bars)
        rsi_gate = float(rsi_override) if rsi_override is not None else self._rsi_gate_threshold(vix_level)
        if rsi > rsi_gate:
            return None

        # --- Trend proximity (not broken beyond repair) ---
        closes = [float(b["close"]) for b in bars]
        ma_50  = sum(closes[-50:]) / 50
        pct_from_ma50 = (price - ma_50) / ma_50
        if pct_from_ma50 < -0.15:
            return None

        # --- Reversal quality (v2) ---
        reversal = self._reversal_quality(bars)
        if reversal.get("count", 0) < int(min_reversal_count or 0):
            return None

        # Real options signal — for Contrarian, HIGH PCR = extreme put buying = capitulation confirmation
        opt_sig = _get_options_score_adj(ticker) if _HAS_OPTIONS_SIGNAL else {
            "adj": 0.0, "pcr": None, "gamma": "NEUTRAL", "source": "unavailable", "note": "module unavailable"
        }

        # --- Score ---
        score = self._score(rsi, bottoming, accumulation, pct_from_ma50, vix_level, reversal, opt_sig, rsi_gate=rsi_gate)
        if score < self.MIN_SCORE:
            return None

        # --- Signal construction ---
        low_20 = min(float(b["low"]) for b in bars[-20:])
        stop   = round(low_20 * 0.99, 2)
        risk   = price - stop
        if risk <= 0:
            return None

        target = round(price + risk * 2.0, 2)
        rrr    = (target - price) / risk
        if rrr < self.MIN_RRR:
            return None

        avg_vol_20 = sum(float(b["volume"]) for b in bars[-20:]) / 20

        signal = {
            "signal":            "BUY",
            "direction":         "LONG",
            "entry_price":       round(price, 2),
            "stop_loss":         stop,
            "target_price":      target,
            "risk_reward_ratio": round(rrr, 2),
            "composite_score":   score,
            "rsi":               round(rsi, 1),
            "ma_50":             round(ma_50, 2),
            "pct_from_ma50":     round(pct_from_ma50 * 100, 1),
            "avg_volume":        int(avg_vol_20),
            "shares":            0,
            "strategy":          "CONTRARIAN",
            "sector":            "UNKNOWN",   # enriched by _apply_diversity
            "market_value":      0.0,
            "position_value":    0.0,
        }

        return {
            "strategy":      "CONTRARIAN",
            "ticker":        ticker,
            "signal":        signal,
            "decision":      {"decision": "EXECUTE", "confidence": round(score / 100, 2)},
            "quality_score": score,
            "vix_context":   vix_level,
            "size_mult":     self.SIZE_MULT,
            "washout_ctx":   washout_ctx,
            "options_pcr":   opt_sig.get("pcr"),
            "options_gamma": opt_sig.get("gamma", "NEUTRAL"),
            "options_score_adj": opt_sig.get("adj", 0.0),
            "patterns": {
                "bottoming":       bottoming,
                "accumulation":    accumulation,
                "rsi":             round(rsi, 1),
                "rsi_gate":        round(rsi_gate, 1),
                "strong_close":    reversal["strong_close"],
                "reversal_candle": reversal["reversal_candle"],
                "higher_low":      reversal["higher_low"],
                "reversal_count":  reversal["count"],
            },
        }

    # ------------------------------------------------------------------
    # 2. Reversal quality (v2) — three explicit price-action confirmations
    # ------------------------------------------------------------------

    def _reversal_quality(self, bars: List[Dict]) -> Dict:
        """
        Check three independent price-action signals for evidence of
        stabilization or buyer re-entry. Each signal worth +10 in scoring.

        None of these signals is sufficient alone. Together they form a
        coherent picture of a stock that is bottoming rather than falling.
        """
        sc = self._strong_close(bars)
        rc = self._reversal_candle(bars)
        hl = self._higher_low(bars)
        return {
            "count":           int(sc) + int(rc) + int(hl),
            "strong_close":    sc,
            "reversal_candle": rc,
            "higher_low":      hl,
        }

    def _strong_close(self, bars: List[Dict]) -> bool:
        """
        Closed in the upper 50% of today's range.

        Interpretation: buyers came in intraday and defended price into the
        close. A stock closing at its low of the day is still in free-fall —
        this gate rejects those.
        """
        bar = bars[-1]
        day_range = float(bar["high"]) - float(bar["low"])
        if day_range <= 0:
            return False
        close_pos = (float(bar["close"]) - float(bar["low"])) / day_range
        return close_pos >= 0.50

    def _reversal_candle(self, bars: List[Dict]) -> bool:
        """
        Hammer or bullish engulfing on the most recent bar.

        Hammer: lower wick dominates (at least 2× body AND 30% of range),
        upper wick minimal — rejection of lows with buyers absorbing supply.

        Bullish engulfing: today is green and its body fully covers yesterday's
        red body — aggressive buying after a down day.
        """
        if len(bars) < 2:
            return False

        cur  = bars[-1]
        prev = bars[-2]

        c_open  = float(cur["open"])
        c_close = float(cur["close"])
        c_high  = float(cur["high"])
        c_low   = float(cur["low"])

        day_range = c_high - c_low
        if day_range <= 0:
            return False

        body       = abs(c_close - c_open)
        lower_wick = min(c_close, c_open) - c_low
        upper_wick = c_high - max(c_close, c_open)

        # Hammer: lower shadow dominant, small upper shadow
        hammer = (
            lower_wick >= max(body * 2.0, day_range * 0.30)
            and upper_wick <= body
        )

        # Bullish engulfing: green body covers prior red body entirely
        engulfing = (
            c_close > c_open
            and float(prev["close"]) < float(prev["open"])
            and c_open <= float(prev["close"])
            and c_close >= float(prev["open"])
        )

        return hammer or engulfing

    def _higher_low(self, bars: List[Dict]) -> bool:
        """
        Today's low is above yesterday's low.

        The simplest possible evidence of selling exhaustion: sellers pushed
        less far today than yesterday. Not conclusive alone, but meaningful
        in combination with accumulation and oversold RSI.
        """
        if len(bars) < 2:
            return False
        return float(bars[-1]["low"]) > float(bars[-2]["low"])

    # ------------------------------------------------------------------
    # Pattern helpers (unchanged from v1)
    # ------------------------------------------------------------------

    def _is_bottoming(self, bars: List[Dict]) -> bool:
        """Price is 5%+ off its 20-day lows after a 10%+ drawdown."""
        if len(bars) < 20:
            return False
        price   = float(bars[-1]["close"])
        low_20  = min(float(b["low"])  for b in bars[-20:])
        high_20 = max(float(b["high"]) for b in bars[-20:])
        return price > low_20 * 1.05 and low_20 < high_20 * 0.90

    def _is_accumulation(self, bars: List[Dict]) -> bool:
        """Up-day volume 20%+ higher than prior period; down-day volume stable."""
        if len(bars) < 20:
            return False
        recent = bars[-10:]
        prior  = bars[-20:-10]
        recent_up   = sum(float(b["volume"]) for b in recent if float(b["close"]) > float(b["open"]))
        prior_up    = sum(float(b["volume"]) for b in prior  if float(b["close"]) > float(b["open"]))
        recent_down = sum(float(b["volume"]) for b in recent if float(b["close"]) < float(b["open"]))
        prior_down  = sum(float(b["volume"]) for b in prior  if float(b["close"]) < float(b["open"]))
        if prior_up <= 0:
            return False
        return recent_up > prior_up * 1.2 and recent_down <= prior_down

    # ------------------------------------------------------------------
    # Scoring (v2 weights)
    # ------------------------------------------------------------------

    def _score(
        self,
        rsi: float,
        bottoming: bool,
        accumulation: bool,
        pct_from_ma50: float,
        vix_level: float,
        reversal: Dict,
        opt_sig: Dict = None,
        rsi_gate: float = 42.0,
    ) -> float:
        """
        Score 0–100.

        Structural patterns (max 40):
          Accumulation               +25   (volume signature of institutional buying)
          Bottoming                  +15   (reduced from 25 — structure alone is weak)

        Momentum (max 15):
          RSI < 30                   +15   (deeply oversold)
          RSI 30–42                  +8

        Trend proximity (max 10):
          Within 5% of 50MA          +10
          Within 10% of 50MA         +5

        Fear premium (max 7):
          VIX > 32                   +7    (extreme fear = better historical forward returns)

        Reversal quality (max 30 — v2 addition):
          +10 per confirmed signal (strong_close, reversal_candle, higher_low)

        Options capitulation bonus (max +5, min -3 — inverted PCR logic):
          For Contrarian, HIGH PCR = extreme put buying = fear capitulation = BULLISH signal.
          PCR > 1.5 → +5 (extreme hedging/panic = classic contrarian buy)
          PCR 1.3–1.5 → +3
          PCR 0.9–1.3 → 0 (neutral)
          PCR < 0.9 in fear regime → -3 (call-heavy even in panic = not yet capitulated)

        Design intent:
          Without any reversal signals, max achievable ≈ 72 (requires acc+bot+deep RSI).
          A setup with NO reversal signals and ONLY accumulation pattern: 25+8+7 = 40.
          That does not pass 60. Reversal signals are now genuinely load-bearing.
        """
        score = 0.0

        # Structural
        if accumulation:
            score += 25
        if bottoming:
            score += 15

        # Momentum
        if rsi < 30:
            score += 15
        elif rsi <= rsi_gate:
            score += 8

        # Trend proximity
        if pct_from_ma50 >= -0.05:
            score += 10
        elif pct_from_ma50 >= -0.10:
            score += 5

        # Fear premium
        if vix_level > 32:
            score += 7

        # Reversal quality (new in v2)
        score += reversal.get("count", 0) * 10

        # Options capitulation bonus — inverted PCR: high put buying = contrarian bullish
        if opt_sig:
            pcr = opt_sig.get("pcr")
            if pcr is not None:
                if pcr > 1.5:
                    score += 5    # extreme panic hedging
                elif pcr > 1.3:
                    score += 3    # elevated put buying
                elif pcr < 0.9:
                    score -= 3    # call-heavy in panic = market not capitulated
                logger.debug(
                    "Contrarian options bonus: PCR=%.2f GEX=%s → adj=%+.0f",
                    pcr, opt_sig.get("gamma", "?"),
                    5 if pcr > 1.5 else 3 if pcr > 1.3 else (-3 if pcr < 0.9 else 0),
                )

        return min(100.0, round(score, 1))

    def _rsi_gate_threshold(self, vix_level: float) -> float:
        """
        Tighten oversold quality as fear rises.

        In real washout regimes, we want actual panic, not stocks that are only
        mildly weak. Keep 42 as the outer ceiling, but require 35 when VIX is in
        the 30+ zone.
        """
        if vix_level >= 30.0:
            return 35.0
        if vix_level >= self.VIX_TRIGGER:
            return 38.0
        return float(self.RSI_OVERSOLD)

    # ------------------------------------------------------------------
    # 3. Sector diversity control (v2)
    # ------------------------------------------------------------------

    def _apply_diversity(self, opps: List[Dict]) -> List[Dict]:
        """
        Enforce sector diversity across contrarian picks.

        Rule:
          - One pick per GICS sector.
          - A second pick from the same sector is allowed only if its score
            reaches EXCEPTIONAL_SCORE (75+), meaning the setup is materially
            better than the first pick from that sector.
          - Hard cap at MAX_PICKS regardless of sectors.

        Applied after score-sorting so the best setup from each sector
        always wins the allocation slot.

        Sector lookup: yfinance per passing ticker, cached in module-level
        _SECTOR_CACHE for process lifetime (sectors don't change intraday).
        Only passing tickers are looked up — typically 2–10 per cycle.
        """
        sector_counts: Dict[str, int] = {}
        result: List[Dict] = []

        for opp in opps:   # already sorted by quality_score descending
            ticker = opp["ticker"]

            # Sector lookup with cache
            sector = _SECTOR_CACHE.get(ticker)
            if sector is None:
                try:
                    info   = yf.Ticker(ticker).info
                    sector = (info.get("sector") or "UNKNOWN").strip() or "UNKNOWN"
                except Exception:
                    sector = "UNKNOWN"
                _SECTOR_CACHE[ticker] = sector

            # Enrich signal so downstream systems have sector without re-fetching
            opp["signal"]["sector"] = sector

            count = sector_counts.get(sector, 0)
            if count == 0:
                result.append(opp)
                sector_counts[sector] = 1
            elif count == 1 and opp["quality_score"] >= self.EXCEPTIONAL_SCORE:
                # Exceptional second pick from same sector — allow it
                result.append(opp)
                sector_counts[sector] = 2
            # else: skip — already represented

            if len(result) >= self.MAX_PICKS:
                break

        return result

    # ------------------------------------------------------------------
    # Technical helper
    # ------------------------------------------------------------------

    def _calc_rsi(self, bars: List[Dict], period: int = 14) -> float:
        """Wilder's RSI — identical implementation to mean_reversion_analyzer_v2.py."""
        if len(bars) < period + 1:
            return 50.0
        try:
            changes = [
                float(bars[i]["close"]) - float(bars[i - 1]["close"])
                for i in range(1, len(bars))
            ]
            gains  = [c if c > 0 else 0.0 for c in changes]
            losses = [abs(c) if c < 0 else 0.0 for c in changes]

            avg_gain = statistics.mean(gains[:period])
            avg_loss = statistics.mean(losses[:period])

            for i in range(period, len(gains)):
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period

            if avg_loss == 0:
                return 100.0
            return 100 - (100 / (1 + avg_gain / avg_loss))
        except Exception:
            return 50.0


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    vix_data = yf.download("^VIX", period="5d", progress=False)
    vix_now  = float(vix_data["Close"].iloc[-1]) if not vix_data.empty else 31.0
    print(f"Current VIX: {vix_now:.1f}\n")

    scanner      = ContrarianScanner()
    washout_ctx  = scanner._market_washout_context()
    print(f"Market washout context:")
    print(f"  Washout state : {washout_ctx.get('washout')}")
    print(f"  SPY extension : {washout_ctx.get('spy_extension_pct', 'N/A')}%")
    print(f"  SPY RSI       : {washout_ctx.get('spy_rsi', 'N/A')}")
    print()

    test_tickers = ["AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA", "AMD",
                    "JPM", "BAC", "XOM", "JNJ", "PFE", "COST", "WMT"]
    results = scanner.scan(test_tickers, vix_level=vix_now)

    if results:
        print(f"Found {len(results)} contrarian setups (after diversity filter):\n")
        for r in results:
            s = r["signal"]
            p = r["patterns"]
            print(
                f"  {r['ticker']:6s}  sector={s['sector']:<18s}  "
                f"score={r['quality_score']:5.1f}  "
                f"entry=${s['entry_price']:.2f}  stop=${s['stop_loss']:.2f}  "
                f"target=${s['target_price']:.2f}  RRR={s['risk_reward_ratio']:.2f}  "
                f"RSI={p['rsi']:4.1f}  "
                f"acc={p['accumulation']}  bot={p['bottoming']}  "
                f"rev=[sc={p['strong_close']} rc={p['reversal_candle']} hl={p['higher_low']}]"
            )
    else:
        reason = (
            "VIX below trigger"
            if vix_now < ContrarianScanner.VIX_TRIGGER
            else f"no qualifying setups (washout={washout_ctx.get('washout')})"
        )
        print(f"No contrarian setups — {reason}")
