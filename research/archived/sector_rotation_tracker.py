"""
Sector Rotation Tracker - Institutional Grade

Tracks money flow between sectors in real-time:
- 9 Major sector ETFs
- Relative strength vs SPY
- Rotation patterns (offensive → defensive)
- Money flow direction
- Leading vs lagging identification

Used by Chief Strategists to identify where smart money is flowing
"""

from datetime import datetime
from typing import Dict, List, Tuple
from alpaca_data import AlpacaDataFeed


class SectorRotationTracker:
    """
    Track institutional money flow between sectors
    
    Sectors:
    - OFFENSIVE: XLK (Tech), XLY (Consumer Disc), XLF (Finance)
    - DEFENSIVE: XLP (Staples), XLU (Utilities), XLV (Healthcare)
    - CYCLICAL: XLE (Energy), XLI (Industrials), XLB (Materials)
    
    Rotation Patterns:
    - RISK_ON: Offensive sectors outperforming
    - RISK_OFF: Defensive sectors outperforming
    - EARLY_CYCLE: Cyclicals + Financials leading
    - LATE_CYCLE: Defensive + Utilities leading
    """
    
    # Sector definitions
    SECTORS = {
        'XLK': {'name': 'Technology', 'type': 'OFFENSIVE'},
        'XLY': {'name': 'Consumer Discretionary', 'type': 'OFFENSIVE'},
        'XLF': {'name': 'Financials', 'type': 'OFFENSIVE'},
        'XLP': {'name': 'Consumer Staples', 'type': 'DEFENSIVE'},
        'XLU': {'name': 'Utilities', 'type': 'DEFENSIVE'},
        'XLV': {'name': 'Healthcare', 'type': 'DEFENSIVE'},
        'XLE': {'name': 'Energy', 'type': 'CYCLICAL'},
        'XLI': {'name': 'Industrials', 'type': 'CYCLICAL'},
        'XLB': {'name': 'Materials', 'type': 'CYCLICAL'}
    }
    
    def __init__(self):
        """Initialize sector rotation tracker"""
        self.data_feed = AlpacaDataFeed()
        print("🎨 Sector Rotation Tracker initialized")
        print(f"   Tracking {len(self.SECTORS)} sectors")
    
    def analyze_rotation(self, timeframe: str = '5d') -> Dict:
        """
        Analyze current sector rotation
        
        Args:
            timeframe: '1d', '5d', '1m', '3m'
        
        Returns:
            Complete rotation analysis
        """
        
        # Get days back based on timeframe
        days_map = {'1d': 1, '5d': 5, '1m': 20, '3m': 60}
        days_back = days_map.get(timeframe, 5)
        
        # Get sector performance
        sector_perf = self._get_sector_performance(days_back)
        
        # Get SPY performance for relative strength
        spy_perf = self._get_spy_performance(days_back)
        
        # Calculate relative strength
        relative_strength = self._calculate_relative_strength(sector_perf, spy_perf)
        
        # Identify rotation pattern
        rotation_pattern = self._identify_rotation_pattern(relative_strength)
        
        # Get money flow direction
        money_flow = self._determine_money_flow(relative_strength)
        
        # Identify leaders and laggards
        leaders, laggards = self._identify_leaders_laggards(relative_strength)
        
        # Generate signals
        signals = self._generate_rotation_signals(rotation_pattern, money_flow)
        
        # Rotation strength: average RS magnitude of the top 3 leaders vs bottom 3 laggards
        all_rs = sorted(
            [d['relative_strength'] for d in relative_strength.values()],
            reverse=True
        )
        leader_avg  = sum(all_rs[:3]) / 3 if len(all_rs) >= 3 else 0
        laggard_avg = sum(all_rs[-3:]) / 3 if len(all_rs) >= 3 else 0
        rotation_strength = round(leader_avg - laggard_avg, 2)  # spread = conviction

        return {
            'timeframe': timeframe,
            'spy_performance': spy_perf,
            'sector_performance': sector_perf,
            'relative_strength': relative_strength,
            'rotation_pattern': rotation_pattern,
            'rotation_strength': rotation_strength,
            'money_flow': money_flow,
            'leaders': leaders,
            'laggards': laggards,
            'signals': signals,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    def _get_sector_performance(self, days_back: int) -> Dict:
        """Get performance for all sectors"""
        
        performance = {}
        
        for ticker, info in self.SECTORS.items():
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=days_back + 1)
                
                if bars and len(bars) >= 2:
                    start_price = bars[0]['close']
                    current_price = bars[-1]['close']
                    
                    # Calculate performance
                    perf = ((current_price - start_price) / start_price) * 100
                    
                    # Get current price and volume
                    volume = bars[-1]['volume']
                    
                    performance[ticker] = {
                        'name': info['name'],
                        'type': info['type'],
                        'performance': perf,
                        'price': current_price,
                        'volume': volume
                    }
                else:
                    # Default if no data
                    performance[ticker] = {
                        'name': info['name'],
                        'type': info['type'],
                        'performance': 0.0,
                        'price': 0.0,
                        'volume': 0
                    }
            
            except Exception as e:
                # Handle errors gracefully
                performance[ticker] = {
                    'name': info['name'],
                    'type': info['type'],
                    'performance': 0.0,
                    'price': 0.0,
                    'volume': 0
                }
        
        return performance
    
    def _get_spy_performance(self, days_back: int) -> float:
        """Get SPY performance for comparison"""
        
        try:
            bars = self.data_feed.get_daily_bars('SPY', days_back=days_back + 1)
            
            if bars and len(bars) >= 2:
                start = bars[0]['close']
                current = bars[-1]['close']
                return ((current - start) / start) * 100
        except:
            pass
        
        return 0.0
    
    def _calculate_relative_strength(self, sector_perf: Dict, 
                                     spy_perf: float) -> Dict:
        """Calculate relative strength vs SPY"""
        
        relative = {}
        
        for ticker, data in sector_perf.items():
            sector_return = data['performance']
            
            # Relative strength = Sector return - SPY return
            rel_strength = sector_return - spy_perf
            
            relative[ticker] = {
                'name': data['name'],
                'type': data['type'],
                'absolute_return': sector_return,
                'relative_strength': rel_strength,
                'outperforming': rel_strength > 0
            }
        
        return relative
    
    def _identify_rotation_pattern(self, relative_strength: Dict) -> str:
        """Identify current rotation pattern using count + magnitude weighting.

        Economic cycle phases:
          RISK_ON    — Offensive (Tech/Disc/Finance) leading with meaningful RS
          RISK_OFF   — Defensive (Staples/Utilities/Healthcare) outperforming
          LATE_CYCLE — Energy + Materials both outperforming while Tech fades
                       (commodity inflation, peak expansion before contraction)
          EARLY_CYCLE— Cyclicals + Financials leading (recovery, post-recession)
          MIXED      — No clear pattern
        """

        # Separate RS values by bucket
        offensive_rs = [
            data['relative_strength']
            for data in relative_strength.values()
            if data['type'] == 'OFFENSIVE'
        ]
        defensive_rs = [
            data['relative_strength']
            for data in relative_strength.values()
            if data['type'] == 'DEFENSIVE'
        ]
        cyclical_rs = [
            data['relative_strength']
            for data in relative_strength.values()
            if data['type'] == 'CYCLICAL'
        ]

        # Count outperformers (RS > 0)
        offensive_out = sum(1 for rs in offensive_rs if rs > 0)
        defensive_out = sum(1 for rs in defensive_rs if rs > 0)
        cyclical_out  = sum(1 for rs in cyclical_rs  if rs > 0)

        # Average RS by group — magnitude check prevents noise flips
        offensive_avg = sum(offensive_rs) / len(offensive_rs) if offensive_rs else 0
        defensive_avg = sum(defensive_rs) / len(defensive_rs) if defensive_rs else 0
        cyclical_avg  = sum(cyclical_rs)  / len(cyclical_rs)  if cyclical_rs  else 0

        # LATE_CYCLE: Energy + Materials both outperforming AND Tech fading
        # Commodities bid while growth sectors roll over = peak expansion signal
        xle_rs = relative_strength.get('XLE', {}).get('relative_strength', -99)
        xlb_rs = relative_strength.get('XLB', {}).get('relative_strength', -99)
        xlk_rs = relative_strength.get('XLK', {}).get('relative_strength', 0)
        late_cycle = (xle_rs > 0 and xlb_rs > 0 and xlk_rs < 0
                      and cyclical_out >= 2 and cyclical_avg > offensive_avg)

        if late_cycle:
            return 'LATE_CYCLE'
        elif offensive_out >= 2 and offensive_avg > 0:
            return 'RISK_ON'
        elif defensive_out >= 2 and defensive_avg > 0:
            return 'RISK_OFF'
        elif cyclical_out >= 2 and cyclical_avg > 0:
            return 'EARLY_CYCLE'
        else:
            return 'MIXED'
    
    def _determine_money_flow(self, relative_strength: Dict) -> Dict:
        """Determine where money is flowing"""
        
        # Sort by relative strength
        sorted_sectors = sorted(
            relative_strength.items(),
            key=lambda x: x[1]['relative_strength'],
            reverse=True
        )
        
        # Top 3 = Money flowing IN
        flowing_into = sorted_sectors[:3]
        
        # Bottom 3 = Money flowing OUT
        flowing_out = sorted_sectors[-3:]
        
        # Determine primary flow
        top_types = [s[1]['type'] for s in flowing_into]
        
        if top_types.count('DEFENSIVE') >= 2:
            primary_flow = 'INTO_DEFENSE'
        elif top_types.count('OFFENSIVE') >= 2:
            primary_flow = 'INTO_OFFENSE'
        elif top_types.count('CYCLICAL') >= 2:
            primary_flow = 'INTO_CYCLICALS'
        else:
            primary_flow = 'MIXED'
        
        return {
            'primary_flow': primary_flow,
            'flowing_into': [
                {
                    'ticker': s[0],
                    'name': s[1]['name'],
                    'rel_strength': s[1]['relative_strength']
                }
                for s in flowing_into
            ],
            'flowing_out': [
                {
                    'ticker': s[0],
                    'name': s[1]['name'],
                    'rel_strength': s[1]['relative_strength']
                }
                for s in flowing_out
            ]
        }
    
    def _identify_leaders_laggards(self, relative_strength: Dict) -> Tuple[List, List]:
        """Identify leading and lagging sectors"""
        
        # Sort by relative strength
        sorted_sectors = sorted(
            relative_strength.items(),
            key=lambda x: x[1]['relative_strength'],
            reverse=True
        )
        
        # Leaders (top 3)
        leaders = [
            {
                'ticker': s[0],
                'name': s[1]['name'],
                'type': s[1]['type'],
                'absolute': s[1]['absolute_return'],
                'relative': s[1]['relative_strength']
            }
            for s in sorted_sectors[:3]
        ]
        
        # Laggards (bottom 3)
        laggards = [
            {
                'ticker': s[0],
                'name': s[1]['name'],
                'type': s[1]['type'],
                'absolute': s[1]['absolute_return'],
                'relative': s[1]['relative_strength']
            }
            for s in sorted_sectors[-3:]
        ]
        
        return leaders, laggards
    
    def _generate_rotation_signals(self, pattern: str, money_flow: Dict) -> Dict:
        """Generate actionable signals from rotation"""
        
        signals = {
            'RISK_ON': {
                'tone': 'OFFENSIVE',
                'favor': ['XLK', 'XLY', 'XLF'],
                'avoid': ['XLP', 'XLU'],
                'message': 'Risk appetite high — deploy in growth sectors'
            },
            'RISK_OFF': {
                'tone': 'DEFENSIVE',
                'favor': ['XLP', 'XLU', 'XLV'],
                'avoid': ['XLK', 'XLY'],
                'message': 'Risk aversion — rotate to defensive havens'
            },
            'LATE_CYCLE': {
                'tone': 'PEAK',
                'favor': ['XLE', 'XLB', 'XLV'],
                'avoid': ['XLK', 'XLY'],
                'message': 'Peak expansion — commodities bid, growth fading; trim longs'
            },
            'EARLY_CYCLE': {
                'tone': 'RECOVERY',
                'favor': ['XLI', 'XLB', 'XLF'],
                'avoid': ['XLU', 'XLP'],
                'message': 'Early recovery — cyclicals leading, add risk'
            },
            'MIXED': {
                'tone': 'NEUTRAL',
                'favor': [],
                'avoid': [],
                'message': 'Mixed signals — wait for sector clarity'
            }
        }
        
        base_signal = signals.get(pattern, signals['MIXED'])
        
        # Add money flow context
        base_signal['money_flow'] = money_flow['primary_flow']
        
        return base_signal
    
    def print_rotation_report(self, timeframe: str = '5d'):
        """Print comprehensive rotation report"""
        
        analysis = self.analyze_rotation(timeframe)
        
        print("\n" + "="*70)
        print(f"🎨 SECTOR ROTATION ANALYSIS ({timeframe.upper()})")
        print("="*70)
        
        # Market performance
        print(f"\n📊 MARKET BENCHMARK:")
        print(f"   SPY: {analysis['spy_performance']:+.2f}%")
        
        # Rotation pattern
        print(f"\n🔄 ROTATION PATTERN: {analysis['rotation_pattern']}")
        print(f"💰 MONEY FLOW: {analysis['money_flow']['primary_flow']}")
        
        # Leaders
        print(f"\n🏆 LEADING SECTORS:")
        for leader in analysis['leaders']:
            print(f"   {leader['ticker']:4} {leader['name']:25} "
                  f"{leader['absolute']:+6.2f}% (vs SPY: {leader['relative']:+.2f}%)")
        
        # Laggards
        print(f"\n📉 LAGGING SECTORS:")
        for laggard in analysis['laggards']:
            print(f"   {laggard['ticker']:4} {laggard['name']:25} "
                  f"{laggard['absolute']:+6.2f}% (vs SPY: {laggard['relative']:+.2f}%)")
        
        # Money flow details
        print(f"\n💸 MONEY FLOW DETAILS:")
        print(f"   Flowing INTO:")
        for sector in analysis['money_flow']['flowing_into']:
            print(f"      • {sector['name']:25} ({sector['rel_strength']:+.2f}% vs SPY)")
        
        print(f"   Flowing OUT OF:")
        for sector in analysis['money_flow']['flowing_out']:
            print(f"      • {sector['name']:25} ({sector['rel_strength']:+.2f}% vs SPY)")
        
        # Signals
        print(f"\n💡 STRATEGIC SIGNALS:")
        print(f"   Tone: {analysis['signals']['tone']}")
        print(f"   Message: {analysis['signals']['message']}")
        
        if analysis['signals']['favor']:
            print(f"   Favor: {', '.join(analysis['signals']['favor'])}")
        if analysis['signals']['avoid']:
            print(f"   Avoid: {', '.join(analysis['signals']['avoid'])}")
        
        print("\n" + "="*70 + "\n")
        
        return analysis
    
    def get_sector_heatmap_data(self) -> Dict:
        """Get data formatted for heatmap display"""
        
        analysis = self.analyze_rotation('5d')
        
        heatmap = {}
        
        for ticker, data in analysis['relative_strength'].items():
            heatmap[ticker] = {
                'name': data['name'],
                'type': data['type'],
                'performance': data['absolute_return'],
                'rel_strength': data['relative_strength'],
                'color': self._get_color_for_performance(data['relative_strength'])
            }
        
        return heatmap
    
    def _get_color_for_performance(self, rel_strength: float) -> str:
        """Get color code for performance"""
        
        if rel_strength > 1.0:
            return 'STRONG_GREEN'
        elif rel_strength > 0:
            return 'GREEN'
        elif rel_strength > -1.0:
            return 'RED'
        else:
            return 'STRONG_RED'


# Test
if __name__ == "__main__":
    tracker = SectorRotationTracker()
    
    # Test 5-day rotation
    print("\n" + "="*70)
    print("TESTING 5-DAY ROTATION")
    print("="*70)
    tracker.print_rotation_report('5d')
    
    # Test 1-day rotation
    print("\n" + "="*70)
    print("TESTING 1-DAY ROTATION (Today)")
    print("="*70)
    tracker.print_rotation_report('1d')
