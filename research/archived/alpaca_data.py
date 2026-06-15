from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from config import TradingConfig
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple, Any
import time
import logging

logger = logging.getLogger(__name__)

class AlpacaDataFeed:
    """Get FREE real-time market data from Alpaca"""

    # ── Connection banner ──────────────────────────────────────────────────────
    _connection_banner_printed = False

    # ── CLASS-LEVEL shared caches (all instances share one pool) ──────────────
    # Key: (ticker, days_back, adj, feed)  →  (timestamp, bars_list)
    _bars_cache: Dict[Tuple, Tuple[float, list]] = {}
    _bars_cache_ttl: float = 45.0       # 45 s during active use; good for full scan cycles

    # Key: ticker  →  (timestamp, quote_dict)
    _quote_cache: Dict[str, Tuple[float, dict]] = {}
    _quote_cache_ttl: float = 4.0       # 4 s — quotes change fast but stops per-render hammering

    # Key: (ticker, timeframe, limit)  →  (timestamp, bars_list)
    _intraday_cache: Dict[Tuple, Tuple[float, list]] = {}
    _intraday_cache_ttl: float = 12.0   # 12 s — intraday bars stable within a scan

    # ── Rate-limit tracking ────────────────────────────────────────────────────
    _rate_limit_count: int = 0           # total 429s seen this process
    _last_rate_limit_ts: float = 0.0     # monotonic time of most recent 429

    # ── Visibility counters ────────────────────────────────────────────────────
    _api_hits: int = 0                   # actual REST calls made
    _cache_hits: int = 0                 # requests served from cache

    def __init__(self):
        self.config = TradingConfig()
        self._network_error_logged = False

        # Alpaca gives FREE data with your trading account!
        self.data_client = StockHistoricalDataClient(
            self.config.ALPACA_API_KEY,
            self.config.ALPACA_SECRET_KEY
        )

        if not AlpacaDataFeed._connection_banner_printed:
            print("✅ Alpaca Data Feed connected (SIP feed)")
            AlpacaDataFeed._connection_banner_printed = True

    # ── Cache helpers (operate on class-level dicts) ──────────────────────────
    @classmethod
    def _cache_get(cls, store: dict, key, ttl: float):
        rec = store.get(key)
        if not rec:
            return None
        ts, val = rec
        if (time.time() - ts) <= ttl:
            cls._cache_hits += 1
            return val
        try:
            del store[key]
        except KeyError:
            pass
        return None

    @classmethod
    def _cache_set(cls, store: dict, key, val):
        store[key] = (time.time(), val)

    # ── Feed health / visibility ──────────────────────────────────────────────
    @classmethod
    def get_feed_stats(cls) -> dict:
        """Return a snapshot of request counts and cache performance."""
        total = cls._api_hits + cls._cache_hits
        hit_rate = (cls._cache_hits / total * 100) if total else 0.0
        return {
            "api_hits": cls._api_hits,
            "cache_hits": cls._cache_hits,
            "cache_hit_rate_pct": round(hit_rate, 1),
            "rate_limit_count": cls._rate_limit_count,
            "last_rate_limit_ts": cls._last_rate_limit_ts,
            "status": (
                "RATE-LIMITED" if (time.time() - cls._last_rate_limit_ts) < 60
                else "OK"
            ),
        }

    @classmethod
    def reset_stats(cls):
        cls._api_hits = 0
        cls._cache_hits = 0

    @classmethod
    def invalidate_daily_bars_cache(cls, ticker: str, days_back: int | None = None) -> None:
        symbol = str(ticker or "").upper().strip()
        if not symbol:
            return
        targets = []
        for key in list(cls._bars_cache.keys()):
            try:
                key_symbol, key_days, _adj, _feed = key
            except Exception:
                continue
            if str(key_symbol).upper().strip() != symbol:
                continue
            if days_back is not None and int(key_days) != int(days_back):
                continue
            targets.append(key)
        for key in targets:
            cls._bars_cache.pop(key, None)
    
    def get_live_quote_if_available(self, ticker: str) -> dict | None:
        """
        Return a live websocket quote if LiveFeed is running and fresh.
        Falls back to None so caller proceeds to REST. Keeps REST path untouched.

        Dict shape matches get_real_time_quote() for drop-in compatibility:
          ask_price, bid_price, mid_price, last_price, ask_size, bid_size,
          timestamp, _stale, _source ("websocket" | "rest")
        """
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return None
        try:
            from live_feed import LiveFeed
            q = LiveFeed.get_live_quote(ticker)
            if not q:
                return None
            bid = q.get("bid") or 0.0
            ask = q.get("ask") or 0.0
            mid = q.get("mid") or ((bid + ask) / 2 if (bid and ask) else (bid or ask or 0.0))
            result = {
                "ticker": ticker,
                "ask_price": ask,
                "bid_price": bid,
                "mid_price": mid,
                "last_price": q.get("last") or mid,
                "ask_size": q.get("ask_size"),
                "bid_size": q.get("bid_size"),
                "timestamp": q.get("timestamp"),
                "_stale": False,
                "_source": "websocket",
                "_age_seconds": q.get("age_seconds"),
            }
            # Also populate the REST cache so other callers benefit
            self._cache_set(self._quote_cache, ticker, result)
            return result
        except Exception:
            return None

    def get_real_time_quote(self, ticker: str):
        """Get current quote from SIP feed. Websocket-first, then cached REST."""
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return None

        # Websocket-first: if LiveFeed has a fresh quote, return it without a REST call
        ws_quote = self.get_live_quote_if_available(ticker)
        if ws_quote is not None:
            AlpacaDataFeed._cache_hits += 1
            return ws_quote

        # REST cache check
        cached = self._cache_get(self._quote_cache, ticker, self._quote_cache_ttl)
        if cached is not None:
            return cached

        # Attempt with 429-aware backoff (max 3 tries)
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                AlpacaDataFeed._api_hits += 1
                request = StockLatestQuoteRequest(symbol_or_symbols=ticker, feed=DataFeed.SIP)
                resp = self.data_client.get_stock_latest_quote(request)

                q = resp[ticker]
                ask = float(q.ask_price) if q.ask_price is not None else 0.0
                bid = float(q.bid_price) if q.bid_price is not None else 0.0
                mid = (ask + bid) / 2 if (ask and bid) else (ask or bid)

                result = {
                    "ticker": ticker,
                    "ask_price": ask,
                    "bid_price": bid,
                    "mid_price": mid,
                    "last_price": mid,
                    "ask_size": q.ask_size,
                    "bid_size": q.bid_size,
                    "timestamp": q.timestamp,
                    "_stale": False,
                }
                self._cache_set(self._quote_cache, ticker, result)
                return result

            except Exception as e:
                msg = f"{type(e).__name__}: {e}".lower()
                if "too many requests" in msg or "429" in msg or "rate limit" in msg:
                    AlpacaDataFeed._rate_limit_count += 1
                    AlpacaDataFeed._last_rate_limit_ts = time.time()
                    backoff = min(1.5 ** attempt, 8.0)
                    logger.warning("get_real_time_quote(%s) rate-limited (attempt %s/%s) — backoff %.1fs",
                                   ticker, attempt, max_attempts, backoff)
                    if attempt < max_attempts:
                        time.sleep(backoff)
                        continue
                # Non-429 or final attempt
                logger.debug("get_real_time_quote(%s) error: %s", ticker, e)
                # Return stale cached data if any exists (even expired)
                stale = self._quote_cache.get(ticker)
                if stale:
                    result = dict(stale[1])
                    result["_stale"] = True
                    return result
                return None
    
    def get_daily_bars(self, ticker, days_back=20, adjustment="all"):
        """Get recent daily price bars (shared cache + retry + degrade). Returns [] on failure."""
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return []

        # -------- class-level shared cache --------
        def cache_get(key):
            return self._cache_get(self._bars_cache, key, self._bars_cache_ttl)

        def cache_set(key, val):
            self._cache_set(self._bars_cache, key, val)

        # -------- helpers --------
        def is_transient(msg: str) -> bool:
            m = (msg or "").lower()
            # Alpaca transient patterns
            return any(x in m for x in [
                "internal server error",  # 500
                "status code 500",
                "status code: 500",
                "too many requests",      # 429
                "status code 429",
                "timeout",
                "timed out",
                "temporarily unavailable",
                "service unavailable",
                "connection aborted",
                "connection reset",
                "read timed out",
            ])

        def is_dns(msg: str) -> bool:
            return ("nameresolutionerror" in msg.lower()) or ("failed to resolve" in msg.lower())

        def fetch(feed, adj) -> list:
            AlpacaDataFeed._api_hits += 1
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days_back)

            request = StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                feed=feed,
                adjustment=adj
            )

            bars_resp = self.data_client.get_stock_bars(request)
            bar_list = getattr(bars_resp, "data", {}).get(ticker, []) if bars_resp else []

            bars = []
            for bar in bar_list:
                bars.append({
                    "timestamp": bar.timestamp,
                    "open": float(bar.open),
                    "high": float(bar.high),
                    "low": float(bar.low),
                    "close": float(bar.close),
                    "volume": int(bar.volume),
                })
            return bars

        # -------- attempt plan (degrade strategy) --------
        # 1) SIP + requested adjustment
        # 2) SIP + raw (if adjustment="all" causes issues)
        # 3) IEX + requested adjustment (optional degrade; helps if SIP is flaky)
        # 4) IEX + raw
        plans = [
            (DataFeed.SIP, adjustment),
        ]
        if str(adjustment).lower() != "raw":
            plans.append((DataFeed.SIP, "raw"))
        plans.append((DataFeed.IEX, adjustment))
        if str(adjustment).lower() != "raw":
            plans.append((DataFeed.IEX, "raw"))

        # Retry tuning
        max_attempts_per_plan = 3
        base_backoff = 0.6  # seconds

        last_err = None

        for feed, adj in plans:
            cache_key = (ticker, int(days_back), str(adj), str(feed))
            cached = cache_get(cache_key)
            if cached is not None:
                return cached

            for attempt in range(1, max_attempts_per_plan + 1):
                try:
                    bars = fetch(feed=feed, adj=adj)

                    # cache even empty results (briefly) to stop repeated hammering
                    cache_set(cache_key, bars)

                    if not bars:
                        logger.debug(
                            "get_daily_bars(%s): 0 bars (days_back=%s feed=%s adj=%s)",
                            ticker, days_back, feed, adj
                        )
                    return bars

                except Exception as e:
                    msg = f"{type(e).__name__}: {e}"
                    last_err = msg

                    # Rate-limit tracking
                    if "too many requests" in msg.lower() or "429" in msg or "rate limit" in msg.lower():
                        AlpacaDataFeed._rate_limit_count += 1
                        AlpacaDataFeed._last_rate_limit_ts = time.time()

                    # DNS failures: suppress spam like you already do
                    if is_dns(msg):
                        if not self._network_error_logged:
                            logger.error("Market data network resolution failed. Further per-ticker errors suppressed.")
                            self._network_error_logged = True
                        logger.debug("get_daily_bars(%s) network error: %s", ticker, msg)
                        return []

                    # Transient (including 429): retry with backoff
                    if is_transient(msg) and attempt < max_attempts_per_plan:
                        sleep_s = base_backoff * (2 ** (attempt - 1))
                        # small jitter to reduce thundering herd if many tickers fail
                        sleep_s = min(sleep_s, 5.0) + (0.05 * attempt)
                        logger.debug(
                            "get_daily_bars(%s) transient error (feed=%s adj=%s attempt=%s/%s): %s | backoff=%.2fs",
                            ticker, feed, adj, attempt, max_attempts_per_plan, msg, sleep_s
                        )
                        time.sleep(sleep_s)
                        continue

                    # Non-transient OR last retry: log once and break to degrade plan
                    logger.warning(
                        "Error getting bars for %s (feed=%s adj=%s attempt=%s/%s): %s",
                        ticker, feed, adj, attempt, max_attempts_per_plan, msg
                    )
                    break  # move to next plan (degrade)

        # All plans failed
        logger.warning("get_daily_bars(%s) failed after retries/degrade. last_error=%s", ticker, last_err)
        return []

    def get_daily_bars_batch(self, tickers: List[str], days_back: int = 20, adjustment: str = "all",
                             chunk_size: int = 200) -> Dict[str, List[Dict]]:
        """
        Fetch daily bars for many symbols in batches.

        Returns a dict keyed by ticker. Missing/failed symbols map to [].
        Uses the same per-symbol cache keys as get_daily_bars() so both paths share results.
        """
        normalized = []
        seen = set()
        for ticker in tickers or []:
            symbol = str(ticker or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            normalized.append(symbol)
        if not normalized:
            return {}

        chunk_size = max(1, int(chunk_size or 1))
        results: Dict[str, List[Dict]] = {}

        def cache_get(key):
            return self._cache_get(self._bars_cache, key, self._bars_cache_ttl)

        def cache_set(key, val):
            self._cache_set(self._bars_cache, key, val)

        def is_transient(msg: str) -> bool:
            m = (msg or "").lower()
            return any(x in m for x in [
                "internal server error",
                "status code 500",
                "status code: 500",
                "too many requests",
                "status code 429",
                "timeout",
                "timed out",
                "temporarily unavailable",
                "service unavailable",
                "connection aborted",
                "connection reset",
                "read timed out",
            ])

        def is_dns(msg: str) -> bool:
            return ("nameresolutionerror" in msg.lower()) or ("failed to resolve" in msg.lower())

        def fetch_chunk(symbols: List[str], feed, adj) -> Dict[str, List[Dict]]:
            AlpacaDataFeed._api_hits += 1
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days_back)
            request = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                feed=feed,
                adjustment=adj,
            )
            bars_resp = self.data_client.get_stock_bars(request)
            raw_data = getattr(bars_resp, "data", {}) or {}

            parsed: Dict[str, List[Dict]] = {}
            for symbol in symbols:
                bar_list = raw_data.get(symbol, [])
                bars = []
                for bar in bar_list:
                    bars.append({
                        "timestamp": bar.timestamp,
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": int(bar.volume),
                    })
                parsed[symbol] = bars
            return parsed

        plans = [(DataFeed.SIP, adjustment)]
        if str(adjustment).lower() != "raw":
            plans.append((DataFeed.SIP, "raw"))
        plans.append((DataFeed.IEX, adjustment))
        if str(adjustment).lower() != "raw":
            plans.append((DataFeed.IEX, "raw"))

        unresolved = set(normalized)
        last_err = None

        for feed, adj in plans:
            current_keys = {ticker: (ticker, int(days_back), str(adj), str(feed)) for ticker in unresolved}

            cached_symbols = []
            for ticker, key in current_keys.items():
                cached = cache_get(key)
                if cached is not None:
                    results[ticker] = cached
                    cached_symbols.append(ticker)
            unresolved.difference_update(cached_symbols)
            if not unresolved:
                break

            symbols = sorted(unresolved)
            for idx in range(0, len(symbols), chunk_size):
                chunk = symbols[idx: idx + chunk_size]
                max_attempts = 3
                base_backoff = 0.6

                for attempt in range(1, max_attempts + 1):
                    try:
                        payload = fetch_chunk(chunk, feed=feed, adj=adj)
                        for ticker, bars in payload.items():
                            cache_key = (ticker, int(days_back), str(adj), str(feed))
                            cache_set(cache_key, bars)
                            if bars and ticker not in results:
                                results[ticker] = bars
                        break
                    except Exception as exc:
                        msg = f"{type(exc).__name__}: {exc}"
                        last_err = msg

                        if "too many requests" in msg.lower() or "429" in msg or "rate limit" in msg.lower():
                            AlpacaDataFeed._rate_limit_count += 1
                            AlpacaDataFeed._last_rate_limit_ts = time.time()

                        if is_dns(msg):
                            if not self._network_error_logged:
                                logger.error("Market data network resolution failed. Further batch errors suppressed.")
                                self._network_error_logged = True
                            logger.debug("get_daily_bars_batch chunk network error: %s", msg)
                            break

                        if is_transient(msg) and attempt < max_attempts:
                            sleep_s = min(base_backoff * (2 ** (attempt - 1)), 5.0) + (0.05 * attempt)
                            logger.debug(
                                "get_daily_bars_batch transient error (feed=%s adj=%s attempt=%s/%s chunk=%s): %s | backoff=%.2fs",
                                feed, adj, attempt, max_attempts, len(chunk), msg, sleep_s
                            )
                            time.sleep(sleep_s)
                            continue

                        logger.warning(
                            "Error getting batch bars (feed=%s adj=%s attempt=%s/%s chunk=%s): %s",
                            feed, adj, attempt, max_attempts, len(chunk), msg
                        )
                        break

            unresolved = {ticker for ticker in unresolved if ticker not in results}
            if not unresolved:
                break

        for ticker in normalized:
            results.setdefault(ticker, [])

        if unresolved:
            logger.debug(
                "get_daily_bars_batch unresolved=%s/%s last_error=%s",
                len(unresolved), len(normalized), last_err
            )

        return results

    def get_bars(self, ticker: str, timeframe: str = '1Day', limit: int = 100) -> Optional[List[Dict]]:
        """
        Get bars for any timeframe (intraday or daily). Cached via _intraday_cache.

        Args:
            ticker: Stock symbol
            timeframe: '1Week', '1Day', '4Hour', '1Hour', '15Min', '5Min', '1Min'
            limit: Number of bars to fetch

        Returns:
            List of bar dictionaries with OHLCV data
        """
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return None

        cache_key = (ticker, timeframe, limit)
        cached = self._cache_get(self._intraday_cache, cache_key, self._intraday_cache_ttl)
        if cached is not None:
            return cached

        try:
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            from alpaca.data.requests import StockBarsRequest

            # Map string timeframe to Alpaca TimeFrame object
            timeframe_map = {
                '1Week': TimeFrame(1, TimeFrameUnit.Week),
                '1Day': TimeFrame(1, TimeFrameUnit.Day),
                '4Hour': TimeFrame(4, TimeFrameUnit.Hour),
                '1Hour': TimeFrame(1, TimeFrameUnit.Hour),
                '15Min': TimeFrame(15, TimeFrameUnit.Minute),
                '5Min': TimeFrame(5, TimeFrameUnit.Minute),
                '1Min': TimeFrame(1, TimeFrameUnit.Minute)
            }

            if timeframe not in timeframe_map:
                print(f"⚠️  Invalid timeframe: {timeframe}")
                return None

            alpaca_timeframe = timeframe_map[timeframe]

            # Calculate start date based on timeframe
            if timeframe == '1Week':
                start_date = datetime.now(timezone.utc) - timedelta(weeks=limit + 10)
            elif timeframe == '1Day':
                start_date = datetime.now(timezone.utc) - timedelta(days=limit + 30)
            elif timeframe in ['4Hour', '1Hour']:
                start_date = datetime.now(timezone.utc) - timedelta(days=30)
            else:  # Minutes
                start_date = datetime.now(timezone.utc) - timedelta(days=10)

            # Create request
            request = StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=alpaca_timeframe,
                start=start_date,
                limit=limit,
                feed=DataFeed.SIP,
                adjustment="raw",
            )

            # Fetch bars
            AlpacaDataFeed._api_hits += 1
            bars_data = self.data_client.get_stock_bars(request)

            if ticker not in bars_data.data:
                return None

            # Convert to simple dict format
            bars = []
            for bar in bars_data.data[ticker]:
                bars.append({
                    'timestamp': bar.timestamp,
                    'open': float(bar.open),
                    'high': float(bar.high),
                    'low': float(bar.low),
                    'close': float(bar.close),
                    'volume': int(bar.volume)
                })

            self._cache_set(self._intraday_cache, cache_key, bars)
            return bars

        except Exception as e:
            print(f"⚠️  Error fetching {timeframe} bars for {ticker}: {e}")
            return None
    
    def calculate_volume_score(self, ticker):
        """Analyze volume for whale activity"""
        bars = self.get_daily_bars(ticker, days_back=20)
        
        if not bars or len(bars) < 5:
            return 0
        
        # Get volumes
        volumes = [bar['volume'] for bar in bars]
        current_volume = volumes[-1]
        avg_volume = sum(volumes[:-1]) / len(volumes[:-1])
        
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
        
        # Scoring
        if volume_ratio > 3.0:
            return 40  # Massive unusual volume
        elif volume_ratio > 2.0:
            return 30  # High volume
        elif volume_ratio > 1.5:
            return 20  # Elevated volume
        else:
            return 10  # Normal
    
    def calculate_price_momentum_score(self, ticker):
        """Calculate price momentum score"""
        bars = self.get_daily_bars(ticker, days_back=10)
        
        if not bars or len(bars) < 2:
            return 0
        
        latest = bars[-1]
        previous = bars[-2]
        
        price_change_pct = ((latest['close'] - previous['close']) / previous['close']) * 100
        
        # Strong moves indicate institutional activity
        if abs(price_change_pct) > 5:
            return 30
        elif abs(price_change_pct) > 3:
            return 20
        elif abs(price_change_pct) > 1:
            return 10
        else:
            return 5
    
    def calculate_whale_score(self, ticker):
        """
        Comprehensive whale score (0-100)
        Combines volume, price action, and momentum
        """
        score = 0
        
        # Volume analysis (40 points)
        volume_score = self.calculate_volume_score(ticker)
        score += volume_score
        
        # Price momentum (30 points)
        momentum_score = self.calculate_price_momentum_score(ticker)
        score += momentum_score
        
        # Active trading (30 points)
        quote = self.get_real_time_quote(ticker)
        if quote:
            # Tight spread = institutional interest
            spread = quote['ask_price'] - quote['bid_price']
            spread_pct = (spread / quote['mid_price']) * 100 if quote['mid_price'] > 0 else 0
            
            if spread_pct < 0.01:  # Very tight spread
                score += 30
            elif spread_pct < 0.05:
                score += 20
            else:
                score += 10
        
        return min(100, score)
    
    def display_whale_analysis(self, ticker):
        """Display complete whale analysis"""
        print(f"\n🐋 WHALE ANALYSIS: {ticker}")
        print("=" * 80)
        
        # Calculate whale score
        whale_score = self.calculate_whale_score(ticker)
        
        if whale_score >= 70:
            score_emoji = "🟢 STRONG SIGNAL"
            signal = "BUY OPPORTUNITY"
        elif whale_score >= 50:
            score_emoji = "🟡 MODERATE SIGNAL"
            signal = "WATCH CLOSELY"
        elif whale_score >= 30:
            score_emoji = "🟠 WEAK SIGNAL"
            signal = "NEUTRAL"
        else:
            score_emoji = "🔴 NO SIGNAL"
            signal = "AVOID"
        
        print(f"\n{score_emoji}")
        print(f"Whale Score: {whale_score}/100")
        print(f"Signal: {signal}")
        
        # Current quote
        quote = self.get_real_time_quote(ticker)
        if quote:
            print(f"\n💰 CURRENT PRICE:")
            print(f"   Mid Price: ${quote['mid_price']:.2f}")
            print(f"   Bid: ${quote['bid_price']:.2f} ({quote['bid_size']} shares)")
            print(f"   Ask: ${quote['ask_price']:.2f} ({quote['ask_size']} shares)")
            print(f"   Updated: {quote['timestamp']}")
        
        # Price action
        print(f"\n📊 RECENT PRICE ACTION:")
        print("-" * 80)
        
        bars = self.get_daily_bars(ticker, days_back=5)
        if bars:
            for bar in bars[-5:]:
                change = ((bar['close'] - bar['open']) / bar['open']) * 100
                emoji = "📈" if change > 0 else "📉"
                
                date_str = bar['timestamp'].strftime('%Y-%m-%d')
                print(f"{emoji} {date_str}: ${bar['close']:.2f} ({change:+.2f}%) "
                      f"Vol: {bar['volume']:,.0f}")
            
            # Volume analysis
            volumes = [bar['volume'] for bar in bars]
            if len(volumes) > 1:
                avg_vol = sum(volumes[:-1]) / len(volumes[:-1])
                current_vol = volumes[-1]
                vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1
                
                print(f"\n📊 Volume Analysis:")
                print(f"   Current: {current_vol:,.0f}")
                print(f"   Average: {avg_vol:,.0f}")
                print(f"   Ratio: {vol_ratio:.2f}x")
                
                if vol_ratio > 2.0:
                    print("   ⚠️  UNUSUAL VOLUME DETECTED! 🚨")
                elif vol_ratio > 1.5:
                    print("   ⚡ Elevated volume")
        
        print("=" * 80)


def test_alpaca_data():
    """Test Alpaca data feed"""
    print("🚀 Testing Alpaca FREE Data Feed...\n")
    
    feed = AlpacaDataFeed()
    
    # Test with watchlist
    test_tickers = ["AAPL", "NVDA", "TSLA", "MSFT"]
    
    print(f"Analyzing: {', '.join(test_tickers)}\n")
    
    for ticker in test_tickers:
        try:
            feed.display_whale_analysis(ticker)
            print("\n")
        except Exception as e:
            print(f"❌ Error analyzing {ticker}: {e}\n")


if __name__ == "__main__":
    test_alpaca_data()
