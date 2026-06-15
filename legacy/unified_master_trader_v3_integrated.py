"""
Unified Master Trader V3 - With VIX Personalities & Dynamic Slippage

MAJOR ENHANCEMENTS:
1. VIX Personality System:
   - Sniper: Conservative (VIX <20)
   - Remora: Opportunistic (VIX <30 with dynamic sizing)

2. Dynamic Slippage Protection:
   - Adjusts spread tolerance based on VIX
   - Protects against excessive slippage
   - Doesn't miss opportunities

INTEGRATION POINTS:
- Import VIX personality and spread checker
- Initialize in __init__
- Use in scan_and_trade
- Apply to execution logic
"""

# Core imports (your existing imports)
# ... [Keep all your existing imports] ...

# NEW IMPORTS
from vix_personality_manager import VIXPersonalityManager
from dynamic_spread_checker import DynamicSpreadChecker


class UnifiedMasterTraderV3:
    """
    Complete V3 platform with VIX personalities and dynamic slippage
    """
    
    def __init__(self):
        print("\n" + "="*80)
        print("👑 UNIFIED MASTER TRADER V3 - WITH VIX PERSONALITIES")
        print("="*80 + "\n")
        
        # ... [Your existing initialization code] ...
        
        # NEW: VIX Personality Manager
        self.vix_personality = VIXPersonalityManager()
        
        # NEW: Dynamic Spread Checker
        self.spread_checker = DynamicSpreadChecker()
        
        print("\n✅ VIX PERSONALITY SYSTEM ACTIVE")
        print("   Sniper: Conservative (VIX <20)")
        print("   Remora: Opportunistic (VIX <30)")
        print("\n✅ DYNAMIC SLIPPAGE PROTECTION ENABLED")
        print("   Spread tolerance adjusts with VIX")
    
    def scan_and_trade(self):
        """
        Enhanced scan with VIX personalities and slippage protection
        """
        
        print("\n" + "="*80)
        print(f"🔎 V3 INTELLIGENT SCAN - {datetime.now().strftime('%I:%M %p ET')}")
        print("="*80 + "\n")
        
        # Get current VIX
        try:
            vix_bars = self.data_feed.get_daily_bars('VIX', days_back=1)
            current_vix = vix_bars[-1]['close'] if vix_bars else 20.0
        except:
            current_vix = 20.0
        
        # Get VIX-based strategy permissions
        vix_status = self.vix_personality.get_strategy_status(current_vix)
        
        # Get market state
        try:
            spy_bars = self.data_feed.get_daily_bars('SPY', days_back=2)
            spy_change = ((spy_bars[-1]['close'] - spy_bars[-2]['close']) / 
                         spy_bars[-2]['close']) * 100 if len(spy_bars) >= 2 else 0
        except:
            spy_change = 0
        
        spy_state = "BULL" if spy_change > 0.2 else "BEAR" if spy_change < -0.2 else "FLAT"
        
        # Print market state
        print(f"📊 Market State:")
        print(f"   SPY: {spy_state} ({spy_change:+.2f}%)")
        print(f"   VIX: {current_vix:.1f} ({vix_status['vix_regime']})")
        print()
        
        # Print strategy permissions
        print(f"🎭 Strategy Permissions:")
        print(f"   🎯 Sniper: {'✅ ACTIVE' if vix_status['sniper']['allowed'] else '❌ STANDBY'}")
        print(f"      {vix_status['sniper']['reason']}")
        print(f"   🦈 Remora: {'✅ HUNTING' if vix_status['remora']['allowed'] else '❌ STANDBY'}")
        print(f"      {vix_status['remora']['reason']}")
        if vix_status['remora']['allowed'] and vix_status['remora']['position_multiplier'] < 1.0:
            print(f"      ⚠️  Position sizing: {vix_status['remora']['position_multiplier']*100:.0f}%")
        print()
        
        # Print slippage protection
        vix_regime = self.vix_personality.get_vix_regime(current_vix).regime
        max_spread = self.spread_checker.SPREAD_TOLERANCE[vix_regime]
        print(f"💸 Slippage Protection:")
        print(f"   Max spread: {max_spread}% (VIX {vix_regime})")
        print()
        
        # Portfolio mode
        print(f"💼 Portfolio Mode: {self.portfolio_coordinator.mode}")
        print(f"   Allocation: Sniper {self.portfolio_coordinator.sniper_allocation}% | "
              f"Remora {self.portfolio_coordinator.remora_allocation}%")
        print()
        
        # Scan strategies
        sniper_opps = 0
        remora_opps = 0
        
        # SNIPER SCAN (only if VIX allows)
        if vix_status['sniper']['allowed']:
            print(f"🎯 Scanning Sniper Strategy (V3 Enhanced)...")
            sniper_opps = self._scan_sniper_with_vix(current_vix)
        else:
            print(f"🎯 Sniper Strategy: STANDBY")
            print(f"   VIX {current_vix:.1f} ≥ 20 (institutions sidelining)")
        
        print()
        
        # REMORA SCAN (if VIX allows, with position adjustment)
        if vix_status['remora']['allowed']:
            print(f"🦈 Scanning Remora Strategy (V3 Enhanced)...")
            remora_opps = self._scan_remora_with_vix(
                current_vix,
                vix_status['remora']['position_multiplier']
            )
        else:
            print(f"🦈 Remora Strategy: STANDBY")
            print(f"   VIX {current_vix:.1f} ≥ 30 (too chaotic)")
        
        print()
        
        # Summary
        total_opps = sniper_opps + remora_opps
        
        if total_opps == 0:
            print(f"📊 No opportunities met V3 criteria - Standing aside")
        else:
            print(f"📊 Found {total_opps} V3-validated opportunities")
            print(f"   Sniper: {sniper_opps} | Remora: {remora_opps}")
        
        print()
    
    def _scan_sniper_with_vix(self, current_vix: float) -> int:
        """Scan Sniper with VIX awareness"""
        
        # Your existing Sniper scan logic
        # But add spread checking before execution
        
        opportunities = 0
        
        # ... [Your existing Sniper scan code] ...
        
        # When ready to execute, check spread:
        # if self._check_spread_before_execution(ticker, current_vix):
        #     execute_trade()
        
        return opportunities
    
    def _scan_remora_with_vix(self, current_vix: float, 
                             position_multiplier: float) -> int:
        """Scan Remora with VIX-adjusted position sizing"""
        
        # Your existing Remora scan logic
        # But multiply position size by position_multiplier
        
        opportunities = 0
        
        # ... [Your existing Remora scan code] ...
        
        # Adjust position size:
        # base_shares = calculate_shares()
        # adjusted_shares = int(base_shares * position_multiplier)
        
        return opportunities
    
    def _check_spread_before_execution(self, ticker: str, 
                                       current_vix: float) -> bool:
        """
        Check spread before executing trade
        
        Returns True if spread acceptable, False if too wide
        """
        
        try:
            # Get current quote
            quote = self.data_feed.get_real_time_quote(ticker)
            
            if not quote:
                print(f"   ⚠️  No quote data for {ticker}")
                return False
            
            bid = quote.get('bid_price', 0)
            ask = quote.get('ask_price', 0)
            
            if bid <= 0 or ask <= 0:
                # Fallback to last price
                return True
            
            # Check spread
            spread_check = self.spread_checker.check_spread(
                ticker, bid, ask, current_vix
            )
            
            if spread_check.allowed:
                print(f"   ✅ Spread OK: {spread_check.reason}")
                return True
            else:
                print(f"   ❌ Spread too wide: {spread_check.reason}")
                print(f"   Skipping {ticker} to avoid excessive slippage")
                return False
        
        except Exception as e:
            print(f"   ⚠️  Spread check error: {e}")
            # Default to allowing if can't check
            return True
    
    def _execute_with_slippage_protection(self, ticker: str, shares: int,
                                          side: str, current_vix: float):
        """
        Execute trade with slippage protection
        
        Gets optimal execution price based on current spread and VIX
        """
        
        try:
            # Get quote
            quote = self.data_feed.get_real_time_quote(ticker)
            
            if not quote:
                print(f"   ❌ Cannot execute: No quote data")
                return None
            
            bid = quote.get('bid_price', 0)
            ask = quote.get('ask_price', 0)
            
            # Get execution price (mid-price if spread OK)
            exec_price = self.spread_checker.get_execution_price(
                ticker, bid, ask, side, current_vix
            )
            
            if not exec_price:
                print(f"   ❌ Spread too wide, skipping execution")
                return None
            
            # Execute at mid-price (smart limit order)
            print(f"   💰 Executing at ${exec_price:.2f} (mid-price)")
            
            # Your existing execution logic
            # result = self.smart_executor.execute_entry(ticker, shares)
            
            return exec_price
        
        except Exception as e:
            print(f"   ❌ Execution error: {e}")
            return None


# Your existing methods remain the same
# Just add the new VIX and slippage logic to scan_and_trade
