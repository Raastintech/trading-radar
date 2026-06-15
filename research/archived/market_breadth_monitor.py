"""
Market Breadth Monitor - Institutional Grade

Measures market health beyond price indices:
- Advance/Decline ratio
- New Highs/New Lows
- % Stocks above 50MA and 200MA
- Breadth divergence detection
- Market health scoring

Used by Chief Strategists to validate trends and spot divergences
(Price up but breadth weak = Warning!)
"""

import math
from datetime import datetime
from typing import Dict, List
from alpaca_data import AlpacaDataFeed
from breadth_utils import calendar_days_for_trading_bars


class MarketBreadthMonitor:
    """
    Monitor market internals and breadth
    
    Key Metrics:
    - Advance/Decline Ratio (bullish if >1.5, bearish if <0.67)
    - New Highs/New Lows (bullish if NH > NL, bearish if NL > NH)
    - % Above 50MA (bullish if >60%, bearish if <40%)
    - % Above 200MA (bullish if >60%, bearish if <40%)
    
    Signals:
    - STRONG: Price and breadth aligned and strong
    - HEALTHY: Both positive, some divergence
    - DIVERGENCE: Price up but breadth weak (WARNING)
    - WEAK: Both deteriorating
    - CAPITULATION: Extreme weakness (potential bottom)
    """
    
    # Sector ETFs — included for MA breadth but EXCLUDED from A/D ratio
    # (ETFs correlate with their component stocks already in the universe → double-counting)
    SECTOR_ETFS = {
        'XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'XLP', 'XLI', 'XLU', 'XLB'
    }
    SECTOR_ETF_ORDER = ['XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'XLP', 'XLI', 'XLU', 'XLB']
    STOCK_BREADTH_WEIGHT = 0.7
    SECTOR_BREADTH_WEIGHT = 0.3

    # Equity breadth sample for institutional-style participation checks.
    STOCK_BREADTH_UNIVERSE = [
        # Mega caps
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA',
        # Large caps
        'BRK.B', 'JPM', 'V', 'JNJ', 'WMT', 'PG', 'MA', 'HD', 'CVX',
        'XOM', 'LLY', 'ABBV', 'MRK', 'PEP', 'KO', 'COST', 'AVGO',
        # Growth tech
        'NFLX', 'AMD', 'CRM', 'ADBE', 'INTC', 'CSCO', 'ORCL',
        # Mid caps
        'UBER', 'ABNB', 'COIN', 'SNOW', 'DDOG', 'NET', 'CRWD',
        'ZS', 'PLTR', 'RBLX', 'U', 'DASH',
        # Industrials
        'BA', 'CAT', 'GE', 'UPS', 'LMT', 'RTX',
        # Finance
        'BAC', 'WFC', 'C', 'GS', 'MS', 'SCHW',
        # Consumer
        'MCD', 'NKE', 'SBUX', 'DIS', 'CMCSA',
        # Healthcare
        'UNH', 'TMO', 'DHR', 'ABT', 'ISRG',
        # Energy
        'SLB', 'EOG', 'COP', 'PSX',
    ]
    # Universe for breadth calculation
    BREADTH_UNIVERSE = STOCK_BREADTH_UNIVERSE + SECTOR_ETF_ORDER
    REQUIRED_HISTORY_BARS = 250
    PRICE_HISTORY_CALENDAR_DAYS = calendar_days_for_trading_bars(REQUIRED_HISTORY_BARS)
    
    def __init__(self):
        """Initialize breadth monitor"""
        self.data_feed = AlpacaDataFeed()
        print("📊 Market Breadth Monitor initialized")
        print(f"   Tracking {len(self.BREADTH_UNIVERSE)} stocks")
    
    def analyze_breadth(self) -> Dict:
        """
        Analyze current market breadth
        
        Returns:
            Complete breadth analysis
        """
        
        # Get stock data
        stock_data = self._get_stock_data()
        
        # Calculate advance/decline
        adv_dec = self._calculate_advance_decline(stock_data)
        
        # Calculate new highs/lows
        high_low = self._calculate_new_highs_lows(stock_data)
        
        # Calculate % above moving averages
        ma_breadth = self._calculate_ma_breadth(stock_data)
        
        # Get SPY for comparison
        spy_performance = self._get_spy_performance()
        
        # Detect divergence
        divergence = self._detect_divergence(spy_performance, adv_dec, ma_breadth)
        
        # Calculate health score
        health_score = self._calculate_health_score(adv_dec, high_low, ma_breadth)
        
        # Generate signal
        signal = self._generate_signal(health_score, divergence)
        
        return {
            'advance_decline': adv_dec,
            'new_highs_lows': high_low,
            'ma_breadth': ma_breadth,
            'spy_performance': spy_performance,
            'divergence': divergence,
            'health_score': health_score,
            'signal': signal,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    
    def _get_stock_data(self) -> Dict:
        """Get data for breadth universe"""
        
        stock_data = {}
        
        for ticker in self.BREADTH_UNIVERSE:
            try:
                # Get recent data
                bars = self.data_feed.get_daily_bars(
                    ticker,
                    days_back=self.PRICE_HISTORY_CALENDAR_DAYS,
                )
                
                if bars and len(bars) >= self.REQUIRED_HISTORY_BARS:
                    current = bars[-1]['close']
                    prev = bars[-2]['close']
                    
                    # Calculate MAs
                    prices_50 = [b['close'] for b in bars[-50:]]
                    prices_200 = [b['close'] for b in bars[-200:]]
                    
                    ma_50 = sum(prices_50) / len(prices_50)
                    ma_200 = sum(prices_200) / len(prices_200)
                    
                    # Find 52-week high/low
                    high_52w = max([b['high'] for b in bars[-self.REQUIRED_HISTORY_BARS:]])
                    low_52w = min([b['low'] for b in bars[-self.REQUIRED_HISTORY_BARS:]])
                    
                    stock_data[ticker] = {
                        'current': current,
                        'prev': prev,
                        'change': current - prev,
                        'change_pct': ((current - prev) / prev) * 100,
                        'ma_50': ma_50,
                        'ma_200': ma_200,
                        'above_50': current > ma_50,
                        'above_200': current > ma_200,
                        'high_52w': high_52w,
                        'low_52w': low_52w,
                        'near_high': current > high_52w * 0.98,  # Within 2% of high
                        'near_low': current < low_52w * 1.02     # Within 2% of low
                    }
            
            except Exception as e:
                # Skip stocks with errors
                continue
        
        return stock_data
    
    def _calculate_advance_decline(self, stock_data: Dict) -> Dict:
        """Calculate advance/decline metrics — individual stocks only (excludes sector ETFs)"""

        advancing = 0
        declining = 0
        unchanged = 0

        for ticker, data in stock_data.items():
            if ticker in self.SECTOR_ETFS:
                continue   # ETFs double-count their underlying components
            if data['change'] > 0:
                advancing += 1
            elif data['change'] < 0:
                declining += 1
            else:
                unchanged += 1
        
        total = advancing + declining + unchanged
        
        # A/D Ratio
        if total == 0:
            ratio = None
        elif declining == 0:
            ratio = math.inf if advancing > 0 else None
        else:
            ratio = advancing / declining
        
        # Interpretation
        if ratio is None:
            status = 'NO_DATA'
        elif ratio > 2.0:
            status = 'VERY_STRONG'
        elif ratio > 1.5:
            status = 'STRONG'
        elif ratio > 1.0:
            status = 'POSITIVE'
        elif ratio > 0.67:
            status = 'NEGATIVE'
        elif ratio > 0.5:
            status = 'WEAK'
        else:
            status = 'VERY_WEAK'
        
        return {
            'advancing': advancing,
            'declining': declining,
            'unchanged': unchanged,
            'total': total,
            'ratio': ratio,
            'status': status,
            'pct_advancing': (advancing / total * 100) if total > 0 else 0
        }
    
    def _calculate_new_highs_lows(self, stock_data: Dict) -> Dict:
        """Calculate new highs and lows"""
        
        new_highs = 0
        new_lows = 0
        
        for ticker, data in stock_data.items():
            if data['near_high']:
                new_highs += 1
            if data['near_low']:
                new_lows += 1
        
        # NH-NL Index
        nh_nl_index = new_highs - new_lows
        
        # Interpretation
        if nh_nl_index > 20:
            status = 'VERY_BULLISH'
        elif nh_nl_index > 10:
            status = 'BULLISH'
        elif nh_nl_index > 0:
            status = 'POSITIVE'
        elif nh_nl_index > -10:
            status = 'NEGATIVE'
        elif nh_nl_index > -20:
            status = 'BEARISH'
        else:
            status = 'VERY_BEARISH'
        
        return {
            'new_highs': new_highs,
            'new_lows': new_lows,
            'nh_nl_index': nh_nl_index,
            'status': status
        }
    
    def _calculate_ma_breadth(self, stock_data: Dict) -> Dict:
        """Calculate composite breadth with separate stock and sector components."""

        stock_component = self._calculate_component_ma_breadth(
            stock_data,
            [ticker for ticker in stock_data.keys() if ticker not in self.SECTOR_ETFS],
        )
        sector_component = self._calculate_component_ma_breadth(
            stock_data,
            [ticker for ticker in self.SECTOR_ETF_ORDER if ticker in stock_data],
        )

        raw_above_50 = stock_component["above_50ma"] + sector_component["above_50ma"]
        raw_above_200 = stock_component["above_200ma"] + sector_component["above_200ma"]
        raw_total = stock_component["total"] + sector_component["total"]

        pct_above_50 = self._weighted_breadth_pct(
            stock_component["pct_above_50ma"],
            sector_component["pct_above_50ma"],
        )
        pct_above_200 = self._weighted_breadth_pct(
            stock_component["pct_above_200ma"],
            sector_component["pct_above_200ma"],
        )
        
        # Interpretation for 50MA
        if pct_above_50 > 70:
            status_50 = 'STRONG'
        elif pct_above_50 > 60:
            status_50 = 'HEALTHY'
        elif pct_above_50 > 50:
            status_50 = 'NEUTRAL'
        elif pct_above_50 > 40:
            status_50 = 'WEAK'
        else:
            status_50 = 'VERY_WEAK'
        
        # Interpretation for 200MA
        if pct_above_200 > 70:
            status_200 = 'STRONG'
        elif pct_above_200 > 60:
            status_200 = 'HEALTHY'
        elif pct_above_200 > 50:
            status_200 = 'NEUTRAL'
        elif pct_above_200 > 40:
            status_200 = 'WEAK'
        else:
            status_200 = 'VERY_WEAK'
        
        return {
            'above_50ma': raw_above_50,
            'above_200ma': raw_above_200,
            'total': raw_total,
            'pct_above_50ma': pct_above_50,
            'pct_above_200ma': pct_above_200,
            'status_50ma': status_50,
            'status_200ma': status_200,
            'stock_component': stock_component,
            'sector_component': sector_component,
            'breadth_model': 'stock_sector_composite_v1',
            'stock_weight': self.STOCK_BREADTH_WEIGHT,
            'sector_weight': self.SECTOR_BREADTH_WEIGHT,
        }

    def _calculate_component_ma_breadth(self, stock_data: Dict, tickers: List[str]) -> Dict:
        tickers = [ticker for ticker in tickers if ticker in stock_data]
        above_50 = 0
        above_200 = 0
        total = len(tickers)

        for ticker in tickers:
            data = stock_data[ticker]
            if data['above_50']:
                above_50 += 1
            if data['above_200']:
                above_200 += 1

        pct_above_50 = (above_50 / total * 100) if total > 0 else None
        pct_above_200 = (above_200 / total * 100) if total > 0 else None

        return {
            'above_50ma': above_50,
            'above_200ma': above_200,
            'total': total,
            'pct_above_50ma': pct_above_50,
            'pct_above_200ma': pct_above_200,
        }

    def _weighted_breadth_pct(self, stock_pct, sector_pct) -> float:
        stock_ok = stock_pct is not None
        sector_ok = sector_pct is not None
        if stock_ok and sector_ok:
            return (
                float(stock_pct) * float(self.STOCK_BREADTH_WEIGHT) +
                float(sector_pct) * float(self.SECTOR_BREADTH_WEIGHT)
            )
        if stock_ok:
            return float(stock_pct)
        if sector_ok:
            return float(sector_pct)
        return 0.0
    
    def _get_spy_performance(self) -> Dict:
        """Get SPY performance for comparison"""
        
        try:
            bars = self.data_feed.get_daily_bars('SPY', days_back=5)
            
            if bars and len(bars) >= 2:
                current = bars[-1]['close']
                week_ago = bars[0]['close']
                
                perf = ((current - week_ago) / week_ago) * 100
                
                direction = 'UP' if perf > 0 else 'DOWN' if perf < 0 else 'FLAT'
                
                return {
                    'performance': perf,
                    'direction': direction
                }
        except:
            pass
        
        return {
            'performance': 0.0,
            'direction': 'UNKNOWN'
        }
    
    def _detect_divergence(self, spy_perf: Dict, adv_dec: Dict, 
                          ma_breadth: Dict) -> Dict:
        """Detect price/breadth divergence"""
        
        spy_direction = spy_perf['direction']
        ratio = adv_dec.get('ratio')

        if ratio is None:
            return {
                'divergence': 'NONE',
                'severity': 'NONE',
                'message': 'Breadth ratio unavailable'
            }
        
        # Positive divergence: Price down but breadth improving
        if spy_direction == 'DOWN' and ratio > 1.0:
            divergence = 'POSITIVE'
            severity = 'MINOR'
            message = 'Price declining but breadth holding - potential bottom'
        
        # Negative divergence: Price up but breadth weak
        elif spy_direction == 'UP' and ratio < 1.0:
            divergence = 'NEGATIVE'
            
            # Severity based on how weak breadth is
            if ma_breadth['pct_above_50ma'] < 40:
                severity = 'SEVERE'
                message = 'Price rising but breadth very weak - major warning!'
            elif ma_breadth['pct_above_50ma'] < 50:
                severity = 'MODERATE'
                message = 'Price rising but breadth weak - caution'
            else:
                severity = 'MINOR'
                message = 'Price rising but breadth mixed - watch closely'
        
        # No divergence: Price and breadth aligned
        else:
            divergence = 'NONE'
            severity = 'NONE'
            message = 'Price and breadth aligned'
        
        return {
            'divergence': divergence,
            'severity': severity,
            'message': message
        }
    
    def _calculate_health_score(self, adv_dec: Dict, high_low: Dict, 
                                ma_breadth: Dict) -> int:
        """Calculate overall market health score (0-100)"""
        
        score = 0
        ratio = adv_dec.get('ratio')
        
        # A/D Ratio (0-30 points)
        if ratio is not None and ratio > 2.0:
            score += 30
        elif ratio is not None and ratio > 1.5:
            score += 25
        elif ratio is not None and ratio > 1.0:
            score += 15
        elif ratio is not None and ratio > 0.67:
            score += 5
        
        # New Highs/Lows (0-25 points)
        if high_low['nh_nl_index'] > 20:
            score += 25
        elif high_low['nh_nl_index'] > 10:
            score += 20
        elif high_low['nh_nl_index'] > 0:
            score += 10
        elif high_low['nh_nl_index'] > -10:
            score += 5
        
        # % Above 50MA (0-25 points)
        if ma_breadth['pct_above_50ma'] > 70:
            score += 25
        elif ma_breadth['pct_above_50ma'] > 60:
            score += 20
        elif ma_breadth['pct_above_50ma'] > 50:
            score += 12
        elif ma_breadth['pct_above_50ma'] > 40:
            score += 5
        
        # % Above 200MA (0-20 points)
        if ma_breadth['pct_above_200ma'] > 70:
            score += 20
        elif ma_breadth['pct_above_200ma'] > 60:
            score += 15
        elif ma_breadth['pct_above_200ma'] > 50:
            score += 10
        elif ma_breadth['pct_above_200ma'] > 40:
            score += 5
        
        return min(score, 100)
    
    def _generate_signal(self, health_score: int, divergence: Dict) -> Dict:
        """Generate actionable signal from breadth"""
        
        # Base signal from health score
        if health_score >= 80:
            base_signal = 'VERY_HEALTHY'
            action = 'DEPLOY'
            message = 'Broad participation — deploy with conviction'
        elif health_score >= 60:
            base_signal = 'HEALTHY'
            action = 'DEPLOY SELECTIVELY'
            message = 'Good breadth — market supported, stay invested'
        elif health_score >= 40:
            base_signal = 'NEUTRAL'
            action = 'BE SELECTIVE'
            message = 'Mixed breadth — pick the strongest setups only'
        elif health_score >= 20:
            base_signal = 'WEAK'
            action = 'REDUCE EXPOSURE'
            message = 'Weak breadth — trim longs, raise cash'
        else:
            base_signal = 'VERY_WEAK'
            action = 'EXIT LONGS'
            message = 'Very weak breadth — avoid new longs, protect capital'

        # Modify for divergence
        if divergence['divergence'] == 'NEGATIVE' and divergence['severity'] in ['MODERATE', 'SEVERE']:
            action = 'REDUCE EXPOSURE'
            message = f"WARNING: {divergence['message']}"

        elif divergence['divergence'] == 'POSITIVE':
            message = f"OPPORTUNITY: {divergence['message']}"
        
        return {
            'signal': base_signal,
            'action': action,
            'message': message,
            'health_score': health_score
        }
    
    def print_breadth_report(self):
        """Print comprehensive breadth report"""
        
        analysis = self.analyze_breadth()
        
        print("\n" + "="*70)
        print("📊 MARKET BREADTH ANALYSIS")
        print("="*70)
        
        # Overall health
        print(f"\n🏥 MARKET HEALTH: {analysis['signal']['signal']}")
        print(f"   Score: {analysis['health_score']}/100")
        print(f"   Action: {analysis['signal']['action']}")
        
        # Advance/Decline
        print(f"\n📈 ADVANCE/DECLINE:")
        print(f"   Advancing: {analysis['advance_decline']['advancing']}")
        print(f"   Declining: {analysis['advance_decline']['declining']}")
        ratio = analysis['advance_decline']['ratio']
        ratio_label = "N/A" if ratio is None else ("∞" if math.isinf(ratio) else f"{ratio:.2f}")
        print(f"   Ratio: {ratio_label} ({analysis['advance_decline']['status']})")
        print(f"   % Advancing: {analysis['advance_decline']['pct_advancing']:.1f}%")
        
        # New Highs/Lows
        print(f"\n🎯 NEW HIGHS/LOWS:")
        print(f"   New Highs: {analysis['new_highs_lows']['new_highs']}")
        print(f"   New Lows: {analysis['new_highs_lows']['new_lows']}")
        print(f"   NH-NL Index: {analysis['new_highs_lows']['nh_nl_index']:+d} ({analysis['new_highs_lows']['status']})")
        
        # MA Breadth
        print(f"\n📊 MOVING AVERAGE BREADTH:")
        print(f"   Above 50MA: {analysis['ma_breadth']['pct_above_50ma']:.1f}% ({analysis['ma_breadth']['status_50ma']})")
        print(f"   Above 200MA: {analysis['ma_breadth']['pct_above_200ma']:.1f}% ({analysis['ma_breadth']['status_200ma']})")
        
        # Divergence
        print(f"\n⚠️  DIVERGENCE CHECK:")
        print(f"   Status: {analysis['divergence']['divergence']}")
        if analysis['divergence']['divergence'] != 'NONE':
            print(f"   Severity: {analysis['divergence']['severity']}")
            print(f"   Message: {analysis['divergence']['message']}")
        else:
            print(f"   Price and breadth aligned ✓")
        
        # Signal
        print(f"\n💡 RECOMMENDATION:")
        print(f"   {analysis['signal']['message']}")
        
        print("\n" + "="*70 + "\n")
        
        return analysis


# Test
if __name__ == "__main__":
    monitor = MarketBreadthMonitor()
    monitor.print_breadth_report()
