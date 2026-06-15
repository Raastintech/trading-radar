"""
Trading Mode System - Short-Term vs Swing Trade Configurations

Allows flexible strategy deployment based on market conditions and goals

SHORT_TERM: 1-5 days, quick momentum plays, tight management
SWING: 1-4 weeks, trend following, patient capital deployment
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class ModeConfig:
    """Configuration for a trading mode"""
    
    name: str
    hold_period_min: int  # days
    hold_period_max: int  # days
    profit_target_1: float  # First partial exit %
    profit_target_2: float  # Second partial exit %
    profit_target_3: float  # Final target %
    stop_multiplier: float  # ATR multiplier for stops
    trailing_stop_activation: float  # % gain to activate trail
    trailing_stop_distance: float  # % distance for trail
    min_confluence: float  # Minimum confluence score
    scan_frequency: str  # 'DAILY' or 'WEEKLY'
    position_size_base: float  # Base position size multiplier
    description: str


class TradingModeManager:
    """
    Manage different trading modes with optimal parameters
    
    Each mode is optimized for different holding periods and objectives
    """
    
    MODES = {
        'SHORT_TERM': ModeConfig(
            name='SHORT_TERM',
            hold_period_min=1,
            hold_period_max=5,
            profit_target_1=0.08,   # Take 25% at +8%
            profit_target_2=0.15,   # Take 25% at +15%
            profit_target_3=0.25,   # Exit remaining at +25%
            stop_multiplier=1.0,    # Tight stops (1x ATR)
            trailing_stop_activation=0.10,  # Activate at +10%
            trailing_stop_distance=0.05,    # Trail 5% below high
            min_confluence=72,      # Need strong setup
            scan_frequency='DAILY',
            position_size_base=1.0,
            description='Fast momentum trades, 1-5 days, tight management'
        ),
        
        'SWING': ModeConfig(
            name='SWING',
            hold_period_min=7,
            hold_period_max=30,
            profit_target_1=0.20,   # Take 25% at +20%
            profit_target_2=0.35,   # Take 25% at +35%
            profit_target_3=0.50,   # Exit remaining at +50%
            stop_multiplier=1.5,    # Wider stops (1.5x ATR)
            trailing_stop_activation=0.15,  # Activate at +15%
            trailing_stop_distance=0.08,    # Trail 8% below high
            min_confluence=68,      # Can be more patient
            scan_frequency='WEEKLY',
            position_size_base=1.2,  # Slightly larger positions
            description='Trend following, 1-4 weeks, let winners run'
        ),
        
        'AGGRESSIVE': ModeConfig(
            name='AGGRESSIVE',
            hold_period_min=1,
            hold_period_max=3,
            profit_target_1=0.05,   # Take 50% at +5%
            profit_target_2=0.10,   # Take 25% at +10%
            profit_target_3=0.20,   # Exit remaining at +20%
            stop_multiplier=0.8,    # Very tight stops
            trailing_stop_activation=0.08,  # Activate at +8%
            trailing_stop_distance=0.03,    # Trail 3% below high
            min_confluence=75,      # Only best setups
            scan_frequency='DAILY',
            position_size_base=0.8,  # Smaller size (higher risk)
            description='Scalping mode, 1-3 days, quick profits'
        ),
        
        'CONSERVATIVE': ModeConfig(
            name='CONSERVATIVE',
            hold_period_min=14,
            hold_period_max=60,
            profit_target_1=0.25,   # Take 25% at +25%
            profit_target_2=0.50,   # Take 25% at +50%
            profit_target_3=0.75,   # Exit remaining at +75%
            stop_multiplier=2.0,    # Very wide stops
            trailing_stop_activation=0.20,  # Activate at +20%
            trailing_stop_distance=0.12,    # Trail 12% below high
            min_confluence=65,      # More permissive
            scan_frequency='WEEKLY',
            position_size_base=1.5,  # Larger positions
            description='Long-term positions, 2-8 weeks, maximum patience'
        )
    }
    
    def __init__(self, default_mode='SHORT_TERM'):
        """
        Initialize with default mode
        """
        self.current_mode = default_mode
        self.mode_history = []
        
        print(f"✅ Trading Mode Manager initialized")
        print(f"📊 Default mode: {default_mode}")
        print(f"💡 Available modes: {', '.join(self.MODES.keys())}")
    
    def get_mode(self, mode_name: Optional[str] = None) -> ModeConfig:
        """
        Get configuration for specified mode
        
        Args:
            mode_name: Mode to get (uses current_mode if None)
        
        Returns:
            ModeConfig object
        """
        mode = mode_name or self.current_mode
        
        if mode not in self.MODES:
            print(f"⚠️  Unknown mode: {mode}, using {self.current_mode}")
            mode = self.current_mode
        
        return self.MODES[mode]
    
    def set_mode(self, mode_name: str):
        """Change current trading mode"""
        
        if mode_name not in self.MODES:
            print(f"❌ Invalid mode: {mode_name}")
            print(f"   Available: {', '.join(self.MODES.keys())}")
            return False
        
        old_mode = self.current_mode
        self.current_mode = mode_name
        
        self.mode_history.append({
            'timestamp': datetime.now(),
            'old_mode': old_mode,
            'new_mode': mode_name
        })
        
        print(f"✅ Trading mode changed: {old_mode} → {mode_name}")
        self._print_mode_details(mode_name)
        
        return True
    
    def _print_mode_details(self, mode_name: str):
        """Print mode configuration details"""
        
        config = self.MODES[mode_name]
        
        print(f"\n📋 {config.name} MODE DETAILS:")
        print(f"   Description: {config.description}")
        print(f"   Hold Period: {config.hold_period_min}-{config.hold_period_max} days")
        print(f"   Profit Targets: {config.profit_target_1*100:.0f}% / {config.profit_target_2*100:.0f}% / {config.profit_target_3*100:.0f}%")
        print(f"   Stop Strategy: {config.stop_multiplier}x ATR")
        print(f"   Trailing Stop: Activate @ +{config.trailing_stop_activation*100:.0f}%, trail {config.trailing_stop_distance*100:.0f}%")
        print(f"   Min Confluence: {config.min_confluence}")
        print(f"   Scan Frequency: {config.scan_frequency}")
        print(f"   Position Size: {config.position_size_base}x base")
        print()
    
    def compare_modes(self):
        """Compare all available modes"""
        
        print(f"\n{'='*80}")
        print(f"📊 TRADING MODE COMPARISON")
        print(f"{'='*80}\n")
        
        for mode_name, config in self.MODES.items():
            marker = "👉" if mode_name == self.current_mode else "  "
            print(f"{marker} {mode_name}:")
            print(f"   {config.description}")
            print(f"   Hold: {config.hold_period_min}-{config.hold_period_max}d | "
                  f"Targets: {config.profit_target_1*100:.0f}%/{config.profit_target_2*100:.0f}%/{config.profit_target_3*100:.0f}% | "
                  f"Stop: {config.stop_multiplier}x ATR")
            print()
    
    def suggest_mode(self, market_conditions: Dict) -> str:
        """
        Suggest optimal mode based on market conditions
        
        Args:
            market_conditions: Dict with 'vix', 'spy_trend', etc.
        
        Returns:
            Suggested mode name
        """
        
        vix = market_conditions.get('vix_level', 15)
        spy_trend = market_conditions.get('spy_trend', 'BULL')
        volatility = market_conditions.get('volatility', 'MEDIUM')
        
        # High VIX (>25) = shorter timeframes
        if vix > 25:
            return 'AGGRESSIVE'
        
        # Bull market + low VIX = swing trades
        elif spy_trend == 'BULL' and vix < 18:
            return 'SWING'
        
        # Choppy market = short-term
        elif volatility == 'HIGH' or spy_trend == 'NEUTRAL':
            return 'SHORT_TERM'
        
        # Stable bull = conservative
        else:
            return 'CONSERVATIVE'
    
    def get_position_params(self, base_entry: float, base_stop: float, 
                           atr: float, mode: Optional[str] = None) -> Dict:
        """
        Calculate position parameters adjusted for current mode
        
        Args:
            base_entry: Entry price
            base_stop: Base stop loss
            atr: Average True Range
            mode: Override current mode
        
        Returns:
            Dict with adjusted entry/stop/targets
        """
        
        config = self.get_mode(mode)
        
        # Adjust stop loss based on mode
        stop_distance = atr * config.stop_multiplier
        adjusted_stop = base_entry - stop_distance
        
        # Calculate profit targets
        target_1 = base_entry * (1 + config.profit_target_1)
        target_2 = base_entry * (1 + config.profit_target_2)
        target_3 = base_entry * (1 + config.profit_target_3)
        
        # Calculate R:R
        risk = base_entry - adjusted_stop
        reward_1 = target_1 - base_entry
        reward_2 = target_2 - base_entry
        
        return {
            'mode': config.name,
            'entry_price': base_entry,
            'stop_loss': adjusted_stop,
            'stop_distance': stop_distance,
            'target_1': target_1,
            'target_2': target_2,
            'target_3': target_3,
            'risk_reward_1': reward_1 / risk if risk > 0 else 0,
            'risk_reward_2': reward_2 / risk if risk > 0 else 0,
            'trailing_stop_activation': config.trailing_stop_activation,
            'trailing_stop_distance': config.trailing_stop_distance,
            'hold_period_min': config.hold_period_min,
            'hold_period_max': config.hold_period_max,
            'position_size_multiplier': config.position_size_base
        }


# =============================================================================
# CLI Interface for Mode Selection
# =============================================================================

def select_trading_mode_interactive():
    """
    Interactive CLI for selecting trading mode
    """
    
    manager = TradingModeManager()
    
    print(f"\n{'='*80}")
    print(f"🎯 SELECT TRADING MODE")
    print(f"{'='*80}\n")
    
    manager.compare_modes()
    
    print(f"Current mode: {manager.current_mode}\n")
    
    while True:
        choice = input("Select mode (SHORT_TERM/SWING/AGGRESSIVE/CONSERVATIVE) or 'q' to quit: ").strip().upper()
        
        if choice == 'Q':
            break
        
        if manager.set_mode(choice):
            break
    
    return manager.current_mode


if __name__ == "__main__":
    # Demo
    print("🧪 Trading Mode Manager Demo\n")
    
    manager = TradingModeManager(default_mode='SHORT_TERM')
    
    # Compare all modes
    manager.compare_modes()
    
    # Example position calculation
    print(f"{'='*80}")
    print(f"📊 EXAMPLE: Position Parameters for Each Mode")
    print(f"{'='*80}\n")
    
    base_entry = 100.0
    base_stop = 95.0
    atr = 3.0
    
    for mode_name in ['SHORT_TERM', 'SWING', 'AGGRESSIVE', 'CONSERVATIVE']:
        params = manager.get_position_params(base_entry, base_stop, atr, mode_name)
        
        print(f"{mode_name}:")
        print(f"  Entry: ${params['entry_price']:.2f}")
        print(f"  Stop: ${params['stop_loss']:.2f} ({params['stop_distance']:.2f} distance)")
        print(f"  Target 1: ${params['target_1']:.2f} (R:R {params['risk_reward_1']:.2f})")
        print(f"  Target 2: ${params['target_2']:.2f} (R:R {params['risk_reward_2']:.2f})")
        print(f"  Hold: {params['hold_period_min']}-{params['hold_period_max']} days")
        print()
    
    # Test mode suggestion
    print(f"{'='*80}")
    print(f"💡 MODE SUGGESTIONS")
    print(f"{'='*80}\n")
    
    scenarios = [
        {'vix_level': 28, 'spy_trend': 'BULL', 'volatility': 'HIGH'},
        {'vix_level': 15, 'spy_trend': 'BULL', 'volatility': 'LOW'},
        {'vix_level': 20, 'spy_trend': 'NEUTRAL', 'volatility': 'MEDIUM'},
    ]
    
    for scenario in scenarios:
        suggestion = manager.suggest_mode(scenario)
        print(f"Market: VIX={scenario['vix_level']}, Trend={scenario['spy_trend']}")
        print(f"  → Suggested mode: {suggestion}\n")