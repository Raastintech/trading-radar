"""
Options Positioning Tracker — Real yfinance chain data.

Replaces all VIX-arithmetic estimates with actual options chain data
from OptionsIntelligence (yfinance, ~15 min delayed, free).

Public interface (analyze_positioning, print_positioning_report) unchanged.
When Tradier is configured in tradier_options_feed.py, swap the
OptionsIntelligence dependency; all downstream logic is feed-agnostic.
"""

from datetime import datetime
from typing import Dict, Optional

from options_intelligence import OptionsIntelligence


class OptionsPositioningTracker:
    """
    Institutional-grade options positioning using real chain data.

    Key metrics — all now sourced from actual OI/volume/IV:
      Put/Call ratio  — real volume + OI per strike
      Max pain        — real OI-weighted minimum-payout level
      IV skew         — real put vs call IV comparison
      Implied move    — real ATM IV × √(DTE/365)
      Dealer position — inferred from real PCR + skew
      GEX regime      — real chain-derived (not VIX proxy)
    """

    def __init__(self):
        self._intel = OptionsIntelligence()
        print("📈 Options Positioning Tracker initialized (real chain data)")

    # ── public ─────────────────────────────────────────────────────────────

    def analyze_positioning(self, ticker: str = "SPY") -> Dict:
        """
        Full positioning analysis for ticker.
        Returns a dict compatible with the original interface plus
        additional real-data fields.
        """
        pcr    = self._intel.get_put_call_ratios(ticker)
        pain   = self._intel.calculate_real_max_pain(ticker)
        skew   = self._intel.get_iv_skew(ticker)
        mv     = self._intel.get_implied_move(ticker)
        gex    = self._intel.estimate_gamma_exposure(ticker)
        price  = self._intel.get_current_price(ticker)

        # ── put/call section ──────────────────────────────────────────────
        if pcr:
            spy_pc = {
                "ratio":                  pcr["pcr_volume"],
                "ratio_oi":               pcr["pcr_oi"],
                "sentiment":              pcr["sentiment_vol"],
                "total_call_vol":         pcr["total_call_vol"],
                "total_put_vol":          pcr["total_put_vol"],
                "source":                 "real_chain",
            }
        else:
            spy_pc = {"ratio": 1.0, "sentiment": "NEUTRAL", "source": "unavailable"}

        # ── max pain section ──────────────────────────────────────────────
        if pain:
            max_pain_out = {
                "price":          pain["price"],
                "distance_pct":   pain["distance_pct"],
                "bias":           pain["bias"],
                "magnet_strength": pain["magnet_strength"],
                "expiry":         pain["expiry"],
                "source":         "real_oi",
            }
        else:
            max_pain_out = {
                "price":          price or 0,
                "distance_pct":   0.0,
                "bias":           "NEUTRAL",
                "magnet_strength": "UNKNOWN",
                "source":         "unavailable",
            }

        # ── gamma exposure section ────────────────────────────────────────
        if gex:
            gamma_out = {
                "regime":      gex["gex_type"],
                "magnitude":   "STRONG" if gex["gex_strength"] > 70 else
                               "MODERATE" if gex["gex_strength"] > 40 else "LOW",
                "description": gex["effect"],
                "pcr_volume":  gex.get("pcr_volume"),
                "skew_pct":    gex.get("skew_pct"),
                "source":      "real_chain",
            }
        else:
            gamma_out = {
                "regime":      "NEUTRAL",
                "magnitude":   "UNKNOWN",
                "description": "Chain data unavailable",
                "source":      "unavailable",
            }

        # ── IV skew section ───────────────────────────────────────────────
        skew_out = skew or {
            "skew_pct": 0.0, "sentiment": "NEUTRAL",
            "put_iv_pct": None, "call_iv_pct": None,
        }

        # ── implied move ──────────────────────────────────────────────────
        mv_out = mv or {}

        # ── dealer position ───────────────────────────────────────────────
        dealer = self._infer_dealer_position(spy_pc, gamma_out)

        # ── composite signal ──────────────────────────────────────────────
        signal = self._generate_positioning_signal(spy_pc, gamma_out, max_pain_out, price or 0)

        return {
            "ticker":          ticker,
            "current_price":   round(price, 2) if price else None,
            "put_call":        spy_pc,
            "gamma_exposure":  gamma_out,
            "max_pain":        max_pain_out,
            "iv_skew":         skew_out,
            "implied_move":    mv_out,
            "dealer_position": dealer,
            "signal":          signal,
            "timestamp":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            # Legacy field aliases kept for backwards compat
            "spy_put_call":    spy_pc,
        }

    def print_positioning_report(self, ticker: str = "SPY"):
        """Print comprehensive positioning report."""
        a = self.analyze_positioning(ticker)
        pc = a["put_call"]
        gx = a["gamma_exposure"]
        mp = a["max_pain"]
        sk = a["iv_skew"]
        mv = a["implied_move"]
        dl = a["dealer_position"]
        sg = a["signal"]

        print(f"\n{'='*70}")
        print(f"📈 OPTIONS POSITIONING — {ticker}  (source: {pc.get('source','?')})")
        print(f"{'='*70}")

        print(f"\n💰 PRICE:  ${a['current_price']}")

        print(f"\n📊 PUT/CALL RATIOS:")
        print(f"   Volume PCR : {pc.get('ratio', '—')}  ({pc.get('sentiment', '—')})")
        print(f"   OI PCR     : {pc.get('ratio_oi', '—')}")
        if pc.get("total_call_vol") is not None:
            print(f"   Call Vol / Put Vol: {pc['total_call_vol']:,} / {pc['total_put_vol']:,}")

        print(f"\n⚡ GAMMA EXPOSURE:")
        print(f"   Regime   : {gx['regime']}  ({gx['magnitude']})")
        print(f"   {gx['description']}")
        if gx.get("pcr_volume"):
            print(f"   PCR Vol  : {gx['pcr_volume']}")
        if gx.get("skew_pct") is not None:
            print(f"   IV Skew  : {gx['skew_pct']:+.2f}pp")

        print(f"\n🎯 MAX PAIN  ({mp.get('expiry','?')}):")
        print(f"   Level    : ${mp['price']}")
        print(f"   Distance : {mp['distance_pct']:+.2f}%  ({mp['bias']})")
        print(f"   Magnet   : {mp['magnet_strength']}")

        if sk.get("put_iv_pct") is not None:
            print(f"\n📉 IV SKEW:")
            print(f"   Put 10%-OTM  IV : {sk['put_iv_pct']}%")
            print(f"   Call 10%-OTM IV : {sk['call_iv_pct']}%")
            print(f"   Skew            : {sk['skew_pct']:+.2f}pp  ({sk['sentiment']})")

        if mv.get("implied_move_pct"):
            print(f"\n📐 IMPLIED MOVE  ({mv.get('expiry','?')}, DTE={mv.get('dte','?')}):")
            print(f"   ATM IV   : {mv['atm_iv']}%")
            print(f"   ± Move   : {mv['implied_move_pct']:.2f}%  (${mv['implied_move_dollar']:.2f})")
            print(f"   Range    : ${mv['downside_target']} — ${mv['upside_target']}")

        print(f"\n🏦 DEALER POSITION:")
        print(f"   Position : {dl['position']}")
        print(f"   Hedging  : {dl['hedging_flow']}")
        print(f"   {dl['description']}")

        print(f"\n💡 SIGNAL:")
        print(f"   {sg['signal']}  →  {sg['action']}")
        print(f"   {sg['message']}")
        print(f"   Confidence: {sg['confidence']}")
        print(f"\n{'='*70}\n")
        return a

    # ── internals ──────────────────────────────────────────────────────────

    def _infer_dealer_position(self, pc: dict, gx: dict) -> dict:
        ratio   = pc.get("ratio") or 1.0
        regime  = gx.get("regime", "NEUTRAL")

        if ratio > 1.3 and regime == "NEGATIVE":
            return {
                "position":     "NET_SHORT",
                "hedging_flow": "DESTABILIZING",
                "description":  "High put buying + short gamma → amplify downside",
            }
        if ratio > 1.3:
            return {
                "position":     "SHORT_PUTS",
                "hedging_flow": "SUPPORT_DIPS",
                "description":  "Elevated puts → dealers buy dips to hedge short puts",
            }
        if ratio is not None and ratio < 0.8 and regime == "POSITIVE":
            return {
                "position":     "NET_LONG",
                "hedging_flow": "STABILIZING",
                "description":  "Low put/call + long gamma → dampen moves",
            }
        if ratio is not None and ratio < 0.8:
            return {
                "position":     "SHORT_CALLS",
                "hedging_flow": "RESIST_RALLIES",
                "description":  "Low PCR → dealers sell rallies to hedge short calls",
            }
        return {
            "position":     "BALANCED",
            "hedging_flow": "NEUTRAL",
            "description":  "Balanced positioning — no strong hedging pressure",
        }

    def _generate_positioning_signal(
        self, pc: dict, gx: dict, mp: dict, current_price: float
    ) -> dict:
        ratio    = pc.get("ratio") or 1.0
        regime   = gx.get("regime", "NEUTRAL")
        dist     = mp.get("distance_pct", 0.0)
        strength = mp.get("magnet_strength", "WEAK")

        if ratio is not None and ratio > 1.3 and regime == "NEGATIVE":
            return {
                "signal": "VOLATILE_DOWNSIDE", "action": "REDUCE_RISK",
                "message": "High PCR + negative gamma → amplified downside risk",
                "confidence": "HIGH",
            }
        if ratio is not None and ratio < 0.8 and regime == "POSITIVE":
            return {
                "signal": "STABLE_UPSIDE", "action": "DEPLOY_CAPITAL",
                "message": "Low PCR + positive gamma → supported grind higher",
                "confidence": "HIGH",
            }
        if abs(dist) < 0.5 and strength == "STRONG":
            return {
                "signal": "MAX_PAIN_MAGNET", "action": "EXPECT_CHOP",
                "message": f"Price near max pain (${mp['price']}) — expect pinning",
                "confidence": "MODERATE",
            }
        if dist > 1.5:
            return {
                "signal": "ABOVE_MAX_PAIN", "action": "WATCH_FOR_PULLBACK",
                "message": f"Price {dist:.1f}% above max pain — gravity risk",
                "confidence": "MODERATE",
            }
        if dist < -1.5:
            return {
                "signal": "BELOW_MAX_PAIN", "action": "WATCH_FOR_BOUNCE",
                "message": f"Price {abs(dist):.1f}% below max pain — upside magnetic pull",
                "confidence": "MODERATE",
            }
        return {
            "signal": "NEUTRAL", "action": "MONITOR",
            "message": "No strong directional bias from options positioning",
            "confidence": "LOW",
        }
