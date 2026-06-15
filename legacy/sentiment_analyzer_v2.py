import requests
import os
from datetime import datetime, timedelta, date
import statistics
from typing import Optional, Dict, List
import json

class RealSentimentAnalyzer:
    """
    Real Sentiment Analysis using actual APIs
    
    Data Sources:
    1. News API - Headline sentiment
    2. AlphaVantage - Market sentiment scores
    3. Fallback - Price/volume proxy
    """
    
    def __init__(self, news_api_key: str = None, alpha_vantage_key: str = None):
        """Initialize with API keys"""
        
        self.news_api_key = news_api_key or os.getenv("NEWS_API_KEY", "")
        self.alpha_vantage_key = (
            alpha_vantage_key
            or os.getenv("ALPHAVANTAGE_API_KEY", "")
            or os.getenv("ALPHA_VANTAGE_KEY", "")
        )
        self.news_enabled = bool(self.news_api_key)
        self.alpha_vantage_enabled = bool(self.alpha_vantage_key)
        
        # Rate limiting
        self.last_news_call = None
        self.last_av_call = None
        self.av_disabled = False  # turns True if we hit daily limit or repeated failures
        self.av_fail_count = 0

        # Daily cache file (resets each day)
        self.cache_date = date.today().isoformat()
        self.cache_file = f"sentiment_cache_{self.cache_date}.json"
        self._sentiment_cache = self._load_daily_cache()
        self.av_disabled = bool(self._sentiment_cache.get("_av_disabled", False))
        
        print("✅ Real Sentiment Analyzer initialized")
        print(f"📰 News API: {'ENABLED' if self.news_enabled else 'DISABLED (no key)'}")
        if self.av_disabled:
            print("📊 AlphaVantage: DISABLED (daily throttle lockout)")
        else:
            print(f"📊 AlphaVantage: {'ENABLED' if self.alpha_vantage_enabled else 'DISABLED (no key)'}")

    def _load_daily_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_daily_cache(self):
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self._sentiment_cache, f, indent=2)
        except Exception:
            pass

    def _cache_get(self, ticker, key):
        ticker = ticker.upper()
        node = self._sentiment_cache.get(ticker, {})
        if key in node:  # IMPORTANT: distinguish missing vs cached None
            return True, node.get(key)
        return False, None

    def _cache_set(self, ticker, key, value):
        ticker = ticker.upper()
        if ticker not in self._sentiment_cache:
            self._sentiment_cache[ticker] = {}
        self._sentiment_cache[ticker][key] = value
        self._save_daily_cache()

    def _is_av_rate_limited(self, payload_text):
        if not payload_text:
            return False
        t = payload_text.lower()
        return ("standard api rate limit" in t) or ("premium plans" in t) or ("thank you for using alpha vantage" in t)

    def _disable_av_for_today(self, reason: str = ""):
        self.av_disabled = True
        self._sentiment_cache["_av_disabled"] = True
        if reason:
            self._sentiment_cache["_av_disabled_reason"] = reason
        self._save_daily_cache()
    
    def _rate_limit(self, last_call, min_delay_seconds=1):
        """Simple rate limiting"""
        import time
        if last_call:
            elapsed = time.time() - last_call
            if elapsed < min_delay_seconds:
                time.sleep(min_delay_seconds - elapsed)
        return time.time()
    
    def get_news_sentiment(self, ticker: str) -> Optional[Dict]:
        """Get news sentiment from News API - STRICT: only return if valid data"""
        
        if not self.news_enabled:
            return None
        try:
            # Rate limit
            self.last_news_call = self._rate_limit(self.last_news_call, 1)
            
            # Get news from last 7 days
            from_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': ticker,
                'from': from_date,
                'sortBy': 'publishedAt',
                'language': 'en',
                'pageSize': 20,
                'apiKey': self.news_api_key
            }
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code != 200:
                print(f"  ⚠️  News API HTTP {response.status_code}")
                return None
            
            data = response.json()
            
            # Check for API errors
            if data.get('status') == 'error':
                print(f"  ⚠️  News API error: {data.get('message', 'Unknown error')}")
                return None
            
            articles = data.get('articles', [])
            
            if not articles:
                print(f"  📰 News: No articles found")
                return None  # Don't return fake 50 score - return None!
            
            # Sentiment word lists
            sentiment_words_positive = [
                'surge', 'rally', 'soar', 'jump', 'gain', 'rise', 'bull', 
                'upgrade', 'beat', 'strong', 'growth', 'profit', 'win',
                'breakthrough', 'success', 'boom', 'record', 'best', 'high'
            ]
            
            sentiment_words_negative = [
                'plunge', 'crash', 'drop', 'fall', 'loss', 'bear', 'downgrade',
                'miss', 'weak', 'concern', 'warn', 'risk', 'fail', 'worst',
                'decline', 'struggle', 'lawsuit', 'investigation', 'scandal', 'low'
            ]
            
            positive_count = 0
            negative_count = 0
            valid_articles = 0
            
            for article in articles[:15]:  # Check more articles
                # CRITICAL: Check for None values before calling .lower()
                title = article.get('title')
                description = article.get('description')
                
                # Skip if title AND description are both None/empty
                if not title and not description:
                    continue
                
                # Build text safely
                text_parts = []
                if title and isinstance(title, str):
                    text_parts.append(title.lower())
                if description and isinstance(description, str):
                    text_parts.append(description.lower())
                
                if not text_parts:
                    continue
                
                text = ' '.join(text_parts)
                valid_articles += 1
                
                # Count sentiment words
                pos = sum(1 for word in sentiment_words_positive if word in text)
                neg = sum(1 for word in sentiment_words_negative if word in text)
                
                positive_count += pos
                negative_count += neg
            
            # If no valid articles with text, return None
            if valid_articles == 0:
                print(f"  📰 News: {len(articles)} articles but no valid text")
                return None
            
            total = positive_count + negative_count
            
            # If no sentiment words found at all, return None (not fake 50)
            if total == 0:
                print(f"  📰 News: {valid_articles} articles but no sentiment keywords")
                return None
            
            # Calculate actual sentiment
            sentiment_score = (positive_count / total) * 100
            confidence = min(0.8, 0.4 + (total / 20))
            
            return {
                'source': 'News API',
                'articles_count': valid_articles,
                'positive_mentions': positive_count,
                'negative_mentions': negative_count,
                'sentiment_score': round(sentiment_score, 1),
                'confidence': round(confidence, 2),
                'reason': f"{valid_articles} articles: {positive_count}+ / {negative_count}-"
            }
            
        except Exception as e:
            print(f"  ⚠️  News API exception: {e}")
            return None  # Return None, not fake data!

    def _get_news_sentiment(self, ticker: str) -> Optional[Dict]:
        found, cached = self._cache_get(ticker, "news")
        if found:
            return cached  # dict OR None

        news = self.get_news_sentiment(ticker)
        if not news:
            self._cache_set(ticker, "news", None)
            return None
        result = {
            "score": float(news.get("sentiment_score", 50.0) or 50.0),
            "conf": float(news.get("confidence", 0.0) or 0.0),
            "details": news,
        }
        self._cache_set(ticker, "news", result)
        return result
    
    def _get_alphavantage_sentiment(self, ticker: str) -> Optional[Dict]:
        # If already disabled this run/day, skip
        if self.av_disabled or not self.alpha_vantage_enabled:
            return None

        # Cache hit
        found, cached = self._cache_get(ticker, "alphavantage")
        if found:
            # cached can be dict OR None; both are valid cached results
            return cached

        try:
            # Rate limit (max 5 per minute, 25 per day on free tier)
            self.last_av_call = self._rate_limit(self.last_av_call, 12)

            url = "https://www.alphavantage.co/query"
            params = {
                'function': 'NEWS_SENTIMENT',
                'tickers': ticker,
                'apikey': self.alpha_vantage_key,
                'limit': 50
            }

            response = requests.get(url, params=params, timeout=15)
            payload_text = response.text or ""

            if response.status_code != 200:
                print(f"  ⚠️  AlphaVantage HTTP {response.status_code}")
                self._cache_set(ticker, "alphavantage", None)
                return None

            if self._is_av_rate_limited(payload_text):
                self._disable_av_for_today("AlphaVantage rate limited / daily cap hit")
                self._cache_set(ticker, "alphavantage", None)
                return None

            data = response.json()
            payload_json_text = json.dumps(data)

            # Check for rate limit / API messages
            if 'Note' in data:
                print(f"  ⚠️  AlphaVantage: {data['Note']}")
                if self._is_av_rate_limited(str(data['Note'])):
                    self._disable_av_for_today("AlphaVantage rate limited / daily cap hit")
                self._cache_set(ticker, "alphavantage", None)
                return None

            if 'Information' in data:
                print(f"  ⚠️  AlphaVantage: {data['Information']}")
                if self._is_av_rate_limited(str(data['Information'])):
                    self._disable_av_for_today("AlphaVantage rate limited / daily cap hit")
                self._cache_set(ticker, "alphavantage", None)
                return None

            if 'Error Message' in data:
                print(f"  ⚠️  AlphaVantage: {data['Error Message']}")
                self._cache_set(ticker, "alphavantage", None)
                return None

            if self._is_av_rate_limited(payload_json_text):
                self._disable_av_for_today("AlphaVantage rate limited / daily cap hit")
                self._cache_set(ticker, "alphavantage", None)
                return None

            feed = data.get('feed', [])
            if not feed:
                print(f"  📊 AlphaVantage: No feed data")
                self._cache_set(ticker, "alphavantage", None)
                return None

            sentiments = []
            relevance_scores = []

            for article in feed[:20]:
                ticker_sentiments = article.get('ticker_sentiment', [])

                for ts in ticker_sentiments:
                    if ts.get('ticker') == ticker:
                        try:
                            score = float(ts.get('ticker_sentiment_score', 0))
                            relevance = float(ts.get('relevance_score', 0))
                            if relevance > 0.1:
                                sentiments.append(score)
                                relevance_scores.append(relevance)
                        except (ValueError, TypeError):
                            continue

            if not sentiments or not relevance_scores:
                print(f"  📊 AlphaVantage: {len(feed)} articles but no {ticker} sentiment")
                self._cache_set(ticker, "alphavantage", None)
                return None

            total_relevance = sum(relevance_scores)
            if total_relevance == 0:
                self._cache_set(ticker, "alphavantage", None)
                return None

            avg_sentiment = sum(s * r for s, r in zip(sentiments, relevance_scores)) / total_relevance
            sentiment_score = ((avg_sentiment + 1) / 2) * 100
            confidence = min(0.9, 0.5 + (len(sentiments) / 40))

            result = {
                "score": round(float(sentiment_score), 1),
                "conf": round(float(confidence), 2),
                "details": {
                    'source': 'AlphaVantage',
                    'articles_count': len(feed),
                    'sentiment_count': len(sentiments),
                    'avg_sentiment': round(avg_sentiment, 3),
                    'reason': f"{len(sentiments)} scores, avg {avg_sentiment:.2f}"
                }
            }
            self._cache_set(ticker, "alphavantage", result)
            return result

        except Exception as e:
            self.av_fail_count += 1
            self._cache_set(ticker, "alphavantage", None)
            if self.av_fail_count >= 3:
                self._disable_av_for_today("AlphaVantage repeated failures")
            return None

    def get_alphavantage_sentiment(self, ticker: str) -> Optional[Dict]:
        """Backwards-compatible public method."""
        av = self._get_alphavantage_sentiment(ticker)
        if not av:
            return None
        details = av.get("details", {})
        return {
            'source': details.get('source', 'AlphaVantage'),
            'articles_count': details.get('articles_count', 0),
            'sentiment_count': details.get('sentiment_count', 0),
            'avg_sentiment': details.get('avg_sentiment', 0),
            'sentiment_score': av.get("score"),
            'confidence': av.get("conf"),
            'reason': details.get('reason', 'AlphaVantage sentiment')
        }
    
    def get_fallback_sentiment(self, ticker: str) -> Optional[Dict]:
        """
        Fallback ONLY when APIs completely fail
        Returns None if can't get good proxy data
        """
        
        try:
            from alpaca_data import AlpacaDataFeed
            data_feed = AlpacaDataFeed()
            
            bars = data_feed.get_daily_bars(ticker, days_back=10)
            
            if not bars or len(bars) < 5:
                return None
            
            volumes = [b['volume'] for b in bars[-5:]]
            closes = [b['close'] for b in bars[-5:]]
            
            avg_vol = sum(volumes[:-1]) / len(volumes[:-1])
            if avg_vol == 0:
                return None
            
            vol_ratio = volumes[-1] / avg_vol
            price_change = ((closes[-1] - closes[-3]) / closes[-3]) * 100
            
            # Only return a score if there's a CLEAR signal
            if vol_ratio > 1.5 and abs(price_change) > 3:
                if price_change > 0:
                    sentiment_score = 70
                    reason = f"Strong buying: Vol {vol_ratio:.1f}x, +{price_change:.1f}%"
                else:
                    sentiment_score = 30
                    reason = f"Strong selling: Vol {vol_ratio:.1f}x, {price_change:.1f}%"
                
                return {
                    'source': 'Price/Volume Proxy',
                    'sentiment_score': sentiment_score,
                    'confidence': 0.35,
                    'reason': reason
                }
            
            # If no clear signal, return None (don't fake it!)
            return None
            
        except Exception as e:
            return None
    
    def aggregate_sentiment(self, ticker: str) -> Dict:
        """
        Aggregate sentiment - STRICT MODE
        Only return scores if we have REAL data
        """
        
        print(f"\n🔍 Gathering sentiment for {ticker}...")
        
        sources = []

        news = self._get_news_sentiment(ticker)
        av = self._get_alphavantage_sentiment(ticker)

        news_score = (news or {}).get("score", 50.0)
        news_conf = (news or {}).get("conf", 0.0)

        av_score = (av or {}).get("score", 50.0)
        av_conf = (av or {}).get("conf", 0.0)

        if news:
            nd = news.get("details", {})
            sources.append(nd)
            print(f"  📰 News: {news_score:.1f} (conf {news_conf:.2f}) - {nd.get('reason', '')}")

        if av:
            ad = av.get("details", {})
            sources.append({
                "source": ad.get("source", "AlphaVantage"),
                "sentiment_score": av_score,
                "confidence": av_conf,
                "reason": ad.get("reason", "AlphaVantage data"),
            })
            print(f"  📊 AlphaVantage: {av_score:.1f} (conf {av_conf:.2f}) - {ad.get('reason', '')}")
        
        # If NO sources worked, try fallback as last resort
        if not sources:
            print(f"  ⚠️  No API data, trying fallback...")
            fallback = self.get_fallback_sentiment(ticker)
            if fallback:
                sources.append(fallback)
                print(f"  📉 Fallback: {fallback['sentiment_score']:.1f} - {fallback['reason']}")
        
        # If STILL no sources, return UNKNOWN with low confidence
        if not sources:
            print(f"  ❌ No sentiment data available")
            return {
                'ticker': ticker,
                'sentiment_score': None,  # Don't fake a score!
                'confidence': 0.0,
                'label': 'NO DATA',
                'sources_used': 0,
                'sources': [],
                'reason': 'No sentiment sources available'
            }
        
        # If AlphaVantage unavailable, use News only (no penalty, just less confidence)
        if av is None and news is not None:
            weighted_score = float(news_score)
            avg_confidence = float(news_conf)
        else:
            # Weighted by confidence across whatever sources are available
            total_weight = sum(s.get('confidence', 0) for s in sources)
            if total_weight > 0:
                weighted_score = sum(s.get('sentiment_score', 50) * s.get('confidence', 0) for s in sources) / total_weight
                avg_confidence = sum(s.get('confidence', 0) for s in sources) / len(sources)
            else:
                weighted_score = 50.0
                avg_confidence = 0.0
        
        # Label
        if weighted_score >= 65:
            label = 'BULLISH'
        elif weighted_score >= 55:
            label = 'SLIGHTLY BULLISH'
        elif weighted_score >= 45:
            label = 'NEUTRAL'
        elif weighted_score >= 35:
            label = 'SLIGHTLY BEARISH'
        else:
            label = 'BEARISH'
        
        return {
            'ticker': ticker,
            'sentiment_score': round(weighted_score, 1),
            'confidence': round(avg_confidence, 2),
            'label': label,
            'sources_used': len(sources),
            'sources': sources,
            'reason': f"Real data from {len(sources)} source(s)"
        }
    
    def vote_on_trade(self, ticker: str) -> Dict:
        """Veto Council voting - ABSTAIN if no real data"""
        
        analysis = self.aggregate_sentiment(ticker)
        
        # If no data at all, ABSTAIN (don't vote with fake data!)
        if analysis['sentiment_score'] is None or analysis['sources_used'] == 0:
            return {
                'vote': 'ABSTAIN',
                'reason': 'No sentiment data available',
                'score': None,
                'confidence': 0.0
            }
        
        score = analysis['sentiment_score']
        conf = analysis['confidence']
        label = analysis['label']

        if analysis['sources_used'] == 1 and conf < 0.50:
            return {
                'vote': 'ABSTAIN',
                'reason': f"Only 1 sentiment source with low confidence ({conf:.2f})",
                'score': None,
                'confidence': conf
            }
        
        # HARD VETO on strong bearish
        if label == 'BEARISH' and score < 35 and conf >= 0.55:
            return {
                'vote': 'VETO',
                'reason': f"BEARISH sentiment ({score}/100, {conf:.2f} conf) from {analysis['sources_used']} sources",
                'score': score,
                'confidence': conf
            }
        
        # APPROVE on strong bullish
        if label == 'BULLISH' and score >= 65 and conf >= 0.50:
            return {
                'vote': 'APPROVE',
                'reason': f"BULLISH sentiment ({score}/100, {conf:.2f} conf) from {analysis['sources_used']} sources",
                'score': score,
                'confidence': conf
            }
        
        # CAUTION on bearish lean
        if label in ['SLIGHTLY BEARISH', 'BEARISH'] and conf >= 0.40:
            return {
                'vote': 'CAUTION',
                'reason': f"{label} sentiment ({score}/100, {conf:.2f} conf)",
                'score': score,
                'confidence': conf
            }
        
        # ABSTAIN if low confidence
        if conf < 0.35:
            return {
                'vote': 'ABSTAIN',
                'reason': f"Low confidence sentiment ({conf:.2f})",
                'score': None,
                'confidence': conf
            }
        
        # Default CAUTION
        return {
            'vote': 'CAUTION',
            'reason': f"{label} ({score}/100, {conf:.2f} conf)",
            'score': score,
            'confidence': conf
        }


def test_real_sentiment():
    """Test real sentiment analyzer"""
    
    print("🚀 Testing Real Sentiment Analyzer with configured API keys\n")
    
    analyzer = RealSentimentAnalyzer()
    
    # Test on stocks
    test_tickers = ["AAPL", "BROS", "RKLB", "TSLA"]
    
    for ticker in test_tickers:
        print(f"\n{'='*80}")
        print(f"💭 REAL SENTIMENT: {ticker}")
        print(f"{'='*80}")
        
        try:
            analysis = analyzer.aggregate_sentiment(ticker)
            
            print(f"\n📊 AGGREGATED SENTIMENT:")
            print(f"  Label: {analysis['label']}")
            print(f"  Score: {analysis['sentiment_score']}/100")
            print(f"  Confidence: {analysis['confidence']:.2f}")
            print(f"  Sources: {analysis['sources_used']}")
            
            vote = analyzer.vote_on_trade(ticker)
            
            vote_emoji = {
                'APPROVE': '✅',
                'VETO': '❌',
                'CAUTION': '⚠️',
                'ABSTAIN': '⚪'
            }.get(vote['vote'], '❓')
            
            print(f"\n{vote_emoji} VOTE: {vote['vote']}")
            print(f"  {vote['reason']}")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
        
        print(f"\n{'='*80}\n")
        
        # Rate limiting
        import time
        time.sleep(2)


if __name__ == "__main__":
    test_real_sentiment()
