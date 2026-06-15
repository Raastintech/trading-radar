#!/usr/bin/env python3
"""
Live Dashboard V3 — Operator Grade  (self-contained)
=====================================================
Professional, decision-dense terminal for the multi-strategy trading platform.

Design philosophy — answer in 3-5 seconds:
  1) What regime are we in?
  2) Which strategies are active / gated / hunting?
  3) Do I have open positions and are they protected?
  4) Are there active opportunities right now?
  5) If no trades, why not?
  6) Is macro/event risk blocking action?
  7) Is portfolio risk acceptable?

Data sources (in priority order):
  strategy   : pos["strategy"] → signal["strategy"] → meta["strategy"] → UNK
  direction  : pos["direction"] → meta["direction"] → — (never faked)
  stop       : Alpaca open orders (live) → DB decisions → log parser → —
  target     : Alpaca open orders (live) → DB decisions → log parser → —
  direction  : explicit → qty sign (< 0 = SHORT) → —
  protection : BRKT/S+T/STOP/TGT/BARE/UNKN — derived, never faked
  brackets   : _fetch_broker_brackets() queries Alpaca live open stop/limit orders
  VIX        : yfinance ^VIX (direct) — fetched every 15s
  SPY/VXX    : yfinance — fetched every 15s (log never emits these lines)
  strategy gates: Sniper gates off at VIX >= 20; Remora gates off at VIX > 30 (active at VIX <= 30)

Run:
  python live_dashboard_v3.py
  python live_dashboard_v3.py --log logs/trader_v3_YYYYMMDD.log
"""

import glob
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from secure_env import load_runtime_env
from strategy_display import get_strategy_display_name, get_strategy_short_name
from terminal_policy_snapshot import load_terminal_policy_snapshot
from terminal_trend_snapshot import compact_trend_labels, load_terminal_trend_snapshot
from terminal_cockpit import (
    build_action_buckets,
    compact_reason,
    compute_scan_delta,
    fetch_recent_blocked_decisions,
    meter_bar,
    percent_meter,
)

load_runtime_env("live_dashboard_v3")
try:
    from edge_analytics import EdgeAnalytics
except ImportError:
    EdgeAnalytics = None  # type: ignore

try:
    from scan_diagnostics import ScanDiagnosticsEngine as _ScanDiagEngine
    _SCAN_DIAG_AVAILABLE = True
except ImportError:
    _ScanDiagEngine = None  # type: ignore
    _SCAN_DIAG_AVAILABLE = False

try:
    from rr_shadow_calibration import RRShadowCalibrationEngine as _RRShadowEngine
    _RR_SHADOW_AVAILABLE = True
except ImportError:
    _RRShadowEngine = None  # type: ignore
    _RR_SHADOW_AVAILABLE = False

try:
    from shadow_outcome_tracker import ShadowOutcomeTracker as _ShadowOutcomeTracker
    _SHADOW_OUTCOME_AVAILABLE = True
except ImportError:
    _ShadowOutcomeTracker = None  # type: ignore
    _SHADOW_OUTCOME_AVAILABLE = False

try:
    from pilot_policy_controller import PilotPolicyController as _PilotCtrl
    _PILOT_AVAILABLE = True
except ImportError:
    _PilotCtrl = None  # type: ignore
    _PILOT_AVAILABLE = False

try:
    from options_positioning_tracker import OptionsPositioningTracker as _OptionsTracker
    _OPTIONS_TRACKER_AVAILABLE = True
except ImportError:
    _OptionsTracker = None
    _OPTIONS_TRACKER_AVAILABLE = False
try:
    from colorama import Fore, Style
except ImportError:
    class _ColorStub:
        GREEN = ""
        RED = ""
        CYAN = ""
        RESET_ALL = ""
    Fore = Style = _ColorStub()

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

try:
    from rich.console import Console, Group
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich import box as rich_box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

try:
    from macro_calendar import MacroCalendar
    CALENDAR_AVAILABLE = True
except ImportError:
    CALENDAR_AVAILABLE = False

try:
    from log_selection import select_active_trader_log
except ImportError:
    def select_active_trader_log(pattern):
        import glob as _glob
        files = sorted(_glob.glob(pattern))
        return files[-1] if files else None

try:
    from trading_state import normalize_position, read_heartbeat, get_daemon_status
except ImportError:
    # Inline fallbacks so the dashboard still runs if trading_state.py is absent
    def normalize_position(raw, meta=None, source=None):
        if meta is None:
            meta = {}
        nested = raw.get("signal", {}) if isinstance(raw, dict) else {}
        pm = raw.get("metadata", {}) if isinstance(raw, dict) else {}
        def _sf(v):
            try: return float(v)
            except Exception: return None
        def _pos(v): return v if v and float(v) > 0 else None
        rs = raw.get("strategy") or nested.get("strategy") or meta.get("strategy")
        stop = _pos(raw.get("stop_loss") or nested.get("stop_loss") or pm.get("stop_loss") or meta.get("stop_loss"))
        tgt = _pos(raw.get("target_price") or nested.get("target_price") or pm.get("target_price") or meta.get("target_price"))
        bracket = bool(meta.get("bracket_attached"))
        has_s, has_t = stop is not None, tgt is not None
        if not meta and not has_s and not has_t: prot = "UNKN"
        elif bracket: prot = "BRKT"
        elif has_s and has_t: prot = "S+T"
        elif has_s: prot = "STOP"
        elif has_t: prot = "TGT"
        else: prot = "BARE"
        return {
            "strategy": rs, "direction": raw.get("direction") or meta.get("direction"),
            "shares": _sf(raw.get("qty") or raw.get("shares")),
            "entry_price": _sf(raw.get("entry") or raw.get("entry_price")),
            "current_price": _sf(raw.get("current") or raw.get("current_price")),
            "stop_loss": stop, "target_price": tgt,
            "market_value": _sf(raw.get("market_value")),
            "unrealized_pl": _sf(raw.get("pnl") or raw.get("unrealized_pl")),
            "unrealized_pl_pct": _sf(raw.get("pnl_pct") or raw.get("unrealized_pl_pct")),
            "protection_status": prot, "state": str(meta.get("exit_mode", "hold")),
            "sector": raw.get("sector") or meta.get("sector"), "source": source or "unknown",
        }
    def read_heartbeat():
        try:
            import json as _json
            with open("trader_heartbeat.json") as f: return _json.load(f)
        except Exception: return {}
    def get_daemon_status():
        hb = read_heartbeat()
        if not hb:
            return "NO DAEMON"
        return hb.get("status", "UNKN")

try:
    from live_feed import LiveFeed, TradingFeed
    _LIVE_FEED_AVAILABLE = True
except ImportError:
    _LIVE_FEED_AVAILABLE = False
    class LiveFeed:
        """Stub when live_feed.py is absent."""
        @classmethod
        def start(cls, *a, **kw): pass
        @classmethod
        def stop(cls): pass
        @classmethod
        def update_subscriptions(cls, tickers): pass
        @classmethod
        def get_live_quote(cls, ticker): return None
        @classmethod
        def get_live_price(cls, ticker): return None
        @classmethod
        def get_feed_status(cls): return {"status": "UNAVAILABLE"}
        @classmethod
        def write_snapshot(cls, force=False): pass
        @classmethod
        def is_live(cls): return False

    class TradingFeed:
        """Stub when live_feed.py is absent."""
        @classmethod
        def start(cls, *a, **kw): pass
        @classmethod
        def stop(cls): pass
        @classmethod
        def get_recent_fills(cls, limit=10): return []
        @classmethod
        def get_status(cls): return {"status": "UNAVAILABLE"}


# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

class V3InstitutionalDashboard:
    """
    Data layer: log parsing, broker sync, DB hydration, live market fetch.
    All state lives here. Subclasses handle rendering only.
    """
    CONFLUENCE_TTL_SECONDS = 4 * 60 * 60

    _INVALID_STRATEGY_VALUES = {
        "LIVE", "PAPER", "DRY_RUN", "LIVE_TRADING", "PAPER_TRADING",
        "NONE", "NULL", "N/A", "", "ACTIVE", "STANDBY", "HUNTING",
    }

    def __init__(self, log_file: str = None):
        if not RICH_AVAILABLE:
            raise ImportError("rich library required: pip install rich")

        self.console = Console()

        # Macro calendar
        self.macro_cal = MacroCalendar() if CALENDAR_AVAILABLE else None

        # Log file
        if not log_file:
            log_file = select_active_trader_log("logs/trader_v3_*.log")
        self.log_file = log_file or "logs/trader_v3_20260226.log"
        self.last_position = 0
        # Start from EOF to avoid replaying stale historical confluence events.
        try:
            if self.log_file and os.path.exists(self.log_file):
                self.last_position = os.path.getsize(self.log_file)
        except Exception:
            self.last_position = 0

        # Market state — VIX/SPY/VXX populated via yfinance, not log parsing
        self.market_state = {
            "spy_state": "LOADING", "spy_change": "0.00%",
            "vix_level": "—", "vix_regime": "—",
            "vxx_change": "0.0%", "vxx_status": "NORMAL",
            "vix_source": "INIT", "vix_health": "INIT",
        }

        self.strategy_status = {
            "sniper": "LOADING", "sniper_reason": "Initializing...",
            "remora": "LOADING", "remora_reason": "Initializing...",
            "contrarian": "LOADING", "contrarian_reason": "Initializing...",
            "vix_sizing": "100%", "stress_sizing": "100%",
        }

        self.confluence_data = {
            "active": False, "count": 0,
            "tickers": [], "last_detected": None,
            "last_detected_ts": 0.0,   # epoch ts for expiry; tickers cleared after 4h
            "pre_signal_overlap": 0,
            "last_overlap_time": None,
            "universes": {"voyager": 0, "sniper": 0, "remora": 0},
        }

        # Broker
        self.trading_client = None
        try:
            from alpaca.trading.client import TradingClient
            api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
            secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
            if api_key and secret_key:
                self.trading_client = TradingClient(api_key, secret_key, paper=True)
        except Exception:
            pass

        self.positions: Dict = {}
        self.position_meta: Dict = {}
        self.account_info: Dict[str, float] = {}
        self.executed_orders: List = []
        self.pattern_distribution: Dict = {}
        self.opportunities = deque(maxlen=10)
        self.denials = deque(maxlen=20)
        self.recent_events = deque(maxlen=30)
        self._opp_seen: dict = {}        # (ticker, signal) → epoch ts; 30-min dedup
        self.recent_scans = deque(maxlen=5)
        self.scan_count = 0
        self.last_scan_time = None
        self.last_log_update = None
        self.last_scan_dt = None
        self._last_exec_ticker = None

        # Throttle timestamps — prevent hammering Alpaca on every 2s render tick
        self._last_positions_fetch: float = 0.0   # get_all_positions(): at most every 10s
        self._last_brackets_fetch: float = 0.0    # get_orders() brackets: at most every 30s
        self._last_journal_db_fetch: float = 0.0  # journal DB query: at most every 15s

        # Daemon heartbeat — read from trader_heartbeat.json every 5s
        self._heartbeat_state: dict = {}
        self._last_heartbeat_read: float = 0.0
        self._strategy_snapshot_backfilled = False
        self._risk_gate_snapshot: Dict[str, object] = {}
        self._last_risk_gate_refresh: float = 0.0
        self._policy_snapshot: Dict[str, object] = {}
        self._last_policy_snapshot_refresh: float = 0.0
        self._trend_snapshot: Dict[str, object] = {}
        self._last_trend_snapshot_refresh: float = 0.0
        self._action_snapshot: Dict[Tuple[str, str], Dict[str, float]] = {}
        self._action_delta_rows: List[Dict[str, str]] = []
        self._action_snapshot_scan_count: int = -1

        # Edge analytics cache (Phase 3)
        self._edge_cache: Dict = {}
        self._edge_cache_ts: float = 0.0

        # Options positioning cache — SPY PCR + gamma for FLOW/CONTEXT panel
        self._spy_options_cache: Dict = {}
        self._spy_options_ts: float = 0.0
        self._options_tracker = _OptionsTracker() if _OPTIONS_TRACKER_AVAILABLE else None

        # Scan quality diagnostics cache — refreshed every 5 minutes
        self._sq_cache: Dict = {}
        self._sq_ts: float = 0.0
        self._sq_engine = _ScanDiagEngine() if _SCAN_DIAG_AVAILABLE else None

        # RR shadow calibration cache — refreshed every 5 minutes
        self._rr_shadow_cache: Dict = {}
        self._rr_shadow_ts: float = 0.0
        self._rr_shadow_engine = _RRShadowEngine() if _RR_SHADOW_AVAILABLE else None

        # Shadow outcome tracker cache — refreshed every 5 minutes (read-only analysis)
        self._shadow_outcome_cache: Dict = {}
        self._shadow_outcome_ts: float = 0.0
        self._shadow_tracker = _ShadowOutcomeTracker() if _SHADOW_OUTCOME_AVAILABLE else None

        # Pilot policy cache — refreshed every 5 minutes (read-only, Phase 4.3)
        self._pilot_cache: Dict = {}
        self._pilot_ts: float = 0.0

        # News feed — refreshed every 120s in background thread
        self._news_items: List[Dict] = []
        self._last_news_fetch: float = 0.0
        self._news_lock = threading.Lock()

        # Live websocket feed — start background thread if keys available
        self._ws_subscribed: frozenset = frozenset()  # track last-sent subscription set
        self._ws_pos_tickers: frozenset = frozenset()  # position tickers as of last sub update
        self._last_ws_subscription_ts: float = 0.0    # epoch ts of last subscription push
        # Core tickers always subscribed for regime context (VIX proxy, market ETFs)
        self._ws_core: List[str] = ["SPY", "QQQ", "VXX", "IWM"]
        try:
            _ak = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID", "")
            _sk = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY", "")
            if _ak and _sk and _LIVE_FEED_AVAILABLE:
                LiveFeed.start(_ak, _sk, tickers=self._ws_core, feed="sip")
                TradingFeed.start(_ak, _sk, paper=True)
        except Exception:
            pass

    # ── helpers ───────────────────────────────────────────────────────────────

    def _now_et(self):
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("America/New_York"))
        return datetime.now()

    def _parse_scan_marker_dt(self, raw: str):
        if not raw:
            return None
        m = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)\s*ET", str(raw), re.IGNORECASE)
        if not m:
            m = re.search(r"\d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2}:\d{2})", str(raw))
            if m:
                try:
                    return datetime.strptime(m.group(0), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    return None
            return None
        try:
            t = datetime.strptime(m.group(1).strip(), "%I:%M %p")
            now = self._now_et()
            return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        except Exception:
            return None

    def _format_age(self, dt_obj) -> str:
        if dt_obj is None:
            return "—"
        try:
            now = datetime.now(dt_obj.tzinfo) if getattr(dt_obj, "tzinfo", None) else datetime.now()
            secs = max(0, int((now - dt_obj).total_seconds()))
        except Exception:
            return "—"
        if secs < 60:
            return f"{secs}s"
        mins = secs // 60
        if mins < 60:
            return f"{mins}m"
        return f"{mins // 60}h"

    def _safe_float(self, value, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _humanize_reject_reason(self, raw_reason, max_len: int = 24) -> str:
        """Convert machine reject keys into concise operator labels."""
        key = str(raw_reason or "").strip().lower()
        if not key:
            return "Unknown"
        mapping = {
            "risk_reward_too_low": "Risk/Reward Too Low",
            "universe_prefilter_failed": "Universe Pre-Filter Failed",
            "score_below_threshold": "Score Below Threshold",
            "no_voyager_pathway_qualified": "No Voyager Pathway",
            "no_short_pathway_qualified": "No Short Pathway",
            "position_sizing_failed": "Position Sizing Failed",
            "data_insufficient": "Data Insufficient",
            "data_error": "Data Error",
            "rs_declining": "RS Declining",
            "rs_declining_weak": "RS Declining (Weak)",
            "below_50ma": "Below 50-MA",
            "no_accumulation": "No Accumulation",
        }
        label = mapping.get(key)
        if not label:
            label = key.replace("_", " ").replace("-", " ").strip().title()
        if len(label) > max_len:
            return label[: max_len - 3].rstrip() + "..."
        return label

    def _edge_summary(self, ttl: int = 30) -> Optional[dict]:
        """Cache EdgeAnalytics summary to avoid hammering DB; returns None if unavailable."""
        if EdgeAnalytics is None:
            return None
        now = time.time()
        if (now - self._edge_cache_ts) > ttl or not self._edge_cache:
            try:
                ea = EdgeAnalytics()
                self._edge_cache = ea.get_operator_summary(lookback_days=90)
                self._edge_cache_ts = now
            except Exception:
                return None
        return self._edge_cache

    def _normalize_strategy(self, raw) -> str:
        """Map raw strategy names to 3-char display codes. Never returns mode/state values."""
        if raw is None:
            return "UNK"
        v = str(raw).strip().upper()
        if not v or v in self._INVALID_STRATEGY_VALUES:
            return "UNK"
        if v in ("VOYAGER", "VOY"):
            return "VOY"
        if v in ("SNIPER", "SNP"):
            return "SNP"
        if v in ("REMORA", "REM"):
            return "REM"
        if v in ("SHRT", "SHORT", "SHORT_BOOK", "SHORTBOOK"):
            return "SHRT"
        if v in ("CONTRARIAN", "REAPER", "RPR"):
            return "RPR"
        return v[:7]

    def _last_update_age(self) -> str:
        return self._format_age(self.last_log_update)

    def _last_scan_age(self) -> str:
        return self._format_age(self.last_scan_dt)

    def _refresh_spy_options(self) -> Dict:
        """Refresh SPY options positioning cache. TTL 120s. Returns cached dict."""
        now = time.time()
        if (now - self._spy_options_ts) < 120 and self._spy_options_cache:
            return self._spy_options_cache
        if not self._options_tracker:
            return {}
        try:
            data = self._options_tracker.analyze_positioning("SPY")
            if data:
                self._spy_options_cache = data
                self._spy_options_ts = now
        except Exception:
            pass
        return self._spy_options_cache

    def _refresh_scan_quality(self) -> Dict:
        """Refresh scan quality snapshot from DB. TTL 300s. Returns latest snapshot dict."""
        now = time.time()
        if (now - self._sq_ts) < 300 and self._sq_cache:
            return self._sq_cache
        if not self._sq_engine:
            return {}
        try:
            snap = self._sq_engine.get_latest_snapshot()
            if snap:
                self._sq_cache = snap
                self._sq_ts = now
        except Exception:
            pass
        return self._sq_cache

    def _refresh_rr_shadow(self) -> Dict:
        """Refresh RR shadow calibration snapshot from DB. TTL 300s."""
        now = time.time()
        if (now - self._rr_shadow_ts) < 300 and self._rr_shadow_cache:
            return self._rr_shadow_cache
        if not self._rr_shadow_engine:
            return {}
        try:
            snap = self._rr_shadow_engine.get_latest_snapshot()
            if snap:
                self._rr_shadow_cache = snap
                self._rr_shadow_ts = now
        except Exception:
            pass
        return self._rr_shadow_cache

    def _refresh_shadow_outcomes(self) -> Dict:
        """Refresh shadow outcome summary from DB. TTL 300s. Read-only analysis."""
        now = time.time()
        if (now - self._shadow_outcome_ts) < 300 and self._shadow_outcome_cache:
            return self._shadow_outcome_cache
        if not self._shadow_tracker:
            return {}
        try:
            summary = self._shadow_tracker.get_summary()
            if summary:
                self._shadow_outcome_cache = summary
                self._shadow_outcome_ts = now
        except Exception:
            pass
        return self._shadow_outcome_cache

    def _refresh_pilot(self) -> Dict:
        """Refresh pilot today-summary from pilot_events table. TTL 300s. Read-only."""
        now = time.time()
        if (now - self._pilot_ts) < 300 and self._pilot_cache:
            return self._pilot_cache
        if not _PILOT_AVAILABLE:
            return {}
        try:
            ctrl = _PilotCtrl()
            summary = ctrl.get_today_summary()
            if summary:
                self._pilot_cache = summary
                self._pilot_ts = now
        except Exception:
            pass
        return self._pilot_cache

    def _refresh_heartbeat(self):
        """Read trader_heartbeat.json at most every 5s. Updates self._heartbeat_state."""
        _now = time.time()
        if (_now - self._last_heartbeat_read) < 5:
            return
        self._last_heartbeat_read = _now
        self._heartbeat_state = read_heartbeat()

    def _classify_denial_bucket(self, decision: str, reason: str) -> str:
        text = f"{decision or ''} {reason or ''}".upper()
        if "RISK_OVERLAY" in text:
            return "Risk"
        if "PORTFOLIO" in text or "CIRCUIT_BREAKER" in text or "DAILY_LOSS_LIMIT" in text:
            return "Portfolio"
        if "CONCENTRATION" in text or "HEAT" in text:
            return "Concentration"
        if any(token in text for token in ("SPREAD", "HALT", "FROZEN", "QUOTE", "POSITION_SIZE", "EXECUTION_POLICY")):
            return "Execution"
        return "Other"

    def _refresh_risk_gate_snapshot(self):
        _now = time.time()
        if (_now - self._last_risk_gate_refresh) < 15:
            return
        self._last_risk_gate_refresh = _now

        snapshot = {
            "portfolio_mode": "UNKNOWN",
            "circuit_breaker": False,
            "daily_pnl": 0.0,
            "gate_counts": {},
            "strategy_counts": {},
            "latest_reason": "",
            "total_denials": 0,
        }

        self._refresh_heartbeat()
        hb = self._heartbeat_state or {}
        snapshot["portfolio_mode"] = str(hb.get("portfolio_mode") or "UNKNOWN")
        snapshot["circuit_breaker"] = bool(hb.get("portfolio_circuit_breaker", False))
        snapshot["daily_pnl"] = self._safe_float(hb.get("portfolio_daily_pnl"))

        db_path = "trading_performance.db"
        if os.path.exists(db_path):
            conn = None
            try:
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                cur.execute(
                    "SELECT timestamp, strategy, council_decision, execution_deny_reason "
                    "FROM decisions "
                    "WHERE execution_denied = 1 OR council_decision LIKE '%DENIED%' "
                    "ORDER BY timestamp DESC LIMIT 200"
                )
                cutoff = datetime.now() - timedelta(days=1)
                for ts_raw, strategy, decision, deny_reason in cur.fetchall():
                    try:
                        ts = datetime.fromisoformat(str(ts_raw))
                    except Exception:
                        continue
                    if ts < cutoff:
                        continue
                    bucket = self._classify_denial_bucket(decision, deny_reason)
                    snapshot["gate_counts"][bucket] = snapshot["gate_counts"].get(bucket, 0) + 1
                    strat = self._strategy_family_name(strategy or "UNK")
                    snapshot["strategy_counts"][strat] = snapshot["strategy_counts"].get(strat, 0) + 1
                    snapshot["total_denials"] += 1
                    if not snapshot["latest_reason"]:
                        snapshot["latest_reason"] = str(deny_reason or decision or "")[:72]
            except Exception:
                pass
            finally:
                if conn:
                    conn.close()

        self._risk_gate_snapshot = snapshot

    def _refresh_policy_snapshot(self):
        now_ts = time.time()
        if (now_ts - self._last_policy_snapshot_refresh) < 15:
            return
        self._last_policy_snapshot_refresh = now_ts
        self._policy_snapshot = load_terminal_policy_snapshot("trading_performance.db")

    def _refresh_trend_snapshot(self):
        now_ts = time.time()
        if (now_ts - self._last_trend_snapshot_refresh) < 60:
            return
        self._last_trend_snapshot_refresh = now_ts
        self._trend_snapshot = load_terminal_trend_snapshot("trading_performance.db", "logs", width=10)

    def _short_policy_label(self) -> Tuple[str, str]:
        self._refresh_policy_snapshot()
        snapshot = self._policy_snapshot or {}
        if snapshot.get("short_live_enabled", True):
            return "LIVE", "green"
        return "SHADOW", "bold yellow"

    def _voyager_breadth_label(self) -> Tuple[str, str]:
        self._refresh_policy_snapshot()
        snapshot = self._policy_snapshot or {}
        pct = snapshot.get("voyager_breadth_pct")
        mode = str(snapshot.get("voyager_breadth_mode") or "UNKNOWN")
        if pct is None:
            return "n/a", "dim"
        if mode == "SUPPRESSED":
            return f"{float(pct):.1f}% BLOCK", "bold red"
        if mode == "HALF_SIZE":
            return f"{float(pct):.1f}% HALF", "bold yellow"
        return f"{float(pct):.1f}% FULL", "green"

    def _forecast_policy_label(self) -> Tuple[str, str]:
        self._refresh_policy_snapshot()
        snapshot = self._policy_snapshot or {}
        threshold = int(snapshot.get("forecast_sample_threshold") or 200)
        active = list(snapshot.get("forecast_active_strategies") or [])
        if not active:
            return f"OFF <{threshold}", "yellow"
        short_names = ",".join(get_strategy_short_name(s) for s in active[:3])
        if len(active) > 3:
            short_names += "+"
        return f"LIVE {short_names}", "green"

    def _action_board_payload(self) -> Dict[str, List[Dict[str, object]]]:
        stale_cutoff = time.time() - 7200
        blocked = fetch_recent_blocked_decisions("trading_performance.db", limit=4)
        return build_action_buckets(
            list(self.opportunities),
            blocked_rows=blocked,
            stale_cutoff_ts=stale_cutoff,
            ready_limit=4,
            watch_limit=4,
            blocked_limit=4,
        )

    def _refresh_action_snapshot(self) -> Dict[str, List[Dict[str, object]]]:
        payload = self._action_board_payload()
        if self.scan_count != self._action_snapshot_scan_count:
            self._action_delta_rows, self._action_snapshot = compute_scan_delta(
                list(self.opportunities),
                self._action_snapshot,
                stale_cutoff_ts=time.time() - 7200,
                limit=4,
            )
            self._action_snapshot_scan_count = self.scan_count
        return payload

    def _daemon_status(self) -> tuple:
        """
        Return (label, style) based on heartbeat age.
          LIVE    age < 60s    bold green
          STALE   60–300s      bold yellow
          DEAD    > 300s       bold red
          NO DAEMON  absent    dim
        """
        hb = self._heartbeat_state
        if not hb:
            return "NO DAEMON", "dim"
        status = hb.get("status") or get_daemon_status()
        if status == "LIVE":
            return "LIVE", "bold green"
        if status == "STALE":
            return "STALE", "bold yellow"
        if status == "DEAD":
            return "DEAD", "bold red"
        return str(status), "dim"

    def _primary_ticker(self) -> str:
        if self.positions:
            try:
                top = max(
                    self.positions.items(),
                    key=lambda kv: abs(self._safe_float((kv[1] or {}).get("market_value"))),
                )
                return top[0]
            except Exception:
                return next(iter(self.positions.keys()))
        recent_ticker = self._latest_journal_ticker()
        if recent_ticker:
            return recent_ticker
        if self.confluence_data["tickers"]:
            return self.confluence_data["tickers"][0]
        if self.opportunities:
            return self.opportunities[0].get("ticker", "—")
        return "—"

    def _extract_ticker_from_journal_detail(self, detail: str) -> Optional[str]:
        try:
            m = re.match(r"\s*([A-Z]{1,6})\b", str(detail or ""))
            return m.group(1) if m else None
        except Exception:
            return None

    def _latest_journal_ticker(self) -> Optional[str]:
        for entry in list(self.recent_events):
            try:
                ticker = self._extract_ticker_from_journal_detail(entry[2])
                if ticker:
                    return ticker
            except Exception:
                continue
        return None

    _MEANINGFUL_EVENTS = {
        "OPEN", "SYNC", "EXIT", "FILL", "ENTRY_SUBMITTED", "APPROVED_FILL",
        "EXIT_SUBMITTED", "EXECUTE", "PORTFOLIO_DENIED", "EXECUTION_DENIED",
        "REJECT",
    }

    def _latest_action_context(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        for entry in list(self.recent_events):
            try:
                ts, event, detail = entry
                if str(event).upper() not in self._MEANINGFUL_EVENTS:
                    continue
                return str(ts), str(event), str(detail)
            except Exception:
                continue
        return None, None, None

    def _ensure_positions_snapshot(self):
        """Populate positions/meta for panels that should not rely on render order."""
        _now = time.time()
        if self.positions and (_now - self._last_positions_fetch) < 10:
            return
        if self.trading_client and (_now - self._last_positions_fetch) >= 10:
            try:
                raw = self.trading_client.get_all_positions()
                self._last_positions_fetch = _now
                self.positions = {}
                for p in raw:
                    self.positions[p.symbol] = normalize_position(
                        p,
                        source="ALPACA_API",
                        trading_client=self.trading_client,
                    )
            except Exception:
                self.positions = {}
                self._last_positions_fetch = _now
        if self.positions:
            self._refresh_position_meta_from_decisions()

    # ── VIX strategy gates ────────────────────────────────────────────────────

    def _apply_vix_strategy_gates(self):
        """
        Enforce VIX-based strategy gates on displayed status.

        Strategy mandates:
          Voyager : 6-18 month institutional macro moves — no VIX gate
          Sniper  : 1-50 day institutional moves — institutions sideline at VIX >= 20
          Remora  : 2-48h institutional flaw capture — viable up to VIX <= 30

        Universe count log lines ('Sniper: 73 stocks') reflect the watchlist pool,
        NOT the VIX gate. This override enforces the real gate on displayed state.
        """
        try:
            vix = float(self.market_state.get("vix_level", 0))
        except (ValueError, TypeError):
            return
        if vix <= 0:
            return
        if vix >= 20.0:
            self.strategy_status["sniper"] = "STANDBY"
            self.strategy_status["sniper_reason"] = f"VIX {vix:.1f} >= 20.0 (institutions sidelining)"
        if vix > 30.0:
            self.strategy_status["remora"] = "STANDBY"
            self.strategy_status["remora_reason"] = f"VIX {vix:.1f} > 30.0 (chaos — institutional footprints unreadable)"
        # Reaper (Contrarian): activates at VIX >= 28 (fear-opportunity zone)
        if vix >= 28.0:
            if self.strategy_status.get("contrarian") not in ("HUNTING",):
                self.strategy_status["contrarian"] = "ARMED"
                self.strategy_status["contrarian_reason"] = f"VIX {vix:.1f} >= 28.0 — scanning for fear setups"
        else:
            self.strategy_status["contrarian"] = "STANDBY"
            self.strategy_status["contrarian_reason"] = f"VIX {vix:.1f} < 28.0 — below trigger"

    # ── live market data fetch ────────────────────────────────────────────────

    def _fetch_live_market_data(self):
        """
        Fetch SPY / VIX / VXX using the resilient market-data path.
        Preference order:
          1. RegimeFilter (Yahoo with retry, Alpaca fallback)
          2. MarketDataHelper quote fallback
        SPY/VXX % change and VIX level are never emitted in the trading log.
        """
        self.market_state["vix_source"] = "DOWN"
        self.market_state["vix_health"] = "DOWN"

        try:
            from regime_filter import RegimeFilter
            regime = RegimeFilter()

            spy = regime.get_spy_regime() or {}
            if spy.get("regime") not in (None, "UNKNOWN"):
                self.market_state["spy_state"] = str(spy.get("regime"))
            if spy.get("distance_pct") is not None:
                self.market_state["spy_change"] = f"{float(spy.get('distance_pct')):+.2f}%"

            vix = regime.get_vix_regime() or {}
            vix_regime = str(vix.get("regime") or "")
            if vix.get("vix_level") is not None:
                self.market_state["vix_level"] = f"{float(vix.get('vix_level')):.1f}"
            if vix_regime:
                self.market_state["vix_regime"] = "HIGH" if vix_regime == "PANIC" else vix_regime
            source = str(vix.get("source") or "").upper()
            if source == "YAHOO":
                self.market_state["vix_source"] = "YAHOO"
                self.market_state["vix_health"] = "LIVE"
            elif source == "ALPACA_PROXY":
                self.market_state["vix_source"] = "VXX_PROXY"
                self.market_state["vix_health"] = "FALLBACK"
        except Exception:
            pass

        try:
            from terminal_helpers import MarketDataHelper
            vxx_quote = MarketDataHelper.get_stock_quote("VXX") or {}
            curr = vxx_quote.get("current_price")
            prev = vxx_quote.get("prev_close")
            if curr not in (None, 0) and prev not in (None, 0):
                pct = (float(curr) - float(prev)) / float(prev) * 100
                self.market_state["vxx_change"] = f"{pct:+.1f}%"
                self.market_state["vxx_status"] = (
                    "SPIKING" if pct > 3 else "ELEVATED" if pct > 1 else "NORMAL"
                )
        except Exception:
            pass

        if self.market_state.get("vix_source") == "DOWN" and self.market_state.get("vxx_change") not in ("0.0%", "", None):
            self.market_state["vix_source"] = "VXX_PROXY"
            self.market_state["vix_health"] = "FALLBACK"

    # ── news feed ─────────────────────────────────────────────────────────────

    def _fetch_news(self) -> None:
        """Pull critical market headlines from free RSS feeds. No API key needed."""
        _KEYWORDS = {
            "federal reserve", "fed ", "fomc", "powell", "rate hike", "rate cut",
            "interest rate", "inflation", "cpi", "ppi", "recession", "gdp",
            "circuit breaker", "trading halt", "market crash", "bank crisis",
            "tariff", "sanctions", "default", "bankruptcy", "earnings miss",
            "guidance cut", "jobs report", "nonfarm", "unemployment",
        }
        _FEEDS = [
            "https://finance.yahoo.com/news/rssindex",
            "http://feeds.marketwatch.com/marketwatch/topstories",
        ]
        items: List[Dict] = []
        for url in _FEEDS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    raw = resp.read()
                root = ET.fromstring(raw)
                for item in root.iter("item"):
                    title_el = item.find("title")
                    if title_el is None or not title_el.text:
                        continue
                    title = title_el.text.strip()
                    low = title.lower()
                    score = sum(1 for kw in _KEYWORDS if kw in low)
                    if score >= 1:
                        items.append({"title": title, "score": score})
            except Exception:
                pass
        items.sort(key=lambda x: -x["score"])
        with self._news_lock:
            self._news_items = items[:5]
        self._last_news_fetch = time.time()

    # ── log parsing ───────────────────────────────────────────────────────────

    def parse_log_file(self):
        """Read new log lines, parse into state, then apply VIX gates."""
        try:
            try:
                file_size = os.path.getsize(self.log_file)
                if self.last_position > file_size:
                    self.last_position = 0
            except Exception:
                pass
            with open(self.log_file, "r") as f:
                f.seek(self.last_position)
                new_lines = f.readlines()
                self.last_position = f.tell()
                for line in new_lines:
                    self._parse_line(line)
                if new_lines:
                    self.last_log_update = datetime.fromtimestamp(
                        os.path.getmtime(self.log_file)
                    ).astimezone()
        except Exception:
            pass
        self._expire_stale_confluence()
        self._apply_vix_strategy_gates()
        if not self._has_strategy_snapshot():
            self._backfill_strategy_snapshot()

    def _expire_stale_confluence(self):
        if not self.confluence_data.get("active"):
            return
        last_ts = float(self.confluence_data.get("last_detected_ts", 0.0) or 0.0)
        if last_ts <= 0:
            return
        if (time.time() - last_ts) > self.CONFLUENCE_TTL_SECONDS:
            self.confluence_data["active"] = False
            self.confluence_data["count"] = 0
            self.confluence_data["tickers"] = []
            self.confluence_data["last_detected"] = None

    def _has_strategy_snapshot(self) -> bool:
        if self.strategy_status.get("sniper") != "LOADING":
            return True
        if self.strategy_status.get("remora") != "LOADING":
            return True
        universes = self.confluence_data.get("universes", {})
        return any((universes.get(name) or 0) > 0 for name in ("voyager", "sniper", "remora"))

    def _backfill_strategy_snapshot(self):
        """Load the latest informative strategy snapshot from recent trader logs."""
        if self._strategy_snapshot_backfilled:
            return

        candidates = []
        for path in sorted(glob.glob("logs/trader_v3_*.log"), reverse=True):
            if path != self.log_file:
                candidates.append(path)

        for path in candidates[:5]:
            try:
                with open(path, "r") as handle:
                    for line in handle:
                        self._parse_strategy_snapshot_line(line)
                if self._has_strategy_snapshot():
                    self._strategy_snapshot_backfilled = True
                    return
            except Exception:
                continue

    def _format_journal_timestamp(self, ts_raw) -> str:
        """Format DB/log timestamps so stale rows are visibly old."""
        raw = str(ts_raw or "").strip()
        if not raw:
            return "—"
        try:
            dt = datetime.fromisoformat(raw)
        except Exception:
            try:
                dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                if len(raw) >= 19 and raw[4] == "-" and raw[7] == "-":
                    return f"{raw[5:7]}/{raw[8:10]} {raw[11:16]}"
                return raw[:10]

        today = datetime.now().date()
        if dt.date() == today:
            return dt.strftime("%H:%M:%S")
        return dt.strftime("%m/%d %H:%M")

    def _journal_strategy_for_ticker(self, ticker: str, qty: Optional[float] = None) -> str:
        """Resolve the best available strategy label for journal rows."""
        ticker = str(ticker or "").upper()
        pos = self.positions.get(ticker, {}) if isinstance(self.positions, dict) else {}
        meta = self.position_meta.get(ticker, {}) if isinstance(self.position_meta, dict) else {}
        overrides = self._load_position_overrides()
        override_meta = overrides.get(ticker, {}) if isinstance(overrides, dict) else {}

        strategy = None
        if isinstance(pos, dict):
            strategy = pos.get("strategy")
        if not strategy:
            strategy = meta.get("strategy")
        if not strategy:
            strategy = override_meta.get("strategy")
        if not strategy and isinstance(pos, dict):
            merged_meta = dict(meta)
            merged_meta.update(override_meta)
            strategy = normalize_position(pos, meta=merged_meta, source="journal").get("strategy")

        code = self._normalize_strategy(strategy)
        if code != "UNK":
            return code

        try:
            qty_val = float(qty if qty is not None else pos.get("qty"))
            return "SHRT" if qty_val < 0 else "UNK"
        except Exception:
            return "UNK"

    def _strategy_family_name(self, raw) -> str:
        code = self._normalize_strategy(raw)
        if code == "UNK":
            return "—"
        internal = {
            "VOY": "VOYAGER",
            "SNP": "SNIPER",
            "REM": "REMORA",
            "TRPL": "TRIPLE",
            "TRIPLE": "TRIPLE",
            "SHRT": "SHRT",
        }.get(code, code)
        if internal in ("TRPL", "TRIPLE"):
            return "TRIPLE"
        if internal == "SHRT":
            return get_strategy_display_name("SHORT")
        return get_strategy_display_name(internal)

    def _parse_strategy_snapshot_line(self, line: str):
        """Parse only durable strategy snapshot lines from historical logs."""
        if "Sniper Strategy: STANDBY" in line or "Sniper Strategy: ACTIVE" in line:
            if "STANDBY" in line:
                self.strategy_status["sniper"] = "STANDBY"
                self.strategy_status["sniper_reason"] = "VIX gate — institutions sidelining"
            elif "ACTIVE" in line:
                self.strategy_status["sniper"] = "ACTIVE"
                self.strategy_status["sniper_reason"] = "Signal engine active"
            return

        if "Sniper Strategy:" in line:
            if "STANDBY" in line:
                self.strategy_status["sniper"] = "STANDBY"
                self.strategy_status["sniper_reason"] = "Volatility gate"
            elif "ACTIVE" in line:
                self.strategy_status["sniper"] = "ACTIVE"
                self.strategy_status["sniper_reason"] = "Signal engine active"
            return

        if "Sniper universe:" in line and "candidates" in line:
            try:
                m = re.search(r"Sniper universe:\s*(\d+)\s*candidates", line)
                if m:
                    self.confluence_data["universes"]["sniper"] = int(m.group(1))
            except Exception:
                pass
            return

        if re.search(r"\bSniper:\s+(\d+)\s+stocks\b", line):
            try:
                m = re.search(r"\bSniper:\s+(\d+)\s+stocks\b", line)
                if m:
                    self.confluence_data["universes"]["sniper"] = int(m.group(1))
            except Exception:
                pass
            return

        if "Remora Strategy:" in line:
            if "HUNTING" in line or "ACTIVE" in line:
                self.strategy_status["remora"] = "HUNTING"
                self.strategy_status["remora_reason"] = "Predatory engine active"
            elif "STANDBY" in line:
                self.strategy_status["remora"] = "STANDBY"
                self.strategy_status["remora_reason"] = "Volatility gate"
            return

        if "Remora universe:" in line and "candidates" in line:
            try:
                m = re.search(r"Remora universe:\s*(\d+)\s*candidates", line)
                if m:
                    count = int(m.group(1))
                    self.confluence_data["universes"]["remora"] = count
                    if count > 0 and self.strategy_status.get("remora") not in ("STANDBY",):
                        self.strategy_status["remora"] = "HUNTING"
                        self.strategy_status["remora_reason"] = f"{count} symbols active"
            except Exception:
                pass
            return

        if re.search(r"\bRemora:\s+(\d+)\s+stocks\b", line):
            try:
                m = re.search(r"\bRemora:\s+(\d+)\s+stocks\b", line)
                if m:
                    count = int(m.group(1))
                    self.confluence_data["universes"]["remora"] = count
                    if count > 0 and self.strategy_status.get("remora") not in ("STANDBY",):
                        self.strategy_status["remora"] = "HUNTING"
                        self.strategy_status["remora_reason"] = f"{count} symbols active"
            except Exception:
                pass
            return

        if "Voyager universe:" in line and "candidates" in line:
            try:
                m = re.search(r"Voyager universe:\s*(\d+)\s*candidates", line)
                if m:
                    self.confluence_data["universes"]["voyager"] = int(m.group(1))
            except Exception:
                pass
            return

        if re.search(r"\bVoyager:\s+(\d+)\s+stocks\b", line):
            try:
                m = re.search(r"\bVoyager:\s+(\d+)\s+stocks\b", line)
                if m:
                    self.confluence_data["universes"]["voyager"] = int(m.group(1))
            except Exception:
                pass

        # Reaper/Contrarian backfill
        if "Running Contrarian scan" in line:
            self.strategy_status["contrarian"] = "HUNTING"
            self.strategy_status["contrarian_reason"] = "Fear scan active (historical)"
            return
        if "Contrarian: standing down" in line:
            self.strategy_status["contrarian"] = "STANDBY"
            self.strategy_status["contrarian_reason"] = "Below VIX trigger (historical)"
            return

    def _parse_line(self, line: str):
        # ── market state ──────────────────────────────────────────────────────
        if "SPY:" in line and "(" in line:
            try:
                parts = line.split("SPY:")[1].strip().split("(")
                self.market_state["spy_state"] = parts[0].strip()
                self.market_state["spy_change"] = parts[1].split(")")[0].strip()
            except Exception:
                pass

        # VIX lines in log: "VIX: 26.0 (HIGH) [VXX_PROXY]"
        # Guard against actual VXX ticker lines ("VXX: +1.2%") but allow [VXX_PROXY] tags
        if "VIX:" in line and "(" in line and "VXX:" not in line:
            try:
                parts = line.split("VIX:")[1].strip().split("(")
                level_str = parts[0].strip()
                regime_str = parts[1].split(")")[0].strip()
                # Only update from log if yfinance hasn't provided a real value yet.
                # "20.0" removed from guard — VIX can legitimately be 20.0.
                if self.market_state.get("vix_level") in ("—", ""):
                    self.market_state["vix_level"] = level_str
                    self.market_state["vix_regime"] = regime_str
            except Exception:
                pass

        if "VXX:" in line and "(" in line:
            try:
                parts = line.split("VXX:")[1].strip().split("(")
                self.market_state["vxx_change"] = parts[0].strip()
                self.market_state["vxx_status"] = parts[1].split(")")[0].strip()
            except Exception:
                pass

        # ── strategy status ───────────────────────────────────────────────────
        if "Sniper Strategy: STANDBY" in line or "Sniper Strategy: ACTIVE" in line:
            if "STANDBY" in line:
                self.strategy_status["sniper"] = "STANDBY"
                self.strategy_status["sniper_reason"] = "VIX gate — institutions sidelining"
            elif "ACTIVE" in line:
                self.strategy_status["sniper"] = "ACTIVE"
                self.strategy_status["sniper_reason"] = "Signal engine active"

        elif "Sniper Strategy:" in line:
            if "STANDBY" in line:
                self.strategy_status["sniper"] = "STANDBY"
                self.strategy_status["sniper_reason"] = "Volatility gate"
            elif "ACTIVE" in line:
                self.strategy_status["sniper"] = "ACTIVE"
                self.strategy_status["sniper_reason"] = "Signal engine active"

        elif "Sniper universe:" in line and "candidates" in line:
            # Universe count only — do NOT set ACTIVE from this (VIX gate owns that decision)
            try:
                m = re.search(r"Sniper universe:\s*(\d+)\s*candidates", line)
                if m:
                    self.confluence_data["universes"]["sniper"] = int(m.group(1))
            except Exception:
                pass

        elif re.search(r"\bSniper:\s+(\d+)\s+stocks\b", line):
            try:
                m = re.search(r"\bSniper:\s+(\d+)\s+stocks\b", line)
                if m:
                    self.confluence_data["universes"]["sniper"] = int(m.group(1))
            except Exception:
                pass

        if "Remora Strategy:" in line:
            if "HUNTING" in line or "ACTIVE" in line:
                self.strategy_status["remora"] = "HUNTING"
                self.strategy_status["remora_reason"] = "Predatory engine active"
            elif "STANDBY" in line:
                self.strategy_status["remora"] = "STANDBY"
                self.strategy_status["remora_reason"] = "Volatility gate"

        elif "Remora universe:" in line and "candidates" in line:
            try:
                m = re.search(r"Remora universe:\s*(\d+)\s*candidates", line)
                if m:
                    self.confluence_data["universes"]["remora"] = int(m.group(1))
                    if int(m.group(1)) > 0 and self.strategy_status.get("remora") not in ("STANDBY",):
                        self.strategy_status["remora"] = "HUNTING"
                        self.strategy_status["remora_reason"] = f"{m.group(1)} symbols active"
            except Exception:
                pass

        elif re.search(r"\bRemora:\s+(\d+)\s+stocks\b", line):
            try:
                m = re.search(r"\bRemora:\s+(\d+)\s+stocks\b", line)
                if m:
                    count = int(m.group(1))
                    self.confluence_data["universes"]["remora"] = count
                    if count > 0 and self.strategy_status.get("remora") not in ("STANDBY",):
                        self.strategy_status["remora"] = "HUNTING"
                        self.strategy_status["remora_reason"] = f"{count} symbols active"
            except Exception:
                pass

        # ── Reaper (Contrarian) strategy status ───────────────────────────────
        if "Running Contrarian scan" in line:
            self.strategy_status["contrarian"] = "HUNTING"
            try:
                m = re.search(r"VIX=([0-9.]+)", line)
                vix_str = f"VIX={m.group(1)}" if m else "scanning"
                self.strategy_status["contrarian_reason"] = f"Fear scan active ({vix_str})"
            except Exception:
                self.strategy_status["contrarian_reason"] = "Fear scan active"

        elif "Contrarian: standing down" in line:
            self.strategy_status["contrarian"] = "STANDBY"
            try:
                m = re.search(r"VIX=([0-9.]+)", line)
                vix_str = f"VIX={m.group(1)}" if m else ""
                self.strategy_status["contrarian_reason"] = f"Below trigger {vix_str}".strip()
            except Exception:
                self.strategy_status["contrarian_reason"] = "Below trigger"

        elif "Contrarian:" in line and "setups" in line:
            try:
                m = re.search(r"Contrarian:\s*(\d+)\s*setups", line)
                count = int(m.group(1)) if m else 0
                if count > 0:
                    self.strategy_status["contrarian"] = "HUNTING"
                    self.strategy_status["contrarian_reason"] = f"{count} fear setup(s) found"
                else:
                    self.strategy_status["contrarian"] = "ARMED"
                    self.strategy_status["contrarian_reason"] = "No setups this cycle"
            except Exception:
                pass

        elif "ContrarianScanner:" in line and "setups" in line:
            try:
                m = re.search(r"(\d+)\s*setups", line)
                count = int(m.group(1)) if m else 0
                if count > 0:
                    self.strategy_status["contrarian"] = "HUNTING"
                    self.strategy_status["contrarian_reason"] = f"{count} fear setup(s) found"
                elif "standing down" not in line:
                    self.strategy_status["contrarian"] = "ARMED"
                    self.strategy_status["contrarian_reason"] = "No setups this cycle"
            except Exception:
                pass

        # ── sizing ────────────────────────────────────────────────────────────
        if "VIX sizing:" in line:
            try:
                self.strategy_status["vix_sizing"] = line.split("VIX sizing:")[1].strip().split()[0]
            except Exception:
                pass

        if "Stress sizing:" in line:
            try:
                self.strategy_status["stress_sizing"] = line.split("Stress sizing:")[1].strip().split()[0]
            except Exception:
                pass

        # ── triple confluence ─────────────────────────────────────────────────
        if "Pre-signal overlap:" in line:
            try:
                overlap = int(line.split("Pre-signal overlap:", 1)[1].split("symbols")[0].strip())
                self.confluence_data["pre_signal_overlap"] = overlap
                self.confluence_data["last_overlap_time"] = datetime.now().strftime("%H:%M")
            except Exception:
                pass

        if (
            "No signal-level triple confluence detected" in line
            or "No triple confluence at this time" in line
        ):
            self.confluence_data["active"] = False
            self.confluence_data["count"] = 0
            self.confluence_data["tickers"] = []

        if "TRIPLE CONFLUENCE DETECTED" in line:
            try:
                count = None
                m = re.search(r"TRIPLE CONFLUENCE DETECTED:\s*(\d+)\s*stocks?", line)
                if m:
                    count = int(m.group(1))
                else:
                    m = re.search(r"(\d+)\s+TRIPLE CONFLUENCE DETECTED", line)
                    if m:
                        count = int(m.group(1))
                if count is None:
                    raise ValueError("no parseable triple confluence count")
                _now_ts = time.time()
                # New confluence event — always reset ticker list to this cycle.
                self.confluence_data["tickers"] = []
                self.confluence_data["active"] = True
                self.confluence_data["count"] = count
                self.confluence_data["last_detected"] = datetime.now().strftime("%H:%M")
                self.confluence_data["last_detected_ts"] = _now_ts
            except Exception:
                pass

        if (
            "⭐⭐⭐" in line
            and "EXECUTING:" not in line
            and (self.confluence_data.get("active") or "MAXIMUM CONVICTION" in line)
        ):
            try:
                m = re.search(r"⭐⭐⭐\s*([A-Z]{1,6})\b", line)
                ticker = m.group(1) if m else None
                if ticker and ticker not in self.confluence_data["tickers"]:
                    self.confluence_data["tickers"].append(ticker)
                    _now_ts = time.time()
                    self._opp_seen[(ticker, "TRIPLE CONFLUENCE")] = _now_ts
                    self.opportunities.appendleft({
                        "ticker": ticker,
                        "signal": "TRIPLE CONFLUENCE",
                        "time": datetime.now().strftime("%H:%M"),
                        "conviction": "MAXIMUM",
                        "_ts": _now_ts,
                    })
            except Exception:
                pass

        # ── universe sizes ────────────────────────────────────────────────────
        if ("Voyager universe:" in line or "Voyager:" in line) and "candidates" in line:
            try:
                count = int(line.split("candidates")[0].split()[-1])
                self.confluence_data["universes"]["voyager"] = count
            except Exception:
                pass

        # ── scan markers ──────────────────────────────────────────────────────
        if "V3 INTELLIGENT SCAN" in line or "SCAN CYCLE" in line or "VOYAGER PRODUCTION COMPLETE SCAN" in line:
            try:
                time_str = line.split("-")[-1].strip()
                self.last_scan_time = time_str
                self.last_scan_dt = self._parse_scan_marker_dt(line)
                self.scan_count += 1
                self.recent_scans.appendleft({
                    "time": time_str,
                    "opportunities": 0,
                    "status": "Completed",
                })
            except Exception:
                pass

        if any(k in line for k in ("Bottoming:", "Accumulation:", "Breakout:")):
            try:
                name, val = line.split(":", 1)
                self.pattern_distribution[name.strip().split()[-1]] = int(val.strip().split()[0])
            except Exception:
                pass

        # ── opportunities ─────────────────────────────────────────────────────
        if "Found:" in line and "opportunities" in line:
            try:
                count = int(line.split("Found:")[1].split("opportunities")[0].strip())
                if self.recent_scans:
                    self.recent_scans[0]["opportunities"] = count
            except Exception:
                pass

        # Only capture BULLISH/BEARISH lines that look like scanner signal output,
        # not regime/status summaries which mention these words every scan cycle.
        _NON_TICKER_WORDS = {
            "BULL", "BEAR", "BULLISH", "BEARISH", "VIX", "SPY", "SPX", "QQQ",
            "ETF", "LONG", "SHORT", "BUY", "SELL", "HIGH", "LOW", "OPEN",
            "CALM", "NORMAL", "ELEVATED", "EXTREME", "ACTIVE", "STANDBY",
            "SNIPER", "REMORA", "VOYAGER", "STRATEGY", "REGIME", "SIGNAL",
        }
        _SIGNAL_CONTEXT = (
            "SIGNAL:", "SETUP:", "BREAKOUT:", "CANDIDATE:", "OPPORTUNITY:",
            "ALERT:", "RANKED:", "SCORE:", "CONFIRMATION:",
        )
        if ("BULLISH" in line or "BEARISH" in line) and any(ctx in line for ctx in _SIGNAL_CONTEXT):
            for word in line.split():
                if (word.isupper() and 2 <= len(word) <= 6 and word.isalpha()
                        and word not in _NON_TICKER_WORDS):
                    signal = "BULLISH" if "BULLISH" in line else "BEARISH"
                    key = (word, signal)
                    _now_ts = time.time()
                    if _now_ts - self._opp_seen.get(key, 0) >= 1800:  # 30-min dedup
                        self._opp_seen[key] = _now_ts
                        self.opportunities.appendleft({
                            "ticker": word,
                            "signal": signal,
                            "time": datetime.now().strftime("%H:%M"),
                            "_ts": _now_ts,
                        })
                    break

        # ── execution context ─────────────────────────────────────────────────
        if "EXECUTING:" in line:
            m = re.search(r"EXECUTING:\s+([A-Z]{1,6})\s+\(([^)]+)\)", line)
            if m:
                t = m.group(1).upper()
                s = m.group(2).upper()
                self._last_exec_ticker = t
                rec = self.position_meta.get(t, {})
                rec["strategy"] = s
                self.position_meta[t] = rec

        # New bracket execution format: "BRACKET ORDER EXECUTED!"
        if "BRACKET ORDER EXECUTED" in line and self._last_exec_ticker:
            rec = self.position_meta.get(self._last_exec_ticker, {})
            rec["bracket_attached"] = True
            self.position_meta[self._last_exec_ticker] = rec

        if self._last_exec_ticker and "Direction:" in line:
            m = re.search(r"Direction:\s*(LONG|SHORT)", line)
            if m:
                rec = self.position_meta.get(self._last_exec_ticker, {})
                rec["direction"] = m.group(1)
                self.position_meta[self._last_exec_ticker] = rec

        if self._last_exec_ticker and "Stop:" in line and "$" in line:
            m = re.search(r"Stop:\s*\$([0-9]+(?:\.[0-9]+)?)", line)
            if m:
                rec = self.position_meta.get(self._last_exec_ticker, {})
                rec["stop_loss"] = self._safe_float(m.group(1))
                rec["stop_active"] = True
                rec["bracket_attached"] = bool(rec.get("stop_loss") and rec.get("target_price"))
                self.position_meta[self._last_exec_ticker] = rec

        if self._last_exec_ticker and "Target:" in line and "$" in line:
            m = re.search(r"Target:\s*\$([0-9]+(?:\.[0-9]+)?)", line)
            if m:
                rec = self.position_meta.get(self._last_exec_ticker, {})
                rec["target_price"] = self._safe_float(m.group(1))
                rec["target_active"] = True
                rec["bracket_attached"] = bool(rec.get("stop_loss") and rec.get("target_price"))
                self.position_meta[self._last_exec_ticker] = rec

        # ── Sniper V2 breakout opportunities ──────────────────────────────────
        if "SniperV2 BREAKOUT:" in line:
            m = re.search(r"SniperV2 BREAKOUT:\s+([A-Z]{1,6})", line)
            if m:
                score_m = re.search(r"score\s+(\d+)", line)
                rr_m = re.search(r"R/R\s+([0-9.]+)", line)
                _now_ts = time.time()
                key = (m.group(1), "SNP BREAKOUT")
                if _now_ts - self._opp_seen.get(key, 0) >= 1800:
                    self._opp_seen[key] = _now_ts
                    self.opportunities.appendleft({
                        "ticker": m.group(1),
                        "signal": "SNP BREAKOUT",
                        "time": datetime.now().strftime("%H:%M"),
                        "score": int(score_m.group(1)) if score_m else 0,
                        "rr": float(rr_m.group(1)) if rr_m else 0.0,
                        "_ts": _now_ts,
                    })

        # ── denials ───────────────────────────────────────────────────────────
        denial_markers = [
            "EXECUTION DENIED:", "ENTRY DENIED:", "EXIT DENIED:",
            "PORTFOLIO_DENIED", "VETO",
        ]
        if any(mk in line for mk in denial_markers):
            reason = line.strip()
            ticker = "—"
            for word in line.replace("|", " ").replace(":", " ").split():
                if word.isupper() and 1 < len(word) <= 6 and word.isalpha():
                    ticker = word
                    break
            self.denials.appendleft({
                "time": datetime.now().strftime("%H:%M:%S"),
                "ticker": ticker,
                "reason": reason[-84:],
            })

        # ── journal — log-file fallback (DB is authoritative via _refresh_journal_from_db) ──
        # These patterns capture real print() output from the trading engine.
        if "EXECUTING:" in line and ("SNIPER" in line or "REMORA" in line or "VOYAGER" in line):
            m = re.search(r"EXECUTING:\s+([A-Z]{1,6})\s+\(([^)]+)\)", line)
            if m:
                detail = f"{m.group(1)} [{self._normalize_strategy(m.group(2))}]"
                self.recent_events.appendleft(
                    (datetime.now().strftime("%H:%M:%S"), "ENTRY_SUBMITTED", detail)
                )
        elif "EXIT:" in line and "shares" in line:
            m = re.search(r"EXIT:\s+(\d+)\s+shares", line)
            detail = f"{m.group(1)} shares" if m else line.strip()[-48:]
            self.recent_events.appendleft(
                (datetime.now().strftime("%H:%M:%S"), "EXIT_SUBMITTED", detail)
            )
        elif "EXECUTION DENIED:" in line or "Entry denied:" in line:
            self.recent_events.appendleft(
                (datetime.now().strftime("%H:%M:%S"), "EXECUTION_DENIED", line.strip()[-64:])
            )

    # ── DB / broker hydration ─────────────────────────────────────────────────

    def _refresh_journal_from_db(self):
        """Pull recent trade decisions from DB into self.recent_events (journal panel).

        The trading engine writes ENTRY_SUBMITTED / APPROVED_FILL / EXIT_SUBMITTED /
        PORTFOLIO_DENIED as council_decision values in the DB — they are NOT emitted
        as log-file strings. This method is the authoritative journal source.
        Throttled to at most every 15s to avoid unnecessary DB overhead.
        """
        _now = time.time()
        if (_now - self._last_journal_db_fetch) < 15:
            return
        self._last_journal_db_fetch = _now

        db_path = "trading_performance.db"
        if not os.path.exists(db_path):
            return
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'"
            )
            if not cur.fetchone():
                return
            self.recent_events.clear()

            today_prefix = datetime.now().strftime("%Y-%m-%d")

            cur.execute(
                "SELECT entry_date, ticker, quantity, entry_price, notes "
                "FROM trades "
                "WHERE entry_date LIKE ? "
                "ORDER BY entry_date DESC LIMIT 10",
                (f"{today_prefix}%",),
            )
            rows = cur.fetchall()
            for ts_raw, ticker, qty, entry_price, notes in rows:
                try:
                    ts_fmt = self._format_journal_timestamp(ts_raw)
                    strat_code = self._journal_strategy_for_ticker(ticker, qty)
                    strat_label = self._strategy_family_name(strat_code)
                    event = "SYNC" if "imported from existing alpaca position" in str(notes or "").lower() else "OPEN"
                    qty_txt = f"{abs(int(float(qty)))} sh" if qty not in (None, "") else "size ?"
                    px_txt = f" @ ${float(entry_price):.2f}" if entry_price not in (None, "") else ""
                    self.recent_events.append((ts_fmt, event, f"{ticker or '—'} [{strat_label}] {qty_txt}{px_txt}"))
                    if len(self.recent_events) >= 5:
                        break
                except Exception:
                    pass

            cur.execute(
                "SELECT timestamp, ticker, strategy, exit_reason, exit_price, return_pct "
                "FROM exit_outcomes "
                "WHERE timestamp LIKE ? "
                "ORDER BY timestamp DESC LIMIT 10",
                (f"{today_prefix}%",),
            )
            rows = cur.fetchall()
            for ts_raw, ticker, strategy, exit_reason, exit_price, return_pct in rows:
                try:
                    ts_fmt = self._format_journal_timestamp(ts_raw)
                    strat_code = self._strategy_family_name(strategy)
                    detail = f"{ticker or '—'} [{strat_code}] {str(exit_reason or 'EXIT')[:18]}"
                    if exit_price not in (None, ""):
                        detail += f" @ ${float(exit_price):.2f}"
                    if return_pct not in (None, ""):
                        detail += f" ({float(return_pct):+.1f}%)"
                    self.recent_events.append((ts_fmt, "EXIT", detail))
                    if len(self.recent_events) >= 5:
                        break
                except Exception:
                    pass

            if self.recent_events:
                return

            cur.execute(
                "SELECT timestamp, ticker, council_decision, strategy "
                "FROM decisions "
                "WHERE council_decision IN ("
                "  'ENTRY_SUBMITTED','APPROVED_FILL','EXIT_SUBMITTED',"
                "  'PORTFOLIO_DENIED','EXECUTION_DENIED','EXECUTE','REJECT'"
                ") "
                "AND timestamp LIKE ? "
                "ORDER BY timestamp DESC LIMIT 30"
            , (f"{today_prefix}%",))
            rows = cur.fetchall()
            for ts_raw, tkr, decision, strat in rows:
                try:
                    ts_fmt = self._format_journal_timestamp(ts_raw)
                    strat_code = self._strategy_family_name(strat)
                    label = str(decision or "").upper()
                    self.recent_events.append((ts_fmt, label, f"{tkr or '—'} [{strat_code}]"))
                    if len(self.recent_events) >= 5:
                        break
                except Exception:
                    pass

            if self.recent_events:
                return

            cur.execute(
                "SELECT timestamp, ticker, council_decision, strategy "
                "FROM decisions "
                "WHERE council_decision IN ('DATA_ERROR','FILTERED_LOW_RR','FILTERED_NO_SIGNAL') "
                "AND timestamp LIKE ? "
                "ORDER BY timestamp DESC LIMIT 10"
            , (f"{today_prefix}%",))
            rows = cur.fetchall()
            for ts_raw, tkr, decision, strat in rows:
                try:
                    ts_fmt = self._format_journal_timestamp(ts_raw)
                    strat_code = self._strategy_family_name(strat)
                    label = str(decision or "").upper()
                    self.recent_events.append((ts_fmt, label, f"{tkr or '—'} [{strat_code}]"))
                    if len(self.recent_events) >= 5:
                        break
                except Exception:
                    pass
            if self.recent_events:
                return

            cur.execute(
                "SELECT timestamp, ticker, council_decision, strategy "
                "FROM decisions "
                "WHERE council_decision IN ("
                "  'ENTRY_SUBMITTED','APPROVED_FILL','EXIT_SUBMITTED',"
                "  'PORTFOLIO_DENIED','EXECUTION_DENIED','EXECUTE','REJECT'"
                ") "
                "ORDER BY timestamp DESC LIMIT 30"
            )
            rows = cur.fetchall()
            for ts_raw, tkr, decision, strat in rows:
                try:
                    ts_fmt = self._format_journal_timestamp(ts_raw)
                    strat_code = self._strategy_family_name(strat)
                    label = str(decision or "").upper()
                    self.recent_events.append((ts_fmt, label, f"{tkr or '—'} [{strat_code}]"))
                    if len(self.recent_events) >= 5:
                        break
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

    def _load_position_overrides(self) -> dict:
        """Load position_overrides.json sidecar for manual stop/target/strategy overrides."""
        path = os.path.join(os.path.dirname(self.log_file or "."), "position_overrides.json")
        if not os.path.exists(path):
            path = "position_overrides.json"
        if not os.path.exists(path):
            return {}
        try:
            with open(path) as f:
                data = json.load(f)
            return {k.upper(): v for k, v in data.items()} if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _fetch_broker_brackets(self):
        """
        Hydrate stop_loss / target_price / bracket_attached from LIVE Alpaca open orders.
        Bracket child orders (type=stop → stop_loss; type=limit → take_profit) are the
        authoritative source when real bracket orders have been submitted.
        Overwrites any DB or log-parsed values for the same field.
        """
        if not self.trading_client or not self.positions:
            return
        _now = time.time()
        if (_now - self._last_brackets_fetch) < 30:
            return
        self._last_brackets_fetch = _now
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus, OrderType
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)
            orders = self.trading_client.get_orders(filter=req)
        except Exception:
            try:
                orders = self.trading_client.get_orders()
            except Exception:
                return

        stop_by_symbol: Dict[str, float] = {}
        target_by_symbol: Dict[str, float] = {}

        for order in orders:
            sym = getattr(order, "symbol", None)
            if not sym or sym not in self.positions:
                continue
            otype = str(getattr(order, "type", "") or "").lower()
            stop_px = getattr(order, "stop_price", None)
            limit_px = getattr(order, "limit_price", None)

            if otype in ("stop", "stop_limit") and stop_px:
                val = float(stop_px)
                if val > 0:
                    stop_by_symbol[sym] = val
            elif otype == "limit" and limit_px:
                val = float(limit_px)
                if val > 0:
                    target_by_symbol[sym] = val

        for sym in self.positions:
            if sym not in stop_by_symbol and sym not in target_by_symbol:
                continue
            rec = dict(self.position_meta.get(sym, {}))
            if sym in stop_by_symbol:
                rec["stop_loss"] = stop_by_symbol[sym]
                rec["stop_active"] = True
            if sym in target_by_symbol:
                rec["target_price"] = target_by_symbol[sym]
                rec["target_active"] = True
            rec["bracket_attached"] = bool(
                rec.get("stop_loss") and rec.get("target_price")
            )
            self.position_meta[sym] = rec

    def _refresh_position_meta_from_decisions(self):
        """Hydrate strategy/stop/target from trading_performance.db decisions table."""
        if not self.positions:
            self.position_meta = {}
            return

        db_path = "trading_performance.db"
        tickers = list(self.positions.keys())
        meta = dict(self.position_meta)
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            table = "decisions"
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'")
            if not cur.fetchone():
                cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions_old'")
                if cur.fetchone():
                    table = "decisions_old"
                else:
                    self.position_meta = {}
                    return

            for ticker in tickers:
                cur.execute(f"PRAGMA table_info({table})")
                cols = {r[1] for r in cur.fetchall()}
                select_cols = [c for c in ("strategy", "stop_loss", "target_price", "direction", "notes", "order_id") if c in cols]
                if not select_cols:
                    continue

                pos_filter = "AND position_opened = 1" if "position_opened" in cols else ""
                cur.execute(
                    f"SELECT {', '.join(select_cols)} FROM {table} "
                    f"WHERE ticker = ? {pos_filter} ORDER BY id DESC LIMIT 1",
                    (ticker.upper(),),
                )
                row = cur.fetchone()
                if not row:
                    cur.execute(
                        f"SELECT {', '.join(select_cols)} FROM {table} "
                        f"WHERE ticker = ? ORDER BY id DESC LIMIT 1",
                        (ticker.upper(),),
                    )
                    row = cur.fetchone()
                if not row:
                    continue

                record = dict(zip(select_cols, row))
                stop_loss = record.get("stop_loss")
                target_price = record.get("target_price")
                has_stop = stop_loss is not None and self._safe_float(stop_loss) > 0
                has_target = target_price is not None and self._safe_float(target_price) > 0
                meta[ticker] = {
                    **meta.get(ticker, {}),
                    "strategy": record.get("strategy") or meta.get(ticker, {}).get("strategy"),
                    "stop_loss": self._safe_float(stop_loss) if has_stop else meta.get(ticker, {}).get("stop_loss"),
                    "target_price": self._safe_float(target_price) if has_target else meta.get(ticker, {}).get("target_price"),
                    "direction": record.get("direction") or meta.get(ticker, {}).get("direction"),
                    "order_id": record.get("order_id") or meta.get(ticker, {}).get("order_id"),
                    "bracket_attached": bool(
                        (has_stop and has_target) or meta.get(ticker, {}).get("bracket_attached")
                    ),
                    "stop_active": bool(has_stop or meta.get(ticker, {}).get("stop_active")),
                    "target_active": bool(has_target or meta.get(ticker, {}).get("target_active")),
                    "exit_mode": meta.get(ticker, {}).get("exit_mode", "managed"),
                    "notes": record.get("notes") or meta.get(ticker, {}).get("notes", ""),
                }
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

        self.position_meta = meta

        # Manual overrides win over DB
        overrides = self._load_position_overrides()
        for ticker, ov in overrides.items():
            if ticker not in self.positions:
                continue
            rec = dict(self.position_meta.get(ticker, {}))
            if ov.get("strategy"):
                rec["strategy"] = str(ov["strategy"]).upper()
            if ov.get("stop_loss"):
                sl = self._safe_float(ov["stop_loss"])
                if sl > 0:
                    rec["stop_loss"] = sl
                    rec["stop_active"] = True
            if ov.get("target_price"):
                tp = self._safe_float(ov["target_price"])
                if tp > 0:
                    rec["target_price"] = tp
                    rec["target_active"] = True
            if "direction" in ov:
                rec["direction"] = str(ov["direction"]).upper()
            if "exit_mode" in ov:
                rec["exit_mode"] = str(ov["exit_mode"])
            rec["bracket_attached"] = bool(rec.get("stop_loss") and rec.get("target_price"))
            self.position_meta[ticker] = rec

        # Live Alpaca bracket orders are highest-priority source (overwrite DB/log values)
        self._fetch_broker_brackets()
        # Weighted scoring metadata from JSONL journal
        self._refresh_position_meta_from_trade_journal()
        # Journal: pull recent decisions from DB (throttled to 15s)
        self._refresh_journal_from_db()

    def _refresh_position_meta_from_trade_journal(self):
        """Hydrate weighted scoring metadata from trade_journal.jsonl when present."""
        path = "trade_journal.jsonl"
        if not os.path.exists(path):
            return

        latest_by_ticker: Dict[str, dict] = {}
        try:
            with open(path, "r") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except Exception:
                        continue
                    ticker = str(entry.get("ticker") or "").upper()
                    if not ticker or ticker not in self.positions:
                        continue
                    latest_by_ticker[ticker] = entry
        except Exception:
            return

        for ticker, entry in latest_by_ticker.items():
            rec = dict(self.position_meta.get(ticker, {}))
            strategy = str(entry.get("strategy") or rec.get("strategy") or "").upper()
            confidence_summary = entry.get("confidence_summary", {}) or {}
            tier_breakdown = entry.get("tier_breakdown", {}) or rec.get("tier_breakdown", {})
            pathway_qualification = entry.get("pathway_qualification", {}) or rec.get("pathway_qualification", {})

            if strategy:
                rec["strategy"] = strategy
            if entry.get("score") is not None:
                rec["score"] = self._safe_float(entry.get("score"))
            if entry.get("grade"):
                rec["grade"] = str(entry.get("grade"))
            rec["growth_mode"] = bool(entry.get("growth_mode", rec.get("growth_mode", False)))
            rec["confidence_summary"] = confidence_summary
            rec["tier_breakdown"] = tier_breakdown
            rec["pathway_qualification"] = pathway_qualification
            rec["primary_pathway"] = entry.get("primary_pathway") or pathway_qualification.get("primary_pathway")
            rec["pathways_used"] = entry.get("pathways_used") or pathway_qualification.get("pathways_passed", [])
            rec["position_pct"] = self._safe_float(entry.get("position_pct")) or rec.get("position_pct")
            rec["risk_pct"] = self._safe_float(entry.get("risk_pct")) or rec.get("risk_pct")

            # Surface the new fundamental category changes in a compact label.
            if strategy == "VOYAGER":
                rec["score_category"] = "GROWTH"
            elif strategy == "SHORT":
                rec["score_category"] = "DETERIORATION"
            elif strategy == "SNIPER":
                rec["score_category"] = "TACTICAL"
            elif strategy == "REMORA":
                rec["score_category"] = "CATALYST"

            self.position_meta[ticker] = rec

    def _load_realized_trade_analytics(self, days: int = 30) -> Dict[str, object]:
        stats: Dict[str, object] = {
            "total": 0,
            "wins": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_return_pct": 0.0,
            "strategy": {},
            "pathway": {},
            "score_bucket": {},
        }
        db_path = "trading_performance.db"
        if not os.path.exists(db_path):
            return stats

        cutoff = datetime.now() - timedelta(days=days)
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT ticker, exit_date, pnl, pnl_percent, setup_type, confluence_score, notes "
                "FROM trades "
                "WHERE exit_date IS NOT NULL "
                "ORDER BY exit_date DESC LIMIT 500"
            )
            rows = cur.fetchall()
            for ticker, exit_date, pnl, pnl_percent, setup_type, confluence_score, notes in rows:
                try:
                    closed_at = datetime.fromisoformat(str(exit_date))
                except Exception:
                    continue
                if closed_at < cutoff:
                    continue
                pnl_val = self._safe_float(pnl)
                ret_val = self._safe_float(pnl_percent)
                score_val = self._safe_float(confluence_score)
                strategy = self._strategy_family_name(setup_type or "UNK")
                note_text = str(notes or "")
                pathway = "Unknown"
                for part in note_text.split("|"):
                    if part.startswith("pathway="):
                        pathway = part.split("=", 1)[1] or "Unknown"
                        break

                if score_val >= 80:
                    bucket = "80+"
                elif score_val >= 70:
                    bucket = "70s"
                elif score_val >= 60:
                    bucket = "60s"
                else:
                    bucket = "<60"

                stats["total"] += 1
                stats["total_pnl"] += pnl_val
                stats["avg_return_pct"] += ret_val
                if pnl_val > 0:
                    stats["wins"] += 1

                for group_name, key in (("strategy", strategy), ("pathway", pathway), ("score_bucket", bucket)):
                    group = stats[group_name]
                    group.setdefault(key, {"count": 0, "wins": 0, "pnl": 0.0})
                    group[key]["count"] += 1
                    group[key]["pnl"] += pnl_val
                    if pnl_val > 0:
                        group[key]["wins"] += 1

            if stats["total"]:
                stats["win_rate"] = stats["wins"] / stats["total"] * 100.0
                stats["avg_return_pct"] = stats["avg_return_pct"] / stats["total"]
        except Exception:
            return stats
        finally:
            if conn:
                conn.close()
        return stats


# ══════════════════════════════════════════════════════════════════════════════
# RENDERING LAYER
# ══════════════════════════════════════════════════════════════════════════════

class V3DashboardPro(V3InstitutionalDashboard):
    """
    Operator-grade terminal.
    Inherits all data plumbing from V3InstitutionalDashboard.
    This class: rendering only.
    """

    # ── render helpers ────────────────────────────────────────────────────────

    def _fmt(self, v, fmt: str = ".2f", fallback: str = "—") -> str:
        try:
            return format(float(v), fmt)
        except Exception:
            return fallback

    def _pathway_short(self, pathway: Optional[str]) -> str:
        pathway = str(pathway or "").upper()
        if not pathway:
            return "—"
        return {
            "GROWTH": "GRO",
            "GROWTH_INFLECTION": "INF",
            "OPERATING_LEVERAGE": "LEV",
            "QUALITY": "QLT",
            "REVENUE": "REV",
            "MARGIN": "MAR",
            "GUIDANCE": "GUI",
            "STRESS": "STR",
        }.get(pathway, pathway[:3])

    def _load_position_metadata(self, ticker: str) -> Dict:
        ticker = str(ticker or "").upper()
        if not ticker:
            return {}
        if isinstance(self.position_meta, dict):
            return self.position_meta.get(ticker, {}) or {}
        return {}

    def _format_pathway_summary(self, positions: List[Dict]) -> str:
        """
        Show pathway distribution only when metadata exists
        Hides section for legacy positions without pathway data
        """

        if not positions:
            return ""

        has_pathway_data = False
        pathway_counts = {}
        pathway_pnl = {}

        for pos in positions:
            symbol = pos.get("symbol") or pos.get("ticker")
            meta = self._load_position_metadata(symbol)

            if meta and 'pathway_qualification' in meta:
                has_pathway_data = True
                pathway = meta['pathway_qualification'].get('primary_pathway', 'Unknown')

                pathway_counts[pathway] = pathway_counts.get(pathway, 0) + 1

                pnl = pos.get('unrealized_pl', 0)
                pathway_pnl[pathway] = pathway_pnl.get(pathway, 0) + pnl

        if not has_pathway_data:
            return ""

        pathway_parts = []

        for pathway in sorted(pathway_counts.keys()):
            count = pathway_counts[pathway]
            pnl = pathway_pnl[pathway]
            pnl_color = Fore.GREEN if pnl >= 0 else Fore.RED

            pathway_label = {
                'GROWTH': 'Growth',
                'GROWTH_INFLECTION': 'Inflection',
                'OPERATING_LEVERAGE': 'Leverage',
                'QUALITY': 'Quality',
                'REVENUE': 'Revenue Det.',
                'MARGIN': 'Margin Comp.',
                'GUIDANCE': 'Guidance Cuts',
                'STRESS': 'Financial Stress'
            }.get(pathway, pathway)

            pathway_parts.append(
                f"{pathway_label} {count} {pnl_color}${pnl:,.0f}{Style.RESET_ALL}"
            )

        if pathway_parts:
            return "Pathways: " + "  ".join(pathway_parts)
        else:
            return ""

    def _pathway_summary_text(self, positions: Dict[str, Dict]) -> Text:
        rows = []
        for ticker, pos in positions.items():
            row = dict(pos)
            row["symbol"] = ticker
            rows.append(row)
        summary = self._format_pathway_summary(rows)
        return Text.from_ansi(summary) if summary else Text()

    def _dir_normalize(self, raw) -> Optional[str]:
        if not raw:
            return None
        v = str(raw).strip().upper()
        if v in ("BUY", "LONG"):
            return "LONG"
        if v in ("SELL", "SHORT"):
            return "SHORT"
        if v in ("—", "-", "NONE", "NULL", "N/A", ""):
            return None
        return v[:5]

    def _prot_label(
        self, bracket: bool, has_stop: bool, has_tgt: bool, no_meta: bool = False
    ) -> Tuple[str, str]:
        """
        BRKT = broker bracket confirmed      (bright_green)
        S+T  = stop + target in-code         (green)
        STOP = stop only                     (yellow)
        TGT  = target only                   (yellow)
        BARE = no protection                 (bold red)
        UNKN = no metadata available         (dim)
        """
        if no_meta:
            return "UNKN", "dim"
        if bracket:
            return "BRKT", "bright_green"
        if has_stop and has_tgt:
            return "S+T", "green"
        if has_stop:
            return "STOP", "yellow"
        if has_tgt:
            return "TGT", "yellow"
        return "BARE", "bold red"

    _STRAT_COLORS = {
        "VOY": "blue",        # Growth Leader
        "SNP": "magenta",     # Breakout
        "REM": "cyan",        # Catalyst Trade
        "SHRT": "red",        # Short Trade
        "RPR": "yellow",      # Reaper (Contrarian)
    }

    def _strat_text(self, raw) -> Text:
        code = self._normalize_strategy(raw)
        internal = {
            "VOY": "VOYAGER",
            "SNP": "SNIPER",
            "REM": "REMORA",
            "SHRT": "SHORT",
            "RPR": "CONTRARIAN",
        }.get(code, code)
        return Text(get_strategy_short_name(internal), style=self._STRAT_COLORS.get(code, "dim"))

    def _denial_key(self, reason: str) -> str:
        _KEYS = [
            "SPREAD_TOO_WIDE", "PORTFOLIO_HEAT", "SECTOR_LIMIT", "SECTOR_CONCENTRATION",
            "NO_ACCEPTANCE", "SHOCK_MISALIGNED", "MACRO_GATE", "MACRO_BLOCK",
            "EXECUTION_DENIED", "EXECUTION DENIED", "ENTRY_DENIED", "ENTRY DENIED",
            "PORTFOLIO_DENIED", "VETO", "MAX_POSITIONS", "DAILY_LOSS", "DAILY_LIMIT",
            "REGIME_FILTER", "RISK_LIMIT", "POSITION_SIZE", "DRAWDOWN",
            "ATR_TOO_LOW", "VOLUME_TOO_LOW", "SETUP_INVALID", "PRICE_TOO_FAR",
            "STOP_DISTANCE", "NO_SETUP", "TIMEOUT", "INSUFFICIENT",
        ]
        upper = reason.upper()
        for k in _KEYS:
            if k in upper:
                return k.replace(" ", "_")
        parts = [t for t in reason.split() if len(t) > 3 and not t.startswith("[")]
        return parts[-1][:28] if parts else "DENIED"

    def _market_status(self) -> Tuple[bool, str]:
        """Returns (is_open, label). Labels: OPEN / PRE-MKT / AFTER-HRS / CLOSED / WEEKEND"""
        try:
            try:
                import pytz
                et = pytz.timezone("America/New_York")
                now_et = datetime.now(et)
            except ImportError:
                from datetime import timezone
                now_et = datetime.now(timezone(timedelta(hours=-5)))
            weekday = now_et.weekday()
            h, m = now_et.hour, now_et.minute
            if weekday >= 5:
                return False, "WEEKEND"
            if (h, m) >= (9, 30) and (h, m) < (16, 0):
                return True, "OPEN"
            if (h, m) >= (4, 0) and (h, m) < (9, 30):
                return False, "PRE-MKT"
            if (h, m) >= (16, 0) and (h, m) < (20, 0):
                return False, "AFTER-HRS"
            return False, "CLOSED"
        except Exception:
            return False, "UNK"

    # ── panels ────────────────────────────────────────────────────────────────

    def _panel_header(self) -> Panel:
        now = datetime.now()
        grid = Table.grid(expand=True)
        grid.add_column(ratio=3)
        grid.add_column(ratio=2, justify="right")

        left = Text()
        left.append("V3 INSTITUTIONAL", style="bold white")
        left.append(" PLATFORM", style="bold cyan")
        if self.confluence_data.get("active") and self.confluence_data.get("count", 0) > 0:
            left.append(
                f"  [{self.confluence_data['count']} CONFLUENCE]",
                style="bold yellow on red",
            )

        right = Text()
        right.append(now.strftime("%A, %B %d, %Y"), style="white")
        right.append(" | ", style="dim")
        right.append(now.strftime("%I:%M:%S %p ET"), style="bold cyan")

        grid.add_row(left, right)
        self._refresh_trend_snapshot()
        self._refresh_policy_snapshot()
        trend = compact_trend_labels(
            self._trend_snapshot,
            current_vix=self.market_state.get("vix_level"),
            current_breadth=self._policy_snapshot.get("voyager_breadth_pct"),
            current_equity=(self.account_info or {}).get("equity"),
        )
        trend_row = Text()
        trend_row.append("TAPE ", style="bold")
        trend_row.append("VIX ", style="bold")
        trend_row.append(trend["vix"], style="white")
        trend_row.append("   BR ", style="bold")
        trend_row.append(trend["breadth"], style="cyan")
        trend_row.append("   EQ ", style="bold")
        trend_row.append(trend["equity"], style="green")
        grid.add_row(trend_row, Text(""))
        return Panel(grid, border_style="white", padding=(0, 1))

    def _panel_regime_strip(self) -> Panel:
        """
        The one line a trader reads first.
        Market status + strategy VIX gates + regime + scan freshness.
        """
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)

        vix_level = self.market_state.get("vix_level", "—")
        vix_regime = self.market_state.get("vix_regime", "—")
        mkt_open, mkt_label = self._market_status()
        mkt_style = (
            "bold green" if mkt_open
            else "yellow" if mkt_label in ("PRE-MKT", "AFTER-HRS")
            else "bold red"
        )
        daemon_status, daemon_style = self._daemon_status()
        pos_count = len(self.positions)
        opp_count = len([o for o in list(self.opportunities)[:10] if o.get("ticker")])
        total_pnl = sum(self._safe_float(p.get("unrealized_pl")) for p in self.positions.values()) if self.positions else 0.0
        pnl_style = "green" if total_pnl >= 0 else "red"
        vix_color = (
            "green" if "CALM" in str(vix_regime).upper() or "NORMAL" in str(vix_regime).upper()
            else "red" if "EXTREME" in str(vix_regime).upper()
            else "cyan"
        )

        summary = Text()
        summary.append("MKT: ", style="bold")
        summary.append(mkt_label, style=mkt_style)
        summary.append("   SYS: ", style="bold")
        summary.append(daemon_status, style=daemon_style)
        summary.append("   POS: ", style="bold")
        summary.append(str(pos_count), style="white")
        summary.append("   P/L: ", style="bold")
        summary.append(f"${total_pnl:+,.0f}", style=pnl_style)
        summary.append("   OPP: ", style="bold")
        summary.append(str(opp_count), style="white")
        self._refresh_risk_gate_snapshot()
        port_mode = str(self._risk_gate_snapshot.get("portfolio_mode") or "UNKNOWN")
        port_style = (
            "bold red" if port_mode in ("DEFENSIVE", "CONSERVATIVE")
            else "bold yellow" if port_mode == "FEAR_OPPORTUNITY"
            else "green" if port_mode == "AGGRESSIVE"
            else "cyan"
        )
        summary.append("   PORT: ", style="bold")
        summary.append(port_mode, style=port_style)
        # Reaper (Contrarian) gate status on strip
        ctr_status = self.strategy_status.get("contrarian", "LOADING")
        ctr_style = (
            "bold green" if ctr_status == "HUNTING"
            else "bold yellow" if ctr_status == "ARMED"
            else "dim"
        )
        summary.append("   REAPER: ", style="bold")
        summary.append(ctr_status, style=ctr_style)
        summary.append("   REGIME: ", style="bold")
        summary.append(str(vix_regime), style=f"bold {vix_color}")
        summary.append("   VIX: ", style="bold")
        summary.append(str(vix_level), style="white")
        short_label, short_style = self._short_policy_label()
        breadth_label, breadth_style = self._voyager_breadth_label()
        forecast_label, forecast_style = self._forecast_policy_label()
        summary.append("   SHORT: ", style="bold")
        summary.append(short_label, style=short_style)
        summary.append("   VOY BR: ", style="bold")
        summary.append(breadth_label, style=breadth_style)
        summary.append("   FCST: ", style="bold")
        summary.append(forecast_label, style=forecast_style)

        # Edge mix + readiness badge + hot strat (Phase 3)
        edge = self._edge_summary()
        if edge:
            mix = edge.get("edge_mix", {})
            a = (mix.get("ALIGNMENT", {}) or {}).get("open", 0)
            i = (mix.get("INEFFICIENCY", {}) or {}).get("open", 0)
            c = (mix.get("CONTRARIAN", {}) or {}).get("open", 0)
            closed_n   = edge.get("closed_n", 0)
            data_ready = edge.get("data_ready", False)
            badge_text  = f"live (n={closed_n})" if data_ready else f"warming (n={closed_n})"
            badge_style = "bold green"             if data_ready else "dim yellow"
            summary.append("   EDGE: ", style="bold")
            summary.append(badge_text, style=badge_style)
            summary.append(f" A{a}/I{i}/C{c}", style="cyan")
            best = edge.get("best_strategy")
            if data_ready and best:
                summary.append("   HOT: ", style="bold")
                summary.append(get_strategy_display_name(best), style="green")

        self._refresh_trend_snapshot()
        self._refresh_policy_snapshot()
        trend = compact_trend_labels(
            self._trend_snapshot,
            current_vix=self.market_state.get("vix_level"),
            current_breadth=self._policy_snapshot.get("voyager_breadth_pct"),
            current_equity=(self.account_info or {}).get("equity"),
        )
        trend_line = Text()
        trend_line.append("TREND ", style="bold")
        trend_line.append("VIX ", style="bold")
        trend_line.append(trend["vix"], style="white")
        trend_line.append("   BR ", style="bold")
        trend_line.append(trend["breadth"], style="cyan")
        trend_line.append("   EQ ", style="bold")
        trend_line.append(trend["equity"], style="green")

        grid.add_row(summary)
        grid.add_row(trend_line)
        return Panel(
            grid,
            title="[bold white]STRATEGY / REGIME",
            border_style="cyan",
            padding=(0, 1),
        )

    def _panel_live_positions(self) -> Panel:
        """Full position context: strategy, direction, P/L, stop, target, protection."""
        # Broker refresh — throttled: at most every 10s to avoid hammering Alpaca
        _now = time.time()
        if self.trading_client and (_now - self._last_positions_fetch) >= 10:
            try:
                raw = self.trading_client.get_all_positions()
                self._last_positions_fetch = _now
                self.positions = {}
                for p in raw:
                    self.positions[p.symbol] = normalize_position(
                        p,
                        source="ALPACA_API",
                        trading_client=self.trading_client,
                    )
            except Exception:
                self.positions = {}
                self._last_positions_fetch = _now

        self._refresh_position_meta_from_decisions()

        # Keep websocket subscriptions aligned with open positions + opportunities + core.
        # Position changes apply immediately; opp-list-only changes debounced to 30s
        # to prevent subscription churn on each 2s render tick.
        opp_tickers = [
            o.get("ticker", "")
            for o in list(self.opportunities)[:10]
            if o.get("ticker") and o.get("ticker") not in ("—", "")
        ]
        pos_tickers = frozenset(self.positions.keys())
        all_tickers = pos_tickers | frozenset(opp_tickers) | frozenset(self._ws_core)
        if all_tickers != self._ws_subscribed:
            _ws_now = time.time()
            pos_changed = pos_tickers != self._ws_pos_tickers
            elapsed = _ws_now - self._last_ws_subscription_ts
            if pos_changed or elapsed >= 30:
                self._ws_pos_tickers = pos_tickers
                self._ws_subscribed = all_tickers
                self._last_ws_subscription_ts = _ws_now
                LiveFeed.update_subscriptions(sorted(all_tickers))

        table = Table(
            show_header=True, box=rich_box.SIMPLE_HEAD, padding=(0, 1), expand=True,
        )
        table.add_column("Ticker", width=6, style="bold")
        table.add_column("Strat", width=6)
        table.add_column("Dir", width=5)
        table.add_column("Sh", justify="right", width=5)
        table.add_column("Entry", justify="right", width=7)
        table.add_column("Now", justify="right", width=8)
        table.add_column("P/L$", justify="right", width=9)
        table.add_column("P/L%", justify="right", width=7)
        table.add_column("Stop", justify="right", width=7)
        table.add_column("Tgt", justify="right", width=8)
        table.add_column("Prot", width=5)

        subtitle: Optional[str] = None
        if self.positions:
            total_mv = sum(abs(self._safe_float(p.get("market_value"))) for p in self.positions.values())
            total_pnl = sum(self._safe_float(p.get("unrealized_pl")) for p in self.positions.values())
            if total_mv > 0:
                top = max(
                    self.positions.items(),
                    key=lambda kv: abs(self._safe_float(kv[1].get("market_value"))),
                )
                top_pct = abs(self._safe_float(top[1].get("market_value"))) / total_mv * 100
                open_risk = sum(
                    abs((self.position_meta.get(t, {}).get("stop_loss") or 0.0) - (p.get("entry_price") or 0.0))
                    * abs(self._safe_float(p.get("shares") or p.get("qty") or 0))
                    for t, p in self.positions.items()
                    if (self.position_meta.get(t, {}).get("stop_loss") or 0.0) > 0
                )
                risk_str = f"  |  Risk: ${open_risk:,.0f}" if open_risk > 0 else ""
                subtitle = f"Total P/L: ${total_pnl:+,.2f}{risk_str}  |  Top: {top[0]} {top_pct:.1f}%"

        if not self.positions:
            table.add_row("—", "—", "—", "—", "—", "—", "—", "—", "—", "—", "—")
        else:
            for ticker, pos in sorted(
                self.positions.items(), key=lambda x: x[1].get("unrealized_pl") or 0.0, reverse=True
            )[:8]:
                meta = self.position_meta.get(ticker, {})
                norm = normalize_position(
                    pos,
                    meta=meta,
                    source="alpaca",
                    trading_client=self.trading_client,
                )

                strat_txt = self._strat_text(norm["strategy"])

                direction = norm["direction"]
                if direction == "LONG":
                    dir_txt = Text("LONG", style="green")
                elif direction == "SHORT":
                    dir_txt = Text("SHORT", style="red")
                elif direction:
                    dir_txt = Text(direction, style="dim")
                else:
                    dir_txt = Text("—", style="dim")

                stop_f = norm["stop_loss"] or 0.0
                tgt_f = norm["target_price"] or 0.0
                has_stop = stop_f > 0
                has_tgt = tgt_f > 0
                bracket = bool(meta.get("bracket_attached")) or norm["protection_status"] == "BRKT"
                prot_label, prot_style = self._prot_label(
                    bracket, has_stop, has_tgt,
                    no_meta=(norm["protection_status"] == "UNKN"),
                )

                # Overlay websocket live price when available (fresher than 15s REST data)
                live_q = LiveFeed.get_live_quote(ticker)
                ws_price = None
                if live_q:
                    ws_price = live_q.get("mid") or live_q.get("last")
                    if ws_price and ws_price > 0:
                        norm["current_price"] = ws_price
                        # Recalculate P&L with websocket price
                        entry = norm["entry_price"]
                        shares = norm["shares"]
                        if entry and entry > 0 and shares:
                            direction_sign = -1 if norm["direction"] == "SHORT" else 1
                            norm["unrealized_pl"] = direction_sign * (ws_price - entry) * shares
                            norm["unrealized_pl_pct"] = (
                                direction_sign * (ws_price - entry) / entry * 100
                            )

                pnl = norm["unrealized_pl"] or 0.0
                pnl_pct = norm["unrealized_pl_pct"] or 0.0
                pnl_style = "green" if pnl >= 0 else "red"

                # "Now" column: show live price with WS indicator, or REST price plain
                now_price_str = self._fmt(norm["current_price"])
                now_txt = (
                    Text(f"●{now_price_str}", style="bold cyan")   # ● = websocket live
                    if ws_price else Text(now_price_str, style="white")
                )

                # Stop / target / protection display
                stop_txt = Text(f"${stop_f:.2f}", style="yellow") if has_stop else Text("—", style="dim")
                tgt_txt = Text(f"${tgt_f:.2f}", style="cyan") if has_tgt else Text("—", style="dim")

                table.add_row(
                    ticker,
                    strat_txt,
                    dir_txt,
                    str(int(norm["shares"] or 0)),
                    self._fmt(norm["entry_price"]),
                    now_txt,
                    Text(f"{pnl:+.2f}", style=pnl_style),
                    Text(f"{pnl_pct:+.1f}%", style=pnl_style),
                    stop_txt,
                    tgt_txt,
                    Text(prot_label, style=prot_style),
                )

        return Panel(
            Group(table, self._pathway_summary_text(self.positions)),
            title="[bold white]LIVE POSITIONS",
            subtitle=subtitle,
            border_style="green",
            padding=(0, 1),
        )

    def _panel_opportunities_decisions(self) -> Panel:
        """
        Action board:
        - READY: executable/approved names
        - WATCH: live candidates that need confirmation
        - BLOCKED: real denied decisions from the DB
        """
        payload = self._refresh_action_snapshot()
        ready_rows = payload.get("ready", [])
        watch_rows = payload.get("watch", [])
        blocked_rows = payload.get("blocked", [])

        def _build_bucket(title: str, rows: List[Dict[str, object]], *, empty: str, style: str) -> Table:
            table = Table(show_header=True, box=None, padding=(0, 1), expand=True)
            table.add_column(title, width=6, style="bold")
            table.add_column("Str", width=4)
            table.add_column("Tk", width=6, style="bold")
            table.add_column("S", width=4, justify="right")
            table.add_column("RR", width=4, justify="right")
            table.add_column("Detail")
            if not rows:
                table.add_row("", "", "—", "—", "—", Text(empty, style="dim"))
                return table
            for row in rows:
                strat = get_strategy_short_name(row.get("strategy", "")) or "—"
                score = row.get("score")
                rr = row.get("rr")
                detail = str(row.get("detail") or "—")
                table.add_row(
                    Text("●", style=style),
                    strat,
                    str(row.get("ticker") or "—"),
                    f"{float(score):.0f}" if score not in (None, "") and float(score) > 0 else "—",
                    f"{float(rr):.1f}" if rr not in (None, "") and float(rr) > 0 else "—",
                    detail[:28],
                )
            return table

        def _build_blocked(rows: List[Dict[str, object]]) -> Table:
            table = Table(show_header=True, box=None, padding=(0, 1), expand=True)
            table.add_column("Blk", width=6, style="bold")
            table.add_column("Str", width=4)
            table.add_column("Tk", width=6, style="bold")
            table.add_column("S", width=4, justify="right")
            table.add_column("RR", width=4, justify="right")
            table.add_column("Why")
            if not rows:
                table.add_row("", "", "—", "—", "—", Text("No live blocks", style="dim"))
                return table
            for row in rows:
                table.add_row(
                    Text("●", style="bold red"),
                    get_strategy_short_name(row.get("strategy", "")) or "—",
                    str(row.get("ticker") or "—"),
                    f"{float(row.get('score') or 0):.0f}" if float(row.get("score") or 0) > 0 else "—",
                    f"{float(row.get('rr') or 0):.1f}" if float(row.get("rr") or 0) > 0 else "—",
                    compact_reason(row.get("reason"), max_len=28),
                )
            return table

        delta_txt = Text()
        if self._action_delta_rows:
            for idx, row in enumerate(self._action_delta_rows):
                if idx:
                    delta_txt.append("  ")
                kind = row.get("kind", "—")
                style = {
                    "NEW": "bold green",
                    "UP": "green",
                    "DOWN": "yellow",
                    "DROP": "dim red",
                }.get(kind, "dim")
                delta_txt.append(f"{kind} ", style=style)
                delta_txt.append(str(row.get("label") or "—"), style="white")
                detail = str(row.get("detail") or "")
                if detail:
                    delta_txt.append(f" {detail}", style="dim")
        else:
            delta_txt.append("No material change since last scan", style="dim")

        self._refresh_risk_gate_snapshot()
        gate_counts = self._risk_gate_snapshot.get("gate_counts", {}) or {}
        if gate_counts:
            order = ["Risk", "Portfolio", "Concentration", "Execution", "Other"]
            subtitle = "Blocks 24h: " + "  ".join(
                f"{name}:{gate_counts[name]}" for name in order if gate_counts.get(name)
            )
        else:
            subtitle = "Blocks 24h: none"

        return Panel(
            Group(
                _build_bucket("Ready", ready_rows, empty="No executable names", style="bold green"),
                _build_bucket("Watch", watch_rows, empty="No live watch names", style="bold yellow"),
                _build_blocked(blocked_rows),
                Text.assemble(("Δ ", "bold"), delta_txt),
            ),
            title="[bold white]ACTION BOARD",
            subtitle=subtitle,
            border_style="yellow",
            padding=(0, 1),
        )

    def _panel_macro_risk(self) -> Panel:
        """Macro event window state: CLEAR / CAUTION WINDOW / EVENT LOCKOUT / POST-EVENT STABILIZING."""
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("K", width=14, style="bold")
        table.add_column("V")

        try:
            state = self.macro_cal.get_macro_window_state() if self.macro_cal else None
        except Exception:
            state = None

        if state:
            window = state.get('window', 'CLEAR')
            event_name = str(state.get('event_name', '') or '—')
            if len(event_name) > 22:
                event_name = event_name[:19] + "..."
            impact = str(state.get('impact', '') or '—').upper()
            display_label = state.get('display_label', 'CLEAR')
            minutes_to = state.get('minutes_to_event')

            if window == 'EVENT_LOCKOUT':
                gate_style = "bold red"
                sev_style = "bold red" if impact == "CRITICAL" else "bold yellow"
                gate_note = Text("Sniper/Remora: DENIED  |  Voyager/Short: caution", style="red")
            elif window == 'PRE_EVENT':
                gate_style = "bold yellow"
                sev_style = "bold yellow"
                gate_note = Text("All strategies: caution (event imminent)", style="yellow")
            elif window == 'POST_EVENT':
                gate_style = "yellow"
                sev_style = "yellow"
                gate_note = Text("Sniper/Remora: caution  |  Voyager/Short: normalizing", style="yellow")
            else:
                # CLEAR — show next upcoming event as context
                gate_style = "green"
                sev_style = "green" if impact not in ("CRITICAL", "HIGH") else "dim"
                gate_note = Text("All entries permitted", style="green")

            if minutes_to is not None:
                if minutes_to > 0:
                    h, m = divmod(int(minutes_to), 60)
                    countdown = f"{h}h {m}m" if h else f"{m}m"
                    time_label = f"in {countdown}"
                else:
                    m_ago = abs(minutes_to)
                    time_label = f"{m_ago}m ago"
            else:
                time_label = "—"

            table.add_row("Event", event_name)
            table.add_row("When", time_label)
            table.add_row("Severity", Text(impact, style=sev_style))
            table.add_row("Window", Text(display_label, style=gate_style))
            table.add_row("Action", gate_note)
        else:
            table.add_row("Event", "None scheduled")
            table.add_row("When", "—")
            table.add_row("Severity", Text("—", style="green"))
            table.add_row("Window", Text("CLEAR", style="green"))
            table.add_row("Action", Text("All entries permitted", style="green"))

        return Panel(table, title="[bold white]MACRO / EVENT RISK", border_style="magenta", padding=(0, 1))

    def _panel_portfolio_risk(self) -> Panel:
        """Compact risk summary with by-strategy breakdown."""
        self._refresh_risk_gate_snapshot()
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("K", width=18, style="bold")
        table.add_column("V")

        n = len(self.positions)
        total_pnl = sum(self._safe_float(p.get("pnl")) for p in self.positions.values()) if self.positions else 0.0
        total_mv = sum(abs(self._safe_float(p.get("market_value"))) for p in self.positions.values()) if self.positions else 0.0

        top_name, top_weight = "—", "—"
        if self.positions and total_mv > 0:
            top = max(
                self.positions.items(),
                key=lambda kv: abs(self._safe_float(kv[1].get("market_value"))),
            )
            top_name = top[0]
            top_weight = f"{abs(self._safe_float(top[1].get('market_value'))) / total_mv * 100:.1f}%"

        pnl_style = "green" if total_pnl >= 0 else "red"

        open_risk = 0.0
        for ticker, pos in self.positions.items():
            meta = self.position_meta.get(ticker, {})
            norm = normalize_position(pos, meta=meta, source="alpaca")
            entry = norm["entry_price"] or 0.0
            stop = norm["stop_loss"] or 0.0
            qty = norm["shares"] or 0.0
            if entry > 0 and stop > 0 and qty > 0:
                open_risk += abs(entry - stop) * qty
        risk_str = f"${open_risk:,.2f}" if open_risk > 0 else "— (no stops set)"
        risk_style = "yellow" if open_risk > 0 else "dim"

        sector_counts: dict = {}
        for ticker, pos in self.positions.items():
            meta = self.position_meta.get(ticker, {})
            sector = pos.get("sector") or meta.get("sector") or ""
            if sector and sector.upper() not in ("", "UNKNOWN", "N/A"):
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
        sector_str = f"{max(sector_counts, key=lambda k: sector_counts[k])} ({sector_counts[max(sector_counts, key=lambda k: sector_counts[k])]}/{n})" if sector_counts else "—"

        strat_counts: dict = {}
        strat_mv: dict = {}
        for ticker, pos in self.positions.items():
            meta = self.position_meta.get(ticker, {})
            norm = normalize_position(pos, meta=meta, source="alpaca")
            code = self._normalize_strategy(norm["strategy"])
            strat_counts[code] = strat_counts.get(code, 0) + 1
            strat_mv[code] = strat_mv.get(code, 0.0) + abs(norm["market_value"] or 0.0)

        table.add_row("Open Positions", str(n))
        table.add_row("Unrealized P/L", Text(f"${total_pnl:+,.2f}", style=pnl_style))
        table.add_row("Open Risk ($)", Text(risk_str, style=risk_style))
        table.add_row("Top Position", f"{top_name} ({top_weight})")
        table.add_row("Top Sector", sector_str)

        equity = self._safe_float((self.account_info or {}).get("equity"))
        gross_pct = (total_mv / equity * 100.0) if equity > 0 else 0.0
        risk_pct = (open_risk / equity * 100.0) if equity > 0 else 0.0
        self._refresh_policy_snapshot()
        breadth_pct = self._policy_snapshot.get("voyager_breadth_pct")
        breadth_meter = percent_meter(breadth_pct, width=10) if breadth_pct is not None else "░" * 10
        table.add_row("Gross Util", f"{meter_bar(gross_pct, 100.0, width=10)} {gross_pct:.1f}%")
        table.add_row("Stop Risk", f"{meter_bar(risk_pct, 2.0, width=10)} {risk_pct:.2f}%")
        table.add_row(
            "Breadth",
            f"{breadth_meter} {float(breadth_pct):.1f}%" if breadth_pct is not None else "░░░░░░░░░░ n/a",
        )

        if strat_counts:
            parts = [f"{k}:{strat_counts[k]} ${strat_mv.get(k, 0):,.0f}" for k in sorted(strat_counts)]
            table.add_row("By Strategy", Text("  ".join(parts), style="dim"))

        port_mode = str(self._risk_gate_snapshot.get("portfolio_mode") or "UNKNOWN")
        mode_style = (
            "bold red" if port_mode in ("DEFENSIVE", "CONSERVATIVE")
            else "bold yellow" if port_mode == "FEAR_OPPORTUNITY"
            else "green" if port_mode == "AGGRESSIVE"
            else "cyan"
        )
        table.add_row("Portfolio Mode", Text(port_mode, style=mode_style))
        circuit = bool(self._risk_gate_snapshot.get("circuit_breaker", False))
        table.add_row("Circuit", Text("TRIGGERED" if circuit else "CLEAR", style="bold red" if circuit else "green"))

        gate_counts = self._risk_gate_snapshot.get("gate_counts", {}) or {}
        if gate_counts:
            order = ["Risk", "Portfolio", "Concentration", "Execution", "Other"]
            gate_text = "  ".join(f"{name}:{gate_counts[name]}" for name in order if gate_counts.get(name))
            table.add_row("Denied 24h", Text(gate_text, style="yellow"))

        strategy_denials = self._risk_gate_snapshot.get("strategy_counts", {}) or {}
        if strategy_denials:
            strat_text = "  ".join(f"{k}:{v}" for k, v in sorted(strategy_denials.items()))
            table.add_row("Denied By Strat", Text(strat_text, style="dim"))

        return Panel(table, title="[bold white]PORTFOLIO RISK", border_style="red", padding=(0, 1))

    def _panel_flow_context(self) -> Panel:
        """Compact situational context: primary ticker, regime, last execution."""
        self._ensure_positions_snapshot()
        self._refresh_journal_from_db()

        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("K", width=12, style="dim")
        table.add_column("V")

        vix_level = self.market_state.get("vix_level", "—")
        vix_regime = self.market_state.get("vix_regime", "—")
        if vix_level in ("—", "", None) or vix_regime in ("—", "", None):
            vix_text = Text("Awaiting market feed", style="yellow")
        else:
            vix_color = (
                "green" if "CALM" in vix_regime or "NORMAL" in vix_regime
                else "red" if "EXTREME" in vix_regime
                else "cyan"
            )
            vix_text = Text(f"{vix_level}  {vix_regime}", style=vix_color)

        sniper_status = self.strategy_status.get("sniper", "LOADING")
        sniper_reason = self.strategy_status.get("sniper_reason", "")
        if sniper_status == "LOADING":
            sniper_gate = Text("Awaiting scan / VIX", style="yellow")
        else:
            sniper_gate = Text(f"{sniper_status}: {sniper_reason[:26]}", style="dim")

        conf = self.confluence_data
        if conf.get("active") and conf.get("count", 0) > 0:
            conf_txt = Text(f"{conf['count']} aligned (signal)", style="bold yellow")
        else:
            overlap = int(conf.get("pre_signal_overlap", 0) or 0)
            conf_txt = Text(f"none (pre {overlap})", style="dim") if overlap > 0 else Text("none", style="dim")

        # Options signals — SPY PCR + gamma (Tradier real-time)
        opt = self._refresh_spy_options()
        if opt:
            pc = (opt.get("put_call") or opt.get("spy_put_call") or {})
            pcr_val = pc.get("ratio")
            gex = (opt.get("gamma_exposure") or {})
            gamma_regime = gex.get("regime", "NEUTRAL")
            pcr_style = (
                "green"  if (pcr_val is not None and pcr_val < 0.80) else
                "red"    if (pcr_val is not None and pcr_val > 1.20) else
                "yellow"
            )
            gamma_style = (
                "green"  if gamma_regime == "POSITIVE" else
                "red"    if gamma_regime == "NEGATIVE" else
                "yellow"
            )
            pcr_str = f"{pcr_val:.2f}" if pcr_val is not None else "—"
            opt_txt = Text(f"PCR {pcr_str}  GEX {gamma_regime}", style=pcr_style)
            gamma_txt = Text(gamma_regime, style=gamma_style)
        else:
            opt_txt = Text("Options loading...", style="dim")
            gamma_txt = Text("—", style="dim")

        table.add_row("Primary", self._primary_ticker())
        table.add_row("VIX / Regime", vix_text)
        table.add_row("SPY Options", opt_txt)
        table.add_row("Sniper Gate", sniper_gate)
        table.add_row("Confluence", conf_txt)
        self._refresh_policy_snapshot()
        policy = self._policy_snapshot or {}
        short_mode = "LIVE" if policy.get("short_live_enabled", True) else "SHADOW ONLY"
        short_style = "green" if short_mode == "LIVE" else "bold yellow"
        breadth_pct = policy.get("voyager_breadth_pct")
        breadth_mode = str(policy.get("voyager_breadth_mode") or "UNKNOWN").replace("_", " ")
        if breadth_pct is None:
            breadth_text = Text("n/a", style="dim")
        else:
            breadth_style = (
                "bold red" if "SUPPRESSED" in breadth_mode
                else "bold yellow" if "HALF" in breadth_mode
                else "green"
            )
            breadth_text = Text(f"{float(breadth_pct):.1f}%  {breadth_mode}", style=breadth_style)
        forecast_counts = policy.get("closed_trade_counts", {}) or {}
        threshold = int(policy.get("forecast_sample_threshold") or 200)
        active = list(policy.get("forecast_active_strategies") or [])
        if active:
            active_txt = ",".join(get_strategy_short_name(s) for s in active)
            forecast_text = Text(
                f"{policy.get('forecast_model_version', 'shadow')}  live for {active_txt}",
                style="green",
            )
        else:
            forecast_text = Text(
                f"{policy.get('forecast_model_version', 'shadow')}  off until n>={threshold} "
                f"(VOY {forecast_counts.get('VOYAGER', 0)}, SHRT {forecast_counts.get('SHORT', 0)})",
                style="yellow",
            )
        table.add_row("Short Exec", Text(short_mode, style=short_style))
        table.add_row("VOY Breadth", breadth_text)
        table.add_row("Forecast", forecast_text)

        action_ts, action_event, action_detail = self._latest_action_context()
        if action_detail:
            table.add_row("Last Action", f"{action_event} {action_detail[:28]}")
            if action_ts:
                table.add_row("Action Time", Text(action_ts, style="dim"))
        else:
            table.add_row("Last Action", Text("—", style="dim"))

        return Panel(table, title="[bold white]FLOW / CONTEXT", border_style="blue", padding=(0, 1))

    def _panel_trade_analytics(self) -> Panel:
        # ── Phase 3: Edge Intelligence panel ──────────────────────────
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column("K", width=16, style="bold")
        tbl.add_column("V")

        if EdgeAnalytics is None:
            tbl.add_row("Status", Text("edge_analytics not available", style="dim"))
            return Panel(tbl, title="[bold white]EDGE INTELLIGENCE", border_style="cyan", padding=(0, 1))

        try:
            ea = EdgeAnalytics()
            summary = ea.get_operator_summary(lookback_days=90)
        except Exception as exc:
            tbl.add_row("Error", Text(str(exc)[:40], style="red"))
            return Panel(tbl, title="[bold white]EDGE INTELLIGENCE", border_style="cyan", padding=(0, 1))

        closed_n     = summary.get("closed_n", 0)
        data_ready   = summary.get("data_ready", False)
        edge_mix     = summary.get("edge_mix", {})
        funnel       = summary.get("funnel", {})
        by_strategy  = summary.get("by_strategy", {})

        # ── Section 1: Edge type mix (open positions — always available)
        _EDGE_STYLE = {"ALIGNMENT": "cyan", "INEFFICIENCY": "yellow", "CONTRARIAN": "magenta"}
        edge_parts = []
        for etype in ("ALIGNMENT", "INEFFICIENCY", "CONTRARIAN"):
            bucket = edge_mix.get(etype, {})
            n_open = bucket.get("open", 0)
            if n_open:
                edge_parts.append(Text(f"{etype[:3]}:{n_open}", style=_EDGE_STYLE.get(etype, "white")))
        if edge_parts:
            edge_row = Text()
            for i, part in enumerate(edge_parts):
                if i:
                    edge_row.append("  ")
                edge_row.append_text(part)
            tbl.add_row("Edge Mix", edge_row)
        else:
            tbl.add_row("Edge Mix", Text("no open positions", style="dim"))

        # ── Section 2: Scanner funnel (always available from decisions table)
        if funnel:
            tbl.add_row("─" * 12, Text("─ FUNNEL (7d) ─", style="dim"))
            for strat in sorted(funnel.keys()):
                fd = funnel[strat]
                total = fd.get("total_evaluated", 0)
                passed = fd.get("approved", 0)
                pr = fd.get("pass_rate", 0.0)
                pr_style = "green" if pr >= 10 else "yellow" if pr >= 3 else "red"
                display_name = get_strategy_display_name(strat) or strat
                tbl.add_row(
                    display_name,
                    Text(f"{passed}/{total} ({pr:.0f}%)", style=pr_style),
                )
        else:
            tbl.add_row("Funnel", Text("no scanner data (7d)", style="dim"))

        # ── Section 3: Realized expectancy by strategy
        tbl.add_row("─" * 12, Text(f"─ REALIZED (90d n={closed_n}) ─", style="dim"))
        if not data_ready:
            tbl.add_row("Expectancy", Text(f"awaiting data  n={closed_n}/10", style="dim"))
            if by_strategy:
                # Show counts even if data isn't ready
                for strat, blk in sorted(by_strategy.items()):
                    n = blk.get("n", 0)
                    if n:
                        display_name = get_strategy_display_name(strat) or strat
                        tbl.add_row(display_name, Text(f"n={n}  (accumulating)", style="dim"))
        else:
            # Rank by expectancy descending
            ranked = sorted(
                [(k, v) for k, v in by_strategy.items() if v.get("n", 0) >= 3],
                key=lambda x: x[1].get("expectancy", 0),
                reverse=True,
            )
            for strat, blk in ranked:
                display_name = get_strategy_display_name(strat) or strat
                exp = blk.get("expectancy", 0)
                wr  = blk.get("win_rate", 0)
                n   = blk.get("n", 0)
                exp_style = "green" if exp > 0 else "red"
                tbl.add_row(
                    display_name,
                    Text(f"E${exp:+.0f}  WR{wr:.0f}%  n={n}", style=exp_style),
                )

            best_strat = summary.get("best_strategy")
            best_strat_display = get_strategy_display_name(best_strat) if best_strat else None
            best_path  = summary.get("best_pathway")
            if best_strat_display:
                tbl.add_row("Hot Strat", Text(best_strat_display, style="bold green"))
            if best_path:
                tbl.add_row("Hot Path", Text(str(best_path)[:16], style="green"))

        # ── Section 4: Scan quality snapshot (Phase 4) ──────────────────────
        sq = self._refresh_scan_quality()
        tbl.add_row("─" * 12, Text("─ SCAN QUALITY ─", style="dim"))
        if not sq:
            tbl.add_row("Quality", Text("no report yet — run report_scan_quality.py", style="dim"))
        else:
            sq_funnel = sq.get("funnel", {})
            sq_scanned  = sq_funnel.get("total_scanned", 0)
            sq_approved = sq_funnel.get("total_approved", 0)
            sq_rate     = sq_funnel.get("approval_rate_pct", 0.0)
            sq_runs     = sq.get("run_count", 0)
            sq_days     = sq.get("window_days", "?")
            rate_style  = "green" if sq_rate >= 10 else "yellow" if sq_rate >= 3 else "red"
            tbl.add_row(
                f"Funnel ({sq_days}d)",
                Text(f"{sq_approved}/{sq_scanned} ({sq_rate:.0f}%)  runs={sq_runs}", style=rate_style),
            )
            # top reject reason
            top_reasons = sq.get("top_reject_reasons", [])
            if top_reasons:
                top = top_reasons[0]
                top_label = self._humanize_reject_reason(top.get("reason"), max_len=24)
                tbl.add_row(
                    "Top Reject",
                    Text(f"{top_label}  n={top.get('count', 0)}", style="yellow"),
                )
            # near misses
            nm_count = len(sq.get("near_misses", []))
            if nm_count:
                tbl.add_row("Near Misses", Text(f"{nm_count} tickers within 10pts", style="cyan"))
            # stable pattern warning
            stability = sq.get("reason_stability", {})
            if stability.get("stable"):
                stable_label = self._humanize_reject_reason(
                    stability.get("dominant_reason"),
                    max_len=20,
                )
                tbl.add_row(
                    "Pattern",
                    Text(f"⚠ {stable_label} x{stability.get('consecutive_runs', 0)}", style="bold red"),
                )
            gate_overlap = sq.get("gate_overlap", {})
            combos = gate_overlap.get("top_combos", []) if isinstance(gate_overlap, dict) else []
            if combos:
                top_combo = str(combos[0].get("combo") or "").replace("+", " + ").upper()
                top_pct = float(combos[0].get("pct_of_tagged", 0.0) or 0.0)
                tbl.add_row("Gate Mix", Text(f"{top_combo}  {top_pct:.0f}% tagged", style="magenta"))
            bte = sq.get("bte_advisory", {}) if isinstance(sq, dict) else {}
            if bte:
                top_bucket = bte.get("top_score_bucket") or "—"
                top_window = bte.get("top_score_bucket_window") or "—"
                top_lift = bte.get("top_score_bucket_lift")
                lift_txt = f"x{float(top_lift):.2f}" if top_lift is not None else "—"
                tbl.add_row(
                    "BTE",
                    Text(f"{top_bucket}  {lift_txt}  {top_window}", style="cyan"),
                )
            bte_short = sq.get("bte_short_advisory", {}) if isinstance(sq, dict) else {}
            if bte_short:
                top_bucket = bte_short.get("top_score_bucket") or "—"
                top_window = bte_short.get("top_score_bucket_window") or "—"
                top_lift = bte_short.get("top_score_bucket_lift")
                lift_txt = f"x{float(top_lift):.2f}" if top_lift is not None else "—"
                tbl.add_row(
                    "BTE-S",
                    Text(f"{top_bucket}  {lift_txt}  {top_window}", style="cyan"),
                )

        # ── Section 5: RR shadow calibration (Phase 4.1, read-only) ────────
        rr = self._refresh_rr_shadow()
        if rr:
            compact = rr.get("compact_summary", [])
            dq = rr.get("data_quality", {})
            if compact:
                parts = []
                for item in compact[:3]:
                    strat_short = item["strategy"][:3]
                    thr = item["best_alt_threshold"]
                    rec = item["recovery_count"]
                    parts.append(f"{strat_short} +{rec}@{thr}")
                tbl.add_row(
                    "RR Shadow",
                    Text("  ".join(parts), style="cyan"),
                )
            if dq:
                dq_parts = []
                for strat, q in sorted(dq.items()):
                    avail = q.get("rr_availability_pct", 0.0)
                    dq_parts.append(f"{strat[:3]} {avail:.0f}%")
                tbl.add_row(
                    "RR Data",
                    Text("  ".join(dq_parts[:3]), style="dim"),
                )
        else:
            tbl.add_row("RR Shadow", Text("run --with-rr-shadow to populate", style="dim"))

        # ── Section 6: Shadow Outcomes (read-only, Phase 4.2) ───────────────
        tbl.add_row("─" * 12, Text("─ SHADOW OUTCOMES ─", style="dim"))
        so = self._refresh_shadow_outcomes()
        if so:
            # Find best policy by target hit % among completed outcomes
            best_policy = None
            best_tgt = -1.0
            for pname, pdata in so.items():
                done = pdata.get("completed_outcomes", 0)
                tgt_pct = pdata.get("target_hit_pct")
                if done > 0 and tgt_pct is not None and tgt_pct > best_tgt:
                    best_tgt = tgt_pct
                    best_policy = (pname, pdata, done)
            if best_policy:
                pname, pdata, done = best_policy
                tgt_s = f"{pdata['target_hit_pct']:.0f}%" if pdata["target_hit_pct"] is not None else "—"
                short_name = pname.replace("_", "").upper()[:8]
                tbl.add_row(
                    "Best Policy",
                    Text(f"{short_name} tgt={tgt_s} n={done}", style="cyan"),
                )
            else:
                tbl.add_row("Outcomes", Text("no outcomes yet", style="dim"))
        else:
            tbl.add_row("Outcomes", Text("no outcomes yet", style="dim"))

        # ── Section 7: Pilot framework (Phase 4.3, compact read-only) ───────
        tbl.add_row("─" * 12, Text("─ PILOT ─", style="dim"))
        pilot_enabled = _PILOT_AVAILABLE and os.getenv("PILOT_ENABLE", "0").strip() in ("1", "true", "yes", "on")
        pilot_policies = os.getenv("PILOT_ALLOWED_POLICIES", "SHORT_2_5,SNIPER_2_2")
        status_txt = Text("ON", style="bold green") if pilot_enabled else Text("OFF", style="dim")
        tbl.add_row("Status", Text.assemble(status_txt, f"  Policy: {pilot_policies}"))
        if pilot_enabled:
            pd = self._refresh_pilot()
            exec_n = pd.get("executed", 0)
            blocked_n = pd.get("blocked", 0)
            tbl.add_row(
                "Today",
                Text(f"{exec_n} exec / {blocked_n} blocked    PnL: —", style="cyan"),
            )
        else:
            tbl.add_row("Today", Text("0 exec / 0 blocked    PnL: —", style="dim"))

        return Panel(tbl, title="[bold white]EDGE INTELLIGENCE", border_style="cyan", padding=(0, 1))

    def _panel_trade_journal(self) -> Panel:
        """Recent execution actions — WS fills first, then DB events."""
        _EVENT_MAP = {
            "SYNC":             ("SYNC",      "cyan"),
            "OPEN":             ("OPEN",      "bold green"),
            "ENTRY_SUBMITTED":  ("ENTRY",     "cyan"),
            "APPROVED_FILL":    ("FILL",      "bold green"),
            "EXIT_SUBMITTED":   ("EXIT",      "yellow"),
            "PORTFOLIO_DENIED": ("P-DENIED",  "red"),
            "EXECUTION_DENIED": ("E-DENIED",  "red"),
            "EXECUTE":          ("EXECUTE",   "bold green"),
            "REJECT":           ("REJECT",    "red"),
            "DATA_ERROR":       ("DATA",      "yellow"),
        }
        _FILL_EVENT_MAP = {
            "fill":         ("FILL ●",   "bold green"),
            "partial_fill": ("PART ●",   "green"),
            "canceled":     ("CANCEL",   "red"),
            "expired":      ("EXPIRED",  "dim red"),
            "replaced":     ("REPLACED", "yellow"),
            "done_for_day": ("DONE",     "dim"),
            "pending_new":  ("PENDING",  "dim"),
        }
        table = Table(show_header=True, box=rich_box.SIMPLE_HEAD, padding=(0, 1), expand=True)
        table.add_column("Time", width=10, style="dim")
        table.add_column("Event", width=10)
        table.add_column("Details")

        rendered = 0
        max_rows = 6

        # WS fills first — these are real-time and most actionable.
        # Track which (ticker, fill-class) pairs are shown so DB dups are suppressed.
        _ws_fill_tickers: set = set()   # tickers shown via WS fill/partial_fill
        _ws_cancel_tickers: set = set() # tickers shown via WS cancel/expired
        for fill in TradingFeed.get_recent_fills(limit=3):
            if rendered >= max_rows:
                break
            ev = fill.get("event", "")
            label, style = _FILL_EVENT_MAP.get(ev, (ev[:8].upper(), "dim"))
            ticker = fill.get("ticker", "—")
            side = fill.get("side", "")
            price = fill.get("fill_price")
            qty = fill.get("fill_qty")
            detail = f"{ticker} {side}"
            if price:
                detail += f" @ ${price:.2f}"
            if qty:
                detail += f" x{qty:.0f}"
            table.add_row(fill.get("ts", "--:--"), Text(label, style=style), detail[:64])
            rendered += 1
            if ev in ("fill", "partial_fill"):
                _ws_fill_tickers.add(ticker)
            elif ev in ("canceled", "expired"):
                _ws_cancel_tickers.add(ticker)

        # DB / log-sourced events fill remaining rows.
        # Skip entries whose ticker+event-class was already rendered via WS.
        _FILL_DB_EVENTS = {"APPROVED_FILL", "ENTRY_SUBMITTED", "EXECUTE", "OPEN", "SYNC"}
        _CANCEL_DB_EVENTS = {"EXECUTION_DENIED", "REJECT"}
        # Events that are scanner internals — not meaningful trade actions.
        _NOISE_EVENTS = {"DATA_ERROR", "FILTERED_LOW_RR", "FILTERED_NO_SIGNAL"}
        for entry in list(self.recent_events)[: max_rows - rendered]:
            try:
                ts, ev, details = entry
                ev_upper = str(ev).upper()
                # Suppress scanner noise — these are not trade actions.
                if ev_upper in _NOISE_EVENTS:
                    continue
                # Suppress DB duplicate if WS already surfaced the same ticker/event
                db_ticker = str(details).split()[0].upper() if details else ""
                if db_ticker and ev_upper in _FILL_DB_EVENTS and db_ticker in _ws_fill_tickers:
                    continue
                if db_ticker and ev_upper in _CANCEL_DB_EVENTS and db_ticker in _ws_cancel_tickers:
                    continue

                label, style = next(
                    ((v[0], v[1]) for k, v in _EVENT_MAP.items() if k in str(ev)),
                    (str(ev)[:10], "dim"),
                )
                time_style = "yellow" if "/" in str(ts) else "dim"
                detail_text = str(details)[:64]
                if "/" in str(ts):
                    detail_text = f"STALE  {detail_text}"
                table.add_row(
                    Text(str(ts)[:10], style=time_style),
                    Text(label, style=style),
                    detail_text,
                )
                rendered += 1
            except Exception:
                pass

        if rendered == 0:
            table.add_row("--:--", Text("NONE", style="dim"), "No recent actions logged")

        ts_status = TradingFeed.get_status()
        ts_label = ts_status.get("status", "—")
        ts_style = "bold green" if ts_label in ("LIVE", "IDLE") else "dim"
        return Panel(
            table,
            title=f"[bold white]TRADE JOURNAL / ACTIONS  [dim][Orders: {ts_label}]",
            border_style="white",
            padding=(0, 1),
        )

    def _panel_critical_news(self) -> Panel:
        """Critical news strip — only rendered when active headlines exist."""
        with self._news_lock:
            items = list(self._news_items)
        table = Table.grid(padding=(0, 1))
        table.add_column(width=10)
        table.add_column()
        for item in items[:3]:
            score = item["score"]
            if score >= 3:
                badge = Text("CRITICAL", style="bold red on black")
            elif score >= 2:
                badge = Text("ALERT", style="bold yellow")
            else:
                badge = Text("NEWS", style="yellow")
            table.add_row(badge, Text(item["title"][:115], style="white"))
        age_s = int(time.time() - self._last_news_fetch) if self._last_news_fetch else 0
        age_str = f"{age_s // 60}m ago" if age_s >= 60 else f"{age_s}s ago"
        return Panel(
            table,
            title=f"[bold red]⚡ CRITICAL MARKET ALERTS  [dim white]{len(items)} headlines · fetched {age_str}",
            border_style="bold red",
            padding=(0, 1),
        )

    def _panel_footer(self) -> Panel:
        log_name = os.path.basename(self.log_file) if self.log_file else "no log attached"
        txt = Text()
        txt.append("LOG: ", style="dim")
        txt.append(log_name, style="white")
        txt.append("  |  ", style="dim")
        txt.append(f"Scans: {self.scan_count}", style="cyan")
        txt.append("  |  ", style="dim")
        txt.append("REFRESH: 2s  LIVE MKT: 60s", style="dim")
        txt.append("  |  ", style="dim")
        # Websocket live feed status
        ws = LiveFeed.get_feed_status()
        ws_label = ws.get("status", "—")
        ws_subs = ws.get("subscribed_count", 0)
        ws_age = ws.get("last_msg_age_s")
        ws_style = (
            "bold green" if ws_label == "LIVE"
            else "bold yellow" if ws_label in ("STALE", "STARTING")
            else "bold red" if ws_label in ("DISCONNECTED", "STOPPED")
            else "dim"
        )
        ws_age_str = f" {ws_age:.0f}s" if ws_age is not None else ""
        txt.append(f"WS: {ws_label}{ws_age_str} [{ws_subs}t]", style=ws_style)
        txt.append("  ", style="dim")
        # Trading stream status
        ts_st = TradingFeed.get_status()
        ts_label = ts_st.get("status", "—")
        ts_style = "bold green" if ts_label in ("LIVE", "IDLE") else "bold red" if ts_label in ("DISCONNECTED", "STOPPED") else "dim"
        txt.append(f"Orders: {ts_label}", style=ts_style)
        txt.append("  |  ", style="dim")
        # REST feed health
        try:
            from alpaca_data import AlpacaDataFeed
            stats = AlpacaDataFeed.get_feed_stats()
            status = stats.get("status", "OK")
            hit_rate = stats.get("cache_hit_rate_pct", 0)
            rl_count = stats.get("rate_limit_count", 0)
            feed_style = "bold red" if status == "RATE-LIMITED" else ("yellow" if rl_count > 0 else "dim green")
            txt.append(f"REST: {status} c={hit_rate:.0f}%", style=feed_style)
            if rl_count:
                txt.append(f" rl={rl_count}", style="bold red")
        except Exception:
            txt.append("REST: —", style="dim")
        txt.append("  |  ", style="dim")
        # Edge mix in footer (same cache as strip — no extra DB hit)
        edge_f = self._edge_summary()
        if edge_f:
            mix_f = edge_f.get("edge_mix", {})
            af = (mix_f.get("ALIGNMENT", {}) or {}).get("open", 0)
            if_ = (mix_f.get("INEFFICIENCY", {}) or {}).get("open", 0)
            cf = (mix_f.get("CONTRARIAN", {}) or {}).get("open", 0)
            txt.append(f"EDGE: A{af}/I{if_}/C{cf}", style="cyan")
        else:
            txt.append("EDGE: —", style="dim")
        txt.append("  |  ", style="dim")
        txt.append("CTRL+C TO EXIT", style="dim italic")
        return Panel(txt, border_style="dim")

    # ── layout ────────────────────────────────────────────────────────────────

    def generate_layout(self) -> Layout:
        """
        Row budget (~42 terminal lines):
          header    3
          strip     5   <- regime/strategy — read first
          core     16   <- positions | opps/denials | macro risk
          secondary 10  <- portfolio risk | flow context | trade journal
          news      4   <- critical market alerts (only when active)
          footer    3
        """
        with self._news_lock:
            has_news = bool(self._news_items)

        rows = [
            Layout(name="header",    size=3),
            Layout(name="strip",     size=5),
            Layout(name="core",      size=16),
            Layout(name="secondary", size=10),
        ]
        if has_news:
            rows.append(Layout(name="news", size=4))
        rows.append(Layout(name="footer", size=3))

        layout = Layout()
        layout.split_column(*rows)

        layout["header"].update(self._panel_header())
        layout["strip"].update(self._panel_regime_strip())

        layout["core"].split_row(
            Layout(self._panel_live_positions(),          ratio=5),
            Layout(self._panel_opportunities_decisions(), ratio=3),
            Layout(self._panel_macro_risk(),              ratio=2),
        )

        layout["secondary"].split_row(
            Layout(self._panel_portfolio_risk(), ratio=2),
            Layout(self._panel_trade_analytics(), ratio=2),
            Layout(self._panel_trade_journal(),  ratio=3),
        )

        if has_news:
            layout["news"].update(self._panel_critical_news())
        layout["footer"].update(self._panel_footer())
        return layout

    # ── run loop ──────────────────────────────────────────────────────────────

    def run(self):
        """Operator terminal. 2s UI refresh; market data every 15s; news every 120s (all non-blocking)."""
        self.console.clear()
        self.console.print("\n[bold white]V3 INSTITUTIONAL PLATFORM[/bold white] — starting up...\n")

        _last_mkt_fetch = 0.0
        _last_news_fetch = 0.0

        def _bg_market():
            try:
                self._fetch_live_market_data()
                self._apply_vix_strategy_gates()
            except Exception:
                pass

        def _bg_news():
            try:
                self._fetch_news()
            except Exception:
                pass

        # Initial data load — market and news fetched in background so UI starts immediately
        self.parse_log_file()
        threading.Thread(target=_bg_market, daemon=True).start()
        threading.Thread(target=_bg_news, daemon=True).start()
        _last_mkt_fetch = time.time()
        _last_news_fetch = time.time()

        try:
            with Live(
                self.generate_layout(),
                refresh_per_second=0.5,
                console=self.console,
                screen=True,
            ) as live:
                while True:
                    self.parse_log_file()       # incremental log tail — fast, never blocks
                    self._refresh_heartbeat()   # throttled 5s
                    LiveFeed.write_snapshot()   # throttled 2s
                    now = time.time()
                    if now - _last_mkt_fetch >= 15:
                        _last_mkt_fetch = now   # stamp before thread to prevent double-spawn
                        threading.Thread(target=_bg_market, daemon=True).start()
                    if now - _last_news_fetch >= 120:
                        _last_news_fetch = now
                        threading.Thread(target=_bg_news, daemon=True).start()
                    live.update(self.generate_layout())
                    time.sleep(2)
        except KeyboardInterrupt:
            self.console.clear()
            self.console.print("\n[white]Dashboard stopped.[/white]\n")
        finally:
            LiveFeed.stop()
            TradingFeed.stop()


# ══════════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Live Dashboard V3 — Operator Grade")
    parser.add_argument("--log", default=None, help="Path to trader log file")
    args = parser.parse_args()

    if not RICH_AVAILABLE:
        print("\nrich is required: pip install rich\n")
        return

    try:
        dash = V3DashboardPro(log_file=args.log)
        dash.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\nStartup error: {e}\n")
        raise


if __name__ == "__main__":
    main()
