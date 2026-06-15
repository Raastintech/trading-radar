from alpaca_data import AlpacaDataFeed
from technical_indicators import TechnicalAnalyzer
from datetime import datetime
from confluence_manager import ConfluenceManager
from whale_velocity import WhaleVelocityTracker

class MasterSignalGenerator:
    """
    Combines Whale Flow + Technical Analysis
    Wall Street-grade signal generation
    """
    
    def __init__(self):
        self.data_feed = AlpacaDataFeed()
        self.tech_analyzer = TechnicalAnalyzer()
        self.confluence_mgr = ConfluenceManager()  # ADD THIS
        self.velocity_tracker = WhaleVelocityTracker()
        print("✅ Whale Velocity Tracker initialized")
        print("✅ Master Signal Generator initialized (with ConfluenceManager)")
    
    def generate_composite_score(self, ticker):
        """
        Generate MASTER score (0-100)
        Weights:
        - Whale Activity: 40%
        - Technical Analysis: 40%
        - Momentum: 20%
        """
        
        # Whale score + velocity boost
        # Primary path: real SIP block prints via LiveFeed (score_ticker_from_live_feed)
        # Fallback:     daily bar volume acceleration (_calculate_velocity_boost)
        base_whale_score = self.data_feed.calculate_whale_score(ticker)

        live_result = self.velocity_tracker.score_ticker_from_live_feed(
            ticker, float(base_whale_score)
        )
        if live_result.get("block_print_count", 0) > 0:
            # Real block data from SIP — use directly
            whale_score    = min(100.0, live_result["enhanced_whale_score"])
            velocity_boost = 1.0 + live_result["velocity_boost_pct"] / 100.0
        else:
            # No live block data — fall back to daily-bar volume proxy
            bars = self.data_feed.get_daily_bars(ticker, days_back=20)
            velocity_boost = self._calculate_velocity_boost(ticker, bars)
            whale_score    = min(100.0, float(base_whale_score) * velocity_boost)

        velocity_score = max(0.0, min(100.0, 50.0 + ((velocity_boost - 1.0) / 0.5) * 50.0))
        
        # Get technical score
        tech_analysis = self.tech_analyzer.calculate_technical_score(ticker)
        tech_score = tech_analysis['score']
        
        # Calculate momentum score
        momentum_score = self._calculate_momentum_score(ticker)
        
        # Weighted composite
        composite = (
            whale_score * 0.40 +
            tech_score * 0.40 +
            momentum_score * 0.20
        )
        
        return {
            'composite_score': round(composite, 1),
            'whale_score': round(whale_score, 1),
            'base_whale_score': round(float(base_whale_score), 1),
            'velocity_boost': round(float(velocity_boost), 3),
            'velocity_score': round(velocity_score, 1),
            'technical_score': tech_score,
            'momentum_score': momentum_score,
            'breakdown': {
                'whale_contribution': round(whale_score * 0.40, 1),
                'technical_contribution': round(tech_score * 0.40, 1),
                'momentum_contribution': round(momentum_score * 0.20, 1)
            }
        }

    def _calculate_velocity_boost(self, ticker: str, bars: list) -> float:
        """
        Simple velocity boost using daily volume acceleration.
        Returns boost multiplier (1.0 = no boost, 1.5 = +50%).
        """
        if not bars or len(bars) < 10:
            return 1.0

        try:
            recent_vol = sum(b['volume'] for b in bars[-3:]) / 3
            baseline_vol = sum(b['volume'] for b in bars[-10:]) / 10

            if baseline_vol == 0:
                return 1.0

            vol_ratio = recent_vol / baseline_vol

            if vol_ratio > 3.0:
                boost = 1.5
                print(f"  ⚡ INSTANT volume surge: {vol_ratio:.1f}x baseline (+50% boost)")
            elif vol_ratio > 2.0:
                boost = 1.3
                print(f"  ⚡ URGENT volume surge: {vol_ratio:.1f}x baseline (+30% boost)")
            elif vol_ratio > 1.5:
                boost = 1.15
            else:
                boost = 1.0

            return boost
        except Exception:
            return 1.0
    
    def _calculate_momentum_score(self, ticker):
        """Calculate price momentum score"""
        bars = self.data_feed.get_daily_bars(ticker, days_back=10)
        
        if not bars or len(bars) < 5:
            return 50
        
        # Calculate consecutive up/down days
        closes = [bar['close'] for bar in bars[-5:]]
        
        up_days = 0
        for i in range(1, len(closes)):
            if closes[i] > closes[i-1]:
                up_days += 1
        
        # Score based on momentum
        if up_days >= 4:
            return 90  # Strong upward momentum
        elif up_days == 3:
            return 70
        elif up_days == 2:
            return 50
        elif up_days == 1:
            return 30
        else:
            return 10  # Strong downward momentum
    
    def generate_signal(self, ticker):
        """Generate signal using ConfluenceManager"""

        # Get component scores
        scores = self.generate_composite_score(ticker)
        whale_score = scores['whale_score']
        tech_score = scores['technical_score']
        momentum_score = scores['momentum_score']

        # Calculate R:R first
        sr = self.tech_analyzer.detect_support_resistance(ticker)
        rsi = self.tech_analyzer.calculate_rsi(ticker)

        if sr:
            current_price = sr['current_price']
            entry_price = current_price
            stop_loss = sr['support'] * 0.98
            target_price = sr['resistance'] * 0.98

            risk = entry_price - stop_loss
            reward = target_price - entry_price
            risk_reward = reward / risk if risk > 0 else 0
            sr_method = "SUPPORT_RESISTANCE"
        else:
            # Fallback: ATR-based levels when S/R is missing (ATH, new regimes, etc.)
            bars = self.data_feed.get_daily_bars(ticker, days_back=30)

            if bars and len(bars) >= 16:
                entry_price = bars[-1]['close']

                highs = [b['high'] for b in bars]
                lows = [b['low'] for b in bars]
                closes = [b['close'] for b in bars]

                # True Range-based ATR(14)
                trs = []
                for i in range(-14, 0):
                    prev_close = closes[i - 1]
                    tr = max(
                        highs[i] - lows[i],
                        abs(highs[i] - prev_close),
                        abs(lows[i] - prev_close)
                    )
                    trs.append(tr)

                atr = sum(trs) / len(trs)

                stop_loss = entry_price - (1.5 * atr)
                target_price = entry_price + (3.0 * atr)

                risk = entry_price - stop_loss
                reward = target_price - entry_price
                risk_reward = reward / risk if risk > 0 else 0

                sr_method = "ATR_FALLBACK"
            else:
                entry_price = None
                stop_loss = None
                target_price = None
                risk_reward = 0
                sr_method = "NONE"

        # Now pass R:R to confluence manager
        confluence_result = self.confluence_mgr.calculate_adaptive_confluence(
            ticker, whale_score, tech_score, momentum_score, risk_reward
        )

        signal_data = {
            'ticker': ticker,
            'signal': confluence_result['signal'],
            'action': self._get_action_text(confluence_result),
            'emoji': confluence_result['emoji'],
            'composite_score': confluence_result['confluence_score'],
            'confluence_data': confluence_result,
            'scores': scores,
            'entry_price': round(entry_price, 2) if entry_price else None,
            'stop_loss': round(stop_loss, 2) if stop_loss else None,
            'target_price': round(target_price, 2) if target_price else None,
            'sr_method': sr_method,
            'risk_reward_ratio': round(risk_reward, 2) if risk_reward else None,
            'rsi': rsi,
            'velocity_score': scores.get('velocity_score'),
            'velocity_boost': scores.get('velocity_boost'),
            'base_whale_score': scores.get('base_whale_score'),
            'timestamp': datetime.now()
        }

        # Canonicalize RR for downstream consumers.
        rr = signal_data.get("risk_reward_ratio")
        if rr is not None:
            try:
                signal_data["rr"] = float(rr)
            except Exception:
                pass

        return signal_data

    def _get_action_text(self, confluence_result):
        """Generate action recommendation based on confluence"""
        signal = confluence_result['signal']
        profile = confluence_result['profile']['type']
        
        if signal == "STRONG BUY":
            return f"Enter position now - Strong confluence ({profile} stock)"
        elif signal == "BUY":
            return f"Good entry opportunity - Multiple factors aligned ({profile})"
        elif signal == "WATCH":
            return f"Monitor closely - Needs more confirmation ({profile})"
        else:
            return f"Wait for better setup - Insufficient confluence ({profile})"
    
    def scan_watchlist(self, tickers):
        """
        Scan multiple tickers and rank by signal strength
        """
        signals = []
        
        for ticker in tickers:
            try:
                signal = self.generate_signal(ticker)
                signals.append(signal)
            except Exception as e:
                print(f"⚠️  Error scanning {ticker}: {e}")
        
        # Sort by composite score (highest first)
        signals.sort(key=lambda x: x['composite_score'], reverse=True)
        
        return signals
    
    def display_signal(self, ticker):
        """Display detailed signal for a ticker"""
        signal_data = self.generate_signal(ticker)
        
        print(f"\n{'='*80}")
        print(f"🎯 TRADING SIGNAL: {ticker}")
        print(f"{'='*80}")
        
        print(f"\n{signal_data['emoji']} SIGNAL: {signal_data['signal']}")
        print(f"Action: {signal_data['action']}")
        print(f"Composite Score: {signal_data['composite_score']}/100")
        
        print(f"\n📊 SCORE BREAKDOWN:")
        print(f"  Whale Activity: {signal_data['scores']['whale_score']}/100 "
              f"(Weight: {signal_data['scores']['breakdown']['whale_contribution']})")
        print(f"  Technical: {signal_data['scores']['technical_score']}/100 "
              f"(Weight: {signal_data['scores']['breakdown']['technical_contribution']})")
        print(f"  Momentum: {signal_data['scores']['momentum_score']}/100 "
              f"(Weight: {signal_data['scores']['breakdown']['momentum_contribution']})")
        
        if signal_data['entry_price']:
            print(f"\n💰 TRADE SETUP:")
            print(f"  Entry Price: ${signal_data['entry_price']}")
            print(f"  Stop Loss: ${signal_data['stop_loss']}")
            print(f"  Target Price: ${signal_data['target_price']}")
            print(f"  Risk/Reward: 1:{signal_data['risk_reward_ratio']}")
            
            if signal_data['risk_reward_ratio'] >= 2:
                print(f"  ✅ Favorable risk/reward!")
            elif signal_data['risk_reward_ratio'] >= 1:
                print(f"  ⚠️  Acceptable risk/reward")
            else:
                print(f"  ❌ Poor risk/reward - consider waiting")
        
        print(f"\n⏰ Generated: {signal_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*80}\n")
    
    def display_watchlist_scan(self, tickers):
        """Display ranked watchlist"""
        print(f"\n{'='*80}")
        print(f"🔍 WATCHLIST SCAN - {len(tickers)} STOCKS")
        print(f"{'='*80}\n")
        
        signals = self.scan_watchlist(tickers)
        
        print(f"{'RANK':<6} {'TICKER':<8} {'SIGNAL':<12} {'SCORE':<8} {'ENTRY':<10} {'R:R':<8}")
        print("-" * 80)
        
        for i, signal in enumerate(signals, 1):
            entry = f"${signal['entry_price']}" if signal['entry_price'] else "N/A"
            rr = f"1:{signal['risk_reward_ratio']}" if signal['risk_reward_ratio'] else "N/A"
            
            print(f"{i:<6} {signal['ticker']:<8} {signal['emoji']} {signal['signal']:<10} "
                  f"{signal['composite_score']:<8} {entry:<10} {rr:<8}")
        
        print("\n" + "="*80)
        
        # Show top 3 in detail
        print(f"\n🏆 TOP 3 OPPORTUNITIES:\n")
        for signal in signals[:3]:
            self.display_signal(signal['ticker'])


def test_signal_generator():
    """Test the master signal generator"""
    print("🚀 Testing Master Signal Generator...\n")
    
    generator = MasterSignalGenerator()
    
    # Use watchlist from config file (automatically updates with any changes)
    from config import TradingConfig
    config = TradingConfig()
    watchlist = config.get_watchlist()
    
    print(f"📊 Scanning {len(watchlist)} stocks from config...\n")
    
    # Scan and display
    generator.display_watchlist_scan(watchlist)


if __name__ == "__main__":
    test_signal_generator()
