#!/usr/bin/env python3
"""
Command Center Terminal V3
==========================
Operator-grade multi-strategy terminal.

Real data only. No synthetic metrics. No fake flows.

Panels:
  Header         — time, market status, scan count, confluence
  Market Health  — regime, breadth, trend (institutional modules)
  Money Flow     — sector rotation leaders/laggards
  Smart Money    — put/call ratio, gamma, global macro
  Opportunities  — log-parsed signal candidates
  Strategy/Pos   — live positions with stop/target/protection + strategy states
  Catalysts      — upcoming macro events (macro calendar)
  Trading Signal — consolidated risk-on/risk-off score
  Footer         — log source, refresh

Data sources:
  strategy   : pos["strategy"] → meta["strategy"] → UNK
  direction  : pos["direction"] → meta["direction"] → qty sign (< 0 = SHORT) → —
  stop       : Alpaca open orders (live) → DB decisions → log parser → —
  target     : Alpaca open orders (live) → DB decisions → log parser → —
  protection : BRKT/S+T/STOP/TGT/BARE/UNKN — derived, never faked
  meta       : trading_performance.db decisions table + position_overrides.json
  brackets   : _fetch_broker_brackets() queries Alpaca open stop/limit orders live
"""

import argparse
import glob
import os
import re
import sqlite3
import threading
import time
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
)
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

load_runtime_env("command_center_terminal_v3")

try:
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box as rich_box
    RICH_AVAILABLE = True
except Exception:
    RICH_AVAILABLE = False

try:
    from market_regime_detector import MarketRegimeDetector
    from sector_rotation_tracker import SectorRotationTracker
    from market_breadth_monitor import MarketBreadthMonitor
    from options_positioning_tracker import OptionsPositioningTracker
    from inter_market_monitor import InterMarketMonitor
    INSTITUTIONAL_AVAILABLE = True
except Exception:
    INSTITUTIONAL_AVAILABLE = False

try:
    from macro_calendar import MacroCalendar
    CALENDAR_AVAILABLE = True
except Exception:
    CALENDAR_AVAILABLE = False

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
    from alpaca.trading.client import TradingClient
    ALPACA_AVAILABLE = True
except Exception:
    ALPACA_AVAILABLE = False

from log_selection import select_active_trader_log
from trading_state import normalize_position, read_heartbeat, get_daemon_status


class CommandCenterV3:
    """
    Lean, operator-grade command center.
    All rendered data is real — no synthetic metrics or fake flows.
    """
    CONFLUENCE_TTL_SECONDS = 4 * 60 * 60

    _INVALID_STRATEGY_VALUES = {
        "LIVE", "PAPER", "DRY_RUN", "LIVE_TRADING", "PAPER_TRADING",
        "NONE", "NULL", "N/A", "", "ACTIVE", "STANDBY", "HUNTING",
    }

    def __init__(self, log_file: Optional[str] = None):
        if not RICH_AVAILABLE:
            raise ImportError("rich is required: pip install rich")

        self.console = Console()

        if not log_file:
            log_file = select_active_trader_log("logs/trader_v3_*.log")

        self.log_file = log_file
        self.last_position = 0
        self.last_log_update: Optional[datetime] = None
        # Start from EOF to avoid replaying stale historical confluence events.
        try:
            if self.log_file and os.path.exists(self.log_file):
                self.last_position = os.path.getsize(self.log_file)
        except Exception:
            self.last_position = 0

        self.scan_count = 0
        self.last_scan_time: Optional[datetime] = None
        self.strategy_status: Dict[str, str] = {
            "voyager": "ACTIVE",
            "sniper": "LOADING",
            "remora": "LOADING",
            "short": "ACTIVE",
            "contrarian": "STANDBY",
            "vix_sizing": "100%",
        }

        self.triple_active = False
        self.triple_count = 0
        self.triple_tickers: List[str] = []
        self.triple_last_ts: float = 0.0
        self.pre_signal_overlap: int = 0
        self.pre_signal_overlap_time: Optional[str] = None
        self.opportunities: List[Dict] = []
        self.pattern_distribution: Dict[str, int] = {}

        self.trading_client = None
        self.positions: Dict[str, Dict] = {}
        self.position_meta: Dict[str, Dict] = {}
        self.account_info: Dict[str, float] = {}
        self._last_exec_ticker: Optional[str] = None

        self.cached_regime = None
        self.cached_rotation = None
        self.cached_breadth = None
        self.cached_options = None
        self.cached_inter = None
        self.last_data_refresh = 0.0

        self.events_cache: List[Dict] = []
        self.last_events_refresh = 0.0

        # Throttle timestamps — prevent hammering Alpaca on every 4s render tick
        self._last_positions_fetch: float = 0.0   # get_all_positions(): at most every 10s
        self._last_brackets_fetch: float = 0.0    # get_orders() brackets: at most every 30s
        self._heartbeat_state: Dict = {}
        self._last_heartbeat_read: float = 0.0
        self._risk_gate_snapshot: Dict[str, object] = {}
        self._last_risk_gate_refresh: float = 0.0
        self._policy_snapshot: Dict[str, object] = {}
        self._last_policy_snapshot_refresh: float = 0.0
        self._trend_snapshot: Dict[str, object] = {}
        self._last_trend_snapshot_refresh: float = 0.0
        self._action_snapshot: Dict[Tuple[str, str], Dict[str, float]] = {}
        self._action_delta_rows: List[Dict[str, str]] = []
        self._action_snapshot_scan_count: int = -1

        if INSTITUTIONAL_AVAILABLE:
            self.regime_detector = MarketRegimeDetector()
            self.rotation_tracker = SectorRotationTracker()
            self.breadth_monitor = MarketBreadthMonitor()
            self.options_tracker = OptionsPositioningTracker()
            self.inter_market = InterMarketMonitor()
        else:
            self.regime_detector = None
            self.rotation_tracker = None
            self.breadth_monitor = None
            self.options_tracker = None
            self.inter_market = None

        self.macro_cal = MacroCalendar() if CALENDAR_AVAILABLE else None

        # Scan quality diagnostics cache
        self._sq_cache: Dict = {}
        self._sq_ts: float = 0.0
        self._sq_engine = _ScanDiagEngine() if _SCAN_DIAG_AVAILABLE else None

        # RR shadow calibration cache
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

        self._init_broker()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _safe_float(self, v, default: float = 0.0) -> float:
        try:
            return float(v)
        except Exception:
            return default

    def _normalize_strategy(self, raw) -> str:
        if raw is None:
            return "UNK"
        v = str(raw).strip().upper()
        v = re.sub(r"[\s\-]+", "_", v)
        if not v or v in self._INVALID_STRATEGY_VALUES:
            return "UNK"
        if v in ("VOYAGER", "VOY"):
            return "VOY"
        if v in ("SNIPER", "SNP"):
            return "SNP"
        if v in ("REMORA", "REM"):
            return "REM"
        if v in ("SHRT", "SHORT", "SHORT_BOOK", "SHORTBOOK", "SHORT_TRADE", "SHORTTRADE"):
            return "SHRT"
        if v in (
            "CONTRARIAN",
            "CONTRARIAN_TRADE",
            "REAPER",
            "REAPER_TRADE",
            "REAP",
            "RPR",
        ):
            return "RPR"
        return v[:7]

    def _dir_normalize(self, raw) -> Optional[str]:
        """Returns None when direction is unknown — caller renders '—'. Never fakes LONG."""
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
        S+T  = stop + target, in-code        (green)
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

    def _market_status(self) -> Tuple[bool, str]:
        """
        Returns (is_open, label). Labels: OPEN / PRE-MKT / AFTER-HRS / CLOSED / WEEKEND
        Uses pytz (ET) with UTC-5 fallback.
        """
        try:
            try:
                import pytz
                et = pytz.timezone("America/New_York")
                now_et = datetime.now(et)
            except ImportError:
                from datetime import timezone, timedelta
                now_et = datetime.now(timezone(timedelta(hours=-5)))
            weekday = now_et.weekday()  # 0=Mon … 6=Sun
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

    def _refresh_heartbeat(self):
        now = time.time()
        if (now - self._last_heartbeat_read) < 5:
            return
        self._last_heartbeat_read = now
        self._heartbeat_state = read_heartbeat()

    def _daemon_status(self) -> Tuple[str, str]:
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
        now = time.time()
        if (now - self._last_risk_gate_refresh) < 15:
            return
        self._last_risk_gate_refresh = now

        snapshot = {
            "portfolio_mode": "UNKNOWN",
            "circuit_breaker": False,
            "gate_counts": {},
        }
        self._refresh_heartbeat()
        hb = self._heartbeat_state or {}
        snapshot["portfolio_mode"] = str(hb.get("portfolio_mode") or "UNKNOWN")
        snapshot["circuit_breaker"] = bool(hb.get("portfolio_circuit_breaker", False))

        db_path = "trading_performance.db"
        if os.path.exists(db_path):
            con = None
            try:
                con = sqlite3.connect(db_path)
                cur = con.cursor()
                cur.execute(
                    "SELECT timestamp, council_decision, execution_deny_reason "
                    "FROM decisions "
                    "WHERE execution_denied = 1 OR council_decision LIKE '%DENIED%' "
                    "ORDER BY timestamp DESC LIMIT 200"
                )
                cutoff = datetime.now() - timedelta(days=1)
                for ts_raw, decision, reason in cur.fetchall():
                    try:
                        ts = datetime.fromisoformat(str(ts_raw))
                    except Exception:
                        continue
                    if ts < cutoff:
                        continue
                    bucket = self._classify_denial_bucket(decision, reason)
                    snapshot["gate_counts"][bucket] = snapshot["gate_counts"].get(bucket, 0) + 1
            except Exception:
                pass
            finally:
                if con:
                    con.close()
        self._risk_gate_snapshot = snapshot

    def _refresh_policy_snapshot(self):
        now = time.time()
        if (now - self._last_policy_snapshot_refresh) < 15:
            return
        self._last_policy_snapshot_refresh = now
        self._policy_snapshot = load_terminal_policy_snapshot("trading_performance.db")

    def _refresh_trend_snapshot(self):
        now = time.time()
        if (now - self._last_trend_snapshot_refresh) < 60:
            return
        self._last_trend_snapshot_refresh = now
        self._trend_snapshot = load_terminal_trend_snapshot("trading_performance.db", "logs", width=10)

    def _action_board_payload(self) -> Dict[str, List[Dict[str, object]]]:
        blocked = fetch_recent_blocked_decisions("trading_performance.db", limit=4)
        return build_action_buckets(
            list(self.opportunities),
            blocked_rows=blocked,
            ready_limit=3,
            watch_limit=3,
            blocked_limit=4,
        )

    def _refresh_action_snapshot(self) -> Dict[str, List[Dict[str, object]]]:
        payload = self._action_board_payload()
        if self.scan_count != self._action_snapshot_scan_count:
            self._action_delta_rows, self._action_snapshot = compute_scan_delta(
                list(self.opportunities),
                self._action_snapshot,
                limit=4,
            )
            self._action_snapshot_scan_count = self.scan_count
        return payload

    def _now_et(self):
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("America/New_York"))
        return datetime.now()

    def _parse_scan_marker_dt(self, raw: str):
        if not raw:
            return None
        m = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)\s*ET", str(raw), re.IGNORECASE)
        if not m:
            return None
        try:
            clock = datetime.strptime(m.group(1).upper().replace("  ", " "), "%I:%M %p")
            now_et = self._now_et()
            candidate = now_et.replace(
                hour=clock.hour,
                minute=clock.minute,
                second=0,
                microsecond=0,
            )
            while candidate > (now_et + timedelta(minutes=5)):
                candidate -= timedelta(days=1)
            return candidate
        except Exception:
            return None

    def _format_age(self, dt_obj: Optional[datetime]) -> str:
        if not dt_obj:
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
        hours = mins // 60
        return f"{hours}h"

    def _humanize_reject_reason(self, raw_reason: object, max_len: int = 24) -> str:
        """Render machine reject keys as concise operator-friendly labels."""
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

    def _refresh_scan_quality(self) -> Dict:
        """Refresh scan quality snapshot from DB. TTL 300s."""
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

    def _load_position_overrides(self) -> dict:
        """Read position_overrides.json — allows manual stop/target/strategy for legacy positions."""
        path = "position_overrides.json"
        if self.log_file:
            alt = os.path.join(os.path.dirname(self.log_file), "position_overrides.json")
            if os.path.exists(alt):
                path = alt
        if not os.path.exists(path):
            return {}
        try:
            import json
            with open(path) as f:
                data = json.load(f)
            return {k.upper(): v for k, v in data.items() if not k.startswith("_")}
        except Exception:
            return {}

    def _refresh_position_meta(self):
        """Hydrate stop/target/strategy/direction from decisions DB, then apply overrides."""
        db_path = "trading_performance.db"
        if os.path.exists(db_path) and self.positions:
            try:
                con = sqlite3.connect(db_path)
                cur = con.cursor()
                tickers = list(self.positions.keys())
                placeholders = ",".join("?" * len(tickers))
                cur.execute(
                    f"""SELECT ticker, strategy, stop_loss, target_price, direction, exit_mode
                        FROM decisions
                        WHERE ticker IN ({placeholders})
                          AND position_opened = 1
                        ORDER BY timestamp DESC""",
                    tickers,
                )
                rows = cur.fetchall()
                con.close()
                seen: set = set()
                for ticker, strategy, stop_loss, target_price, direction, exit_mode in rows:
                    t = (ticker or "").upper()
                    if t in seen:
                        continue
                    seen.add(t)
                    rec = dict(self.position_meta.get(t, {}))
                    if strategy:
                        rec["strategy"] = str(strategy).upper()
                    sl = self._safe_float(stop_loss)
                    if sl > 0:
                        rec["stop_loss"] = sl
                    tp = self._safe_float(target_price)
                    if tp > 0:
                        rec["target_price"] = tp
                    if direction:
                        rec["direction"] = str(direction).upper()
                    if exit_mode:
                        rec["exit_mode"] = str(exit_mode)
                    rec["bracket_attached"] = bool(rec.get("stop_loss") and rec.get("target_price"))
                    self.position_meta[t] = rec
            except Exception:
                pass

        # Overrides always win — read every cycle (no restart required)
        for ticker, ov in self._load_position_overrides().items():
            if ticker not in self.positions:
                continue
            rec = dict(self.position_meta.get(ticker, {}))
            if ov.get("strategy"):
                rec["strategy"] = str(ov["strategy"]).upper()
            sl = self._safe_float(ov.get("stop_loss", 0))
            if sl > 0:
                rec["stop_loss"] = sl
            tp = self._safe_float(ov.get("target_price", 0))
            if tp > 0:
                rec["target_price"] = tp
            if ov.get("direction"):
                rec["direction"] = str(ov["direction"]).upper()
            rec["bracket_attached"] = bool(rec.get("stop_loss") and rec.get("target_price"))
            self.position_meta[ticker] = rec

        # Live Alpaca bracket orders are highest-priority source (overwrite DB values)
        self._fetch_broker_brackets()

    def _fetch_broker_brackets(self):
        """
        Hydrate stop_loss / target_price / bracket_attached from live Alpaca open orders.
        Stop child orders (type=stop) → stop_loss price.
        Take-profit child orders (type=limit) → target_price.
        Called after DB hydration so broker is the authoritative source.
        """
        if not self.trading_client or not self.positions:
            return
        _now = time.time()
        if (_now - self._last_brackets_fetch) < 30:
            return
        self._last_brackets_fetch = _now
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=200)
            orders = self.trading_client.get_orders(filter=req)
        except Exception:
            try:
                orders = self.trading_client.get_orders()
            except Exception:
                return

        stop_by_symbol: dict = {}
        target_by_symbol: dict = {}

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
            rec["bracket_attached"] = bool(rec.get("stop_loss") and rec.get("target_price"))
            self.position_meta[sym] = rec

    # ── broker ────────────────────────────────────────────────────────────────

    def _init_broker(self):
        if not ALPACA_AVAILABLE:
            return
        try:
            api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
            secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
            if api_key and secret_key:
                self.trading_client = TradingClient(api_key, secret_key, paper=True)
        except Exception:
            self.trading_client = None

    # ── log parsing ───────────────────────────────────────────────────────────

    def _apply_vix_strategy_gates(self):
        """Enforce VIX-based strategy gates on the displayed status.

        Sniper tracks institutional 1-50 day moves — institutions sideline at VIX ≥ 20.
        Remora hunts institutional flaws 2-48h — viable up to VIX < 30.
        Reaper (Contrarian) is armed in fear regimes at VIX ≥ 28.
        Universe count lines ('Sniper: 73 stocks') reflect the watchlist pool only;
        this override enforces the actual VIX gate on displayed state.
        """
        vix = None
        # Prefer live regime data from MarketRegimeDetector (yfinance ^VIX)
        if self.cached_regime:
            try:
                vix = float(self.cached_regime.get("vix_level", 0) or 0)
            except (ValueError, TypeError):
                vix = None

        if not vix or vix <= 0:
            return

        if vix >= 20.0:
            self.strategy_status["sniper"] = "STANDBY"

        if vix >= 30.0:
            self.strategy_status["remora"] = "STANDBY"
        elif self.strategy_status.get("remora") == "LOADING":
            # Remora is active at VIX <= 30 by design — infer HUNTING until log confirms
            self.strategy_status["remora"] = "HUNTING"

        if vix >= 28.0:
            if self.strategy_status.get("contrarian") not in ("HUNTING",):
                self.strategy_status["contrarian"] = "ARMED"
        else:
            # Reaper must stand down below trigger regardless of stale prior state.
            self.strategy_status["contrarian"] = "STANDBY"

    def parse_log_file(self):
        if not self.log_file or not os.path.exists(self.log_file):
            return
        try:
            try:
                file_size = os.path.getsize(self.log_file)
                if self.last_position > file_size:
                    self.last_position = 0
            except Exception:
                pass
            with open(self.log_file, "r") as f:
                f.seek(self.last_position)
                lines = f.readlines()
                self.last_position = f.tell()
            if lines:
                self.last_log_update = datetime.fromtimestamp(os.path.getmtime(self.log_file)).astimezone()
        except Exception:
            return

        for raw in lines:
            line = raw.strip()
            if not line:
                continue

            # Scan cycle detection
            if any(k in line for k in ("SCAN CYCLE", "V3 INTELLIGENT SCAN", "VOYAGER PRODUCTION COMPLETE SCAN")):
                self.scan_count += 1
                self.last_scan_time = self._parse_scan_marker_dt(line) or self.last_log_update
                self.opportunities = []

            # Strategy status
            if "🎯 Sniper:" in line or "Sniper Strategy:" in line:
                if "ACTIVE" in line:
                    self.strategy_status["sniper"] = "ACTIVE"
                elif "STANDBY" in line:
                    self.strategy_status["sniper"] = "STANDBY"
            elif re.search(r"\bSniper:\s+(\d+)\s+stocks\b", line):
                m = re.search(r"\bSniper:\s+(\d+)\s+stocks\b", line)
                if m:
                    self.strategy_status["sniper"] = "ACTIVE" if int(m.group(1)) > 0 else "STANDBY"

            if "🦈 Remora:" in line or "Remora Strategy:" in line:
                if "HUNTING" in line or "ACTIVE" in line:
                    self.strategy_status["remora"] = "HUNTING"
                elif "STANDBY" in line:
                    self.strategy_status["remora"] = "STANDBY"
            elif re.search(r"\bRemora:\s+(\d+)\s+stocks\b", line):
                m = re.search(r"\bRemora:\s+(\d+)\s+stocks\b", line)
                if m:
                    self.strategy_status["remora"] = "HUNTING" if int(m.group(1)) > 0 else "STANDBY"

            if "Short Strategy:" in line:
                if "ACTIVE" in line:
                    self.strategy_status["short"] = "ACTIVE"
                elif "STANDBY" in line:
                    self.strategy_status["short"] = "STANDBY"
            elif re.search(r"\bShort:\s+(\d+)\s+dedicated short setups\b", line):
                # This line confirms the short engine executed this cycle.
                self.strategy_status["short"] = "ACTIVE"
            elif re.search(r"\bShort:\s+(\d+)\s+stocks\b", line):
                self.strategy_status["short"] = "ACTIVE"

            # Reaper (Contrarian) status is sourced from scan-cycle logs.
            if "Running Contrarian scan" in line:
                self.strategy_status["contrarian"] = "HUNTING"
            elif "Contrarian: standing down" in line:
                self.strategy_status["contrarian"] = "STANDBY"
            elif "Contrarian:" in line and "setups" in line:
                try:
                    m = re.search(r"Contrarian:\s*(\d+)\s*setups", line)
                    count = int(m.group(1)) if m else 0
                    self.strategy_status["contrarian"] = "HUNTING" if count > 0 else "ARMED"
                except Exception:
                    pass

            if "VIX sizing:" in line:
                try:
                    self.strategy_status["vix_sizing"] = line.split("VIX sizing:", 1)[1].strip().split()[0]
                except Exception:
                    pass

            # Triple confluence
            if "Pre-signal overlap:" in line:
                try:
                    overlap = int(line.split("Pre-signal overlap:", 1)[1].split("symbols")[0].strip())
                    self.pre_signal_overlap = overlap
                    self.pre_signal_overlap_time = datetime.now().strftime("%H:%M:%S")
                except Exception:
                    pass

            if (
                "No signal-level triple confluence detected" in line
                or "No triple confluence at this time" in line
            ):
                self.triple_active = False
                self.triple_count = 0
                self.triple_tickers = []

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
                    self.triple_count = count
                    self.triple_active = self.triple_count > 0
                    self.triple_last_ts = time.time()
                    self.triple_tickers = []
                except Exception:
                    pass

            if "⭐⭐⭐" in line and "EXECUTING:" not in line and (self.triple_active or "MAXIMUM CONVICTION" in line):
                m = re.search(r"⭐⭐⭐\s*([A-Z]{1,6})\b", line)
                if m and m.group(1) not in self.triple_tickers:
                    self.triple_tickers.append(m.group(1))

            # ── execution context (bracket orders) ────────────────────────────
            if "EXECUTING:" in line or "SINGLE STRATEGY:" in line or "DOUBLE CONFLUENCE:" in line:
                m = re.search(r"(?:EXECUTING:|SINGLE STRATEGY:|DOUBLE CONFLUENCE:)\s*([A-Z]{1,6})", line)
                if m:
                    self._last_exec_ticker = m.group(1).upper()

            if "BRACKET ORDER EXECUTED" in line and getattr(self, "_last_exec_ticker", None):
                rec = dict(self.position_meta.get(self._last_exec_ticker, {}))
                rec["bracket_attached"] = True
                self.position_meta[self._last_exec_ticker] = rec

            if getattr(self, "_last_exec_ticker", None) and "Direction:" in line:
                m = re.search(r"Direction:\s*(LONG|SHORT)", line)
                if m:
                    rec = dict(self.position_meta.get(self._last_exec_ticker, {}))
                    rec["direction"] = m.group(1)
                    self.position_meta[self._last_exec_ticker] = rec

            if getattr(self, "_last_exec_ticker", None) and "Stop:" in line and "$" in line:
                m = re.search(r"Stop:\s*\$([0-9]+(?:\.[0-9]+)?)", line)
                if m:
                    rec = dict(self.position_meta.get(self._last_exec_ticker, {}))
                    rec["stop_loss"] = float(m.group(1))
                    rec["stop_active"] = True
                    rec["bracket_attached"] = bool(rec.get("stop_loss") and rec.get("target_price"))
                    self.position_meta[self._last_exec_ticker] = rec

            if getattr(self, "_last_exec_ticker", None) and "Target:" in line and "$" in line:
                m = re.search(r"Target:\s*\$([0-9]+(?:\.[0-9]+)?)", line)
                if m:
                    rec = dict(self.position_meta.get(self._last_exec_ticker, {}))
                    rec["target_price"] = float(m.group(1))
                    rec["target_active"] = True
                    rec["bracket_attached"] = bool(rec.get("stop_loss") and rec.get("target_price"))
                    self.position_meta[self._last_exec_ticker] = rec

            # ── Sniper V2 breakout opportunities ──────────────────────────────
            if "SniperV2 BREAKOUT:" in line:
                m = re.search(r"SniperV2 BREAKOUT:\s+([A-Z]{1,6})", line)
                if m:
                    score_m = re.search(r"score\s+(\d+)", line)
                    rr_m = re.search(r"R/R\s+([0-9.]+)", line)
                    self.opportunities.append({
                        "ticker": m.group(1),
                        "score": int(score_m.group(1)) if score_m else 0,
                        "rr": float(rr_m.group(1)) if rr_m else 0.0,
                        "approved": True,
                    })

            # Opportunity candidates
            if "APPROVED (" in line:
                self.opportunities = []

            if "- Score" in line:
                ticker_m = re.search(r"(?:\d+\.\s*)?([A-Z]{1,6})\s*-\s*Score", line)
                score_m = re.search(r"Score:?\s*(\d+)", line)
                rr_m = re.search(r"R/R:?\s*([0-9]+(?:\.[0-9]+)?)", line)
                if ticker_m and score_m:
                    tk = ticker_m.group(1)
                    score = int(score_m.group(1))
                    rr = float(rr_m.group(1)) if rr_m else 0.0
                    approved = "✅" in line or "APPROVED" in line
                    existing = next((o for o in self.opportunities if o.get("ticker") == tk), None)
                    if existing:
                        existing.update({"score": score, "rr": rr, "approved": approved})
                    else:
                        self.opportunities.append({"ticker": tk, "score": score, "rr": rr, "approved": approved})

            # Pattern distribution
            if any(k in line for k in ("Bottoming:", "Accumulation:", "Breakout:")):
                try:
                    name, val = line.split(":", 1)
                    label = name.strip().split()[-1]
                    self.pattern_distribution[label] = int(val.strip().split()[0])
                except Exception:
                    pass

        self._expire_stale_confluence()

    def _expire_stale_confluence(self):
        if not self.triple_active:
            return
        if self.triple_last_ts <= 0:
            return
        if (time.time() - self.triple_last_ts) > self.CONFLUENCE_TTL_SECONDS:
            self.triple_active = False
            self.triple_count = 0
            self.triple_tickers = []

    # ── data refresh ──────────────────────────────────────────────────────────

    def refresh_market_data(self):
        now = time.time()
        if now - self.last_data_refresh < 60:
            return
        if INSTITUTIONAL_AVAILABLE:
            _calls = [
                ("cached_regime",    lambda: self.regime_detector.detect_regime()),
                ("cached_rotation",  lambda: self.rotation_tracker.analyze_rotation("5d")),
                ("cached_breadth",   lambda: self.breadth_monitor.analyze_breadth()),
                ("cached_options",   lambda: self.options_tracker.analyze_positioning()),
                ("cached_inter",     lambda: self.inter_market.analyze_inter_market("5d")),
            ]
            for attr, call in _calls:
                try:
                    setattr(self, attr, call())
                except Exception:
                    pass
        self.last_data_refresh = now
        self._apply_vix_strategy_gates()

    def refresh_events(self):
        now = time.time()
        if now - self.last_events_refresh < 300:
            return
        if self.macro_cal:
            try:
                self.events_cache = self.macro_cal.get_upcoming_events(days_ahead=7) or []
            except Exception:
                self.events_cache = []
        self.last_events_refresh = now

    def refresh_positions(self):
        if not self.trading_client:
            return
        try:
            acct = self.trading_client.get_account()
            equity = float(acct.equity)
            last_equity = float(acct.last_equity) if acct.last_equity else equity
            self.account_info = {
                "equity": equity,
                "cash": float(acct.cash),
                "buying_power": float(acct.buying_power),
                "daily_pnl": equity - last_equity,
                "last_equity": last_equity,
            }
        except Exception:
            pass
        _now = time.time()
        if (_now - self._last_positions_fetch) >= 10:
            try:
                raw = self.trading_client.get_all_positions()
                self._last_positions_fetch = _now
                self.positions = {}
                for p in raw:
                    self.positions[p.symbol] = normalize_position(
                        p,
                        meta=self.position_meta.get(p.symbol, {}),
                        source="ALPACA_API",
                        trading_client=self.trading_client,
                    )
            except Exception:
                self.positions = {}
                self._last_positions_fetch = _now
        self._refresh_position_meta()

    # ── panels ────────────────────────────────────────────────────────────────

    def _panel_header(self) -> Panel:
        now = datetime.now()
        mkt_open, mkt_label = self._market_status()
        self._refresh_heartbeat()
        daemon_status, daemon_style = self._daemon_status()
        mkt_style = (
            "bold green" if mkt_open
            else "yellow" if mkt_label in ("PRE-MKT", "AFTER-HRS")
            else "bold red"
        )
        sniper = self.strategy_status.get("sniper", "LOADING")
        remora = self.strategy_status.get("remora", "LOADING")

        grid = Table.grid(expand=True)
        grid.add_column(ratio=4)
        grid.add_column(ratio=2, justify="right")

        left = Text()
        left.append("COMMAND CENTER", style="bold white")
        left.append(" V3", style="bold cyan")
        left.append("   MKT:", style="bold")
        left.append(f" {mkt_label}", style=mkt_style)
        if self.triple_active and self.triple_count > 0:
            left.append(f"   💎 TRIPLE ×{self.triple_count}", style="bold yellow on red")
        elif self.pre_signal_overlap > 0:
            left.append(f"   🔎 OVR {self.pre_signal_overlap}", style="yellow")

        right = Text()
        right.append(now.strftime("%a %b %d"), style="white")
        right.append(" | ", style="dim")
        right.append(now.strftime("%I:%M:%S %p"), style="bold cyan")

        grid.add_row(left, right)

        status = Text()
        pos_count = len(self.positions)
        opp_count = len(self.opportunities)
        total_pnl = sum(self._safe_float(p.get("pnl")) for p in self.positions.values()) if self.positions else 0.0
        pnl_style = "green" if total_pnl >= 0 else "red"
        regime = (self.cached_regime or {}).get("regime", "—")
        vix = (self.cached_regime or {}).get("vix_level", "—")
        regime_style = "green" if "BULL" in str(regime).upper() or "RISK_ON" in str(regime).upper() else "red" if "BEAR" in str(regime).upper() or "RISK_OFF" in str(regime).upper() else "cyan"

        status.append("MKT: ", style="bold")
        status.append(mkt_label, style=mkt_style)
        status.append("   SYS: ", style="bold")
        status.append(daemon_status, style=daemon_style)
        status.append("   POS: ", style="bold")
        status.append(str(pos_count), style="white")
        status.append("   P/L: ", style="bold")
        status.append(f"${total_pnl:+,.0f}", style=pnl_style)
        status.append("   OPP: ", style="bold")
        status.append(str(opp_count), style="white")
        self._refresh_risk_gate_snapshot()
        port_mode = str(self._risk_gate_snapshot.get("portfolio_mode") or "UNKNOWN")
        port_style = (
            "bold red" if port_mode in ("DEFENSIVE", "CONSERVATIVE")
            else "bold yellow" if port_mode == "FEAR_OPPORTUNITY"
            else "green" if port_mode == "AGGRESSIVE"
            else "cyan"
        )
        status.append("   PORT: ", style="bold")
        status.append(port_mode, style=port_style)
        status.append("   REGIME: ", style="bold")
        status.append(str(regime), style=f"bold {regime_style}")
        status.append("   VIX: ", style="bold")
        status.append(f"{float(vix):.1f}" if isinstance(vix, (int, float)) else "—", style="white")
        self._refresh_policy_snapshot()
        policy = self._policy_snapshot or {}
        short_mode = "LIVE" if policy.get("short_live_enabled", True) else "SHADOW"
        short_style = "green" if short_mode == "LIVE" else "bold yellow"
        breadth_pct = policy.get("voyager_breadth_pct")
        breadth_mode = str(policy.get("voyager_breadth_mode") or "UNKNOWN")
        breadth_style = (
            "bold red" if breadth_mode == "SUPPRESSED"
            else "bold yellow" if breadth_mode == "HALF_SIZE"
            else "green" if breadth_mode == "FULL_SIZE"
            else "dim"
        )
        forecast_active = list(policy.get("forecast_active_strategies") or [])
        threshold = int(policy.get("forecast_sample_threshold") or 200)
        status.append("   SHORT: ", style="bold")
        status.append(short_mode, style=short_style)
        status.append("   VOY BR: ", style="bold")
        if breadth_pct is None:
            status.append("n/a", style="dim")
        else:
            status.append(f"{float(breadth_pct):.1f}% {breadth_mode.replace('_', ' ')}", style=breadth_style)
        status.append("   FCST: ", style="bold")
        if forecast_active:
            status.append(
                "LIVE " + ",".join(get_strategy_short_name(s) for s in forecast_active[:3]),
                style="green",
            )
        else:
            status.append(f"OFF <{threshold}", style="yellow")

        grid.add_row(status, "")
        self._refresh_trend_snapshot()
        trend = compact_trend_labels(
            self._trend_snapshot,
            current_vix=self._safe_float(vix) if isinstance(vix, (int, float)) else None,
            current_breadth=breadth_pct,
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
        grid.add_row(trend_row, "")
        return Panel(grid, border_style="blue", padding=(0, 1))

    def _panel_market_health(self) -> Panel:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("K", style="bold", width=10)
        table.add_column("V")

        if not self.cached_regime or not self.cached_breadth:
            table.add_row("Status", Text("Loading institutional model...", style="dim"))
            return Panel(table, title="[bold white]MARKET HEALTH", border_style="cyan")

        try:
            health = self.cached_breadth.get("health_score", 0)
            table.add_row(
                "Health",
                Text(
                    f"{'Strong' if health > 60 else 'Mixed' if health > 40 else 'Weak'} ({health}/100)",
                    style="green" if health > 60 else "yellow" if health > 40 else "red",
                ),
            )
            table.add_row("Phase", str(self.cached_regime.get("phase", "—")).title())

            trend = self.cached_regime.get("trend_strength", 0)
            table.add_row(
                "Trend",
                Text(f"{trend}/100", style="green" if trend > 70 else "yellow" if trend > 40 else "red"),
            )

            adv = self.cached_breadth.get("advance_decline", {}).get("pct_advancing", 0)
            breadth_hint = "Most stocks rising" if adv > 60 else "Mixed — be selective" if adv > 40 else "Most stocks falling"
            table.add_row(
                "Breadth",
                Text(f"{adv:.0f}% advancing — {breadth_hint}", style="green" if adv > 60 else "yellow" if adv > 40 else "red"),
            )
            self._refresh_policy_snapshot()
            policy = self._policy_snapshot or {}
            breadth_pct = policy.get("voyager_breadth_pct")
            breadth_mode = str(policy.get("voyager_breadth_mode") or "UNKNOWN")
            if breadth_pct is None:
                breadth_gate = Text("n/a", style="dim")
            else:
                breadth_gate = Text(
                    f"{float(breadth_pct):.1f}% → {breadth_mode.replace('_', ' ')}",
                    style=(
                        "bold red" if breadth_mode == "SUPPRESSED"
                        else "bold yellow" if breadth_mode == "HALF_SIZE"
                        else "green"
                    ),
                )
            table.add_row("VOY Gate", breadth_gate)

            spy50 = self.cached_regime.get("spy_vs_50ma", 0)
            spy200 = self.cached_regime.get("spy_vs_200ma", 0)
            if spy50 > 0 and spy200 > 0:
                spy_txt = Text(f"50+200MA ({spy50:+.1f}%) Above all trends", style="green")
            elif spy200 > 0:
                spy_txt = Text(f"200MA ({spy200:+.1f}%) Holding long-term", style="yellow")
            else:
                spy_txt = Text("Below MAs — Downtrend", style="red")
            table.add_row("SPY", spy_txt)

            vix_level = self.cached_regime.get("vix_level", 0)
            if vix_level > 0:
                if vix_level >= 30:
                    vix_desc = f"{vix_level:.1f} — Extreme fear, reduce size"
                    vix_style = "bold red"
                elif vix_level >= 20:
                    vix_desc = f"{vix_level:.1f} — Elevated fear, be selective"
                    vix_style = "yellow"
                elif vix_level >= 15:
                    vix_desc = f"{vix_level:.1f} — Normal market conditions"
                    vix_style = "white"
                else:
                    vix_desc = f"{vix_level:.1f} — Calm, good for risk-on"
                    vix_style = "green"
                table.add_row("VIX", Text(vix_desc, style=vix_style))
        except Exception:
            table.add_row("Error", Text("Parse error", style="dim"))

        return Panel(table, title="[bold white]MARKET HEALTH", border_style="cyan")

    def _panel_money_flow(self) -> Panel:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("K", style="bold", width=10)
        table.add_column("V")

        if not self.cached_rotation:
            table.add_row("Status", Text("Loading sector model...", style="dim"))
            return Panel(table, title="[bold white]MONEY FLOW", border_style="magenta")

        try:
            flow = self.cached_rotation.get("money_flow", {}).get("primary_flow", "MIXED")
            _FLOW_MAP = {
                "INTO_OFFENSE": ("AGGRESSIVE", "green"),
                "INTO_DEFENSE": ("DEFENSIVE", "red"),
                "INTO_CYCLICALS": ("CYCLICAL", "yellow"),
                "MIXED": ("MIXED", "yellow"),
            }
            flow_label, flow_style = _FLOW_MAP.get(flow, (flow, "yellow"))
            table.add_row("Flow", Text(flow_label, style=flow_style))

            pattern = self.cached_rotation.get("rotation_pattern", "MIXED")
            p_style = "green" if pattern == "RISK_ON" else "red" if pattern in ("RISK_OFF", "LATE_CYCLE") else "yellow"
            table.add_row("Pattern", Text(pattern.replace("_", " "), style=p_style))

            strength = self.cached_rotation.get("rotation_strength", 0)
            if strength > 0:
                s_style = "green" if strength > 2 else "yellow"
                table.add_row("Strength", Text(f"{strength:.1f}% spread — {'Strong' if strength > 2 else 'Weak'} signal", style=s_style))

            for leader in self.cached_rotation.get("leaders", [])[:2]:
                table.add_row(
                    f"↑{leader.get('ticker', '?')}",
                    f"{leader.get('name', '')[:14]} ({leader.get('relative', 0):+.1f}%)",
                )
            for lag in self.cached_rotation.get("laggards", [])[:2]:
                table.add_row(
                    f"↓{lag.get('ticker', '?')}",
                    Text(f"{lag.get('name', '')[:14]} ({lag.get('relative', 0):+.1f}%)", style="dim"),
                )
        except Exception:
            table.add_row("Error", Text("Parse error", style="dim"))

        return Panel(table, title="[bold white]MONEY FLOW", border_style="magenta")

    def _panel_smart_money(self) -> Panel:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("K", style="bold", width=10)
        table.add_column("V")

        if not self.cached_options or not self.cached_inter:
            table.add_row("Status", Text("Loading smart money...", style="dim"))
            return Panel(table, title="[bold white]SMART MONEY", border_style="yellow")

        try:
            pc_block = (
                self.cached_options.get("spy_put_call")
                or self.cached_options.get("put_call")
                or {}
            )
            pc_raw = pc_block.get("ratio")
            if pc_raw is None:
                pc_raw = pc_block.get("pcr_volume")
            pc = self._safe_float(pc_raw, default=1.0)
            pc_style = "green" if pc < 0.8 else "red" if pc > 1.2 else "yellow"
            fear = "Low fear" if pc < 0.8 else "Neutral" if pc < 1.2 else "High fear"
            table.add_row("P/C", Text(f"{pc:.2f} {fear}", style=pc_style))

            gamma = self.cached_options.get("gamma_exposure", {}).get("regime", "NEUTRAL")
            gamma_hint = {
                "POSITIVE": "POSITIVE — dealers buy dips (stabilizing)",
                "NEGATIVE": "NEGATIVE — dealers amplify moves (volatile)",
                "NEUTRAL": "NEUTRAL — normal dealer hedging",
            }.get(gamma, gamma)
            g_style = "green" if gamma == "POSITIVE" else "red" if gamma == "NEGATIVE" else "yellow"
            table.add_row("Gamma", Text(gamma_hint, style=g_style))

            macro_raw = self.cached_inter.get("macro_regime", {})
            macro = macro_raw.get("regime", "MIXED") if isinstance(macro_raw, dict) else str(macro_raw or "MIXED")
            m_style = "green" if macro == "RISK_ON" else "red" if macro in ("RISK_OFF", "CRISIS") else "yellow"
            table.add_row("Global", Text(macro.replace("_", "-"), style=m_style))

            signal_raw = self.cached_options.get("signal", {})
            action = signal_raw.get("action", "") if isinstance(signal_raw, dict) else str(signal_raw or "")
            if action:
                a_style = "green" if "DEPLOY" in action else "red" if "REDUCE" in action else "yellow"
                table.add_row("Signal", Text(action.replace("_", " "), style=a_style))
        except Exception:
            table.add_row("Error", Text("Parse error", style="dim"))

        # ── Scan quality (Phase 4) ───────────────────────────────────────────
        sq = self._refresh_scan_quality()
        table.add_row("──────────", Text("── SCAN QUALITY ──", style="dim"))
        if not sq:
            table.add_row("Quality", Text("no report yet", style="dim"))
        else:
            sq_funnel   = sq.get("funnel", {})
            sq_scanned  = sq_funnel.get("total_scanned", 0)
            sq_approved = sq_funnel.get("total_approved", 0)
            sq_rate     = sq_funnel.get("approval_rate_pct", 0.0)
            sq_days     = sq.get("window_days", "?")
            rate_style  = "green" if sq_rate >= 10 else "yellow" if sq_rate >= 3 else "red"
            table.add_row(
                f"Funnel({sq_days}d)",
                Text(f"{sq_approved}/{sq_scanned} ({sq_rate:.0f}%)", style=rate_style),
            )
            top_reasons = sq.get("top_reject_reasons", [])
            if top_reasons:
                top = top_reasons[0]
                top_label = self._humanize_reject_reason(top.get("reason"), max_len=24)
                table.add_row(
                    "Top Reject",
                    Text(f"{top_label}  n={top.get('count', 0)}", style="yellow"),
                )
            stability = sq.get("reason_stability", {})
            if stability.get("stable"):
                stable_label = self._humanize_reject_reason(
                    stability.get("dominant_reason"),
                    max_len=20,
                )
                table.add_row(
                    "Pattern",
                    Text(f"⚠ {stable_label} x{stability.get('consecutive_runs', 0)}", style="bold red"),
                )
            gate_overlap = sq.get("gate_overlap", {})
            combos = gate_overlap.get("top_combos", []) if isinstance(gate_overlap, dict) else []
            if combos:
                top_combo = str(combos[0].get("combo") or "").replace("+", " + ").upper()
                top_pct = float(combos[0].get("pct_of_tagged", 0.0) or 0.0)
                table.add_row(
                    "Gate Mix",
                    Text(f"{top_combo}  {top_pct:.0f}% tagged", style="magenta"),
                )
            bte = sq.get("bte_advisory", {}) if isinstance(sq, dict) else {}
            if bte:
                top_bucket = bte.get("top_score_bucket") or "—"
                top_window = bte.get("top_score_bucket_window") or "—"
                top_lift = bte.get("top_score_bucket_lift")
                lift_txt = f"x{float(top_lift):.2f}" if top_lift is not None else "—"
                table.add_row("BTE", Text(f"{top_bucket}  {lift_txt}  {top_window}", style="cyan"))
            bte_short = sq.get("bte_short_advisory", {}) if isinstance(sq, dict) else {}
            if bte_short:
                top_bucket = bte_short.get("top_score_bucket") or "—"
                top_window = bte_short.get("top_score_bucket_window") or "—"
                top_lift = bte_short.get("top_score_bucket_lift")
                lift_txt = f"x{float(top_lift):.2f}" if top_lift is not None else "—"
                table.add_row("BTE-S", Text(f"{top_bucket}  {lift_txt}  {top_window}", style="cyan"))

        # ── RR shadow calibration compact line (Phase 4.1, read-only) ───────
        rr = self._refresh_rr_shadow()
        compact = rr.get("compact_summary", []) if rr else []
        if compact:
            parts = [
                f"{item['strategy'][:3]} +{item['recovery_count']}@{item['best_alt_threshold']}"
                for item in compact[:3]
            ]
            table.add_row("RR Shadow", Text("  |  ".join(parts), style="cyan"))
        else:
            table.add_row("RR Shadow", Text("no data — run --with-rr-shadow", style="dim"))

        # ── Shadow Win% (Phase 4.2, read-only) ──────────────────────────────
        so = self._refresh_shadow_outcomes()
        so_parts = []
        for pname, pdata in sorted(so.items()):
            done = pdata.get("completed_outcomes", 0)
            tgt_pct = pdata.get("target_hit_pct")
            if done > 0 and tgt_pct is not None:
                short = pname.replace("_", "").upper()[:6]
                so_parts.append(f"{short} {tgt_pct:.0f}%")
        if so_parts:
            table.add_row("Shadow Win%", Text("  |  ".join(so_parts[:3]), style="cyan"))
        else:
            table.add_row("Shadow Win%", Text("accumulating...", style="dim"))

        # ── Pilot framework (Phase 4.3, compact read-only) ──────────────────
        import os as _os
        table.add_row("──────────", Text("── PILOT ──", style="dim"))
        pilot_enabled = _PILOT_AVAILABLE and _os.getenv("PILOT_ENABLE", "0").strip() in ("1", "true", "yes", "on")
        pilot_policies = _os.getenv("PILOT_ALLOWED_POLICIES", "SHORT_2_5,SNIPER_2_2")
        p_status = Text("ON", style="bold green") if pilot_enabled else Text("OFF", style="dim")
        table.add_row("Status", Text.assemble(p_status, f"  Policy: {pilot_policies}"))
        if pilot_enabled:
            pd = self._refresh_pilot()
            exec_n = pd.get("executed", 0)
            blocked_n = pd.get("blocked", 0)
            table.add_row(
                "Today",
                Text(f"{exec_n} exec / {blocked_n} blocked    PnL: —", style="cyan"),
            )
        else:
            table.add_row("Today", Text("0 exec / 0 blocked    PnL: —", style="dim"))

        return Panel(table, title="[bold white]SMART MONEY", border_style="yellow")

    def _panel_opportunities(self) -> Panel:
        self._refresh_risk_gate_snapshot()
        payload = self._refresh_action_snapshot()

        def _bucket_table(title: str, rows: List[Dict[str, object]], *, empty: str, style: str) -> Table:
            table = Table(show_header=True, box=None, padding=(0, 1), expand=True)
            table.add_column(title, width=6, style="bold")
            table.add_column("Tk", width=6, style="bold")
            table.add_column("Str", width=4)
            table.add_column("S", width=4, justify="right")
            table.add_column("RR", width=4, justify="right")
            table.add_column("Detail")
            if not rows:
                table.add_row("", "—", "", "—", "—", Text(empty, style="dim"))
                return table
            for row in rows:
                table.add_row(
                    Text("●", style=style),
                    str(row.get("ticker") or "—"),
                    get_strategy_short_name(row.get("strategy", "")) or "—",
                    f"{float(row.get('score') or 0):.0f}" if float(row.get("score") or 0) > 0 else "—",
                    f"{float(row.get('rr') or 0):.1f}" if float(row.get("rr") or 0) > 0 else "—",
                    str(row.get("detail") or "—")[:24],
                )
            return table

        blocked_rows = payload.get("blocked", [])
        blocked = Table(show_header=True, box=None, padding=(0, 1), expand=True)
        blocked.add_column("Blk", width=6, style="bold")
        blocked.add_column("Tk", width=6, style="bold")
        blocked.add_column("Str", width=4)
        blocked.add_column("Why")
        if not blocked_rows:
            blocked.add_row("", "—", "", Text("No live blocks", style="dim"))
        else:
            for row in blocked_rows:
                blocked.add_row(
                    Text("●", style="bold red"),
                    str(row.get("ticker") or "—"),
                    get_strategy_short_name(row.get("strategy", "")) or "—",
                    compact_reason(row.get("reason"), max_len=26),
                )

        delta = Text()
        if self._action_delta_rows:
            for idx, row in enumerate(self._action_delta_rows):
                if idx:
                    delta.append("  ")
                kind = row.get("kind", "—")
                style = {"NEW": "bold green", "UP": "green", "DOWN": "yellow", "DROP": "dim red"}.get(kind, "dim")
                delta.append(f"{kind} ", style=style)
                delta.append(str(row.get("label") or "—"), style="white")
        else:
            delta.append("No material change since last scan", style="dim")

        gate_counts = self._risk_gate_snapshot.get("gate_counts", {}) or {}
        subtitle = None
        if gate_counts:
            order = ["Risk", "Portfolio", "Concentration", "Execution", "Other"]
            subtitle = "Denied 24h: " + "  ".join(
                f"{name}:{gate_counts[name]}" for name in order if gate_counts.get(name)
            )
        return Panel(
            Group(
                _bucket_table("Ready", payload.get("ready", []), empty="No executable names", style="bold green"),
                _bucket_table("Watch", payload.get("watch", []), empty="No live watch names", style="bold yellow"),
                blocked,
                Text.assemble(("Δ ", "bold"), delta),
            ),
            title="[bold white]ACTION BOARD",
            subtitle=subtitle,
            border_style="green",
        )

    def _panel_strategy_positions(self) -> Panel:
        """
        Live positions with stop/target/protection.
        Strategy states appended below position rows.
        Field integrity: same rules as live_dashboard_v3.
        """
        _STRAT_COLORS = {
            "VOY": "blue",
            "SNP": "magenta",
            "REM": "cyan",
            "SHRT": "red",
            "RPR": "yellow",
        }

        table = Table(show_header=True, box=rich_box.SIMPLE_HEAD, padding=(0, 1), expand=True)
        table.add_column("Ticker", width=8, style="bold")
        table.add_column("Strat", width=7)
        table.add_column("Dir", width=9)
        table.add_column("P/L", justify="right", width=14)
        table.add_column("Stop", justify="right", width=7)
        table.add_column("Tgt", justify="right", width=7)
        table.add_column("Prot", justify="center", width=6)

        if not self.positions:
            table.add_row(Text("NONE", style="dim"), "", "", Text("No open setups", style="dim"), "", "", "")
        else:
            for ticker, pos in sorted(
                self.positions.items(), key=lambda x: x[1].get("pnl", 0), reverse=True
            )[:3]:
                meta = self.position_meta.get(ticker, {})

                strategy_raw = pos.get("strategy") or meta.get("strategy")
                strat_code = self._normalize_strategy(strategy_raw)
                internal = {
                    "VOY": "VOYAGER",
                    "SNP": "SNIPER",
                    "REM": "REMORA",
                    "SHRT": "SHORT",
                    "RPR": "CONTRARIAN",
                }.get(strat_code, strat_code)
                strat_txt = Text(get_strategy_short_name(internal), style=_STRAT_COLORS.get(strat_code, "dim"))

                raw_dir = pos.get("direction") or meta.get("direction")
                if not raw_dir:
                    qty_val = self._safe_float(pos.get("qty"))
                    if qty_val < 0:
                        raw_dir = "SHORT"
                    elif qty_val > 0:
                        raw_dir = "LONG"
                direction = self._dir_normalize(raw_dir)
                if direction == "LONG":
                    dir_txt = Text("LONG", style="green")
                elif direction == "SHORT":
                    dir_txt = Text("SHORT", style="red")
                else:
                    dir_txt = Text("—", style="dim")

                stop_f = self._safe_float(pos.get("stop_loss") or meta.get("stop_loss"))
                tgt_f = self._safe_float(pos.get("target_price") or meta.get("target_price"))
                has_stop = stop_f > 0
                has_tgt = tgt_f > 0
                bracket = bool(meta.get("bracket_attached"))
                no_meta_flag = not meta and not has_stop and not has_tgt

                prot_label, prot_style = self._prot_label(bracket, has_stop, has_tgt, no_meta=no_meta_flag)
                pnl_pct = self._safe_float(pos.get("pnl_pct"))

                table.add_row(
                    ticker,
                    strat_txt,
                    dir_txt,
                    Text(f"${self._safe_float(pos.get('pnl')):+,.0f} ({pnl_pct:+.1f}%)", style="green" if pnl_pct >= 0 else "red"),
                    f"{stop_f:.2f}" if has_stop else "—",
                    f"{tgt_f:.2f}" if has_tgt else "—",
                    Text(prot_label, style=prot_style),
                )

        # Strategy status board — all 5 strategies with timeframe context
        _strategy_board = [
            # (retail_name,      label_color, status_key, timeframe,     status_color_map)
            ("Growth",        "blue",    "voyager", "6-18m",
             {"ACTIVE": "bold green", "STANDBY": "bold red"}),
            ("Breakout",      "magenta", "sniper",  "3-30d",
             {"ACTIVE": "bold green", "STANDBY": "bold red"}),
            ("Catalyst",      "cyan",    "remora",  "2-48h",
             {"HUNTING": "bold green", "ACTIVE": "bold green", "STANDBY": "bold red"}),
            ("Short",         "red",     "short",   "6-12w",
             {"ACTIVE": "bold green", "STANDBY": "bold red"}),
            ("Reaper",        "yellow",  "contrarian", "2-10d",
             {"HUNTING": "bold green", "ACTIVE": "bold green", "ARMED": "yellow", "STANDBY": "bold red"}),
        ]
        for retail_name, label_color, key, timeframe, color_map in _strategy_board:
            val = self.strategy_status.get(key, "LOADING")
            if key == "short":
                self._refresh_policy_snapshot()
                policy = self._policy_snapshot or {}
                if not policy.get("short_live_enabled", True):
                    val = "SHADOW"
            c = color_map.get(val, "yellow")
            timeframe_txt = timeframe
            if key == "contrarian" and val == "STANDBY":
                timeframe_txt = "WAIT VIX>28"
            if key == "short" and val == "SHADOW":
                c = "bold yellow"
                timeframe_txt = "scan/log only"
            table.add_row(
                Text(retail_name, style=label_color),
                Text(val, style=c),
                Text(timeframe_txt, style="dim"),
                "", "", "", "",
            )

        self._refresh_policy_snapshot()
        policy = self._policy_snapshot or {}
        breadth_pct = policy.get("voyager_breadth_pct")
        breadth_mode = str(policy.get("voyager_breadth_mode") or "UNKNOWN").replace("_", " ")
        threshold = int(policy.get("forecast_sample_threshold") or 200)
        counts = policy.get("closed_trade_counts", {}) or {}
        forecast_active = list(policy.get("forecast_active_strategies") or [])
        breadth_note = "n/a" if breadth_pct is None else f"{float(breadth_pct):.1f}%  {breadth_mode}"
        table.add_row(Text("VOY Breadth", style="dim"), Text(breadth_note, style="cyan"), "", "", "", "", "")
        if forecast_active:
            forecast_note = (
                f"{policy.get('forecast_model_version', 'shadow')} live: "
                f"{','.join(get_strategy_short_name(s) for s in forecast_active)}"
            )
            forecast_style = "green"
        else:
            forecast_note = (
                f"{policy.get('forecast_model_version', 'shadow')} off <{threshold} "
                f"(VOY {counts.get('VOYAGER', 0)} SHRT {counts.get('SHORT', 0)})"
            )
            forecast_style = "yellow"
        table.add_row(Text("Forecast", style="dim"), Text(forecast_note, style=forecast_style), "", "", "", "", "")

        self._refresh_risk_gate_snapshot()
        port_mode = str(self._risk_gate_snapshot.get("portfolio_mode") or "UNKNOWN")
        circuit = bool(self._risk_gate_snapshot.get("circuit_breaker", False))
        subtitle = f"Portfolio: {port_mode}"
        if circuit:
            subtitle += "  |  CIRCUIT BREAKER"
        return Panel(table, title="[bold white]STRATEGY / POSITIONS", subtitle=subtitle, border_style="cyan")

    def _panel_catalysts(self) -> Panel:
        table = Table(show_header=True, box=rich_box.SIMPLE_HEAD, padding=(0, 1), expand=True)
        table.add_column("Event", width=18, style="bold")
        table.add_column("Impact", justify="center", width=8)
        table.add_column("When", justify="right", width=8)
        table.add_column("Gate", justify="center", width=10)

        # Get current macro window state once (time-window aware)
        macro_state = None
        if self.macro_cal:
            try:
                macro_state = self.macro_cal.get_macro_window_state()
            except Exception:
                macro_state = None

        cur_window = macro_state.get('window', 'CLEAR') if macro_state else 'CLEAR'
        cur_event_name = macro_state.get('event_name', '') if macro_state else ''

        _window_gate = {
            'EVENT_LOCKOUT': ('LOCKOUT',    'bold red'),
            'PRE_EVENT':     ('CAUTION',    'bold yellow'),
            'POST_EVENT':    ('STABILIZING','yellow'),
        }

        events = self.events_cache or []
        if not events:
            table.add_row("None scheduled", "—", "—", Text("CLEAR", style="green"))
        else:
            for ev in events[:5]:
                impact = str(ev.get("impact", "LOW")).upper()
                ev_name = str(ev.get("event", ""))

                # Style impact
                if impact == "CRITICAL":
                    imp_txt = Text(impact, style="bold red")
                elif impact == "HIGH":
                    imp_txt = Text(impact, style="bold yellow")
                elif impact == "MEDIUM":
                    imp_txt = Text(impact, style="yellow")
                else:
                    imp_txt = Text(impact, style="green")

                # Determine gate label based on actual window state
                if cur_window != 'CLEAR' and ev_name == cur_event_name:
                    # This event is currently in an active window
                    label, style = _window_gate.get(cur_window, ('ACTIVE', 'bold red'))
                    gate_txt = Text(label, style=style)
                elif impact in ("CRITICAL", "HIGH"):
                    days_away = ev.get('days_away', 999)
                    if days_away == 0:
                        # Today but outside window — still worth noting
                        gate_txt = Text("TODAY", style="bold yellow")
                    elif days_away <= 2:
                        gate_txt = Text("WATCH", style="yellow")
                    else:
                        gate_txt = Text("AHEAD", style="dim")
                else:
                    gate_txt = Text("CLEAR", style="green")

                table.add_row(
                    ev_name[:18],
                    imp_txt,
                    str(ev.get("countdown", "—")),
                    gate_txt,
                )

        return Panel(table, title="[bold white]UPCOMING CATALYSTS", border_style="yellow")

    def _panel_trading_signal(self) -> Panel:
        """
        Consolidated risk-on / risk-off vote from all real signal sources.
        No synthetic inputs. Each source counted once.
        """
        risk_on = 0
        risk_off = 0
        votes: list = []  # (label, value_str, style)

        # 1. Market Regime
        if self.cached_regime:
            regime_str = self.cached_regime.get("regime", "—")
            if "BEAR" in regime_str or self.cached_regime.get("risk_state") == "RISK_OFF":
                risk_off += 1
                votes.append(("Regime", regime_str[:12], "red"))
            elif "BULL" in regime_str or self.cached_regime.get("risk_state") == "RISK_ON":
                risk_on += 1
                votes.append(("Regime", regime_str[:12], "green"))
            else:
                votes.append(("Regime", regime_str[:12], "yellow"))

        # 2. Sector Rotation — LATE_CYCLE = risk-off (commodities bid, growth fading)
        if self.cached_rotation:
            rp = self.cached_rotation.get("rotation_pattern", "MIXED")
            if rp in ("RISK_OFF", "LATE_CYCLE"):
                risk_off += 1
                votes.append(("Rotation", rp.replace("_", "-")[:12], "red"))
            elif rp in ("RISK_ON", "EARLY_CYCLE"):
                risk_on += 1
                votes.append(("Rotation", rp.replace("_", "-")[:12], "green"))
            else:
                votes.append(("Rotation", rp.replace("_", "-")[:12], "yellow"))

        # 3. Market Breadth
        if self.cached_breadth:
            hs = self.cached_breadth.get("health_score", 50)
            if hs < 50:
                risk_off += 1
                votes.append(("Breadth", f"{hs:.0f}/100", "red"))
            elif hs > 60:
                risk_on += 1
                votes.append(("Breadth", f"{hs:.0f}/100", "green"))
            else:
                votes.append(("Breadth", f"{hs:.0f}/100", "yellow"))

        # 4. Options Positioning
        if self.cached_options:
            signal_raw = self.cached_options.get("signal", {})
            action = signal_raw.get("action", "") if isinstance(signal_raw, dict) else str(signal_raw or "")
            if action in ("REDUCE_RISK", "WATCH_FOR_PULLBACK"):
                risk_off += 1
                votes.append(("Options", action.replace("_", " ")[:12], "red"))
            elif action in ("DEPLOY_CAPITAL", "WATCH_FOR_BOUNCE"):
                risk_on += 1
                votes.append(("Options", action.replace("_", " ")[:12], "green"))
            elif action:
                votes.append(("Options", action.replace("_", " ")[:12], "yellow"))

        # 5. Inter-Market Macro
        if self.cached_inter:
            macro_raw = self.cached_inter.get("macro_regime", {})
            reg = macro_raw.get("regime", "MIXED") if isinstance(macro_raw, dict) else str(macro_raw or "MIXED")
            if reg in ("RISK_OFF", "CRISIS", "STAGFLATION"):
                risk_off += 1
                votes.append(("Inter-Mkt", reg.replace("_", "-")[:12], "red"))
            elif reg == "RISK_ON":
                risk_on += 1
                votes.append(("Inter-Mkt", reg.replace("_", "-")[:12], "green"))
            else:
                votes.append(("Inter-Mkt", reg.replace("_", "-")[:12], "yellow"))

        if risk_off >= 4:
            badge = Text("  STAND ASIDE  ", style="bold white on red")
            detail = "Risk elevated across multiple signals. Preserve capital."
        elif risk_off >= 3:
            badge = Text("  BE CAREFUL  ", style="bold black on yellow")
            detail = "Mixed-to-weak tape. Only highest quality setups."
        elif risk_on >= 4:
            badge = Text("  PULL THE TRIGGER  ", style="bold white on green")
            detail = "Favorable regime and multi-signal alignment."
        elif risk_on >= 3:
            badge = Text("  SELECTIVE SETUPS  ", style="bold white on green")
            detail = "Constructive. Focus on leaders and A+ entries only."
        else:
            badge = Text("  WAIT FOR CLARITY  ", style="bold black on yellow")
            detail = "Signals mixed. No edge in forcing trades."

        grid = Table.grid(expand=True)
        grid.add_column(ratio=2)
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)

        score_txt = Text()
        score_txt.append(f"Risk-On: {risk_on}/5", style="green")
        score_txt.append("   |   ", style="dim")
        score_txt.append(f"Risk-Off: {risk_off}/5", style="red")

        vote_cells = []
        for label, val, vstyle in votes:
            cell = Text()
            cell.append(f"{label}: ", style="dim")
            cell.append(val, style=vstyle)
            vote_cells.append(cell)

        # Pad to 5 columns
        while len(vote_cells) < 5:
            vote_cells.append(Text(""))

        grid.add_row(badge, *vote_cells)
        grid.add_row(Text(detail, style="dim"), score_txt, "", "", "", "")

        return Panel(grid, title="[bold white]TRADING SIGNAL", border_style="white", padding=(0, 1))

    def _panel_footer(self) -> Panel:
        log_name = os.path.basename(self.log_file) if self.log_file else "no log attached"
        txt = Text()
        txt.append("LOG: ", style="dim")
        txt.append(log_name, style="white")
        txt.append("  •  ", style="dim")
        txt.append(f"SRC AGE: {self._format_age(self.last_log_update)}", style="white")
        txt.append("  •  ", style="dim")
        txt.append(f"SCAN AGE: {self._format_age(self.last_scan_time)}", style="white")
        txt.append("  •  REFRESH: 4s  •  CTRL+C TO EXIT", style="dim")
        return Panel(txt, border_style="dim", padding=(0, 1))

    # ── layout ────────────────────────────────────────────────────────────────

    def build_layout(self) -> Layout:
        """
        Clean layout — real data panels only.

        header   4    time | market status | strategy states | confluence
        row1    10    market_health(3) | money_flow(3) | smart_money(2)
        row2    12    opportunities(2) | strategy/positions(3) | catalysts(2)
        signal   5    trading signal (full width)
        footer   3
        """
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=4),
            Layout(name="row1",   size=10),
            Layout(name="row2",   size=12),
            Layout(name="signal", size=5),
            Layout(name="footer", size=3),
        )

        layout["header"].update(self._panel_header())

        layout["row1"].split_row(
            Layout(self._panel_market_health(), ratio=3),
            Layout(self._panel_money_flow(),    ratio=3),
            Layout(self._panel_smart_money(),   ratio=2),
        )

        layout["row2"].split_row(
            Layout(self._panel_opportunities(),         ratio=2),
            Layout(self._panel_strategy_positions(),    ratio=3),
            Layout(self._panel_catalysts(),             ratio=2),
        )

        layout["signal"].update(self._panel_trading_signal())
        layout["footer"].update(self._panel_footer())
        return layout

    # ── run ───────────────────────────────────────────────────────────────────

    def run(self, refresh_seconds: int = 2):
        # Initial load — slow institutional calls go to background threads
        self.parse_log_file()
        self.refresh_events()
        self.refresh_positions()
        threading.Thread(target=self.refresh_market_data, daemon=True).start()

        try:
            with Live(
                self.build_layout(),
                refresh_per_second=0.5,
                console=self.console,
                screen=True,
            ) as live:
                while True:
                    self.parse_log_file()
                    # Slow institutional data (60s internal gate) runs off the UI thread
                    threading.Thread(target=self.refresh_market_data, daemon=True).start()
                    self.refresh_events()   # 300s internal gate — fast when throttled
                    self.refresh_positions()
                    live.update(self.build_layout())
                    time.sleep(refresh_seconds)
        except KeyboardInterrupt:
            self.console.clear()
            self.console.print("\n[white]Dashboard stopped.[/white]\n")


# ── entrypoint ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Command Center Terminal V3")
    parser.add_argument("--log", default=None, help="Path to trader log file")
    parser.add_argument("--refresh", type=int, default=4, help="Refresh interval in seconds")
    args = parser.parse_args()

    if not RICH_AVAILABLE:
        print("\n❌  rich is required: pip install rich\n")
        return

    try:
        term = CommandCenterV3(log_file=args.log)
        term.run(refresh_seconds=args.refresh)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\n❌  Startup error: {e}\n")
        raise


if __name__ == "__main__":
    main()
