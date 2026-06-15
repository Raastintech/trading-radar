"""
Adaptive Analysis Module - Handle ANY Data Availability

Professional solution for variable data conditions:
- Weekend data (limited bars)
- New IPOs (short history)
- Data gaps
- Market disruptions

Maintains statistical validity while adapting to constraints.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import statistics


class DataSufficiency(Enum):
    """Data sufficiency classification"""
    EXCELLENT = "EXCELLENT"    # 200+ bars
    GOOD = "GOOD"              # 150-199 bars
    ADEQUATE = "ADEQUATE"      # 100-149 bars
    MINIMAL = "MINIMAL"        # 60-99 bars
    INSUFFICIENT = "INSUFFICIENT"  # < 60 bars


@dataclass
class AdaptiveRequirements:
    """Adaptive data requirements"""
    bars_available: int
    sufficiency: DataSufficiency
    
    # Adaptive periods
    ma_long_period: int      # Adaptive 200-MA
    ma_medium_period: int    # Adaptive 50-MA
    ma_short_period: int     # Adaptive 20-MA
    rs_lookback: int         # Adaptive RS calculation
    atr_period: int          # Adaptive ATR
    
    # Confidence adjustments
    confidence_multiplier: float  # 0.6-1.0
    min_score_adjustment: int     # 0-15 points
    
    # Warnings
    warnings: List[str]


class AdaptiveDataAnalyzer:
    """
    Analyzes data availability and adjusts calculations
    
    Professional approach:
    - Detects actual bars available
    - Calculates optimal periods
    - Maintains statistical validity
    - Provides confidence scoring
    """
    
    # Ideal periods (what we want)
    IDEAL_MA_LONG = 200
    IDEAL_MA_MEDIUM = 50
    IDEAL_MA_SHORT = 20
    IDEAL_RS_LOOKBACK = 60
    IDEAL_ATR_PERIOD = 14
    
    # Minimum viable periods (what we need)
    MIN_MA_LONG = 100
    MIN_MA_MEDIUM = 30
    MIN_MA_SHORT = 10
    MIN_RS_LOOKBACK = 30
    MIN_ATR_PERIOD = 10
    
    def __init__(self):
        pass
    
    def analyze_data_requirements(self, bars: List) -> AdaptiveRequirements:
        """
        Analyze data and calculate adaptive requirements
        
        Returns complete adaptive requirements with confidence scoring
        """
        
        bars_available = len(bars)
        
        # Classify sufficiency
        if bars_available >= 200:
            sufficiency = DataSufficiency.EXCELLENT
            confidence_mult = 1.0
            score_adjustment = 0
            warnings = []
        
        elif bars_available >= 150:
            sufficiency = DataSufficiency.GOOD
            confidence_mult = 0.95
            score_adjustment = 3
            warnings = ["Using 150-199 bars (good quality)"]
        
        elif bars_available >= 100:
            sufficiency = DataSufficiency.ADEQUATE
            confidence_mult = 0.85
            score_adjustment = 8
            warnings = ["Using 100-149 bars (adequate quality)", "Reduce position sizes"]
        
        elif bars_available >= 60:
            sufficiency = DataSufficiency.MINIMAL
            confidence_mult = 0.70
            score_adjustment = 15
            warnings = [
                "Using 60-99 bars (minimal quality)",
                "Significantly reduce position sizes",
                "Higher uncertainty"
            ]
        
        else:
            sufficiency = DataSufficiency.INSUFFICIENT
            confidence_mult = 0.0
            score_adjustment = 100  # Essentially disqualifies
            warnings = ["Insufficient data - skip this ticker"]
        
        # Calculate adaptive periods
        ma_long = self._calculate_adaptive_period(
            bars_available, self.IDEAL_MA_LONG, self.MIN_MA_LONG
        )
        
        ma_medium = self._calculate_adaptive_period(
            bars_available, self.IDEAL_MA_MEDIUM, self.MIN_MA_MEDIUM
        )
        
        ma_short = self._calculate_adaptive_period(
            bars_available, self.IDEAL_MA_SHORT, self.MIN_MA_SHORT
        )
        
        rs_lookback = self._calculate_adaptive_period(
            bars_available, self.IDEAL_RS_LOOKBACK, self.MIN_RS_LOOKBACK
        )
        
        atr_period = self._calculate_adaptive_period(
            bars_available, self.IDEAL_ATR_PERIOD, self.MIN_ATR_PERIOD
        )
        
        return AdaptiveRequirements(
            bars_available=bars_available,
            sufficiency=sufficiency,
            ma_long_period=ma_long,
            ma_medium_period=ma_medium,
            ma_short_period=ma_short,
            rs_lookback=rs_lookback,
            atr_period=atr_period,
            confidence_multiplier=confidence_mult,
            min_score_adjustment=score_adjustment,
            warnings=warnings
        )
    
    def _calculate_adaptive_period(self, bars_available: int,
                                   ideal_period: int, min_period: int) -> int:
        """
        Calculate adaptive period based on data availability
        
        Logic:
        - If have ideal or more: use ideal
        - If between ideal and min: scale proportionally
        - If below min: use what's available (with warning)
        """
        
        if bars_available >= ideal_period:
            return ideal_period
        
        elif bars_available >= min_period:
            # Scale between min and ideal
            # Use 80% of available if above minimum
            return max(min_period, int(bars_available * 0.8))
        
        else:
            # Use what we have (will be flagged as insufficient)
            return max(10, int(bars_available * 0.6))
    
    def calculate_ma(self, bars: List, requirements: AdaptiveRequirements,
                    ma_type: str) -> float:
        """
        Calculate moving average with adaptive period
        
        Args:
            bars: Price bars
            requirements: Adaptive requirements
            ma_type: "long" (200), "medium" (50), or "short" (20)
        
        Returns:
            Moving average value
        """
        
        period_map = {
            'long': requirements.ma_long_period,
            'medium': requirements.ma_medium_period,
            'short': requirements.ma_short_period
        }
        
        period = period_map.get(ma_type, requirements.ma_medium_period)
        
        # Use adaptive period
        actual_bars = min(period, len(bars))
        
        if actual_bars < 10:
            return bars[-1]['close']  # Not enough data
        
        return statistics.mean([b['close'] for b in bars[-actual_bars:]])
    
    def calculate_rs_ratio(self, stock_bars: List, spy_bars: List,
                          requirements: AdaptiveRequirements) -> Optional[Dict]:
        """
        Calculate RS ratio with adaptive lookback
        
        Returns:
            {
                'ratio_current': float,
                'slope': float,
                'lookback_used': int,
                'data_quality': str
            }
        """
        
        lookback = requirements.rs_lookback
        
        # Need at least lookback period in both
        if len(stock_bars) < lookback or len(spy_bars) < lookback:
            return None
        
        try:
            # Calculate ratios over adaptive lookback
            ratios = []
            for i in range(-lookback, 0):
                stock_price = stock_bars[i]['close']
                spy_price = spy_bars[i]['close']
                ratios.append(stock_price / spy_price)
            
            ratio_current = ratios[-1]
            slope = self._calculate_slope(ratios)
            
            # Adjust slope significance based on data quality
            # Shorter lookback = less reliable slope
            slope_confidence = requirements.confidence_multiplier
            
            return {
                'ratio_current': ratio_current,
                'slope': slope,
                'slope_confidence': slope_confidence,
                'lookback_used': lookback,
                'data_quality': requirements.sufficiency.value
            }
        
        except:
            return None
    
    def _calculate_slope(self, values: List[float]) -> float:
        """Calculate linear regression slope"""
        
        n = len(values)
        if n < 2:
            return 0
        
        x = list(range(n))
        x_mean = statistics.mean(x)
        y_mean = statistics.mean(values)
        
        numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        
        return numerator / denominator if denominator != 0 else 0
    
    def calculate_atr(self, bars: List, requirements: AdaptiveRequirements) -> float:
        """Calculate ATR with adaptive period"""
        
        period = requirements.atr_period
        
        if len(bars) < period:
            # Use what we have
            period = max(5, len(bars) - 1)
        
        if len(bars) < 2:
            return 0
        
        try:
            true_ranges = []
            for i in range(1, min(len(bars), period + 1)):
                high = bars[-i]['high']
                low = bars[-i]['low']
                prev_close = bars[-(i+1)]['close']
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            return statistics.mean(true_ranges)
        except:
            return 0
    
    def adjust_score(self, base_score: int, requirements: AdaptiveRequirements) -> int:
        """
        Adjust score based on data quality
        
        Lower quality data = higher score threshold needed
        """
        
        # Apply confidence multiplier
        adjusted_score = int(base_score * requirements.confidence_multiplier)
        
        return adjusted_score
    
    def should_skip_ticker(self, requirements: AdaptiveRequirements) -> bool:
        """Check if ticker should be skipped due to insufficient data"""
        
        return requirements.sufficiency == DataSufficiency.INSUFFICIENT


# ============================================================================
# HELPER FUNCTIONS FOR INTEGRATION
# ============================================================================

def create_adaptive_analyzer() -> AdaptiveDataAnalyzer:
    """Factory function for adaptive analyzer"""
    return AdaptiveDataAnalyzer()


def get_adaptive_requirements(bars: List) -> AdaptiveRequirements:
    """Quick helper to get adaptive requirements"""
    analyzer = AdaptiveDataAnalyzer()
    return analyzer.analyze_data_requirements(bars)