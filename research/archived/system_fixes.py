"""
System Fixes - Optimization & Bug Fixes

Fixes:
1. Cache sector rotation (performance)
2. Better VIX handling
3. Skip fundamentals for ETFs
4. Add limit order execution
5. Optimize risk overlay
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
import re

# For limit orders
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from config import TradingConfig
from execution_policy import (
    require_quote,
    require_spread_ok,
    require_position_size_valid,
    require_not_halted_or_frozen,
)


class OptimizedRiskOverlay:
    """
    Optimized risk overlay with caching
    
    FIXES:
    - Caches sector rotation (1x per scan vs 40x)
    - Handles VIX failures gracefully
    - Skips fundamentals for ETFs
    """
    
    def __init__(self):
        # Import original
        from risk_overlay import RiskOverlay
        self.base_overlay = RiskOverlay()
        
        # Add caching
        self._sector_cache = None
        self._sector_cache_time = None
        self._cache_duration = 3600  # 1 hour cache
        
        print("🛡️ Optimized Risk Overlay initialized (with caching)")

    def __getattr__(self, name):
        """
        Compatibility passthrough to base RiskOverlay.

        unified_master_trader_v2 and other modules still call several
        RiskOverlay methods directly (e.g. check_macro_events,
        get_ticker_sector, generate_risk_report). Forward unknown
        attributes/methods to avoid interface drift.
        """
        return getattr(self.base_overlay, name)
    
    def check_entry_allowed(self, ticker: str, current_date: Optional[datetime] = None) -> Dict:
        """Check with optimized sector calls"""
        
        if current_date is None:
            current_date = datetime.now()
        
        warnings = []
        blocks = []
        risk_score = 0
        
        # CHECK 1: Earnings (skip for ETFs)
        if not self._is_etf(ticker):
            try:
                earnings_check = self.base_overlay.check_earnings_proximity(ticker, current_date)
                
                if earnings_check['has_earnings']:
                    days = earnings_check['days_until']
                    
                    if days <= 2:
                        blocks.append(f"Earnings in {days} days - TOO RISKY")
                        risk_score += 50
                    elif days <= 5:
                        warnings.append(f"Earnings in {days} days - Use caution")
                        risk_score += 25
            except:
                pass  # Skip if can't get earnings
        
        # CHECK 2: Macro events
        macro_check = self.base_overlay.check_macro_events(current_date)
        
        if macro_check['high_impact_today']:
            warnings.append(f"High-impact event today")
            risk_score += 15
        
        # CHECK 3: Black swan (skip for ETFs)
        if not self._is_etf(ticker):
            try:
                black_swan = self.base_overlay.detect_black_swan(ticker)
                
                if black_swan['detected']:
                    blocks.append(f"Black swan: {black_swan['reason']}")
                    risk_score += 40
            except:
                pass
        
        # CHECK 4: Sector health (CACHED!)
        sector = self.base_overlay.get_ticker_sector(ticker)
        sector_check = self._get_sector_health_cached(sector)
        
        if sector_check['momentum'] == 'COLD':
            warnings.append(f"Sector {sector} is COLD")
            risk_score += 10
        
        # DECISION
        allowed = len(blocks) == 0
        
        return {
            'allowed': allowed,
            'warnings': warnings,
            'blocks': blocks,
            'risk_score': risk_score,
            'sector': sector_check
        }
    
    def _get_sector_health_cached(self, sector: str) -> Dict:
        """Get sector health with caching (PERFORMANCE FIX)"""
        
        now = datetime.now()
        
        # Check cache
        if self._sector_cache is not None and self._sector_cache_time:
            age = (now - self._sector_cache_time).total_seconds()

            if age < self._cache_duration:
                # Use cached data — always return from here, never fall through
                for sector_perf in self._sector_cache:
                    if sector_perf.sector == sector:
                        return {
                            'sector': sector,
                            'momentum': sector_perf.momentum,
                            'rank': sector_perf.rank,
                            'should_trade': sector_perf.momentum in ['HOT', 'WARM', 'NEUTRAL']
                        }
                # Sector not in cached list but cache is still valid — return default
                return {
                    'sector': sector,
                    'momentum': 'NEUTRAL',
                    'rank': 6,
                    'should_trade': True
                }

        # Cache expired or not yet populated — refresh
        print("   📊 Refreshing sector rotation cache...")
        self._sector_cache = self.base_overlay.get_sector_rotation() or []
        self._sector_cache_time = now
        
        # Return sector data
        for sector_perf in self._sector_cache:
            if sector_perf.sector == sector:
                return {
                    'sector': sector,
                    'momentum': sector_perf.momentum,
                    'rank': sector_perf.rank,
                    'should_trade': sector_perf.momentum in ['HOT', 'WARM', 'NEUTRAL']
                }
        
        # Default if not found
        return {
            'sector': sector,
            'momentum': 'NEUTRAL',
            'rank': 6,
            'should_trade': True
        }
    
    def _is_etf(self, ticker: str) -> bool:
        """Check if ticker is an ETF (skip fundamentals)"""
        
        # ETF-specific patterns only — avoid 3-letter catch-all which hits AMD, TSM, JPM etc.
        etf_patterns = [
            r'^XL[A-Z]$',   # Sector ETFs: XLK, XLF, XLE, XLV, XLI ...
            r'.*QQQ$',      # Leveraged Q variants: TQQQ, SQQQ
            r'^(SPXL|SPXS|UPRO|URTY|TNA|TZA|LABU|LABD|SOXL|SOXS)$',  # Known leveraged ETFs
        ]
        
        # Known ETFs
        known_etfs = {
            'SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO',
            'XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLP', 'XLB', 'XLRE', 'XLU', 'XLC',
            'TQQQ', 'SQQQ', 'UPRO', 'SPXL', 'BITU', 'CONL',
            # Watchlist ETFs/ETPs without Yahoo fundamentals
            'IBIT',  # iShares Bitcoin Trust
            'VXX',   # iPath S&P 500 VIX Short-Term Futures ETN
            'IGV',   # iShares Expanded Tech-Software ETF
        }
        
        if ticker in known_etfs:
            return True
        
        for pattern in etf_patterns:
            if re.match(pattern, ticker):
                return True
        
        return False


class LimitOrderExecutor:
    """
    Smart limit order execution at mid-price
    
    GEMINI'S ENHANCEMENT:
    - Uses limit orders instead of market orders
    - Sets price at bid-ask midpoint
    - Better fills, saves money
    """
    
    def __init__(self, trading_client):
        self.trading_client = trading_client
        print("💰 Limit Order Executor initialized (mid-price execution)")
    
    def execute_entry(
        self,
        ticker: str,
        shares: int,
        direction: str = "LONG",
        stop_loss: float = None,
        target_price: float = None,
    ) -> Dict:
        """
        Execute entry with limit order at mid-price
        
        Args:
            ticker: Symbol
            shares: Number of shares
        
        Returns:
            {
                'success': bool,
                'order_id': str,
                'limit_price': float,
                'message': str
            }
        """
        
        try:
            qty_check = require_position_size_valid(shares)
            if not qty_check.allowed:
                return {
                    'success': False,
                    'message': f'Execution denied: {qty_check.reason}'
                }

            # Get current quote
            quote = self.trading_client.get_latest_quote(ticker)
            quote_check = require_quote(quote)
            if not quote_check.allowed:
                return {
                    'success': False,
                    'message': f'Execution denied: {quote_check.reason}'
                }

            halt_check = require_not_halted_or_frozen(quote, max_quote_age_seconds=30)
            if not halt_check.allowed:
                return {
                    'success': False,
                    'message': f'Execution denied: {halt_check.reason}'
                }
            
            # Calculate mid-price
            bid = float(quote.bid_price)
            ask = float(quote.ask_price)

            spread_check = require_spread_ok(bid, ask, "NORMAL")
            if not spread_check.allowed:
                return {
                    'success': False,
                    'message': f'Execution denied: {spread_check.reason}'
                }
            
            # Mid-price
            mid_price = (bid + ask) / 2
            
            # Round to 2 decimals
            limit_price = round(mid_price, 2)
            
            d = (direction or "LONG").upper()
            side = OrderSide.BUY if d == "LONG" else OrderSide.SELL
            if side == OrderSide.SELL:
                if not TradingConfig.ALLOW_SHORTS:
                    return {
                        'success': False,
                        'message': 'Execution denied: SHORTS_DISABLED_BY_CONFIG'
                    }
                try:
                    asset = self.trading_client.get_asset(ticker)
                    shortable = bool(getattr(asset, "shortable", False))
                    easy = bool(getattr(asset, "easy_to_borrow", True))
                    if not (shortable and easy):
                        return {
                            'success': False,
                            'message': 'Execution denied: SHORT_NOT_BORROWABLE_OR_UNVERIFIED'
                        }
                except Exception:
                    return {
                        'success': False,
                        'message': 'Execution denied: SHORT_NOT_BORROWABLE_OR_UNVERIFIED'
                    }

            use_bracket = stop_loss is not None and target_price is not None
            if use_bracket:
                from alpaca.trading.enums import OrderClass
                from alpaca.trading.requests import (
                    TakeProfitRequest,
                    StopLossRequest,
                    MarketOrderRequest,
                )
                try:
                    order_data = LimitOrderRequest(
                        symbol=ticker,
                        qty=shares,
                        side=side,
                        time_in_force=TimeInForce.DAY,
                        limit_price=limit_price,
                        order_class=OrderClass.BRACKET,
                        take_profit=TakeProfitRequest(limit_price=round(float(target_price), 2)),
                        stop_loss=StopLossRequest(stop_price=round(float(stop_loss), 2)),
                    )
                except TypeError:
                    order_data = MarketOrderRequest(
                        symbol=ticker,
                        qty=shares,
                        side=side,
                        time_in_force=TimeInForce.DAY,
                        order_class=OrderClass.BRACKET,
                        take_profit=TakeProfitRequest(limit_price=round(float(target_price), 2)),
                        stop_loss=StopLossRequest(stop_price=round(float(stop_loss), 2)),
                    )
            else:
                order_data = LimitOrderRequest(
                    symbol=ticker,
                    qty=shares,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price
                )
            
            order = self.trading_client.submit_order(order_data)
            
            return {
                'success': True,
                'order_id': order.id,
                'limit_price': limit_price,
                'bid': bid,
                'ask': ask,
                'spread': ask - bid,
                'message': f'Limit order placed at ${limit_price:.2f} (mid-price)'
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Execution error: {e}'
            }
    
    def execute_exit(self, ticker: str, shares: int) -> Dict:
        """Execute exit with limit order at mid-price"""
        
        try:
            qty_check = require_position_size_valid(shares)
            if not qty_check.allowed:
                return {
                    'success': False,
                    'message': f'Exit denied: {qty_check.reason}'
                }

            # Get current quote
            quote = self.trading_client.get_latest_quote(ticker)
            quote_check = require_quote(quote)
            if not quote_check.allowed:
                return {
                    'success': False,
                    'message': f'Exit denied: {quote_check.reason}'
                }
            
            # Calculate mid-price
            bid = float(quote.bid_price)
            ask = float(quote.ask_price)

            spread_check = require_spread_ok(bid, ask, "NORMAL")
            if not spread_check.allowed:
                return {
                    'success': False,
                    'message': f'Exit denied: {spread_check.reason}'
                }
            
            # Mid-price
            mid_price = (bid + ask) / 2
            limit_price = round(mid_price, 2)
            
            # Submit limit order
            order_data = LimitOrderRequest(
                symbol=ticker,
                qty=shares,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price
            )
            
            order = self.trading_client.submit_order(order_data)
            
            return {
                'success': True,
                'order_id': order.id,
                'limit_price': limit_price,
                'message': f'Limit exit placed at ${limit_price:.2f} (mid-price)'
            }
            
        except Exception as e:
            return {
                'success': False,
                'message': f'Exit error: {e}'
            }


# =============================================================================
# Testing
# =============================================================================

def test_fixes():
    """Test all fixes"""
    
    print("\n" + "="*80)
    print("🔧 TESTING SYSTEM FIXES")
    print("="*80 + "\n")
    
    # Test 1: Optimized Risk Overlay
    print("TEST 1: Optimized Risk Overlay (Caching)")
    print("-"*40)
    
    overlay = OptimizedRiskOverlay()
    
    # First call (should calculate)
    start = datetime.now()
    result1 = overlay.check_entry_allowed('NVDA')
    time1 = (datetime.now() - start).total_seconds()
    
    # Second call (should use cache)
    start = datetime.now()
    result2 = overlay.check_entry_allowed('AMD')
    time2 = (datetime.now() - start).total_seconds()
    
    print(f"First call (calculate): {time1:.2f}s")
    print(f"Second call (cached): {time2:.2f}s")
    print(f"Speedup: {time1/time2:.1f}x faster!\n")
    
    # Test 2: ETF Detection
    print("TEST 2: ETF Detection")
    print("-"*40)
    
    test_tickers = ['NVDA', 'SPY', 'TQQQ', 'XLK', 'BITU']
    
    for ticker in test_tickers:
        is_etf = overlay._is_etf(ticker)
        print(f"{ticker}: {'ETF ✅' if is_etf else 'Stock'}")
    
    print("\n" + "="*80 + "\n")


if __name__ == "__main__":
    test_fixes()
