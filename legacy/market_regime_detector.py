"""
Market Regime Detector - Institutional Grade

Determines current market environment and phase:
- Bull, Bear, Transitional states
- Risk-on vs Risk-off
- Market phase (expansion, peak, contraction, trough)

Used by Chief Market Strategists to set portfolio tone
"""

from datetime import datetime
from typing import Dict, Tuple
from alpaca_data import AlpacaDataFeed


class MarketRegimeDetector:
    """
    Detect market regime using institutional indicators
    
    Regimes:
    - STRONG_BULL: SPY > 50MA > 200MA, VIX < 15, breadth strong
    - BULL: SPY > 50MA > 200MA, VIX < 20
    - LATE_BULL: SPY > MAs but weakening, VIX rising
    - DISTRIBUTION: Mixed signals, topping pattern
    - BEAR: SPY < 50MA or 200MA, VIX > 20
    - CAPITULATION: Extreme fear, VIX > 30
    - ACCUMULATION: Bottoming, improving breadth
    
    Risk State:
    - RISK_ON: Offensive positioning
    - RISK_OFF: Defensive positioning
    - TRANSITIONAL: Mixed
    """
    
    def __init__(self):
        """Initialize regime detector"""
        self.data_feed = AlpacaDataFeed()
        print("📊 Market Regime Detector initialized")
    
    def detect_regime(self) -> Dict:
        """
        Detect current market regime
        
        Returns:
            {
                'regime': str,
                'risk_state': str,
                'phase': str,
                'trend_strength': int (0-100),
                'signals': Dict,
                'recommendation': str
            }
        """
        
        # Get market data
        spy_data = self._get_spy_analysis()
        vix_data = self._get_vix_analysis()
        sector_data = self._get_sector_leadership()
        
        # Determine regime
        regime = self._determine_regime(spy_data, vix_data, sector_data)
        
        # Determine risk state
        risk_state = self._determine_risk_state(sector_data, vix_data)
        
        # Determine phase
        phase = self._determine_phase(spy_data, vix_data)
        
        # Calculate trend strength
        trend_strength = self._calculate_trend_strength(spy_data)
        
        # Generate recommendation
        recommendation = self._generate_recommendation(regime, risk_state, phase)
        
        return {
            'regime': regime,
            'risk_state': risk_state,
            'phase': phase,
            'trend_strength': trend_strength,
            'spy_vs_50ma': spy_data['vs_50ma'],
            'spy_vs_200ma': spy_data['vs_200ma'],
            'vix_level': vix_data['level'],
            'vix_regime': vix_data['regime'],
            'sector_leadership': sector_data['leadership'],
            'recommendation': recommendation,
            'signals': {
                'spy': spy_data,
                'vix': vix_data,
                'sectors': sector_data
            }
        }
    
    def _get_spy_analysis(self) -> Dict:
        """Analyze SPY position vs moving averages"""
        
        try:
            # Get 250 days of data for MAs
            bars = self.data_feed.get_daily_bars('SPY', days_back=250)
            
            if not bars or len(bars) < 200:
                return self._default_spy_data()
            
            current_price = bars[-1]['close']
            
            # Calculate MAs
            prices_50 = [b['close'] for b in bars[-50:]]
            prices_200 = [b['close'] for b in bars[-200:]]
            
            ma_50 = sum(prices_50) / len(prices_50)
            ma_200 = sum(prices_200) / len(prices_200)
            
            # Calculate distances
            vs_50ma = ((current_price - ma_50) / ma_50) * 100
            vs_200ma = ((current_price - ma_200) / ma_200) * 100
            
            # Trend
            ma_trend = "BULLISH" if ma_50 > ma_200 else "BEARISH"
            
            # Recent momentum
            week_ago = bars[-5]['close']
            momentum = ((current_price - week_ago) / week_ago) * 100
            
            return {
                'price': current_price,
                'ma_50': ma_50,
                'ma_200': ma_200,
                'vs_50ma': vs_50ma,
                'vs_200ma': vs_200ma,
                'ma_trend': ma_trend,
                'momentum': momentum,
                'above_50': current_price > ma_50,
                'above_200': current_price > ma_200
            }
        
        except:
            return self._default_spy_data()
    
    def _classify_vix(self, level: float, bars=None) -> Dict:
        """Classify a VIX level into regime + trend."""
        if level < 15:
            regime = 'CALM'
        elif level < 20:
            regime = 'NORMAL'
        elif level < 25:
            regime = 'ELEVATED'
        elif level < 30:
            regime = 'HIGH'
        else:
            regime = 'EXTREME'

        trend = 'NEUTRAL'
        if bars and len(bars) >= 5:
            week_ago = bars[-5]['close'] if isinstance(bars[-5], dict) else float(bars[-5])
            trend = 'RISING' if level > week_ago else 'FALLING'

        return {'level': level, 'regime': regime, 'trend': trend}

    def _get_vix_analysis(self) -> Dict:
        """Analyze VIX level and trend.

        Source order: FMP /v3/quote/%5EVIX → Alpaca VXX proxy → default.
        yfinance removed.
        """
        # ── Source 1: FMP ─────────────────────────────────────────────────────
        try:
            from core.fmp_client import get_fmp
            fmp = get_fmp()
            current = fmp.get_vix()
            if current and current > 0:
                # Get 5-day SPY bars for trend context
                spy_bars = fmp.get_spy_bars(days=5)
                closes = [{'close': b['close']} for b in spy_bars]
                return self._classify_vix(current, closes)
        except Exception:
            pass

        # ── Source 2: Alpaca VXX proxy ────────────────────────────────────────
        try:
            bars = self.data_feed.get_daily_bars('VXX', days_back=20)
            if bars:
                proxy = bars[-1]['close']
                vix_equiv = max(10.0, min(80.0, proxy * 0.85))
                result = self._classify_vix(vix_equiv, bars)
                result['source'] = 'vxx_proxy'
                return result
        except Exception:
            pass

        return {'level': 20.0, 'regime': 'NORMAL', 'trend': 'NEUTRAL'}
    
    def _get_sector_leadership(self) -> Dict:
        """Analyze which sectors are leading"""
        
        sectors = {
            'XLK': 'Technology',
            'XLF': 'Financials',
            'XLE': 'Energy',
            'XLV': 'Healthcare',
            'XLY': 'Consumer Discretionary',
            'XLP': 'Consumer Staples',
            'XLI': 'Industrials',
            'XLU': 'Utilities',
            'XLB': 'Materials'
        }
        
        sector_performance = {}
        
        try:
            # Get 5-day performance for each sector
            for ticker, name in sectors.items():
                try:
                    bars = self.data_feed.get_daily_bars(ticker, days_back=5)
                    if bars and len(bars) >= 2:
                        week_ago = bars[0]['close']
                        current = bars[-1]['close']
                        perf = ((current - week_ago) / week_ago) * 100
                        sector_performance[ticker] = {
                            'name': name,
                            'performance': perf
                        }
                except:
                    continue
            
            # Determine leadership type
            if sector_performance:
                # Sort by performance
                sorted_sectors = sorted(
                    sector_performance.items(),
                    key=lambda x: x[1]['performance'],
                    reverse=True
                )
                
                # Top 3 leaders
                leaders = sorted_sectors[:3]
                
                # Classify leadership
                top_tickers = [s[0] for s in leaders]
                
                if any(t in top_tickers for t in ['XLK', 'XLY', 'XLF']):
                    leadership = 'OFFENSIVE'  # Growth sectors leading
                elif any(t in top_tickers for t in ['XLP', 'XLU', 'XLV']):
                    leadership = 'DEFENSIVE'  # Defensive sectors leading
                else:
                    leadership = 'MIXED'
                
                return {
                    'leadership': leadership,
                    'top_3': leaders,
                    'bottom_3': sorted_sectors[-3:],
                    'all_sectors': sector_performance
                }
        
        except:
            pass
        
        return {
            'leadership': 'UNKNOWN',
            'top_3': [],
            'bottom_3': [],
            'all_sectors': {}
        }
    
    def _determine_regime(self, spy_data: Dict, vix_data: Dict, 
                         sector_data: Dict) -> str:
        """Determine market regime"""
        
        # Strong Bull: Above both MAs, VIX calm, offensive sectors
        if (spy_data['above_50'] and spy_data['above_200'] and 
            vix_data['level'] < 15 and sector_data['leadership'] == 'OFFENSIVE'):
            return 'STRONG_BULL'
        
        # Bull: Above both MAs, VIX normal
        if spy_data['above_50'] and spy_data['above_200'] and vix_data['level'] < 20:
            return 'BULL'
        
        # Late Bull: Above MAs but VIX rising or defensive rotation
        if (spy_data['above_50'] and spy_data['above_200'] and 
            (vix_data['level'] >= 20 or sector_data['leadership'] == 'DEFENSIVE')):
            return 'LATE_BULL'
        
        # Distribution: Mixed signals
        if spy_data['above_200'] and not spy_data['above_50']:
            return 'DISTRIBUTION'
        
        # Bear: Below key MAs
        if not spy_data['above_200'] or vix_data['level'] > 25:
            return 'BEAR'
        
        # Capitulation: Extreme fear
        if vix_data['level'] > 30:
            return 'CAPITULATION'
        
        return 'TRANSITIONAL'
    
    def _determine_risk_state(self, sector_data: Dict, vix_data: Dict) -> str:
        """Determine risk-on vs risk-off"""
        
        # Clear risk-off: Defensive sectors + elevated VIX
        if sector_data['leadership'] == 'DEFENSIVE' and vix_data['level'] > 20:
            return 'RISK_OFF'
        
        # Clear risk-on: Offensive sectors + calm VIX
        if sector_data['leadership'] == 'OFFENSIVE' and vix_data['level'] < 18:
            return 'RISK_ON'
        
        return 'TRANSITIONAL'
    
    def _determine_phase(self, spy_data: Dict, vix_data: Dict) -> str:
        """Determine market phase"""
        
        # Expansion: Above MAs, momentum positive
        if spy_data['above_50'] and spy_data['momentum'] > 0:
            return 'EXPANSION'
        
        # Peak: Above MAs but momentum negative
        if spy_data['above_200'] and spy_data['momentum'] < 0:
            return 'PEAK'
        
        # Contraction: Below MAs, negative momentum
        if not spy_data['above_50'] and spy_data['momentum'] < 0:
            return 'CONTRACTION'
        
        # Trough: Below MAs but improving
        if not spy_data['above_200'] and spy_data['momentum'] > 0:
            return 'TROUGH'
        
        return 'TRANSITIONAL'
    
    def _calculate_trend_strength(self, spy_data: Dict) -> int:
        """Calculate trend strength 0-100"""
        
        strength = 0
        
        # Above 50MA: +25
        if spy_data['above_50']:
            strength += 25
        
        # Above 200MA: +25
        if spy_data['above_200']:
            strength += 25
        
        # Bullish MA cross: +25
        if spy_data['ma_trend'] == 'BULLISH':
            strength += 25
        
        # Positive momentum: +25
        if spy_data['momentum'] > 0:
            strength += 25
        
        return strength
    
    def _generate_recommendation(self, regime: str, risk_state: str, 
                                 phase: str) -> str:
        """Generate strategic recommendation"""
        
        recommendations = {
            'STRONG_BULL': 'AGGRESSIVE - Full risk exposure, offensive sectors',
            'BULL': 'BULLISH - Standard risk, favor growth',
            'LATE_BULL': 'CAUTIOUS - Reduce exposure, take profits',
            'DISTRIBUTION': 'DEFENSIVE - Raise cash, avoid new longs',
            'BEAR': 'RISK_OFF - Minimal exposure, defensive only',
            'CAPITULATION': 'OPPORTUNISTIC - Prepare to buy extreme fear',
            'TRANSITIONAL': 'NEUTRAL - Wait for clarity'
        }
        
        return recommendations.get(regime, 'NEUTRAL - Monitor closely')
    
    def _default_spy_data(self) -> Dict:
        """Default SPY data if fetch fails"""
        return {
            'price': 690.0,
            'ma_50': 680.0,
            'ma_200': 670.0,
            'vs_50ma': 1.5,
            'vs_200ma': 3.0,
            'ma_trend': 'BULLISH',
            'momentum': 0.0,
            'above_50': True,
            'above_200': True
        }
    
    def print_regime_report(self):
        """Print detailed regime analysis"""
        
        regime_data = self.detect_regime()
        
        print("\n" + "="*70)
        print("📊 MARKET REGIME ANALYSIS")
        print("="*70)
        
        print(f"\n🎯 REGIME: {regime_data['regime']}")
        print(f"📍 RISK STATE: {regime_data['risk_state']}")
        print(f"📊 PHASE: {regime_data['phase']}")
        print(f"💪 TREND STRENGTH: {regime_data['trend_strength']}/100")
        
        print(f"\n📈 SPY ANALYSIS:")
        print(f"   vs 50MA: {regime_data['spy_vs_50ma']:+.2f}%")
        print(f"   vs 200MA: {regime_data['spy_vs_200ma']:+.2f}%")
        
        print(f"\n⚡ VIX ANALYSIS:")
        print(f"   Level: {regime_data['vix_level']:.1f}")
        print(f"   Regime: {regime_data['vix_regime']}")
        
        print(f"\n🎨 SECTOR LEADERSHIP:")
        print(f"   Type: {regime_data['sector_leadership']}")
        
        print(f"\n💡 RECOMMENDATION:")
        print(f"   {regime_data['recommendation']}")
        
        print("\n" + "="*70 + "\n")
        
        return regime_data


# Test
if __name__ == "__main__":
    detector = MarketRegimeDetector()
    detector.print_regime_report()