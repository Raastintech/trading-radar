from alpaca_data import AlpacaDataFeed
from technical_indicators import TechnicalAnalyzer
from options_intelligence import OptionsIntelligence
from datetime import datetime, timedelta
import statistics


def _safe_float(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


class ConfluenceManager:
    """
    Wall Street-grade confluence scoring with adaptive thresholds
    Dual-mode system: Volatile stocks vs. Stable blue chips
    """
    
    def __init__(self):
        self.data_feed = AlpacaDataFeed()
        self.tech_analyzer = TechnicalAnalyzer()
        self.options_intel = OptionsIntelligence()
        
        # Market regime cache (SPY/QQQ trend)
        self.market_regime = None
        self.regime_last_updated = None
        
        # Signal tracking for time-decay
        self.active_signals = {}  # {ticker: {timestamp, score}}
        
        print("✅ ConfluenceManager initialized - Dual-mode adaptive system")
    
    def get_stock_profile(self, ticker):
        """
        Classify stock as VOLATILE or STABLE
        Returns: dict with profile info
        """
        bars = self.data_feed.get_daily_bars(ticker, days_back=30)
        
        if not bars or len(bars) < 20:
            return {'type': 'UNKNOWN', 'volatility': 0}
        
        # Calculate 30-day volatility
        closes = [bar['close'] for bar in bars]
        returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100 
                   for i in range(1, len(closes))]
        
        volatility = statistics.stdev(returns) if len(returns) > 1 else 0
        
        # Calculate average volume
        volumes = [bar['volume'] for bar in bars]
        avg_volume = sum(volumes) / len(volumes)
        
        # Classify
        if volatility > 3.0:  # 3%+ daily swings
            profile_type = "VOLATILE"
        else:
            profile_type = "STABLE"
        
        return {
            'type': profile_type,
            'volatility': round(volatility, 2),
            'avg_volume': int(avg_volume),
            'price': closes[-1]
        }
    
    def get_market_regime(self, force_refresh=False):
        """
        FILTER #2: Market Regime Filter
        Determines if SPY/QQQ are in uptrend or downtrend
        Adjusts whale score requirements based on market conditions
        """
        # Cache for 1 hour
        if (not force_refresh and self.regime_last_updated and 
            datetime.now() - self.regime_last_updated < timedelta(hours=1)):
            return self.market_regime
        
        # Check SPY trend
        spy_bars = self.data_feed.get_daily_bars('SPY', days_back=50)
        
        if not spy_bars or len(spy_bars) < 50:
            return {
                'status': 'UNKNOWN',
                'volatility': 'NORMAL',
                'whale_adjustment': 0
            }
        
        closes = [bar['close'] for bar in spy_bars]
        
        # Calculate moving averages
        sma_20 = sum(closes[-20:]) / 20
        sma_50 = sum(closes[-50:]) / 50
        current_price = closes[-1]
        
        # Calculate VIX proxy (volatility)
        returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100 
                   for i in range(1, len(closes))]
        market_volatility = statistics.stdev(returns[-20:]) if len(returns) >= 20 else 0
        
        # Determine regime
        if current_price > sma_20 > sma_50:
            status = "UPTREND"
            whale_adjustment = 0  # Normal requirements
        elif current_price < sma_20 < sma_50:
            status = "DOWNTREND"
            whale_adjustment = +10  # Require higher whale score (+10 points)
        else:
            status = "SIDEWAYS"
            whale_adjustment = +5  # Slightly stricter
        
        # Volatility check
        if market_volatility > 2.0:
            volatility_level = "HIGH"
            whale_adjustment += 5  # Even stricter in high vol
        else:
            volatility_level = "NORMAL"
        
        self.market_regime = {
            'status': status,
            'volatility': volatility_level,
            'whale_adjustment': whale_adjustment,
            'spy_price': current_price,
            'sma_20': round(sma_20, 2),
            'sma_50': round(sma_50, 2),
            'market_volatility': round(market_volatility, 2)
        }
        
        self.regime_last_updated = datetime.now()
        
        return self.market_regime
    
    def calculate_volume_ratio(self, ticker, profile):
        """
        FILTER #1: Volume-to-Average Ratio
        For blue chips, whale flow must exceed 30-day average significantly
        """
        bars = self.data_feed.get_daily_bars(ticker, days_back=30)
        
        if not bars or len(bars) < 20:
            return {
                'ratio': 1.0,
                'signal': 'NEUTRAL',
                'points': 0,
                'avg_volume': None,
                'current_volume': None,
                'status': 'INSUFFICIENT_BARS'
            }
        
        volumes = [bar['volume'] for bar in bars]
        avg_volume = sum(volumes[:-1]) / len(volumes[:-1])  # Exclude today
        current_volume = volumes[-1]
        
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        # Different thresholds for different stock types
        if profile['type'] == "VOLATILE":
            # Volatile stocks naturally have high volume swings
            if volume_ratio > 2.5:
                signal = "STRONG"
                points = 30
            elif volume_ratio > 1.8:
                signal = "MODERATE"
                points = 20
            else:
                signal = "WEAK"
                points = 10
        else:
            # STABLE stocks - unusual volume is MORE significant
            if volume_ratio > 2.0:
                signal = "STRONG"
                points = 35  # Higher weight!
            elif volume_ratio > 1.5:
                signal = "MODERATE"
                points = 25
            else:
                signal = "WEAK"
                points = 5
        
        return {
            'ratio': round(volume_ratio, 2),
            'signal': signal,
            'points': points,
            'avg_volume': int(avg_volume),
            'current_volume': int(current_volume)
        }
    
    def check_signal_freshness(self, ticker, whale_score):
        """
        FILTER #3: Time-Decay Filter
        Whale signals expire if price doesn't move within X days
        """
        now = datetime.now()
        
        # Check if we have an existing signal
        if ticker in self.active_signals:
            signal_data = self.active_signals[ticker]
            signal_age = (now - signal_data['timestamp']).days
            
            # Signal expiration rules
            if whale_score >= 80:
                max_age = 10  # Strong signals last 10 days
            elif whale_score >= 70:
                max_age = 7   # Moderate signals last 7 days
            else:
                max_age = 5   # Weak signals last 5 days
            
            if signal_age > max_age:
                # Signal expired
                del self.active_signals[ticker]
                return {
                    'status': 'EXPIRED',
                    'age_days': signal_age,
                    'max_age': max_age,
                    'penalty': -20  # Reduce score
                }
            else:
                return {
                    'status': 'ACTIVE',
                    'age_days': signal_age,
                    'max_age': max_age,
                    'penalty': 0
                }
        else:
            # New signal
            self.active_signals[ticker] = {
                'timestamp': now,
                'score': whale_score
            }
            return {
                'status': 'FRESH',
                'age_days': 0,
                'max_age': 0,
                'penalty': 0
            }

    def calculate_gamma_contribution(self, ticker):
        """
        Add gamma intelligence to confluence scoring
        Returns 0-30 points based on gamma favorability
        """
        try:
            gamma_score = self.options_intel.calculate_gamma_score(ticker)

            # Convert 0-100 gamma score to 0-30 points
            gamma_points = (gamma_score / 100) * 30

            return {
                'gamma_score': gamma_score,
                'gamma_points': round(gamma_points, 1),
                'favorable': gamma_score >= 60
            }
        except:
            # If gamma analysis fails, return neutral
            return {
                'gamma_score': 50,
                'gamma_points': 15,
                'favorable': None
            }
    
    def calculate_adaptive_confluence(self, ticker, whale_score, tech_score, momentum_score, risk_reward=0):
        """
        MASTER CONFLUENCE CALCULATOR
        Combines all filters with adaptive thresholds
        """
        
        # Step 1: Get stock profile
        profile = self.get_stock_profile(ticker)
        
        # Step 2: Get market regime (adjusts requirements)
        regime = self.get_market_regime()
        
        # Step 3: Calculate volume ratio
        volume_analysis = self.calculate_volume_ratio(ticker, profile)
        
        # Step 4: Check signal freshness (time decay)
        freshness = self.check_signal_freshness(ticker, whale_score)
        
        # Step 5: Get gamma intelligence
        gamma_data = self.calculate_gamma_contribution(ticker)

        # Step 6: Calculate base confluence score WITH GAMMA
        volume_points = _safe_float(volume_analysis.get('points'), 0.0)
        gamma_points = _safe_float(gamma_data.get('gamma_points'), 0.0)

        if profile['type'] == "VOLATILE":
            # VOLATILE STOCKS (RKLB, COIN, IREN)
            confluence = (
                whale_score * 0.35 +              # Reduced from 40%
                tech_score * 0.15 +               # Reduced from 20%
                momentum_score * 0.15 +           # Reduced from 20%
                volume_points * 0.15 +            # Reduced from 20%
                gamma_points                      # NEW: 30% max contribution!
            )
            required_threshold = 45
            
        else:
            # STABLE STOCKS (AAPL, NVDA, MSFT)
            confluence = (
                whale_score * 0.30 +
                tech_score * 0.20 +
                momentum_score * 0.10 +
                volume_points * 0.20 +
                gamma_points
            )
            required_threshold = 55
        
        # Step 7: Apply market regime adjustment
        whale_requirement = 70 + regime['whale_adjustment']
        
        # If whale score doesn't meet regime-adjusted requirement, penalize
        if whale_score < whale_requirement:
            confluence -= 10  # Penalty for weak whale score in bad market
        
        # Step 8: Apply time-decay penalty
        confluence += freshness['penalty']
        
        # Normalize risk/reward input (passed in from signal_generator)
        risk_reward = risk_reward or 0

        # INSTITUTIONAL-GRADE SIGNAL DETERMINATION
        # Multiple paths to BUY signal based on different alpha sources

        # Path 1: Classic high confluence
        if confluence >= required_threshold + 15:
            signal = "STRONG BUY"
            action = "High confluence across all factors"
            emoji = "🟢🟢"

        # Path 2: Standard confluence threshold
        elif confluence >= required_threshold:
            signal = "BUY"
            action = "Confluence threshold met"
            emoji = "🟢"

        # Path 3: ASYMMETRIC R:R OVERRIDE (Alpha generation)
        # Score just below threshold BUT exceptional risk/reward
        elif confluence >= (required_threshold - 5) and risk_reward >= 5.0:
            signal = "BUY"
            action = "ASYMMETRIC SETUP - Exceptional risk/reward overrides confluence gap"
            emoji = "🟢"
            # Boost displayed score to show it qualified via override
            confluence = required_threshold

        # Path 4: HIGH R:R with GAMMA FAVORABLE
        # Lower score acceptable if both gamma AND R:R are exceptional
        elif confluence >= (required_threshold - 10) and risk_reward >= 8.0 and gamma_data.get('gamma_score', 50) >= 70:
            signal = "BUY"
            action = "GAMMA + R:R SETUP - Dealer hedging will amplify move"
            emoji = "🟢"
            confluence = required_threshold - 2  # Show as close qualifier

        # Path 5: Watch zone - close to qualifying
        elif confluence >= (required_threshold - 10):
            signal = "WATCH"
            action = f"Monitor - {required_threshold - confluence:.1f} points from BUY"
            emoji = "🟡"

        # Path 6: Hold
        elif confluence >= 30:
            signal = "HOLD"
            action = "Wait for better setup"
            emoji = "🟠"

        # Path 7: Avoid
        else:
            signal = "AVOID"
            action = "Insufficient confluence"
            emoji = "🔴"
        
        return {
            'ticker': ticker,
            'confluence_score': round(confluence, 1),
            'signal': signal,
            'action': action,
            'emoji': emoji,
            'required_threshold': required_threshold,
            'profile': profile,
            'market_regime': regime,
            'volume_analysis': volume_analysis,
            'gamma_data': gamma_data,
            'freshness': freshness,
            'whale_requirement': whale_requirement,
            'breakdown': {
                'whale_score': whale_score,
                'tech_score': tech_score,
                'momentum_score': momentum_score,
                'volume_points': volume_points,
                'gamma_points': gamma_points
            }
        }
    
    def display_confluence_analysis(self, ticker, whale_score, tech_score, momentum_score):
        """Display detailed confluence breakdown"""
        
        result = self.calculate_adaptive_confluence(ticker, whale_score, tech_score, momentum_score)
        
        print(f"\n{'='*80}")
        print(f"🎯 CONFLUENCE ANALYSIS: {ticker}")
        print(f"{'='*80}")
        
        print(f"\n{result['emoji']} SIGNAL: {result['signal']}")
        print(f"Confluence Score: {result['confluence_score']:.1f}/{result['required_threshold']} (Threshold)")
        
        print(f"\n📊 STOCK PROFILE:")
        print(f"  Type: {result['profile']['type']}")
        print(f"  Volatility: {result['profile']['volatility']}%")
        print(f"  Avg Volume: {result['profile']['avg_volume']:,}")
        
        print(f"\n🌍 MARKET REGIME:")
        print(f"  Status: {result['market_regime']['status']}")
        print(f"  Volatility: {result['market_regime']['volatility']}")
        if 'spy_price' in result['market_regime']:
            print(f"  SPY: ${result['market_regime']['spy_price']:.2f}")
            print(f"  SMA20: ${result['market_regime']['sma_20']:.2f}")
            print(f"  SMA50: ${result['market_regime']['sma_50']:.2f}")
        print(f"  Whale Requirement: {result['whale_requirement']}+ (adjusted for regime)")
        
        print(f"\n📈 VOLUME ANALYSIS (Filter #1):")
        print(f"  Ratio: {result['volume_analysis']['ratio']}x average")
        print(f"  Signal: {result['volume_analysis']['signal']}")
        print(f"  Points: {result['volume_analysis']['points']}/35")
        
        print(f"\n⏰ SIGNAL FRESHNESS (Filter #3):")
        print(f"  Status: {result['freshness']['status']}")
        if result['freshness']['age_days'] > 0:
            print(f"  Age: {result['freshness']['age_days']} days")
            print(f"  Max Age: {result['freshness']['max_age']} days")
        
        print(f"\n🧮 SCORE BREAKDOWN:")
        print(f"  Whale: {result['breakdown']['whale_score']}/100")
        print(f"  Technical: {result['breakdown']['tech_score']}/100")
        print(f"  Momentum: {result['breakdown']['momentum_score']}/100")
        print(f"  Volume: {result['breakdown']['volume_points']}/35")
        
        print(f"\n{'='*80}\n")
        
        return result


def test_confluence():
    """Test the confluence manager"""
    print("🚀 Testing ConfluenceManager - Dual-Mode Adaptive System\n")
    
    manager = ConfluenceManager()
    
    # Test stocks
    test_cases = [
        ("RKLB", 70, 27, 50),   # Volatile stock
        ("AAPL", 35, 38, 30),   # Stable blue chip
        ("NVDA", 45, 40, 50),   # Stable but trending
    ]
    
    for ticker, whale, tech, momentum in test_cases:
        manager.display_confluence_analysis(ticker, whale, tech, momentum)


if __name__ == "__main__":
    test_confluence()
