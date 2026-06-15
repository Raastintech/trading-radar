from alpaca_data import AlpacaDataFeed
from datetime import datetime, timedelta
import statistics
import math

class VannaCharmAnalyzer:
    """
    Advanced Options Greeks Analysis
    Vanna: IV/Price relationship (post-earnings predictor)
    Charm: Time decay positioning (Friday drift detector)
    """
    
    def __init__(self):
        self.data_feed = AlpacaDataFeed()
        print("✅ Vanna & Charm Analyzer initialized")
    
    def estimate_vanna_exposure(self, ticker):
        """
        Vanna = dDelta/dIV
        How much dealer hedging changes when volatility changes
        
        HIGH POSITIVE VANNA:
        - IV drops (after earnings) → Price rallies
        - Dealers forced to buy stock to stay hedged
        
        NEGATIVE VANNA:
        - IV drops → Price falls
        - Dealers sell stock
        
        Using simplified model based on recent volatility patterns
        """
        
        # Get recent price data
        bars = self.data_feed.get_daily_bars(ticker, days_back=30)
        if not bars or len(bars) < 20:
            return None
        
        closes = [bar['close'] for bar in bars]
        volumes = [bar['volume'] for bar in bars]
        
        # Calculate realized volatility
        returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100 
                   for i in range(1, len(closes))]
        
        current_vol = statistics.stdev(returns[-10:]) if len(returns) >= 10 else 0
        historical_vol = statistics.stdev(returns[-20:]) if len(returns) >= 20 else 0
        
        # Estimate vanna exposure based on volatility regime
        # High vol → High vanna exposure (more dealer hedging)
        
        if current_vol > historical_vol * 1.3:
            # Elevated volatility = high vanna
            vanna_regime = "HIGH"
            vanna_score = 80
            effect = "IV drop will cause strong rally (dealers buy)"
        elif current_vol < historical_vol * 0.7:
            # Low volatility = low vanna
            vanna_regime = "LOW"
            vanna_score = 30
            effect = "Limited vanna impact"
        else:
            vanna_regime = "NORMAL"
            vanna_score = 50
            effect = "Moderate vanna exposure"
        
        # Check if near earnings (high vol event)
        avg_volume = sum(volumes) / len(volumes)
        recent_volume = sum(volumes[-3:]) / 3
        
        if recent_volume > avg_volume * 1.5:
            near_catalyst = True
            vanna_score += 15  # Boost score near events
        else:
            near_catalyst = False
        
        return {
            'ticker': ticker,
            'vanna_regime': vanna_regime,
            'vanna_score': min(100, vanna_score),
            'current_vol': round(current_vol, 2),
            'historical_vol': round(historical_vol, 2),
            'near_catalyst': near_catalyst,
            'effect': effect,
            'trading_implication': self._get_vanna_strategy(vanna_regime)
        }
    
    def _get_vanna_strategy(self, regime):
        """Get trading strategy based on vanna regime"""
        
        if regime == "HIGH":
            return "LONG favorable - Expect rally when vol normalizes"
        elif regime == "LOW":
            return "Neutral - Limited dealer hedging impact"
        else:
            return "Moderate - Watch for vol changes"
    
    def estimate_charm_exposure(self, ticker):
        """
        Charm = dDelta/dTime
        How dealer hedging changes as options approach expiration
        
        POSITIVE CHARM (Most common):
        - As time passes, dealers unwind PUT hedges
        - = Dealers BUY stock
        - = Friday afternoon drift UP
        
        NEGATIVE CHARM:
        - Dealers unwind CALL hedges
        - = Dealers SELL stock
        - = Downward pressure
        
        Using simplified model based on day-of-week patterns
        """
        
        # Get recent data including today
        bars = self.data_feed.get_daily_bars(ticker, days_back=60)
        if not bars or len(bars) < 30:
            return None
        
        # Analyze day-of-week patterns (Friday drift)
        friday_returns = []
        thursday_returns = []
        
        for i in range(1, len(bars)):
            # Alpaca data includes day of week info
            current_return = ((bars[i]['close'] - bars[i-1]['close']) / 
                            bars[i-1]['close'] * 100)
            
            # Check if Friday (simplified - would need actual date check)
            # For now, look for end-of-week patterns
            if i % 5 == 4:  # Approximate Friday
                friday_returns.append(current_return)
            elif i % 5 == 3:  # Approximate Thursday
                thursday_returns.append(current_return)
        
        # Calculate average drift
        avg_friday_return = (sum(friday_returns) / len(friday_returns) 
                            if friday_returns else 0)
        avg_thursday_return = (sum(thursday_returns) / len(thursday_returns) 
                              if thursday_returns else 0)
        
        # Positive charm = Friday outperforms
        friday_premium = avg_friday_return - avg_thursday_return
        
        if friday_premium > 0.3:
            charm_regime = "POSITIVE"
            charm_score = 75
            effect = "Friday drift UP expected (dealers unwind puts)"
        elif friday_premium < -0.3:
            charm_regime = "NEGATIVE"
            charm_score = 25
            effect = "Friday drift DOWN expected (dealers unwind calls)"
        else:
            charm_regime = "NEUTRAL"
            charm_score = 50
            effect = "No clear time decay pattern"
        
        # Get current day of week
        today = datetime.now().strftime('%A')
        
        # Charm is most impactful Thursday/Friday
        if today in ['Thursday', 'Friday']:
            time_sensitive = True
            if charm_regime == "POSITIVE":
                charm_score += 15
        else:
            time_sensitive = False
        
        return {
            'ticker': ticker,
            'charm_regime': charm_regime,
            'charm_score': min(100, charm_score),
            'friday_premium': round(friday_premium, 2),
            'current_day': today,
            'time_sensitive': time_sensitive,
            'effect': effect,
            'trading_implication': self._get_charm_strategy(charm_regime, today)
        }
    
    def _get_charm_strategy(self, regime, day):
        """Get trading strategy based on charm"""
        
        if regime == "POSITIVE":
            if day in ['Thursday', 'Friday']:
                return "LONG BIAS - Enter Thursday, hold through Friday close"
            else:
                return "Wait for Thursday entry"
        elif regime == "NEGATIVE":
            if day in ['Thursday', 'Friday']:
                return "AVOID LONGS - Dealers will sell into close"
            else:
                return "Neutral earlier in week"
        else:
            return "No clear charm signal"
    
    def calculate_greek_composite_score(self, ticker):
        """
        Combine Vanna + Charm for overall Greek favorability
        Returns 0-100 score
        """
        
        vanna = self.estimate_vanna_exposure(ticker)
        charm = self.estimate_charm_exposure(ticker)
        
        if not vanna or not charm:
            # Keep return contract stable: always return a dict
            return {
                'ticker': ticker,
                'greek_score': 50.0,
                'vanna_data': vanna,
                'charm_data': charm,
                'favorable': False,
                'status': 'INSUFFICIENT_DATA',
            }
        
        # Weight: 60% Vanna (more important), 40% Charm
        composite = (vanna['vanna_score'] * 0.6) + (charm['charm_score'] * 0.4)
        
        return {
            'ticker': ticker,
            'greek_score': round(composite, 1),
            'vanna_data': vanna,
            'charm_data': charm,
            'favorable': composite >= 60
        }
    
    def vote_on_trade(self, ticker):
        """
        Agent voting function for Veto Council
        Returns: APPROVE, CAUTION, or VETO
        """
        
        analysis = self.calculate_greek_composite_score(ticker)

        if not analysis or not isinstance(analysis, dict):
            return {
                'vote': 'CAUTION',
                'reason': 'Insufficient data for Greek analysis',
                'score': 50
            }
        if analysis.get('status') == 'INSUFFICIENT_DATA':
            return {
                'vote': 'ABSTAIN',
                'reason': 'Insufficient data for Greek analysis',
                'score': None
            }
        
        score = analysis['greek_score']
        vanna = analysis['vanna_data']
        charm = analysis['charm_data']

        # Strong score overrides conflict concerns.
        if score >= 70:
            return {
                'vote': 'APPROVE',
                'reason': f"Strong Greek support ({score}/100): {vanna['effect']}",
                'score': score
            }

        # Potential conflict (only if score < 70).
        if vanna['vanna_regime'] == "HIGH" and charm['charm_regime'] == "NEGATIVE":
            return {
                'vote': 'CAUTION',
                'reason': f'Potential chop: high vanna + negative charm (score {score})',
                'score': score
            }
        
        # Neutral zone -> no edge
        if 45 <= score <= 55:
            return {
                'vote': 'ABSTAIN',
                'reason': 'Neutral/no edge',
                'score': None
            }

        # CAUTION - non-neutral moderate support
        if score > 55:
            return {
                'vote': 'CAUTION',
                'reason': f"Moderate Greek support ({score}/100)",
                'score': score
            }
        
        # Low score = no edge, not a veto
        return {
            'vote': 'ABSTAIN',
            'reason': f"No Greek edge ({score}/100)",
            'score': None
        }
    
    def display_greek_analysis(self, ticker):
        """Display complete Greek analysis"""
        
        print(f"\n{'='*80}")
        print(f"⚡ VANNA & CHARM ANALYSIS: {ticker}")
        print(f"{'='*80}")
        
        analysis = self.calculate_greek_composite_score(ticker)
        
        if not analysis:
            print("❌ Insufficient data")
            return None
        
        vanna = analysis['vanna_data']
        charm = analysis['charm_data']
        
        print(f"\n📊 VANNA EXPOSURE:")
        print(f"  Regime: {vanna['vanna_regime']}")
        print(f"  Score: {vanna['vanna_score']}/100")
        print(f"  Current Vol: {vanna['current_vol']}%")
        print(f"  Historical Vol: {vanna['historical_vol']}%")
        print(f"  Near Catalyst: {'YES' if vanna['near_catalyst'] else 'NO'}")
        print(f"  Effect: {vanna['effect']}")
        print(f"  Strategy: {vanna['trading_implication']}")
        
        print(f"\n⏰ CHARM (Time Decay):")
        print(f"  Regime: {charm['charm_regime']}")
        print(f"  Score: {charm['charm_score']}/100")
        print(f"  Friday Premium: {charm['friday_premium']:+.2f}%")
        print(f"  Current Day: {charm['current_day']}")
        print(f"  Time Sensitive: {'YES' if charm['time_sensitive'] else 'NO'}")
        print(f"  Effect: {charm['effect']}")
        print(f"  Strategy: {charm['trading_implication']}")
        
        # Composite verdict
        score = analysis['greek_score']
        
        if score >= 70:
            emoji = "🟢"
            verdict = "GREEKS FAVORABLE - Dealer hedging supports move"
        elif score >= 50:
            emoji = "🟡"
            verdict = "GREEKS NEUTRAL - Mixed signals"
        else:
            emoji = "🔴"
            verdict = "GREEKS UNFAVORABLE - Dealers will resist"
        
        print(f"\n{emoji} GREEK COMPOSITE: {score}/100")
        print(f"  {verdict}")
        
        # Voting recommendation
        vote = self.vote_on_trade(ticker)
        print(f"\n🗳️  VETO COUNCIL VOTE: {vote['vote']}")
        print(f"  Reason: {vote['reason']}")
        
        print(f"\n{'='*80}\n")
        
        return analysis


def test_vanna_charm():
    """Test Vanna & Charm analysis"""
    
    print("🚀 Testing Vanna & Charm Analyzer...\n")
    
    analyzer = VannaCharmAnalyzer()
    
    # Test on your current positions
    test_tickers = ["HIMS", "RKLB", "COIN", "OPEN"]
    
    for ticker in test_tickers:
        try:
            analyzer.display_greek_analysis(ticker)
        except Exception as e:
            print(f"❌ Error analyzing {ticker}: {e}\n")


if __name__ == "__main__":
    test_vanna_charm()
