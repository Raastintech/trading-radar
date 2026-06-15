"""
Chief Strategist Terminal

Institutional command center — all data backed by real feeds.
Panels removed if data was fake/hardcoded/arithmetic-on-scan-count.

Real data sources:
  Market Regime     : Alpaca SPY bars + yfinance VIX
  Sector Rotation   : 9 sector ETFs via Alpaca
  Market Breadth    : Alpaca breadth universe with stock/sector composite
  Options           : SPY P/C ratio, gamma, max pain via Alpaca
  Inter-Market      : SPY/DXY/TLT/GLD/USO via Alpaca
  Macro Calendar    : Hardcoded economic event schedule
  Strategy Status   : Log parsing (Voyager/Sniper/Remora)
  Portfolio         : Alpaca account (equity, cash, daily P/L)
  Positions         : Alpaca open positions
  Executions        : Log parsing (executed trade lines)
  Confluence        : Log parsing (triple confluence lines)
  Consensus         : Derived from all 5 institutional signals

Removed (fake/cosmetic):
  - Market Microstructure : bid/ask from offsets, tape from static list
  - Options Flow Intel    : call sweep + dark pool print hardcoded
  - Alpha Feed            : analyst upgrades + social sentiment all fake
  - AI Intelligence Center: confidence/win/move = scan_count%N (not ML)
  - Performance Attribution: SPY=0.29/QQQ=0.15 hardcoded constants
  - Portfolio Risk Metrics : Sharpe/Sortino/MaxDD = scan_count%N cycling
  - Trade Journal         : fake AAPL/TSLA samples + hardcoded win stats
  - Macro Intelligence    : duplicate of Macro Calendar + wrong Fed dates
"""

import time
import os
import re
import json
import math
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import deque
from secure_env import load_runtime_env

load_runtime_env("chief_strategist_terminal")
from strategy_display import get_strategy_display_name
from strategy_readiness_report import StrategyReadinessReport
from terminal_policy_snapshot import load_terminal_policy_snapshot
from terminal_cockpit import compact_reason, fetch_recent_watch_decisions, percent_meter
from terminal_trend_snapshot import compact_trend_labels, load_terminal_trend_snapshot
from market_breadth_context import (
    DEFAULT_MIN_SYMBOLS_WITH_DATA,
    has_sufficient_market_breadth_coverage,
)
try:
    from edge_analytics import EdgeAnalytics
except ImportError:
    EdgeAnalytics = None  # edge metrics optional

try:
    from scan_diagnostics import ScanDiagnosticsEngine as _ScanDiagEngine
    _SCAN_DIAG_AVAILABLE = True
except ImportError:
    _ScanDiagEngine = None  # type: ignore
    _SCAN_DIAG_AVAILABLE = False

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from market_regime_detector import MarketRegimeDetector
from sector_rotation_tracker import SectorRotationTracker
from market_breadth_monitor import MarketBreadthMonitor
from options_positioning_tracker import OptionsPositioningTracker
from inter_market_monitor import InterMarketMonitor
from macro_calendar import MacroCalendar
from log_selection import select_active_trader_log
from trading_state import normalize_position, read_heartbeat, get_daemon_status


class ChiefStrategistTerminal:
    """
    Chief Strategist Terminal — only real-data panels.
    Broker state throttled to max every 10s to avoid rate limits.
    """
    CONFLUENCE_TTL_SECONDS = 4 * 60 * 60  # expire active confluence after 4h without refresh

    def __init__(self, log_file: str = None):
        if not RICH_AVAILABLE:
            raise ImportError("rich library required: pip install rich")

        self.console = Console()

        print("\n  Initializing Chief Strategist Terminal...")
        print("=" * 70)

        self.regime_detector = MarketRegimeDetector()
        self.rotation_tracker = SectorRotationTracker()
        self.breadth_monitor = MarketBreadthMonitor()
        self.options_tracker = OptionsPositioningTracker()
        self.inter_market = InterMarketMonitor()
        self.macro_cal = MacroCalendar()

        print("=" * 70)
        print("All institutional components loaded\n")

        if not log_file:
            log_file = select_active_trader_log('logs/trader_v3_*.log')
        self.log_file = log_file or 'logs/trader_v3.log'
        self.db_path = "trading_performance.db"
        self.last_position = 0
        self.last_log_update: Optional[datetime] = None
        # Start tailing from EOF to avoid replaying stale historical confluence events.
        try:
            if os.path.exists(self.log_file):
                self.last_position = os.path.getsize(self.log_file)
        except Exception:
            self.last_position = 0

        self.strategy_status: Dict[str, str] = {
            'sniper': 'LOADING',
            'remora': 'LOADING',
            'contrarian': 'LOADING',
            'vix_sizing': '100%',
            'stress_sizing': '100%',
        }

        self.confluence_analytics: Dict = {
            'active': False,
            'count': 0,
            'tickers': [],
            'last_detected': None,
            'detection_time': None,
            'last_detected_ts': 0.0,
            'pre_signal_overlap': 0,
            'last_overlap_time': None,
            'universes': {
                'voyager': {'count': 0, 'last_update': None},
                'sniper':  {'count': 0, 'last_update': None},
                'remora':  {'count': 0, 'last_update': None},
            },
            'total_coverage': 0,
            'unique_coverage': 0,
            'confluence_history': deque(maxlen=10),
        }

        # Alpaca broker client
        self.trading_client = None
        try:
            from alpaca.trading.client import TradingClient
            api_key = os.getenv('ALPACA_API_KEY') or os.getenv('APCA_API_KEY_ID')
            secret_key = os.getenv('ALPACA_SECRET_KEY') or os.getenv('APCA_API_SECRET_KEY')
            if api_key and secret_key:
                self.trading_client = TradingClient(api_key, secret_key, paper=True)
        except Exception:
            self.trading_client = None

        self.positions: Dict[str, Dict] = {}
        self.account_info: Dict[str, float] = {}
        self.executed_trades: List[Dict] = []
        self.pattern_distribution: Dict[str, int] = {}
        self.scan_count = 0
        self.last_scan_dt: Optional[datetime] = None
        self._edge_cache: Dict = {}
        self._edge_cache_ts: float = 0.0

        # Institutional data cache — refreshed every 60s
        self.last_refresh: float = 0.0
        self.cached_regime = None
        self.cached_rotation = None
        self.cached_breadth = None
        self.cached_options = None
        self.cached_inter_market = None

        # Broker state throttle — max every 10s (4 panels called it every 2s before)
        self._last_broker_refresh: float = 0.0
        self._heartbeat_state: Dict = {}
        self._last_heartbeat_read: float = 0.0
        self._risk_gate_snapshot: Dict = {}
        self._last_risk_gate_refresh: float = 0.0
        self._policy_snapshot: Dict = {}
        self._last_policy_snapshot_refresh: float = 0.0
        self._trend_snapshot: Dict = {}
        self._last_trend_snapshot_refresh: float = 0.0
        self._strategy_readiness_snapshot: Dict = {}
        self._last_strategy_readiness_refresh: float = 0.0
        self._sq_cache: Dict = {}
        self._sq_ts: float = 0.0
        self._sq_engine = _ScanDiagEngine() if _SCAN_DIAG_AVAILABLE else None

    # ── helpers ───────────────────────────────────────────────────────────────

    def _now_et(self) -> datetime:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("America/New_York"))
        return datetime.now()

    def _parse_scan_marker_dt(self, raw: str) -> Optional[datetime]:
        if not raw:
            return None
        m = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)\s*ET", str(raw), re.IGNORECASE)
        if not m:
            return None
        try:
            clock = datetime.strptime(m.group(1).upper().replace("  ", " "), "%I:%M %p")
            now_et = self._now_et()
            candidate = now_et.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
            while candidate > (now_et + timedelta(minutes=5)):
                candidate -= timedelta(days=1)
            return candidate
        except Exception:
            return None

    def _format_age(self, dt_obj) -> str:
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
        return f"{mins // 60}h"

    def _refresh_heartbeat(self):
        now = time.time()
        if (now - self._last_heartbeat_read) < 5:
            return
        self._last_heartbeat_read = now
        self._heartbeat_state = read_heartbeat()

    def _daemon_status(self):
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
            "strategy_counts": {},
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
                    "SELECT timestamp, strategy, council_decision, execution_deny_reason "
                    "FROM decisions "
                    "WHERE execution_denied = 1 OR council_decision LIKE '%DENIED%' "
                    "ORDER BY timestamp DESC LIMIT 200"
                )
                cutoff = datetime.now() - timedelta(days=1)
                for ts_raw, strategy, decision, reason in cur.fetchall():
                    try:
                        ts = datetime.fromisoformat(str(ts_raw))
                    except Exception:
                        continue
                    if ts < cutoff:
                        continue
                    bucket = self._classify_denial_bucket(decision, reason)
                    snapshot["gate_counts"][bucket] = snapshot["gate_counts"].get(bucket, 0) + 1
                    key = str(strategy or "UNK").upper()
                    snapshot["strategy_counts"][key] = snapshot["strategy_counts"].get(key, 0) + 1
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
        self._policy_snapshot = load_terminal_policy_snapshot(self.db_path)

    def _refresh_trend_snapshot(self):
        now = time.time()
        if (now - self._last_trend_snapshot_refresh) < 60:
            return
        self._last_trend_snapshot_refresh = now
        self._trend_snapshot = load_terminal_trend_snapshot(self.db_path, "logs", width=10)

    def _refresh_strategy_readiness(self):
        now = time.time()
        if (now - self._last_strategy_readiness_refresh) < 60:
            return
        self._last_strategy_readiness_refresh = now
        try:
            self._strategy_readiness_snapshot = StrategyReadinessReport(
                db_path=self.db_path
            ).build_report(days=30, shadow_horizon_days=5)
        except Exception:
            self._strategy_readiness_snapshot = {}

    def _refresh_scan_quality(self) -> Dict:
        now = time.time()
        sq_ts = float(getattr(self, "_sq_ts", 0.0) or 0.0)
        sq_cache = getattr(self, "_sq_cache", {}) or {}
        sq_engine = getattr(self, "_sq_engine", None)
        if (now - sq_ts) < 300 and sq_cache:
            return sq_cache
        if not sq_engine:
            return {}
        try:
            snap = sq_engine.get_latest_snapshot()
            if snap:
                self._sq_cache = snap
                self._sq_ts = now
        except Exception:
            pass
        return getattr(self, "_sq_cache", {}) or {}

    @staticmethod
    def _compact_bte_guidance_reason(reason: str) -> str:
        text = str(reason or "").strip()
        match = re.search(r"insufficient_total_breakouts\((\d+)<(\d+)\)", text)
        if match:
            return f"{match.group(1)} brk / {match.group(2)} req"
        return compact_reason(text, max_len=18)

    def _readiness_row(self, strategy: str) -> Dict:
        rows = (self._strategy_readiness_snapshot or {}).get("strategies", []) or []
        target = str(strategy or "").upper()
        for row in rows:
            if str(row.get("strategy") or "").upper() == target:
                return row
        return {}

    def _readiness_status_text(self, strategy: str) -> Text:
        row = self._readiness_row(strategy)
        status = str(row.get("status") or "—").upper()
        style = (
            "green" if status == "LIVE"
            else "bold yellow" if status == "SHADOW"
            else "yellow" if status == "REBUILD"
            else "dim"
        )
        return Text(status, style=style)

    def _readiness_note(self, strategy: str) -> str:
        row = self._readiness_row(strategy)
        if not row:
            return "no evidence"
        trade = row.get("trade_metrics", {}) or {}
        blockers = (row.get("decision_metrics", {}) or {}).get("top_failure_reasons") or []
        closed = int(trade.get("closed_trades") or 0)
        avg_r = trade.get("avg_r_multiple")
        if closed > 0 and avg_r is not None:
            return f"c{closed} r{float(avg_r):+.2f}"
        if closed > 0:
            return f"c{closed}"
        if blockers:
            return compact_reason(blockers[0].get("reason"), max_len=18)
        return compact_reason(row.get("status_reason"), max_len=18)

    def _watchlist_snapshot(self) -> List[Dict[str, object]]:
        return fetch_recent_watch_decisions(self.db_path, limit=4)

    def _load_open_trade_tickers_from_db(self) -> set[str]:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT UPPER(TRIM(ticker))
                FROM trades
                WHERE status = 'OPEN'
                  AND ticker IS NOT NULL
                  AND TRIM(ticker) != ''
                """
            )
            rows = {str(row[0]).strip().upper() for row in cur.fetchall() if row and row[0]}
            conn.close()
            return rows
        except Exception:
            return set()

    def _position_reconciliation_note(self) -> Optional[str]:
        broker_tickers = {str(ticker).strip().upper() for ticker in (self.positions or {}).keys() if ticker}
        db_tickers = self._load_open_trade_tickers_from_db()
        if not broker_tickers and not db_tickers:
            return None

        broker_only = sorted(broker_tickers - db_tickers)
        db_only = sorted(db_tickers - broker_tickers)
        if not broker_only and not db_only:
            return None

        parts = []
        if broker_only:
            parts.append(f"broker_only:{','.join(broker_only[:3])}")
        if db_only:
            parts.append(f"db_only:{','.join(db_only[:3])}")
        return "Position mismatch " + " | ".join(parts)

    def _breadth_display_state(self) -> Dict[str, object]:
        self._refresh_policy_snapshot()
        policy = self._policy_snapshot or {}
        breadth_context = policy.get("daily_context") or {}

        b = self.cached_breadth or {}
        ad = b.get('advance_decline') or {}
        ma = b.get('ma_breadth') or {}
        hl = b.get('new_highs_lows') or {}
        health_score = b.get('health_score')
        divergence = b.get('divergence') or {}
        signal = b.get('signal') or {}

        live_ad_total = int(ad.get('total') or 0)
        live_ma_total = int(ma.get('total') or 0)
        live_available = live_ad_total > 0 and live_ma_total > 0

        if has_sufficient_market_breadth_coverage(
            breadth_context,
            min_symbols_with_data=DEFAULT_MIN_SYMBOLS_WITH_DATA,
        ):
            try:
                raw_pct = breadth_context.get("market_breadth_pct")
                breadth_pct = float(raw_pct) if raw_pct is not None else None
            except Exception:
                breadth_pct = None
        else:
            breadth_pct = None

        if live_available:
            return {
                "mode": "LIVE",
                "health_score": health_score,
                "advance_decline": ad,
                "ma_breadth": ma,
                "new_highs_lows": hl,
                "context_date": breadth_context.get("context_date"),
                "persisted_breadth_pct": breadth_pct,
                "divergence": divergence,
                "signal": signal,
            }

        if breadth_pct is None:
            return {
                "mode": "NO_DATA",
                "health_score": None,
                "advance_decline": {
                    "advancing": 0,
                    "declining": 0,
                    "unchanged": 0,
                    "total": 0,
                    "ratio": None,
                    "status": "NO_DATA",
                },
                "ma_breadth": {
                    "above_50ma": None,
                    "above_200ma": None,
                    "total": 0,
                    "pct_above_50ma": None,
                    "pct_above_200ma": None,
                    "status_50ma": "NO_DATA",
                    "status_200ma": "NO_DATA",
                    "stock_component": {"pct_above_200ma": None, "total": 0},
                    "sector_component": {"pct_above_200ma": None, "total": 0},
                    "breadth_model": None,
                },
                "new_highs_lows": {
                    "new_highs": None,
                    "new_lows": None,
                    "nh_nl_index": None,
                    "status": "NO_DATA",
                },
                "context_date": breadth_context.get("context_date"),
                "persisted_breadth_pct": None,
                "divergence": {},
                "signal": {},
            }

        symbols_with_data = int(breadth_context.get("symbols_with_data") or 0)
        above_200ma_count = int(breadth_context.get("above_200ma_count") or 0)
        stock_symbols_with_data = int(breadth_context.get("stock_symbols_with_data") or 0)
        stock_above_200ma_count = int(breadth_context.get("stock_above_200ma_count") or 0)
        sector_symbols_with_data = int(breadth_context.get("sector_symbols_with_data") or 0)
        sector_above_200ma_count = int(breadth_context.get("sector_above_200ma_count") or 0)
        return {
            "mode": "FALLBACK",
            "health_score": None,
            "advance_decline": {
                "advancing": 0,
                "declining": 0,
                "unchanged": 0,
                "total": 0,
                "ratio": None,
                "status": "NO_DATA",
            },
            "ma_breadth": {
                "above_50ma": None,
                "above_200ma": above_200ma_count,
                "total": symbols_with_data,
                "pct_above_50ma": None,
                "pct_above_200ma": breadth_pct,
                "status_50ma": "NO_DATA",
                "status_200ma": "PERSISTED",
                "stock_component": {
                    "above_200ma": stock_above_200ma_count,
                    "total": stock_symbols_with_data,
                    "pct_above_200ma": breadth_context.get("stock_breadth_pct"),
                },
                "sector_component": {
                    "above_200ma": sector_above_200ma_count,
                    "total": sector_symbols_with_data,
                    "pct_above_200ma": breadth_context.get("sector_breadth_pct"),
                },
                "breadth_model": breadth_context.get("breadth_model"),
            },
            "new_highs_lows": {
                "new_highs": None,
                "new_lows": None,
                "nh_nl_index": None,
                "status": "NO_DATA",
            },
            "context_date": breadth_context.get("context_date"),
            "persisted_breadth_pct": breadth_pct,
            "divergence": {},
            "signal": {},
        }

    def _market_status(self):
        """Returns (is_open: bool, label: str). Labels: OPEN / PRE-MKT / AFTER-HRS / CLOSED / WEEKEND"""
        try:
            import pytz
            now_et = datetime.now(pytz.timezone('America/New_York'))
            h, m, wd = now_et.hour, now_et.minute, now_et.weekday()
            if wd >= 5:
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

    # ── data refresh ──────────────────────────────────────────────────────────

    def refresh_institutional_data(self):
        """Refresh institutional module data — max every 15s."""
        now = time.time()
        if now - self.last_refresh < 15:
            return
        try:
            self.cached_regime = self.regime_detector.detect_regime()
            self.cached_rotation = self.rotation_tracker.analyze_rotation('5d')
            self.cached_breadth = self.breadth_monitor.analyze_breadth()
            self.cached_options = self.options_tracker.analyze_positioning()
            self.cached_inter_market = self.inter_market.analyze_inter_market('5d')
            self.last_refresh = now
        except Exception:
            pass

    def _edge_summary(self, ttl: int = 30) -> Optional[dict]:
        """Cache EdgeAnalytics summary to avoid frequent DB hits."""
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

    def _refresh_broker_state(self):
        """Fetch account and positions from Alpaca — max every 10s.

        Previously called by 4 separate panels on every 2s render tick,
        resulting in ~4 API calls/second. Now centralised with a 10s gate.
        pnl uses account.last_equity (yesterday close) instead of $100k.
        """
        if not self.trading_client:
            return
        now = time.time()
        if now - self._last_broker_refresh < 10:
            return
        self._last_broker_refresh = now

        try:
            account = self.trading_client.get_account()
            equity = float(account.equity)
            cash = float(account.cash)
            buying_power = float(account.buying_power)
            last_equity = float(account.last_equity) if account.last_equity else equity
            daily_pnl = equity - last_equity
            self.account_info = {
                'equity': equity,
                'cash': cash,
                'buying_power': buying_power,
                'daily_pnl': daily_pnl,
                'last_equity': last_equity,
            }
        except Exception:
            pass

        try:
            raw_positions = self.trading_client.get_all_positions()
            self.positions = {}
            for pos in raw_positions:
                self.positions[pos.symbol] = normalize_position(
                    pos,
                    source="ALPACA_API",
                    trading_client=self.trading_client,
                )
        except Exception:
            self.positions = {}

    def parse_log_file(self):
        """Tail log file for strategy status, confluence, and execution data."""
        try:
            with open(self.log_file, 'r') as f:
                try:
                    file_size = os.path.getsize(self.log_file)
                    # Handle log rotation/truncation.
                    if self.last_position > file_size:
                        self.last_position = 0
                except Exception:
                    pass
                f.seek(self.last_position)
                new_lines = f.readlines()
                self.last_position = f.tell()
                if new_lines:
                    self.last_log_update = datetime.fromtimestamp(
                        os.path.getmtime(self.log_file)
                    ).astimezone()

            for line in new_lines:
                line = line.strip()

                # Strategy status
                if 'Sniper Strategy:' in line or '🎯 Sniper:' in line:
                    # Explicit "Sniper Strategy: ACTIVE/STANDBY" — reliable gate status
                    if 'ACTIVE' in line:
                        self.strategy_status['sniper'] = 'ACTIVE'
                    elif 'STANDBY' in line:
                        self.strategy_status['sniper'] = 'STANDBY'
                # "Sniper: N stocks" is a watchlist-size log — it does NOT confirm VIX
                # clearance. Do NOT set strategy_status from this line; universe count
                # only goes to confluence_analytics. The VIX gate in _panel_strategy()
                # is the authoritative override regardless.

                if 'Remora Strategy:' in line or '🦈 Remora:' in line:
                    if 'HUNTING' in line or 'ACTIVE' in line:
                        self.strategy_status['remora'] = 'HUNTING'
                    elif 'STANDBY' in line:
                        self.strategy_status['remora'] = 'STANDBY'
                # "Remora: N stocks" same as above — watchlist size, not gate status

                # Reaper (Contrarian) strategy status
                if 'Running Contrarian scan' in line:
                    self.strategy_status['contrarian'] = 'HUNTING'
                elif 'Contrarian: standing down' in line:
                    self.strategy_status['contrarian'] = 'STANDBY'
                elif 'Contrarian:' in line and 'setups' in line:
                    try:
                        m = re.search(r'Contrarian:\s*(\d+)\s*setups', line)
                        count = int(m.group(1)) if m else 0
                        self.strategy_status['contrarian'] = 'HUNTING' if count > 0 else 'ARMED'
                    except Exception:
                        pass

                if 'VIX sizing:' in line:
                    try:
                        self.strategy_status['vix_sizing'] = line.split('VIX sizing:')[1].strip().split()[0]
                    except Exception:
                        pass

                if 'Stress sizing:' in line:
                    try:
                        self.strategy_status['stress_sizing'] = line.split('Stress sizing:')[1].strip().split()[0]
                    except Exception:
                        pass

                # Triple confluence
                if 'TRIPLE CONFLUENCE DETECTED' in line:
                    try:
                        count = None
                        m = re.search(r'TRIPLE CONFLUENCE DETECTED:\s*(\d+)\s*stocks?', line)
                        if m:
                            count = int(m.group(1))
                        else:
                            m = re.search(r'(\d+)\s+TRIPLE CONFLUENCE DETECTED', line)
                            if m:
                                count = int(m.group(1))
                        if count is None:
                            raise ValueError('no parseable triple confluence count')
                        now_ts = time.time()
                        self.confluence_analytics['active'] = True
                        self.confluence_analytics['count'] = count
                        self.confluence_analytics['detection_time'] = datetime.now().strftime('%H:%M:%S')
                        self.confluence_analytics['last_detected'] = datetime.now().strftime('%H:%M')
                        self.confluence_analytics['last_detected_ts'] = now_ts
                        self.confluence_analytics['tickers'] = []
                        self.confluence_analytics['confluence_history'].appendleft(
                            {'time': datetime.now(), 'count': count, 'tickers': []}
                        )
                    except Exception:
                        pass

                if 'Pre-signal overlap:' in line:
                    try:
                        overlap = int(line.split('Pre-signal overlap:')[1].split('symbols')[0].strip())
                        self.confluence_analytics['pre_signal_overlap'] = overlap
                        self.confluence_analytics['last_overlap_time'] = datetime.now().strftime('%H:%M:%S')
                    except Exception:
                        pass

                if (
                    'No signal-level triple confluence detected' in line
                    or 'No triple confluence at this time' in line
                ):
                    self.confluence_analytics['active'] = False
                    self.confluence_analytics['count'] = 0
                    self.confluence_analytics['tickers'] = []

                if (
                    '⭐⭐⭐' in line
                    and 'EXECUTING:' not in line
                    and (
                        self.confluence_analytics.get('active')
                        or 'MAXIMUM CONVICTION' in line
                    )
                ):
                    try:
                        m = re.search(r'⭐⭐⭐\s*([A-Z]{1,6})\b', line)
                        ticker = m.group(1).upper() if m else ''
                        if ticker and ticker not in self.confluence_analytics['tickers']:
                            self.confluence_analytics['tickers'].append(ticker)
                            if self.confluence_analytics['confluence_history']:
                                self.confluence_analytics['confluence_history'][0]['tickers'].append(ticker)
                    except Exception:
                        pass

                # Universe sizes
                for strat in ('voyager', 'sniper', 'remora'):
                    tag = strat.capitalize()
                    if (f'{tag} universe:' in line or (f'{tag}:' in line and 'candidates' in line)):
                        try:
                            count = int(line.split('candidates')[0].split()[-1])
                            self.confluence_analytics['universes'][strat]['count'] = count
                            self.confluence_analytics['universes'][strat]['last_update'] = datetime.now()
                        except Exception:
                            pass

                # Scan cycle marker
                if 'V3 INTELLIGENT SCAN' in line or 'SCAN CYCLE' in line:
                    self.scan_count += 1
                    try:
                        self.last_scan_dt = self._parse_scan_marker_dt(line)
                    except Exception:
                        pass

                # Pattern distribution (Bottoming / Accumulation / Breakout)
                if any(k in line for k in ['Bottoming:', 'Accumulation:', 'Breakout:']):
                    try:
                        name, val = line.split(':', 1)
                        self.pattern_distribution[name.strip().split()[-1]] = int(val.strip().split()[0])
                    except Exception:
                        pass

                # Trade execution
                if any(k in line for k in ['SINGLE STRATEGY:', 'DOUBLE CONFLUENCE:', 'TRIPLE CONFLUENCE:']):
                    try:
                        ticker = line.split(':', 1)[1].strip().split()[0]
                        conf_type = 'TRIPLE' if 'TRIPLE' in line else ('DOUBLE' if 'DOUBLE' in line else 'SINGLE')
                        self.executed_trades.append({'ticker': ticker, 'confluence_type': conf_type,
                                                     'timestamp': datetime.now()})
                    except Exception:
                        pass

                if 'Shares:' in line and self.executed_trades:
                    try:
                        self.executed_trades[-1]['shares'] = int(line.split('Shares:')[1].strip().split()[0])
                    except Exception:
                        pass

                if 'Entry: $' in line and self.executed_trades:
                    try:
                        self.executed_trades[-1]['entry_price'] = float(line.split('Entry: $')[1].strip().split()[0])
                    except Exception:
                        pass

        except Exception:
            pass

        self._expire_stale_confluence()
        self._calculate_coverage_stats()

    def _expire_stale_confluence(self):
        """Expire active confluence badge when no refresh is seen for TTL."""
        if not self.confluence_analytics.get('active'):
            return
        last_ts = float(self.confluence_analytics.get('last_detected_ts') or 0.0)
        if last_ts <= 0:
            return
        if (time.time() - last_ts) > self.CONFLUENCE_TTL_SECONDS:
            self.confluence_analytics['active'] = False
            self.confluence_analytics['count'] = 0
            self.confluence_analytics['tickers'] = []
            self.confluence_analytics['detection_time'] = None

    def _calculate_coverage_stats(self):
        v = self.confluence_analytics['universes']['voyager']['count']
        s = self.confluence_analytics['universes']['sniper']['count']
        r = self.confluence_analytics['universes']['remora']['count']
        self.confluence_analytics['total_coverage'] = v + s + r
        # unique_coverage: use total (honest) — 0.7× was an invented estimate
        self.confluence_analytics['unique_coverage'] = v + s + r

    # ── panels ────────────────────────────────────────────────────────────────

    def _panel_header(self) -> Panel:
        self._refresh_heartbeat()
        daemon_status, daemon_style = self._daemon_status()
        now = datetime.now()
        grid = Table.grid(expand=True)
        grid.add_column(ratio=3)
        grid.add_column(ratio=2, justify="right")

        left = Text()
        left.append("CHIEF STRATEGIST", style="bold white")
        left.append(" TERMINAL", style="bold cyan")
        left.append("  Daemon: ", style="bold")
        left.append(daemon_status, style=daemon_style)
        if self.confluence_analytics['active'] and self.confluence_analytics['count'] > 0:
            left.append(f"  TRIPLE CONFLUENCE x{self.confluence_analytics['count']}", style="bold yellow on red")

        right = Text()
        right.append(now.strftime("%a %b %d %Y"), style="white")
        right.append("  ", style="dim")
        right.append(now.strftime("%I:%M:%S %p ET"), style="bold cyan")
        grid.add_row(left, right)

        status = Text()
        mkt_open, mkt_label = self._market_status()
        mkt_style = (
            "bold green" if mkt_open
            else "yellow" if mkt_label in ("PRE-MKT", "AFTER-HRS")
            else "bold red"
        )
        pos_count = len(self.positions)
        opp_count = sum(int(self.confluence_analytics['universes'][k]['count'] or 0) for k in ('voyager', 'sniper', 'remora'))
        open_pnl = sum((p.get('unrealized_pnl') or 0.0) for p in self.positions.values()) if self.positions else 0.0
        pnl_style = "green" if open_pnl >= 0 else "red"
        regime = (self.cached_regime or {}).get('regime', '—')
        vix = (self.cached_regime or {}).get('vix_level', '—')
        regime_style = "green" if "BULL" in str(regime).upper() or "RISK_ON" in str(regime).upper() else "red" if "BEAR" in str(regime).upper() or "RISK_OFF" in str(regime).upper() else "cyan"

        status.append("MKT: ", style="bold")
        status.append(mkt_label, style=mkt_style)
        status.append("   SYS: ", style="bold")
        status.append(daemon_status, style=daemon_style)
        status.append("   POS: ", style="bold")
        status.append(str(pos_count), style="white")
        status.append("   P/L: ", style="bold")
        status.append(f"${open_pnl:+,.0f}", style=pnl_style)
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
        status.append(str(vix), style="white")
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
            status.append("LIVE", style="green")
        else:
            status.append(f"OFF <{threshold}", style="yellow")
        grid.add_row(status, Text(""))

        self._refresh_trend_snapshot()
        trend = compact_trend_labels(
            self._trend_snapshot,
            current_vix=self.cached_regime.get("vix_level") if self.cached_regime else None,
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
        grid.add_row(trend_row, Text(""))

        return Panel(grid, border_style="white", padding=(0, 1))

    def _panel_market_regime(self) -> Panel:
        if not self.cached_regime:
            return Panel("[dim]Loading...[/dim]", title="[bold white]MARKET REGIME", border_style="blue")

        r = self.cached_regime
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=16, style="bold")
        tbl.add_column(width=22)

        rc = "green" if "BULL" in r['regime'] else "red" if "BEAR" in r['regime'] else "yellow"
        tbl.add_row("Regime", Text(r['regime'], style=f"bold {rc}"))

        sc = "green" if r['risk_state'] == 'RISK_ON' else "red" if r['risk_state'] == 'RISK_OFF' else "yellow"
        tbl.add_row("Risk State", Text(r['risk_state'], style=f"bold {sc}"))

        tbl.add_row("Phase", r['phase'])

        ts = r['trend_strength']
        tc = "green" if ts > 70 else "yellow" if ts > 40 else "red"
        tbl.add_row("Trend Strength", Text(f"{ts}/100", style=tc))

        tbl.add_row("SPY vs 50MA", f"{r['spy_vs_50ma']:+.1f}%")
        tbl.add_row("SPY vs 200MA", f"{r['spy_vs_200ma']:+.1f}%")

        if r.get('vix_level'):
            vc = "green" if r['vix_level'] < 20 else "yellow" if r['vix_level'] <= 30 else "red"
            tbl.add_row("VIX", Text(f"{r['vix_level']:.1f}  {r.get('vix_regime','')}", style=vc))
            self._refresh_trend_snapshot()
            trend = compact_trend_labels(self._trend_snapshot, current_vix=r.get("vix_level"))
            tbl.add_row("VIX Trend", Text(trend["vix"], style=vc))

        return Panel(tbl, title="[bold white]MARKET REGIME", border_style="blue", padding=(0, 1))

    # Short sector names for display — ticker: short label
    _SECTOR_LABELS = {
        'XLK': 'Tech',
        'XLY': 'Cons Disc',
        'XLF': 'Finance',
        'XLP': 'Staples',
        'XLU': 'Utilities',
        'XLV': 'Healthcare',
        'XLE': 'Energy',
        'XLI': 'Industrial',
        'XLB': 'Materials',
    }

    def _panel_sector_rotation(self) -> Panel:
        if not self.cached_rotation:
            return Panel("[dim]Loading...[/dim]", title="[bold white]SECTOR ROTATION", border_style="magenta")

        rot = self.cached_rotation
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=10, style="bold")
        tbl.add_column(width=28)

        pattern = rot['rotation_pattern']
        if pattern == 'RISK_ON':
            pc = "green"
        elif pattern in ('RISK_OFF', 'LATE_CYCLE'):
            pc = "red"
        elif pattern == 'EARLY_CYCLE':
            pc = "cyan"
        else:
            pc = "yellow"

        tbl.add_row("Pattern", Text(pattern, style=f"bold {pc}"))

        # Rotation strength — how decisive the move is
        strength = rot.get('rotation_strength', 0)
        sc = "green" if strength > 1.0 else "yellow" if strength > 0.3 else "dim"
        tbl.add_row("Strength", Text(f"{strength:+.2f}% spread", style=sc))

        tbl.add_row("Flow", Text(rot['money_flow']['primary_flow'], style="cyan"))

        for i, ldr in enumerate(rot['leaders'][:3], 1):
            label = self._SECTOR_LABELS.get(ldr['ticker'], ldr['ticker'])
            rs_color = "green" if ldr['relative'] > 0 else "red"
            tbl.add_row(
                f"▲ {i}",
                Text(f"{ldr['ticker']} {label:<10}  {ldr['relative']:+.1f}%", style=rs_color)
            )

        for i, lag in enumerate(rot['laggards'][:2], 1):
            label = self._SECTOR_LABELS.get(lag['ticker'], lag['ticker'])
            tbl.add_row(
                f"▼ {i}",
                Text(f"{lag['ticker']} {label:<10}  {lag['relative']:+.1f}%", style="red")
            )

        return Panel(tbl, title="[bold white]SECTOR ROTATION", border_style="magenta", padding=(0, 1))

    def _panel_breadth(self) -> Panel:
        display = self._breadth_display_state()
        if display.get("mode") == "NO_DATA":
            return Panel("[dim]No breadth data[/dim]", title="[bold white]MARKET BREADTH", border_style="green")

        b = display
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=13, style="bold")
        tbl.add_column(width=28)

        # ── Health score ──────────────────────────────────────────────────────
        hs = b.get('health_score')
        mode = str(b.get("mode") or "LIVE").upper()
        if hs is None:
            health_style = "bold yellow" if mode == "FALLBACK" else "dim"
            health_label = "Fallback" if mode == "FALLBACK" else "No data"
            tbl.add_row("Health", Text(f"N/A  {health_label}", style=health_style))
        else:
            hc = "green" if hs > 60 else "yellow" if hs > 40 else "red"
            hs_word = "Healthy" if hs > 60 else "Neutral" if hs > 40 else "Weak"
            tbl.add_row("Health", Text(f"{hs}/100  {hs_word}", style=f"bold {hc}"))

        # ── Advance / Decline ────────────────────────────────────────────────
        # How many stocks rose vs fell today (ETFs excluded to avoid double-count)
        ad = b['advance_decline']
        ratio = ad.get('ratio')
        if mode == "FALLBACK":
            tbl.add_row("Adv/Dec", Text("—  persisted breadth only", style="yellow"))
        else:
            if ratio is None:
                adc = "dim"
                ratio_label = "N/A"
                ad_desc = "No breadth data"
            elif math.isinf(ratio):
                adc = "green"
                ratio_label = "∞"
                ad_desc = "All advancers"
            else:
                adc = "green" if ratio > 1.5 else "yellow" if ratio > 1.0 else "red"
                ratio_label = f"{ratio:.1f}x"
                ad_desc = "Bulls dominate" if ratio > 2.0 else "Bulls winning" if ratio > 1.5 else "Slight edge" if ratio > 1.0 else "Bears winning"
            tbl.add_row("Adv/Dec", Text(f"{ad['advancing']}↑ {ad['declining']}↓  {ratio_label}  {ad_desc}", style=adc))

        # ── Near Highs / Near Lows ───────────────────────────────────────────
        # Stocks within 2% of 52-week high or low — shows where leaders/losers cluster
        hl = b['new_highs_lows']
        if mode == "FALLBACK":
            tbl.add_row("Near H/L", Text("—  persisted breadth only", style="yellow"))
        else:
            nh_nl_index = hl.get('nh_nl_index')
            if nh_nl_index is None:
                tbl.add_row("Near H/L", Text("N/A  No breadth data", style="dim"))
            else:
                hlc = "green" if nh_nl_index > 0 else "red"
                hl_desc = "Upside momentum" if nh_nl_index >= 5 else "Downside pressure" if nh_nl_index <= -5 else "Balanced"
                tbl.add_row("Near H/L", Text(f"H:{hl['new_highs']}  L:{hl['new_lows']}  {hl_desc}", style=hlc))

        # ── % Above 50-day MA ────────────────────────────────────────────────
        # Short-term trend health — >60% = uptrend intact, <40% = correction
        ma = b['ma_breadth']
        pct_above_50 = ma.get('pct_above_50ma')
        if pct_above_50 is None:
            row_style = "yellow" if mode == "FALLBACK" else "dim"
            row_label = "N/A  persisted breadth only" if mode == "FALLBACK" else "N/A  No breadth data"
            tbl.add_row("Above 50MA", Text(row_label, style=row_style))
        else:
            m50c = "green" if pct_above_50 > 60 else "yellow" if pct_above_50 > 40 else "red"
            m50_desc = "Uptrend" if pct_above_50 > 60 else "Transition" if pct_above_50 > 40 else "Downtrend"
            tbl.add_row("Above 50MA", Text(f"{pct_above_50:.0f}%  {m50_desc}", style=m50c))

        # ── % Above 200-day MA ───────────────────────────────────────────────
        # Long-term trend health — composite combines stock participation and
        # sector confirmation so one narrow sleeve cannot dominate the signal.
        stock_component = ma.get("stock_component") or {}
        sector_component = ma.get("sector_component") or {}
        stock_pct_above_200 = stock_component.get("pct_above_200ma")
        sector_pct_above_200 = sector_component.get("pct_above_200ma")

        if stock_pct_above_200 is not None:
            stock_style = "green" if float(stock_pct_above_200) > 60 else "yellow" if float(stock_pct_above_200) > 40 else "red"
            tbl.add_row("Stock 200MA", Text(f"{float(stock_pct_above_200):.0f}%", style=stock_style))
        elif mode == "FALLBACK":
            tbl.add_row("Stock 200MA", Text("N/A  persisted breadth only", style="yellow"))

        if sector_pct_above_200 is not None:
            sector_style = "green" if float(sector_pct_above_200) > 60 else "yellow" if float(sector_pct_above_200) > 40 else "red"
            tbl.add_row("Sector 200MA", Text(f"{float(sector_pct_above_200):.0f}%", style=sector_style))
        elif mode == "FALLBACK":
            tbl.add_row("Sector 200MA", Text("N/A  persisted breadth only", style="yellow"))

        pct_above_200 = ma.get('pct_above_200ma')
        if pct_above_200 is not None:
            m200c = "green" if pct_above_200 > 60 else "yellow" if pct_above_200 > 40 else "red"
            if mode == "FALLBACK":
                m200_desc = "Composite daily context"
            else:
                m200_desc = "Bull market" if pct_above_200 > 60 else "Transition" if pct_above_200 > 40 else "Bear market"
            tbl.add_row("Composite", Text(f"{pct_above_200:.0f}%  {m200_desc}", style=m200c))
            tbl.add_row("Breadth Bar", Text(f"{percent_meter(pct_above_200, width=12)}", style=m200c))
        else:
            tbl.add_row("Composite", Text("N/A  No breadth data", style="dim"))

        context_date = b.get("context_date")
        if mode == "FALLBACK" and context_date:
            tbl.add_row("Source", Text(f"daily_context  {context_date}", style="yellow"))

        policy = self._policy_snapshot or {}
        breadth_pct = policy.get("voyager_breadth_pct")
        breadth_mode = str(policy.get("voyager_breadth_mode") or "UNKNOWN")
        if breadth_pct is not None:
            gate_style = (
                "bold red" if breadth_mode == "SUPPRESSED"
                else "bold yellow" if breadth_mode == "HALF_SIZE"
                else "green"
            )
            tbl.add_row(
                "VOY Gate",
                Text(f"{float(breadth_pct):.1f}%  {breadth_mode.replace('_', ' ')}", style=gate_style),
            )
            self._refresh_trend_snapshot()
            trend = compact_trend_labels(self._trend_snapshot, current_breadth=breadth_pct)
            tbl.add_row("Breadth Trend", Text(trend["breadth"], style=gate_style))

        # ── Divergence warning ───────────────────────────────────────────────
        # Price going up but most stocks NOT participating = hidden weakness
        div = b.get('divergence', {})
        if mode == "LIVE":
            div_type = div.get('divergence', 'NONE')
            if div_type == 'NEGATIVE':
                sev = div.get('severity', '')
                div_label = f"⚠ Price/breadth split ({sev})"
                tbl.add_row("Diverge", Text(div_label, style="bold red"))
            elif div_type == 'POSITIVE':
                tbl.add_row("Diverge", Text("Bounce setup forming", style="cyan"))

        # ── Actionable signal ────────────────────────────────────────────────
        sig = b.get('signal', {})
        if mode == "LIVE" and sig.get('action'):
            action = sig['action']
            sc = ("green" if 'DEPLOY' in action
                  else "red"  if 'REDUCE' in action or 'EXIT' in action
                  else "yellow")
            tbl.add_row("Signal", Text(action, style=f"bold {sc}"))

        return Panel(tbl, title="[bold white]MARKET BREADTH", border_style="green", padding=(0, 1))

    def _panel_options(self) -> Panel:
        if not self.cached_options:
            return Panel("[dim]Loading...[/dim]", title="[bold white]OPTIONS", border_style="yellow")

        opt = self.cached_options
        def _f(value):
            try:
                return float(value)
            except Exception:
                return None

        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=14, style="bold")
        tbl.add_column(width=24)

        pc = opt.get('spy_put_call') or {}
        pc_ratio = _f(pc.get('ratio'))
        if pc_ratio is None:
            pc_c = "dim"
            pc_label = "N/A"
        else:
            pc_c = "green" if pc_ratio < 0.8 else "yellow" if pc_ratio < 1.2 else "red"
            pc_label = f"{pc_ratio:.2f}"
        tbl.add_row("SPY P/C", Text(pc_label, style=pc_c))
        tbl.add_row("Sentiment", str(pc.get('sentiment') or "UNKNOWN"))

        if opt.get('qqq_put_call'):
            qpc = opt['qqq_put_call']
            q_ratio = _f(qpc.get('ratio'))
            if q_ratio is None:
                qc = "dim"
                q_label = "N/A"
            else:
                qc = "green" if q_ratio < 0.8 else "yellow" if q_ratio < 1.2 else "red"
                q_label = f"{q_ratio:.2f}"
            tbl.add_row("QQQ P/C", Text(q_label, style=qc))

        gx = opt.get('gamma_exposure') or {}
        gx_regime = str(gx.get('regime') or "NEUTRAL")
        gc = "green" if gx_regime == 'POSITIVE' else "red" if gx_regime == 'NEGATIVE' else "yellow"
        tbl.add_row("Gamma", Text(gx_regime, style=gc))

        mp = opt.get('max_pain') or {}
        mp_price = _f(mp.get('price'))
        mp_dist = _f(mp.get('distance_pct'))
        tbl.add_row("Max Pain", f"${mp_price:.0f}" if mp_price is not None else "N/A")
        tbl.add_row("Distance", f"{mp_dist:+.1f}%" if mp_dist is not None else "N/A")

        sig = opt.get('signal') or {}
        sig_action = str(sig.get('action') or "WAIT")
        ac = "green" if 'DEPLOY' in sig_action or 'BOUNCE' in sig_action else "red" if 'REDUCE' in sig_action else "cyan"
        tbl.add_row("Signal", Text(sig_action, style=ac))

        return Panel(tbl, title="[bold white]OPTIONS POSITIONING", border_style="yellow", padding=(0, 1))

    def _panel_inter_market(self) -> Panel:
        if not self.cached_inter_market:
            return Panel("[dim]Loading...[/dim]", title="[bold white]INTER-MARKET", border_style="cyan")

        im = self.cached_inter_market
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=5, style="bold")
        tbl.add_column(width=8)
        tbl.add_column(width=2)
        tbl.add_column(width=12, style="dim")

        _IM_LABELS = {
            'SPY':  'Stocks',
            'DXY':  'Dollar',
            'TLT':  'Bonds',
            'GLD':  'Gold',
            'USO':  'Oil',
            'IBIT': 'Bitcoin',
        }

        for ticker in ['SPY', 'DXY', 'TLT', 'GLD', 'USO', 'IBIT']:
            if ticker in im['performance']:
                d = im['performance'][ticker]
                pc = "green" if d['performance'] > 0 else "red" if d['performance'] < 0 else "white"
                arrow = "↑" if d['direction'] == 'UP' else "↓" if d['direction'] == 'DOWN' else "→"
                tbl.add_row(ticker, Text(f"{d['performance']:+.1f}%", style=pc), arrow, _IM_LABELS.get(ticker, ''))

        regime = im['macro_regime']['regime']
        rc = "green" if regime == 'RISK_ON' else "red" if regime in ('RISK_OFF', 'CRISIS') else "yellow"
        footer = Text()
        footer.append("Macro: ", style="dim")
        footer.append(regime, style=f"bold {rc}")

        return Panel(tbl, title="[bold white]INTER-MARKET", subtitle=footer, border_style="cyan", padding=(0, 1))

    def _panel_macro_calendar(self) -> Panel:
        # Key-value layout — no column-width truncation, full gate notes including Contrarian
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column("K", width=10, style="bold")
        tbl.add_column("V")

        ws = self.macro_cal.get_macro_window_state()
        window = ws['window']

        if window == 'EVENT_LOCKOUT':
            win_style = "bold red"
            gate_note = Text(
                "Sniper/Remora: DENIED  |  Voyager/Short: caution  |  Contrarian: caution",
                style="red",
            )
            border = "red"
        elif window == 'PRE_EVENT':
            win_style = "bold yellow"
            gate_note = Text(
                "All strategies: caution (event imminent)  |  Contrarian: clear",
                style="yellow",
            )
            border = "yellow"
        elif window == 'POST_EVENT':
            win_style = "yellow"
            gate_note = Text(
                "Sniper/Remora: caution  |  Voyager/Short: normalizing  |  Contrarian: caution",
                style="yellow",
            )
            border = "green"   # stabilizing back toward normal — distinct from PRE yellow
        else:
            win_style = "bold green"
            gate_note = Text("All entries permitted", style="green")
            border = "yellow"

        event_name = str(ws.get('event_name') or '—')
        impact     = str(ws.get('impact') or '—').upper()
        minutes_to = ws.get('minutes_to_event')
        display_label = ws.get('display_label', 'CLEAR')

        if minutes_to is not None:
            if minutes_to > 0:
                h, m = divmod(int(minutes_to), 60)
                d, h = divmod(h, 24)
                time_label = f"{d}d {h}h" if d else (f"{h}h {m}m" if h else f"{m}m")
                time_label = f"T-{time_label}"
            else:
                time_label = f"T+{abs(int(minutes_to))}min"
        else:
            time_label = "—"

        imp_style = "bold red" if impact == "CRITICAL" else ("bold yellow" if impact == "HIGH" else "dim")

        tbl.add_row("Window", Text(display_label, style=win_style))
        tbl.add_row("Event",  event_name)
        tbl.add_row("Impact", Text(impact, style=imp_style))
        tbl.add_row("When",   time_label)
        tbl.add_row("Gate",   gate_note)

        # Upcoming events list
        events = self.macro_cal.get_upcoming_events(days_ahead=7)
        if events:
            tbl.add_row("", "")  # spacer
            for ev in events[:4]:
                ev_text = Text()
                ev_text.append(ev['event'], style="white")
                ev_text.append("  ")
                if ev['impact'] == 'CRITICAL':
                    ev_text.append("CRIT", style="bold red")
                elif ev['impact'] == 'HIGH':
                    ev_text.append("HIGH", style="bold yellow")
                else:
                    ev_text.append("MED", style="dim")
                ev_text.append(f"  {ev['countdown']}", style="cyan")
                tbl.add_row(Text(ev['date'][5:], style="dim"), ev_text)

        return Panel(tbl, title="[bold white]MACRO CALENDAR",
                     border_style=border, padding=(0, 1))

    def _panel_strategy(self) -> Panel:
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=10, style="bold")
        tbl.add_column(width=12)
        tbl.add_column(width=8)
        tbl.add_column(width=18)
        self._refresh_policy_snapshot()
        self._refresh_strategy_readiness()
        policy = self._policy_snapshot or {}

        tbl.add_row(
            Text("", style="dim"),
            Text("Runtime", style="dim"),
            Text("Ready", style="dim"),
            Text("Evidence", style="dim"),
        )

        # Live VIX from institutional data — authoritative gate for strategy eligibility.
        # Sniper gates off at VIX >= 20; Remora gates off at VIX > 30 (active at VIX <= 30).
        # Log-parsed status is secondary to the live VIX check.
        vix_level = (self.cached_regime or {}).get('vix_level', 0.0) or 0.0

        tbl.add_row(
            get_strategy_display_name("VOYAGER"),
            Text("● ACTIVE", style="bold green"),
            self._readiness_status_text("VOYAGER"),
            self._readiness_note("VOYAGER"),
        )

        if vix_level >= 20:
            sb = Text("● VIX GATED", style="bold red")
            sniper_note = f"VIX {vix_level:.0f} >= 20"
        else:
            s = self.strategy_status['sniper']
            sb = Text("● ACTIVE",  style="bold green") if s == 'ACTIVE' else \
                 Text("● STANDBY", style="bold red")   if s == 'STANDBY' else \
                 Text("● LOADING", style="yellow")
            sniper_note = "3-30 days"
        tbl.add_row(
            get_strategy_display_name("SNIPER"),
            sb,
            self._readiness_status_text("SNIPER"),
            self._readiness_note("SNIPER") if sniper_note == "3-30 days" else sniper_note,
        )

        if vix_level > 30:
            rb = Text("● VIX GATED", style="bold red")
            remora_note = f"VIX {vix_level:.0f} > 30"
        else:
            r = self.strategy_status['remora']
            # LOADING = log not yet parsed; Remora is active at VIX <= 30 by definition → infer HUNTING
            if r == 'LOADING' and vix_level > 0:
                r = 'HUNTING'
            rb = Text("● HUNTING", style="bold green") if r == 'HUNTING' else \
                 Text("● STANDBY", style="bold red")   if r == 'STANDBY' else \
                 Text("● LOADING", style="yellow")
            remora_note = "2-48 hours"
        tbl.add_row(
            get_strategy_display_name("REMORA"),
            rb,
            self._readiness_status_text("REMORA"),
            self._readiness_note("REMORA") if remora_note == "2-48 hours" else remora_note,
        )

        if policy.get("short_live_enabled", True):
            short_status = Text("● ACTIVE", style="bold green")
            short_note = "shadow/live"
        else:
            short_status = Text("● SHADOW", style="bold yellow")
            short_note = "scan/log only"
        tbl.add_row(
            get_strategy_display_name("SHORT"),
            short_status,
            self._readiness_status_text("SHORT"),
            self._readiness_note("SHORT") if short_note == "shadow/live" or short_note == "scan/log only" else short_note,
        )

        # Reaper (Contrarian): activates at VIX >= 28
        if vix_level >= 28:
            ctr_raw = self.strategy_status.get('contrarian', 'LOADING')
            if ctr_raw == 'HUNTING':
                cb = Text("● HUNTING", style="bold green")
            elif ctr_raw == 'ARMED':
                cb = Text("● ARMED",   style="bold yellow")
            else:
                cb = Text("● ARMED",   style="bold yellow")
            ctr_note = f"VIX {vix_level:.0f} >= 28"
        else:
            cb = Text("● STANDBY", style="dim")
            ctr_note = f"VIX {vix_level:.0f} < 28"
        tbl.add_row(
            "Reaper",
            cb,
            self._readiness_status_text("CONTRARIAN"),
            self._readiness_note("CONTRARIAN") if ctr_note.endswith("28") or "< 28" in ctr_note else ctr_note,
        )

        vix = self.strategy_status['vix_sizing']
        stress = self.strategy_status['stress_sizing']
        if vix != '100%' or stress != '100%':
            tbl.add_row("", "", "", "")
            tbl.add_row(Text("SIZING", style="dim"), f"VIX:{vix}", Text("—", style="dim"), f"Stress:{stress}")

        breadth_pct = policy.get("voyager_breadth_pct")
        breadth_mode = str(policy.get("voyager_breadth_mode") or "UNKNOWN").replace("_", " ")
        threshold = int(policy.get("forecast_sample_threshold") or 200)
        counts = policy.get("closed_trade_counts", {}) or {}
        if breadth_pct is None:
            tbl.add_row("VOY Breadth", Text("n/a", style="dim"), Text("—", style="dim"), "")
        else:
            tbl.add_row("VOY Breadth", Text(f"{float(breadth_pct):.1f}% {breadth_mode}", style="cyan"), Text("—", style="dim"), "")
        active = list(policy.get("forecast_active_strategies") or [])
        if active:
            tbl.add_row(
                "Forecast",
                Text(
                    f"{policy.get('forecast_model_version', 'shadow')} live",
                    style="green",
                ),
                Text("LIVE", style="green"),
                ",".join(s[:3] for s in active),
            )
        else:
            tbl.add_row(
                "Forecast",
                Text(f"{policy.get('forecast_model_version', 'shadow')} off <{threshold}", style="yellow"),
                Text("OFF", style="yellow"),
                f"VOY {counts.get('VOYAGER', 0)}  SHRT {counts.get('SHORT', 0)}",
            )

        # Edge readiness badge in subtitle — uses cached _edge_summary, no extra DB hit
        edge = self._edge_summary()
        readiness_rows = list((self._strategy_readiness_snapshot or {}).get("strategies", []) or [])
        ready_n = sum(1 for row in readiness_rows if bool(row.get("promotion_ready")))
        shadow_n = sum(1 for row in readiness_rows if str(row.get("status") or "").upper() == "SHADOW")
        rebuild_n = sum(1 for row in readiness_rows if str(row.get("status") or "").upper() == "REBUILD")
        readiness_badge = f"READY {ready_n}  SHADOW {shadow_n}  REBUILD {rebuild_n}"
        if edge:
            closed_n   = edge.get("closed_n", 0)
            data_ready = edge.get("data_ready", False)
            badge_text  = (
                f"EDGE: live (n={closed_n})  |  {readiness_badge}"
                if data_ready else
                f"EDGE: warming (n={closed_n})  |  {readiness_badge}"
            )
            badge_style = "bold green"                  if data_ready else "dim yellow"
            subtitle = Text(badge_text, style=badge_style)
        else:
            subtitle = Text(readiness_badge, style="dim yellow")

        return Panel(tbl, title="[bold white]MULTI-STRATEGY", border_style="magenta",
                     padding=(0, 1), subtitle=subtitle)

    def _panel_portfolio(self) -> Panel:
        self._refresh_risk_gate_snapshot()
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=16, style="bold")
        tbl.add_column(width=22)

        if self.account_info:
            equity = self.account_info.get('equity', 0.0)
            cash = self.account_info.get('cash', 0.0)
            bp = self.account_info.get('buying_power', 0.0)
            daily_pnl = self.account_info.get('daily_pnl', 0.0)
            last_eq = self.account_info.get('last_equity', equity) or equity
            pnl_pct = (daily_pnl / last_eq * 100) if last_eq else 0.0
            icon = "+" if daily_pnl >= 0 else ""

            tbl.add_row("Equity", f"${equity:,.2f}")
            tbl.add_row("Cash", f"${cash:,.2f}")
            tbl.add_row("Buying Power", f"${bp:,.2f}")
            pnl_style = "green" if daily_pnl >= 0 else "red"
            tbl.add_row("Daily P/L", Text(f"{icon}${daily_pnl:,.2f} ({pnl_pct:+.2f}%)", style=pnl_style))
            tbl.add_row("Positions", str(len(self.positions)))

            if self.positions:
                open_pnl = sum((p.get('unrealized_pnl') or 0.0) for p in self.positions.values())
                op_style = "green" if open_pnl >= 0 else "red"
                tbl.add_row("Open P/L", Text(f"${open_pnl:+,.2f}", style=op_style))

            # Edge mix + hot strat (Phase 3)
            edge = self._edge_summary()
            if edge:
                mix = edge.get("edge_mix", {})
                a = (mix.get("ALIGNMENT", {}) or {}).get("open", 0)
                i = (mix.get("INEFFICIENCY", {}) or {}).get("open", 0)
                c = (mix.get("CONTRARIAN", {}) or {}).get("open", 0)
                tbl.add_row("Edge Mix", Text(f"A{a}/I{i}/C{c}", style="cyan"))
                if edge.get("data_ready") and edge.get("best_strategy"):
                    tbl.add_row("Hot Strat", Text(get_strategy_display_name(edge["best_strategy"]), style="bold green"))
        else:
            tbl.add_row("Status", "[dim]No account data[/dim]")

        port_mode = str(self._risk_gate_snapshot.get("portfolio_mode") or "UNKNOWN")
        mode_style = (
            "bold red" if port_mode in ("DEFENSIVE", "CONSERVATIVE")
            else "bold yellow" if port_mode == "FEAR_OPPORTUNITY"
            else "green" if port_mode == "AGGRESSIVE"
            else "cyan"
        )
        tbl.add_row("Port Mode", Text(port_mode, style=mode_style))
        circuit = bool(self._risk_gate_snapshot.get("circuit_breaker", False))
        tbl.add_row("Circuit", Text("TRIGGERED" if circuit else "CLEAR", style="bold red" if circuit else "green"))
        gate_counts = self._risk_gate_snapshot.get("gate_counts", {}) or {}
        if gate_counts:
            order = ["Risk", "Portfolio", "Concentration", "Execution", "Other"]
            gate_text = "  ".join(f"{name}:{gate_counts[name]}" for name in order if gate_counts.get(name))
            tbl.add_row("Denied 24h", Text(gate_text, style="yellow"))

        return Panel(tbl, title="[bold white]PORTFOLIO", border_style="green", padding=(0, 1))

    def _panel_confluence(self) -> Panel:
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=16, style="bold")
        tbl.add_column(width=22)

        if self.confluence_analytics['active'] and self.confluence_analytics['count'] > 0:
            tbl.add_row("Status", Text(f"TRIPLE x{self.confluence_analytics['count']}", style="bold yellow on red"))
            if self.confluence_analytics['tickers']:
                tbl.add_row("Tickers", Text(", ".join(self.confluence_analytics['tickers'][:5]), style="bold white"))
            if self.confluence_analytics['detection_time']:
                tbl.add_row("Detected", self.confluence_analytics['detection_time'])
            tbl.add_row("Source", Text("Signal-level", style="cyan"))
            tbl.add_row("Exec Boost", Text("Voyager triple path (1.8x)", style="green"))
            tbl.add_row("", "")
        else:
            tbl.add_row("Status", Text("No active triple", style="dim"))

        overlap = int(self.confluence_analytics.get('pre_signal_overlap', 0) or 0)
        overlap_t = self.confluence_analytics.get('last_overlap_time')
        tbl.add_row("Pre-overlap", f"{overlap} symbols")
        if overlap_t:
            tbl.add_row("Overlap @", overlap_t)
        tbl.add_row("Scope", Text("Growth/Breakout/Catalyst only", style="dim"))
        tbl.add_row("", "")

        v = self.confluence_analytics['universes']['voyager']['count']
        s = self.confluence_analytics['universes']['sniper']['count']
        r = self.confluence_analytics['universes']['remora']['count']

        tbl.add_row(get_strategy_display_name("VOYAGER"), f"{v} stocks")
        tbl.add_row(get_strategy_display_name("SNIPER"), f"{s} stocks")
        tbl.add_row(get_strategy_display_name("REMORA"), f"{r} stocks")

        total = self.confluence_analytics['total_coverage']
        unique = self.confluence_analytics['unique_coverage']
        if total > 0:
            tbl.add_row("", "")
            tbl.add_row("Total Scanned", f"{total}")
            if unique != total:
                tbl.add_row("~Unique", f"{unique}")

        if self.pattern_distribution:
            tbl.add_row("", "")
            top = sorted(self.pattern_distribution.items(), key=lambda x: x[1], reverse=True)[:2]
            for pat, cnt in top:
                tbl.add_row(pat, f"{cnt} setups")

        watch_rows = self._watchlist_snapshot()
        if watch_rows:
            tbl.add_row("", "")
            tbl.add_row("Watchlist", Text("Best blocked / near-actionable", style="cyan"))
            for row in watch_rows[:3]:
                strat = get_strategy_display_name(row.get("strategy") or "UNKNOWN") or str(row.get("strategy") or "UNK")
                score = float(row.get("score") or 0.0)
                rr = float(row.get("rr") or 0.0)
                detail = compact_reason(row.get("reason"), max_len=18)
                tbl.add_row(
                    str(row.get("ticker") or "—"),
                    Text(f"{strat[:8]}  s{score:.0f} rr{rr:.1f}  {detail}", style="white"),
                )

        return Panel(tbl, title="[bold white]CONFLUENCE / WATCHLIST", border_style="yellow", padding=(0, 1))

    def _panel_positions(self) -> Panel:
        tbl = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 1))
        tbl.add_column("Ticker", style="bold", width=8)
        tbl.add_column("Dir", width=6)
        tbl.add_column("Qty", justify="right", width=6)
        tbl.add_column("Entry", justify="right", width=9)
        tbl.add_column("Now", justify="right", width=9)
        tbl.add_column("P/L", justify="right", width=20)
        tbl.add_column("Stop", justify="right", width=9)
        tbl.add_column("Tgt", justify="right", width=9)
        tbl.add_column("Path", width=5)
        tbl.add_column("Prot", width=6)

        if not self.positions:
            tbl.add_row("—", "—", "—", "—", "—", "[dim]No open positions[/dim]", "—", "—", "—", "—")
        else:
            for ticker, pos in sorted(self.positions.items(), key=lambda x: x[1].get('unrealized_pnl') or 0.0, reverse=True)[:6]:
                pl = pos.get('unrealized_pnl') or 0.0
                pl_pct = pos.get('unrealized_pnl_pct') or 0.0
                pl_style = "green" if pl >= 0 else "red"
                meta = self._load_position_metadata(ticker)
                primary_path = (
                    meta.get("primary_pathway")
                    or (meta.get("pathway_qualification", {}) or {}).get("primary_pathway")
                )
                path_short = {
                    "GROWTH": "GRO",
                    "GROWTH_INFLECTION": "INF",
                    "OPERATING_LEVERAGE": "LEV",
                    "QUALITY": "QLT",
                    "REVENUE": "REV",
                    "MARGIN": "MAR",
                    "GUIDANCE": "GUI",
                    "STRESS": "STR",
                }.get(str(primary_path or "").upper(), "—")
                tbl.add_row(
                    ticker,
                    pos.get('direction') or "—",
                    f"{abs(pos.get('qty') or 0):.0f}",
                    f"${(pos.get('entry_price') or 0):.2f}",
                    f"${(pos.get('current_price') or 0):.2f}",
                    Text(f"${pl:+.2f} ({pl_pct:+.1f}%)", style=pl_style),
                    f"${pos['stop_loss']:.2f}" if pos.get('stop_loss') is not None else "—",
                    f"${pos['take_profit']:.2f}" if pos.get('take_profit') is not None else "—",
                    path_short,
                    pos.get('protection_status') or "UNKN",
                )

        subtitle = self._position_reconciliation_note()
        return Panel(tbl, title="[bold white]LIVE POSITIONS", subtitle=subtitle, border_style="cyan", padding=(0, 1))

    def _load_position_metadata(self, ticker: str) -> Dict:
        latest: Dict = {}
        try:
            with open("trade_journal.jsonl", "r") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except Exception:
                        continue
                    if str(entry.get("ticker") or "").upper() == str(ticker or "").upper():
                        latest = entry
        except Exception:
            return {}
        return latest

    def _load_pathway_history(self) -> List[Dict]:
        history: List[Dict] = []
        cutoff = datetime.now() - timedelta(days=30)
        try:
            with open("trade_journal.jsonl", "r") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except Exception:
                        continue
                    ts_raw = entry.get("timestamp")
                    if ts_raw:
                        try:
                            ts = datetime.fromisoformat(str(ts_raw))
                            if ts < cutoff:
                                continue
                        except Exception:
                            pass
                    history.append(entry)
        except Exception:
            return []
        return history

    def _load_realized_trade_analytics(self, days: int = 30) -> Dict[str, object]:
        stats: Dict[str, object] = {
            "total": 0,
            "wins": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_return_pct": 0.0,
            "strategy": {},
            "pathway": {},
        }
        db_path = "trading_performance.db"
        if not os.path.exists(db_path):
            return stats

        cutoff = datetime.now() - timedelta(days=days)
        con = None
        try:
            con = sqlite3.connect(db_path)
            cur = con.cursor()
            cur.execute(
                "SELECT exit_date, pnl, pnl_percent, setup_type, notes "
                "FROM trades WHERE exit_date IS NOT NULL "
                "ORDER BY exit_date DESC LIMIT 500"
            )
            for exit_date, pnl, pnl_percent, setup_type, notes in cur.fetchall():
                try:
                    closed_at = datetime.fromisoformat(str(exit_date))
                except Exception:
                    continue
                if closed_at < cutoff:
                    continue
                pnl_val = float(pnl or 0)
                ret_val = float(pnl_percent or 0)
                strategy = str(setup_type or "UNKNOWN").upper()
                pathway = "UNKNOWN"
                for part in str(notes or "").split("|"):
                    if part.startswith("pathway="):
                        pathway = part.split("=", 1)[1] or "UNKNOWN"
                        break

                stats["total"] += 1
                stats["total_pnl"] += pnl_val
                stats["avg_return_pct"] += ret_val
                if pnl_val > 0:
                    stats["wins"] += 1

                for group_name, key in (("strategy", strategy), ("pathway", pathway)):
                    group = stats[group_name]
                    group.setdefault(key, {"total": 0, "wins": 0, "pnl": 0.0})
                    group[key]["total"] += 1
                    group[key]["pnl"] += pnl_val
                    if pnl_val > 0:
                        group[key]["wins"] += 1

            if stats["total"]:
                stats["win_rate"] = stats["wins"] / stats["total"] * 100.0
                stats["avg_return_pct"] = stats["avg_return_pct"] / stats["total"]
        except Exception:
            return stats
        finally:
            if con:
                con.close()
        return stats

    def _panel_pathway_performance(self) -> Panel:
        tbl = Table(show_header=True, box=box.SIMPLE_HEAD, padding=(0, 1))
        tbl.add_column("Pathway", width=12, style="bold")
        tbl.add_column("Trades", justify="right", width=6)
        tbl.add_column("Win", justify="right", width=7)
        tbl.add_column("Avg", justify="right", width=8)
        analytics = self._load_realized_trade_analytics(days=30)
        if not analytics["total"]:
            tbl.add_row("—", "—", "—", "No closed")
            return Panel(tbl, title="[bold white]PATHWAY PERFORMANCE", subtitle="Realized trades, last 30d", border_style="magenta", padding=(0, 1))

        labels = {
            "GROWTH": "Growth",
            "GROWTH_INFLECTION": "Inflection",
            "OPERATING_LEVERAGE": "Leverage",
            "QUALITY": "Quality",
            "REVENUE": "Revenue",
            "MARGIN": "Margin",
            "GUIDANCE": "Guidance",
            "STRESS": "Stress",
            "UNKNOWN": "Unknown",
        }
        path_stats: Dict[str, Dict] = analytics["pathway"] or {}
        for pathway, data in sorted(path_stats.items(), key=lambda item: item[1]["pnl"], reverse=True):
            total = data["total"]
            win_rate = (data["wins"] / total * 100) if total else 0.0
            avg_return = (data["pnl"] / total) if total else 0.0
            tbl.add_row(
                labels.get(pathway, str(pathway)[:12]),
                str(total),
                Text(f"{win_rate:.1f}%", style="green" if win_rate >= 50 else "yellow" if win_rate >= 40 else "red"),
                Text(f"${avg_return:+.0f}", style="green" if avg_return >= 0 else "red"),
            )

        subtitle = (
            f"Realized 30d  |  Trades {int(analytics['total'])}  "
            f"WR {analytics['win_rate']:.0f}%  P/L ${analytics['total_pnl']:+,.0f}"
        )
        return Panel(tbl, title="[bold white]PATHWAY PERFORMANCE", subtitle=subtitle, border_style="magenta", padding=(0, 1))

    def _load_recent_executions_from_db(self, limit: int = 6) -> List[Dict[str, object]]:
        if not os.path.exists(self.db_path):
            return []

        con = None
        try:
            con = sqlite3.connect(self.db_path)
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute("PRAGMA table_info(trades)")
            cols = {str(row["name"]) for row in cur.fetchall()}
            if "ticker" not in cols:
                return []

            select_cols = [col for col in ("ticker", "strategy", "direction", "quantity", "entry_price", "entry_time", "status") if col in cols]
            if not select_cols:
                return []
            ordering_terms = [col for col in ("entry_time", "entry_date") if col in cols]
            if not ordering_terms:
                ordering_expr = "id"
            elif len(ordering_terms) == 1:
                ordering_expr = ordering_terms[0]
            else:
                ordering_expr = f"COALESCE({', '.join(ordering_terms)})"

            cur.execute(
                f"""
                SELECT {', '.join(select_cols)}
                FROM trades
                WHERE ticker IS NOT NULL
                ORDER BY {ordering_expr} DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            )
            return [dict(row) for row in cur.fetchall()]
        except Exception:
            return []
        finally:
            if con:
                con.close()

    def _panel_executions(self) -> Panel:
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=12, style="bold")
        tbl.add_column(width=22)

        db_rows = self._load_recent_executions_from_db(limit=6)
        if db_rows:
            for trade in db_rows:
                ticker = str(trade.get('ticker') or '?').upper()
                strategy = str(trade.get('strategy') or 'UNK').upper()
                direction = str(trade.get('direction') or '—').upper()
                status = str(trade.get('status') or '—').upper()
                qty = trade.get('quantity')
                entry = trade.get('entry_price')
                qty_label = f"{int(float(qty))}" if qty not in (None, "") else "—"
                entry_label = f"${float(entry):.2f}" if entry not in (None, "") else "—"
                detail = f"{strategy[:4]} {direction[:1]} {qty_label} @ {entry_label} {status[:4]}"
                tbl.add_row(ticker, detail)
        elif self.executed_trades:
            for trade in self.executed_trades[-6:][::-1]:
                conf = trade.get('confluence_type', 'SINGLE')
                icon = "⭐ " if conf == 'TRIPLE' else "◆ " if conf == 'DOUBLE' else "  "
                ticker = trade.get('ticker', '?')
                shares = trade.get('shares', 0)
                entry = trade.get('entry_price', 0.0)
                detail = f"{shares} @ ${entry:.2f}" if shares and entry else "—"
                tbl.add_row(f"{icon}{ticker}", detail)
        else:
            tbl.add_row("[dim]No executions yet[/dim]", "")

        subtitle = "DB-backed recent entries" if db_rows else "Log-backed recent entries" if self.executed_trades else None
        return Panel(tbl, title="[bold white]RECENT EXECUTIONS", subtitle=subtitle, border_style="blue", padding=(0, 1))

    def _panel_consensus(self) -> Panel:
        if not all([self.cached_regime, self.cached_rotation, self.cached_breadth,
                    self.cached_options, self.cached_inter_market]):
            return Panel("[dim]Loading...[/dim]", title="[bold white]CONSENSUS", border_style="white")

        risk_off = 0
        risk_on = 0
        votes = []  # (label, vote_str, style)

        # 1. Market Regime
        regime_str = self.cached_regime['regime']
        if 'BEAR' in regime_str or self.cached_regime['risk_state'] == 'RISK_OFF':
            risk_off += 1
            votes.append(("Regime", regime_str[:10], "red"))
        elif 'BULL' in regime_str or self.cached_regime['risk_state'] == 'RISK_ON':
            risk_on += 1
            votes.append(("Regime", regime_str[:10], "green"))
        else:
            votes.append(("Regime", regime_str[:10], "yellow"))

        # 2. Sector Rotation
        rot_pat = self.cached_rotation['rotation_pattern']
        if rot_pat in ('RISK_OFF', 'LATE_CYCLE'):
            risk_off += 1
            votes.append(("Rotation", rot_pat[:10], "red"))
        elif rot_pat == 'RISK_ON':
            risk_on += 1
            votes.append(("Rotation", rot_pat[:10], "green"))
        elif rot_pat == 'EARLY_CYCLE':
            risk_on += 1
            votes.append(("Rotation", rot_pat[:10], "green"))
        else:
            votes.append(("Rotation", rot_pat[:10], "yellow"))

        # 3. Market Breadth
        breadth_state = self._breadth_display_state()
        breadth_mode = str(breadth_state.get("mode") or "NO_DATA").upper()
        breadth_pct = ((breadth_state.get("ma_breadth") or {}).get("pct_above_200ma"))
        if breadth_pct is None:
            votes.append(("Breadth", "N/A", "dim"))
        else:
            breadth_value = float(breadth_pct)
            breadth_label = f"{breadth_value:.0f}%"
            if breadth_mode == "FALLBACK":
                breadth_label += " ctx"
            if breadth_value < 40:
                risk_off += 1
                votes.append(("Breadth", breadth_label, "red"))
            elif breadth_value > 60:
                risk_on += 1
                votes.append(("Breadth", breadth_label, "green"))
            else:
                votes.append(("Breadth", breadth_label, "yellow"))

        # 4. Options Positioning
        opt_action = str(((self.cached_options or {}).get('signal') or {}).get('action') or 'WAIT')
        if opt_action in ('REDUCE_RISK', 'WATCH_FOR_PULLBACK'):
            risk_off += 1
            votes.append(("Options", opt_action[:10], "red"))
        elif opt_action in ('DEPLOY_CAPITAL', 'WATCH_FOR_BOUNCE'):
            risk_on += 1
            votes.append(("Options", opt_action[:10], "green"))
        else:
            votes.append(("Options", opt_action[:10], "yellow"))

        # 5. Inter-Market Macro
        im_regime = self.cached_inter_market['macro_regime']['regime']
        if im_regime in ('RISK_OFF', 'CRISIS', 'STAGFLATION'):
            risk_off += 1
            votes.append(("Inter-Mkt", im_regime[:10], "red"))
        elif im_regime == 'RISK_ON':
            risk_on += 1
            votes.append(("Inter-Mkt", im_regime[:10], "green"))
        else:
            votes.append(("Inter-Mkt", im_regime[:10], "yellow"))

        if risk_off >= 4:
            consensus, color, conf = "REDUCE RISK", "red", "VERY HIGH"
        elif risk_off >= 3:
            consensus, color, conf = "DEFENSIVE", "yellow", "HIGH"
        elif risk_on >= 4:
            consensus, color, conf = "DEPLOY CAPITAL", "green", "VERY HIGH"
        elif risk_on >= 3:
            consensus, color, conf = "BULLISH", "green", "HIGH"
        else:
            consensus, color, conf = "WAIT & WATCH", "white", "LOW"

        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=12, style="bold")
        tbl.add_column(width=14)

        tbl.add_row("Consensus", Text(consensus, style=f"bold {color}"))
        tbl.add_row("Confidence", Text(conf, style="cyan"))
        tbl.add_row("Risk-Off", f"{risk_off} / 5")
        tbl.add_row("Risk-On", f"{risk_on} / 5")
        tbl.add_row("", "")
        for label, vote_str, vstyle in votes:
            tbl.add_row(label, Text(vote_str, style=vstyle))

        sq = self._refresh_scan_quality()
        bte = sq.get("bte_advisory", {}) if isinstance(sq, dict) else {}
        bte_compare = sq.get("bte_score_comparison", {}) if isinstance(sq, dict) else {}
        bte_short = sq.get("bte_short_advisory", {}) if isinstance(sq, dict) else {}
        bte_short_compare = sq.get("bte_short_score_comparison", {}) if isinstance(sq, dict) else {}
        if bte or bte_compare:
            tbl.add_row("", "")
            if bte:
                if bte.get("guidance_ready"):
                    top_state = str(bte.get("top_candidate_state") or "ACTIVE")
                    top_window = str(bte.get("top_candidate_state_window") or "—")
                    tbl.add_row("BTE", Text(top_state, style="cyan"))
                    tbl.add_row("BTE Time", Text(top_window, style="cyan"))
                else:
                    reason = self._compact_bte_guidance_reason(
                        str(bte.get("guidance_reason") or "insufficient_breakout_sample")
                    )
                    tbl.add_row("BTE", Text("HOLDOUT", style="yellow"))
                    tbl.add_row("BTE Why", Text(reason, style="yellow"))

            if bte_compare:
                if bte_compare.get("guidance_ready"):
                    winner = str(bte_compare.get("winner_dimension") or "NONE")
                    best_bte = str(bte_compare.get("best_bte_bucket") or "—")
                    best_raw = str(bte_compare.get("best_raw_bucket") or "—")
                    tbl.add_row("BTE vs Raw", Text(winner, style="cyan"))
                    tbl.add_row("Buckets", Text(f"BTE {best_bte} | RAW {best_raw}", style="dim"))
                elif not bte and bte_compare.get("guidance_reason"):
                    reason = self._compact_bte_guidance_reason(str(bte_compare.get("guidance_reason")))
                    tbl.add_row("BTE", Text("HOLDOUT", style="yellow"))
                    tbl.add_row("BTE Why", Text(reason, style="yellow"))

        if bte_short or bte_short_compare:
            tbl.add_row("", "")
            if bte_short:
                if bte_short.get("guidance_ready"):
                    top_state = str(bte_short.get("top_candidate_state") or "ACTIVE")
                    top_window = str(bte_short.get("top_candidate_state_window") or "—")
                    tbl.add_row("BTE-S", Text(top_state, style="cyan"))
                    tbl.add_row("BTE-S Time", Text(top_window, style="cyan"))
                else:
                    reason = self._compact_bte_guidance_reason(
                        str(bte_short.get("guidance_reason") or "insufficient_breakdown_sample")
                    )
                    tbl.add_row("BTE-S", Text("HOLDOUT", style="yellow"))
                    tbl.add_row("BTE-S Why", Text(reason, style="yellow"))

            if bte_short_compare:
                if bte_short_compare.get("guidance_ready"):
                    winner = str(bte_short_compare.get("winner_dimension") or "NONE")
                    best_bte = str(bte_short_compare.get("best_bte_bucket") or "—")
                    best_raw = str(bte_short_compare.get("best_raw_bucket") or "—")
                    tbl.add_row("BTE-S vs Raw", Text(winner, style="cyan"))
                    tbl.add_row("Short Buckets", Text(f"BTE {best_bte} | RAW {best_raw}", style="dim"))
                elif not bte_short and bte_short_compare.get("guidance_reason"):
                    reason = self._compact_bte_guidance_reason(str(bte_short_compare.get("guidance_reason")))
                    tbl.add_row("BTE-S", Text("HOLDOUT", style="yellow"))
                    tbl.add_row("BTE-S Why", Text(reason, style="yellow"))

        if self.account_info:
            pnl = self.account_info.get('daily_pnl', 0.0)
            if pnl > 500:
                port_state = f"PROFITABLE +${pnl:,.0f}"
            elif pnl < -500:
                port_state = f"DRAWDOWN -${abs(pnl):,.0f}"
            else:
                port_state = "NEUTRAL"
            tbl.add_row("", "")
            tbl.add_row("Portfolio", port_state)

        return Panel(tbl, title="[bold white]STRATEGIC CONSENSUS", border_style="white", padding=(0, 1))

    def _panel_smart_alerts(self) -> Panel:
        tbl = Table(show_header=False, box=None, padding=(0, 1))
        tbl.add_column(width=38)

        if self.confluence_analytics['active'] and self.confluence_analytics['count'] > 0:
            tickers = ", ".join(self.confluence_analytics['tickers'][:4]) or "—"
            tbl.add_row(Text("TRIPLE CONFLUENCE SIGNAL", style="bold white on red"))
            tbl.add_row(f"Tickers: {tickers}")
            tbl.add_row(f"Detected: {self.confluence_analytics['detection_time'] or '—'}")
            tbl.add_row("Action: Voyager triple path uses 1.8x (risk-gated)")
        else:
            tbl.add_row(Text("No active alerts", style="dim yellow"))
            overlap = int(self.confluence_analytics.get('pre_signal_overlap', 0) or 0)
            if overlap > 0:
                tbl.add_row(f"[dim]Pre-signal overlap: {overlap} symbols[/dim]")
            else:
                tbl.add_row("[dim]Monitoring confluence + risk triggers[/dim]")

        # Surface VIX alert if elevated
        if self.cached_regime and self.cached_regime.get('vix_level', 0) > 30:
            vix = self.cached_regime['vix_level']
            tbl.add_row("")
            tbl.add_row(Text(f"VIX EXTREME: {vix:.1f}", style="bold red"))
            tbl.add_row("Sniper gated. Remora gated (chaos > 30). Voyager only.")
        elif self.cached_regime and self.cached_regime.get('vix_level', 0) >= 20:
            vix = self.cached_regime['vix_level']
            tbl.add_row("")
            tbl.add_row(Text(f"VIX WARNING: {vix:.1f}", style="bold yellow"))
            tbl.add_row("Sniper gated. Voyager/Remora active.")

        # Reaper alert: VIX >= 28 activates fear-opportunity scanner
        if self.cached_regime and self.cached_regime.get('vix_level', 0) >= 28:
            vix = self.cached_regime['vix_level']
            ctr_status = self.strategy_status.get('contrarian', 'LOADING')
            tbl.add_row("")
            tbl.add_row(Text(f"⚡ REAPER ACTIVE: VIX {vix:.1f}", style="bold white on dark_orange"))
            if ctr_status == 'HUNTING':
                tbl.add_row("Fear setups found — check opportunities panel")
            else:
                tbl.add_row("Scanning for panic-selling reversals...")

        return Panel(tbl, title="[bold white]SMART ALERTS", border_style="red", padding=(0, 1))

    def _panel_footer(self) -> Panel:
        self._refresh_heartbeat()
        daemon_status, daemon_style = self._daemon_status()
        txt = Text()
        txt.append("LOG: ", style="dim")
        txt.append(os.path.basename(self.log_file), style="white")
        txt.append("  |  ", style="dim")
        txt.append(f"Log age: {self._format_age(self.last_log_update)}", style="white")
        txt.append("  |  ", style="dim")
        txt.append(f"Scan age: {self._format_age(self.last_scan_dt)}", style="white")
        txt.append("  |  ", style="dim")
        txt.append("Daemon: ", style="white")
        txt.append(daemon_status, style=daemon_style)
        txt.append("  |  ", style="dim")
        txt.append("Institutional data: 60s  Broker: 10s  Render: 2s", style="dim")
        txt.append("  |  ", style="dim")
        txt.append("CTRL+C TO EXIT", style="dim italic")
        return Panel(txt, border_style="dim")

    # ── layout ────────────────────────────────────────────────────────────────

    def generate_layout(self) -> Layout:
        """
        Row budget (~53 lines):
          header    3
          row1     10  Regime | Rotation | Breadth
          row2     10  Options | Inter-Market | Macro Calendar
          row3     10  Strategy | Portfolio | Confluence
          row4     12  Positions | Executions | Consensus
          row5      5  Smart Alerts (full width)
          footer    3
        """
        layout = Layout()
        layout.split_column(
            Layout(name="header",  size=3),
            Layout(name="row1",    size=10),
            Layout(name="row2",    size=10),
            Layout(name="row3",    size=10),
            Layout(name="row4",    size=14),
            Layout(name="row5",    size=5),
            Layout(name="footer",  size=3),
        )

        layout["header"].update(self._panel_header())

        layout["row1"].split_row(
            Layout(self._panel_market_regime()),
            Layout(self._panel_sector_rotation()),
            Layout(self._panel_breadth()),
        )

        layout["row2"].split_row(
            Layout(self._panel_options()),
            Layout(self._panel_inter_market()),
            Layout(self._panel_macro_calendar()),
        )

        layout["row3"].split_row(
            Layout(self._panel_strategy()),
            Layout(self._panel_portfolio()),
            Layout(self._panel_confluence()),
        )

        layout["row4"].split_row(
            Layout(self._panel_positions(), ratio=3),
            Layout(self._panel_executions(), ratio=2),
            Layout(self._panel_pathway_performance(), ratio=2),
            Layout(self._panel_consensus(), ratio=2),
        )

        layout["row5"].update(self._panel_smart_alerts())
        layout["footer"].update(self._panel_footer())

        return layout

    # ── run loop ──────────────────────────────────────────────────────────────

    def run(self):
        self.console.clear()

        # Initial load — institutional data (heavy, multi-source) starts in background
        self.parse_log_file()
        self._refresh_broker_state()
        threading.Thread(target=self.refresh_institutional_data, daemon=True).start()

        try:
            with Live(self.generate_layout(), refresh_per_second=0.5,
                      console=self.console, screen=True) as live:
                while True:
                    self.parse_log_file()
                    # Institutional data has 15s internal gate; spawn non-blocking so UI never freezes
                    threading.Thread(target=self.refresh_institutional_data, daemon=True).start()
                    self._refresh_broker_state()   # 10s internal gate — fast when throttled
                    live.update(self.generate_layout())
                    time.sleep(2)
        except KeyboardInterrupt:
            self.console.clear()
            self.console.print("\n[white]Terminal closed[/white]\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Chief Strategist Terminal')
    parser.add_argument('--log', help='Log file path', default=None)
    args = parser.parse_args()

    try:
        terminal = ChiefStrategistTerminal(log_file=args.log)
        terminal.run()
    except ImportError as e:
        print(f"\nMissing dependency: {e}\npip install rich\n")
    except Exception as e:
        import traceback
        print(f"\nError: {e}\n")
        traceback.print_exc()


if __name__ == "__main__":
    main()
