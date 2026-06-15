"""
Terminal Helpers - Shared utilities for all dashboards
Provides data fetching, formatting, and intelligence gathering
"""

import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import requests
from collections import deque
import time
import random

# Module-level singleton — avoids re-init overhead and keeps API key loading once
_alpaca_feed = None

def _get_alpaca_feed():
    global _alpaca_feed
    if _alpaca_feed is None:
        from alpaca_data import AlpacaDataFeed
        _alpaca_feed = AlpacaDataFeed()
    return _alpaca_feed

class MarketDataHelper:
    """Helper for fetching real-time market data"""

    _session = requests.Session()
    _headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
    }
    _chart_cache = {}
    _cache_ttl_seconds = 15

    @classmethod
    def _cache_get(cls, key):
        rec = cls._chart_cache.get(key)
        if not rec:
            return None
        ts, payload = rec
        if (time.time() - ts) <= cls._cache_ttl_seconds:
            return payload
        try:
            del cls._chart_cache[key]
        except Exception:
            pass
        return None

    @classmethod
    def _cache_set(cls, key, payload):
        try:
            cls._chart_cache[key] = (time.time(), payload)
        except Exception:
            pass

    @classmethod
    def _fetch_chart_result(cls, symbol: str, interval: str = "1m", range_: str = "1d") -> Optional[Dict]:
        symbol = (symbol or "").upper().strip()
        if not symbol:
            return None

        cache_key = (symbol, interval, range_)
        cached = cls._cache_get(cache_key)
        if cached is not None:
            return cached

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"interval": interval, "range": range_}
        max_attempts = 4

        for attempt in range(1, max_attempts + 1):
            try:
                response = cls._session.get(
                    url,
                    params=params,
                    headers=cls._headers,
                    timeout=6,
                )

                if response.status_code == 429:
                    if attempt < max_attempts:
                        delay = min(3.5, 0.35 * (2 ** (attempt - 1))) + random.uniform(0.05, 0.25)
                        time.sleep(delay)
                        continue
                    return None

                if response.status_code >= 500:
                    if attempt < max_attempts:
                        delay = min(3.5, 0.35 * (2 ** (attempt - 1))) + random.uniform(0.05, 0.25)
                        time.sleep(delay)
                        continue
                    return None

                if response.status_code != 200:
                    return None

                data = response.json()
                chart = data.get("chart") if isinstance(data, dict) else None
                if not isinstance(chart, dict):
                    return None

                if chart.get("error"):
                    return None

                result = chart.get("result")
                if not isinstance(result, list) or not result:
                    return None

                payload = result[0]
                cls._cache_set(cache_key, payload)
                return payload
            except Exception:
                if attempt < max_attempts:
                    delay = min(3.5, 0.35 * (2 ** (attempt - 1))) + random.uniform(0.05, 0.25)
                    time.sleep(delay)
                    continue
                return None

        return None

    @staticmethod
    def _alpaca_quote_fallback(symbol: str) -> Optional[Dict]:
        try:
            quote = _get_alpaca_feed().get_real_time_quote(symbol)
            if not quote:
                return None

            return {
                "symbol": symbol,
                "current_price": quote.get("last_price") or quote.get("mid_price") or 0,
                "bid": quote.get("bid_price", 0),
                "ask": quote.get("ask_price", 0),
                "bid_size": quote.get("bid_size", 0),
                "ask_size": quote.get("ask_size", 0),
                "volume": 0,
                "prev_close": 0,
                "source": "alpaca_fallback",
            }
        except Exception:
            return None
    
    @staticmethod
    def get_stock_quote(symbol: str) -> Optional[Dict]:
        """Get real-time quote data"""
        try:
            result = MarketDataHelper._fetch_chart_result(symbol, interval="1m", range_="1d")
            if result:
                meta = result.get("meta", {})
                return {
                    'symbol': symbol,
                    'current_price': meta.get('regularMarketPrice', 0),
                    'bid': meta.get('bid', 0),
                    'ask': meta.get('ask', 0),
                    'bid_size': meta.get('bidSize', 0),
                    'ask_size': meta.get('askSize', 0),
                    'volume': meta.get('regularMarketVolume', 0),
                    'prev_close': meta.get('previousClose', 0),
                    'source': 'yahoo'
                }
            return MarketDataHelper._alpaca_quote_fallback(symbol)
        except Exception:
            return MarketDataHelper._alpaca_quote_fallback(symbol)
    
    @staticmethod
    def get_vwap(symbol: str) -> Optional[float]:
        """Calculate VWAP for today"""
        try:
            result = MarketDataHelper._fetch_chart_result(symbol, interval="1m", range_="1d")
            if result:
                indicators = ((result.get("indicators") or {}).get("quote") or [{}])[0]
                volumes = indicators.get("volume") or []
                closes = indicators.get("close") or []

                total_pv = 0.0
                total_v = 0.0
                for p, v in zip(closes, volumes):
                    if p is None or v is None:
                        continue
                    if v <= 0:
                        continue
                    total_pv += float(p) * float(v)
                    total_v += float(v)

                if total_v > 0:
                    return total_pv / total_v

            # Fallback: build VWAP from Alpaca 1-minute bars
            bars = _get_alpaca_feed().get_bars(symbol, timeframe="1Min", limit=390) or []
            if not bars:
                return None

            total_pv = 0.0
            total_v = 0.0
            for bar in bars:
                close = bar.get("close")
                volume = bar.get("volume")
                if close is None or volume is None:
                    continue
                if volume <= 0:
                    continue
                total_pv += float(close) * float(volume)
                total_v += float(volume)

            if total_v > 0:
                return total_pv / total_v
        except Exception:
            pass
        return None


class NewsHelper:
    """Helper for fetching news and sentiment"""
    
    @staticmethod
    def get_latest_news(symbol: str, limit: int = 3) -> List[Dict]:
        """Get latest news for symbol"""
        try:
            api_key = os.getenv('FINNHUB_API_KEY', '')
            if not api_key or api_key == 'demo':
                return []

            url = f"https://finnhub.io/api/v1/company-news"
            params = {
                'symbol': symbol,
                'from': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
                'to': datetime.now().strftime('%Y-%m-%d'),
                'token': api_key
            }

            response = requests.get(url, params=params, timeout=3)
            news_items = response.json()
            
            if isinstance(news_items, list):
                return news_items[:limit]
        except Exception:
            pass
        return []
    
    @staticmethod
    def get_sentiment_score(symbol: str) -> Optional[float]:
        """Get sentiment score (0-1, higher = more bullish)"""
        try:
            # Simple sentiment from news headlines
            news = NewsHelper.get_latest_news(symbol, limit=10)
            if not news:
                return 0.5
            
            # Basic sentiment scoring
            bullish_words = ['upgrade', 'beat', 'positive', 'growth', 'surge', 'rally', 'bullish']
            bearish_words = ['downgrade', 'miss', 'negative', 'decline', 'fall', 'bearish', 'cut']
            
            bullish_count = 0
            bearish_count = 0
            
            for item in news:
                headline = item.get('headline', '').lower()
                bullish_count += sum(1 for word in bullish_words if word in headline)
                bearish_count += sum(1 for word in bearish_words if word in headline)
            
            total = bullish_count + bearish_count
            if total > 0:
                return bullish_count / total
            
            return 0.5  # Neutral
        except Exception:
            return 0.5


class OptionsHelper:
    """Helper for options flow data"""
    
    @staticmethod
    def get_put_call_ratio(symbol: str) -> Optional[float]:
        """Get put/call ratio"""
        try:
            # Using Yahoo Finance options data
            url = f"https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
            response = requests.get(url, timeout=5)
            data = response.json()
            
            if 'optionChain' in data:
                result = data['optionChain']['result'][0]
                options = result.get('options', [])
                
                if options:
                    calls = options[0].get('calls', [])
                    puts = options[0].get('puts', [])
                    
                    call_volume = sum(c.get('volume', 0) for c in calls)
                    put_volume = sum(p.get('volume', 0) for p in puts)
                    
                    if call_volume > 0:
                        return put_volume / call_volume
        except:
            pass
        return None


class RiskCalculator:
    """Helper for risk calculations"""
    
    @staticmethod
    def calculate_sharpe_ratio(returns: List[float], risk_free_rate: float = 0.04) -> Optional[float]:
        """Calculate Sharpe ratio"""
        if not returns or len(returns) < 2:
            return None
        
        import statistics
        
        avg_return = statistics.mean(returns)
        std_dev = statistics.stdev(returns)
        
        if std_dev == 0:
            return None
        
        # Annualized
        sharpe = (avg_return - risk_free_rate / 252) / std_dev * (252 ** 0.5)
        return sharpe
    
    @staticmethod
    def calculate_max_drawdown(equity_curve: List[float]) -> Optional[float]:
        """Calculate maximum drawdown"""
        if not equity_curve or len(equity_curve) < 2:
            return None
        
        peak = equity_curve[0]
        max_dd = 0
        
        for value in equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd
        
        return max_dd * 100  # Return as percentage


class AlertManager:
    """Manages smart alerts"""
    
    def __init__(self):
        self.alerts = deque(maxlen=10)
        self.active_alerts = []
    
    def add_alert(self, alert_type: str, ticker: str, message: str, priority: str = 'NORMAL'):
        """Add a new alert"""
        alert = {
            'type': alert_type,
            'ticker': ticker,
            'message': message,
            'priority': priority,
            'timestamp': datetime.now(),
            'dismissed': False
        }
        
        self.alerts.append(alert)
        
        if priority in ['CRITICAL', 'HIGH']:
            self.active_alerts.append(alert)
    
    def get_active_alerts(self) -> List[Dict]:
        """Get undismissed alerts"""
        return [a for a in self.active_alerts if not a.get('dismissed')]
    
    def dismiss_alert(self, index: int):
        """Dismiss an alert"""
        if 0 <= index < len(self.active_alerts):
            self.active_alerts[index]['dismissed'] = True


class TradeJournal:
    """Track and analyze trades"""
    
    def __init__(self):
        self.trades = []
        self.closed_trades = []
    
    def add_trade(self, ticker: str, entry_price: float, shares: int, 
                  setup_type: str, confluence: str):
        """Add a new trade"""
        trade = {
            'ticker': ticker,
            'entry_price': entry_price,
            'entry_time': datetime.now(),
            'shares': shares,
            'setup_type': setup_type,
            'confluence': confluence,
            'exit_price': None,
            'exit_time': None,
            'pnl': None,
            'notes': []
        }
        self.trades.append(trade)
    
    def close_trade(self, ticker: str, exit_price: float, notes: str = ""):
        """Close a trade"""
        for trade in self.trades:
            if trade['ticker'] == ticker and trade['exit_price'] is None:
                trade['exit_price'] = exit_price
                trade['exit_time'] = datetime.now()
                trade['pnl'] = (exit_price - trade['entry_price']) * trade['shares']
                if notes:
                    trade['notes'].append(notes)
                
                self.closed_trades.append(trade)
                self.trades.remove(trade)
                break
    
    def get_statistics(self) -> Dict:
        """Calculate trade statistics"""
        if not self.closed_trades:
            return {}
        
        wins = [t for t in self.closed_trades if t['pnl'] > 0]
        losses = [t for t in self.closed_trades if t['pnl'] <= 0]
        
        return {
            'total_trades': len(self.closed_trades),
            'win_rate': len(wins) / len(self.closed_trades) * 100,
            'avg_win': sum(t['pnl'] for t in wins) / len(wins) if wins else 0,
            'avg_loss': sum(t['pnl'] for t in losses) / len(losses) if losses else 0,
            'total_pnl': sum(t['pnl'] for t in self.closed_trades),
            'best_trade': max(self.closed_trades, key=lambda x: x['pnl']),
            'worst_trade': min(self.closed_trades, key=lambda x: x['pnl'])
        }
