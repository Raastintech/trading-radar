"""
Inter-Market Monitor - Institutional Grade

Tracks global macro relationships between asset classes:
- DXY (Dollar strength)
- TLT (Bonds/Treasuries)
- GLD (Gold - safe haven)
- USO (Oil - energy/inflation)

Classic relationships:
- Dollar UP + Bonds UP = RISK-OFF (flight to safety)
- Dollar DOWN + Stocks UP = RISK-ON (growth seeking)
- Gold UP + Bonds UP = FEAR (safety trade)
- Oil UP + Dollar DOWN = INFLATION (commodity surge)

Used by Chief Strategists to understand global macro forces
"""

from datetime import datetime
from typing import Dict, List, Tuple
from alpaca_data import AlpacaDataFeed


class InterMarketMonitor:
    """
    Monitor inter-market relationships for macro context
    
    Asset Classes:
    - CURRENCIES: DXY (US Dollar Index)
    - BONDS: TLT (20Y Treasury), IEF (7-10Y)
    - COMMODITIES: GLD (Gold), USO (Oil), DBC (Commodities)
    - EQUITIES: SPY (Stocks)
    
    Key Relationships:
    - Risk-On: Stocks↑ Dollar↓ Bonds↓ (growth seeking)
    - Risk-Off: Stocks↓ Dollar↑ Bonds↑ (safety seeking)
    - Stagflation: Stocks↓ Oil↑ Gold↑ (inflation + slow growth)
    - Deflation: Stocks↓ Commodities↓ Bonds↑ (deflationary crash)
    """
    
    # Asset definitions
    ASSETS = {
        'DXY': {'name': 'US Dollar', 'class': 'CURRENCY'},
        'TLT': {'name': '20Y Treasury', 'class': 'BONDS'},
        'GLD': {'name': 'Gold', 'class': 'COMMODITIES'},
        'USO': {'name': 'Oil', 'class': 'COMMODITIES'},
        'IBIT': {'name': 'Bitcoin ETF', 'class': 'CRYPTO'},
        'SPY': {'name': 'S&P 500', 'class': 'EQUITIES'}
    }
    
    def __init__(self):
        """Initialize inter-market monitor"""
        self.data_feed = AlpacaDataFeed()
        print("🌍 Inter-Market Monitor initialized")
        print(f"   Tracking {len(self.ASSETS)} asset classes")
    
    def analyze_inter_market(self, timeframe: str = '5d') -> Dict:
        """
        Analyze inter-market relationships
        
        Args:
            timeframe: '1d', '5d', '1m'
        
        Returns:
            Complete inter-market analysis
        """
        
        # Get days back
        days_map = {'1d': 1, '5d': 5, '1m': 20}
        days_back = days_map.get(timeframe, 5)
        
        # Get asset performance
        performance = self._get_asset_performance(days_back)
        
        # Analyze key relationships
        relationships = self._analyze_relationships(performance)
        
        # Determine macro regime
        macro_regime = self._determine_macro_regime(relationships)
        
        # Identify divergences
        divergences = self._identify_divergences(relationships)
        
        # Generate signals
        signals = self._generate_inter_market_signals(macro_regime, relationships)
        
        return {
            'timeframe': timeframe,
            'performance': performance,
            'relationships': relationships,
            'macro_regime': macro_regime,
            'divergences': divergences,
            'signals': signals,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    def _get_asset_performance(self, days_back: int) -> Dict:
        """Get performance for all assets"""
        
        performance = {}
        
        for ticker, info in self.ASSETS.items():
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=days_back + 1)
                
                if bars and len(bars) >= 2:
                    start = bars[0]['close']
                    current = bars[-1]['close']
                    
                    perf = ((current - start) / start) * 100
                    
                    # Determine direction
                    if perf > 0.5:
                        direction = 'UP'
                    elif perf < -0.5:
                        direction = 'DOWN'
                    else:
                        direction = 'FLAT'
                    
                    performance[ticker] = {
                        'name': info['name'],
                        'class': info['class'],
                        'performance': perf,
                        'direction': direction,
                        'price': current
                    }
                else:
                    # Default if no data
                    performance[ticker] = {
                        'name': info['name'],
                        'class': info['class'],
                        'performance': 0.0,
                        'direction': 'FLAT',
                        'price': 0.0
                    }
            
            except:
                # Handle errors
                performance[ticker] = {
                    'name': info['name'],
                    'class': info['class'],
                    'performance': 0.0,
                    'direction': 'FLAT',
                    'price': 0.0
                }
        
        return performance
    
    def _analyze_relationships(self, performance: Dict) -> Dict:
        """Analyze key inter-market relationships"""
        
        # Extract directions
        spy_dir = performance.get('SPY', {}).get('direction', 'FLAT')
        dxy_dir = performance.get('DXY', {}).get('direction', 'FLAT')
        tlt_dir = performance.get('TLT', {}).get('direction', 'FLAT')
        gld_dir = performance.get('GLD', {}).get('direction', 'FLAT')
        uso_dir = performance.get('USO', {}).get('direction', 'FLAT')
        
        # Extract performance values
        spy_perf = performance.get('SPY', {}).get('performance', 0)
        dxy_perf = performance.get('DXY', {}).get('performance', 0)
        tlt_perf = performance.get('TLT', {}).get('performance', 0)
        gld_perf = performance.get('GLD', {}).get('performance', 0)
        uso_perf = performance.get('USO', {}).get('performance', 0)
        
        relationships = {}
        
        # 1. Dollar vs Stocks (Classic risk-on/off)
        if spy_dir == 'UP' and dxy_dir == 'DOWN':
            relationships['dollar_stocks'] = {
                'pattern': 'RISK_ON',
                'description': 'Stocks up, Dollar down (growth seeking)',
                'strength': abs(spy_perf) + abs(dxy_perf)
            }
        elif spy_dir == 'DOWN' and dxy_dir == 'UP':
            relationships['dollar_stocks'] = {
                'pattern': 'RISK_OFF',
                'description': 'Stocks down, Dollar up (safety seeking)',
                'strength': abs(spy_perf) + abs(dxy_perf)
            }
        else:
            relationships['dollar_stocks'] = {
                'pattern': 'MIXED',
                'description': 'No clear risk-on/off signal',
                'strength': 0
            }
        
        # 2. Bonds vs Stocks (Risk appetite)
        if spy_dir == 'UP' and tlt_dir == 'DOWN':
            relationships['bonds_stocks'] = {
                'pattern': 'RISK_ON',
                'description': 'Stocks up, Bonds down (risk seeking)',
                'strength': abs(spy_perf) + abs(tlt_perf)
            }
        elif spy_dir == 'DOWN' and tlt_dir == 'UP':
            relationships['bonds_stocks'] = {
                'pattern': 'RISK_OFF',
                'description': 'Stocks down, Bonds up (flight to safety)',
                'strength': abs(spy_perf) + abs(tlt_perf)
            }
        else:
            relationships['bonds_stocks'] = {
                'pattern': 'MIXED',
                'description': 'Stocks and bonds moving together',
                'strength': 0
            }
        
        # 3. Gold (Fear gauge)
        if gld_dir == 'UP' and tlt_dir == 'UP':
            relationships['gold'] = {
                'pattern': 'FEAR',
                'description': 'Gold + Bonds both up (extreme fear)',
                'strength': abs(gld_perf) + abs(tlt_perf)
            }
        elif gld_dir == 'UP' and spy_dir == 'DOWN':
            relationships['gold'] = {
                'pattern': 'SAFE_HAVEN',
                'description': 'Gold up, Stocks down (seeking safety)',
                'strength': abs(gld_perf) + abs(spy_perf)
            }
        elif gld_dir == 'DOWN':
            relationships['gold'] = {
                'pattern': 'RISK_ON',
                'description': 'Gold declining (risk appetite)',
                'strength': abs(gld_perf)
            }
        else:
            relationships['gold'] = {
                'pattern': 'NEUTRAL',
                'description': 'Gold neutral',
                'strength': 0
            }
        
        # 4. Oil (Inflation/Growth indicator)
        if uso_dir == 'UP' and spy_dir == 'UP':
            relationships['oil'] = {
                'pattern': 'GROWTH',
                'description': 'Oil + Stocks up (economic growth)',
                'strength': abs(uso_perf) + abs(spy_perf)
            }
        elif uso_dir == 'UP' and spy_dir == 'DOWN':
            relationships['oil'] = {
                'pattern': 'STAGFLATION',
                'description': 'Oil up, Stocks down (stagflation risk)',
                'strength': abs(uso_perf) + abs(spy_perf)
            }
        elif uso_dir == 'DOWN' and spy_dir == 'DOWN':
            relationships['oil'] = {
                'pattern': 'DEFLATION',
                'description': 'Oil + Stocks down (deflation/recession)',
                'strength': abs(uso_perf) + abs(spy_perf)
            }
        else:
            relationships['oil'] = {
                'pattern': 'NEUTRAL',
                'description': 'Oil neutral',
                'strength': 0
            }
        
        return relationships
    
    def _determine_macro_regime(self, relationships: Dict) -> Dict:
        """Determine overall macro regime"""
        
        # Count risk-on vs risk-off signals
        risk_on_signals = 0
        risk_off_signals = 0
        
        for key, rel in relationships.items():
            if rel['pattern'] == 'RISK_ON':
                risk_on_signals += 1
            elif rel['pattern'] in ['RISK_OFF', 'FEAR', 'SAFE_HAVEN']:
                risk_off_signals += 1
        
        # Determine regime
        if risk_on_signals >= 3:
            regime = 'RISK_ON'
            description = 'Strong risk appetite - growth seeking environment'
            tone = 'AGGRESSIVE'
        elif risk_off_signals >= 3:
            regime = 'RISK_OFF'
            description = 'Risk aversion - safety seeking environment'
            tone = 'DEFENSIVE'
        elif risk_off_signals > risk_on_signals:
            regime = 'CAUTIOUS'
            description = 'Mixed signals with defensive bias'
            tone = 'REDUCED_RISK'
        elif risk_on_signals > risk_off_signals:
            regime = 'OPPORTUNISTIC'
            description = 'Mixed signals with growth bias'
            tone = 'SELECTIVE'
        else:
            regime = 'TRANSITIONAL'
            description = 'Unclear macro signals - transitioning'
            tone = 'WAIT'
        
        # Check for special regimes
        gold_pattern = relationships.get('gold', {}).get('pattern')
        oil_pattern = relationships.get('oil', {}).get('pattern')
        
        if oil_pattern == 'STAGFLATION':
            regime = 'STAGFLATION'
            description = 'High inflation + weak growth (stagflation)'
            tone = 'VERY_DEFENSIVE'
        elif oil_pattern == 'DEFLATION':
            regime = 'DEFLATION'
            description = 'Deflationary pressures (recession risk)'
            tone = 'RISK_OFF'
        elif gold_pattern == 'FEAR':
            regime = 'CRISIS'
            description = 'Extreme fear - flight to safety'
            tone = 'CASH'
        
        return {
            'regime': regime,
            'description': description,
            'tone': tone,
            'risk_on_signals': risk_on_signals,
            'risk_off_signals': risk_off_signals
        }
    
    def _identify_divergences(self, relationships: Dict) -> List[Dict]:
        """Identify unusual or concerning divergences"""
        
        divergences = []
        
        # Stocks and Bonds both up (unusual)
        bonds_stocks = relationships.get('bonds_stocks', {})
        if bonds_stocks.get('pattern') == 'MIXED':
            divergences.append({
                'type': 'STOCKS_BONDS_TOGETHER',
                'severity': 'MODERATE',
                'message': 'Stocks and bonds moving together (unusual)',
                'implication': 'May indicate Fed policy shift or regime change'
            })
        
        # Gold up with stocks up (conflicting)
        gold = relationships.get('gold', {})
        if gold.get('pattern') in ['FEAR', 'SAFE_HAVEN']:
            divergences.append({
                'type': 'GOLD_STRENGTH',
                'severity': 'HIGH',
                'message': 'Gold showing strength (fear indicator)',
                'implication': 'Underlying anxiety despite stock prices'
            })
        
        # Stagflation warning
        oil = relationships.get('oil', {})
        if oil.get('pattern') == 'STAGFLATION':
            divergences.append({
                'type': 'STAGFLATION_RISK',
                'severity': 'SEVERE',
                'message': 'Oil rising while stocks falling',
                'implication': 'Stagflation risk (worst case for stocks)'
            })
        
        return divergences
    
    def _generate_inter_market_signals(self, macro_regime: Dict, 
                                       relationships: Dict) -> Dict:
        """Generate actionable signals from inter-market analysis"""
        
        regime = macro_regime['regime']
        
        signals = {
            'RISK_ON': {
                'environment': 'BULLISH',
                'favor': ['Growth stocks', 'Tech', 'Emerging markets'],
                'avoid': ['Bonds', 'Gold', 'Defensive sectors'],
                'message': 'Risk appetite strong - deploy capital'
            },
            'RISK_OFF': {
                'environment': 'BEARISH',
                'favor': ['Bonds', 'Gold', 'Cash', 'Defensive stocks'],
                'avoid': ['Growth stocks', 'High beta', 'Commodities'],
                'message': 'Risk aversion - preserve capital'
            },
            'STAGFLATION': {
                'environment': 'VERY_BEARISH',
                'favor': ['Commodities', 'Gold', 'Energy', 'TIPS'],
                'avoid': ['Bonds', 'Growth stocks', 'Duration'],
                'message': 'Stagflation risk - very defensive'
            },
            'DEFLATION': {
                'environment': 'RECESSION',
                'favor': ['Treasuries', 'Cash', 'Quality stocks'],
                'avoid': ['Commodities', 'Junk bonds', 'Cyclicals'],
                'message': 'Deflationary spiral - maximum safety'
            },
            'CRISIS': {
                'environment': 'PANIC',
                'favor': ['Cash', 'Gold', 'Short-term Treasuries'],
                'avoid': ['Everything else'],
                'message': 'Crisis mode - capital preservation only'
            }
        }
        
        base_signal = signals.get(regime, {
            'environment': 'NEUTRAL',
            'favor': [],
            'avoid': [],
            'message': 'Mixed signals - remain flexible'
        })
        
        # Add macro context
        base_signal['regime'] = regime
        base_signal['tone'] = macro_regime['tone']
        
        return base_signal
    
    def print_inter_market_report(self, timeframe: str = '5d'):
        """Print comprehensive inter-market report"""
        
        analysis = self.analyze_inter_market(timeframe)
        
        print("\n" + "="*70)
        print(f"🌍 INTER-MARKET ANALYSIS ({timeframe.upper()})")
        print("="*70)
        
        # Asset performance
        print(f"\n💹 ASSET CLASS PERFORMANCE:")
        for ticker, data in analysis['performance'].items():
            arrow = '↑' if data['direction'] == 'UP' else '↓' if data['direction'] == 'DOWN' else '→'
            print(f"   {ticker:4} {data['name']:20} {arrow} {data['performance']:+6.2f}%")
        
        # Macro regime
        print(f"\n🌐 MACRO REGIME: {analysis['macro_regime']['regime']}")
        print(f"   {analysis['macro_regime']['description']}")
        print(f"   Tone: {analysis['macro_regime']['tone']}")
        print(f"   Risk-On Signals: {analysis['macro_regime']['risk_on_signals']}")
        print(f"   Risk-Off Signals: {analysis['macro_regime']['risk_off_signals']}")
        
        # Key relationships
        print(f"\n🔗 KEY RELATIONSHIPS:")
        for key, rel in analysis['relationships'].items():
            print(f"   {key.upper().replace('_', ' ')}:")
            print(f"      Pattern: {rel['pattern']}")
            print(f"      {rel['description']}")
        
        # Divergences
        if analysis['divergences']:
            print(f"\n⚠️  DIVERGENCES DETECTED:")
            for div in analysis['divergences']:
                print(f"   • {div['message']}")
                print(f"     Severity: {div['severity']}")
                print(f"     Implication: {div['implication']}")
        else:
            print(f"\n✓ No significant divergences detected")
        
        # Signals
        print(f"\n💡 INTER-MARKET SIGNALS:")
        print(f"   Environment: {analysis['signals']['environment']}")
        print(f"   {analysis['signals']['message']}")
        
        if analysis['signals']['favor']:
            print(f"   Favor: {', '.join(analysis['signals']['favor'])}")
        if analysis['signals']['avoid']:
            print(f"   Avoid: {', '.join(analysis['signals']['avoid'])}")
        
        print("\n" + "="*70 + "\n")
        
        return analysis


# Test
if __name__ == "__main__":
    monitor = InterMarketMonitor()
    
    # Test 5-day analysis
    print("\n" + "="*70)
    print("TESTING 5-DAY INTER-MARKET ANALYSIS")
    print("="*70)
    monitor.print_inter_market_report('5d')
    
    # Test 1-day analysis
    print("\n" + "="*70)
    print("TESTING 1-DAY INTER-MARKET ANALYSIS (Today)")
    print("="*70)
    monitor.print_inter_market_report('1d')