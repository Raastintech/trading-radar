"""
strategies/voyager.py — VOYAGER: long-horizon institutional accumulation, LONG only.

Doctrine: docs/strategy/STRATEGY_DOCTRINE.md §VOYAGER
Spec:     docs/strategy/VOYAGER_LONG_HORIZON_SPEC.md

Mandate:
  Capture long-duration accumulation before the major run is widely recognized.
  Piggyback on real institutional accumulation while using retail flexibility to
  enter earlier and size more precisely.

Direction: LONG only. Never SHORT.
Target hold: 6–18 months.
Book:        A (Long Trend / Leadership)

Platform identity: VOYAGER moves WITH institutions. The edge is entering the
accumulation phase before the move is widely recognized — not after it runs.

Three entry archetypes — scanner detects which applies:
  A. BASE_ACCUMULATION  — MA50 > MA200. Stock building a multi-week base with
     rising dollar volume while price stays tight. Entry before the breakout.
  B. TREND_PULLBACK     — MA50 > MA200. Established uptrend pulling back
     constructively 2–10% into MA50 support. Better multi-month entry.
  C. EARLY_ACCUMULATION — MA50 ≤ MA200 but converging (within 3%, rising toward
     golden cross). Stock outperforming SPY, strong accumulation underway before
     the widely-watched golden cross forms. Highest fundamental bar (≥ 55).

Hard entry conditions (all must pass):
  1. LONG only — VOYAGER never shorts
  2. Price ≥ $5, 20-day avg dollar volume ≥ $5M (institutional-grade liquidity)
  3. Price in constructive zone: not below MA200 × 0.92, not more than 12% above MA50
  4. RS vs SPY positive over 50 trading days — relative leadership
  5. Dollar volume trend: 20d avg ≥ 85% of 60d avg (accumulation not fading)
  6. Up-day volume dominance ratio ≥ 1.0
  7. Fundamental quality score ≥ 40 / 100 (≥ 55 for EARLY_ACCUMULATION)
  8. No earnings within 15 calendar days
  9. At least one archetype signal confirmed
  10. Score ≥ 65

13F integration (soft layer — score overlay only):
  SEC 13F-HR quarterly filings via core/whale_tracker.py add -5 to +8 points.
  13F is delayed (45-day lag). It DOES NOT replace live proxies. It is a slow-moving
  confirmation layer. If 13F data is unavailable, scanner runs normally without it.

Trade geometry:
  Stop   = min(entry − 1.5 × ATR, MA200 × 0.97)  — structural MA200-anchored stop
  Target = entry + (entry − stop) × 2.5            — minimum tactical R:R
  MIN_RRR = 2.5

Data sources:
  Alpaca: daily OHLCV (260 bars), live quotes, SPY bars for RS
  FMP:    earnings calendar, income statement, balance sheet, cash flow statement
  SEC:    13F-HR quarterly filings via edgartools (optional enrichment)

Anti-drift: VOYAGER is LONG only. SHORT logic belongs in strategies/short_sleeve.py.
"""
from __future__ import annotations
import logging
import statistics
from typing import Dict, List, Optional, Tuple

import statistics as _statistics

from core.alpaca_client import get_alpaca
from core.fmp_client import get_fmp
from core.whale_tracker import get_whale_tracker, score_thirteen_f
from strategies.shared.risk import calc_atr, size_shares
import core.config as cfg

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MIN_PRICE             = 5.0          # minimum price — no penny stocks
MIN_AVG_DOLLAR_VOL    = 5_000_000    # $5M/day minimum — institutional grade
MAX_EXTENSION_MA50    = 0.12         # price must not be > 12% above MA50
MA200_FLOOR           = 0.92         # price must not be < 8% below MA200
RS_50_WINDOW          = 50           # 10-week relative strength window
RS_130_WINDOW         = 130          # 26-week relative strength window
DVOL_TREND_RATIO      = 0.85         # 20d avg dollar vol must be ≥ 85% of 60d avg
EARNINGS_SAFE_DAYS    = 15           # no earnings within 15 days (longer than tactical)
MIN_FUNDAMENTAL_SCORE       = 40     # hard minimum fundamental score (out of 100)
MIN_FUNDAMENTAL_SCORE_EARLY = 55     # higher bar for EARLY_ACCUMULATION archetype
MIN_SCORE             = 65
MIN_RRR               = 2.5
BARS_NEEDED           = 260          # 52 weeks + buffer; enough for MA200 + 130d RS

# Accumulation archetype thresholds
BASE_MAX_PRICE_TIGHT  = 0.03   # price std dev / mean ≤ 3% → tight consolidation
BASE_MAX_DIST_MA50    = 0.05   # within 5% of MA50 for base archetype
PULLBACK_MIN_DIST     = 0.02   # at least 2% below MA50 for pullback archetype
PULLBACK_MAX_DIST     = 0.10   # no more than 10% below MA50 (not broken)

# Early accumulation archetype thresholds (pre-golden-cross entry)
EARLY_ACCUM_MA50_GAP        = 0.03   # MA50 within 3% below MA200 (converging zone)
EARLY_ACCUM_MIN_DVOL        = 1.15   # stronger dvol trend required for pre-cross entry


class VoyagerScanner:
    """
    Long-horizon institutional accumulation scanner. LONG only.
    Returns opportunities with 6–18 month intended holding period.

    Do not add SHORT logic here. SHORT trades belong in strategies/short_sleeve.py.
    """

    def __init__(self, account_equity: float = 100_000):
        self._alpaca = get_alpaca()
        self._fmp    = get_fmp()
        self._equity = account_equity

    def scan(self, tickers: List[str]) -> List[Dict]:
        # Earnings exclusion list (15-day buffer — longer than tactical strategies)
        earnings_cal  = self._fmp.get_earnings_calendar(days_ahead=EARNINGS_SAFE_DAYS)
        earnings_soon = {e.get("symbol", "").upper() for e in earnings_cal}

        # SPY bars for relative strength calculations and regime context
        spy_bars   = self._alpaca.get_daily_bars("SPY", days=BARS_NEEDED)
        spy_closes = [b["close"] for b in spy_bars]

        # Regime context — computed once per scan, attached to every paper log entry
        spy_ma50  = _statistics.mean(spy_closes[-50:])  if len(spy_closes) >= 50  else None
        spy_ma200 = _statistics.mean(spy_closes[-200:]) if len(spy_closes) >= 200 else None
        spy_above_ma50  = (spy_closes[-1] > spy_ma50)  if spy_ma50  else None
        spy_above_ma200 = (spy_closes[-1] > spy_ma200) if spy_ma200 else None

        vix: Optional[float] = None
        if cfg.VOYAGER_PAPER_LOG:
            try:
                vix = self._fmp.get_vix()
            except Exception:
                pass  # VIX is informational only; don't block signals

        # Prefetch fundamentals for the entire universe once before the evaluation
        # loop. get_fundamentals() is cached 24h by the gatekeeper; prefetching here
        # means _evaluate() reads from this dict (O(1) lookup) rather than hitting the
        # gatekeeper DB or FMP on every ticker. Without this, 150 tickers × every
        # 5-minute scan = 150 gatekeeper queries per cycle, and a cache miss per ticker
        # per day hits FMP directly inside the hot evaluation path.
        fund_map: Dict[str, Optional[Dict]] = {}
        for ticker in tickers:
            try:
                fund_map[ticker] = self._fmp.get_fundamentals(ticker)
            except Exception:
                fund_map[ticker] = None

        rej: Dict[str, int] = {}
        opportunities = []
        for ticker in tickers:
            if ticker.upper() in earnings_soon:
                rej["earnings_soon"] = rej.get("earnings_soon", 0) + 1
                continue
            try:
                opp, reason = self._evaluate(ticker, spy_closes, fund_map.get(ticker))
                if opp:
                    opportunities.append(opp)
                    # ── Paper validation logging ──────────────────────────────
                    if cfg.VOYAGER_PAPER_LOG:
                        try:
                            from core.voyager_paper_logger import log_voyager_paper_signal
                            log_voyager_paper_signal(
                                opp,
                                vix=vix,
                                spy_above_ma50=spy_above_ma50,
                                spy_above_ma200=spy_above_ma200,
                            )
                        except Exception as _log_exc:
                            logger.warning("Paper log failed %s: %s", ticker, _log_exc)
                else:
                    rej[reason] = rej.get(reason, 0) + 1
            except Exception as exc:
                logger.debug("VOYAGER eval error %s: %s", ticker, exc)
                rej["exception"] = rej.get("exception", 0) + 1

        opportunities.sort(key=lambda x: x["score"], reverse=True)
        top_rej = sorted(rej.items(), key=lambda x: -x[1])[:7]
        rej_str = "  ".join(f"{r}={n}" for r, n in top_rej)
        logger.info(
            "VOYAGER: %d setup(s) from %d tickers | rejections: %s",
            len(opportunities), len(tickers), rej_str or "none",
        )
        return opportunities

    # ── Per-ticker evaluation ─────────────────────────────────────────────────

    def _evaluate(self, ticker: str, spy_closes: List[float], fund_data: Optional[Dict] = None) -> Tuple[Optional[Dict], str]:
        """Returns (opp_dict, "") on pass or (None, reason_str) on reject."""
        bars = self._alpaca.get_daily_bars(ticker, days=BARS_NEEDED)
        if len(bars) < 60:
            return None, "stale_bars"

        closes  = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]
        opens   = [b["open"]  for b in bars]
        today   = closes[-1]

        # ── Gate 1: Price and dollar volume floor ─────────────────────────────
        if today < MIN_PRICE:
            return None, "price_too_low"

        avg_dvol_20 = statistics.mean(closes[i] * volumes[i] for i in range(-20, 0))
        if avg_dvol_20 < MIN_AVG_DOLLAR_VOL:
            return None, "low_dollar_vol"

        # ── Gate 2: MA structure ──────────────────────────────────────────────
        if len(closes) < 200:
            return None, "stale_bars"

        ma50  = statistics.mean(closes[-50:])
        ma200 = statistics.mean(closes[-200:])

        # Note: MA50 > MA200 (golden cross) is NOT a universal hard gate here.
        # BASE_ACCUMULATION and TREND_PULLBACK archetypes require it, but
        # EARLY_ACCUMULATION allows pre-golden-cross entries when MA50 is
        # converging. The archetype gate (below) rejects any ticker that
        # does not meet at least one archetype's structural conditions.

        # ── Gate 3: Constructive entry zone ───────────────────────────────────
        if today < ma200 * MA200_FLOOR:
            return None, "below_ma200_floor"   # too far below MA200 — broken trend

        extension_ma50 = (today - ma50) / ma50
        if extension_ma50 > MAX_EXTENSION_MA50:
            return None, "too_extended"         # chasing extension — wrong entry

        # ── Gate 4: RS vs SPY over 50 days ───────────────────────────────────
        rs_50 = self._rs(closes, spy_closes, RS_50_WINDOW)
        if rs_50 is None or rs_50 <= 0:
            return None, "weak_rs_50d"          # not outperforming market

        # ── Gate 5: Dollar volume trend (accumulation not fading) ─────────────
        if len(closes) < 60:
            return None, "stale_bars"
        # Baseline window: the 40 bars *preceding* the recent 20-bar window
        # (indices -60..-21).  Non-overlapping with avg_dvol_20 so the ratio
        # is a clean recent-vs-prior comparison.  The earlier len(closes)<60
        # gate guarantees we have all 60 bars; no fallback needed.  The old
        # form (`dvol_bars[:-20]` over `range(-60, 0)`) was numerically
        # identical but mis-named and confused readers in audit.
        avg_dvol_baseline_40 = statistics.mean(
            closes[i] * volumes[i] for i in range(-60, -20)
        )
        dvol_ratio  = (avg_dvol_20 / avg_dvol_baseline_40
                       if avg_dvol_baseline_40 > 0 else 0.0)
        if dvol_ratio < DVOL_TREND_RATIO:
            return None, "dvol_fading"          # institutional interest declining

        # ── Compute up/down day volume ratio ─────────────────────────────────
        # (used in gate below, after archetype is known)
        up_vols, dn_vols = [], []
        for i in range(-20, 0):
            if closes[i] >= opens[i]:
                up_vols.append(volumes[i])
            else:
                dn_vols.append(volumes[i])
        avg_up_vol   = statistics.mean(up_vols) if up_vols else 0
        avg_dn_vol   = statistics.mean(dn_vols) if dn_vols else 1
        up_vol_ratio = avg_up_vol / avg_dn_vol if avg_dn_vol > 0 else 1.0

        # ── Gate 6: Archetype detection (moved before volume-dominance gate) ──
        # Must run before selling_dominates so that TREND_PULLBACK entries are not
        # unfairly rejected. A stock pulling back 2–10% from MA50 naturally shows
        # some elevated down-day volume during the pullback — that's the mechanism
        # creating the entry opportunity, not a quality defect.
        archetype = self._detect_archetype(closes, ma50, ma200, dvol_ratio)
        if archetype is None:
            return None, "no_archetype"         # no valid long-horizon entry pattern

        # ── Gate 7: Volume dominance — archetype-conditional ─────────────────
        # BASE_ACCUMULATION / EARLY_ACCUMULATION: up-day volume must dominate.
        #   A tight constructive base should show accumulation; selling dominance
        #   during a base means distribution, not accumulation.
        # TREND_PULLBACK: relaxed to ≥ 0.8. Some down-volume during the pullback
        #   is structurally expected and acceptable. Only outright panicking
        #   (sellers 25%+ more active than buyers) triggers rejection.
        if archetype == "TREND_PULLBACK":
            if up_vol_ratio < 0.8:
                return None, "selling_dominates"
        else:
            if up_vol_ratio < 1.0:
                return None, "selling_dominates"

        # ── Gate 8: Fundamental quality ───────────────────────────────────────
        fund_score, fund_note = self._fundamental_score(fund_data)
        # EARLY_ACCUMULATION has a higher floor — fundamentals carry more weight
        # when golden-cross structural confirmation is absent.
        min_fund = MIN_FUNDAMENTAL_SCORE_EARLY if archetype == "EARLY_ACCUMULATION" else MIN_FUNDAMENTAL_SCORE
        if fund_score < min_fund:
            return None, "low_fundamental_quality"

        # ── Trade geometry ────────────────────────────────────────────────────
        atr        = calc_atr(bars)
        stop_atr   = today - atr * 1.5
        stop_ma200 = ma200 * 0.97
        stop       = min(stop_atr, stop_ma200)   # structural stop — more room for long hold

        if stop <= 0 or today <= stop:
            return None, "poor_geometry"

        target = today + (today - stop) * MIN_RRR
        rr     = round((target - today) / (today - stop), 2)
        if rr < MIN_RRR:
            return None, "poor_geometry"

        # ── 13F institutional activity (soft layer — cached, optional) ────────
        thirteen_f_activity = self._get_thirteen_f(ticker)
        thirteen_f_pts      = score_thirteen_f(thirteen_f_activity)

        # ── Scoring ───────────────────────────────────────────────────────────
        rs_130 = self._rs(closes, spy_closes, RS_130_WINDOW)
        base_score = self._score(
            rs_50, rs_130, dvol_ratio, up_vol_ratio,
            fund_score, archetype, extension_ma50,
        )
        score = max(0, min(100, base_score + thirteen_f_pts))

        if score < MIN_SCORE:
            return None, "low_score"

        shares = size_shares(self._equity, today, stop)

        # ── Build signal dict ─────────────────────────────────────────────────
        signal: Dict = {
            "strategy":         "VOYAGER",
            "ticker":           ticker,
            "direction":        "LONG",
            "score":            score,
            "base_score":       base_score,
            "entry_price":      round(today, 2),
            "stop_loss":        round(stop, 2),
            "target_price":     round(target, 2),
            "risk_reward":      rr,
            "shares":           shares,
            "archetype":        archetype,
            "rs_50d":           round(rs_50 * 100, 2),
            "rs_130d":          round(rs_130 * 100, 2) if rs_130 is not None else None,
            "dvol_ratio":       round(dvol_ratio, 2),
            "up_vol_ratio":     round(up_vol_ratio, 2),
            "extension_ma50":   round(extension_ma50 * 100, 1),
            "ma50":             round(ma50, 2),
            "ma200":            round(ma200, 2),
            "fund_score":       fund_score,
            "fund_note":        fund_note,
            # 13F confirmation fields (research / enrichment layer)
            "thirteen_f_flow":        thirteen_f_activity.get("net_flow",    "UNKNOWN") if thirteen_f_activity else "UNKNOWN",
            "thirteen_f_confidence":  thirteen_f_activity.get("confidence",  "UNKNOWN") if thirteen_f_activity else "UNKNOWN",
            "thirteen_f_buying":      thirteen_f_activity.get("whales_buying",  0)       if thirteen_f_activity else None,
            "thirteen_f_selling":     thirteen_f_activity.get("whales_selling", 0)       if thirteen_f_activity else None,
            "thirteen_f_quarter":     thirteen_f_activity.get("last_quarter",   None)    if thirteen_f_activity else None,
            "thirteen_f_pts":         thirteen_f_pts,
        }
        return signal, ""

    # ── Archetype detection ───────────────────────────────────────────────────

    def _detect_archetype(
        self,
        closes: List[float],
        ma50: float,
        ma200: float,
        dvol_ratio: float,
    ) -> Optional[str]:
        """
        Detect which long-horizon entry archetype applies.

        BASE_ACCUMULATION  — MA50 > MA200. Price within 5% of MA50, tight price action.
        TREND_PULLBACK     — MA50 > MA200. Price 2–10% below MA50, MA50 still ascending.
        EARLY_ACCUMULATION — MA50 ≤ MA200 but within 3% and rising toward it.
                             Requires stronger accumulation (dvol_ratio ≥ 1.15) and
                             higher fundamental floor (handled in _evaluate).
        Returns None if no archetype confirmed.
        """
        today        = closes[-1]
        dist_ma50    = (today - ma50) / ma50   # negative = below MA50
        golden_cross = ma50 > ma200

        if golden_cross:
            # BASE_ACCUMULATION: price close to MA50, tight consolidation
            if abs(dist_ma50) <= BASE_MAX_DIST_MA50:
                recent = closes[-20:]
                tightness = statistics.stdev(recent) / statistics.mean(recent) if len(recent) > 1 else 1
                if tightness <= BASE_MAX_PRICE_TIGHT:
                    return "BASE_ACCUMULATION"

            # TREND_PULLBACK: pulled back 2–10% below MA50, MA50 still ascending
            if PULLBACK_MIN_DIST <= abs(dist_ma50) <= PULLBACK_MAX_DIST and dist_ma50 < 0:
                if len(closes) >= 80:
                    ma50_30d_ago = statistics.mean(closes[-80:-30])
                    if ma50 > ma50_30d_ago:   # MA50 is still rising
                        return "TREND_PULLBACK"

        else:
            # EARLY_ACCUMULATION: pre-golden-cross convergence zone
            # MA50 within 3% below MA200, MA50 actively rising toward it, strong dvol
            ma50_gap = (ma200 - ma50) / ma200
            if ma50_gap <= EARLY_ACCUM_MA50_GAP:
                if len(closes) >= 70:
                    ma50_20d_ago = statistics.mean(closes[-70:-20])
                    if ma50 > ma50_20d_ago:                  # MA50 rising toward MA200
                        if dvol_ratio >= EARLY_ACCUM_MIN_DVOL:   # stronger accumulation required
                            return "EARLY_ACCUMULATION"

        return None

    # ── Fundamental quality scoring ───────────────────────────────────────────

    def _fundamental_score(self, fund_data: Optional[Dict]) -> Tuple[int, str]:
        """
        Returns (score 0–100, note_string).
        Score < 40 (or < 55 for EARLY_ACCUMULATION) → hard reject in _evaluate.
        Falls back to (50, "no_data") if FMP returns nothing.

        Scoring (105 pts raw, capped at 100):
          Revenue trend (YoY):  30 pts
          Profitability:        25 pts
          Balance sheet D/E:    25 pts + 5 cash bonus
          Gross margin:         15 pts
          Operating cash flow:   5 pts
        """
        data = fund_data
        if not data:
            return 50, "no_data"

        income   = data.get("income",   [])
        balance  = data.get("balance",  [])
        cashflow = data.get("cashflow", [])

        if not income:
            return 50, "no_income_data"

        score  = 0
        notes: List[str] = []
        latest = income[0] if income else {}

        # Revenue trend (30 pts)
        if len(income) >= 4:
            recent_rev = (income[0].get("revenue") or 0) + (income[1].get("revenue") or 0)
            prior_rev  = (income[2].get("revenue") or 0) + (income[3].get("revenue") or 0)
            if prior_rev > 0:
                rev_growth = (recent_rev - prior_rev) / prior_rev
                if rev_growth > 0.10:
                    score += 30; notes.append("rev+")
                elif rev_growth > 0.03:
                    score += 20; notes.append("rev~")
                elif rev_growth > -0.05:
                    score += 10
                else:
                    notes.append("rev-")
            else:
                score += 10
        elif len(income) >= 2:
            q0 = income[0].get("revenue") or 0
            q1 = income[1].get("revenue") or 0
            if q1 > 0 and q0 >= q1 * 0.95:
                score += 15; notes.append("rev~")
            else:
                notes.append("rev-")
        else:
            score += 10

        # Profitability (25 pts)
        net_inc = latest.get("netIncome")     or 0
        op_inc  = latest.get("operatingIncome") or 0
        if net_inc > 0:
            score += 25; notes.append("profitable")
        elif op_inc > 0:
            score += 15; notes.append("op_profitable")
        else:
            notes.append("unprofitable")

        # Balance sheet (25 pts + 5 cash)
        if balance:
            b      = balance[0]
            debt   = b.get("totalDebt")                  or 0
            equity = b.get("totalStockholdersEquity")    or 0
            cash   = b.get("cashAndShortTermInvestments") or 0
            if equity > 0:
                de = debt / equity
                if de < 0.5:    score += 25
                elif de < 1.5:  score += 18
                elif de < 3.0:  score += 10; notes.append("leveraged")
                else:           notes.append("high_leverage")
            elif equity < 0:
                notes.append("negative_equity")
            else:
                score += 8
            if cash > 0:
                score += 5
        else:
            score += 12

        # Gross margin (15 pts)
        gm = latest.get("grossProfitRatio") or 0
        if gm > 0.40:   score += 15
        elif gm > 0.20: score += 9
        elif gm > 0.10: score += 4
        else:           notes.append("low_margin")

        # Operating cash flow bonus (5 pts)
        if cashflow:
            ocf = cashflow[0].get("operatingCashFlow") or 0
            if ocf > 0:
                score += 5; notes.append("ocf+")

        return min(100, score), ("  ".join(notes) or "ok")

    # ── Base scoring (before 13F overlay) ────────────────────────────────────

    def _score(
        self,
        rs_50:          float,
        rs_130:         Optional[float],
        dvol_ratio:     float,
        up_vol_ratio:   float,
        fund_score:     int,
        archetype:      str,
        extension_ma50: float,
    ) -> int:
        """
        Composite score (0–100) before 13F overlay.
        13F adds -5 to +8 on top of this in _evaluate().

        Base: 30
        RS 50d vs SPY:       +15 max
        RS 130d vs SPY:      +8  max
        Dollar volume trend: +10 max
        Up-day vol dominance:+8  max
        Fundamental quality: +14 max (scaled from fund_score)
        Archetype bonus:     +4 to +8
        Entry timing:        +5  max
        Maximum (pre-13F):   ~98
        """
        s = 30  # base

        # RS vs SPY — 10-week leadership
        if rs_50 > 0.10:   s += 15
        elif rs_50 > 0.05: s += 10
        elif rs_50 > 0.02: s += 5

        # RS vs SPY — 26-week leadership
        if rs_130 is not None:
            if rs_130 > 0.10:   s += 8
            elif rs_130 > 0.05: s += 5
            elif rs_130 > 0:    s += 2

        # Dollar volume trend: accumulation building
        if dvol_ratio > 1.20:       s += 10
        elif dvol_ratio > 1.05:     s += 7
        elif dvol_ratio >= 0.90:    s += 4

        # Up-day volume dominance
        if up_vol_ratio > 1.5:      s += 8
        elif up_vol_ratio > 1.2:    s += 5
        elif up_vol_ratio >= 1.0:   s += 2

        # Fundamental quality (scaled)
        s += int(fund_score / 100 * 14)

        # Archetype bonus
        if archetype == "BASE_ACCUMULATION":
            s += 8   # tightest setup, highest timing conviction
        elif archetype == "TREND_PULLBACK":
            s += 5
        elif archetype == "EARLY_ACCUMULATION":
            s += 4   # pre-golden-cross — earliest entry but less structural confirmation

        # Entry timing: closer to MA50 = better entry
        abs_ext = abs(extension_ma50)
        if abs_ext < 0.02:   s += 5
        elif abs_ext < 0.05: s += 3

        return max(0, min(100, s))

    # ── 13F helper ────────────────────────────────────────────────────────────

    def _get_thirteen_f(self, ticker: str) -> Optional[Dict]:
        """
        Fetch 13F institutional activity for a ticker.
        Returns None gracefully if tracker unavailable or any error.
        Results are cached 24h by core/whale_tracker.py.
        """
        try:
            tracker = get_whale_tracker()
            if tracker is None:
                return None
            return tracker.get_institutional_activity(ticker)
        except Exception as exc:
            logger.debug("13F fetch failed for %s: %s", ticker, exc)
            return None

    # ── Relative strength helper ──────────────────────────────────────────────

    def _rs(
        self, ticker_closes: List[float], spy_closes: List[float], window: int
    ) -> Optional[float]:
        """
        RS = ticker return over `window` bars minus SPY return over same window.
        Positive = outperforming market. Returns None if insufficient data.
        """
        if len(ticker_closes) < window + 1 or len(spy_closes) < window + 1:
            return None
        ticker_ret = ticker_closes[-1] / ticker_closes[-(window + 1)] - 1
        spy_ret    = spy_closes[-1]    / spy_closes[-(window + 1)]    - 1
        return ticker_ret - spy_ret
