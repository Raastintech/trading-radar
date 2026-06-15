"""
Remora Adaptive Universe Builder
Finds stocks with exploitable inefficiency POTENTIAL
"""

from typing import List, Dict
from datetime import datetime, timedelta
from alpaca_data import AlpacaDataFeed
import logging

logger = logging.getLogger(__name__)


class RemoraAdaptiveUniverse:
    """
    Remora Universe Builder - Inefficiency Exploitation
    
    PURPOSE:
    Find stocks where institutional orders create temporary mispricing.
    These are mid-caps where liquidity is LIMITED enough that large
    orders move price, creating edge for fast, opportunistic traders.
    
    The Remora strategy pipeline will then detect actual inefficiency signals.
    
    CRITERIA:
    - Limited liquidity (500k-5M volume sweet spot)
    - Wide enough spreads (opportunity for edge)
    - Mid-cap range ($1B-$20B market cap proxy)
    - Not penny stocks, not mega-caps
    
    NOT looking for: Mega-caps (too liquid), micro-caps (too risky), ETFs
    """
    
    def __init__(self, data_feed: AlpacaDataFeed):
        self.data_feed = data_feed
        
        # Professional thresholds (purpose-driven)
        self.MIN_VOLUME = 500_000          # Tradeable but not too liquid
        self.MAX_VOLUME = 5_000_000        # Limited enough for impact
        self.MIN_PRICE = 8                  # Quality stocks
        self.MAX_PRICE = 300
        self.MIN_SPREAD = 0.5               # Some opportunity exists
        
        logger.info("Remora Universe Builder initialized")
        logger.info(f"  Criteria: Volume {self.MIN_VOLUME:,}-{self.MAX_VOLUME:,}, Spread >{self.MIN_SPREAD}%")
    
    def build_universe(self) -> Dict:
        """
        Build universe of stocks with inefficiency POTENTIAL
        
        Returns stocks where order flow could create exploitable edge.
        The Remora strategy will detect actual inefficiency signals.
        """
        
        print("\n" + "="*80)
        print("🦈 REMORA UNIVERSE BUILDER - Institutional Inefficiency Potential")
        print("="*80 + "\n")
        
        # Get mid-cap universe
        initial_universe = self._get_midcap_universe()
        print(f"📊 Mid-cap universe: {len(initial_universe)} stocks")
        
        # Filter for inefficiency potential
        candidates = self._filter_for_inefficiency_potential(initial_universe)
        
        if len(candidates) == 0:
            print(f"\n⚠️  No inefficiency candidates found")
            print(f"   (Market may be too efficient or liquid)")
            return {'tickers': [], 'stats': {'total': 0, 'avg_volume': 0, 'avg_spread': 0}}
        
        # Prioritize by edge potential
        prioritized = self._prioritize_candidates(candidates)
        
        # Return candidates (let Remora pipeline filter for signals)
        final_tickers = [c['ticker'] for c in prioritized]
        
        print(f"\n✅ Remora universe: {len(final_tickers)} candidates")
        print(f"   Avg volume: {sum(c['volume'] for c in prioritized)/len(prioritized)/1_000_000:.1f}M")
        print(f"   Avg spread: {sum(c['spread'] for c in prioritized)/len(prioritized):.2f}%")
        print(f"\n   Remora strategy will detect actual inefficiency signals...")
        
        return {
            'tickers': final_tickers,
            'stats': {
                'total': len(final_tickers),
                'avg_volume': sum(c['volume'] for c in prioritized) / len(prioritized),
                'avg_spread': sum(c['spread'] for c in prioritized) / len(prioritized)
            }
        }
    
    def _get_midcap_universe(self) -> List[str]:
        """
        Get mid-cap stocks where inefficiency exists
        
        Focus: Russell 2000, quality small-caps, recent IPOs
        These are stocks too small for mega institutions but big enough to trade
        """
        
        universe = set()
        
        # Russell 2000 leaders
        russell = self._get_russell_leaders()
        universe.update(russell)
        
        # Quality small/mid caps
        quality_mids = self._get_quality_midcaps()
        universe.update(quality_mids)
        
        # Recent IPOs (often inefficient)
        ipos = self._get_recent_ipos()
        universe.update(ipos)
        
        # Clean universe
        universe = {t for t in universe if self._is_valid_stock(t)}
        
        return list(universe)
    
    def _filter_for_inefficiency_potential(self, candidates: List[str]) -> List[Dict]:
        """
        Filter for stocks with inefficiency POTENTIAL
        
        Criteria:
        1. Limited liquidity (500k-5M) - orders can move price
        2. Reasonable price ($8-$300) - tradeable
        3. Measureable spread (>0.5%) - opportunity exists
        4. Clean data
        
        These are stocks where inefficiency COULD exist.
        Remora strategy will detect actual signals.
        """
        
        print(f"\n🔍 Filtering for inefficiency potential...")
        
        inefficiency_candidates = []
        
        # FIX: Use longer date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)  # 30 days instead of 10
        
        print(f"   Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        
        stats = {
            'total_checked': 0,
            'no_bars': 0,
            'insufficient_bars': 0,
            'passed': 0
        }
        
        for ticker in candidates:
            stats['total_checked'] += 1
            
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=30)
                
                # Debug first ticker
                if stats['total_checked'] == 1:
                    print(f"\n   DEBUG first ticker ({ticker}):")
                    if bars:
                        print(f"      Got {len(bars)} bars")
                        if len(bars) > 0:
                            latest_ts = bars[-1].get('timestamp', bars[-1].get('time', 'n/a'))
                            print(f"      Latest: {latest_ts}")
                    else:
                        print(f"      No bars returned!")
                
                if not bars:
                    stats['no_bars'] += 1
                    continue
                
                if len(bars) < 10:
                    stats['insufficient_bars'] += 1
                    continue
                
                current_price = bars[-1]['close']
                
                # Use last 20 bars for volume
                recent_bars = bars[-20:] if len(bars) >= 20 else bars
                avg_volume = sum(b['volume'] for b in recent_bars) / len(recent_bars)
                
                # Calculate spread from last 10 bars
                spread_bars = bars[-10:]
                spreads = [(b['high'] - b['low']) / b['close'] * 100 for b in spread_bars]
                avg_spread = sum(spreads) / len(spreads)
                
                # FILTER: Inefficiency volume range
                if avg_volume < self.MIN_VOLUME or avg_volume > self.MAX_VOLUME:
                    continue
                
                # FILTER: Tradeable price
                if current_price < self.MIN_PRICE or current_price > self.MAX_PRICE:
                    continue
                
                # FILTER: Spread opportunity
                if avg_spread < self.MIN_SPREAD:
                    continue
                
                # PASSED
                stats['passed'] += 1
                inefficiency_candidates.append({
                    'ticker': ticker,
                    'volume': avg_volume,
                    'price': current_price,
                    'spread': avg_spread
                })
                
            except Exception as e:
                if stats['total_checked'] <= 3:
                    print(f"   ERROR on {ticker}: {e}")
                stats['no_bars'] += 1
                continue
        
        # Show stats
        print(f"\n   📊 Filtering Stats:")
        print(f"      Total checked: {stats['total_checked']}")
        print(f"      No bars: {stats['no_bars']}")
        print(f"      Insufficient bars: {stats['insufficient_bars']}")
        print(f"      Passed: {stats['passed']}")
        
        return inefficiency_candidates
    
    def _prioritize_candidates(self, candidates: List[Dict]) -> List[Dict]:
        """
        Prioritize by edge potential
        
        Best edge = Lower volume (more impact) + Wider spread (more opportunity)
        """
        
        for c in candidates:
            # Volume score (LOWER is better for inefficiency)
            # 500k = high score, 5M = low score
            vol_millions = c['volume'] / 1_000_000
            volume_score = max(50 - (vol_millions - 0.5) * 10, 0)
            
            # Spread score (WIDER is better)
            spread_score = min(c['spread'] * 10, 50)
            
            c['edge_potential'] = volume_score + spread_score
        
        # Sort by edge potential
        candidates.sort(key=lambda x: x['edge_potential'], reverse=True)
        
        return candidates
    
    def _get_russell_leaders(self) -> List[str]:
        """Russell 2000 quality names"""
        return [
            'CRWD', 'PLTR', 'COIN', 'RBLX', 'U', 'RIVN', 'SNOW', 'DKNG',
            'ZM', 'DOCU', 'TWLO', 'NET', 'DDOG', 'OKTA', 'ZS', 'FTNT',
            'PANW', 'CYBR', 'S', 'SOFI', 'AFRM', 'SQ', 'HOOD', 'BILL',
        ]
    
    def _get_quality_midcaps(self) -> List[str]:
        """Quality mid-cap stocks"""
        return [
            'DELL', 'HPE', 'GRAB', 'EXAS', 'SMAR', 'COUP', 'PCTY',
            'NBIX', 'JAZZ', 'RARE', 'FOLD', 'BROS', 'CVNA', 'CAVA',
        ]
    
    def _get_recent_ipos(self) -> List[str]:
        """Recent IPOs (often inefficient)"""
        return [
            'ARM', 'DASH', 'ABNB', 'COIN', 'RBLX', 'SNOW', 'HOOD',
            'SOFI', 'RIVN', 'LCID', 'GRAB', 'RDDT', 'CELH', 'WING',
        ]
    
    def _is_valid_stock(self, ticker: str) -> bool:
        """Validate ticker"""
        if not ticker or len(ticker) > 5:
            return False
        
        etfs = ['SPY', 'QQQ', 'IWM', 'DIA', 'VXX', 'UVXY', 'SVXY',
                'XLK', 'XLV', 'XLF', 'XLE', 'XLI', 'XLP', 'XLY',
                'XLU', 'XLRE', 'XLC', 'XLB', 'XBI', 'XME', 'XRT']
        
        if ticker in etfs:
            return False
        
        if '-' in ticker or '.' in ticker:
            return False
        
        return True


if __name__ == "__main__":
    from alpaca_data import AlpacaDataFeed
    data_feed = AlpacaDataFeed()
    builder = RemoraAdaptiveUniverse(data_feed)
    result = builder.build_universe()
    
    print(f"\n📊 REMORA UNIVERSE: {len(result['tickers'])} candidates")
    if result['tickers']:
        print(f"   Top 10: {result['tickers'][:10]}")
