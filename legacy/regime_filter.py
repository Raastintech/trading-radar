"""
Market Regime Filter - Institutional Risk Management

Gemini's Wall Street Framework:
1. SPY 200-Day MA: Market structure (bull vs bear)
2. VIX Panic Level: Volatility regime (calm vs storm)
3. Integration: 8th agent in Veto Council

Rules:
- SPY below 200-MA → BEAR MARKET → 50% position size
- VIX > 30 → PANIC MODE → VETO all new entries
- SPY above 200-MA + VIX < 20 → BULL MARKET → 100% size
- SPY above 200-MA + VIX 20-30 → CAUTION → 75% size

This is how professionals survive crashes.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional
from alpaca_data import AlpacaDataFeed
import yfinance as yf
import time
import random

class RegimeFilter:
    """
    Market regime filter - protects capital during storms
    
    The 200-Day MA Rule:
    - Above 200-MA = Bull market (institutions buying)
    - Below 200-MA = Bear market (institutions selling)
    
    The VIX Panic Rule:
    - VIX < 20 = Calm (normal trading)
    - VIX 20-30 = Elevated (caution)
    - VIX > 30 = PANIC (stop trading!)
    """
    
    def __init__(self):
        self.data_feed = AlpacaDataFeed()
        self._cache = {}
        self._cache_ttl_seconds = 30
        
        # VIX thresholds (Yahoo Finance ^VIX path)
        self.vix_calm = 20      # < 20 = CALM
        self.vix_elevated = 25  # 20-25 = ELEVATED (caution, 75% size)
        self.vix_panic = 30     # >= 30 = PANIC (no new trades)
        
        # Position size adjustments
        self.bull_size = 1.0      # 100% size
        self.caution_size = 0.75  # 75% size
        self.bear_size = 0.5      # 50% size
        self.panic_size = 0.0     # NO TRADING
        
        print("✅ Regime Filter initialized (Gemini spec)")
        print("📊 SPY 200-MA + VIX monitoring active")

    def _cache_get(self, key: str):
        rec = self._cache.get(key)
        if not rec:
            return None
        ts, payload = rec
        if (time.time() - ts) <= self._cache_ttl_seconds:
            return payload
        try:
            del self._cache[key]
        except Exception:
            pass
        return None

    def _cache_set(self, key: str, payload: Dict):
        try:
            self._cache[key] = (time.time(), payload)
        except Exception:
            pass

    def _download_with_retry(self, ticker: str, period: str):
        max_attempts = 4
        for attempt in range(1, max_attempts + 1):
            try:
                data = yf.download(ticker, period=period, progress=False, threads=False)
                if data is not None and not data.empty:
                    return data
            except Exception:
                pass

            if attempt < max_attempts:
                delay = min(3.5, 0.35 * (2 ** (attempt - 1))) + random.uniform(0.05, 0.25)
                time.sleep(delay)
        return None

    def _classify_vix(self, vix_level: float) -> Dict:
        if vix_level < self.vix_calm:
            regime = 'CALM'
            reason = f'VIX {vix_level:.1f} < {self.vix_calm} - market is calm'
        elif vix_level < self.vix_elevated:
            regime = 'ELEVATED'
            reason = f'VIX {vix_level:.1f} elevated ({self.vix_calm}-{self.vix_elevated}) - proceed with caution'
        elif vix_level < self.vix_panic:
            regime = 'ELEVATED'
            reason = f'VIX {vix_level:.1f} high ({self.vix_elevated}-{self.vix_panic}) - significant caution'
        else:
            regime = 'PANIC'
            reason = f'VIX {vix_level:.1f} >= {self.vix_panic} - PANIC MODE! 🚨'

        return {
            'regime': regime,
            'vix_level': round(vix_level, 2),
            'reason': reason
        }
    
    def get_spy_regime(self) -> Dict:
        """Check SPY vs 200-day moving average"""
        cached = self._cache_get("SPY_REGIME")
        if cached is not None:
            return cached

        try:
            # Get SPY data (250 days for 200-MA calculation)
            spy_data = self._download_with_retry('SPY', period='1y')

            if spy_data is not None and len(spy_data) >= 200:
                ma_200 = spy_data['Close'].rolling(window=200).mean().iloc[-1].item()
                spy_price = spy_data['Close'].iloc[-1].item()
                distance_pct = ((spy_price - ma_200) / ma_200) * 100

                if spy_price > ma_200:
                    regime = 'BULL'
                    reason = f'SPY ${spy_price:.2f} above 200-MA ${ma_200:.2f} (+{distance_pct:.1f}%)'
                else:
                    regime = 'BEAR'
                    reason = f'SPY ${spy_price:.2f} below 200-MA ${ma_200:.2f} ({distance_pct:.1f}%)'

                out = {
                    'regime': regime,
                    'spy_price': round(spy_price, 2),
                    'ma_200': round(ma_200, 2),
                    'distance_pct': round(distance_pct, 2),
                    'reason': reason,
                    'source': 'yahoo'
                }
                self._cache_set("SPY_REGIME", out)
                return out

            # Fallback path: use Alpaca daily bars for SPY
            bars = self.data_feed.get_daily_bars('SPY', days_back=260)
            if bars and len(bars) >= 200:
                closes = [float(b['close']) for b in bars]
                ma_window = closes[-200:]
                ma_200 = sum(ma_window) / len(ma_window)
                spy_price = closes[-1]
                distance_pct = ((spy_price - ma_200) / ma_200) * 100 if ma_200 else 0

                if spy_price > ma_200:
                    regime = 'BULL'
                    reason = f'SPY ${spy_price:.2f} above 200-MA ${ma_200:.2f} (+{distance_pct:.1f}%) [alpaca fallback]'
                else:
                    regime = 'BEAR'
                    reason = f'SPY ${spy_price:.2f} below 200-MA ${ma_200:.2f} ({distance_pct:.1f}%) [alpaca fallback]'

                out = {
                    'regime': regime,
                    'spy_price': round(spy_price, 2),
                    'ma_200': round(ma_200, 2),
                    'distance_pct': round(distance_pct, 2),
                    'reason': reason,
                    'source': 'alpaca_fallback'
                }
                self._cache_set("SPY_REGIME", out)
                return out

            out = {
                'regime': 'UNKNOWN',
                'reason': 'Insufficient SPY data from Yahoo and Alpaca'
            }
            self._cache_set("SPY_REGIME", out)
            return out

        except Exception as e:
            out = {
                'regime': 'UNKNOWN',
                'reason': f'Error checking SPY: {e}'
            }
            self._cache_set("SPY_REGIME", out)
            return out
    
    def get_vix_regime(self) -> Dict:
        """Check VIX (fear gauge) via Yahoo Finance (^VIX)."""
        cached = self._cache_get("VIX_REGIME")
        if cached is not None:
            return cached

        try:
            vix_data = self._download_with_retry('^VIX', period='5d')
            if vix_data is not None and len(vix_data) >= 1:
                vix_level = float(vix_data['Close'].iloc[-1].item())
                out = self._classify_vix(vix_level)
                out['source'] = 'yahoo'
                self._cache_set("VIX_REGIME", out)
                return out

            # Fallback: use VXX as volatility proxy from Alpaca
            proxy_bars = self.data_feed.get_daily_bars('VXX', days_back=5)
            if proxy_bars and len(proxy_bars) >= 1:
                proxy_level = float(proxy_bars[-1]['close'])
                # Rough proxy map to VIX-like scale for thresholding.
                vix_equiv = max(10.0, min(60.0, proxy_level * 0.85))
                out = self._classify_vix(vix_equiv)
                out['proxy_ticker'] = 'VXX'
                out['proxy_price'] = round(proxy_level, 2)
                out['source'] = 'alpaca_proxy'
                out['reason'] = f"{out['reason']} [VXX proxy ${proxy_level:.2f}]"
                self._cache_set("VIX_REGIME", out)
                return out

            out = {
                'regime': 'UNKNOWN',
                'vix_level': None,
                'reason': 'Cannot fetch VIX data from Yahoo or VXX proxy'
            }
            self._cache_set("VIX_REGIME", out)
            return out

        except Exception as e:
            out = {
                'regime': 'UNKNOWN',
                'vix_level': None,
                'reason': f'Error checking VIX: {e}'
            }
            self._cache_set("VIX_REGIME", out)
            return out
    
    def get_combined_regime(self) -> Dict:
        """
        Combine SPY and VIX into overall market regime
        
        Decision Matrix:
        
        | SPY Regime | VIX Regime | Action        | Size    |
        |------------|-----------|---------------|---------|
        | BULL       | CALM      | FULL SPEED    | 100%    |
        | BULL       | ELEVATED  | CAUTION       | 75%     |
        | BULL       | PANIC     | VETO          | 0%      |
        | BEAR       | CALM      | BEAR MARKET   | 50%     |
        | BEAR       | ELEVATED  | BEAR MARKET   | 50%     |
        | BEAR       | PANIC     | VETO          | 0%      |
        
        Returns:
            Combined regime assessment
        """
        
        spy_regime = self.get_spy_regime()
        vix_regime = self.get_vix_regime()
        
        spy_status = spy_regime.get('regime', 'UNKNOWN')
        vix_status = vix_regime.get('regime', 'UNKNOWN')
        
        # =================================================================
        # REGIME DECISION MATRIX
        # =================================================================
        
        # PANIC MODE: VIX > 30 (ALWAYS VETO)
        if vix_status == 'PANIC':
            return {
                'overall_regime': 'PANIC',
                'action': 'VETO',
                'size_multiplier': self.panic_size,
                'spy_regime': spy_status,
                'vix_regime': vix_status,
                'spy_details': spy_regime,
                'vix_details': vix_regime,
                'reason': f"🚨 PANIC MODE: {vix_regime['reason']} - NO NEW TRADES"
            }
        
        # BEAR MARKET: SPY below 200-MA (50% SIZE)
        if spy_status == 'BEAR':
            if vix_status == 'ELEVATED':
                multiplier = self.bear_size * 0.75  # Extra caution in bear + elevated VIX
            else:
                multiplier = self.bear_size
            
            return {
                'overall_regime': 'BEAR_MARKET',
                'action': 'REDUCE_SIZE',
                'size_multiplier': multiplier,
                'spy_regime': spy_status,
                'vix_regime': vix_status,
                'spy_details': spy_regime,
                'vix_details': vix_regime,
                'reason': f"🐻 BEAR MARKET: {spy_regime['reason']} - Reduce size to {multiplier*100:.0f}%"
            }
        
        # BULL + ELEVATED VIX (75% SIZE)
        if spy_status == 'BULL' and vix_status == 'ELEVATED':
            return {
                'overall_regime': 'BULL_CAUTION',
                'action': 'CAUTION',
                'size_multiplier': self.caution_size,
                'spy_regime': spy_status,
                'vix_regime': vix_status,
                'spy_details': spy_regime,
                'vix_details': vix_regime,
                'reason': f"🟡 BULL + ELEVATED VIX: Reduce to {self.caution_size*100:.0f}% size"
            }
        
        # BULL + CALM (IDEAL CONDITIONS - 100% SIZE)
        if spy_status == 'BULL' and vix_status == 'CALM':
            return {
                'overall_regime': 'BULL_TRENDING',
                'action': 'FULL_SPEED',
                'size_multiplier': self.bull_size,
                'spy_regime': spy_status,
                'vix_regime': vix_status,
                'spy_details': spy_regime,
                'vix_details': vix_regime,
                'reason': f"🟢 BULL TRENDING: {spy_regime['reason']}, {vix_regime['reason']}"
            }
        
        # UNKNOWN/ERROR (DEFAULT TO CAUTION)
        return {
            'overall_regime': 'UNKNOWN',
            'action': 'CAUTION',
            'size_multiplier': self.caution_size,
            'spy_regime': spy_status,
            'vix_regime': vix_status,
            'spy_details': spy_regime,
            'vix_details': vix_regime,
            'reason': 'Unable to determine regime - default to 75% size'
        }
    
    def vote_on_trade(self, ticker: str) -> Dict:
        """
        Veto Council compatible vote
        
        Returns:
            {
                'vote': 'APPROVE' | 'CAUTION' | 'VETO',
                'reason': str,
                'score': float,
                'details': dict,
                'size_multiplier': float
            }
        """
        
        regime = self.get_combined_regime()
        
        action = regime['action']
        
        # VETO in PANIC
        if action == 'VETO':
            return {
                'vote': 'VETO',
                'reason': regime['reason'],
                'score': 0,
                'details': regime,
                'size_multiplier': 0,
                'agent_error': False
            }
        
        # CAUTION in uncertain conditions
        if action in ['CAUTION', 'REDUCE_SIZE']:
            score = 50 if action == 'CAUTION' else 40
            return {
                'vote': 'CAUTION',
                'reason': regime['reason'],
                'score': score,
                'details': regime,
                'size_multiplier': regime['size_multiplier'],
                'agent_error': False
            }
        
        # APPROVE in bull market
        return {
            'vote': 'APPROVE',
            'reason': regime['reason'],
            'score': 80,
            'details': regime,
            'size_multiplier': regime['size_multiplier'],
            'agent_error': False
        }
    
    def display_current_regime(self):
        """Display current market regime"""
        
        print(f"\n{'='*80}")
        print(f"🌍 CURRENT MARKET REGIME")
        print(f"{'='*80}\n")
        
        regime = self.get_combined_regime()
        
        # SPY Status
        spy = regime['spy_details']
        print(f"📊 SPY 200-Day MA Check:")
        print(f"   Current: ${spy.get('spy_price', 'N/A')}")
        print(f"   200-MA: ${spy.get('ma_200', 'N/A')}")
        print(f"   Distance: {spy.get('distance_pct', 0):.1f}%")
        print(f"   Regime: {regime['spy_regime']}")
        
        # VIX Status
        vix = regime['vix_details']
        print(f"\n⚡ VIX Fear Gauge:")
        print(f"   Level: {vix.get('vix_level', 'N/A')}")
        print(f"   Regime: {regime['vix_regime']}")
        
        # Overall
        print(f"\n🎯 OVERALL REGIME: {regime['overall_regime']}")
        print(f"📍 Action: {regime['action']}")
        print(f"📏 Position Size: {regime['size_multiplier']*100:.0f}%")
        print(f"💬 {regime['reason']}")
        
        print(f"\n{'='*80}\n")


def test_regime_filter():
    """Test the regime filter"""
    
    print("🧪 Testing Regime Filter (Gemini Spec)...\n")
    
    regime_filter = RegimeFilter()
    
    # Display current regime
    regime_filter.display_current_regime()
    
    # Test vote
    print("Testing Veto Council vote on AAPL:")
    vote = regime_filter.vote_on_trade('AAPL')
    
    print(f"\n🗳️  VOTE: {vote['vote']}")
    print(f"Reason: {vote['reason']}")
    print(f"Score: {vote['score']}")
    print(f"Size Multiplier: {vote['size_multiplier']*100:.0f}%")
    
    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    test_regime_filter()
