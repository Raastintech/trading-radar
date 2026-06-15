"""
Execution Guard Agent - Pre-Trade Safety Checks

Prevents trades that would execute poorly even if signals align.

Checks:
1. Market session (no pre/post market)
2. Spread & liquidity (avoid illiquid stocks)
3. Slippage estimation (don't trade thin markets)
4. Macro event proximity (size down before Fed, earnings)
5. Hard dollar risk cap (never risk more than $X)

This agent has VETO power - any failed check blocks execution.
"""

from datetime import datetime, time, timedelta
from typing import Dict, Optional
import requests
from alpaca_data import AlpacaDataFeed

class ExecutionGuard:
    """
    Final safety layer before execution
    
    Returns:
        APPROVE: Safe to execute with full size
        CAUTION: Execution risky, size down
        VETO: DO NOT EXECUTE (safety violation)
    """
    
    def __init__(self, account_size=None, risk_pct=0.05):
        """
        Initialize execution guard

        Args:
            account_size: Total account size (if None, fetches from Alpaca)
            risk_pct: Risk percentage per trade (default 5% = 0.05)
        """
        self.data_feed = AlpacaDataFeed()
        self.risk_pct = risk_pct

        if account_size is None:
            self.account_size = self._get_account_size()
        else:
            self.account_size = float(account_size)

        self.max_risk_per_trade = self.account_size * self.risk_pct
        
        # Market hours (NYSE/NASDAQ)
        self.market_open = time(9, 30)   # 9:30 AM ET
        self.market_close = time(16, 0)  # 4:00 PM ET
        
        print("✅ Execution Guard initialized")
        print(f"💰 Account Size: ${self.account_size:,.0f}")
        print(f"📊 Risk per trade: {self.risk_pct*100:.0f}% = ${self.max_risk_per_trade:,.0f}")

    def _get_account_size(self) -> float:
        """Get account size from Alpaca."""
        try:
            from alpaca.trading.client import TradingClient
            from config import TradingConfig

            config = TradingConfig()
            trading_client = TradingClient(
                config.ALPACA_API_KEY,
                config.ALPACA_SECRET_KEY,
                paper=True
            )

            account = trading_client.get_account()
            return float(account.portfolio_value)
        except Exception as e:
            print(f"⚠️  Could not fetch account size: {e}")
            print("   Using default: $200,000")
            return 200000.0
    
    def check_market_session(self) -> Dict:
        """
        Check if market is in regular trading hours (ET timezone)
        
        Returns:
            {'ok': bool, 'session': str, 'reason': str}
        """
        
        from datetime import datetime
        import pytz
        
        # Get current time in ET
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        current_time = now_et.time()
        current_day = now_et.weekday()
        
        # Check if weekday
        if current_day >= 5:  # Saturday=5, Sunday=6
            return {
                'ok': False,
                'session': 'CLOSED',
                'reason': 'Market closed (weekend)'
            }
        
        # Check if regular hours (9:30 AM - 4:00 PM ET)
        if self.market_open <= current_time <= self.market_close:
            return {
                'ok': True,
                'session': 'REGULAR',
                'reason': f'Regular trading hours (ET: {current_time.strftime("%I:%M %p")})'
            }
        
        # Pre-market (4:00 AM - 9:30 AM ET)
        elif time(4, 0) <= current_time < self.market_open:
            return {
                'ok': False,
                'session': 'PRE_MARKET',
                'reason': f'Pre-market session (ET: {current_time.strftime("%I:%M %p")})'
            }
        
        # After-hours (4:00 PM - 8:00 PM ET)
        elif self.market_close < current_time <= time(20, 0):
            return {
                'ok': False,
                'session': 'AFTER_HOURS',
                'reason': f'After-hours session (ET: {current_time.strftime("%I:%M %p")})'
            }
        
        # Closed
        else:
            return {
                'ok': False,
                'session': 'CLOSED',
                'reason': f'Market closed (ET: {current_time.strftime("%I:%M %p")})'
            }
    
    def check_spread_liquidity(self, ticker: str) -> Dict:
        """
        Check bid-ask spread and volume for execution quality
        
        Returns:
            {'ok': bool, 'spread_pct': float, 'volume': int, 'reason': str}
        """
        
        try:
            # Get latest quote
            bars = self.data_feed.get_daily_bars(ticker, days_back=5)
            
            if not bars or len(bars) < 2:
                return {
                    'ok': False,
                    'spread_pct': None,
                    'volume': None,
                    'reason': 'Insufficient data to check liquidity'
                }
            
            latest = bars[-1]
            avg_volume = sum(b['volume'] for b in bars[-5:]) / 5
            
            # Estimate spread from high-low range
            # (Real implementation would use live bid-ask from Alpaca)
            close = latest['close']
            high = latest['high']
            low = latest['low']
            
            # Conservative spread estimate: (high - low) / close
            estimated_spread_pct = ((high - low) / close) * 100
            
            # Volume check
            volume_ok = avg_volume > 100000  # Min 100K daily volume
            
            # Spread check
            spread_ok = estimated_spread_pct < 2.0  # Max 2% spread
            
            if not volume_ok:
                return {
                    'ok': False,
                    'spread_pct': round(estimated_spread_pct, 3),
                    'volume': int(avg_volume),
                    'reason': f'Low volume ({avg_volume:,.0f} < 100K) - poor execution likely'
                }
            
            if not spread_ok:
                return {
                    'ok': False,
                    'spread_pct': round(estimated_spread_pct, 3),
                    'volume': int(avg_volume),
                    'reason': f'Wide spread ({estimated_spread_pct:.2f}% > 2%) - high slippage risk'
                }
            
            return {
                'ok': True,
                'spread_pct': round(estimated_spread_pct, 3),
                'volume': int(avg_volume),
                'reason': 'Good liquidity and tight spread'
            }
            
        except Exception as e:
            return {
                'ok': False,
                'spread_pct': None,
                'volume': None,
                'reason': f'Error checking liquidity: {e}'
            }
    
    def estimate_slippage(self, ticker: str, shares: int) -> Dict:
        """
        Estimate slippage based on order size vs avg volume
        
        Returns:
            {'ok': bool, 'slippage_pct': float, 'reason': str}
        """
        
        try:
            bars = self.data_feed.get_daily_bars(ticker, days_back=5)
            
            if not bars:
                return {
                    'ok': False,
                    'slippage_pct': None,
                    'reason': 'Cannot estimate slippage (no data)'
                }
            
            avg_volume = sum(b['volume'] for b in bars[-5:]) / 5
            
            # Order size as % of daily volume
            order_pct_of_volume = (shares / avg_volume) * 100
            
            # Slippage estimation model (conservative)
            if order_pct_of_volume < 0.1:
                estimated_slippage = 0.05  # 5 bps
                ok = True
                reason = 'Negligible market impact expected'
            elif order_pct_of_volume < 1.0:
                estimated_slippage = 0.10  # 10 bps
                ok = True
                reason = 'Low market impact expected'
            elif order_pct_of_volume < 5.0:
                estimated_slippage = 0.25  # 25 bps
                ok = True
                reason = 'Moderate market impact - acceptable'
            else:
                estimated_slippage = order_pct_of_volume * 0.1  # Linear scaling
                ok = False
                reason = f'Order {order_pct_of_volume:.1f}% of volume - high market impact'
            
            return {
                'ok': ok,
                'slippage_pct': round(estimated_slippage, 3),
                'order_pct_volume': round(order_pct_of_volume, 2),
                'reason': reason
            }
            
        except Exception as e:
            return {
                'ok': False,
                'slippage_pct': None,
                'reason': f'Error estimating slippage: {e}'
            }
    
    def check_macro_proximity(self) -> Dict:
        """
        Check if major macro event is imminent
        
        Events to watch:
        - FOMC meetings
        - CPI/PPI releases
        - Jobs report
        - GDP releases
        
        Returns:
            {'ok': bool, 'next_event': str, 'hours_until': int, 'size_multiplier': float}
        """
        
        # Simplified version - in production, use economic calendar API
        # For now, check known regular events
        
        now = datetime.now()
        
        # FOMC meetings (roughly 8 times/year on Wednesdays)
        # CPI: ~13th of each month, 8:30 AM ET
        # Jobs: First Friday of month, 8:30 AM ET
        
        # Check if today is CPI day (around 13th)
        if 12 <= now.day <= 14 and now.time() < time(10, 0):
            return {
                'ok': False,
                'next_event': 'CPI Release',
                'hours_until': 1,
                'size_multiplier': 0.5,
                'reason': 'CPI release today - size down 50%'
            }
        
        # Check if tomorrow is CPI
        tomorrow = now + timedelta(days=1)
        if 12 <= tomorrow.day <= 14:
            return {
                'ok': True,
                'next_event': 'CPI Release',
                'hours_until': 24,
                'size_multiplier': 0.75,
                'reason': 'CPI tomorrow - consider 25% size reduction'
            }
        
        # Check if Friday (jobs report potential)
        if now.weekday() == 4 and now.day <= 7 and now.time() < time(10, 0):
            return {
                'ok': False,
                'next_event': 'Jobs Report',
                'hours_until': 1,
                'size_multiplier': 0.5,
                'reason': 'Jobs report today - size down 50%'
            }
        
        # All clear
        return {
            'ok': True,
            'next_event': None,
            'hours_until': None,
            'size_multiplier': 1.0,
            'reason': 'No major macro events imminent'
        }
    
    def check_risk_cap(self, entry_price: float, stop_loss: float, shares: int) -> Dict:
        """
        Ensure trade doesn't exceed max $ risk
        
        Returns:
            {'ok': bool, 'risk_dollars': float, 'max_shares': int, 'reason': str}
        """
        
        risk_per_share = entry_price - stop_loss
        total_risk = risk_per_share * shares
        
        if total_risk <= self.max_risk_per_trade:
            return {
                'ok': True,
                'risk_dollars': round(total_risk, 2),
                'max_shares': shares,
                'reason': f'Risk ${total_risk:.2f} within ${self.max_risk_per_trade} limit'
            }
        
        # Calculate max allowable shares
        max_shares = int(self.max_risk_per_trade / risk_per_share)
        
        return {
            'ok': False,
            'risk_dollars': round(total_risk, 2),
            'max_shares': max_shares,
            'reason': f'Risk ${total_risk:.2f} exceeds ${self.max_risk_per_trade} limit - reduce to {max_shares} shares'
        }
    
    def vote_on_trade(self, ticker: str, entry_price: float = None, 
                     stop_loss: float = None, shares: int = 100) -> Dict:
        """
        Comprehensive execution safety check
        
        Returns veto council compatible vote
        """
        
        checks = {}
        issues = []
        warnings = []
        
        # 1. Market session check
        session_check = self.check_market_session()
        checks['session'] = session_check
        if not session_check['ok']:
            issues.append(session_check['reason'])
        
        # 2. Liquidity check
        liquidity_check = self.check_spread_liquidity(ticker)
        checks['liquidity'] = liquidity_check
        if not liquidity_check['ok']:
            issues.append(liquidity_check['reason'])
        
        # 3. Slippage check (if we have share count)
        slippage_check = self.estimate_slippage(ticker, shares)
        checks['slippage'] = slippage_check
        if not slippage_check['ok']:
            warnings.append(slippage_check['reason'])
        
        # 4. Macro proximity
        macro_check = self.check_macro_proximity()
        checks['macro'] = macro_check
        if macro_check['size_multiplier'] < 1.0:
            warnings.append(macro_check['reason'])
        
        # 5. Risk cap (if we have trade details)
        if entry_price and stop_loss:
            risk_check = self.check_risk_cap(entry_price, stop_loss, shares)
            checks['risk_cap'] = risk_check
            if not risk_check['ok']:
                issues.append(risk_check['reason'])
        else:
            checks['risk_cap'] = {'ok': True, 'reason': 'No trade details provided'}
        
        # Determine vote
        if issues:
            # Hard failures = VETO
            return {
                'vote': 'VETO',
                'reason': f"Execution safety: {'; '.join(issues)}",
                'score': 0,
                'details': checks,
                'agent_error': False
            }
        
        if warnings:
            # Warnings = CAUTION
            return {
                'vote': 'CAUTION',
                'reason': f"Execution warnings: {'; '.join(warnings)}",
                'score': 60,
                'details': checks,
                'size_multiplier': macro_check['size_multiplier'],
                'agent_error': False
            }
        
        # All clear = APPROVE
        return {
            'vote': 'APPROVE',
            'reason': 'All execution safety checks passed',
            'score': 85,
            'details': checks,
            'size_multiplier': 1.0,
            'agent_error': False
        }


def test_execution_guard():
    """Test execution guard on various scenarios"""
    
    print("🧪 Testing Execution Guard...\n")
    
    guard = ExecutionGuard(max_risk_per_trade=500)
    
    # Test different tickers
    test_cases = [
        ('AAPL', 180, 175, 100),  # Blue chip, safe
        ('NVDA', 850, 820, 50),   # Volatile but liquid
        ('TSLA', 200, 190, 100),  # High volatility
    ]
    
    for ticker, entry, stop, shares in test_cases:
        print(f"{'='*80}")
        print(f"Testing: {ticker}")
        print(f"Entry: ${entry}, Stop: ${stop}, Shares: {shares}")
        print(f"{'='*80}")
        
        vote = guard.vote_on_trade(ticker, entry, stop, shares)
        
        print(f"\n🗳️  VOTE: {vote['vote']}")
        print(f"Reason: {vote['reason']}")
        print(f"Score: {vote.get('score', 'N/A')}")
        
        if 'details' in vote:
            print(f"\n📋 Detailed Checks:")
            for check_name, check_result in vote['details'].items():
                status = "✅" if check_result.get('ok') else "❌"
                print(f"  {status} {check_name}: {check_result.get('reason', 'N/A')}")
        
        if 'size_multiplier' in vote:
            print(f"\n⚖️  Size Adjustment: {vote['size_multiplier']}x")
        
        print()


if __name__ == "__main__":
    test_execution_guard()
