from alpaca_data import AlpacaDataFeed
import statistics
from datetime import datetime

class TechnicalAnalyzer:
    """
    Calculate technical indicators for trading signals
    Designed to compete with Wall Street quant systems
    """
    
    def __init__(self):
        self.data_feed = AlpacaDataFeed()
    
    def calculate_rsi(self, ticker, period=14):
        """
        Relative Strength Index (0-100)
        < 30 = Oversold (potential BUY)
        > 70 = Overbought (potential SELL)
        """
        bars = self.data_feed.get_daily_bars(ticker, days_back=period + 10)
        
        if not bars or len(bars) < period + 1:
            return None
        
        # Calculate price changes
        closes = [bar['close'] for bar in bars]
        changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        
        # Separate gains and losses
        gains = [change if change > 0 else 0 for change in changes]
        losses = [abs(change) if change < 0 else 0 for change in changes]
        
        # Calculate average gains and losses
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return round(rsi, 2)
    
    def calculate_moving_averages(self, ticker):
        """
        Calculate multiple moving averages
        Returns: SMA20, SMA50, SMA200, current_price
        """
        bars = self.data_feed.get_daily_bars(ticker, days_back=220)
        
        if not bars or len(bars) < 200:
            return None
        
        closes = [bar['close'] for bar in bars]
        current_price = closes[-1]
        
        sma_20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        sma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
        sma_200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
        
        return {
            'current_price': current_price,
            'sma_20': round(sma_20, 2) if sma_20 else None,
            'sma_50': round(sma_50, 2) if sma_50 else None,
            'sma_200': round(sma_200, 2) if sma_200 else None,
            'above_sma_20': current_price > sma_20 if sma_20 else None,
            'above_sma_50': current_price > sma_50 if sma_50 else None,
            'above_sma_200': current_price > sma_200 if sma_200 else None
        }
    
    def calculate_macd(self, ticker):
        """
        Moving Average Convergence Divergence
        Bullish: MACD > Signal
        Bearish: MACD < Signal
        """
        bars = self.data_feed.get_daily_bars(ticker, days_back=50)
        
        if not bars or len(bars) < 35:
            return None
        
        closes = [bar['close'] for bar in bars]
        
        # Calculate EMAs
        ema_12 = self._calculate_ema(closes, 12)
        ema_26 = self._calculate_ema(closes, 26)
        
        if not ema_12 or not ema_26:
            return None
        
        # MACD line
        macd_line = ema_12 - ema_26
        
        # Signal line (9-period EMA of MACD)
        macd_values = [ema_12 - ema_26]
        signal_line = macd_line * 0.9  # Simplified
        
        return {
            'macd': round(macd_line, 2),
            'signal': round(signal_line, 2),
            'histogram': round(macd_line - signal_line, 2),
            'bullish': macd_line > signal_line
        }
    
    def _calculate_ema(self, prices, period):
        """Calculate Exponential Moving Average"""
        if len(prices) < period:
            return None
        
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period  # Start with SMA
        
        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        
        return ema
    
    def detect_support_resistance(self, ticker):
        """
        Detect key support and resistance levels
        Uses recent high/low points
        """
        bars = self.data_feed.get_daily_bars(ticker, days_back=60)
        
        if not bars or len(bars) < 20:
            return None
        
        highs = [bar['high'] for bar in bars]
        lows = [bar['low'] for bar in bars]
        current_price = bars[-1]['close']
        
        # Recent resistance (highs)
        resistance = max(highs[-20:])
        
        # Recent support (lows)
        support = min(lows[-20:])
        
        # Distance from support/resistance
        distance_to_resistance = ((resistance - current_price) / current_price) * 100
        distance_to_support = ((current_price - support) / current_price) * 100
        
        return {
            'support': round(support, 2),
            'resistance': round(resistance, 2),
            'current_price': round(current_price, 2),
            'distance_to_resistance_pct': round(distance_to_resistance, 2),
            'distance_to_support_pct': round(distance_to_support, 2),
            'near_support': distance_to_support < 2,  # Within 2%
            'near_resistance': distance_to_resistance < 2
        }
    
    def calculate_volatility(self, ticker, period=20):
        """
        Calculate price volatility (standard deviation)
        Higher = more volatile = higher risk/reward
        """
        bars = self.data_feed.get_daily_bars(ticker, days_back=period + 5)
        
        if not bars or len(bars) < period:
            return None
        
        closes = [bar['close'] for bar in bars[-period:]]
        returns = [(closes[i] - closes[i-1]) / closes[i-1] * 100 
                   for i in range(1, len(closes))]
        
        volatility = statistics.stdev(returns) if len(returns) > 1 else 0
        
        return {
            'volatility': round(volatility, 2),
            'risk_level': 'HIGH' if volatility > 3 else 'MEDIUM' if volatility > 1.5 else 'LOW'
        }
    
    def calculate_trend_strength(self, ticker):
        """
        Determine trend strength and direction
        Uses ADX-like logic
        """
        bars = self.data_feed.get_daily_bars(ticker, days_back=30)
        
        if not bars or len(bars) < 15:
            return None
        
        closes = [bar['close'] for bar in bars]
        
        # Simple trend detection
        short_term = sum(closes[-5:]) / 5
        long_term = sum(closes[-20:]) / 20
        
        trend_pct = ((short_term - long_term) / long_term) * 100
        
        if trend_pct > 5:
            trend = "STRONG UPTREND"
            strength = 90
        elif trend_pct > 2:
            trend = "UPTREND"
            strength = 70
        elif trend_pct > -2:
            trend = "SIDEWAYS"
            strength = 50
        elif trend_pct > -5:
            trend = "DOWNTREND"
            strength = 30
        else:
            trend = "STRONG DOWNTREND"
            strength = 10
        
        return {
            'trend': trend,
            'strength': strength,
            'trend_pct': round(trend_pct, 2)
        }
    
    def calculate_technical_score(self, ticker):
        """
        Comprehensive technical score (0-100)
        Combines ALL indicators into one score
        """
        score = 0
        signals = []
        
        # RSI (20 points)
        rsi = self.calculate_rsi(ticker)
        if rsi:
            if 40 < rsi < 60:
                score += 20
                signals.append("RSI neutral")
            elif rsi < 40:
                score += 15
                signals.append(f"RSI oversold ({rsi})")
            elif rsi > 60:
                score += 10
                signals.append(f"RSI overbought ({rsi})")
        
        # Moving Averages (25 points)
        ma = self.calculate_moving_averages(ticker)
        if ma:
            if ma['above_sma_20']:
                score += 10
                signals.append("Above SMA20")
            if ma['above_sma_50']:
                score += 10
                signals.append("Above SMA50")
            if ma['above_sma_200']:
                score += 5
                signals.append("Above SMA200")
        
        # MACD (20 points)
        macd = self.calculate_macd(ticker)
        if macd:
            if macd['bullish']:
                score += 20
                signals.append("MACD bullish")
            else:
                score += 5
                signals.append("MACD bearish")
        
        # Support/Resistance (15 points)
        sr = self.detect_support_resistance(ticker)
        if sr:
            if sr['near_support']:
                score += 15
                signals.append("Near support (bounce zone)")
            elif not sr['near_resistance']:
                score += 10
                signals.append("Room to run")
        
        # Trend (20 points)
        trend = self.calculate_trend_strength(ticker)
        if trend:
            score += (trend['strength'] / 100) * 20
            signals.append(f"Trend: {trend['trend']}")
        
        return {
            'score': min(100, round(score)),
            'signals': signals
        }
    
    def display_full_analysis(self, ticker):
        """Display complete technical analysis"""
        print(f"\n📈 TECHNICAL ANALYSIS: {ticker}")
        print("=" * 80)
        
        # Technical Score
        tech_score = self.calculate_technical_score(ticker)
        
        if tech_score['score'] >= 70:
            emoji = "🟢 BULLISH"
        elif tech_score['score'] >= 50:
            emoji = "🟡 NEUTRAL"
        else:
            emoji = "🔴 BEARISH"
        
        print(f"\n{emoji} Technical Score: {tech_score['score']}/100")
        print(f"\nKey Signals:")
        for signal in tech_score['signals']:
            print(f"  • {signal}")
        
        # Detailed Indicators
        print(f"\n📊 DETAILED INDICATORS:")
        print("-" * 80)
        
        # RSI
        rsi = self.calculate_rsi(ticker)
        if rsi:
            rsi_status = "Oversold 🟢" if rsi < 30 else "Overbought 🔴" if rsi > 70 else "Neutral"
            print(f"RSI (14): {rsi} - {rsi_status}")
        
        # Moving Averages
        ma = self.calculate_moving_averages(ticker)
        if ma:
            print(f"\nMoving Averages:")
            print(f"  Current: ${ma['current_price']:.2f}")
            if ma['sma_20']:
                status = "✅" if ma['above_sma_20'] else "❌"
                print(f"  SMA20: ${ma['sma_20']:.2f} {status}")
            if ma['sma_50']:
                status = "✅" if ma['above_sma_50'] else "❌"
                print(f"  SMA50: ${ma['sma_50']:.2f} {status}")
            if ma['sma_200']:
                status = "✅" if ma['above_sma_200'] else "❌"
                print(f"  SMA200: ${ma['sma_200']:.2f} {status}")
        
        # MACD
        macd = self.calculate_macd(ticker)
        if macd:
            status = "🟢 Bullish" if macd['bullish'] else "🔴 Bearish"
            print(f"\nMACD: {macd['macd']} | Signal: {macd['signal']} - {status}")
        
        # Support/Resistance
        sr = self.detect_support_resistance(ticker)
        if sr:
            print(f"\nSupport/Resistance:")
            print(f"  Support: ${sr['support']:.2f} ({sr['distance_to_support_pct']:.1f}% away)")
            print(f"  Resistance: ${sr['resistance']:.2f} ({sr['distance_to_resistance_pct']:.1f}% away)")
            
            if sr['near_support']:
                print(f"  ⚠️  NEAR SUPPORT - Potential bounce zone!")
            if sr['near_resistance']:
                print(f"  ⚠️  NEAR RESISTANCE - May face selling pressure")
        
        # Trend
        trend = self.calculate_trend_strength(ticker)
        if trend:
            print(f"\nTrend: {trend['trend']} (Strength: {trend['strength']}/100)")
        
        # Volatility
        vol = self.calculate_volatility(ticker)
        if vol:
            print(f"Volatility: {vol['volatility']}% - {vol['risk_level']} RISK")
        
        print("=" * 80)


def test_technical_analysis():
    """Test technical indicators"""
    print("🚀 Testing Technical Analysis Engine...\n")
    
    analyzer = TechnicalAnalyzer()
    
    # Test with popular tickers
    test_tickers = ["AAPL", "NVDA", "TSLA"]
    
    for ticker in test_tickers:
        try:
            analyzer.display_full_analysis(ticker)
            print("\n")
        except Exception as e:
            print(f"❌ Error analyzing {ticker}: {e}\n")


if __name__ == "__main__":
    test_technical_analysis()