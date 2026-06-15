"""
Voyager Adaptive Universe Builder
Legacy fallback builder for Voyager candidate discovery
"""

from typing import List, Dict, Set
from datetime import datetime
from alpaca_data import AlpacaDataFeed
import logging

logger = logging.getLogger(__name__)


class VoyagerAdaptiveUniverse:
    """
    Legacy adaptive universe builder for Voyager.
    
    PHILOSOPHY:
    1. Start with curated fallback watchlists
    2. Apply PRE-FILTERS (quick checks)
    3. Scan for PATTERNS (bottoming, accumulation, etc.)
    4. Return only stocks that CURRENTLY meet criteria
    5. Re-discover fresh opportunities every scan
    
    This ensures we:
    - Never miss emerging opportunities
    - Don't waste time on dead stocks
    - Adapt to market conditions
    - Find the BEST setups available NOW
    """
    
    def __init__(self, data_feed: AlpacaDataFeed):
        self.data_feed = data_feed
        
        # Universe size
        self.max_initial_universe = 1000  # Start big
        self.target_candidates = 50       # Narrow to best opportunities
        
        print(f"🔍 Voyager legacy fallback universe initialized")
        print(f"   Configured scan ceiling: {self.max_initial_universe} stocks")
        print(f"   Source: curated fallback watchlists")
        print(f"   Target candidates: {self.target_candidates}")
    
    def build_universe(self, scan_type='full') -> Dict:
        """
        Build adaptive universe
        
        Args:
            scan_type: 'full' (scan all 1000) or 'quick' (scan 300 most active)
        
        Returns:
            {
                'long_candidates': [...],
                'short_candidates': [...],
                'stats': {...}
            }
        """
        
        print("\n" + "="*80)
        print("🚀 VOYAGER LEGACY FALLBACK UNIVERSE BUILDER")
        print("="*80 + "\n")
        
        # STEP 1: Get initial universe
        initial_universe = self._get_initial_universe(scan_type)
        print(f"📊 Initial universe: {len(initial_universe)} stocks")
        
        # STEP 2: PRE-FILTER (quick elimination)
        pre_filtered = self._apply_pre_filters(initial_universe)
        eliminated = len(initial_universe) - len(pre_filtered)
        pct = (eliminated / len(initial_universe) * 100) if initial_universe else 0.0
        print(f"✅ Pre-filtered: {len(pre_filtered)} stocks")
        print(f"   Eliminated: {eliminated} ({pct:.1f}%)")
        
        # STEP 3: PATTERN SCANNING (find opportunities)
        candidates = self._scan_for_patterns(pre_filtered)
        
        print(f"\n✅ Total candidates found: {len(candidates['long_candidates']) + len(candidates['short_candidates'])}")
        print(f"   Long opportunities: {len(candidates['long_candidates'])}")
        print(f"   Short opportunities: {len(candidates['short_candidates'])}")
        
        return candidates
    
    def _get_initial_universe(self, scan_type: str) -> List[str]:
        """
        Get initial universe to scan
        
        Returns 300-1000 stocks depending on scan_type
        """
        
        if scan_type == 'quick':
            # Quick scan: Most active 300 stocks
            universe = self._get_most_active_stocks(300)
        else:
            # Full scan: All major stocks
            universe = self._get_comprehensive_universe()
        
        return universe
    
    def _get_comprehensive_universe(self) -> List[str]:
        """
        Get comprehensive universe (500-1000 stocks)
        
        Sources:
        - S&P 500 (large cap)
        - NASDAQ 100 (tech/growth)
        - Russell 2000 leaders (small/mid cap)
        - Sector leaders
        - Recent IPOs (quality)
        """
        
        universe = set()
        
        # Large caps (S&P 500 components)
        sp500 = self._get_sp500_components()
        universe.update(sp500)
        
        # Tech/Growth (NASDAQ 100)
        nasdaq100 = self._get_nasdaq100_components()
        universe.update(nasdaq100)
        
        # Small/Mid cap leaders (Russell 2000 top 200)
        russell_leaders = self._get_russell_leaders()
        universe.update(russell_leaders)
        
        # Sector ETF holdings (sector rotation)
        sector_leaders = self._get_sector_leaders()
        universe.update(sector_leaders)
        
        # Recent quality IPOs (last 2 years, profitable)
        quality_ipos = self._get_quality_ipos()
        universe.update(quality_ipos)
        
        # Remove invalid tickers
        universe = {t for t in universe if self._is_valid_ticker(t)}
        
        return sorted(universe)
    
    def _get_most_active_stocks(self, count: int) -> List[str]:
        """
        Get most active stocks by volume (for quick scans)
        """
        
        # Legacy fallback path: rank the curated seed list by recent volume.
        
        all_stocks = self._get_comprehensive_universe()
        
        # Get volume data
        stocks_with_volume = []

        for ticker in all_stocks[:500]:  # Sample first 500
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=10)

                if bars and len(bars) > 0:
                    avg_volume = sum(b['volume'] for b in bars) / len(bars)
                    stocks_with_volume.append((ticker, avg_volume))

            except:
                continue
        
        # Sort by volume, return top N
        stocks_with_volume.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in stocks_with_volume[:count]]
    
    def _apply_pre_filters(self, universe: List[str]) -> List[str]:
        """
        Apply quick pre-filters to eliminate obvious non-candidates
        
        PRE-FILTERS (fast checks):
        1. Price range: $5 - $1000
        2. Average volume: >500k
        3. Has sufficient data: 60+ bars
        4. Not in severe downtrend: Not >50% below 200-MA
        5. Not penny stock garbage: Real company
        
        These are QUICK checks to eliminate bad stocks BEFORE
        running expensive pattern analysis
        """
        
        print("\n🔍 Applying pre-filters...")
        
        passed = []

        for ticker in universe:
            try:
                # Use 120 calendar days so we reliably get >=60 trading bars.
                bars = self.data_feed.get_daily_bars(ticker, days_back=120)
                
                if not bars or len(bars) < 60:
                    continue  # Insufficient data
                
                # Current metrics
                current_price = bars[-1]['close']
                avg_volume = sum(b['volume'] for b in bars[-20:]) / 20
                
                # Calculate 200-MA
                if len(bars) >= 200:
                    ma_200 = sum(b['close'] for b in bars[-200:]) / 200
                else:
                    ma_200 = sum(b['close'] for b in bars) / len(bars)
                
                distance_from_200ma = ((current_price - ma_200) / ma_200) * 100
                
                # PRE-FILTER 1: Price range
                if current_price < 5 or current_price > 1000:
                    continue
                
                # PRE-FILTER 2: Volume
                if avg_volume < 500_000:
                    continue
                
                # PRE-FILTER 3: Not in death spiral
                if distance_from_200ma < -50:
                    continue  # More than 50% below 200-MA
                
                # PASSED all pre-filters
                passed.append(ticker)
                
            except Exception as e:
                continue
        
        return passed
    
    def _scan_for_patterns(self, candidates: List[str]) -> Dict:
        """
        Scan for actual patterns (bottoming, accumulation, etc.)
        
        PATTERN TYPES:
        
        LONG PATTERNS:
        1. Bottoming (oversold reversal)
        2. Accumulation (smart money buying)
        3. Breakout (52-week high)
        4. Quality distressed (good company beaten down)
        
        SHORT PATTERNS:
        1. Mean reversion from top (extended above 200-MA)
        2. Overextended parabolic (unsustainable move)
        3. Distribution (smart money selling)
        """
        
        print("\n🔍 Scanning for patterns...")
        
        long_candidates = []
        short_candidates = []
        
        stats = {
            'bottoming': 0,
            'accumulation': 0,
            'breakout': 0,
            'quality_distressed': 0,
            'mean_reversion': 0,
            'overextended': 0,
            'distribution': 0
        }
        
        for ticker in candidates:
            try:
                # Need 365 calendar days to guarantee 252 trading bars
                # (required by _is_breakout and _is_mean_reversion_candidate)
                bars = self.data_feed.get_daily_bars(ticker, days_back=365)
                
                if not bars or len(bars) < 60:
                    continue
                
                # Check each pattern
                patterns_found = []
                
                # LONG PATTERNS
                if self._is_bottoming(bars):
                    patterns_found.append('bottoming')
                    stats['bottoming'] += 1
                
                if self._is_accumulation(bars):
                    patterns_found.append('accumulation')
                    stats['accumulation'] += 1
                
                if self._is_breakout(bars):
                    patterns_found.append('breakout')
                    stats['breakout'] += 1
                
                if self._is_quality_distressed(ticker, bars):
                    patterns_found.append('quality_distressed')
                    stats['quality_distressed'] += 1
                
                # If any long pattern found
                if patterns_found:
                    long_candidates.append({
                        'ticker': ticker,
                        'patterns': patterns_found,
                        'current_price': bars[-1]['close']
                    })
                
                # SHORT PATTERNS
                short_patterns = []
                
                if self._is_mean_reversion_candidate(bars):
                    short_patterns.append('mean_reversion')
                    stats['mean_reversion'] += 1
                
                if self._is_overextended(bars):
                    short_patterns.append('overextended')
                    stats['overextended'] += 1
                
                if self._is_distribution(bars):
                    short_patterns.append('distribution')
                    stats['distribution'] += 1
                
                # If any short pattern found
                if short_patterns:
                    short_candidates.append({
                        'ticker': ticker,
                        'patterns': short_patterns,
                        'current_price': bars[-1]['close']
                    })
                
            except Exception as e:
                continue
        
        # Print pattern breakdown
        print("\n📊 Pattern Distribution:")
        print(f"   LONG:")
        print(f"      Bottoming: {stats['bottoming']}")
        print(f"      Accumulation: {stats['accumulation']}")
        print(f"      Breakout: {stats['breakout']}")
        print(f"      Quality Distressed: {stats['quality_distressed']}")
        print(f"   SHORT:")
        print(f"      Mean Reversion: {stats['mean_reversion']}")
        print(f"      Overextended: {stats['overextended']}")
        print(f"      Distribution: {stats['distribution']}")
        
        return {
            'long_candidates': long_candidates,
            'short_candidates': short_candidates,
            'stats': stats
        }
    
    def _is_bottoming(self, bars: List) -> bool:
        """Check for bottoming pattern (oversold reversal)"""
        
        if len(bars) < 20:
            return False
        
        current_price = bars[-1]['close']
        
        # Calculate 20-day low
        low_20 = min(b['low'] for b in bars[-20:])
        
        # Recent reversal from lows
        if current_price > low_20 * 1.05:  # 5% above 20-day low
            # Check if was deeply oversold
            high_20 = max(b['high'] for b in bars[-20:])
            if low_20 < high_20 * 0.90:  # Was down 10%+ from highs
                return True
        
        return False
    
    def _is_accumulation(self, bars: List) -> bool:
        """Check for accumulation (volume increasing on up days)"""
        
        if len(bars) < 20:
            return False
        
        # Compare recent 10 days vs prior 10 days
        recent_bars = bars[-10:]
        prior_bars = bars[-20:-10]
        
        # Volume on up days
        recent_up_volume = sum(b['volume'] for b in recent_bars if b['close'] > b['open'])
        prior_up_volume = sum(b['volume'] for b in prior_bars if b['close'] > b['open'])
        
        # Volume on down days
        recent_down_volume = sum(b['volume'] for b in recent_bars if b['close'] < b['open'])
        prior_down_volume = sum(b['volume'] for b in prior_bars if b['close'] < b['open'])
        
        # Accumulation if:
        # 1. Up volume increasing
        # 2. Down volume stable or decreasing
        
        if recent_up_volume > prior_up_volume * 1.2:  # 20% more volume on up days
            if recent_down_volume <= prior_down_volume:  # Not selling into strength
                return True
        
        return False
    
    def _is_breakout(self, bars: List) -> bool:
        """Check for breakout (near 52-week high)"""
        
        if len(bars) < 252:
            return False
        
        current_price = bars[-1]['close']
        high_52w = max(b['high'] for b in bars[-252:])
        
        # Within 5% of 52-week high
        if current_price >= high_52w * 0.95:
            return True
        
        return False
    
    def _is_quality_distressed(self, ticker: str, bars: List) -> bool:
        """Check if quality company beaten down"""
        
        # This would check fundamentals
        # For now, simple heuristic: down 30%+ from highs but stable volume
        
        if len(bars) < 60:
            return False
        
        current_price = bars[-1]['close']
        high_60 = max(b['high'] for b in bars[-60:])
        
        if current_price < high_60 * 0.70:  # Down 30%+
            # But not in death spiral (volume not spiking)
            avg_volume_recent = sum(b['volume'] for b in bars[-10:]) / 10
            avg_volume_prior = sum(b['volume'] for b in bars[-30:-10]) / 20
            
            if avg_volume_recent < avg_volume_prior * 1.5:  # Not panic selling
                return True
        
        return False
    
    def _is_mean_reversion_candidate(self, bars: List) -> bool:
        """Check if overextended above 200-MA"""
        
        if len(bars) < 200:
            return False
        
        current_price = bars[-1]['close']
        ma_200 = sum(b['close'] for b in bars[-200:]) / 200
        
        distance = ((current_price - ma_200) / ma_200) * 100
        
        # More than 20% above 200-MA
        if distance > 20:
            return True
        
        return False
    
    def _is_overextended(self, bars: List) -> bool:
        """Check for parabolic move (unsustainable)"""
        
        if len(bars) < 20:
            return False
        
        # 20-day gain
        gain = ((bars[-1]['close'] - bars[-20]['close']) / bars[-20]['close']) * 100
        
        # Parabolic if up 30%+ in 20 days
        if gain > 30:
            return True
        
        return False
    
    def _is_distribution(self, bars: List) -> bool:
        """Check for distribution (smart money selling)"""
        
        if len(bars) < 20:
            return False
        
        # Volume on down days increasing
        recent_bars = bars[-10:]
        prior_bars = bars[-20:-10]
        
        recent_down_volume = sum(b['volume'] for b in recent_bars if b['close'] < b['open'])
        prior_down_volume = sum(b['volume'] for b in prior_bars if b['close'] < b['open'])
        
        if recent_down_volume > prior_down_volume * 1.3:  # 30% more volume on down days
            return True
        
        return False
    
    # Helper methods for getting stock lists
    
    def _get_sp500_components(self) -> List[str]:
        """Get S&P 500 component tickers"""
        # Would fetch from API - returning sample for now
        return [
            # Technology
            'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META', 'AVGO', 'ORCL', 'CSCO', 'ADBE', 'CRM',
            'AMD', 'INTC', 'TXN', 'QCOM', 'AMAT', 'ADI', 'MU', 'LRCX', 'KLAC', 'SNPS',
            
            # Healthcare
            'UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'TMO', 'ABT', 'PFE', 'DHR', 'BMY',
            
            # Financial
            'JPM', 'V', 'MA', 'BAC', 'WFC', 'GS', 'MS', 'C', 'SCHW', 'AXP',
            
            # Consumer
            'AMZN', 'TSLA', 'WMT', 'HD', 'MCD', 'NKE', 'COST', 'SBUX', 'TGT', 'LOW',
            
            # Energy
            'XOM', 'CVX', 'COP', 'SLB', 'EOG', 'PXD', 'MPC', 'VLO', 'PSX', 'OXY',
            
            # Industrials  
            'CAT', 'DE', 'GE', 'BA', 'HON', 'UNP', 'RTX', 'LMT', 'UPS', 'MMM',
            
            # ... (would include all 500)
        ]
    
    def _get_nasdaq100_components(self) -> List[str]:
        """Get NASDAQ 100 component tickers"""
        return [
            'AAPL', 'MSFT', 'AMZN', 'NVDA', 'META', 'GOOGL', 'GOOG', 'TSLA', 'AVGO', 'COST',
            'NFLX', 'AMD', 'PEP', 'ADBE', 'CSCO', 'TMUS', 'INTU', 'TXN', 'QCOM', 'CMCSA',
            # ... (would include all 100)
        ]
    
    def _get_russell_leaders(self) -> List[str]:
        """Get Russell 2000 leaders (top 200 by quality)"""
        return [
            'CRWD', 'PLTR', 'COIN', 'RBLX', 'U', 'RIVN', 'SNOW', 'DKNG', 'ZM', 'DOCU',
            'TWLO', 'NET', 'DDOG', 'OKTA', 'ZS', 'PANW', 'FTNT', 'CYBR', 'CHKP', 'S',
            # ... (would include top 200)
        ]
    
    def _get_sector_leaders(self) -> List[str]:
        """Get sector rotation leaders"""
        return [
            # From sector ETFs
            'XLK', 'XLV', 'XLF', 'XLE', 'XLI', 'XLP', 'XLY', 'XLU', 'XLRE', 'XLC', 'XLB',
            # Plus their top holdings
        ]
    
    def _get_quality_ipos(self) -> List[str]:
        """Get quality recent IPOs (last 2 years)"""
        return [
            'COIN', 'RIVN', 'LCID', 'RBLX', 'ABNB', 'DASH', 'SNOW', 'PLTR', 'ARM',
            # ... (quality IPOs from last 2 years)
        ]
    
    def _is_valid_ticker(self, ticker: str) -> bool:
        """Validate ticker format"""

        if not ticker or len(ticker) > 5:
            return False

        # Exclude ALL sector ETFs
        sector_etfs = {
            'SPY', 'QQQ', 'IWM', 'DIA',   # Index ETFs
            'VXX', 'UVXY', 'SVXY',          # Volatility products
            'XLK', 'XLV', 'XLF', 'XLE',    # Sector SPDR ETFs
            'XLI', 'XLP', 'XLY', 'XLU',    # More sector ETFs
            'XLRE', 'XLC', 'XLB', 'XBI',   # Even more
        }

        if ticker in sector_etfs:
            return False

        # Exclude preferred shares
        if '-' in ticker or '.' in ticker:
            return False

        return True


# Quick test function
if __name__ == "__main__":
    from alpaca_data import AlpacaDataFeed
    
    data_feed = AlpacaDataFeed()
    builder = VoyagerAdaptiveUniverse(data_feed)
    
    # Quick scan
    result = builder.build_universe(scan_type='quick')
    
    print(f"\n✅ Found {len(result['long_candidates'])} long opportunities")
    print(f"✅ Found {len(result['short_candidates'])} short opportunities")
