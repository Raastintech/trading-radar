from alpaca_data import AlpacaDataFeed
from datetime import datetime, timedelta
import statistics

class MarketRegimeAnalyzer:
    """
    Advanced market regime detection
    Determines if market conditions support long positions
    """
    
    def __init__(self):
        self.data_feed = AlpacaDataFeed()
        self._cached = None
        self._cached_time = None
        print("✅ Market Regime Analyzer initialized")

    def _pct_change(self, closes, lookback):
        if not closes or len(closes) < lookback + 1:
            return None
        return (closes[-1] - closes[-1 - lookback]) / closes[-1 - lookback] * 100.0

    def _get_closes(self, ticker, days_back=60):
        bars = self.data_feed.get_daily_bars(ticker, days_back=days_back)
        if not bars or len(bars) < 30:
            return None
        return [b["close"] for b in bars]

    def _rsp_breadth_score(self):
        """
        Breadth score using SPY vs RSP.
        Returns (score_0_100, label, details_dict)
        """
        spy = self._get_closes("SPY", 90)
        rsp = self._get_closes("RSP", 90)

        if not spy or not rsp:
            return (None, "NO_DATA", {"reason": "Missing SPY/RSP bars"})

        # Short + mid lookbacks
        spy_10 = self._pct_change(spy, 10)
        rsp_10 = self._pct_change(rsp, 10)
        spy_20 = self._pct_change(spy, 20)
        rsp_20 = self._pct_change(rsp, 20)

        if None in (spy_10, rsp_10, spy_20, rsp_20):
            return (None, "NO_DATA", {"reason": "Insufficient bars for lookbacks"})

        # Breadth = RSP performance relative to SPY
        rel_10 = rsp_10 - spy_10
        rel_20 = rsp_20 - spy_20

        # Score logic (simple + stable)
        # Positive rel = broad participation
        raw = (rel_10 * 0.6) + (rel_20 * 0.4)

        # Map raw to 0-100 with gentle scaling
        # raw ~ +2% is strong breadth, raw ~ -2% is narrow rally
        score = 50 + (raw * 12.5)  # 2% -> +25 points, -2% -> -25 points
        score = max(0, min(100, score))

        if score >= 65:
            label = "BROAD_BULL"
        elif score <= 35:
            label = "NARROW_RALLY"
        else:
            label = "MIXED"

        return (round(score, 1), label, {
            "spy_10": round(spy_10, 2),
            "rsp_10": round(rsp_10, 2),
            "spy_20": round(spy_20, 2),
            "rsp_20": round(rsp_20, 2),
            "rel_10": round(rel_10, 2),
            "rel_20": round(rel_20, 2),
            "raw": round(raw, 2),
        })
    
    def analyze_spy_trend(self):
        """
        Analyze S&P 500 trend (SPY)
        Primary market barometer
        """
        bars = self.data_feed.get_daily_bars('SPY', days_back=180)

        if not bars or len(bars) < 50:
            return None

        closes = [bar['close'] for bar in bars]
        current_price = closes[-1]

        # Calculate moving averages
        sma_20 = sum(closes[-20:]) / 20
        sma_50 = sum(closes[-50:]) / 50

        # Price relative to MAs
        above_20 = current_price > sma_20
        above_50 = current_price > sma_50

        # MA alignment (bullish when 20 > 50)
        ma_aligned = sma_20 > sma_50

        # Trend strength
        if above_20 and above_50 and ma_aligned:
            trend = "STRONG UPTREND"
            trend_score = 85
        elif above_20 and above_50:
            trend = "UPTREND"
            trend_score = 70
        elif above_20 or above_50:
            trend = "MIXED"
            trend_score = 50
        else:
            trend = "DOWNTREND"
            trend_score = 30

        return {
            'ticker': 'SPY',
            'current_price': round(current_price, 2),
            'sma_20': round(sma_20, 2),
            'sma_50': round(sma_50, 2),
            'trend': trend,
            'trend_score': trend_score,
            'above_20': above_20,
            'above_50': above_50
        }
    
    def analyze_vix(self):
        """
        Analyze VIX (fear gauge)
        High VIX = fear/volatility
        Low VIX = complacency
        """
        
        try:
            # For now, estimate from SPY volatility
            # In production, would query actual VIX
            bars = self.data_feed.get_daily_bars('SPY', days_back=20)
            
            if not bars or len(bars) < 10:
                print("⚠️  Could not fetch VIX proxy data, using fallback...")
                return {
                    'vix_estimate': 0,
                    'vix_regime': "UNKNOWN",
                    'vix_score': 50,
                    'implication': "VIX unavailable - neutral volatility assumption"
                }
            
            closes = [bar['close'] for bar in bars]
            returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100 
                       for i in range(1, len(closes))]
            
            realized_vol = statistics.stdev(returns) if len(returns) > 1 else 0
            
            # Estimate VIX equivalent (rough approximation)
            # VIX typically 10-20 in calm markets, 30+ in fear
            vix_estimate = realized_vol * 15  # Rough scaling
            
            if vix_estimate < 15:
                vix_regime = "LOW"
                vix_score = 85
                implication = "Complacency - Good for longs"
            elif vix_estimate < 25:
                vix_regime = "NORMAL"
                vix_score = 65
                implication = "Normal volatility"
            elif vix_estimate < 35:
                vix_regime = "ELEVATED"
                vix_score = 40
                implication = "Elevated fear - Caution"
            else:
                vix_regime = "EXTREME"
                vix_score = 20
                implication = "Extreme fear - Avoid longs"
            
            return {
                'vix_estimate': round(vix_estimate, 1),
                'vix_regime': vix_regime,
                'vix_score': vix_score,
                'implication': implication
            }
        except Exception as e:
            print(f"⚠️  VIX analysis error: {e}")
            return {
                'vix_estimate': 0,
                'vix_regime': "UNKNOWN",
                'vix_score': 50,
                'implication': "VIX unavailable - neutral volatility assumption"
            }
    
    def analyze_market_breadth(self):
        """
        Analyze market breadth
        What % of stocks are trending up?
        
        Using QQQ (Nasdaq) as proxy
        """
        
        try:
            bars = self.data_feed.get_daily_bars('QQQ', days_back=50)
            
            if not bars or len(bars) < 20:
                print("⚠️  Could not fetch breadth proxy data, using fallback...")
                return {
                    'breadth': "UNKNOWN",
                    'breadth_score': 50,
                    'breadth_pct_estimate': 50,
                    'qqq_above_20': False,
                    'qqq_above_50': False
                }
            
            closes = [bar['close'] for bar in bars]
            
            current_price = closes[-1]
            sma_20 = sum(closes[-20:]) / 20
            sma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma_20
            
            # Simple breadth estimate
            # If QQQ above MAs, assume good breadth
            if current_price > sma_20 and sma_20 > sma_50:
                breadth = "STRONG"
                breadth_score = 80
                breadth_pct = 65  # Estimate
            elif current_price > sma_20:
                breadth = "MODERATE"
                breadth_score = 60
                breadth_pct = 50
            else:
                breadth = "WEAK"
                breadth_score = 35
                breadth_pct = 35
            
            return {
                'breadth': breadth,
                'breadth_score': breadth_score,
                'breadth_pct_estimate': breadth_pct,
                'qqq_above_20': current_price > sma_20,
                'qqq_above_50': current_price > sma_50
            }
        except Exception as e:
            print(f"⚠️  Market breadth error: {e}")
            return {
                'breadth': "UNKNOWN",
                'breadth_score': 50,
                'breadth_pct_estimate': 50,
                'qqq_above_20': False,
                'qqq_above_50': False
            }
    
    def calculate_regime_score(self):
        """
        Composite market regime score (0-100)
        High = favorable for longs
        Low = unfavorable for longs
        """
        if self._cached and self._cached_time and datetime.now() - self._cached_time < timedelta(minutes=5):
            return self._cached
        
        spy = self.analyze_spy_trend()
        vix = self.analyze_vix()
        breadth = self.analyze_market_breadth()

        print("DEBUG bars status:",
              "SPY trend:", "OK" if spy else "MISSING",
              "| VIX:", "OK" if vix else "MISSING",
              "| Breadth:", "OK" if breadth else "MISSING")
        
        if not all([spy, vix, breadth]):
            self._cached = None
            self._cached_time = datetime.now()
            return None
        
        # Weighted composite
        regime_score = (
            spy['trend_score'] * 0.40 +      # 40% weight on SPY trend
            vix['vix_score'] * 0.30 +        # 30% weight on volatility
            breadth['breadth_score'] * 0.30  # 30% weight on breadth
        )
        
        # Determine regime
        if regime_score >= 75:
            regime = "BULL MARKET"
            confidence_adjustment = 0  # No adjustment needed
            recommendation = "Full risk - Market supports longs"
        elif regime_score >= 60:
            regime = "BULLISH"
            confidence_adjustment = 0
            recommendation = "Normal trading - Favorable conditions"
        elif regime_score >= 45:
            regime = "NEUTRAL"
            confidence_adjustment = +5  # Raise thresholds slightly
            recommendation = "Selective trading - Raise confluence threshold +5"
        elif regime_score >= 30:
            regime = "BEARISH"
            confidence_adjustment = +10  # Significantly raise thresholds
            recommendation = "Defensive - Raise confluence threshold +10"
        else:
            regime = "BEAR MARKET"
            confidence_adjustment = +20  # Very strict
            recommendation = "Cash mode - Avoid longs or trade only 70+ scores"
        
        analysis = {
            'regime': regime,
            'regime_score': round(regime_score, 1),
            'confidence_adjustment': confidence_adjustment,
            'recommendation': recommendation,
            'components': {
                'spy': spy,
                'vix': vix,
                'breadth': breadth
            }
        }
        self._cached = analysis
        self._cached_time = datetime.now()
        return analysis
    
    def vote_on_trade(self, ticker):
        """
        Agent voting function for Veto Council
        """
        
        analysis = self.calculate_regime_score()
        
        if not analysis:
            return {
                'vote': 'ABSTAIN',
                'reason': 'Neutral/no edge',
                'score': None,
                'details': {
                    'status': 'NEUTRAL',
                    'volatility': 'NORMAL',
                    'vix_level': None,
                    'atr_pct': None,
                }
            }
        
        regime = analysis['regime']
        score = analysis['regime_score']
        vix = analysis.get('components', {}).get('vix', {})
        vix_regime = str(vix.get('vix_regime') or '').upper()
        if vix_regime in ['ELEVATED', 'EXTREME', 'HIGH']:
            volatility = 'HIGH'
        elif vix_regime == 'LOW':
            volatility = 'LOW'
        else:
            volatility = 'NORMAL'

        if regime in ['BULL MARKET', 'BULLISH']:
            status = 'RISK_ON'
        elif regime in ['BEAR MARKET', 'BEARISH']:
            status = 'RISK_OFF'
        else:
            status = 'NEUTRAL'

        details = {
            'status': status,
            'volatility': volatility,
            'vix_level': vix.get('vix_estimate'),
            'atr_pct': None,
        }
        
        # VETO in bear markets
        if regime == "BEAR MARKET":
            return {
                'vote': 'VETO',
                'reason': f"Bear market regime ({score}/100) - Avoid longs",
                'score': score,
                'hard_veto': True,
                'details': details,
            }

        # --- NEW: Breadth via RSP ---
        rsp_score, rsp_label, rsp_details = self._rsp_breadth_score()
        bullish_regime = regime in ["BULL MARKET", "BULLISH"]

        # If we can't compute breadth, keep existing behavior
        if rsp_score is not None:
            # Breadth-confirmed bullish regime -> approve more often
            if rsp_label == "BROAD_BULL" and bullish_regime:
                return {
                    'vote': 'APPROVE',
                    'reason': f"Bullish regime + broad participation (RSP breadth {rsp_score}/100)",
                    'score': min(95, 70 + (rsp_score - 65)),
                    'details': details,
                }

            # Narrow rally warning -> caution (even if SPY is up)
            if rsp_label == "NARROW_RALLY" and bullish_regime:
                return {
                    'vote': 'CAUTION',
                    'reason': f"Narrow rally risk (RSP breadth {rsp_score}/100) - leadership concentrated",
                    'score': max(40, 60 - (35 - rsp_score)),
                    'details': details,
                }

            # Mixed breadth -> likely abstain unless other signals are strong
            if rsp_label == "MIXED":
                return {
                    'vote': 'ABSTAIN',
                    'reason': f"Breadth mixed (RSP {rsp_score}/100) - no regime edge",
                    'score': None,
                    'details': details,
                }

        # APPROVE in bull markets
        if bullish_regime:
            return {
                'vote': 'APPROVE',
                'reason': f"{regime} regime ({score}/100) - Conditions favorable",
                'score': score,
                'details': details,
            }

        if 45 <= score <= 55:
            return {
                'vote': 'ABSTAIN',
                'reason': 'Neutral/no edge',
                'score': None,
                'details': details,
            }
        
        # CAUTION in neutral/bearish
        return {
            'vote': 'CAUTION',
            'reason': f"{regime} regime ({score}/100) - {analysis['recommendation']}",
            'score': score,
            'details': details,
        }
    
    def display_regime_analysis(self):
        """Display complete market regime analysis"""
        
        print(f"\n{'='*80}")
        print(f"🌍 MARKET REGIME ANALYSIS")
        print(f"{'='*80}")
        
        analysis = self.calculate_regime_score()
        
        if not analysis:
            print("❌ Insufficient data")
            return None
        
        spy = analysis['components']['spy']
        vix = analysis['components']['vix']
        breadth = analysis['components']['breadth']
        
        print(f"\n📊 SPY TREND:")
        print(f"  Current: ${spy['current_price']}")
        print(f"  SMA 20: ${spy['sma_20']} {'✅' if spy['above_20'] else '❌'}")
        print(f"  SMA 50: ${spy['sma_50']} {'✅' if spy['above_50'] else '❌'}")
        print(f"  Trend: {spy['trend']}")
        print(f"  Score: {spy['trend_score']}/100")
        
        print(f"\n⚡ VOLATILITY (VIX Estimate):")
        print(f"  VIX: ~{vix['vix_estimate']}")
        print(f"  Regime: {vix['vix_regime']}")
        print(f"  Score: {vix['vix_score']}/100")
        print(f"  Implication: {vix['implication']}")
        
        print(f"\n📈 MARKET BREADTH (QQQ):")
        print(f"  Breadth: {breadth['breadth']}")
        print(f"  Score: {breadth['breadth_score']}/100")
        print(f"  Est. % Above MA: {breadth['breadth_pct_estimate']}%")
        
        # Composite verdict
        score = analysis['regime_score']
        regime = analysis['regime']
        
        if score >= 75:
            emoji = "🟢🟢"
        elif score >= 60:
            emoji = "🟢"
        elif score >= 45:
            emoji = "🟡"
        elif score >= 30:
            emoji = "🟠"
        else:
            emoji = "🔴"
        
        print(f"\n{emoji} MARKET REGIME: {regime}")
        print(f"  Composite Score: {score}/100")
        print(f"  Confluence Adjustment: {analysis['confidence_adjustment']:+d} points")
        print(f"  Recommendation: {analysis['recommendation']}")
        
        # Voting
        vote = self.vote_on_trade("MARKET")
        print(f"\n🗳️  VETO COUNCIL VOTE: {vote['vote']}")
        print(f"  Reason: {vote['reason']}")
        
        print(f"\n{'='*80}\n")
        
        return analysis


def test_market_regime():
    """Test market regime analyzer"""
    
    print("🚀 Testing Market Regime Analyzer...\n")
    
    analyzer = MarketRegimeAnalyzer()
    analyzer.display_regime_analysis()


if __name__ == "__main__":
    test_market_regime()
