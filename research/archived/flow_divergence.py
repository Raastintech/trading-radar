from alpaca_data import AlpacaDataFeed

class FlowDivergenceDetector:
    """
    Tier-3 Flow Confirmation Agent (Quant-style)

    - APPROVE when flow alignment is strong + confident
    - VETO only on high-confidence traps
    - ABSTAIN when no edge (do not poison the council with fake 50s)

    Fix included:
    - Alpaca get_daily_bars() uses CALENDAR days. We request larger windows
      and then slice the last N trading bars.
    """

    def __init__(self):
        self.data_feed = AlpacaDataFeed()
        print("✅ Flow Divergence Detector initialized")

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------
    def _get_bars(self, ticker: str, min_bars: int, calendar_days: int):
        """
        Fetch bars using larger calendar window to ensure enough TRADING bars.
        """
        bars = self.data_feed.get_daily_bars(ticker, days_back=calendar_days)
        if not bars or len(bars) < min_bars:
            return None
        return bars

    # -------------------------------------------------------------------------
    # OPTIONS FLOW PROXY (continuous)
    # -------------------------------------------------------------------------
    def estimate_options_delta_flow(self, ticker):
        """
        Proxy for options delta flow:
        - Uses volume expansion + multi-day return to infer direction/urgency
        - Returns delta_estimate in [-80, +80], plus confidence
        """
        # Need at least 10 trading bars. Request 45 calendar days to be safe.
        bars = self._get_bars(ticker, min_bars=10, calendar_days=45)
        if not bars:
            return None

        # Use last 10 trading bars for stability
        bars = bars[-10:]

        vols = [b['volume'] for b in bars]
        closes = [b['close'] for b in bars]

        avg_vol = sum(vols[:-1]) / max(1, len(vols[:-1]))
        vol_ratio = vols[-1] / max(1, avg_vol)

        # 5-bar return (since we only have last 10 here)
        # bars[-6] exists because we ensured len(bars) >= 10
        ret = ((closes[-1] - closes[-6]) / closes[-6]) * 100

        if vol_ratio < 1.2:
            flow_type = "QUIET"
            delta = 0
            conf = 0.25
        else:
            delta = int(max(-80, min(80, ret * 10)))  # 1% ~ 10 points
            flow_type = "CALL LEAN" if delta > 20 else "PUT LEAN" if delta < -20 else "MIXED"
            conf = min(1.0, 0.35 + (vol_ratio - 1.2) * 0.4)
            conf = round(max(0.0, conf), 2)

        return {
            'ticker': ticker,
            'flow_type': flow_type,
            'delta_estimate': delta,
            'volume_ratio': round(vol_ratio, 2),
            'price_change_5bar': round(ret, 2),
            'confidence': conf
        }

    # -------------------------------------------------------------------------
    # INSTITUTIONAL TAPE FLOW PROXY (volume-weighted CLV + trend)
    # -------------------------------------------------------------------------
    def estimate_tape_institutional_flow(self, ticker):
        """
        Tape-based institutional flow:
        - CLV (Close Location Value) indicates where price closed within the day's range.
        - Weight CLV by volume to reflect dominance on heavy volume.
        - Combine with trend to label accumulation vs distribution.
        """
        # Need at least 15 trading bars. Request 60 calendar days to be safe.
        bars = self._get_bars(ticker, min_bars=15, calendar_days=60)
        if not bars:
            return None

        # Use last 15 trading bars
        bars = bars[-15:]

        clvs = []
        vols = []

        for b in bars:
            high, low, close = b['high'], b['low'], b['close']
            rng = max(1e-9, high - low)
            clv = ((close - low) - (high - close)) / rng  # [-1, +1]
            clvs.append(clv)
            vols.append(b['volume'])

        vw_clv = sum(c * v for c, v in zip(clvs, vols)) / max(1e-9, sum(vols))

        closes = [b['close'] for b in bars]
        trend = (closes[-1] - closes[0]) / closes[0] * 100

        if vw_clv > 0.15 and trend > 0:
            direction = "ACCUMULATION"
            est = 60
        elif vw_clv < -0.15 and trend < 0:
            direction = "DISTRIBUTION"
            est = -60
        else:
            direction = "NEUTRAL"
            est = 0

        mag = min(1.0, abs(vw_clv) / 0.4)  # 0.4 is very strong
        confidence = round(0.35 + 0.65 * mag, 2)

        return {
            'ticker': ticker,
            'flow_direction': direction,
            'flow_estimate': est,
            'vw_clv': round(vw_clv, 3),
            'trend_15bar': round(trend, 2),
            'confidence': confidence
        }

    # -------------------------------------------------------------------------
    # CONFLICT DETECTION
    # -------------------------------------------------------------------------
    def detect_flow_conflict(self, ticker):
        options = self.estimate_options_delta_flow(ticker)
        tape = self.estimate_tape_institutional_flow(ticker)

        if not options or not tape:
            return None

        net_flow = options['delta_estimate'] + tape['flow_estimate']

        options_bullish = options['delta_estimate'] > 30
        options_bearish = options['delta_estimate'] < -30

        tape_bullish = tape['flow_estimate'] > 30
        tape_bearish = tape['flow_estimate'] < -30

        options_quiet = options['flow_type'] == "QUIET"
        tape_conf = tape.get('confidence', 0.4)

        # 1) True trap detection (high severity)
        if options_bullish and tape_bearish:
            conflict = "BULL TRAP"
            severity = "HIGH"
            recommendation = "AVOID - bullish appetite but tape distributing"

        elif options_bearish and tape_bullish:
            conflict = "BEAR TRAP"
            severity = "HIGH"
            recommendation = "LONG BIAS - bearish appetite but tape accumulating"

        # 2) Agreement = conviction
        elif options_bullish and tape_bullish:
            conflict = "NO CONFLICT"
            severity = "NONE"
            recommendation = "BULLISH CONVICTION - both agree"

        elif options_bearish and tape_bearish:
            conflict = "NO CONFLICT"
            severity = "NONE"
            recommendation = "BEARISH CONVICTION - both agree"

        # 3) NEW: Tape-dominant regimes (options proxy quiet)
        elif options_quiet and tape_bearish and tape_conf >= 0.65:
            conflict = "TAPE DOMINANT BEARISH"
            severity = "MED"
            recommendation = "BEARISH TAPE - institutions distributing"

        elif options_quiet and tape_bullish and tape_conf >= 0.65:
            conflict = "TAPE DOMINANT BULLISH"
            severity = "MED"
            recommendation = "BULLISH TAPE - institutions accumulating"

        else:
            conflict = "MIXED"
            severity = "LOW"
            recommendation = "NEUTRAL - no clear directional agreement"

        # Score map
        if conflict == "NO CONFLICT":
            if net_flow > 80:
                flow_score = 85
            elif net_flow > 40:
                flow_score = 70
            elif net_flow < -80:
                flow_score = 15
            elif net_flow < -40:
                flow_score = 30
            else:
                flow_score = 50

        elif conflict == "TAPE DOMINANT BEARISH":
            flow_score = 35  # bearish bias without options confirmation

        elif conflict == "TAPE DOMINANT BULLISH":
            flow_score = 65  # bullish bias without options confirmation

        elif conflict in ["BULL TRAP", "BEAR TRAP"]:
            flow_score = 50 - min(35, abs(net_flow) * 0.35)

        else:
            flow_score = 50 - min(15, abs(net_flow) * 0.15)

        flow_score = round(max(0, min(100, flow_score)), 1)

        confidence = round((options['confidence'] + tape['confidence']) / 2, 2)

        return {
            'ticker': ticker,
            'options_flow': options,
            'institutional_tape': tape,
            'net_flow': net_flow,
            'conflict': conflict,
            'conflict_severity': severity,
            'flow_score': flow_score,
            'confidence': confidence,
            'recommendation': recommendation
        }

    # -------------------------------------------------------------------------
    # VOTE
    # -------------------------------------------------------------------------
    def vote_on_trade(self, ticker):
        analysis = self.detect_flow_conflict(ticker)

        if not analysis:
            return {'vote': 'ABSTAIN', 'reason': 'Insufficient flow data', 'score': None, 'confidence': 0.0}

        conflict = analysis['conflict']
        severity = analysis['conflict_severity']
        score = analysis['flow_score']
        conf = analysis['confidence']

        if conflict == "BULL TRAP" and severity == "HIGH" and conf >= 0.60:
            return {
                'vote': 'VETO',
                'reason': f"BULL TRAP (conf {conf}) - {analysis['recommendation']}",
                'score': score,
                'confidence': conf,
                'hard_veto': True
            }

        # Soft veto / strong caution for long entries
        if conflict == "TAPE DOMINANT BEARISH" and conf >= 0.55:
            return {
                'vote': 'CAUTION',
                'reason': f"Bearish tape bias (conf {conf}) - wait for reversal/confirmation",
                'score': score,
                'confidence': conf
            }

        if conflict == "NO CONFLICT" and score >= 70 and conf >= 0.55:
            return {
                'vote': 'APPROVE',
                'reason': f"Flow aligned ({score}/100, conf {conf}) - {analysis['recommendation']}",
                'score': score,
                'confidence': conf
            }

        # NEW: Tape dominant bullish can be an early institutional "go" signal
        if conflict == "TAPE DOMINANT BULLISH" and score >= 65 and conf >= 0.50:
            return {
                'vote': 'APPROVE',
                'reason': f"Tape-dominant accumulation ({score}/100, conf {conf})",
                'score': score,
                'confidence': conf
            }

        if 45 <= score <= 55:
            return {
                'vote': 'ABSTAIN',
                'reason': 'Neutral/no edge',
                'score': None,
                'confidence': conf
            }

        if conf < 0.40:
            return {
                'vote': 'ABSTAIN',
                'reason': f"No clear flow edge (score {score}, conf {conf})",
                'score': None,
                'confidence': conf
            }

        return {
            'vote': 'CAUTION',
            'reason': f"Flow {conflict} ({score}/100, conf {conf})",
            'score': score,
            'confidence': conf
        }

    # -------------------------------------------------------------------------
    # DISPLAY
    # -------------------------------------------------------------------------
    def display_flow_analysis(self, ticker):
        print(f"\n{'='*80}")
        print(f"🌊 FLOW DIVERGENCE ANALYSIS: {ticker}")
        print(f"{'='*80}")

        analysis = self.detect_flow_conflict(ticker)
        if not analysis:
            print("❌ Insufficient data")
            return None

        opt = analysis['options_flow']
        tape = analysis['institutional_tape']

        print(f"\n📊 OPTIONS FLOW (proxy):")
        print(f"  Type: {opt['flow_type']}")
        print(f"  Delta Estimate: {opt['delta_estimate']:+d}")
        print(f"  Volume Ratio: {opt['volume_ratio']}x")
        print(f"  5-bar Return: {opt['price_change_5bar']:+.2f}%")
        print(f"  Confidence: {opt['confidence']}")

        print(f"\n🏦 INSTITUTIONAL TAPE (proxy):")
        print(f"  Direction: {tape['flow_direction']}")
        print(f"  Flow Estimate: {tape['flow_estimate']:+d}")
        print(f"  VW-CLV: {tape['vw_clv']:+.3f}")
        print(f"  Trend (15 bars): {tape['trend_15bar']:+.2f}%")
        print(f"  Confidence: {tape['confidence']}")

        print(f"\n⚠️  CONFLICT:")
        print(f"  Conflict: {analysis['conflict']} ({analysis['conflict_severity']})")
        print(f"  Net Flow: {analysis['net_flow']:+d}")
        print(f"  Score: {analysis['flow_score']}/100")
        print(f"  Confidence: {analysis['confidence']}")
        print(f"  Recommendation: {analysis['recommendation']}")

        vote = self.vote_on_trade(ticker)
        print(f"\n🗳️  VOTE: {vote['vote']}")
        print(f"  Reason: {vote['reason']}")
        print(f"{'='*80}\n")
        return analysis


def test_flow_divergence():
    print("🚀 Testing Flow Divergence Detector...\n")
    detector = FlowDivergenceDetector()
    test_tickers = [        "AAPL", "NVDA", "TSLA", "MSFT", "META", "GOOGL", "AMZN", "AMD", "IBIT",
        # === SPACE & DEFENSE (5) ===
        "RKLB", "ASTS", "OKLO", "SMR", "BBAI",
         ]
    for t in test_tickers:
        try:
            detector.display_flow_analysis(t)
        except Exception as e:
            print(f"❌ Error analyzing {t}: {e}\n")


if __name__ == "__main__":
    test_flow_divergence()
