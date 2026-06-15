"""
Sniper Adaptive Universe Builder
Finds stocks with institutional momentum POTENTIAL
"""

from typing import List, Dict
from datetime import datetime, timedelta
from alpaca_data import AlpacaDataFeed
import logging

logger = logging.getLogger(__name__)


class SniperAdaptiveUniverse:
    """
    Sniper Universe Builder - Institutional Momentum Hunting
    
    PURPOSE:
    Find stocks where institutional momentum could develop or is developing.
    These are stocks that INSTITUTIONS can actually trade (liquid enough)
    and where MOMENTUM is possible (volatile enough).
    
    The Sniper strategy pipeline will then filter for actual signals.
    
    CRITERIA:
    - Institutional liquidity (volume >2M)
    - Tradeable price range ($10-$500)
    - Movement potential (recent activity or high volatility)
    - Clean data available
    
    NOT looking for: Penny stocks, illiquid, dead stocks, ETFs
    """
    
    def __init__(self, data_feed: AlpacaDataFeed):
        self.data_feed = data_feed
        
        # Professional thresholds (purpose-driven)
        self.MIN_VOLUME = 2_000_000        # Institutional can trade
        self.MIN_PRICE = 10                 # Serious stocks only
        self.MAX_PRICE = 500
        self.MIN_VOLATILITY = 1.5           # Has movement potential
        
        logger.info("Sniper Universe Builder initialized")
        logger.info(f"  Criteria: Volume >{self.MIN_VOLUME:,}, Price ${self.MIN_PRICE}-${self.MAX_PRICE}, Volatility >{self.MIN_VOLATILITY}%")
    
    def build_universe(self) -> Dict:
        """
        Build universe of stocks with momentum POTENTIAL
        
        Returns stocks that COULD develop momentum.
        The Sniper strategy will filter for stocks with ACTUAL signals.
        """
        
        print("\n" + "="*80)
        print("🎯 SNIPER UNIVERSE BUILDER - Institutional Momentum Potential")
        print("="*80 + "\n")
        
        # Get liquid, tradeable stocks
        initial_universe = self._get_institutional_universe()
        print(f"📊 Institutional universe: {len(initial_universe)} stocks")
        
        # Filter for momentum potential
        candidates = self._filter_for_momentum_potential(initial_universe)
        
        if len(candidates) == 0:
            print(f"\n⚠️  No momentum candidates found")
            print(f"   (Market may be range-bound or low volatility)")
            return {'tickers': [], 'stats': {'total': 0, 'avg_volume': 0, 'avg_volatility': 0}}
        
        # Prioritize by opportunity quality
        prioritized = self._prioritize_candidates(candidates)
        
        # Return candidates (no arbitrary cap - let pipeline filter)
        final_tickers = [c['ticker'] for c in prioritized]
        
        print(f"\n✅ Sniper universe: {len(final_tickers)} candidates")
        print(f"   Avg volume: {sum(c['volume'] for c in prioritized)/len(prioritized)/1_000_000:.1f}M")
        print(f"   Avg volatility: {sum(c['volatility'] for c in prioritized)/len(prioritized):.1f}%")
        print(f"\n   Sniper strategy will filter these for actual signals...")
        
        return {
            'tickers': final_tickers,
            'stats': {
                'total': len(final_tickers),
                'avg_volume': sum(c['volume'] for c in prioritized) / len(prioritized),
                'avg_volatility': sum(c['volatility'] for c in prioritized) / len(prioritized)
            }
        }
    
    def _get_institutional_universe(self) -> List[str]:
        """
        Get stocks institutions actually trade
        
        Focus: Liquid large/mid caps where institutions are active
        """
        
        universe = set()
        
        # S&P 500 - institutional staples
        sp500 = self._get_sp500_components()
        universe.update(sp500)
        
        # NASDAQ 100 - tech/growth leaders
        nasdaq = self._get_nasdaq100_components()
        universe.update(nasdaq)
        
        # High-volume growth - institutional interest
        growth = self._get_institutional_growth()
        universe.update(growth)
        
        # Clean universe
        universe = {t for t in universe if self._is_valid_stock(t)}
        
        return list(universe)
    
    def _filter_for_momentum_potential(self, candidates: List[str]) -> List[Dict]:
        """
        Filter for stocks with momentum POTENTIAL
        
        Criteria:
        1. Liquid enough for institutions (>2M volume)
        2. Price range institutions trade ($10-$500)
        3. Has movement (volatility >1.5% OR recent price action)
        4. Clean, reliable data
        
        These are stocks where momentum COULD develop.
        Sniper strategy will confirm actual signals.
        """
        
        print(f"\n🔍 Filtering for momentum potential...")
        
        momentum_candidates = []
        
        # FIX: Use longer date range and handle after-hours
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
                
                if len(bars) < 10:  # Need at least 10 bars for reliable analysis
                    stats['insufficient_bars'] += 1
                    continue
                
                current_price = bars[-1]['close']
                
                # Use last 20 bars for volume (more stable)
                recent_bars = bars[-20:] if len(bars) >= 20 else bars
                avg_volume = sum(b['volume'] for b in recent_bars) / len(recent_bars)
                
                # Calculate volatility from last 10 bars
                vol_bars = bars[-10:]
                ranges = [(b['high'] - b['low']) for b in vol_bars]
                atr = sum(ranges) / len(ranges)
                volatility = (atr / current_price) * 100
                
                # Recent momentum (last 5 days)
                if len(bars) >= 5:
                    momentum = ((bars[-1]['close'] - bars[-5]['close']) / bars[-5]['close']) * 100
                else:
                    momentum = 0
                
                # FILTER: Institutional liquidity
                if avg_volume < self.MIN_VOLUME:
                    continue
                
                # FILTER: Tradeable price
                if current_price < self.MIN_PRICE or current_price > self.MAX_PRICE:
                    continue
                
                # FILTER: Movement potential
                has_volatility = volatility >= self.MIN_VOLATILITY
                has_movement = abs(momentum) >= 2.0
                ultra_liquid = avg_volume > 10_000_000
                
                if not (has_volatility or has_movement or ultra_liquid):
                    continue
                
                # PASSED
                stats['passed'] += 1
                momentum_candidates.append({
                    'ticker': ticker,
                    'volume': avg_volume,
                    'price': current_price,
                    'volatility': volatility,
                    'momentum': momentum
                })
                
            except Exception as e:
                if stats['total_checked'] <= 3:  # Show first few errors
                    print(f"   ERROR on {ticker}: {e}")
                stats['no_bars'] += 1
                continue
        
        # Show stats
        print(f"\n   📊 Filtering Stats:")
        print(f"      Total checked: {stats['total_checked']}")
        print(f"      No bars: {stats['no_bars']}")
        print(f"      Insufficient bars: {stats['insufficient_bars']}")
        print(f"      Passed: {stats['passed']}")
        
        return momentum_candidates
    
    def _prioritize_candidates(self, candidates: List[Dict]) -> List[Dict]:
        """
        Prioritize by opportunity quality
        
        Quality = Liquidity + Volatility + Recent Action
        Best opportunities float to top for Sniper strategy
        """
        
        for c in candidates:
            # Volume score (higher = better)
            vol_score = min(c['volume'] / 1_000_000, 50)
            
            # Volatility score (more = better opportunity)
            vol_pct_score = min(c['volatility'] * 10, 30)
            
            # Momentum score (either direction)
            momentum_score = min(abs(c['momentum']) * 2, 20)
            
            c['quality'] = vol_score + vol_pct_score + momentum_score
        
        # Sort by quality
        candidates.sort(key=lambda x: x['quality'], reverse=True)
        
        return candidates
    
    def _get_sp500_components(self) -> List[str]:
        """S&P 500 liquid stocks"""
        return [
            'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA',
            'UNH', 'JNJ', 'JPM', 'V', 'XOM', 'PG', 'MA', 'HD', 'CVX',
            'MRK', 'COST', 'AVGO', 'LLY', 'PEP', 'KO', 'WMT', 'MCD',
            'TMO', 'ACN', 'ABT', 'NFLX', 'CRM', 'AMD', 'DHR', 'ADBE',
            'VZ', 'CMCSA', 'NKE', 'INTC', 'DIS', 'TXN', 'QCOM', 'PM',
            'CAT', 'GE', 'BA', 'HON', 'IBM', 'LOW', 'RTX', 'UPS', 'MS',
        ]
    
    def _get_nasdaq100_components(self) -> List[str]:
        """NASDAQ 100 components"""
        return [
            'AAPL', 'MSFT', 'AMZN', 'NVDA', 'META', 'GOOGL', 'GOOG', 'TSLA',
            'AVGO', 'COST', 'NFLX', 'AMD', 'PEP', 'ADBE', 'CSCO', 'TMUS',
            'INTU', 'TXN', 'QCOM', 'CMCSA', 'AMGN', 'HON', 'AMAT', 'SBUX',
        ]
    
    def _get_institutional_growth(self) -> List[str]:
        """High-volume growth stocks institutions track"""
        return [
            'PLTR', 'COIN', 'RBLX', 'SNOW', 'DKNG', 'DASH', 'U', 'RIVN',
            'ZM', 'DOCU', 'TWLO', 'NET', 'DDOG', 'OKTA', 'ZS', 'FTNT',
            'CRWD', 'PANW', 'MDB', 'SNPS', 'CDNS', 'MRVL', 'LRCX', 'KLAC',
        ]
    
    def _is_valid_stock(self, ticker: str) -> bool:
        """Validate ticker"""
        if not ticker or len(ticker) > 5:
            return False
        
        # Exclude ETFs
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
    builder = SniperAdaptiveUniverse(data_feed)
    result = builder.build_universe()
    
    print(f"\n📊 SNIPER UNIVERSE: {len(result['tickers'])} candidates")
    if result['tickers']:
        print(f"   Top 10: {result['tickers'][:10]}")
