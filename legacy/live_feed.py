#!/usr/bin/env python3
"""
live_feed.py — Single shared websocket-fed live state (quotes + order fills).

Two websocket connections per process, each in its own daemon thread:
  LiveFeed    — StockDataStream  — live quotes/trades for subscribed tickers
  TradingFeed — TradingStream    — real-time order fills, partial fills, cancels

Usage (dashboard / any module):

    from live_feed import LiveFeed, TradingFeed

    LiveFeed.start(api_key, secret_key, tickers=["AAPL", "NVDA"])
    TradingFeed.start(api_key, secret_key, paper=True)

    q = LiveFeed.get_live_quote("AAPL")
    fills = TradingFeed.get_recent_fills(limit=10)

Cross-process readers:

    from live_feed import read_live_feed_state
    state = read_live_feed_state()   # reads live_feed_state.json

Design:
  - Background thread runs StockDataStream.run() (blocking asyncio loop)
  - Reconnect loop: exponential backoff 3s → 6s → … → 60s cap
  - Stale detection: quote older than STALE_SECONDS → get_live_quote returns None
  - Subscription update triggers a clean stop/reconnect cycle
  - Dashboard writes JSON snapshot every 2s for external readers
  - Alpaca SIP feed by default (requires Algo Trader plan); falls back to IEX
  - Never crashes if websocket unavailable; callers get None and use REST fallback
"""

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ssl_fix import configure_ssl_defaults

_SSL_CONTEXT = configure_ssl_defaults()

logger = logging.getLogger(__name__)

# Alpaca import guard to avoid ModuleLock deadlocks when multiple threads import simultaneously
_ALPACA_IMPORT_LOCK = threading.Lock()
_ALPACA_WARMED = False
_StockDataStream = None
_DataFeed = None


def _warm_alpaca_imports():
    """Import Alpaca live/historical modules once in the main thread to avoid ModuleLock deadlocks."""
    global _ALPACA_WARMED, _StockDataStream, _DataFeed
    if _ALPACA_WARMED:
        return
    with _ALPACA_IMPORT_LOCK:
        if _ALPACA_WARMED:
            return
        try:
            # Pre-import historical module to satisfy shared module lock
            import alpaca.data.historical  # noqa: F401
        except Exception:
            pass  # non-fatal; live stream will still retry
        try:
            from alpaca.data.live import StockDataStream as _SDS
            from alpaca.data.enums import DataFeed as _DF
            _StockDataStream = _SDS
            _DataFeed = _DF
        except Exception:
            # Leave None; _connect_and_run will raise a clear error later
            pass
        _ALPACA_WARMED = True

# ── constants ─────────────────────────────────────────────────────────────────

SNAPSHOT_PATH = "live_feed_state.json"
STALE_SECONDS = 15.0       # quote older than this → get_live_quote returns None
SNAPSHOT_INTERVAL = 2.0    # JSON snapshot max write frequency
_RECONNECT_BASE = 3.0      # initial reconnect backoff seconds
_RECONNECT_MAX = 60.0      # cap


# ── shared state schema ───────────────────────────────────────────────────────
#
# LiveFeed._quotes[ticker] = {
#   "bid"        : float | None
#   "ask"        : float | None
#   "mid"        : float | None
#   "last"       : float | None   (updated by trade stream)
#   "bid_size"   : int | None
#   "ask_size"   : int | None
#   "timestamp"  : str            ISO timestamp from Alpaca
#   "_updated_ts": float          local epoch ts when this record was written
# }
#
# LiveFeed.get_feed_status() returns:
# {
#   "status"          : "LIVE" | "STALE" | "STARTING" | "DISCONNECTED" | "STOPPED"
#   "last_msg_ts"     : float | None
#   "last_msg_age_s"  : float | None
#   "subscribed_count": int
#   "quote_count"     : int
#   "feed"            : "sip" | "iex" | "—"
# }


class LiveFeed:
    """
    Singleton websocket-fed live quote state.

    All attributes and methods are class-level.
    Do NOT instantiate — call LiveFeed.start() then LiveFeed.get_live_quote().
    """

    # ── class-level shared state ───────────────────────────────────────────
    _quotes: Dict[str, dict] = {}         # ticker → quote dict
    _status: str = "STOPPED"
    _last_msg_ts: float = 0.0             # epoch ts of most recent message
    _last_snapshot_ts: float = 0.0        # epoch ts of last JSON write
    _subscribed: List[str] = []           # current subscription list
    _lock: threading.Lock = threading.Lock()
    _thread: Optional[threading.Thread] = None
    _stop_event: threading.Event = threading.Event()
    _reconnect_event: threading.Event = threading.Event()   # signal reconnect
    _stream = None                         # StockDataStream instance
    _api_key: str = ""
    _secret_key: str = ""
    _feed_name: str = "sip"              # "sip" or "iex"

    # ── block trade detection ──────────────────────────────────────────────
    # Prints >= BLOCK_NOTIONAL_THRESHOLD are stored per ticker (newest first).
    # Callers read via LiveFeed.get_block_trades(ticker).
    BLOCK_NOTIONAL_THRESHOLD: float = 100_000.0   # $100K notional per print
    BLOCK_DEQUE_SIZE: int = 200                    # max prints kept per ticker
    _block_trades: Dict[str, object] = {}          # ticker → deque[dict]

    # ── lifecycle ─────────────────────────────────────────────────────────

    @classmethod
    def start(
        cls,
        api_key: str,
        secret_key: str,
        tickers: List[str] = None,
        feed: str = "sip",
    ):
        """
        Start the websocket background thread.
        Safe to call multiple times — no-op if already running, just updates subs.
        """
        _warm_alpaca_imports()  # preload modules before starting thread to avoid import deadlocks
        cls._api_key = api_key
        cls._secret_key = secret_key
        cls._feed_name = feed.lower()

        new_tickers = sorted(set(t.upper() for t in (tickers or []) if t))
        if set(new_tickers) != set(cls._subscribed):
            cls._subscribed = new_tickers

        if cls._thread and cls._thread.is_alive():
            # Already running — trigger reconnect to pick up subscription change
            cls._trigger_reconnect()
            return

        cls._stop_event.clear()
        cls._reconnect_event.clear()
        cls._status = "STARTING"

        cls._thread = threading.Thread(
            target=cls._run_loop,
            name="LiveFeedWS",
            daemon=True,
        )
        cls._thread.start()
        logger.info(
            "LiveFeed: started (feed=%s) with %d tickers", feed, len(new_tickers)
        )

    @classmethod
    def stop(cls):
        """Signal the background thread to stop cleanly."""
        cls._stop_event.set()
        cls._trigger_reconnect()
        cls._status = "STOPPED"
        logger.info("LiveFeed: stop requested")

    @classmethod
    def _trigger_reconnect(cls):
        """Interrupt a running stream.run() so the reconnect loop picks up changes."""
        cls._reconnect_event.set()
        stream = cls._stream
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass

    # ── background reconnect loop ─────────────────────────────────────────

    @classmethod
    def _run_loop(cls):
        """Outer reconnect loop with exponential backoff."""
        backoff = _RECONNECT_BASE
        while not cls._stop_event.is_set():
            cls._reconnect_event.clear()
            try:
                cls._status = "STARTING"
                cls._connect_and_run()
            except Exception as exc:
                logger.warning(
                    "LiveFeed: connection error (%s: %s) — retrying in %.0fs",
                    type(exc).__name__, exc, backoff,
                )
            else:
                if cls._stop_event.is_set():
                    break
                logger.info(
                    "LiveFeed: stream ended — reconnecting in %.0fs", backoff
                )

            cls._status = "DISCONNECTED"
            # Wait for backoff OR explicit reconnect trigger (subscription change)
            interrupted = cls._reconnect_event.wait(timeout=backoff)
            if cls._stop_event.is_set():
                break
            if not interrupted:
                # Normal backoff expiry — increase
                backoff = min(backoff * 2, _RECONNECT_MAX)
            else:
                # Explicit reconnect (e.g. subscription update) — reset backoff
                backoff = _RECONNECT_BASE

        cls._status = "STOPPED"
        logger.info("LiveFeed: background thread exited")

    @classmethod
    def _connect_and_run(cls):
        """Open one websocket connection and run until stopped or disconnected."""
        global _StockDataStream, _DataFeed
        if _StockDataStream is None or _DataFeed is None:
            with _ALPACA_IMPORT_LOCK:
                if _StockDataStream is None or _DataFeed is None:
                    try:
                        from alpaca.data.live import StockDataStream as _SDS
                        from alpaca.data.enums import DataFeed as _DF
                        _StockDataStream = _SDS
                        _DataFeed = _DF
                    except ImportError as e:
                        raise RuntimeError(f"alpaca-py not installed: {e}") from e

        feed_enum = _DataFeed.SIP if cls._feed_name == "sip" else _DataFeed.IEX
        stream = _StockDataStream(
            cls._api_key,
            cls._secret_key,
            feed=feed_enum,
        )
        cls._stream = stream

        tickers = list(cls._subscribed)
        if not tickers:
            logger.info("LiveFeed: no tickers subscribed — stream idle")
            # Still run so we can detect connection health
        else:
            async def _on_quote(q):
                cls._handle_quote(q)

            async def _on_trade(t):
                cls._handle_trade(t)

            stream.subscribe_quotes(_on_quote, *tickers)
            stream.subscribe_trades(_on_trade, *tickers)
            logger.info(
                "LiveFeed: connected (feed=%s), %d tickers: %s%s",
                feed_enum.value,
                len(tickers),
                tickers[:6],
                " ..." if len(tickers) > 6 else "",
            )

        cls._status = "LIVE"
        # stream.run() blocks until stream.stop() is called or disconnect
        stream.run()
        cls._stream = None

    # ── message handlers (called from within asyncio event loop) ──────────

    @classmethod
    def _handle_quote(cls, q):
        """Process incoming live quote."""
        try:
            sym = str(getattr(q, "symbol", None) or "").upper()
            if not sym:
                return
            bid = float(getattr(q, "bid_price", 0) or 0)
            ask = float(getattr(q, "ask_price", 0) or 0)
            mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else (bid or ask or 0.0)
            ts = getattr(q, "timestamp", None)
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts or "")
            now = time.time()
            with cls._lock:
                existing = cls._quotes.get(sym, {})
                cls._quotes[sym] = {
                    "bid": bid or None,
                    "ask": ask or None,
                    "mid": mid or None,
                    "last": existing.get("last") or (mid or None),
                    "bid_size": int(getattr(q, "bid_size", 0) or 0) or None,
                    "ask_size": int(getattr(q, "ask_size", 0) or 0) or None,
                    "timestamp": ts_str,
                    "_updated_ts": now,
                }
            cls._last_msg_ts = now
        except Exception as exc:
            logger.debug("LiveFeed: quote parse error: %s", exc)

    @classmethod
    def _handle_trade(cls, t):
        """Process incoming live trade (updates last price + block trade detection)."""
        try:
            sym = str(getattr(t, "symbol", None) or "").upper()
            if not sym:
                return
            price = float(getattr(t, "price", 0) or 0)
            if price <= 0:
                return
            size  = int(getattr(t, "size", 0) or 0)
            now   = time.time()

            # ── block trade detection ──────────────────────────────────────
            # A "block trade" is a single print whose notional >= threshold.
            # Trade conditions: 'I' = odd lot, '4' = derivatively priced — skip.
            notional = price * size
            if notional >= cls.BLOCK_NOTIONAL_THRESHOLD and size > 0:
                conditions = list(getattr(t, "conditions", None) or [])
                skip_conds = {"I", "4", "M", "W"}   # odd-lot, avg-price, intermarket
                if not any(str(c) in skip_conds for c in conditions):
                    ts_raw = getattr(t, "timestamp", None)
                    ts_str = ts_raw.isoformat() if hasattr(ts_raw, "isoformat") else str(ts_raw or "")
                    block = {
                        "ticker":    sym,
                        "price":     price,
                        "size":      size,
                        "notional":  round(notional, 0),
                        "timestamp": ts_str,
                        "epoch":     now,
                        "conditions": [str(c) for c in conditions],
                    }
                    with cls._lock:
                        if sym not in cls._block_trades:
                            from collections import deque as _deque
                            cls._block_trades[sym] = _deque(maxlen=cls.BLOCK_DEQUE_SIZE)
                        cls._block_trades[sym].appendleft(block)

            with cls._lock:
                if sym in cls._quotes:
                    cls._quotes[sym]["last"] = price
                    cls._quotes[sym]["_updated_ts"] = now
                else:
                    cls._quotes[sym] = {
                        "bid": None, "ask": None,
                        "mid": price, "last": price,
                        "bid_size": None, "ask_size": None,
                        "timestamp": str(getattr(t, "timestamp", "") or ""),
                        "_updated_ts": now,
                    }
            cls._last_msg_ts = now
        except Exception as exc:
            logger.debug("LiveFeed: trade parse error: %s", exc)

    # ── subscription management ───────────────────────────────────────────

    @classmethod
    def update_subscriptions(cls, tickers: List[str]):
        """
        Update the subscribed ticker list.
        If the list changed, triggers a clean reconnect to pick up new subs.
        """
        new = sorted(set(t.upper() for t in tickers if t))
        if set(new) == set(cls._subscribed):
            return  # no change
        cls._subscribed = new
        logger.info("LiveFeed: subscription changed (%d tickers) — reconnecting", len(new))
        cls._trigger_reconnect()

    # ── public read API ───────────────────────────────────────────────────

    @classmethod
    def get_live_quote(cls, ticker: str) -> Optional[dict]:
        """
        Return live quote dict for ticker, or None if unavailable / stale.

        Returns None when:
        - ticker not subscribed or no data received yet
        - last update older than STALE_SECONDS
        - feed is stopped or disconnected

        Dict keys: bid, ask, mid, last, bid_size, ask_size, timestamp, age_seconds
        """
        ticker = (ticker or "").upper().strip()
        if not ticker:
            return None
        with cls._lock:
            q = cls._quotes.get(ticker)
        if not q:
            return None
        age = time.time() - q.get("_updated_ts", 0)
        if age > STALE_SECONDS:
            return None  # stale — caller should fall back to REST
        return {
            "bid": q.get("bid"),
            "ask": q.get("ask"),
            "mid": q.get("mid"),
            "last": q.get("last"),
            "bid_size": q.get("bid_size"),
            "ask_size": q.get("ask_size"),
            "timestamp": q.get("timestamp", ""),
            "age_seconds": round(age, 2),
        }

    @classmethod
    def get_live_price(cls, ticker: str) -> Optional[float]:
        """Convenience: return best live price (mid → last) or None."""
        q = cls.get_live_quote(ticker)
        if not q:
            return None
        return q.get("mid") or q.get("last")

    @classmethod
    def get_block_trades(
        cls,
        ticker: str,
        max_age_seconds: float = 3600.0,
        limit: int = 50,
    ) -> List[dict]:
        """
        Return recent block trade prints for ticker (newest first).

        A block trade is any single SIP print with notional >=
        BLOCK_NOTIONAL_THRESHOLD ($100K default), excluding odd-lot /
        average-price / intermarket conditions.

        Args:
            ticker:           Stock symbol.
            max_age_seconds:  Discard prints older than this (default 1 hr).
            limit:            Max prints to return.

        Returns list of dicts: ticker, price, size, notional, timestamp, epoch.
        """
        ticker = (ticker or "").upper().strip()
        now = time.time()
        with cls._lock:
            dq = cls._block_trades.get(ticker)
            if not dq:
                return []
            prints = list(dq)

        cutoff = now - max_age_seconds
        filtered = [p for p in prints if p.get("epoch", 0) >= cutoff]
        return filtered[:limit]

    @classmethod
    def get_block_trade_summary(cls, ticker: str, window_seconds: float = 300.0) -> dict:
        """
        Aggregate block trade activity for ticker over the last window_seconds.

        Returns:
            count       — number of block prints
            total_notional — $ sum
            avg_notional   — average per print
            largest_print  — biggest single notional
            first_epoch / last_epoch — time window of activity
        """
        prints = cls.get_block_trades(ticker, max_age_seconds=window_seconds)
        if not prints:
            return {
                "ticker": ticker, "window_seconds": window_seconds,
                "count": 0, "total_notional": 0.0,
                "avg_notional": 0.0, "largest_print": 0.0,
                "first_epoch": None, "last_epoch": None,
            }
        notionals = [p["notional"] for p in prints]
        epochs    = [p["epoch"]    for p in prints]
        return {
            "ticker":          ticker,
            "window_seconds":  window_seconds,
            "count":           len(prints),
            "total_notional":  round(sum(notionals), 0),
            "avg_notional":    round(sum(notionals) / len(notionals), 0),
            "largest_print":   round(max(notionals), 0),
            "first_epoch":     min(epochs),
            "last_epoch":      max(epochs),
        }

    # Backward-compatible plural alias — both names are valid.
    get_block_trades_summary = get_block_trade_summary

    @classmethod
    def get_feed_status(cls) -> dict:
        """
        Return feed health dict for dashboard display.
        Keys: status, last_msg_ts, last_msg_age_s, subscribed_count, quote_count, feed
        """
        now = time.time()
        msg_age = (now - cls._last_msg_ts) if cls._last_msg_ts else None

        if cls._status == "STOPPED":
            label = "STOPPED"
        elif cls._status == "STARTING":
            label = "STARTING"
        elif cls._status == "DISCONNECTED":
            label = "DISCONNECTED"
        elif msg_age is None or msg_age > STALE_SECONDS:
            label = "STALE"
        else:
            label = "LIVE"

        with cls._lock:
            sub_count = len(cls._subscribed)
            quote_count = len(cls._quotes)

        return {
            "status": label,
            "last_msg_ts": cls._last_msg_ts or None,
            "last_msg_age_s": round(msg_age, 1) if msg_age is not None else None,
            "subscribed_count": sub_count,
            "quote_count": quote_count,
            "feed": cls._feed_name,
        }

    @classmethod
    def is_live(cls) -> bool:
        """True if the feed has recent data and is not stale."""
        return cls.get_feed_status()["status"] == "LIVE"

    # ── JSON snapshot (for cross-process readers) ─────────────────────────

    @classmethod
    def write_snapshot(cls, force: bool = False):
        """
        Write live_feed_state.json atomically.
        Throttled to SNAPSHOT_INTERVAL seconds unless force=True.
        Call from dashboard run loop every 2s.
        """
        now = time.time()
        if not force and (now - cls._last_snapshot_ts) < SNAPSHOT_INTERVAL:
            return
        cls._last_snapshot_ts = now

        fs = cls.get_feed_status()
        with cls._lock:
            quotes_clean = {
                ticker: {k: v for k, v in q.items() if not k.startswith("_")}
                for ticker, q in cls._quotes.items()
            }

        snapshot = {
            "status": fs["status"],
            "last_heartbeat_ts": datetime.now(timezone.utc).isoformat(),
            "last_msg_ts": cls._last_msg_ts or None,
            "feed": fs["feed"],
            "subscribed": list(cls._subscribed),
            "subscribed_count": fs["subscribed_count"],
            "quote_count": fs["quote_count"],
            "tickers": quotes_clean,
            # Include recent order fills from TradingFeed for cross-process readers
            "trading_status": TradingFeed.get_status()["status"],
            "recent_fills": TradingFeed.get_recent_fills(limit=20),
        }

        tmp = SNAPSHOT_PATH + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(snapshot, f, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, SNAPSHOT_PATH)
        except Exception as exc:
            logger.debug("LiveFeed: snapshot write error: %s", exc)


# ── TradingFeed — real-time order fill / cancel events ───────────────────────

class TradingFeed:
    """
    Singleton TradingStream connection for real-time order events.

    Provides fill/cancel/partial_fill events as they happen via Alpaca's
    trading websocket — no more 30s REST polling for order state.

    Usage:
        TradingFeed.start(api_key, secret_key, paper=True)
        fills = TradingFeed.get_recent_fills()
        status = TradingFeed.get_status()
    """

    # ── class-level state ─────────────────────────────────────────────────
    _fill_events: deque = deque(maxlen=50)   # recent fills / cancels
    _seen_event_keys: set = set()            # (order_id, event) dedup set
    _status: str = "STOPPED"
    _last_msg_ts: float = 0.0
    _thread: Optional[threading.Thread] = None
    _stop_event: threading.Event = threading.Event()
    _stream = None
    _api_key: str = ""
    _secret_key: str = ""
    _paper: bool = True
    _lock: threading.Lock = threading.Lock()

    # Events we surface to the dashboard (others are internal bookkeeping)
    _NOTABLE_EVENTS = {
        "fill", "partial_fill", "canceled", "expired",
        "replaced", "done_for_day", "pending_new",
    }

    # ── lifecycle ─────────────────────────────────────────────────────────

    @classmethod
    def start(cls, api_key: str, secret_key: str, paper: bool = True):
        """
        Start TradingStream background thread.
        Safe to call multiple times — no-op if already running.
        """
        _warm_alpaca_imports()  # share same import warm-up to avoid module lock contention
        if cls._thread and cls._thread.is_alive():
            return
        cls._api_key = api_key
        cls._secret_key = secret_key
        cls._paper = paper
        cls._stop_event.clear()
        cls._status = "STARTING"
        cls._thread = threading.Thread(
            target=cls._run_loop,
            name="TradingFeedWS",
            daemon=True,
        )
        cls._thread.start()
        logger.info("TradingFeed: started (paper=%s)", paper)

    @classmethod
    def stop(cls):
        """Signal the background thread to stop."""
        cls._stop_event.set()
        stream = cls._stream
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
        cls._status = "STOPPED"

    # ── reconnect loop ────────────────────────────────────────────────────

    @classmethod
    def _run_loop(cls):
        backoff = _RECONNECT_BASE
        while not cls._stop_event.is_set():
            try:
                cls._status = "STARTING"
                cls._connect_and_run()
            except Exception as exc:
                logger.warning(
                    "TradingFeed: error (%s: %s) — retry in %.0fs",
                    type(exc).__name__, exc, backoff,
                )
            else:
                if cls._stop_event.is_set():
                    break
                logger.info("TradingFeed: stream ended — reconnect in %.0fs", backoff)

            cls._status = "DISCONNECTED"
            cls._stop_event.wait(backoff)
            if cls._stop_event.is_set():
                break
            backoff = min(backoff * 2, _RECONNECT_MAX)

        cls._status = "STOPPED"
        logger.info("TradingFeed: thread exited")

    @classmethod
    def _connect_and_run(cls):
        try:
            from alpaca.trading.stream import TradingStream
        except ImportError as exc:
            raise RuntimeError(f"alpaca-py not installed: {exc}") from exc

        stream = TradingStream(
            cls._api_key, cls._secret_key, paper=cls._paper
        )
        cls._stream = stream

        async def _on_trade_update(data):
            cls._handle_trade_update(data)

        stream.subscribe_trade_updates(_on_trade_update)
        cls._status = "LIVE"
        logger.info("TradingFeed: connected (paper=%s)", cls._paper)
        stream.run()
        cls._stream = None

    # ── event handler ─────────────────────────────────────────────────────

    @classmethod
    def _handle_trade_update(cls, data):
        """Process incoming trade update (fill, cancel, etc.)."""
        try:
            event = str(getattr(data, "event", None) or "").lower()
            order = getattr(data, "order", None)
            if not order:
                return

            sym = str(getattr(order, "symbol", None) or "").upper()
            side = str(getattr(order, "side", None) or "").upper()
            order_type = str(getattr(order, "type", None) or "").lower()
            now = time.time()
            cls._last_msg_ts = now

            # Only surface notable events
            if event not in cls._NOTABLE_EVENTS:
                return

            # Build fill price / qty
            fill_price = None
            fill_qty = None
            if event in ("fill", "partial_fill"):
                raw_price = getattr(data, "price", None)
                raw_qty = getattr(data, "qty", None)
                fill_price = float(raw_price) if raw_price else None
                fill_qty = float(raw_qty) if raw_qty else None

            # Timestamp — prefer event ts, fall back to now
            ts_raw = getattr(data, "timestamp", None)
            if ts_raw:
                try:
                    ts_str = ts_raw.strftime("%H:%M:%S") if hasattr(ts_raw, "strftime") else str(ts_raw)[11:19]
                except Exception:
                    ts_str = datetime.now().strftime("%H:%M:%S")
            else:
                ts_str = datetime.now().strftime("%H:%M:%S")

            order_id = str(getattr(order, "id", "") or "")[:16]
            dedup_key = (order_id, event)

            record = {
                "ts": ts_str,
                "epoch_ts": now,
                "event": event,
                "ticker": sym,
                "side": side,
                "order_type": order_type,
                "fill_price": fill_price,
                "fill_qty": fill_qty,
                "order_id": order_id,
            }
            with cls._lock:
                if dedup_key in cls._seen_event_keys:
                    return
                cls._seen_event_keys.add(dedup_key)
                # Bound memory: keep last 250 keys when set grows large
                if len(cls._seen_event_keys) > 500:
                    cls._seen_event_keys = set(list(cls._seen_event_keys)[-250:])
                cls._fill_events.appendleft(record)

            logger.info(
                "TradingFeed: %s %s %s @ %s qty=%s",
                event.upper(), sym, side,
                f"${fill_price:.2f}" if fill_price else "—",
                fill_qty or "—",
            )
        except Exception as exc:
            logger.debug("TradingFeed: handler error: %s", exc)

    # ── public read API ───────────────────────────────────────────────────

    @classmethod
    def get_recent_fills(cls, limit: int = 10) -> List[dict]:
        """Return recent fill/cancel events, newest first."""
        with cls._lock:
            return list(cls._fill_events)[:limit]

    @classmethod
    def get_status(cls) -> dict:
        now = time.time()
        age = (now - cls._last_msg_ts) if cls._last_msg_ts else None
        label = cls._status
        # If "LIVE" but no message recently, mark stale
        if label == "LIVE" and (age is None or age > 120):
            label = "IDLE"   # IDLE = connected but no fills (normal during quiet periods)
        return {
            "status": label,
            "last_msg_ts": cls._last_msg_ts or None,
            "last_msg_age_s": round(age, 0) if age is not None else None,
            "fill_count": len(cls._fill_events),
            "paper": cls._paper,
        }


# ── module-level helpers for cross-process / fallback use ────────────────────

def read_live_feed_state() -> dict:
    """
    Read live_feed_state.json. Safe from any process.
    Returns {} if missing or corrupt.
    """
    try:
        with open(SNAPSHOT_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def get_live_price(ticker: str, fallback=None):
    """
    Best-effort live price. In-process LiveFeed first, then JSON snapshot.
    Returns fallback if no live data available.
    """
    # In-process (same process as websocket thread)
    price = LiveFeed.get_live_price(ticker)
    if price is not None:
        return price

    # Cross-process: read snapshot (staleness not rechecked here — callers verify)
    state = read_live_feed_state()
    tq = state.get("tickers", {}).get(ticker)
    if tq:
        p = tq.get("mid") or tq.get("last")
        if p:
            return float(p)

    return fallback
