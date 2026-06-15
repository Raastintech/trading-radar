"""
Dynamic Spread Checker - VIX-Aware Slippage Protection

Adjusts bid-ask spread tolerance based on VIX:
- Low VIX: Tight spreads expected
- High VIX: Wider spreads acceptable (but still protected)

Prevents:
- Excessive slippage in volatile markets
- Missing opportunities due to overly tight limits
"""

from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class SpreadCheck:
    """Result of spread check"""
    allowed: bool
    spread_pct: float
    max_allowed_pct: float
    vix_level: float
    reason: str


class DynamicSpreadChecker:
    """
    VIX-aware spread checking
    
    Logic:
    - Higher VIX = Wider spreads expected
    - But still protect against excessive slippage
    - Balance: Opportunity vs Protection
    """
    
    # Spread tolerance by VIX regime
    SPREAD_TOLERANCE = {
        'CALM': 0.50,       # VIX <15: 0.50% max spread
        'NORMAL': 0.50,     # VIX 15-20: 0.50% max spread
        'ELEVATED': 0.75,   # VIX 20-25: 0.75% max spread (Remora active)
        'HIGH': 1.00,       # VIX 25-30: 1.00% max spread
        'EXTREME': 1.50,    # VIX >30: 1.50% max spread (or skip)
    }
    
    def __init__(self):
        print("💸 Dynamic Spread Checker initialized")
        print("   VIX-aware slippage protection")
        print("   Spread tolerance:")
        for regime, tolerance in self.SPREAD_TOLERANCE.items():
            print(f"      {regime}: {tolerance}%")
    
    def get_vix_regime(self, vix_level: float) -> str:
        """Determine VIX regime"""
        
        if vix_level < 15:
            return 'CALM'
        elif vix_level < 20:
            return 'NORMAL'
        elif vix_level < 25:
            return 'ELEVATED'
        elif vix_level < 30:
            return 'HIGH'
        else:
            return 'EXTREME'
    
    def check_spread(self, ticker: str, bid: float, ask: float, 
                     vix_level: float) -> SpreadCheck:
        """
        Check if bid-ask spread is acceptable for current VIX
        
        Args:
            ticker: Stock symbol
            bid: Current bid price
            ask: Current ask price
            vix_level: Current VIX level
        
        Returns:
            SpreadCheck with decision and reasoning
        """
        
        if bid <= 0 or ask <= 0:
            return SpreadCheck(
                allowed=False,
                spread_pct=0,
                max_allowed_pct=0,
                vix_level=vix_level,
                reason="Invalid bid/ask prices"
            )
        
        # Calculate spread
        mid_price = (bid + ask) / 2
        spread = ask - bid
        spread_pct = (spread / mid_price) * 100
        
        # Get VIX regime
        vix_regime = self.get_vix_regime(vix_level)
        
        # Get max allowed spread for this VIX level
        max_allowed_pct = self.SPREAD_TOLERANCE[vix_regime]
        
        # Check if acceptable
        allowed = spread_pct <= max_allowed_pct
        
        if allowed:
            reason = f"Spread {spread_pct:.2f}% ≤ {max_allowed_pct}% (VIX {vix_level:.1f} {vix_regime})"
        else:
            reason = f"Spread {spread_pct:.2f}% > {max_allowed_pct}% (VIX {vix_level:.1f} {vix_regime}) - TOO WIDE"
        
        return SpreadCheck(
            allowed=allowed,
            spread_pct=spread_pct,
            max_allowed_pct=max_allowed_pct,
            vix_level=vix_level,
            reason=reason
        )
    
    def get_execution_price(self, ticker: str, bid: float, ask: float,
                           side: str, vix_level: float) -> Optional[float]:
        """
        Get recommended execution price if spread acceptable
        
        Args:
            ticker: Stock symbol
            bid: Current bid
            ask: Current ask
            side: 'BUY' or 'SELL'
            vix_level: Current VIX
        
        Returns:
            Execution price if spread OK, None if too wide
        """
        
        # Check spread first
        spread_check = self.check_spread(ticker, bid, ask, vix_level)
        
        if not spread_check.allowed:
            return None
        
        # Use mid-price for execution (our smart limit order approach)
        mid_price = (bid + ask) / 2
        
        return mid_price


# =============================================================================
# Testing
# =============================================================================

def test_spread_checker():
    """Test dynamic spread checker"""
    
    checker = DynamicSpreadChecker()
    
    print("\n" + "="*70)
    print("🧪 TESTING DYNAMIC SPREAD CHECKER")
    print("="*70 + "\n")
    
    # Test scenarios
    scenarios = [
        # (ticker, bid, ask, vix, description)
        ("NVDA", 195.00, 195.50, 18.0, "Normal spread, normal VIX"),
        ("NVDA", 195.00, 196.50, 18.0, "Wide spread, normal VIX"),
        ("TSLA", 300.00, 302.00, 22.0, "Medium spread, elevated VIX"),
        ("TSLA", 300.00, 304.00, 22.0, "Wide spread, elevated VIX"),
        ("GME", 20.00, 20.50, 28.0, "Medium spread, high VIX"),
        ("GME", 20.00, 21.00, 28.0, "Very wide spread, high VIX"),
    ]
    
    for ticker, bid, ask, vix, desc in scenarios:
        print(f"Scenario: {desc}")
        print(f"  {ticker}: Bid ${bid:.2f}, Ask ${ask:.2f}, VIX {vix}")
        
        result = checker.check_spread(ticker, bid, ask, vix)
        
        status = "✅ ALLOWED" if result.allowed else "❌ REJECTED"
        print(f"  {status}")
        print(f"  {result.reason}")
        
        if result.allowed:
            exec_price = checker.get_execution_price(ticker, bid, ask, 'BUY', vix)
            print(f"  Execution price: ${exec_price:.2f}")
        
        print()


if __name__ == "__main__":
    test_spread_checker()
