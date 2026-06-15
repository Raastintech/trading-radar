"""
VIX Personality Manager - Strategy-Specific VIX Handling

SNIPER (Institutional Shadow):
- Conservative, follows institutions
- VIX <20 = Active
- VIX ≥20 = Standby (institutions are sidelining)

REMORA (Flaw Finder):
- Opportunistic, hunts institutional mistakes
- VIX <30 = Active (more mistakes at higher VIX)
- Dynamic position sizing as VIX rises
- VIX 20-25 = 80% normal size
- VIX 25-30 = 60% normal size
"""

from typing import Dict
from dataclasses import dataclass


@dataclass
class VIXRegime:
    """VIX regime with strategy-specific interpretation"""
    level: float
    regime: str  # 'CALM', 'NORMAL', 'ELEVATED', 'HIGH', 'EXTREME'
    sniper_allowed: bool
    remora_allowed: bool
    remora_multiplier: float  # Position size adjustment for Remora


class VIXPersonalityManager:
    """
    Manage VIX-based behavior for dual strategies
    
    Key Innovation:
    - Sniper: Strict VIX threshold (follows institutions)
    - Remora: Permissive VIX threshold (hunts mistakes)
    - Dynamic position sizing for Remora
    """
    
    # VIX Thresholds
    SNIPER_VIX_MAX = 20.0    # Sniper stands down above this
    REMORA_VIX_MAX = 30.0    # Remora stands down above this
    
    # Remora position size adjustments by VIX level
    REMORA_SIZE_TIERS = {
        (0, 15): 1.0,      # VIX <15: Full size
        (15, 20): 1.0,     # VIX 15-20: Full size
        (20, 25): 0.8,     # VIX 20-25: 80% size (opportunity zone!)
        (25, 30): 0.6,     # VIX 25-30: 60% size (high risk, high reward)
    }
    
    def __init__(self):
        print("🎭 VIX Personality Manager initialized")
        print(f"   Sniper VIX Max: {self.SNIPER_VIX_MAX} (conservative)")
        print(f"   Remora VIX Max: {self.REMORA_VIX_MAX} (opportunistic)")
    
    def get_vix_regime(self, vix_level: float) -> VIXRegime:
        """
        Determine VIX regime and strategy permissions
        
        Returns:
            VIXRegime with strategy-specific settings
        """
        
        # Determine regime name
        if vix_level < 15:
            regime = 'CALM'
        elif vix_level < 20:
            regime = 'NORMAL'
        elif vix_level < 25:
            regime = 'ELEVATED'
        elif vix_level < 30:
            regime = 'HIGH'
        else:
            regime = 'EXTREME'
        
        # Sniper permission (strict)
        sniper_allowed = vix_level < self.SNIPER_VIX_MAX
        
        # Remora permission (permissive)
        remora_allowed = vix_level < self.REMORA_VIX_MAX
        
        # Remora position size multiplier
        remora_multiplier = self._get_remora_multiplier(vix_level)
        
        return VIXRegime(
            level=vix_level,
            regime=regime,
            sniper_allowed=sniper_allowed,
            remora_allowed=remora_allowed,
            remora_multiplier=remora_multiplier
        )
    
    def _get_remora_multiplier(self, vix_level: float) -> float:
        """
        Get Remora position size multiplier based on VIX
        
        Logic:
        - VIX <20: Full size (normal conditions)
        - VIX 20-25: 80% size (elevated but opportunity)
        - VIX 25-30: 60% size (high risk, reduce exposure)
        - VIX >30: 0% size (too chaotic, stand down)
        """
        
        if vix_level >= self.REMORA_VIX_MAX:
            return 0.0
        
        for (low, high), multiplier in self.REMORA_SIZE_TIERS.items():
            if low <= vix_level < high:
                return multiplier
        
        return 1.0  # Default
    
    def get_strategy_status(self, vix_level: float) -> Dict:
        """
        Get complete strategy status for current VIX
        
        Returns:
            {
                'vix_level': float,
                'vix_regime': str,
                'sniper': {
                    'allowed': bool,
                    'reason': str
                },
                'remora': {
                    'allowed': bool,
                    'position_multiplier': float,
                    'reason': str
                }
            }
        """
        
        regime = self.get_vix_regime(vix_level)
        
        # Sniper reasoning
        if regime.sniper_allowed:
            sniper_reason = f"VIX {vix_level:.1f} < {self.SNIPER_VIX_MAX} (institutions active)"
        else:
            sniper_reason = f"VIX {vix_level:.1f} ≥ {self.SNIPER_VIX_MAX} (institutions sidelining)"
        
        # Remora reasoning
        if not regime.remora_allowed:
            remora_reason = f"VIX {vix_level:.1f} ≥ {self.REMORA_VIX_MAX} (too chaotic)"
        elif regime.remora_multiplier == 1.0:
            remora_reason = f"VIX {vix_level:.1f} < 20 (normal operations)"
        else:
            remora_reason = f"VIX {vix_level:.1f} elevated (hunting mistakes, {regime.remora_multiplier*100:.0f}% size)"
        
        return {
            'vix_level': vix_level,
            'vix_regime': regime.regime,
            'sniper': {
                'allowed': regime.sniper_allowed,
                'reason': sniper_reason
            },
            'remora': {
                'allowed': regime.remora_allowed,
                'position_multiplier': regime.remora_multiplier,
                'reason': remora_reason
            }
        }
    
    def print_status(self, vix_level: float):
        """Print human-readable status"""
        
        status = self.get_strategy_status(vix_level)
        
        print("\n" + "="*70)
        print(f"🎭 VIX PERSONALITY STATUS")
        print("="*70)
        print(f"\n📊 VIX: {status['vix_level']:.1f} ({status['vix_regime']})")
        
        print(f"\n🎯 SNIPER (Institutional Shadow):")
        if status['sniper']['allowed']:
            print(f"   ✅ ACTIVE")
        else:
            print(f"   ❌ STANDBY")
        print(f"   {status['sniper']['reason']}")
        
        print(f"\n🦈 REMORA (Flaw Finder):")
        if status['remora']['allowed']:
            print(f"   ✅ HUNTING")
            if status['remora']['position_multiplier'] < 1.0:
                print(f"   ⚠️  Reduced sizing: {status['remora']['position_multiplier']*100:.0f}%")
        else:
            print(f"   ❌ STANDBY")
        print(f"   {status['remora']['reason']}")
        
        print("\n" + "="*70 + "\n")


# =============================================================================
# Testing
# =============================================================================

def test_vix_personalities():
    """Test VIX personality system"""
    
    manager = VIXPersonalityManager()
    
    # Test different VIX scenarios
    test_scenarios = [
        12.5,   # CALM - both active
        18.0,   # NORMAL - both active
        22.0,   # ELEVATED - only Remora (80% size)
        27.0,   # HIGH - only Remora (60% size)
        32.0,   # EXTREME - both standby
    ]
    
    for vix in test_scenarios:
        manager.print_status(vix)
        input("Press Enter for next scenario...")


if __name__ == "__main__":
    test_vix_personalities()
