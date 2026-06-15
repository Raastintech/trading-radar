#!/usr/bin/env python3
"""
Unified Master Trader V3 - Multi-Strategy Daemon
"""

import os
import sys
import time
import logging
import signal
import json
import re
import sqlite3
from datetime import datetime, time as dt_time
from typing import Dict, List, Optional, Set
import pytz
from dataclasses import dataclass
from secure_env import load_runtime_env

load_runtime_env("unified_master_trader_v3")

# Verify Alpaca credentials
ALPACA_API_KEY = os.getenv('ALPACA_API_KEY') or os.getenv('APCA_API_KEY_ID')
ALPACA_SECRET_KEY = os.getenv('ALPACA_SECRET_KEY') or os.getenv('APCA_API_SECRET_KEY')

if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
    print("⚠️  WARNING: Alpaca credentials not found!")
    print("   Check shell env or SNIPER_ENV_PATH provides:")
    print("   ALPACA_API_KEY / APCA_API_KEY_ID")
    print("   ALPACA_SECRET_KEY / APCA_API_SECRET_KEY")

# Core imports
from alpaca_data import AlpacaDataFeed
from vix_snapshot import get_vix_snapshot
from voyager_production_v2_complete import VoyagerProductionV2Complete

# Adaptive universe imports
from voyager_adaptive_universe import VoyagerAdaptiveUniverse
from sniper_adaptive_universe import SniperAdaptiveUniverse
from sniper_scanner_v2 import SniperScannerV2
from remora_adaptive_universe import RemoraAdaptiveUniverse
from remora_scanner_v2 import RemoraScanner
from short_scanner_v1 import ShortScanner
from universe_snapshot_builder import UniverseSnapshotBuilder
from triple_confluence_detector import TripleConfluenceDetector, ConfluenceOpportunity
from execution_policy import require_not_halted_or_frozen
from decision_logger import DecisionLogger
from performance_tracker import PerformanceTracker
from portfolio_coordinator import PortfolioCoordinator
from system_fixes import OptimizedRiskOverlay
from security_baseline import is_entry_allowed, startup_security_report
from market_breadth_context import MarketBreadthContextService
try:
    from short_management_shadow_monitor import ShortManagementShadowMonitor
    _SHORT_MANAGEMENT_SHADOW_AVAILABLE = True
except ImportError:
    ShortManagementShadowMonitor = None  # type: ignore
    _SHORT_MANAGEMENT_SHADOW_AVAILABLE = False

try:
    from contrarian_scanner import ContrarianScanner
    _CONTRARIAN_AVAILABLE = True
except ImportError:
    ContrarianScanner = None  # type: ignore
    _CONTRARIAN_AVAILABLE = False

try:
    from portfolio_correlation_guard import PortfolioCorrelationGuard
    _CORRELATION_GUARD_AVAILABLE = True
except ImportError:
    PortfolioCorrelationGuard = None  # type: ignore
    _CORRELATION_GUARD_AVAILABLE = False

try:
    from trailing_stop_manager import (
        TrailingStopManager,
        ensure_trail_schema as _ensure_trail_schema_impl,
        log_trail_event as _log_trail_event,
    )
    _TRAIL_STOP_AVAILABLE = True
except ImportError:
    TrailingStopManager = None  # type: ignore
    _ensure_trail_schema_impl = None  # type: ignore
    _log_trail_event = None  # type: ignore
    _TRAIL_STOP_AVAILABLE = False
try:
    from trading_state import write_heartbeat
except ImportError:
    def write_heartbeat(*args, **kwargs):
        return None

# Phase 3 — thesis monitoring (optional; degrades gracefully if import fails)
try:
    from thesis_monitor import ThesisMonitor
    _THESIS_MONITOR_AVAILABLE = True
except ImportError:
    _THESIS_MONITOR_AVAILABLE = False

# Phase 3 — enrichment + forecast modules (optional)
try:
    from forecast_engine import ForecastEngine
    _FORECAST_ENGINE_AVAILABLE = True
except ImportError:
    _FORECAST_ENGINE_AVAILABLE = False

try:
    from shadow_rate_forecaster import ShadowRateForecaster
    _SHADOW_RATE_FORECASTER_AVAILABLE = True
except ImportError:
    ShadowRateForecaster = None  # type: ignore
    _SHADOW_RATE_FORECASTER_AVAILABLE = False

try:
    from revision_scorer import RevisionScorer
    _REVISION_SCORER_AVAILABLE = True
except ImportError:
    _REVISION_SCORER_AVAILABLE = False

try:
    from insider_cluster_scorer import InsiderClusterScorer
    _INSIDER_SCORER_AVAILABLE = True
except ImportError:
    _INSIDER_SCORER_AVAILABLE = False

try:
    from short_risk_scorer import ShortRiskScorer
    _SHORT_RISK_SCORER_AVAILABLE = True
except ImportError:
    _SHORT_RISK_SCORER_AVAILABLE = False

try:
    from volume_profile import VolumeProfileAnalyzer
    _VOLUME_PROFILE_AVAILABLE = True
except ImportError:
    _VOLUME_PROFILE_AVAILABLE = False

try:
    from candidate_ranking import CandidateRanker
    _CANDIDATE_RANKER_AVAILABLE = True
except ImportError:
    CandidateRanker = None  # type: ignore
    _CANDIDATE_RANKER_AVAILABLE = False

try:
    from breakout_timing_engine import BreakoutTimingTracker
    _BTE_AVAILABLE = True
except ImportError:
    BreakoutTimingTracker = None  # type: ignore
    _BTE_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class _SniperOpp:
    """Lightweight opportunity wrapper for Sniper dict results (for execution compatibility)."""
    ticker: str
    direction: str
    entry_price: float
    target_price: float
    stop_loss: float
    score: int
    risk_reward: float
    shares: int = 1  # Placeholder; execution layer uses its own sizing
    expected_return: float = 0.0
    strategy: str = "SNIPER"
    grade: str = "N/A"
    growth_mode: bool = False
    tier_breakdown: Optional[Dict] = None
    confidence_summary: Optional[Dict] = None
    pathway_qualification: Optional[Dict] = None
    research_only: bool = False
    dormant: bool = False
    execution_block_reason: Optional[str] = None
    macro_gate_context: Optional[Dict] = None
    machine_rank_score: float = 0.0
    machine_rank_reason: str = ""
    machine_rank_prior: Optional[str] = None


@dataclass
class TradeExecution:
    """Record of a trade execution"""
    ticker: str
    direction: str  # 'LONG' or 'SHORT'
    entry_price: float
    target_price: float
    stop_loss: float
    position_size: int
    position_value: float
    risk_amount: float
    confluence_level: int
    timestamp: datetime


class UnifiedMasterTraderV3:
    """
    Unified Master Trader V3 - Complete Multi-Strategy Platform
    
    FEATURES:
    - Voyager: Strategic 6-18 month positions
    - Sniper: Tactical 3-30 day momentum (universe ready)
    - Remora: Opportunistic 2-48 hour inefficiency (universe ready)
    - Triple Confluence Detection
    - Adaptive Universe Discovery
    - Automated Execution
    - Complete Risk Management
    
    MODES:
    - PAPER: Paper trading (safe testing)
    - LIVE: Real money (production)
    """
    
    def __init__(
        self,
        account_size: float = 100000,
        execution_mode: str = 'PAPER',
        enable_voyager: bool = True,
        enable_adaptive_universes: bool = True,
        scan_interval_minutes: int = 5,
        verbose: bool = True
    ):
        """
        Initialize Unified Master Trader V3
        
        Args:
            account_size: Total account capital
            execution_mode: 'PAPER' or 'LIVE'
            enable_voyager: Enable Voyager strategy
            enable_adaptive_universes: Enable adaptive universe discovery
            scan_interval_minutes: Minutes between scans
            verbose: Detailed logging
        """
        
        self.account_size = account_size
        self.execution_mode = execution_mode.upper()
        self.enable_voyager = enable_voyager
        self.enable_adaptive_universes = enable_adaptive_universes
        self.scan_interval_minutes = scan_interval_minutes
        self.verbose = verbose
        
        # Trading state
        self.running = False
        self.active_positions = {}
        self.executed_trades = []
        self.last_scan_time = None
        self.scan_count = 0
        self._heartbeat_stage = "startup"
        self._last_progress_heartbeat_ts = 0.0
        self._heartbeat_progress_interval_sec = max(
            20.0,
            self._as_float(os.getenv("HEARTBEAT_PROGRESS_INTERVAL_SEC"), 45.0),
        )
        self.current_run_id = None
        self.run_id = None
        self._current_regime_context = {
            "status": "UNKNOWN",
            "volatility": "UNKNOWN",
            "regime_overall": "UNKNOWN",
            "regime_spy": "UNKNOWN",
            "regime_vix": "UNKNOWN",
            "vix_level": 0.0,
        }
        self._last_reconcile_ts: float = 0.0   # epoch seconds; throttles intra-scan reconciliation
        self.killswitch_path = os.getenv("TRADING_KILLSWITCH_FILE", ".trading_killswitch")
        self._forced_exit_reasons: Dict[str, str] = {}
        self._beta_cache: Dict[str, float] = {}
        self._warned_rr_alignment: set = set()
        self._contrarian_import_warned = False
        self._opening_window_block = False
        self._opening_window_minutes: Optional[float] = None
        self.max_loss_circuit_breaker_pct = self._as_float(
            os.getenv("CIRCUIT_BREAKER_MAX_LOSS_PCT"), 0.25
        )
        self._last_trailing_check_ts: float = 0.0
        self.voyager_distribution_auto_exit = str(
            os.getenv("VOYAGER_DISTRIBUTION_AUTO_EXIT", "0")
        ).strip().lower() in ("1", "true", "yes", "on")
        self._thesis_exit_cooldown: Dict[str, float] = {}
        self.security_report = startup_security_report(
            component="unified_master_trader_v3",
            execution_mode=self.execution_mode,
            killswitch_path=self.killswitch_path,
        )
        self._latest_market_breadth_context: Dict = {}
        
        # Market hours (ET)
        self.et_tz = pytz.timezone('America/New_York')
        self.market_open = dt_time(9, 30)
        self.market_close = dt_time(16, 0)
        self.strategy_scan_cadence_minutes = self._load_strategy_scan_cadences()
        self._strategy_last_scan_started_at: Dict[str, datetime] = {}

        # Initialize Alpaca Trading Client
        api_key = os.getenv('ALPACA_API_KEY') or os.getenv('APCA_API_KEY_ID')
        secret_key = os.getenv('ALPACA_SECRET_KEY') or os.getenv('APCA_API_SECRET_KEY')

        if api_key and secret_key:
            from alpaca.trading.client import TradingClient
            self.trading_client = TradingClient(api_key, secret_key, paper=True)
            logger.info("✅ Alpaca Trading Client initialized (PAPER mode)")
        else:
            self.trading_client = None
            logger.warning("⚠️  No Alpaca credentials - trading disabled")

        # Fetch live account equity from Alpaca (overrides hardcoded default)
        if self.trading_client:
            try:
                live_account = self.trading_client.get_account()
                live_equity = float(live_account.equity)
                if live_equity > 1000:
                    self.account_size = live_equity
                    logger.info(f"💰 Live account equity: ${live_equity:,.2f} (fetched from Alpaca)")
                else:
                    logger.warning(f"⚠️  Live equity ${live_equity:,.2f} too low — using parameter ${account_size:,.0f}")
            except Exception as e:
                logger.warning(f"⚠️  Could not fetch live equity: {e} — using parameter ${account_size:,.0f}")

        # Initialize system
        logger.info("="*80)
        logger.info("🚀 UNIFIED MASTER TRADER V3 - INITIALIZING")
        logger.info("="*80)
        logger.info(f"💰 Account: ${self.account_size:,.0f}")
        logger.info(f"🎯 Mode: {execution_mode}")
        logger.info(f"📊 Adaptive Universes: {'ENABLED' if enable_adaptive_universes else 'DISABLED'}")
        logger.info(f"⏱️  Daemon loop interval: {scan_interval_minutes} minutes")
        logger.info(
            "⏱️  Strategy cadences: Voyager=%sm Sniper=%sm Remora=%sm Short=%sm Contrarian=%sm",
            self._get_strategy_scan_cadence_minutes("VOYAGER"),
            self._get_strategy_scan_cadence_minutes("SNIPER"),
            self._get_strategy_scan_cadence_minutes("REMORA"),
            self._get_strategy_scan_cadence_minutes("SHORT"),
            self._get_strategy_scan_cadence_minutes("CONTRARIAN"),
        )
        if not self.security_report.get("env_file_secure", True):
            logger.warning(f"⚠️  {self.security_report.get('env_file_message')}")
        if not self.security_report.get("live_armed", True):
            logger.warning(f"⛔ {self.security_report.get('live_mode_message')}")
        if self.security_report.get("killswitch_active", False):
            logger.warning(f"⛔ Entry kill-switch active: {self.security_report.get('killswitch_reason')}")
        if self.voyager_distribution_auto_exit:
            logger.info("🛑 Voyager distribution auto-exit: ENABLED")
        
        # Initialize data feed
        self.data_feed = AlpacaDataFeed()
        self.decision_logger = DecisionLogger()
        self.performance_tracker = PerformanceTracker()
        self.db_path = self.performance_tracker.db_path
        self.market_breadth_context = MarketBreadthContextService(db_path=self.db_path)
        self.short_management_shadow_monitor = (
            ShortManagementShadowMonitor(db_path=self.db_path)
            if _SHORT_MANAGEMENT_SHADOW_AVAILABLE else None
        )
        self.portfolio = PortfolioCoordinator(account_size=self.account_size, db_path=self.db_path)
        self.portfolio.sync_realized_from_db = True
        self.portfolio._refresh_realized_state_from_db()
        self.risk_overlay = OptimizedRiskOverlay()
        self.correlation_guard = PortfolioCorrelationGuard() if _CORRELATION_GUARD_AVAILABLE else None
        self.trailing_stop_manager = TrailingStopManager() if _TRAIL_STOP_AVAILABLE else None

        # Macro event window gate — strategy-aware time-window controls
        try:
            from macro_calendar import MacroCalendar as _MacroCalendar
            self._macro_cal = _MacroCalendar()
        except Exception:
            self._macro_cal = None
        self._macro_window_cache: Optional[Dict] = None
        self._macro_window_ts: float = 0.0
        
        # Initialize Voyager
        self.voyager = None
        if enable_voyager:
            logger.info("📍 Initializing Voyager (Strategic)...")
            self.voyager = VoyagerProductionV2Complete(
                account_size=account_size * 0.4,  # 40% allocation
                verbose=False,
                growth_mode=False
            )
            logger.info("   ✅ Voyager ready")
        
        # Universe builders (initialized on demand)
        self.universe_snapshot_builder = None
        self.voyager_universe_builder = None
        self.sniper_universe_builder = None
        self.remora_universe_builder = None
        self.confluence_detector = None
        
        if enable_adaptive_universes:
            logger.info("🔍 Initializing Adaptive Universe Builders...")
            self.universe_snapshot_builder = UniverseSnapshotBuilder(
                self.data_feed,
                trading_client=self.trading_client,
            )
            self.voyager_universe_builder = VoyagerAdaptiveUniverse(self.data_feed)
            self.sniper_universe_builder = SniperAdaptiveUniverse(self.data_feed)
            self.sniper_scanner = SniperScannerV2(self.data_feed, account_equity=self.account_size)
            self.remora_universe_builder = RemoraAdaptiveUniverse(self.data_feed)
            self.remora_scanner = RemoraScanner(self.data_feed, account_equity=self.account_size)
            self.short_scanner = ShortScanner(self.data_feed, account_equity=self.account_size)
            self.confluence_detector = TripleConfluenceDetector()
            logger.info("   ✅ Adaptive universes ready")
        else:
            self.sniper_scanner = None
            self.remora_scanner = None
            self.short_scanner = None

        # Contrarian scanner — regime-gated and import-guarded.
        if _CONTRARIAN_AVAILABLE and ContrarianScanner is not None:
            self.contrarian_scanner = ContrarianScanner()
            logger.info(
                f"📉 Contrarian scanner ready (activates at VIX >= {ContrarianScanner.VIX_TRIGGER:.0f})"
            )
        else:
            self.contrarian_scanner = None
            logger.warning("⚠️  Contrarian scanner unavailable (import failed)")

        # Phase 3 — thesis monitor
        if _THESIS_MONITOR_AVAILABLE:
            self.thesis_monitor = ThesisMonitor(data_feed=self.data_feed)
            logger.info("🧭 Thesis monitor ready (INTACT/WARN/BROKEN lifecycle)")
        else:
            self.thesis_monitor = None

        # Phase 3 — entry enrichment engines (optional)
        _base_forecaster = ForecastEngine(db_path=self.db_path) if _FORECAST_ENGINE_AVAILABLE else None
        if _SHADOW_RATE_FORECASTER_AVAILABLE and ShadowRateForecaster is not None:
            self.forecast_engine = ShadowRateForecaster(
                db_path=self.db_path,
                base_forecaster=_base_forecaster,
            )
        else:
            self.forecast_engine = _base_forecaster
        self.revision_scorer = RevisionScorer() if _REVISION_SCORER_AVAILABLE else None
        self.insider_scorer = InsiderClusterScorer() if _INSIDER_SCORER_AVAILABLE else None
        self.short_risk_scorer = ShortRiskScorer() if _SHORT_RISK_SCORER_AVAILABLE else None
        self.volume_profile_analyzer = VolumeProfileAnalyzer(self.data_feed) if _VOLUME_PROFILE_AVAILABLE else None
        self.candidate_ranker = CandidateRanker(db_path=self.db_path) if _CANDIDATE_RANKER_AVAILABLE else None
        self.breakout_timing_tracker = (
            BreakoutTimingTracker(db_path=self.db_path, data_feed=self.data_feed)
            if _BTE_AVAILABLE and BreakoutTimingTracker is not None
            else None
        )
        self._phase3_ticker_cache: Dict[str, Dict] = {}
        if any([
            self.forecast_engine, self.revision_scorer, self.insider_scorer,
            self.short_risk_scorer, self.volume_profile_analyzer
        ]):
            logger.info("🧠 Phase 3 enrichment engines ready")
        if self.candidate_ranker is not None:
            logger.info("🏁 Best-idea ranker ready")
        if self.breakout_timing_tracker is not None:
            logger.info("⏱️  BTE advisory timing layer ready")

        # Phase A — isolated options spreads overlay (non-fatal, additive only)
        options_enable = str(os.getenv("OPTIONS_ENABLE", "0")).strip().lower() in ("1", "true", "yes", "on")
        if options_enable:
            try:
                from options_master import OptionsMaster

                self._options_master = OptionsMaster(
                    trading_client=self.trading_client,
                    db_path=self.db_path,
                    account_size=self.account_size,
                    execution_mode=self.execution_mode,
                )
                if self._options_master.is_enabled():
                    logger.info("🧩 Options spreads overlay initialized (OPTIONS_ENABLE=1)")
            except ImportError:
                self._options_master = None
            except Exception as exc:
                self._options_master = None
                logger.warning(f"⚠️  Options spreads overlay init failed (non-fatal): {exc}")
        else:
            self._options_master = None
        
        logger.info("="*80)
        logger.info("✅ UNIFIED MASTER TRADER V3 READY")
        logger.info("="*80)

        # Phase 4.3 — pilot schema migration (additive, idempotent, import-guarded)
        self._ensure_pilot_schema()
        # Phase 5.1 — trailing stop schema migration (additive, idempotent, import-guarded)
        self._ensure_trail_schema()
    
    def is_market_hours(self) -> bool:
        """Check if currently in market hours"""
        now_et = datetime.now(self.et_tz)
        current_time = now_et.time()
        is_weekday = now_et.weekday() < 5  # Monday=0, Friday=4
        
        return is_weekday and self.market_open <= current_time <= self.market_close

    def _heartbeat_positions_count(self) -> int:
        trading_client = getattr(self, "trading_client", None)
        if trading_client:
            try:
                return len(trading_client.get_all_positions())
            except Exception:
                pass
        return len(getattr(self, "active_positions", {}) or {})

    def _write_heartbeat(
        self,
        market_status: str,
        is_trading: bool,
        *,
        stage: Optional[str] = None,
        scan_ts: Optional[datetime] = None,
    ):
        portfolio_status = {}
        try:
            portfolio = getattr(self, "portfolio", None)
            portfolio_status = portfolio.get_portfolio_status() if portfolio is not None else {}
        except Exception:
            portfolio_status = {}
        resolved_stage = str(stage or getattr(self, "_heartbeat_stage", "idle") or "idle")
        self._heartbeat_stage = resolved_stage
        write_heartbeat(
            source="unified_master_trader_v3",
            scan_ts=scan_ts or getattr(self, "last_scan_time", None) or datetime.now(),
            extra={
                "scan_count": int(getattr(self, "scan_count", 0) or 0),
                "positions_count": self._heartbeat_positions_count(),
                "market_status": market_status,
                "is_trading": is_trading,
                "version": "3.0",
                "heartbeat_stage": resolved_stage,
                "portfolio_mode": portfolio_status.get("mode", "UNKNOWN"),
                "portfolio_circuit_breaker": bool(portfolio_status.get("circuit_breaker", False)),
                "portfolio_daily_pnl": float(portfolio_status.get("daily_pnl", 0.0) or 0.0),
                "portfolio_weekly_pnl": float(portfolio_status.get("weekly_pnl", 0.0) or 0.0),
            },
        )
        self._last_progress_heartbeat_ts = time.monotonic()

    def _write_progress_heartbeat(self, stage: str, *, force: bool = False) -> bool:
        now_mono = time.monotonic()
        interval = float(getattr(self, "_heartbeat_progress_interval_sec", 45.0) or 45.0)
        if not force and (now_mono - float(getattr(self, "_last_progress_heartbeat_ts", 0.0) or 0.0)) < interval:
            return False
        try:
            market_status = "OPEN" if self.is_market_hours() else "CLOSED"
        except Exception:
            market_status = "UNKNOWN"
        self._write_heartbeat(
            market_status=market_status,
            is_trading=bool(getattr(self, "running", False)),
            stage=stage,
            scan_ts=getattr(self, "last_scan_time", None) or datetime.now(),
        )
        return True

    def _make_progress_callback(self, stage_prefix: str):
        def _callback(detail: Optional[str] = None, force: bool = False):
            stage = stage_prefix if not detail else f"{stage_prefix}:{detail}"
            self._write_progress_heartbeat(stage, force=force)

        return _callback

    def _market_session_now(self) -> str:
        now_et = datetime.now(self.et_tz)
        if now_et.weekday() >= 5:
            return "CLOSED"
        mins = now_et.hour * 60 + now_et.minute
        if 4 * 60 <= mins < 9 * 60 + 30:
            return "PRE"
        if 9 * 60 + 30 <= mins < 16 * 60:
            return "REGULAR"
        if 16 * 60 <= mins < 20 * 60:
            return "POST"
        return "CLOSED"

    def _load_strategy_scan_cadences(self) -> Dict[str, int]:
        defaults = {
            "VOYAGER": ("VOYAGER_SCAN_INTERVAL_MINUTES", 60),
            "SNIPER": ("SNIPER_SCAN_INTERVAL_MINUTES", 15),
            "REMORA": ("REMORA_SCAN_INTERVAL_MINUTES", 5),
            "SHORT": ("SHORT_SCAN_INTERVAL_MINUTES", 60),
            "CONTRARIAN": ("CONTRARIAN_SCAN_INTERVAL_MINUTES", 60),
        }
        resolved: Dict[str, int] = {}
        for strategy, (env_name, default_value) in defaults.items():
            raw = os.getenv(env_name, str(default_value))
            parsed = self._as_float(raw, None)
            if parsed is None or parsed < 1:
                resolved[strategy] = default_value
            else:
                resolved[strategy] = max(1, int(round(parsed)))
        return resolved

    def _get_strategy_scan_cadence_minutes(self, strategy: str) -> int:
        strategy = str(strategy or "").upper().strip()
        cadences = getattr(self, "strategy_scan_cadence_minutes", None) or {}
        if strategy in cadences:
            return int(cadences[strategy])
        return {
            "VOYAGER": 60,
            "SNIPER": 15,
            "REMORA": 5,
            "SHORT": 60,
            "CONTRARIAN": 60,
        }.get(strategy, max(1, int(round(getattr(self, "scan_interval_minutes", 5) or 5))))

    def _should_run_strategy_scan(self, strategy: str, now_et: Optional[datetime] = None) -> bool:
        strategy = str(strategy or "").upper().strip()
        now_et = now_et or datetime.now(self.et_tz)
        last_scans = getattr(self, "_strategy_last_scan_started_at", None) or {}
        last_started = last_scans.get(strategy)
        if last_started is None:
            return True
        cadence_minutes = self._get_strategy_scan_cadence_minutes(strategy)
        elapsed_minutes = (now_et - last_started).total_seconds() / 60.0
        return elapsed_minutes >= float(cadence_minutes)

    def _mark_strategy_scan_started(self, strategy: str, now_et: Optional[datetime] = None) -> None:
        strategy = str(strategy or "").upper().strip()
        now_et = now_et or datetime.now(self.et_tz)
        last_scans = getattr(self, "_strategy_last_scan_started_at", None)
        if not isinstance(last_scans, dict):
            last_scans = {}
            self._strategy_last_scan_started_at = last_scans
        last_scans[strategy] = now_et

    def _strategy_cadence_hold_message(self, strategy: str, now_et: Optional[datetime] = None) -> str:
        strategy = str(strategy or "").upper().strip()
        now_et = now_et or datetime.now(self.et_tz)
        last_scans = getattr(self, "_strategy_last_scan_started_at", None) or {}
        last_started = last_scans.get(strategy)
        cadence_minutes = self._get_strategy_scan_cadence_minutes(strategy)
        if last_started is None:
            return f"{strategy} cadence ready"
        elapsed_minutes = max(0.0, (now_et - last_started).total_seconds() / 60.0)
        wait_remaining = max(0.0, float(cadence_minutes) - elapsed_minutes)
        return (
            f"{strategy} cadence hold: {elapsed_minutes:.1f}m since last scan, "
            f"{wait_remaining:.1f}m until next scheduled run"
        )

    def _is_remora_open_window(self, now_et: Optional[datetime] = None) -> bool:
        """
        Returns True when the current ET time is within the REMORA open window
        (9:30 AM–2:30 PM ET on a weekday).

        REMORA is a catalyst strategy. It still cares most about the open, but real
        macro sympathy, M&A tape, and sector contagion can trigger well into the
        afternoon. We stop at 2:30 PM ET to avoid late-day entries with poor exit
        quality and noisy closing-auction tape.

        Controlled by env var REMORA_OPEN_WINDOW_ONLY (default: "1").
        Set to "0" to allow REMORA to run at any session time.
        """
        if os.getenv("REMORA_OPEN_WINDOW_ONLY", "1").strip() == "0":
            return True  # gate disabled — allow all session times
        now_et = now_et or datetime.now(self.et_tz)
        if now_et.weekday() >= 5:
            return False  # weekend
        mins = now_et.hour * 60 + now_et.minute
        return 9 * 60 + 30 <= mins < 14 * 60 + 30  # 9:30 AM–2:30 PM ET

    def _start_decision_run(self, universe_data: Dict, regime_context: Optional[Dict] = None) -> Optional[str]:
        try:
            watchlist_size = len(
                set(universe_data.get('voyager_universe', set()))
                | set(universe_data.get('sniper_universe', set()))
                | set(universe_data.get('remora_universe', set()))
            )
            regime_context = self._normalize_regime_context(
                regime_context or self._current_regime_context
            )
            run_id = self.decision_logger.start_run(
                engine_name="UNIFIED_MASTER_V3",
                notes=f"scan_cycle_{self.scan_count}",
                watchlist_size=watchlist_size,
                market_session=self._market_session_now(),
                regime_status=regime_context.get("status"),
                regime_volatility=regime_context.get("volatility"),
            )
            self.current_run_id = run_id
            self.run_id = run_id
            return run_id
        except Exception as exc:
            logger.warning(f"Decision run start failed: {exc}")
            return None

    def _finalize_decision_run(self):
        run_id = self.current_run_id or self.run_id
        if not run_id:
            return
        try:
            self.decision_logger.finalize_run(run_id)
        except Exception as exc:
            logger.warning(f"Decision run finalize failed: {exc}")

    def _log_short_shadow_match(
        self,
        *,
        decision_id: Optional[int],
        ticker: str,
        decision: str,
        reason: str,
        avg_score: Optional[float],
        rr: Optional[float],
        candidate_state: Optional[str],
        phase3_ctx: Optional[Dict] = None,
        regime_ctx: Optional[Dict] = None,
        notes: Optional[Dict] = None,
    ) -> None:
        if self.short_management_shadow_monitor is None or decision_id is None:
            return
        ctx = self._normalize_regime_context(regime_ctx or self._current_regime_context)
        phase3_ctx = phase3_ctx or {}
        try:
            self.short_management_shadow_monitor.log_decision(
                decision_id=decision_id,
                run_id=self.current_run_id or self.run_id,
                ticker=ticker,
                council_decision=decision,
                council_reason=reason,
                rr=rr,
                avg_score=avg_score,
                candidate_state=candidate_state or phase3_ctx.get("bte_short_candidate_state"),
                regime_status=ctx.get("status"),
                regime_volatility=ctx.get("volatility"),
                regime_vix=ctx.get("regime_vix"),
                bte_short_breakdown_probability=phase3_ctx.get("bte_short_breakdown_probability"),
                bte_short_source_dimension=phase3_ctx.get("bte_short_source_dimension"),
                bte_short_source_segment=phase3_ctx.get("bte_short_source_segment"),
                notes=notes,
            )
        except Exception as exc:
            logger.debug(f"Short shadow monitor log failed {ticker}: {exc}")

    def _log_decision_event(
        self,
        ticker: str,
        strategy: str,
        direction: str,
        decision: str,
        reason: str = "",
        opp=None,
        shares: int = 0,
        order_submitted: int = 0,
        order_id: str = None,
        position_opened: int = 0,
        execution_denied: int = 0,
        execution_deny_reason: str = None,
        notes: str = None,
        revision_score: float = None,
        insider_cluster_score: float = None,
        squeeze_risk_score: float = None,
        options_pcr: float = None,
        options_gamma: str = None,
        correlation_blocked: int = None,
        is_pilot: int = None,
        pilot_policy: str = None,
        pilot_threshold: float = None,
        pilot_decision_reason: str = None,
    ):
        run_id = self.current_run_id or self.run_id
        if not run_id:
            return
        try:
            regime_ctx = self._normalize_regime_context(self._current_regime_context)
            signal = {
                "strategy": strategy,
                "direction": direction,
                "shares": int(shares or 0),
            }
            if opp is not None:
                signal.update({
                    "entry_price": getattr(opp, "entry_price", None),
                    "stop_loss": getattr(opp, "stop_loss", None),
                    "target_price": getattr(opp, "target_price", None),
                    "risk_reward": getattr(opp, "risk_reward", None),
                    "signal": getattr(opp, "strategy", strategy),
                    "setup_type": getattr(opp, "strategy", strategy),
                    "sector": "UNKNOWN",
                })
            phase3_ctx = self._get_phase3_context(opp) if opp is not None else {}
            revision_score = revision_score if revision_score is not None else phase3_ctx.get("revision_score")
            insider_cluster_score = (
                insider_cluster_score if insider_cluster_score is not None
                else phase3_ctx.get("insider_cluster_score")
            )
            squeeze_risk_score = (
                squeeze_risk_score if squeeze_risk_score is not None
                else phase3_ctx.get("squeeze_risk_score")
            )
            # Options flow signals — read from opportunity dict (set by scanners) or phase3_ctx
            opp_dict = opp if isinstance(opp, dict) else {}
            if options_pcr is None:
                options_pcr = opp_dict.get("options_pcr") or phase3_ctx.get("options_pcr")
            if options_gamma is None:
                options_gamma = opp_dict.get("options_gamma") or phase3_ctx.get("options_gamma")
            opp_score = opp_dict.get("score") if opp_dict else getattr(opp, "score", None)
            opp_rr = opp_dict.get("risk_reward") if opp_dict else getattr(opp, "risk_reward", None)
            decision_id = self.decision_logger.log_decision(
                run_id=run_id,
                ticker=ticker,
                council_decision={
                    "decision": decision,
                    "reason": reason,
                    "strategy": strategy,
                    "direction": direction,
                    "shares": int(shares or 0),
                    "avg_score": opp_score if opp is not None else None,
                    "approve_count": 1 if decision in {"ENTRY_SUBMITTED", "APPROVED_FILL", "EXECUTE"} else 0,
                    "caution_count": 0,
                    "veto_count": 1 if execution_denied else 0,
                    "veto_reasons": [execution_deny_reason or reason] if (execution_denied or reason) else [],
                    "raw_votes": {},
                },
                signal=signal,
                market_session=self._market_session_now(),
                regime=regime_ctx,
                setup_type=getattr(opp, "strategy", strategy) if opp is not None else strategy,
                order_submitted=order_submitted,
                order_id=order_id,
                position_opened=position_opened,
                execution_denied=execution_denied,
                execution_deny_reason=execution_deny_reason,
                notes=notes,
                revision_score=revision_score,
                insider_cluster_score=insider_cluster_score,
                squeeze_risk_score=squeeze_risk_score,
                options_pcr=options_pcr,
                options_gamma=options_gamma,
                correlation_blocked=correlation_blocked,
                is_pilot=is_pilot,
                pilot_policy=pilot_policy,
                pilot_threshold=pilot_threshold,
                pilot_decision_reason=pilot_decision_reason,
            )
            if str(strategy or "").upper() == "SHORT":
                self._log_short_shadow_match(
                    decision_id=decision_id,
                    ticker=ticker,
                    decision=decision,
                    reason=reason,
                    avg_score=opp_score if opp is not None else None,
                    rr=opp_rr if opp is not None else None,
                    candidate_state=phase3_ctx.get("bte_short_candidate_state"),
                    phase3_ctx=phase3_ctx,
                    regime_ctx=regime_ctx,
                    notes={
                        "source": "decision_event",
                        "execution_denied": int(execution_denied or 0),
                    },
                )
        except Exception as exc:
            logger.warning(f"Decision log failed for {ticker}: {exc}")

    def _log_scanner_rejects(self, rejects: list, strategy: str, direction: str):
        """
        Persist scanner-level rejects into decisions table.

        Called after each scanner's scan pass.  Each entry in `rejects` is a dict
        produced by the scanner's _scan_rejects list with at minimum:
            ticker, reason, score, grade, rr
        and optionally: entry, stop, target, primary_pathway, pathways_failed.

        Normalized reason values:
            universe_prefilter_failed  — did not pass technical pre-filter (SHORT only)
            risk_reward_too_low        — R/R below strategy threshold
            no_short_pathway_qualified — pathway engine found no qualifying pathway
            score_below_threshold      — scored but below recommendation threshold
            position_sizing_failed     — scoring passed but position sizer rejected
            data_insufficient          — fewer bars than required
            data_error                 — exception during data fetch
        """
        run_id = self.current_run_id or self.run_id
        if not rejects or not run_id:
            return
        regime_ctx = self._normalize_regime_context(self._current_regime_context)
        for r in rejects:
            try:
                reject_notes = {
                    'type': 'scanner_reject',
                    'score': r.get('score', 0.0),
                    'grade': r.get('grade', 'F'),
                    'primary_pathway': r.get('primary_pathway'),
                    'pathways_failed': r.get('pathways_failed', []),
                    'rr': r.get('rr'),
                    'gates_failed': r.get('gates_failed', []),
                    'geometry_ctx': r.get('geometry_ctx'),
                    'rs_context': r.get('rs_context'),
                    'candidate_state': r.get('candidate_state'),
                    'support_distance_pct': r.get('support_distance_pct'),
                    'breakdown_support': r.get('breakdown_support'),
                }
                decision_id = self.decision_logger.log_decision(
                    run_id=run_id,
                    ticker=r['ticker'],
                    council_decision={
                        'decision': 'SCANNER_REJECT',
                        'reason': r.get('reason', 'unknown'),
                        'strategy': strategy,
                        'direction': direction,
                        'shares': 0,
                        'avg_score': r.get('score', 0.0),
                        'approve_count': 0,
                        'caution_count': 0,
                        'veto_count': 1,
                        'veto_reasons': [r.get('reason', 'unknown')],
                        'raw_votes': {},
                    },
                    signal={
                        'strategy': strategy,
                        'direction': direction,
                        'shares': 0,
                        'entry_price': r.get('entry'),
                        'stop_loss': r.get('stop'),
                        'target_price': r.get('target'),
                        'risk_reward': r.get('rr') or None,  # 0.0 → NULL; pre-filter cases have no real R/R
                    },
                    market_session=self._market_session_now(),
                    regime=regime_ctx,
                    execution_denied=1,
                    execution_deny_reason=r.get('reason', 'unknown'),
                    options_pcr=r.get('options_pcr'),
                    options_gamma=r.get('options_gamma'),
                    notes=json.dumps(reject_notes, default=str),
                )
                if str(strategy or "").upper() == "SHORT":
                    self._log_short_shadow_match(
                        decision_id=decision_id,
                        ticker=r.get('ticker'),
                        decision='SCANNER_REJECT',
                        reason=r.get('reason', 'unknown'),
                        avg_score=r.get('score'),
                        rr=r.get('rr'),
                        candidate_state=r.get('candidate_state'),
                        regime_ctx=regime_ctx,
                        notes={
                            "source": "scanner_reject",
                            "gates_failed": r.get('gates_failed', []),
                            "primary_pathway": r.get('primary_pathway'),
                        },
                    )
            except Exception as exc:
                logger.debug(f"Scanner reject log failed {r.get('ticker', '?')}: {exc}")

    def _collect_all_scan_rejects(self) -> List[Dict]:
        """
        Collect scanner reject rows from all active scanners for the pilot observe block.
        Attaches 'strategy' key so pilot_policy_controller can match policies.
        Returns a combined list — safe even if scanners are None.
        """
        all_rejects: List[Dict] = []
        scanner_map = [
            (getattr(self, 'sniper_scanner', None), 'SNIPER'),
            (getattr(self, 'remora_scanner', None), 'REMORA'),
            (getattr(self, 'short_scanner', None), 'SHORT'),
            (getattr(self, 'contrarian_scanner', None), 'CONTRARIAN'),
        ]
        for scanner, strat in scanner_map:
            if scanner is None:
                continue
            rejects = getattr(scanner, '_scan_rejects', []) or []
            for r in rejects:
                row = dict(r)
                if 'strategy' not in row or not row['strategy']:
                    row['strategy'] = strat
                all_rejects.append(row)
        # Voyager rejects via dedicated extractor
        try:
            voyager_rejects = self._extract_voyager_long_rejects()
            for r in voyager_rejects:
                row = dict(r)
                if 'strategy' not in row or not row['strategy']:
                    row['strategy'] = 'VOYAGER'
                all_rejects.append(row)
        except Exception:
            pass
        return all_rejects

    def _count_open_pilot_positions(self) -> int:
        """
        Count currently-open pilot positions.

        Schema-adaptive behavior:
          1) Prefer trades table if it has is_pilot and lifecycle status/exit fields.
          2) Fall back to decisions table (position_opened markers).
        """
        try:
            db_path = getattr(self, 'db_path', 'trading_performance.db')
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            # Prefer trades table (actual position lifecycle source of truth)
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
            if cur.fetchone():
                cur.execute("PRAGMA table_info(trades)")
                tcols = {row[1] for row in cur.fetchall()}
                if 'is_pilot' in tcols:
                    analytics_filter = "AND COALESCE(analytics_excluded, 0) = 0" if 'analytics_excluded' in tcols else ""
                    if 'status' in tcols:
                        cur.execute(
                            """
                            SELECT COUNT(*) FROM trades
                            WHERE is_pilot = 1
                              AND UPPER(COALESCE(status, 'OPEN')) = 'OPEN'
                              {analytics_filter}
                            """
                            .format(analytics_filter=analytics_filter)
                        )
                    elif 'exit_date' in tcols:
                        cur.execute(
                            """
                            SELECT COUNT(*) FROM trades
                            WHERE is_pilot = 1
                              AND exit_date IS NULL
                              {analytics_filter}
                            """
                            .format(analytics_filter=analytics_filter)
                        )
                    else:
                        cur.execute(
                            """
                            SELECT COUNT(*) FROM trades
                            WHERE is_pilot = 1
                              {analytics_filter}
                            """
                            .format(analytics_filter=analytics_filter)
                        )
                    row = cur.fetchone()
                    conn.close()
                    return int(row[0]) if row else 0

            # Fallback: decisions table
            cur.execute("PRAGMA table_info(decisions)")
            dcols = {row[1] for row in cur.fetchall()}
            if 'is_pilot' not in dcols:
                conn.close()
                return 0

            if 'position_closed' in dcols:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM decisions
                    WHERE is_pilot = 1
                      AND position_opened = 1
                      AND (position_closed IS NULL OR position_closed = 0)
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM decisions
                    WHERE is_pilot = 1
                      AND position_opened = 1
                    """
                )
            row = cur.fetchone()
            conn.close()
            return int(row[0]) if row else 0
        except Exception as exc:
            logger.debug(f"_count_open_pilot_positions error (non-fatal): {exc}")
            return 0

    def _ensure_pilot_schema(self) -> None:
        """Additive idempotent pilot schema migration. Called once at startup."""
        try:
            from pilot_policy_controller import _ensure_pilot_schema as _do_migrate
            db_path = getattr(self, 'db_path', 'trading_performance.db')
            _do_migrate(db_path)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug(f"_ensure_pilot_schema error (non-fatal): {exc}")

    def _ensure_trail_schema(self) -> None:
        """Additive idempotent trailing-stop schema migration. Called once at startup."""
        try:
            if not _TRAIL_STOP_AVAILABLE or _ensure_trail_schema_impl is None:
                return
            db_path = getattr(self, 'db_path', 'trading_performance.db')
            _ensure_trail_schema_impl(db_path)
        except Exception as exc:
            logger.debug(f"_ensure_trail_schema error (non-fatal): {exc}")

    def _normalize_reject_reason(self, reason: object) -> str:
        """
        Normalize freeform reject text into stable analytics keys.

        Examples:
            "No VOYAGER pathway qualified" -> "no_voyager_pathway_qualified"
            "risk_validation_failed"       -> "risk_validation_failed"
        """
        txt = str(reason or "unknown").strip().lower()
        txt = txt.replace("/", "_")
        txt = re.sub(r"[^a-z0-9_]+", "_", txt)
        txt = re.sub(r"_+", "_", txt).strip("_")
        return txt or "unknown"

    def _extract_voyager_long_rejects(self) -> List[Dict]:
        """
        Build scanner-reject rows from Voyager long pipeline audit state.

        Voyager tracks all long candidates (Discovery/Scoring/Risk Gate) in
        self.voyager._last_long_candidates. We convert rejected entries into
        the same schema used by _log_scanner_rejects so analytics are unified.
        """
        engine = self.voyager
        if engine is None:
            return []

        rows = getattr(engine, "_last_long_candidates", None) or []
        rejects: List[Dict] = []
        for c in rows:
            if not isinstance(c, dict):
                continue
            if str(c.get("status", "")).strip().lower() != "rejected":
                continue
            ticker = str(c.get("ticker") or "").upper().strip()
            if not ticker:
                continue

            metrics = c.get("metrics") if isinstance(c.get("metrics"), dict) else {}
            reason_raw = c.get("rejection_reason") or c.get("reason") or "unknown"
            score_val = c.get("score")
            try:
                score_val = float(score_val)
            except Exception:
                score_val = 0.0

            # Prefer explicit candidate RR, then metrics-derived fallback.
            rr_val = c.get("risk_reward")
            if rr_val is None and isinstance(metrics, dict):
                rr_val = metrics.get("risk_reward")
            stop_val = c.get("stop_loss")
            if stop_val is None and isinstance(metrics, dict):
                stop_val = metrics.get("stop_loss")
            target_val = c.get("target_price")
            if target_val is None and isinstance(metrics, dict):
                target_val = metrics.get("target_price")

            # Infer gates from reason string
            _reason_key = self._normalize_reject_reason(reason_raw).lower()
            _v_gates = []
            if "risk_reward" in _reason_key:
                _v_gates.append("rr")
            elif "prefilter" in _reason_key or "universe_prefilter" in _reason_key:
                _v_gates.append("prefilter")
            elif "data_insuff" in _reason_key or "data_error" in _reason_key or "no_data" in _reason_key:
                _v_gates.append("data")
            elif "pathway" in _reason_key:
                _v_gates.append("pathway")
            elif _reason_key in ("rs_declining", "rs_declining_weak", "no_accumulation", "below_50ma", "score_below_threshold", "no_voyager_pathway_qualified"):
                _v_gates.append("score")
            else:
                _v_gates.append("other")

            rejects.append({
                "ticker": ticker,
                "reason": self._normalize_reject_reason(reason_raw),
                "score": score_val,
                "grade": c.get("grade", "N/A"),
                "rr": rr_val,
                "entry": metrics.get("entry_price") if isinstance(metrics, dict) else None,
                "stop": stop_val,
                "target": target_val,
                "primary_pathway": (c.get("score_result") or {}).get("pathway_qualification", {}).get("primary_pathway")
                if isinstance(c.get("score_result"), dict) else None,
                "pathways_failed": (c.get("score_result") or {}).get("pathway_qualification", {}).get("pathways_failed", [])
                if isinstance(c.get("score_result"), dict) else [],
                "gates_failed": _v_gates,
            })

        return rejects

    def _get_macro_gate_for_strategy(self, strategy: str) -> tuple:
        """
        Return the macro event window gate for a specific strategy.

        Uses get_macro_window_state() from MacroCalendar with a 60s TTL cache to
        avoid repeated datetime parsing on every per-ticker execution call.

        Returns: (gate_action: 'CLEAR'|'CAUTION'|'DENY', reason: str)

        Gate meanings:
            CLEAR   — no active macro window, proceed normally
            CAUTION — in a macro window but entry is still allowed; log a warning
            DENY    — in EVENT_LOCKOUT window and strategy is execution-sensitive;
                      block this entry (logged as macro_event_deny)
        """
        now_ts = time.time()
        if (now_ts - self._macro_window_ts) > 60 or self._macro_window_cache is None:
            if self._macro_cal is not None:
                try:
                    self._macro_window_cache = self._macro_cal.get_macro_window_state()
                except Exception:
                    self._macro_window_cache = {'window': 'CLEAR', 'strategy_gates': {}, 'event_name': ''}
            else:
                self._macro_window_cache = {'window': 'CLEAR', 'strategy_gates': {}, 'event_name': ''}
            self._macro_window_ts = now_ts

        state = self._macro_window_cache
        gate = state.get('strategy_gates', {}).get(strategy.upper(), 'CLEAR')
        if gate == 'CLEAR':
            return 'CLEAR', ''

        event_name = state.get('event_name', 'macro event') or 'macro event'
        window = state.get('window', 'UNKNOWN')
        reason = f"macro_{window.lower()}:{event_name}"
        return gate, reason

    def _has_open_tracked_trade(self, ticker: str) -> bool:
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM trades WHERE ticker = ? AND exit_date IS NULL ORDER BY id DESC LIMIT 1",
                (str(ticker or "").upper(),),
            )
            return cur.fetchone() is not None
        except Exception:
            return False
        finally:
            if conn:
                conn.close()

    def _load_open_trade_meta_map(self) -> Dict[str, Dict]:
        """Load the latest open trade metadata keyed by ticker."""
        meta_map: Dict[str, Dict] = {}
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(trades)")
            cols = {row[1] for row in cur.fetchall()}
            if not cols:
                return {}

            select_cols = ["ticker"]
            for name in ("strategy", "direction", "entry_price", "stop_loss", "initial_stop_loss", "quantity"):
                if name in cols:
                    select_cols.append(name)
            order_col = "entry_time" if "entry_time" in cols else ("entry_date" if "entry_date" in cols else "id")
            closed_predicate = "COALESCE(exit_time, exit_date) IS NULL"
            if "status" in cols:
                closed_predicate = "(UPPER(COALESCE(status, 'OPEN')) = 'OPEN' OR COALESCE(exit_time, exit_date) IS NULL)"
            cur.execute(
                f"""
                SELECT {', '.join(select_cols)}
                FROM trades
                WHERE {closed_predicate}
                ORDER BY {order_col} DESC, id DESC
                """
            )
            for row in cur.fetchall():
                ticker = str(row["ticker"] or "").upper()
                if not ticker or ticker in meta_map:
                    continue
                stop_loss = row["stop_loss"] if "stop_loss" in row.keys() else None
                initial_stop = row["initial_stop_loss"] if "initial_stop_loss" in row.keys() else None
                meta_map[ticker] = {
                    "strategy": str(row["strategy"] or "UNKNOWN").upper() if "strategy" in row.keys() else "UNKNOWN",
                    "direction": str(row["direction"] or "LONG").upper() if "direction" in row.keys() else "LONG",
                    "entry_price": float(row["entry_price"] or 0.0) if "entry_price" in row.keys() else 0.0,
                    "stop_loss": float(stop_loss) if stop_loss not in (None, "") else (
                        float(initial_stop) if initial_stop not in (None, "") else None
                    ),
                    "quantity": int(abs(float(row["quantity"] or 0))) if "quantity" in row.keys() else 0,
                }
        except Exception:
            return {}
        finally:
            if conn:
                conn.close()
        return meta_map

    def _has_existing_ticker_exposure(self, ticker: str) -> bool:
        """
        Return True when the system already has open exposure in this ticker.

        V3 currently models positions at the ticker level, not by tax lot.
        Failing closed here is safer than allowing same-ticker adds that the
        canonical trade log and active position map cannot represent cleanly.
        """
        tk = str(ticker or "").upper().strip()
        if not tk:
            return False

        try:
            if tk in {str(k or "").upper() for k in (self.active_positions or {}).keys()}:
                return True
        except Exception:
            pass

        if self._has_open_tracked_trade(tk):
            return True

        if self.trading_client:
            try:
                for pos in self.trading_client.get_all_positions():
                    if str(getattr(pos, "symbol", "") or "").upper() != tk:
                        continue
                    qty = self._as_float(getattr(pos, "qty", 0), 0.0) or 0.0
                    market_value = self._as_float(getattr(pos, "market_value", 0), 0.0) or 0.0
                    if abs(qty) > 0 or abs(market_value) > 0:
                        return True
            except Exception:
                pass
        return False

    def _record_portfolio_exit_result(self, trade_id: Optional[int]):
        """Feed realized closed-trade results back into the portfolio coordinator."""
        if not trade_id:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(trades)")
            cols = {row[1] for row in cur.fetchall()}
            pnl_expr = "COALESCE(pnl_usd, pnl, 0.0)" if "pnl_usd" in cols else ("COALESCE(pnl, 0.0)" if "pnl" in cols else "0.0")
            pct_expr = "COALESCE(pnl_pct, pnl_percent, 0.0)" if "pnl_pct" in cols else ("COALESCE(pnl_percent, 0.0)" if "pnl_percent" in cols else "0.0")
            strategy_expr = "COALESCE(strategy, setup_type, 'UNKNOWN')" if "strategy" in cols else ("COALESCE(setup_type, 'UNKNOWN')" if "setup_type" in cols else "'UNKNOWN'")
            status_expr = "UPPER(COALESCE(status, ''))" if "status" in cols else "'CLOSED'"
            cur.execute(
                f"""
                SELECT ticker,
                       {strategy_expr} AS strategy_name,
                       {pnl_expr} AS pnl_value,
                       {pct_expr} AS pnl_pct_value,
                       {status_expr} AS trade_status
                FROM trades
                WHERE id = ?
                """,
                (trade_id,),
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                return
            if str(row["trade_status"] or "").upper() != "CLOSED":
                return
            self.portfolio.record_trade_result(
                strategy=str(row["strategy_name"] or "UNKNOWN").upper(),
                ticker=str(row["ticker"] or "").upper(),
                pnl=float(row["pnl_value"] or 0.0),
                return_pct=float(row["pnl_pct_value"] or 0.0),
            )
        except Exception as exc:
            logger.debug(f"Portfolio exit result sync failed for trade_id={trade_id}: {exc}")

    @staticmethod
    def _as_float(value, default=None):
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _classify_vix_regime(vix_level: float) -> str:
        """Normalize VIX level into stable categorical regimes for telemetry."""
        try:
            vix = float(vix_level or 0.0)
        except Exception:
            return "UNKNOWN"
        if vix <= 0:
            return "UNKNOWN"
        if vix < 15:
            return "CALM"
        if vix < 20:
            return "NORMAL"
        if vix < 25:
            return "ELEVATED"
        if vix < 30:
            return "HIGH"
        return "EXTREME"

    def _normalize_regime_context(self, regime_context: Optional[Dict] = None) -> Dict:
        """
        Ensure runtime regime context always carries canonical keys used by
        decisions/runs diagnostics.
        """
        ctx = dict(regime_context or self._current_regime_context or {})
        status = str(
            ctx.get("status")
            or ctx.get("regime_status")
            or ctx.get("regime_overall")
            or "UNKNOWN"
        ).upper()
        volatility = str(
            ctx.get("volatility")
            or ctx.get("regime_volatility")
            or ctx.get("regime_vix")
            or "UNKNOWN"
        ).upper()
        try:
            vix_level = float(ctx.get("vix_level") or 0.0)
        except Exception:
            vix_level = 0.0

        ctx["status"] = status
        ctx["volatility"] = volatility
        ctx["regime_overall"] = str(ctx.get("regime_overall") or status).upper()
        ctx["regime_spy"] = str(ctx.get("regime_spy") or status).upper()
        ctx["regime_vix"] = str(ctx.get("regime_vix") or volatility).upper()
        ctx["vix_level"] = vix_level
        return ctx

    @staticmethod
    def _is_contrarian_regime_eligible(regime_overall: str, regime_vix: str) -> bool:
        status = str(regime_overall or "UNKNOWN").upper()
        vol = str(regime_vix or "UNKNOWN").upper()
        return vol in ("ELEVATED", "HIGH", "EXTREME") or status in ("BEAR", "CORRECTION")

    @staticmethod
    def _calc_execution_rr(direction: str, entry_price: float, stop_loss: float, target_price: float) -> float:
        """Compute planned execution R/R from concrete entry/stop/target levels."""
        try:
            entry = float(entry_price)
            stop = float(stop_loss)
            target = float(target_price)
            risk = abs(entry - stop)
            if risk <= 0:
                return 0.0
            if str(direction or "LONG").upper() == "SHORT":
                reward = entry - target
            else:
                reward = target - entry
            if reward <= 0:
                return 0.0
            return float(round(reward / risk, 4))
        except Exception:
            return 0.0

    def _get_ticker_beta(self, ticker: str) -> float:
        """Best-effort beta lookup with in-memory cache and safe fallback."""
        key = str(ticker or "").upper()
        if not key:
            return 1.0
        if key in self._beta_cache:
            return self._beta_cache[key]

        beta = 1.0
        try:
            tk = yf.Ticker(key)
            info = getattr(tk, "info", {}) or {}
            raw = info.get("beta")
            parsed = self._as_float(raw, None)
            if parsed is not None and parsed > 0:
                beta = float(parsed)
        except Exception:
            pass

        self._beta_cache[key] = float(beta)
        return float(beta)

    def _build_open_positions_for_correlation(self) -> List[Dict]:
        """Return open positions with sector/beta metadata for correlation checks."""
        positions: List[Dict] = []
        seen: set = set()

        if self.trading_client:
            try:
                for pos in self.trading_client.get_all_positions():
                    ticker = str(getattr(pos, "symbol", "") or "").upper()
                    if not ticker:
                        continue
                    seen.add(ticker)
                    sector = "UNKNOWN"
                    try:
                        sector = str(self.risk_overlay.get_ticker_sector(ticker) or "UNKNOWN")
                    except Exception:
                        pass
                    positions.append({
                        "ticker": ticker,
                        "sector": sector,
                        "beta": self._get_ticker_beta(ticker),
                    })
            except Exception:
                pass

        for ticker, row in (self.active_positions or {}).items():
            tk = str(ticker or "").upper()
            if not tk or tk in seen:
                continue
            sector = str((row or {}).get("sector") or "UNKNOWN")
            beta = self._as_float((row or {}).get("beta"), None)
            positions.append({
                "ticker": tk,
                "sector": sector,
                "beta": float(beta if beta is not None else self._get_ticker_beta(tk)),
            })

        return positions

    def _check_portfolio_correlation_guard(self, ticker: str, direction: str) -> tuple:
        """
        Optional pre-trade correlation guard (sector concentration + beta overload).

        Returns (allowed: bool, reason: str).
        """
        if not _CORRELATION_GUARD_AVAILABLE or self.correlation_guard is None:
            return True, "OK"

        # Apply to long entries only; short baskets are managed by dedicated short gates.
        if str(direction or "").upper() != "LONG":
            return True, "OK"

        new_ticker = str(ticker or "").upper()
        if not new_ticker:
            return True, "OK"
        try:
            new_sector = str(self.risk_overlay.get_ticker_sector(new_ticker) or "UNKNOWN")
        except Exception:
            new_sector = "UNKNOWN"
        new_beta = self._get_ticker_beta(new_ticker)
        open_positions = self._build_open_positions_for_correlation()
        return self.correlation_guard.check(
            new_ticker=new_ticker,
            new_sector=new_sector,
            new_beta=new_beta,
            open_positions=open_positions,
        )

    def _derive_primary_pathway(self, opp) -> Optional[str]:
        pq = getattr(opp, "pathway_qualification", None) or {}
        if isinstance(pq, dict):
            pp = pq.get("primary_pathway")
            if pp:
                return str(pp).upper()
        direct = getattr(opp, "primary_pathway", None)
        if direct:
            return str(direct).upper()
        return None

    def _get_phase3_context(self, opp) -> Dict:
        """
        Build a single entry context used by both decision logs and trade entries.
        Result is cached per-ticker for expensive external signals.
        """
        if opp is None:
            return {}

        existing = getattr(opp, "_phase3_context", None)
        if isinstance(existing, dict) and existing:
            return existing

        ticker = str(getattr(opp, "ticker", "") or "").upper()
        strategy = str(getattr(opp, "strategy", "UNKNOWN") or "UNKNOWN").upper()
        pathway = self._derive_primary_pathway(opp) or ""
        score = self._as_float(getattr(opp, "score", None), default=50.0) or 50.0

        ticker_ctx = self._phase3_ticker_cache.get(ticker, {})
        if not ticker_ctx:
            revision_score = None
            insider_score = None
            squeeze_score = None
            volume_quality = "NONE"

            if self.revision_scorer is not None and ticker:
                try:
                    revision_score = self._as_float(
                        self.revision_scorer.score(ticker).get("revision_score"), default=50.0
                    )
                except Exception:
                    revision_score = 50.0

            if self.insider_scorer is not None and ticker:
                try:
                    insider_score = self._as_float(
                        self.insider_scorer.score(ticker, save_to_db=True).get("insider_cluster_score"),
                        default=0.0,
                    )
                except Exception:
                    insider_score = 0.0

            if self.short_risk_scorer is not None and ticker:
                try:
                    squeeze_score = self._as_float(
                        self.short_risk_scorer.score(ticker, save_to_db=True).get("squeeze_risk_score"),
                        default=50.0,
                    )
                except Exception:
                    squeeze_score = 50.0

            if self.volume_profile_analyzer is not None and ticker:
                try:
                    vp = self.volume_profile_analyzer.analyze(ticker, days_back=60)
                    volume_quality = str(vp.get("quality") or "NONE").upper()
                except Exception:
                    volume_quality = "NONE"

            ticker_ctx = {
                "revision_score": revision_score,
                "insider_cluster_score": insider_score,
                "squeeze_risk_score": squeeze_score,
                "volume_profile_quality": volume_quality,
            }
            if ticker:
                self._phase3_ticker_cache[ticker] = ticker_ctx

        if strategy == "SNIPER" and "bte_advisory" not in ticker_ctx:
            bte_ctx = {}
            if self.breakout_timing_tracker is not None and ticker:
                try:
                    bte_ctx = self.breakout_timing_tracker.get_live_advisory(
                        ticker=ticker,
                        strategy_source=strategy,
                        regime_snapshot=self._current_regime_context,
                    ) or {}
                except Exception:
                    bte_ctx = {}
            ticker_ctx = dict(ticker_ctx)
            ticker_ctx["bte_advisory"] = bte_ctx
            if ticker:
                self._phase3_ticker_cache[ticker] = ticker_ctx

        if strategy == "SHORT" and "bte_short_advisory" not in ticker_ctx:
            bte_short_ctx = {}
            if self.breakout_timing_tracker is not None and ticker:
                try:
                    bte_short_ctx = self.breakout_timing_tracker.get_short_live_advisory(
                        ticker=ticker,
                        strategy_source=strategy,
                        regime_snapshot=self._current_regime_context,
                    ) or {}
                except Exception:
                    bte_short_ctx = {}
            ticker_ctx = dict(ticker_ctx)
            ticker_ctx["bte_short_advisory"] = bte_short_ctx
            if ticker:
                self._phase3_ticker_cache[ticker] = ticker_ctx

        forecast_ctx = {}
        if self.forecast_engine is not None:
            try:
                forecast_kwargs = {
                    "strategy": strategy,
                    "pathway": pathway,
                    "composite_score": score,
                    "min_samples": 10,
                }
                if getattr(self.forecast_engine, "supports_shadow_context", False):
                    regime_ctx = self._normalize_regime_context(self._current_regime_context)
                    scanner_rr = self._as_float(getattr(opp, "risk_reward", None), None)
                    if scanner_rr is None or scanner_rr <= 0:
                        scanner_rr = self._calc_execution_rr(
                            direction=getattr(opp, "direction", "LONG"),
                            entry_price=getattr(opp, "entry_price", 0),
                            stop_loss=getattr(opp, "stop_loss", 0),
                            target_price=getattr(opp, "target_price", 0),
                        )
                    forecast_kwargs.update({
                        "rr": scanner_rr,
                        "regime_status": regime_ctx.get("status"),
                        "regime_volatility": regime_ctx.get("volatility"),
                        "vix_level": regime_ctx.get("vix_level"),
                    })
                forecast_ctx = self.forecast_engine.entry_snapshot(
                    **forecast_kwargs,
                ) or {}
            except Exception:
                forecast_ctx = {}

        context = {
            "revision_score": ticker_ctx.get("revision_score"),
            "insider_cluster_score": ticker_ctx.get("insider_cluster_score"),
            "squeeze_risk_score": ticker_ctx.get("squeeze_risk_score"),
            "volume_profile_quality": ticker_ctx.get("volume_profile_quality", "NONE"),
            "forecast_p_win": forecast_ctx.get("forecast_p_win"),
            "forecast_expected_return": forecast_ctx.get("forecast_expected_return"),
            "forecast_expected_rr": forecast_ctx.get("forecast_expected_rr"),
            "forecast_horizon_days": forecast_ctx.get("forecast_horizon_days"),
            "forecast_conf_low": forecast_ctx.get("forecast_conf_low"),
            "forecast_conf_high": forecast_ctx.get("forecast_conf_high"),
            "forecast_model_version": forecast_ctx.get("forecast_model_version"),
            "thesis_status": "INTACT",
            "thesis_strength": forecast_ctx.get("forecast_p_win"),
            "composite_score": score,
            "pathway": pathway or None,
        }
        if strategy == "SNIPER":
            context.update(ticker_ctx.get("bte_advisory") or {})
        elif strategy == "SHORT":
            context.update(ticker_ctx.get("bte_short_advisory") or {})

        try:
            setattr(opp, "_phase3_context", context)
        except Exception:
            pass
        return context

    def _record_entry_performance(
        self,
        opp,
        shares: int,
        is_pilot: Optional[int] = None,
        pilot_policy: Optional[str] = None,
        pilot_threshold: Optional[float] = None,
    ):
        if shares <= 0 or self._has_open_tracked_trade(opp.ticker):
            return
        try:
            pq = (getattr(opp, 'pathway_qualification', None) or {})
            primary_pathway = pq.get('primary_pathway') if isinstance(pq, dict) else None
            strategy  = getattr(opp, 'strategy', 'UNKNOWN')
            direction = getattr(opp, 'direction', 'LONG')
            score     = float(getattr(opp, 'score', 0) or 0)
            grade     = getattr(opp, 'grade', None)
            scanner_rr = self._as_float(getattr(opp, 'risk_reward', None), None)
            if scanner_rr is None or scanner_rr <= 0:
                scanner_rr = self._calc_execution_rr(
                    direction=direction,
                    entry_price=getattr(opp, 'entry_price', 0),
                    stop_loss=getattr(opp, 'stop_loss', 0),
                    target_price=getattr(opp, 'target_price', 0),
                )
            scanner_rr = float(round(float(scanner_rr or 0.0), 4))
            phase3_ctx = self._get_phase3_context(opp)

            trade_id = self.performance_tracker.log_entry(
                ticker=opp.ticker,
                entry_price=opp.entry_price,
                quantity=int(shares),
                stop_loss=opp.stop_loss,
                target_price=opp.target_price,
                initial_stop_loss=opp.stop_loss,
                confidence=grade,
                confluence_score=score,
                predicted_rr=scanner_rr,
                scanner_rr=scanner_rr,
                setup_type=strategy,
                notes=f"strategy={strategy}|pathway={primary_pathway}",
                strategy=strategy,
                direction=direction,
                score=score,
                grade=grade,
                primary_pathway=primary_pathway,
                forecast_p_win=phase3_ctx.get("forecast_p_win"),
                forecast_expected_return=phase3_ctx.get("forecast_expected_return"),
                forecast_expected_rr=phase3_ctx.get("forecast_expected_rr"),
                forecast_horizon_days=phase3_ctx.get("forecast_horizon_days"),
                forecast_conf_low=phase3_ctx.get("forecast_conf_low"),
                forecast_conf_high=phase3_ctx.get("forecast_conf_high"),
                forecast_model_version=phase3_ctx.get("forecast_model_version"),
                revision_score=phase3_ctx.get("revision_score"),
                insider_cluster_score=phase3_ctx.get("insider_cluster_score"),
                squeeze_risk_score=phase3_ctx.get("squeeze_risk_score"),
                volume_profile_quality=phase3_ctx.get("volume_profile_quality"),
                thesis_status=phase3_ctx.get("thesis_status"),
                thesis_strength=phase3_ctx.get("thesis_strength"),
                composite_score=phase3_ctx.get("composite_score", score),
                pathway=phase3_ctx.get("pathway") or primary_pathway,
                is_pilot=is_pilot,
                pilot_policy=pilot_policy,
                pilot_threshold=pilot_threshold,
            )
            # Store trade_id so reconciliation can match DB rows to active positions
            if trade_id and opp.ticker in self.active_positions:
                self.active_positions[opp.ticker]['trade_id'] = trade_id
        except Exception as exc:
            logger.warning(f"Performance entry log failed for {opp.ticker}: {exc}")

    def _pilot_candidate_to_opportunity(
        self,
        candidate: Dict,
        max_risk_pct: float,
    ) -> Optional[_SniperOpp]:
        """
        Convert pilot candidate row into execution-compatible opportunity.
        Returns None for incomplete/invalid rows.
        """
        ticker = str(candidate.get("ticker") or "").upper().strip()
        if not ticker:
            return None

        direction = str(candidate.get("direction") or "LONG").upper().strip()
        strategy = str(candidate.get("strategy") or "UNKNOWN").upper().strip()

        entry = self._as_float(candidate.get("entry_price"), None)
        stop = self._as_float(candidate.get("stop_loss"), None)
        target = self._as_float(candidate.get("target_price"), None)
        if entry is None or stop is None or target is None:
            return None

        if direction == "LONG" and stop >= entry:
            return None
        if direction == "SHORT" and stop <= entry:
            return None

        risk_per_share = abs(entry - stop)
        if risk_per_share <= 0:
            return None

        try:
            risk_budget = float(self.account_size) * (float(max_risk_pct or 0.0) / 100.0)
        except Exception:
            risk_budget = 0.0
        if risk_budget <= 0:
            # Safe fallback: 0.25% risk budget if config parse fails.
            risk_budget = float(self.account_size or 0.0) * 0.0025

        shares = max(1, int(risk_budget / risk_per_share)) if risk_budget > 0 else 1

        rr_raw = self._as_float(candidate.get("rr"), None)
        if rr_raw is None or rr_raw <= 0:
            if direction == "SHORT":
                reward = max(entry - target, 0.0)
            else:
                reward = max(target - entry, 0.0)
            rr_raw = (reward / risk_per_share) if risk_per_share > 0 else 0.0

        if direction == "SHORT":
            expected_return = ((entry - target) / entry) * 100 if entry > 0 else 0.0
        else:
            expected_return = ((target - entry) / entry) * 100 if entry > 0 else 0.0

        score = int(round(self._as_float(candidate.get("score"), 0.0) or 0.0))
        grade = str(candidate.get("grade") or "N/A")
        primary_pathway = candidate.get("primary_pathway")

        return _SniperOpp(
            ticker=ticker,
            direction=direction,
            entry_price=float(entry),
            target_price=float(target),
            stop_loss=float(stop),
            score=score,
            risk_reward=float(rr_raw or 0.0),
            shares=int(shares),
            expected_return=float(expected_return),
            strategy=strategy,
            grade=grade,
            pathway_qualification={"primary_pathway": primary_pathway} if primary_pathway else {},
        )

    def _execute_pilot_candidates(self, pilot_candidates: List[Dict]) -> None:
        """
        Phase 4.4 controlled pilot execution (paper only, env-gated).
        """
        if not pilot_candidates:
            return

        try:
            from pilot_policy_controller import PilotPolicyController as _PilotCtrl
        except ImportError:
            return
        except Exception as exc:
            logger.debug(f"[PILOT] import failed (non-fatal): {exc}")
            return

        ctrl = _PilotCtrl(execution_mode=self.execution_mode, db_path=self.db_path)
        if not ctrl.is_pilot_enabled():
            return

        cfg = ctrl.get_pilot_config()
        if not bool(cfg.get("execute_paper", False)):
            logger.info("[PILOT] observe-only mode (PILOT_EXECUTE_PAPER=0)")
            return

        if str(self.execution_mode or "").upper() == "LIVE":
            logger.warning("[PILOT] LIVE mode hard-block: skipping pilot execution")
            return

        max_risk_pct = float(cfg.get("max_risk_pct", 0.25) or 0.25)
        run_id = self.current_run_id or self.run_id
        executed = 0
        blocked = 0
        current_vix = self._get_vix()

        logger.warning(f"[PILOT] execute mode ON — evaluating {len(pilot_candidates)} eligible candidate(s)")
        for cand in pilot_candidates:
            ticker = str(cand.get("ticker") or "").upper().strip()
            strategy = str(cand.get("strategy") or "").upper().strip()
            rr = self._as_float(cand.get("rr"), 0.0) or 0.0
            threshold = self._as_float(cand.get("shadow_threshold"), 0.0) or 0.0
            policy_name = str(cand.get("policy_name") or "").strip()
            spy_below_50ma = bool((self._current_regime_context or {}).get("spy_below_50ma", False))

            if not ticker:
                blocked += 1
                continue

            # Respect live strategy VIX gates for pilot entries.
            vix_reason = None
            if strategy == "SNIPER" and self._is_sniper_regime_blocked(current_vix, spy_below_50ma):
                vix_reason = "VIX_GATE_BLOCKED_SNIPER"
            elif strategy == "REMORA" and current_vix > 30.0:
                vix_reason = "VIX_GATE_BLOCKED_REMORA"
            elif strategy in ("CONTRARIAN", "REAPER") and current_vix < 28.0:
                vix_reason = "VIX_GATE_BLOCKED_CONTRARIAN"

            if vix_reason:
                blocked += 1
                ctrl.log_pilot_event(
                    run_id=run_id,
                    ticker=ticker,
                    policy_name=policy_name,
                    event_type="BLOCKED",
                    reason_code=vix_reason,
                    rr=rr,
                    threshold=threshold,
                    notes=f"strategy={strategy}|vix={current_vix:.2f}",
                )
                continue

            if self._has_open_tracked_trade(ticker):
                blocked += 1
                ctrl.log_pilot_event(
                    run_id=run_id,
                    ticker=ticker,
                    policy_name=policy_name,
                    event_type="BLOCKED",
                    reason_code="OPEN_POSITION_EXISTS",
                    rr=rr,
                    threshold=threshold,
                    notes="pilot_skip_open_position",
                )
                continue

            opp = self._pilot_candidate_to_opportunity(cand, max_risk_pct=max_risk_pct)
            if opp is None:
                blocked += 1
                ctrl.log_pilot_event(
                    run_id=run_id,
                    ticker=ticker,
                    policy_name=policy_name,
                    event_type="BLOCKED",
                    reason_code="INVALID_CANDIDATE",
                    rr=rr,
                    threshold=threshold,
                    notes="pilot_candidate_missing_entry_stop_target",
                )
                continue

            ok, reason = self._execute_single_trade(
                opp,
                opp.direction,
                set(),
                {},
                pilot_ctx=cand,
            )
            if ok:
                executed += 1
                ctrl.log_pilot_event(
                    run_id=run_id,
                    ticker=ticker,
                    policy_name=policy_name,
                    event_type="EXECUTED",
                    reason_code="PAPER_EXECUTED",
                    rr=float(getattr(opp, "risk_reward", rr) or rr),
                    threshold=threshold,
                    notes=f"shares={getattr(opp, 'shares', 0)}",
                )
            else:
                blocked += 1
                ctrl.log_pilot_event(
                    run_id=run_id,
                    ticker=ticker,
                    policy_name=policy_name,
                    event_type="BLOCKED",
                    reason_code=str(reason or "EXECUTION_DENIED"),
                    rr=float(getattr(opp, "risk_reward", rr) or rr),
                    threshold=threshold,
                    notes="pilot_execute_denied",
                )

        logger.info(f"[PILOT] execute summary: executed={executed}, blocked={blocked}")

    def _run_circuit_breaker_check(self) -> None:
        """
        Force-close positions that breach max loss threshold (gap/halts protection).
        """
        if not self.trading_client:
            return
        max_loss_pct = self._as_float(self.max_loss_circuit_breaker_pct, 0.25)
        if max_loss_pct is None or max_loss_pct <= 0:
            return

        try:
            positions = self.trading_client.get_all_positions()
        except Exception:
            return

        for pos in positions:
            ticker = str(getattr(pos, "symbol", "") or "").upper()
            if not ticker or ticker in self._forced_exit_reasons:
                continue
            plpc = self._as_float(getattr(pos, "unrealized_plpc", None), None)
            if plpc is None:
                continue
            if plpc > -max_loss_pct:
                continue

            loss_pct = abs(float(plpc))
            logger.warning(
                f"[CIRCUIT_BREAKER] {ticker} loss {loss_pct:.1%} exceeds "
                f"{max_loss_pct:.1%}. Forcing market exit."
            )
            try:
                self.trading_client.close_position(ticker)
                self._forced_exit_reasons[ticker] = "CIRCUIT_BREAKER"
                active = self.active_positions.get(ticker, {})
                self._log_decision_event(
                    ticker=ticker,
                    strategy=str(active.get("strategy", "UNKNOWN")),
                    direction=str(active.get("direction", "LONG")),
                    decision="EXIT_SUBMITTED",
                    reason="CIRCUIT_BREAKER",
                    shares=int(abs(float(getattr(pos, "qty", 0) or 0))),
                    notes=f"circuit_breaker_loss_pct={loss_pct:.4f}",
                )
            except Exception as exc:
                logger.warning(f"[CIRCUIT_BREAKER] close failed for {ticker}: {exc}")

    def _run_trailing_stop_check(self) -> None:
        """
        Raise local stops for winning trades using R-multiple trailing rules.

        This is broker-independent protection:
          - Tightens `trades.stop_loss` when thresholds are hit.
          - Logs trail events for auditability.
          - Force-exits via market close when price breaches tightened local stop.
        """
        if not self.trading_client or not self.trailing_stop_manager:
            return

        try:
            positions = self.trading_client.get_all_positions()
        except Exception:
            return
        if not positions:
            return

        pos_map = {
            str(getattr(p, "symbol", "") or "").upper(): p
            for p in positions
            if str(getattr(p, "symbol", "") or "").upper()
        }
        if not pos_map:
            return

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(trades)")
            tcols = {row[1] for row in cur.fetchall()}
            needed = {"id", "ticker", "direction", "entry_price", "stop_loss", "target_price", "exit_date"}
            if not needed.issubset(tcols):
                conn.close()
                return

            select_cols = ["id", "ticker", "direction", "entry_price", "stop_loss", "target_price"]
            for extra_col in ("initial_stop_loss", "trail_stop_activated", "trail_locked_at_r", "status"):
                if extra_col in tcols:
                    select_cols.append(extra_col)
            cur.execute(
                f"SELECT {', '.join(select_cols)} FROM trades WHERE exit_date IS NULL"
            )
            open_rows = cur.fetchall()

            for row in open_rows:
                ticker = str(row["ticker"] or "").upper()
                if not ticker or ticker not in pos_map:
                    continue
                if ticker in self._forced_exit_reasons:
                    continue

                direction = str(row["direction"] or "LONG").upper()
                entry_price = self._as_float(row["entry_price"], None)
                current_stop = self._as_float(row["stop_loss"], None)
                target_price = self._as_float(row["target_price"], None)
                original_stop = self._as_float(
                    row["initial_stop_loss"] if "initial_stop_loss" in row.keys() else row["stop_loss"],
                    None,
                )
                if None in (entry_price, current_stop, target_price, original_stop):
                    continue

                pos = pos_map[ticker]
                current_price = self._as_float(getattr(pos, "current_price", None), None)
                if current_price is None or current_price <= 0:
                    try:
                        q = self.data_feed.get_real_time_quote(ticker)
                        bid = self._as_float((q or {}).get("bid"), None) if isinstance(q, dict) else None
                        ask = self._as_float((q or {}).get("ask"), None) if isinstance(q, dict) else None
                        if bid and ask and bid > 0 and ask > 0:
                            current_price = (bid + ask) / 2.0
                    except Exception:
                        current_price = None
                if current_price is None or current_price <= 0:
                    continue

                new_stop, reason = self.trailing_stop_manager.compute_new_stop(
                    ticker=ticker,
                    direction=direction,
                    entry_price=float(entry_price),
                    current_stop=float(current_stop),
                    current_price=float(current_price),
                    original_stop=float(original_stop),
                    target_price=float(target_price),
                )

                active_stop = float(current_stop)
                if reason and new_stop is not None and abs(float(new_stop) - float(current_stop)) > 1e-6:
                    updates = {"stop_loss": float(new_stop)}
                    if "trail_stop_activated" in tcols:
                        updates["trail_stop_activated"] = 1
                    if "trail_locked_at_r" in tcols and reason == "TRAIL_LOCKED_1R":
                        updates["trail_locked_at_r"] = 1.0
                    if "status" in tcols:
                        updates["status"] = "OPEN"

                    set_clause = ", ".join([f"{k}=?" for k in updates.keys()])
                    vals = [updates[k] for k in updates.keys()] + [row["id"]]
                    cur.execute(f"UPDATE trades SET {set_clause} WHERE id=?", vals)
                    conn.commit()
                    active_stop = float(new_stop)

                    if ticker in self.active_positions:
                        self.active_positions[ticker]["stop_loss"] = float(new_stop)

                    # Compute gain_r for trail event telemetry.
                    if direction == "SHORT":
                        denom = (float(original_stop) - float(entry_price))
                        gain_r = ((float(entry_price) - float(current_price)) / denom) if denom > 0 else 0.0
                    else:
                        denom = (float(entry_price) - float(original_stop))
                        gain_r = ((float(current_price) - float(entry_price)) / denom) if denom > 0 else 0.0

                    if _log_trail_event is not None:
                        _log_trail_event(
                            db_path=self.db_path,
                            ticker=ticker,
                            direction=direction,
                            reason_code=str(reason),
                            entry_price=float(entry_price),
                            old_stop=float(current_stop),
                            new_stop=float(new_stop),
                            current_price=float(current_price),
                            gain_r=float(gain_r),
                            execution_mode=self.execution_mode,
                            run_id=self.current_run_id or self.run_id,
                        )
                    logger.info(
                        f"[TRAIL] {ticker} {direction}: stop {float(current_stop):.2f} -> "
                        f"{float(new_stop):.2f} ({reason})"
                    )

                # Local trailing-stop breach protection.
                breached = False
                if direction == "SHORT":
                    breached = float(current_price) >= float(active_stop)
                else:
                    breached = float(current_price) <= float(active_stop)
                if not breached:
                    continue

                try:
                    logger.warning(
                        f"[TRAIL_STOP] {ticker}: price {float(current_price):.2f} breached "
                        f"stop {float(active_stop):.2f} — forcing market exit"
                    )
                    self.trading_client.close_position(ticker)
                    self._forced_exit_reasons[ticker] = "TRAIL_STOP_HIT"
                    self._log_decision_event(
                        ticker=ticker,
                        strategy=str((self.active_positions.get(ticker) or {}).get("strategy", "UNKNOWN")),
                        direction=str((self.active_positions.get(ticker) or {}).get("direction", direction)),
                        decision="EXIT_SUBMITTED",
                        reason="TRAIL_STOP_HIT",
                        shares=int(abs(float(getattr(pos, "qty", 0) or 0))),
                        notes=f"trail_stop={float(active_stop):.4f}|price={float(current_price):.4f}",
                    )
                except Exception as exc:
                    logger.warning(f"[TRAIL_STOP] close failed for {ticker}: {exc}")
        except Exception as exc:
            logger.debug(f"_run_trailing_stop_check error (non-fatal): {exc}")
        finally:
            if conn:
                conn.close()

    def _resolve_exit_price_and_reason(
        self, ticker: str, entry_price: float, stop_loss, target_price, direction: str
    ):
        """
        Get exit price from Alpaca closed orders; fall back to last bar close.

        Exit reason priority:
          1. Alpaca order.type — 'limit' filled on closing side → TARGET_HIT;
             'stop' / 'stop_limit' → STOP_HIT.  Most reliable — reads actual
             bracket leg type, no tolerance needed.
          2. Price proximity fallback (±2% of entry) — used only when order type
             is ambiguous (market, trailing_stop, etc.) or Alpaca query fails.
        """
        exit_price = None
        exit_reason = None  # populated from order type when possible
        exit_meta = {
            "exit_provenance": "unknown",
            "exit_order_type": None,
            "exit_price_source": None,
        }

        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            req = GetOrdersRequest(
                status=QueryOrderStatus.CLOSED, symbols=[ticker], limit=20
            )
            orders = self.trading_client.get_orders(filter=req)
            closing_side = 'sell' if direction.upper() == 'LONG' else 'buy'

            for order in sorted(
                orders,
                key=lambda o: str(
                    getattr(o, 'filled_at', '') or getattr(o, 'submitted_at', '') or ''
                ),
                reverse=True,
            ):
                side   = str(getattr(order, 'side',   '') or '').lower()
                status = str(getattr(order, 'status', '') or '').lower()
                otype  = str(getattr(order, 'type',   '') or '').lower()
                fill   = getattr(order, 'filled_avg_price', None)

                if side == closing_side and status == 'filled' and fill is not None:
                    exit_price = float(fill)
                    exit_meta["exit_order_type"] = otype or None
                    exit_meta["exit_price_source"] = "broker_filled_avg_price"
                    # Derive reason directly from order type — no tolerance needed
                    if otype == 'limit':
                        exit_reason = 'TARGET_HIT'
                        exit_meta["exit_provenance"] = "broker_limit_order"
                    elif otype in ('stop', 'stop_limit'):
                        exit_reason = 'STOP_HIT'
                        exit_meta["exit_provenance"] = "broker_stop_order"
                    elif otype == 'trailing_stop':
                        exit_reason = 'TRAIL_STOP_HIT'
                        exit_meta["exit_provenance"] = "broker_trailing_stop"
                    elif otype == 'market':
                        exit_meta["exit_provenance"] = "broker_market_order"
                    else:
                        exit_meta["exit_provenance"] = "broker_other_order"
                    # market / trailing_stop / other → leave None, fall through to proximity
                    break
        except Exception:
            pass

        # Fall back to last bar close if Alpaca query yielded nothing
        if exit_price is None:
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=3)
                if bars:
                    exit_price = float(bars[-1]['close'])
                    exit_meta["exit_price_source"] = "daily_bar_close_fallback"
            except Exception:
                pass

        # Last resort — use entry price (prevents silent skip; reason stays MARKET_EXIT)
        if exit_price is None:
            exit_price = float(entry_price)
            exit_meta["exit_price_source"] = "entry_price_last_resort"

        # Price-proximity fallback — only used when order type was ambiguous/unavailable
        if exit_reason is None:
            tol = float(entry_price) * 0.02
            if stop_loss is not None and abs(exit_price - float(stop_loss)) <= tol:
                exit_reason = 'STOP_HIT'
                if exit_meta["exit_provenance"] == "unknown":
                    exit_meta["exit_provenance"] = "price_proximity_stop"
            elif target_price is not None and abs(exit_price - float(target_price)) <= tol:
                exit_reason = 'TARGET_HIT'
                if exit_meta["exit_provenance"] == "unknown":
                    exit_meta["exit_provenance"] = "price_proximity_target"
            else:
                exit_reason = 'MARKET_EXIT'
                if exit_meta["exit_provenance"] == "unknown":
                    exit_meta["exit_provenance"] = "market_exit_unclassified"

        return exit_price, exit_reason, exit_meta

    def _reconcile_closed_positions(self):
        """
        Detect positions closed by Alpaca bracket orders between scan cycles.

        Compares open trades in the DB against current live Alpaca positions.
        Any ticker present in `trades` (exit_date IS NULL) but absent from Alpaca
        is treated as closed — exit metadata is fetched and logged via log_exit.

        Called at the start of each run_scan_cycle so realized analytics stay current.
        """
        if not self.trading_client:
            return
        import time as _time
        self._last_reconcile_ts = _time.time()
        try:
            alpaca_positions = self.trading_client.get_all_positions()
            alpaca_tickers = {p.symbol.upper() for p in alpaca_positions}

            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(trades)")
            tcols = {row[1] for row in cur.fetchall()}
            select_cols = [
                "id", "ticker", "entry_price", "stop_loss", "target_price",
                "quantity", "strategy", "direction", "entry_date",
            ]
            for extra_col in ("is_pilot", "pilot_policy", "pilot_threshold", "predicted_rr"):
                if extra_col in tcols:
                    select_cols.append(extra_col)
            cur.execute(
                f"SELECT {', '.join(select_cols)} FROM trades WHERE exit_date IS NULL"
            )
            open_trades = cur.fetchall()
            conn.close()

            for row in open_trades:
                trade_id = row["id"]
                ticker = row["ticker"]
                entry_price = row["entry_price"]
                stop_loss = row["stop_loss"]
                target_price = row["target_price"]
                quantity = row["quantity"]
                strategy = row["strategy"]
                direction = row["direction"]
                entry_date = row["entry_date"]
                is_pilot_trade = int(row["is_pilot"]) == 1 if "is_pilot" in row.keys() and row["is_pilot"] is not None else False
                pilot_policy = row["pilot_policy"] if "pilot_policy" in row.keys() else None
                try:
                    pilot_threshold = float(row["pilot_threshold"]) if "pilot_threshold" in row.keys() and row["pilot_threshold"] is not None else 0.0
                except Exception:
                    pilot_threshold = 0.0
                try:
                    pilot_rr = float(row["predicted_rr"]) if "predicted_rr" in row.keys() and row["predicted_rr"] is not None else 0.0
                except Exception:
                    pilot_rr = 0.0

                if ticker.upper() in alpaca_tickers:
                    continue  # still open

                direction = (direction or 'LONG').upper()
                try:
                    exit_price, exit_reason, exit_meta = self._resolve_exit_price_and_reason(
                        ticker, entry_price, stop_loss, target_price, direction
                    )
                except Exception as e:
                    logger.warning(f"_reconcile: exit price resolution failed {ticker}: {e}")
                    continue

                forced_reason = self._forced_exit_reasons.get(str(ticker or "").upper())
                if forced_reason:
                    exit_reason = str(forced_reason)
                    exit_meta["forced_exit_reason"] = str(forced_reason)
                    exit_meta["exit_provenance"] = "forced_exit_override"

                try:
                    self.performance_tracker.log_exit(
                        ticker=ticker,
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        entry_id=trade_id,
                        circuit_breaker_triggered=1 if exit_reason == "CIRCUIT_BREAKER" else 0,
                        exit_provenance=exit_meta.get("exit_provenance"),
                        exit_order_type=exit_meta.get("exit_order_type"),
                        exit_price_source=exit_meta.get("exit_price_source"),
                        forced_exit_reason=exit_meta.get("forced_exit_reason"),
                    )
                    self._record_portfolio_exit_result(trade_id)
                    logger.info(
                        f"📊 Exit reconciled: {ticker} [{strategy or direction}] "
                        f"@ ${exit_price:.2f} ({exit_reason})"
                    )
                    if forced_reason:
                        self._forced_exit_reasons.pop(str(ticker or "").upper(), None)
                    if is_pilot_trade:
                        try:
                            from pilot_policy_controller import PilotPolicyController as _PilotCtrl
                            _pilot_ctrl = _PilotCtrl(
                                execution_mode=self.execution_mode,
                                db_path=self.db_path,
                            )
                            _pilot_ctrl.log_pilot_event(
                                run_id=self.current_run_id or self.run_id,
                                ticker=ticker,
                                policy_name=str(pilot_policy or ""),
                                event_type="CLOSED",
                                reason_code=str(exit_reason or "CLOSED"),
                                rr=pilot_rr,
                                threshold=pilot_threshold,
                                notes=f"trade_id={trade_id}|exit_price={exit_price:.4f}",
                            )
                        except Exception as _pilot_exc:
                            logger.debug(f"_reconcile: pilot CLOSED log failed {ticker}: {_pilot_exc}")
                except Exception as e:
                    logger.warning(f"_reconcile: log_exit failed {ticker}: {e}")

        except Exception as e:
            logger.warning(f"_reconcile_closed_positions error: {e}")

    def _sync_positions_to_portfolio_coordinator(self):
        """Push broker-aware positions into the portfolio coordinator."""
        try:
            merged_positions: Dict[str, Dict] = {}
            trade_meta = self._load_open_trade_meta_map()

            if self.trading_client:
                try:
                    for pos in self.trading_client.get_all_positions():
                        ticker = str(getattr(pos, "symbol", "") or "").upper()
                        if not ticker:
                            continue
                        meta = trade_meta.get(ticker, {})
                        active = (self.active_positions or {}).get(ticker, {})
                        direction = (
                            str(meta.get("direction") or active.get("direction") or getattr(pos, "side", "") or "").upper()
                            or ("SHORT" if (self._as_float(getattr(pos, "qty", 0), 0.0) or 0.0) < 0 else "LONG")
                        )
                        entry_price = self._as_float(meta.get("entry_price"), None)
                        if entry_price is None or entry_price <= 0:
                            entry_price = self._as_float(active.get("entry_price"), None)
                        if entry_price is None or entry_price <= 0:
                            entry_price = self._as_float(getattr(pos, "avg_entry_price", 0), 0.0) or 0.0

                        stop_loss = self._as_float(meta.get("stop_loss"), None)
                        if stop_loss is None or stop_loss <= 0:
                            stop_loss = self._as_float(active.get("stop_loss"), None)
                        if stop_loss is None or stop_loss <= 0:
                            if entry_price > 0:
                                stop_loss = entry_price * (1.04 if direction == "SHORT" else 0.96)
                            else:
                                stop_loss = 0.0

                        shares = int(abs(self._as_float(getattr(pos, "qty", 0), 0.0) or 0.0))
                        market_value = abs(self._as_float(getattr(pos, "market_value", 0), 0.0) or 0.0)
                        merged_positions[ticker] = {
                            "ticker": ticker,
                            "strategy": str(meta.get("strategy") or active.get("strategy") or "UNKNOWN").upper(),
                            "direction": direction,
                            "entry_price": float(entry_price),
                            "stop_loss": float(stop_loss),
                            "initial_stop_loss": float(stop_loss),
                            "target_price": self._as_float(active.get("target_price"), 0.0) or 0.0,
                            "shares": shares,
                            "market_value": market_value,
                            "sector": str(active.get("sector") or "UNKNOWN"),
                            "beta": self._as_float(active.get("beta"), None),
                        }
                except Exception as exc:
                    logger.debug(f"Broker position sync failed: {exc}")

            for ticker, row in (self.active_positions or {}).items():
                tk = str(ticker or "").upper()
                if not tk or tk in merged_positions:
                    continue
                merged_positions[tk] = dict(row or {})

            self.portfolio.positions = merged_positions
        except Exception as exc:
            logger.debug(f"Portfolio sync failed: {exc}")

    def _build_regime_snapshot(self, vix_level: float) -> Dict:
        """Small regime snapshot for portfolio allocation mode updates."""
        if vix_level >= 30:
            spy_regime = "BEAR"
            breadth_score = 0.2
        elif vix_level >= 25:
            spy_regime = "SIDEWAYS"
            breadth_score = 0.35
        elif vix_level < 15:
            spy_regime = "BULL"
            # VIX < 15 is genuine complacency, not a routine low-volatility reading.
            breadth_score = 0.7
        else:
            spy_regime = "BULL"
            breadth_score = 0.55
        return {
            "vix_level": float(vix_level or 0.0),
            "spy_regime": spy_regime,
            "breadth_score": breadth_score,
        }

    def _compute_spy_trend_context(self) -> Dict:
        """Lightweight SPY trend context used by regime-aware strategy gates."""
        try:
            bars = self.data_feed.get_daily_bars("SPY", days_back=55)
            if not bars or len(bars) < 50:
                return {"spy_below_50ma": False}
            closes = [float(bar.get("close", 0.0) or 0.0) for bar in bars[-50:]]
            if len(closes) < 50 or any(close <= 0 for close in closes):
                return {"spy_below_50ma": False}
            spy_close = float(closes[-1])
            spy_ma_50 = sum(closes) / 50.0
            return {
                "spy_below_50ma": bool(spy_close < spy_ma_50),
                "spy_close": round(spy_close, 4),
                "spy_ma_50": round(spy_ma_50, 4),
            }
        except Exception:
            return {"spy_below_50ma": False}

    @staticmethod
    def _is_sniper_regime_blocked(vix_level: float, spy_below_50ma: bool) -> bool:
        vix = float(vix_level or 0.0)
        return (bool(spy_below_50ma) and vix > 22.0) or vix > 28.0

    def _apply_sniper_universe_gate(self, universe_data: Dict, vix_level: float) -> Dict:
        sniper_universe = universe_data.get("sniper_universe") or set()
        spy_below_50ma = bool((self._current_regime_context or {}).get("spy_below_50ma", False))
        if self._is_sniper_regime_blocked(vix_level, spy_below_50ma):
            if sniper_universe:
                logger.info(
                    "   ⚠️ Sniper blocked: SPY_below_50ma=%s VIX=%.1f",
                    spy_below_50ma,
                    float(vix_level or 0.0),
                )
            universe_data["sniper_universe"] = set()
        return universe_data

    @staticmethod
    def _is_short_regime_paused(vix_level: float) -> bool:
        return float(vix_level or 0.0) >= 28.0

    @staticmethod
    def _is_short_regime_watch(vix_level: float) -> bool:
        vix = float(vix_level or 0.0)
        return 25.0 <= vix < 28.0

    def _get_short_score_floor(self) -> float:
        return self._as_float(os.environ.get("SHORT_SCORE_FLOOR"), 55.0)

    def _get_vix_watch_short_size_multiplier(self) -> float:
        multiplier = self._as_float(os.environ.get("VIX_SHORT_WATCH_SIZE_MULTIPLIER"), 0.25)
        if multiplier is None:
            return 0.25
        return max(0.01, min(1.0, float(multiplier)))

    def _get_vix_watch_short_max_position_pct(self) -> float:
        max_pct = self._as_float(os.environ.get("VIX_SHORT_WATCH_MAX_POSITION_PCT"), 3.0)
        if max_pct is None:
            return 3.0
        return max(0.25, float(max_pct))

    @staticmethod
    def _is_validate_short_only_enabled() -> bool:
        raw_value = str(os.environ.get("VALIDATE_SHORT_ONLY", "0")).strip().lower()
        return raw_value in ("1", "true", "yes", "on")

    def _is_strategy_frozen_for_short_validation(self, strategy: Optional[str]) -> bool:
        if not self._is_validate_short_only_enabled():
            return False
        strategy_name = str(strategy or "").upper().strip()
        return strategy_name in {"VOYAGER", "SNIPER", "REMORA", "CONTRARIAN"}

    def _log_validation_freeze(self, strategy: Optional[str]) -> None:
        strategy_name = str(strategy or "UNKNOWN").upper().strip() or "UNKNOWN"
        logger.warning("[FREEZE] %s frozen — SHORT validation mode active", strategy_name)

    def _is_short_live_enabled(self) -> bool:
        raw_value = str(os.environ.get("SHORT_LIVE_ENABLED", "1")).strip().lower()
        if raw_value in ("1", "true", "yes", "on"):
            return True
        # If the explicit env var disables shorts, still allow execution in PAPER mode
        # when the pilot is configured to execute. PAPER mode carries no real capital
        # risk, and shadow-only shorts in paper make the paper account meaningless for
        # validating short strategy performance.
        if self.execution_mode == "PAPER":
            pilot_execute = str(os.environ.get("PILOT_EXECUTE_PAPER", "0")).strip().lower()
            if pilot_execute in ("1", "true", "yes", "on"):
                return True
        return False

    def _get_opening_window_state(self, now_et: Optional[datetime] = None) -> tuple[bool, float]:
        now_et = now_et or datetime.now(self.et_tz)
        market_open_today = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_since_open = (now_et - market_open_today).total_seconds() / 60.0
        in_opening_window = 0.0 <= minutes_since_open < 15.0
        return in_opening_window, minutes_since_open

    def _get_market_breadth_context(self) -> Dict:
        service = getattr(self, "market_breadth_context", None)
        if service is None:
            return {}
        try:
            context = service.get_or_compute()
            self._latest_market_breadth_context = dict(context or {})
            return dict(context or {})
        except Exception as exc:
            logger.warning(f"Market breadth context unavailable: {exc}")
            return dict(getattr(self, "_latest_market_breadth_context", {}) or {})

    def _apply_short_candidate_controls(
        self,
        candidates: List,
        vix_level: float,
        *,
        source_label: str,
        default_strategy: str,
    ) -> List:
        if self._is_short_regime_paused(vix_level):
            logger.info(
                "   ⚠️  %s PAUSED: VIX=%.1f >= 28.0 (panic regime — squeeze risk too high)",
                source_label,
                float(vix_level or 0.0),
            )
            return []

        short_score_floor = self._get_short_score_floor()
        force_pilot = self._is_short_regime_watch(vix_level)
        filtered: List = []

        for candidate in candidates or []:
            normalized = self._normalize_opportunity(candidate, "SHORT", default_strategy) or candidate
            ticker = str(
                getattr(normalized, "ticker", None)
                or (normalized.get("ticker") if isinstance(normalized, dict) else "")
                or "?"
            ).upper()
            score = self._as_float(
                getattr(normalized, "score", None)
                if not isinstance(normalized, dict)
                else normalized.get("score"),
                0.0,
            )
            if score < short_score_floor:
                logger.info(
                    "   ⛔ SHORT score floor: %s score=%.0f < %.0f",
                    ticker,
                    float(score or 0.0),
                    float(short_score_floor),
                )
                continue
            if force_pilot:
                try:
                    setattr(normalized, "_vix_pilot_forced", True)
                    setattr(normalized, "_vix_watch_level", float(vix_level or 0.0))
                    setattr(normalized, "_vix_watch_threshold", 25.0)
                    setattr(normalized, "_vix_watch_policy", "VIX_SHORT_WATCH")
                except Exception:
                    pass
            filtered.append(normalized)

        if force_pilot:
            logger.info(
                "   ⚠️  %s VIX WATCH: VIX=%.1f in [25, 28) — short entries reduced to pilot-size risk",
                source_label,
                float(vix_level or 0.0),
            )
        return filtered

    @staticmethod
    def _is_voyager_crisis_accumulation_regime(vix_level: float) -> bool:
        vix = float(vix_level or 0.0)
        return 30.0 <= vix < 35.0

    def _apply_voyager_crisis_accumulation_controls(self, opportunities: List, vix_level: float) -> List:
        """
        In panic-but-not-crash regimes, Voyager stays available as a reduced-risk
        accumulation sleeve rather than normal full-risk trend execution.
        """
        if not self._is_voyager_crisis_accumulation_regime(vix_level):
            return list(opportunities or [])

        if not opportunities:
            return []

        market_cap_floor = 10_000_000_000.0
        accumulation_score_floor = 60.0
        accumulation_ratio_floor = 1.15
        max_distribution_score = 45.0
        tranche_multiplier = 0.5
        filtered: List = []

        for opportunity in opportunities or []:
            normalized = self._normalize_opportunity(opportunity, "LONG", "VOYAGER") or opportunity
            ticker = str(
                getattr(normalized, "ticker", None)
                or (opportunity.get("ticker") if isinstance(opportunity, dict) else "")
                or "?"
            ).upper()
            metrics = getattr(opportunity, "metrics", None)
            if not isinstance(metrics, dict) and isinstance(opportunity, dict):
                metrics = opportunity.get("metrics", {}) or {}
            metrics = metrics if isinstance(metrics, dict) else {}

            fundamentals = {}
            if self.voyager and hasattr(self.voyager, "_get_voyager_fundamentals"):
                try:
                    fundamentals = self.voyager._get_voyager_fundamentals(ticker) or {}
                except Exception:
                    fundamentals = {}

            market_cap = self._as_float((fundamentals or {}).get("market_cap"), 0.0) or 0.0
            fcf_positive = bool((fundamentals or {}).get("fcf_positive", False))
            debt_manageable = bool((fundamentals or {}).get("debt_manageable", False))
            accumulation_score = self._as_float(metrics.get("accumulation_score"), 0.0) or 0.0
            accumulation_ratio = self._as_float(metrics.get("accumulation_ratio"), 0.0) or 0.0
            distribution_score = self._as_float(metrics.get("distribution_score"), 100.0) or 100.0

            reject_reasons: List[str] = []
            if market_cap < market_cap_floor:
                reject_reasons.append("market_cap_below_10b")
            if not fcf_positive:
                reject_reasons.append("fcf_not_positive")
            if not debt_manageable:
                reject_reasons.append("debt_not_manageable")
            if accumulation_score < accumulation_score_floor and accumulation_ratio < accumulation_ratio_floor:
                reject_reasons.append("no_accumulation_stabilization")
            if distribution_score > max_distribution_score:
                reject_reasons.append("distribution_pressure_high")

            if reject_reasons:
                logger.info(
                    "   ⛔ VOYAGER crisis gate: %s rejected (%s)",
                    ticker,
                    ", ".join(reject_reasons),
                )
                self._log_decision_event(
                    ticker=ticker,
                    strategy="VOYAGER",
                    direction="LONG",
                    decision="SCANNER_REJECT",
                    reason="voyager_crisis_quality_gate_failed",
                    opp=normalized,
                    notes=(
                        f"reasons={','.join(reject_reasons)}|"
                        f"vix={float(vix_level or 0.0):.2f}|"
                        f"market_cap={int(market_cap)}|"
                        f"fcf_positive={int(fcf_positive)}|"
                        f"debt_manageable={int(debt_manageable)}|"
                        f"accumulation_score={float(accumulation_score):.1f}|"
                        f"accumulation_ratio={float(accumulation_ratio):.2f}|"
                        f"distribution_score={float(distribution_score):.1f}"
                    ),
                )
                continue

            base_shares = max(int(self._as_float(getattr(normalized, "shares", 1), 1.0) or 1), 1)
            staged_shares = max(1, int(base_shares * tranche_multiplier))
            staged_shares = min(staged_shares, base_shares)
            entry_price = self._as_float(getattr(normalized, "entry_price", None), 0.0) or 0.0
            stop_loss = self._as_float(getattr(normalized, "stop_loss", None), 0.0) or 0.0
            risk_per_share = abs(entry_price - stop_loss)

            try:
                setattr(normalized, "shares", staged_shares)
                if hasattr(normalized, "position_value") and entry_price > 0:
                    setattr(normalized, "position_value", staged_shares * entry_price)
                if hasattr(normalized, "risk_amount") and risk_per_share > 0:
                    setattr(normalized, "risk_amount", staged_shares * risk_per_share)
                if hasattr(normalized, "risk_pct") and self.account_size > 0 and risk_per_share > 0:
                    setattr(normalized, "risk_pct", ((staged_shares * risk_per_share) / self.account_size) * 100.0)
                if hasattr(normalized, "exposure_pct") and self.account_size > 0 and entry_price > 0:
                    setattr(normalized, "exposure_pct", ((staged_shares * entry_price) / self.account_size) * 100.0)
            except Exception:
                pass

            warnings = list(getattr(normalized, "warnings", []) or [])
            warnings.append("High-VIX crisis accumulation mode: reduced first tranche")
            try:
                setattr(normalized, "warnings", warnings)
            except Exception:
                pass

            confidence_summary = dict(getattr(normalized, "confidence_summary", {}) or {})
            confidence_summary["crisis_accumulation"] = {
                "enabled": True,
                "vix_level": round(float(vix_level or 0.0), 2),
                "size_multiplier": tranche_multiplier,
                "market_cap_floor": int(market_cap_floor),
                "market_cap": int(market_cap),
                "fcf_positive": fcf_positive,
                "debt_manageable": debt_manageable,
                "accumulation_score": round(float(accumulation_score), 1),
                "accumulation_ratio": round(float(accumulation_ratio), 2),
                "distribution_score": round(float(distribution_score), 1),
            }
            try:
                setattr(normalized, "confidence_summary", confidence_summary)
            except Exception:
                pass

            pathway_qualification = dict(getattr(normalized, "pathway_qualification", {}) or {})
            pathway_qualification["crisis_accumulation"] = True
            pathway_qualification["crisis_size_multiplier"] = tranche_multiplier
            try:
                setattr(normalized, "pathway_qualification", pathway_qualification)
                setattr(normalized, "_voyager_crisis_accumulation", True)
            except Exception:
                pass

            logger.info(
                "   🌊 VOYAGER crisis accumulation: %s accepted at reduced first tranche (%s -> %s shares)",
                ticker,
                base_shares,
                staged_shares,
            )
            filtered.append(normalized)

        return filtered

    def _apply_voyager_breadth_controls(self, opportunities: List, market_breadth_pct: Optional[float]) -> List:
        breadth = self._as_float(market_breadth_pct, None)
        if breadth is None:
            return list(opportunities or [])
        if breadth < 40.0:
            return []
        if breadth >= 60.0:
            return list(opportunities or [])

        size_multiplier = 0.5
        throttled: List = []
        for opportunity in opportunities or []:
            normalized = self._normalize_opportunity(opportunity, "LONG", "VOYAGER") or opportunity
            base_shares = max(int(self._as_float(getattr(normalized, "shares", 1), 1.0) or 1), 1)
            scaled_shares = max(1, int(base_shares * size_multiplier))
            scaled_shares = min(scaled_shares, base_shares)
            entry_price = self._as_float(getattr(normalized, "entry_price", None), 0.0) or 0.0
            stop_loss = self._as_float(getattr(normalized, "stop_loss", None), 0.0) or 0.0
            risk_per_share = abs(entry_price - stop_loss)

            try:
                setattr(normalized, "shares", scaled_shares)
                if hasattr(normalized, "position_value") and entry_price > 0:
                    setattr(normalized, "position_value", scaled_shares * entry_price)
                if hasattr(normalized, "risk_amount") and risk_per_share > 0:
                    setattr(normalized, "risk_amount", scaled_shares * risk_per_share)
                if hasattr(normalized, "risk_pct") and self.account_size > 0 and risk_per_share > 0:
                    setattr(normalized, "risk_pct", ((scaled_shares * risk_per_share) / self.account_size) * 100.0)
                if hasattr(normalized, "exposure_pct") and self.account_size > 0 and entry_price > 0:
                    setattr(normalized, "exposure_pct", ((scaled_shares * entry_price) / self.account_size) * 100.0)
                setattr(normalized, "_voyager_breadth_throttled", True)
                setattr(normalized, "_voyager_breadth_pct", float(breadth))
            except Exception:
                pass

            warnings = list(getattr(normalized, "warnings", []) or [])
            warnings.append(
                f"Market breadth throttle active: composite breadth {breadth:.1f}%, size reduced to 50%"
            )
            try:
                setattr(normalized, "warnings", warnings)
            except Exception:
                pass

            confidence_summary = dict(getattr(normalized, "confidence_summary", {}) or {})
            confidence_summary["breadth_gate"] = {
                "enabled": True,
                "market_breadth_pct": round(float(breadth), 2),
                "size_multiplier": size_multiplier,
            }
            try:
                setattr(normalized, "confidence_summary", confidence_summary)
            except Exception:
                pass

            pathway_qualification = dict(getattr(normalized, "pathway_qualification", {}) or {})
            pathway_qualification["breadth_throttle"] = True
            pathway_qualification["breadth_size_multiplier"] = size_multiplier
            try:
                setattr(normalized, "pathway_qualification", pathway_qualification)
            except Exception:
                pass

            logger.info(
                "   📉 VOYAGER breadth throttle: %s reduced to 50%% size (%s -> %s shares, composite breadth=%.1f%%)",
                getattr(normalized, "ticker", "?"),
                base_shares,
                scaled_shares,
                float(breadth),
            )
            throttled.append(normalized)

        return throttled

    def _should_suppress_voyager_long_scan(
        self,
        regime_status: str,
        regime_volatility: str,
        market_breadth_pct: Optional[float] = None,
    ) -> bool:
        breadth = self._as_float(market_breadth_pct, None)
        if breadth is not None and breadth < 40.0:
            return True
        raw = str(os.getenv("VOYAGER_SUPPRESS_IN_HIGH_VIX_SIDEWAYS", "1")).strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False
        return str(regime_volatility or "").upper() == "HIGH" and str(regime_status or "").upper() in {
            "SIDEWAYS",
            "BEAR",
            "CORRECTION",
        }

    def _build_decision_regime_context(
        self,
        vix_level: float,
        market_breadth_pct: Optional[float] = None,
    ) -> Dict:
        """Regime context persisted to runs/decisions for diagnostics."""
        try:
            vix = float(vix_level or 0.0)
        except Exception:
            vix = 0.0
        vix_regime = self._classify_vix_regime(vix)
        if vix <= 0:
            return self._normalize_regime_context({
                "status": "UNKNOWN",
                "volatility": "UNKNOWN",
                "regime_overall": "UNKNOWN",
                "regime_spy": "UNKNOWN",
                "regime_vix": "UNKNOWN",
                "vix_level": 0.0,
                "market_breadth_pct": self._as_float(market_breadth_pct, None),
                **self._compute_spy_trend_context(),
            })
        snap = self._build_regime_snapshot(vix)
        return self._normalize_regime_context({
            "status": snap.get("spy_regime", "UNKNOWN"),
            "volatility": vix_regime,
            "regime_overall": snap.get("spy_regime", "UNKNOWN"),
            "regime_spy": snap.get("spy_regime", "UNKNOWN"),
            "regime_vix": vix_regime,
            "vix_level": vix,
            "market_breadth_pct": self._as_float(market_breadth_pct, None),
            **self._compute_spy_trend_context(),
        })

    def _check_open_position_theses(self) -> None:
        """
        Phase 3 — Thesis lifecycle monitoring.

        For every open trade in the DB, evaluate thesis health (INTACT/WARN/BROKEN)
        and update the thesis_* columns on the trades row.  When status changes,
        write a thesis_events log row and emit a warning log line.

        Safe no-op when:
          - thesis_monitor is None (import failed)
          - trades table has no Phase 3 columns (migration not yet run)
          - any individual position check raises an exception
        """
        if not self.thesis_monitor:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cur  = conn.cursor()

            # Check Phase 3 columns exist
            cur.execute("PRAGMA table_info(trades)")
            cols = {row[1] for row in cur.fetchall()}
            if "thesis_status" not in cols:
                conn.close()
                return

            cur.execute("""
                SELECT id, ticker, strategy, entry_price, stop_loss, target_price,
                       thesis_status, composite_score
                FROM trades WHERE exit_date IS NULL
            """)
            open_rows = cur.fetchall()
            conn.close()
        except Exception as exc:
            logger.debug(f"Thesis check DB read failed: {exc}")
            return

        for row in open_rows:
            (trade_id, ticker, strategy, entry_price,
             stop_loss, target_price, prev_status, comp_score) = row

            try:
                scores = {"composite_score": comp_score or 50}
                result = self.thesis_monitor.check_thesis(
                    ticker, strategy or "",
                    float(entry_price or 0),
                    scores=scores,
                    stop_loss=float(stop_loss) if stop_loss else None,
                    target_price=float(target_price) if target_price else None,
                )
                new_status   = result["status"]
                new_strength = result["strength"]
                new_reason   = result["reason"]

                if new_status == "NONE":
                    continue  # insufficient data — skip update

                ts = datetime.now().isoformat()
                try:
                    conn2 = sqlite3.connect(self.db_path)
                    conn2.execute("""
                        UPDATE trades SET
                            thesis_status=?, thesis_strength=?,
                            thesis_reason=?, thesis_updated_at=?
                        WHERE id=?
                    """, (new_status, new_strength, new_reason, ts, trade_id))
                    conn2.commit()
                    conn2.close()
                except Exception:
                    pass

                # Optional auto-protection: flatten Voyager positions on distribution break.
                self._maybe_exit_on_distribution_break(
                    ticker=ticker,
                    strategy=strategy or "",
                    new_status=new_status,
                    reason=new_reason,
                )

                # Log transition events and emit warnings
                if prev_status and prev_status != new_status:
                    logger.warning(
                        f"🧭 THESIS {ticker} [{strategy}]: "
                        f"{prev_status} → {new_status} — {new_reason}"
                    )
                    self.thesis_monitor.log_thesis_event(
                        ticker=ticker, to_status=new_status,
                        from_status=prev_status, reason=new_reason,
                        strength=new_strength, trade_id=trade_id,
                        strategy=strategy or "", scores=scores,
                    )
                elif new_status in ("WARN", "BROKEN"):
                    logger.info(
                        f"🧭 {ticker} thesis: {new_status} "
                        f"({new_strength:.2f}) — {new_reason}"
                    )

            except Exception as exc:
                logger.debug(f"Thesis check failed for {ticker}: {exc}")

    def _maybe_exit_on_distribution_break(
        self, ticker: str, strategy: str, new_status: str, reason: str
    ) -> None:
        """
        Optional safeguard for Voyager:
        when thesis is BROKEN due to distribution pressure, submit market exit.
        Disabled by default; enable with VOYAGER_DISTRIBUTION_AUTO_EXIT=1.
        """
        if not self.voyager_distribution_auto_exit:
            return
        strat = str(strategy or "").upper()
        if strat not in ("VOYAGER", "VOY"):
            return
        if str(new_status or "").upper() != "BROKEN":
            return
        reason_l = str(reason or "").lower()
        if "distribution" not in reason_l:
            return
        if not self.trading_client:
            return

        now_ts = time.time()
        tk = str(ticker or "").upper()
        if not tk:
            return
        last_ts = float(self._thesis_exit_cooldown.get(tk, 0.0) or 0.0)
        if (now_ts - last_ts) < 900:
            return

        try:
            pos = self.trading_client.get_open_position(tk)
            qty_raw = float(getattr(pos, "qty", 0.0) or 0.0)
            qty = int(abs(qty_raw))
            if qty <= 0:
                return

            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            side = OrderSide.SELL if qty_raw > 0 else OrderSide.BUY
            order = self.trading_client.submit_order(
                MarketOrderRequest(
                    symbol=tk,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                )
            )
            self._thesis_exit_cooldown[tk] = now_ts
            logger.warning(
                f"🛑 THESIS EXIT submitted for {tk}: distribution break "
                f"(order_id={getattr(order, 'id', 'n/a')})"
            )
        except Exception as exc:
            logger.warning(f"Thesis auto-exit failed for {tk}: {exc}")

    def _apply_overlay_and_portfolio_gates(self, opp, direction: str, proposed_shares: int):
        """
        Apply cached risk overlay and portfolio-level gates to an already-sized V3 trade.

        Returns: (allowed, final_shares, reason)
        """
        ticker = opp.ticker
        strategy = getattr(opp, "strategy", direction)

        try:
            overlay = self.risk_overlay.check_entry_allowed(ticker)
        except Exception as exc:
            logger.warning(f"Risk overlay failed for {ticker}: {exc}")
            overlay = {"allowed": True, "warnings": [], "blocks": [], "sector": {"sector": "UNKNOWN"}}

        if not overlay.get("allowed", True):
            reason = "; ".join(overlay.get("blocks", [])) or "risk_overlay_block"
            self._log_decision_event(
                ticker=ticker,
                strategy=strategy,
                direction=direction,
                decision="RISK_OVERLAY_DENIED",
                reason=reason,
                opp=opp,
                shares=proposed_shares,
                execution_denied=1,
                execution_deny_reason=reason,
                notes="risk_overlay_check",
            )
            return False, 0, reason

        # Strategy-aware macro event window gate
        macro_gate, macro_reason = self._get_macro_gate_for_strategy(strategy or direction)
        if macro_gate == 'DENY':
            logger.warning(f"   ⏸️  MACRO EVENT LOCKOUT: {ticker} ({strategy}) — {macro_reason}")
            self._log_decision_event(
                ticker=ticker,
                strategy=strategy,
                direction=direction,
                decision="MACRO_EVENT_DENY",
                reason=macro_reason,
                opp=opp,
                shares=proposed_shares,
                execution_denied=1,
                execution_deny_reason=macro_reason,
                notes="macro_event_window_gate",
            )
            return False, 0, macro_reason
        elif macro_gate == 'CAUTION':
            logger.info(f"   ⚠️  Macro caution [{strategy}] {ticker}: {macro_reason}")

        if self._has_existing_ticker_exposure(ticker):
            reason = f"DUPLICATE_TICKER_OPEN: {ticker} already has open exposure"
            self._log_decision_event(
                ticker=ticker,
                strategy=strategy,
                direction=direction,
                decision="PORTFOLIO_DENIED",
                reason=reason,
                opp=opp,
                shares=proposed_shares,
                execution_denied=1,
                execution_deny_reason=reason,
                notes="duplicate_ticker_guard",
            )
            return False, 0, reason

        sector_info = overlay.get("sector", {}) if isinstance(overlay.get("sector"), dict) else {}
        sector = sector_info.get("sector") or "UNKNOWN"
        market_value = float(proposed_shares * opp.entry_price)
        signal = {
            "entry_price": opp.entry_price,
            "stop_loss": opp.stop_loss,
            "target_price": opp.target_price,
            "direction": direction,
            "market_value": market_value,
            "position_value": market_value,
            "sector": sector,
            "shares": proposed_shares,
        }

        self._sync_positions_to_portfolio_coordinator()
        portfolio_decision = self.portfolio.evaluate_trade_request(
            strategy=strategy,
            ticker=ticker,
            signal=signal,
            decision={"decision": "EXECUTE", "avg_score": getattr(opp, "score", 0)},
        )
        if not portfolio_decision.get("approved", False):
            reason = portfolio_decision.get("reason", "portfolio_denied")
            self._log_decision_event(
                ticker=ticker,
                strategy=strategy,
                direction=direction,
                decision="PORTFOLIO_DENIED",
                reason=reason,
                opp=opp,
                shares=proposed_shares,
                execution_denied=1,
                execution_deny_reason=reason,
                notes="portfolio_coordinator",
            )
            return False, 0, reason

        allowed_shares = int(portfolio_decision.get("shares", proposed_shares) or proposed_shares)
        final_shares = proposed_shares
        if 0 < allowed_shares < proposed_shares:
            final_shares = allowed_shares
        return True, final_shares, portfolio_decision.get("reason", "approved")

    def _security_entry_allowed(self, opp, direction: str, shares: int) -> bool:
        """Run manual kill-switch and LIVE arming checks before new entries."""
        allowed, reason = is_entry_allowed(
            execution_enabled=bool(self.trading_client),
            execution_mode=self.execution_mode,
            killswitch_path=self.killswitch_path,
        )
        if allowed:
            return True

        ticker = getattr(opp, "ticker", "")
        strategy = getattr(opp, "strategy", direction)
        logger.warning(f"   ⛔ SECURITY DENIED: {reason}")
        self._log_decision_event(
            ticker=ticker,
            strategy=strategy,
            direction=direction,
            decision="SECURITY_DENIED",
            reason=reason,
            opp=opp,
            shares=shares,
            execution_denied=1,
            execution_deny_reason=reason,
            notes="security_baseline",
        )
        return False
    
    def _get_vix(self) -> float:
        """Fetch current VIX using the shared live/proxy helper."""
        snapshot = get_vix_snapshot(data_feed=self.data_feed)
        value = snapshot.get("vix_level")
        if isinstance(value, (int, float)):
            if snapshot.get("source") != "yfinance":
                logger.info(
                    "_get_vix: using %s (%.2f) [%s]",
                    snapshot.get("source"),
                    float(value),
                    snapshot.get("reason"),
                )
            return float(value)
        logger.warning("_get_vix: VIX unavailable [%s]", snapshot.get("reason"))
        return 0.0

    def _build_adaptive_universes(self) -> Dict:
        """
        Build adaptive universes for all strategies
        Returns universe data and confluence analysis
        """
        
        if not self.enable_adaptive_universes:
            return {
                'base_universe': set(),
                'voyager_universe': set(),
                'sniper_universe': set(),
                'short_universe': set(),
                'remora_universe': set(),
                'contrarian_universe': set(),
                'snapshot': None,
                'confluence': None
            }
        
        logger.info("🔍 Building adaptive universes...")
        self._write_progress_heartbeat("scan:universes", force=True)

        base_universe = set()
        voyager_universe = set()
        sniper_universe = set()
        short_universe = set()
        remora_universe = set()
        contrarian_universe = set()
        snapshot = None

        def extract_tickers(candidates):
            tickers = set()
            for candidate in candidates or []:
                if isinstance(candidate, dict):
                    symbol = str(candidate.get('ticker', '') or '').upper().strip()
                else:
                    symbol = str(candidate or '').upper().strip()
                if symbol:
                    tickers.add(symbol)
            return tickers

        if self.universe_snapshot_builder:
            try:
                snapshot = self.universe_snapshot_builder.build_snapshot()
                base_universe = set(snapshot.get('base_universe', []))
                voyager_universe = set(snapshot.get('voyager_universe', []))
                sniper_universe = set(snapshot.get('sniper_universe', []))
                short_universe = set(snapshot.get('short_universe', []))
                remora_universe = set(snapshot.get('remora_universe', []))
                contrarian_universe = set(snapshot.get('contrarian_universe', []))

                summary = snapshot.get('summary', {}) or {}
                if snapshot.get('fallback_used'):
                    logger.warning(
                        "   ⚠️ Dynamic universe snapshot fallback: %s",
                        snapshot.get('fallback_reason') or 'unknown'
                    )
                logger.info(
                    "   🌐 Shared base universe: %s symbols (source=%s, passed=%s)",
                    len(base_universe),
                    summary.get('source_assets', 0),
                    summary.get('passed_basic_filters', 0),
                )
                logger.info("   ✅ Voyager universe: %s candidates", len(voyager_universe))
                logger.info("   ✅ Sniper universe: %s candidates", len(sniper_universe))
                logger.info("   ✅ Short universe: %s candidates", len(short_universe))
                logger.info("   ✅ Remora universe: %s candidates", len(remora_universe))
                logger.info("   ✅ Contrarian universe: %s candidates", len(contrarian_universe))
                self._write_progress_heartbeat("scan:universes:snapshot")
            except Exception as e:
                logger.error(f"   ❌ Error building dynamic universe snapshot: {e}")
                snapshot = None
                base_universe = set()
                voyager_universe = set()
                sniper_universe = set()
                short_universe = set()
                remora_universe = set()
                contrarian_universe = set()

        # Legacy fallback: retain prior universe builders when dynamic snapshot is unavailable.
        if not base_universe:
            # Build Voyager universe
            if self.voyager and self.voyager_universe_builder:
                try:
                    voyager_result = self.voyager_universe_builder.build_universe(scan_type='quick')
                    voyager_long = voyager_result.get('long_candidates', [])
                    voyager_short = voyager_result.get('short_candidates', [])
                    voyager_universe = extract_tickers(voyager_long) | extract_tickers(voyager_short)
                    logger.info(f"   ✅ Voyager universe: {len(voyager_universe)} candidates")
                    self._write_progress_heartbeat("scan:universes:voyager")
                except Exception as e:
                    logger.error(f"   ❌ Error building Voyager universe: {e}")

            # Build Sniper universe
            if self.sniper_universe_builder:
                try:
                    sniper_result = self.sniper_universe_builder.build_universe()
                    sniper_universe = set(sniper_result.get('tickers', []))
                    logger.info(f"   ✅ Sniper universe: {len(sniper_universe)} candidates")
                    self._write_progress_heartbeat("scan:universes:sniper")
                except Exception as e:
                    logger.error(f"   ❌ Error building Sniper universe: {e}")

            # Build Remora universe
            if self.remora_universe_builder:
                try:
                    remora_result = self.remora_universe_builder.build_universe()
                    remora_universe = set(remora_result.get('tickers', []))
                    logger.info(f"   ✅ Remora universe: {len(remora_universe)} candidates")
                    self._write_progress_heartbeat("scan:universes:remora")
                except Exception as e:
                    logger.error(f"   ❌ Error building Remora universe: {e}")

            base_universe = voyager_universe | sniper_universe | remora_universe
            short_universe = set(voyager_universe)
            contrarian_universe = set(voyager_universe | sniper_universe)
        
        # Pre-scan overlap only (universe membership), not final signal confluence.
        # Do not call TripleConfluenceDetector here; that would emit legacy
        # "TRIPLE CONFLUENCE ANALYSIS" logs and pollute dashboard parsing.
        confluence_result = None
        if voyager_universe or sniper_universe or remora_universe:
            try:
                pre_triple = voyager_universe & sniper_universe & remora_universe
                pre_vs = (voyager_universe & sniper_universe) - pre_triple
                pre_vr = (voyager_universe & remora_universe) - pre_triple
                pre_sr = (sniper_universe & remora_universe) - pre_triple
                total_unique = len(voyager_universe | sniper_universe | remora_universe)

                confluence_result = {
                    'triple': [],
                    'double': {
                        'voyager_sniper': [],
                        'voyager_remora': [],
                        'sniper_remora': [],
                    },
                    'stats': {
                        'triple_count': len(pre_triple),
                        'double_count': len(pre_vs) + len(pre_vr) + len(pre_sr),
                        'triple_long_count': len(pre_triple),
                        'triple_short_count': 0,
                        'total_unique': total_unique,
                    },
                }
                logger.info(f"   🔎 Pre-signal overlap: {len(pre_triple)} symbols")

            except Exception as e:
                logger.error(f"   ❌ Error computing pre-signal overlap: {e}")
        
        return {
            'base_universe': base_universe,
            'voyager_universe': voyager_universe,
            'sniper_universe': sniper_universe,
            'short_universe': short_universe,
            'remora_universe': remora_universe,
            'contrarian_universe': contrarian_universe,
            'snapshot': snapshot,
            'confluence': confluence_result
        }

    def _build_signal_confluence(
        self,
        voyager_opportunities: List,
        sniper_opportunities: List,
        remora_opportunities: List,
    ) -> Optional[Dict]:
        """Build confluence from approved scanner outputs (not universe overlap)."""
        if not self.confluence_detector:
            return None

        try:
            if hasattr(self.confluence_detector, "analyze_from_opportunities"):
                return self.confluence_detector.analyze_from_opportunities(
                    voyager_opportunities or [],
                    sniper_opportunities or [],
                    remora_opportunities or [],
                )

            # Backward-compatible fallback.
            def _tickers(items):
                out = []
                for item in items or []:
                    if isinstance(item, dict):
                        t = item.get("ticker")
                    else:
                        t = getattr(item, "ticker", None)
                    if t:
                        out.append(str(t).upper())
                return out

            return self.confluence_detector.analyze_confluence(
                _tickers(voyager_opportunities),
                _tickers(sniper_opportunities),
                _tickers(remora_opportunities),
            )
        except Exception as exc:
            logger.error(f"   ❌ Signal confluence error: {exc}")
            return None
    
    def _execute_triple_confluence_trades(
        self,
        voyager_opportunities: List,
        confluence_result: Dict
    ):
        """
        Execute trades that have triple confluence
        
        Triple confluence trades get 1.8x position size
        """
        
        if not confluence_result:
            return
        
        # Get triple confluence tickers
        triple_tickers = {opp.ticker for opp in confluence_result.get('triple', [])}
        
        if not triple_tickers:
            logger.info("   No triple confluence trades to execute")
            return
        
        logger.warning("="*80)

    def _normalize_opportunity(self, opp, default_direction: str, default_strategy: str):
        """Convert dict- or object-shaped opportunities into execution-compatible objects."""
        if hasattr(opp, "ticker") and hasattr(opp, "entry_price"):
            return opp

        if not isinstance(opp, dict):
            return None

        ticker = opp.get("ticker")
        entry = opp.get("entry_price", opp.get("entry"))
        stop = opp.get("stop_loss", opp.get("stop"))
        target = opp.get("target_price", opp.get("target"))
        shares = int(opp.get("shares", 1) or 1)
        direction = str(opp.get("direction", default_direction)).upper()
        strategy = opp.get("strategy", default_strategy)

        if not ticker or entry is None or stop is None or target is None:
            return None

        try:
            entry = float(entry)
            stop = float(stop)
            target = float(target)
        except (TypeError, ValueError):
            return None

        risk = abs(entry - stop)
        if risk <= 0:
            return None

        if direction == "SHORT":
            reward = max(entry - target, 0.0)
        else:
            reward = max(target - entry, 0.0)

        risk_reward = opp.get("risk_reward")
        if risk_reward is None:
            risk_reward = (reward / risk) if risk > 0 else 0.0

        expected_return = opp.get("expected_return")
        if expected_return is None and entry > 0:
            if direction == "SHORT":
                expected_return = ((entry - target) / entry) * 100
            else:
                expected_return = ((target - entry) / entry) * 100

        return _SniperOpp(
            ticker=str(ticker).upper(),
            direction=direction,
            entry_price=entry,
            target_price=target,
            stop_loss=stop,
            score=int(round(float(opp.get("score", 0) or 0))),
            risk_reward=float(risk_reward),
            shares=shares,
            expected_return=float(expected_return or 0.0),
            strategy=str(strategy).upper(),
            grade=opp.get("grade", "N/A"),
            growth_mode=bool(opp.get("growth_mode", False)),
            tier_breakdown=opp.get("tier_breakdown"),
            confidence_summary=opp.get("confidence_summary"),
            pathway_qualification=opp.get("pathway_qualification"),
            research_only=bool(opp.get("research_only", False)),
            dormant=bool(opp.get("dormant", False)),
            execution_block_reason=opp.get("execution_block_reason"),
            macro_gate_context=opp.get("macro_gate_context"),
        )

    def _merge_unique_opportunities(self, existing: List, new_items: List, direction: str, strategy: str) -> List:
        """Merge opportunities without duplicate ticker collisions, preferring higher score."""
        merged: Dict[str, object] = {}

        for opp in existing:
            normalized = self._normalize_opportunity(opp, direction, strategy) or opp
            ticker = getattr(normalized, "ticker", None) or (normalized.get("ticker") if isinstance(normalized, dict) else None)
            if ticker:
                merged[str(ticker).upper()] = normalized

        for opp in new_items:
            normalized = self._normalize_opportunity(opp, direction, strategy)
            if normalized is None:
                continue
            current = merged.get(normalized.ticker)
            if current is None or getattr(current, "score", 0) < normalized.score:
                merged[normalized.ticker] = normalized

        return list(merged.values())

    def _apply_machine_ranking(self, long_opportunities: List, short_opportunities: List) -> tuple[List, List]:
        """Rank approved opportunities so execution order follows best current ideas."""
        normalized_longs: List = []
        normalized_shorts: List = []

        for opp in long_opportunities or []:
            direction = str(getattr(opp, "direction", "LONG") or "LONG").upper()
            strategy = str(getattr(opp, "strategy", "VOYAGER") or "VOYAGER").upper()
            normalized = self._normalize_opportunity(opp, direction, strategy) or opp
            self._get_phase3_context(normalized)
            normalized_longs.append(normalized)

        for opp in short_opportunities or []:
            direction = str(getattr(opp, "direction", "SHORT") or "SHORT").upper()
            strategy = str(getattr(opp, "strategy", "SHORT") or "SHORT").upper()
            normalized = self._normalize_opportunity(opp, direction, strategy) or opp
            self._get_phase3_context(normalized)
            normalized_shorts.append(normalized)

        all_candidates = normalized_longs + normalized_shorts
        if not all_candidates or self.candidate_ranker is None:
            return normalized_longs, normalized_shorts

        ranked = self.candidate_ranker.rank_live_opportunities(
            all_candidates,
            regime_snapshot=self._current_regime_context or {},
        )
        rank_lookup = {
            (row.ticker, row.strategy, row.direction): row
            for row in ranked
        }
        for opp in all_candidates:
            key = (
                str(getattr(opp, "ticker", "")).upper(),
                str(getattr(opp, "strategy", "")).upper(),
                str(getattr(opp, "direction", "LONG")).upper(),
            )
            rank_row = rank_lookup.get(key)
            if rank_row is None:
                continue
            try:
                setattr(opp, "machine_rank_score", float(rank_row.overall_rank_score))
                setattr(opp, "machine_rank_reason", str(rank_row.why))
                setattr(opp, "machine_rank_prior", str(rank_row.prior_label))
                setattr(opp, "bte_breakout_probability", rank_row.bte_breakout_probability)
                setattr(opp, "bte_timing_window_low_days", rank_row.bte_timing_window_low_days)
                setattr(opp, "bte_timing_window_high_days", rank_row.bte_timing_window_high_days)
                setattr(opp, "bte_source_dimension", rank_row.bte_source_dimension)
                setattr(opp, "bte_source_segment", rank_row.bte_source_segment)
                setattr(opp, "bte_advisory_label", rank_row.bte_advisory_label)
                setattr(opp, "bte_short_breakdown_probability", rank_row.bte_short_breakdown_probability)
                setattr(opp, "bte_short_timing_window_low_days", rank_row.bte_short_timing_window_low_days)
                setattr(opp, "bte_short_timing_window_high_days", rank_row.bte_short_timing_window_high_days)
                setattr(opp, "bte_short_source_dimension", rank_row.bte_short_source_dimension)
                setattr(opp, "bte_short_source_segment", rank_row.bte_short_source_segment)
                setattr(opp, "bte_short_advisory_label", rank_row.bte_short_advisory_label)
            except Exception:
                pass

        normalized_longs.sort(
            key=lambda opp: (
                float(getattr(opp, "machine_rank_score", 0.0) or 0.0),
                float(getattr(opp, "score", 0.0) or 0.0),
                float(getattr(opp, "risk_reward", 0.0) or 0.0),
            ),
            reverse=True,
        )
        normalized_shorts.sort(
            key=lambda opp: (
                float(getattr(opp, "machine_rank_score", 0.0) or 0.0),
                float(getattr(opp, "score", 0.0) or 0.0),
                float(getattr(opp, "risk_reward", 0.0) or 0.0),
            ),
            reverse=True,
        )

        logger.info("")
        logger.info("🏁 MACHINE-RANKED EXECUTION SLATE")
        for row in ranked[:8]:
            bte_suffix = ""
            if (
                row.strategy == "SNIPER"
                and row.bte_breakout_probability is not None
                and row.bte_timing_window_low_days is not None
                and row.bte_timing_window_high_days is not None
            ):
                bte_suffix = (
                    f" bte={row.bte_breakout_probability:.2f}"
                    f"@{row.bte_timing_window_low_days:g}-{row.bte_timing_window_high_days:g}d"
                )
            elif (
                row.strategy == "SHORT"
                and row.bte_short_breakdown_probability is not None
                and row.bte_short_timing_window_low_days is not None
                and row.bte_short_timing_window_high_days is not None
            ):
                bte_suffix = (
                    f" bte-short={row.bte_short_breakdown_probability:.2f}"
                    f"@{row.bte_short_timing_window_low_days:g}-{row.bte_short_timing_window_high_days:g}d"
                )
            logger.info(
                "   %s %-8s %-5s rank=%.1f rr=%s score=%s%s",
                row.ticker,
                row.strategy,
                row.direction,
                row.overall_rank_score,
                f"{row.rr:.2f}" if row.rr is not None else "n/a",
                f"{row.avg_score:.1f}" if row.avg_score is not None else "n/a",
                bte_suffix,
            )

        return normalized_longs, normalized_shorts

    def _execute_triple_confluence_trades(
        self,
        voyager_opportunities: List,
        confluence_result: Dict
    ):
        """
        Execute trades that have triple confluence.

        Triple confluence trades get 1.8x position size.
        """

        if not confluence_result:
            return

        triple_tickers = {opp.ticker for opp in confluence_result.get('triple', [])}
        if not triple_tickers:
            logger.info("   No triple confluence trades to execute")
            return

        if self._is_strategy_frozen_for_short_validation("VOYAGER"):
            self._log_validation_freeze("VOYAGER")
            return

        logger.warning("="*80)
        logger.warning("💎 EXECUTING TRIPLE CONFLUENCE TRADES")
        logger.warning("="*80)
        
        for opp in voyager_opportunities:
            if opp.ticker in triple_tickers:
                self._get_phase3_context(opp)
                logger.warning(f"\n⭐⭐⭐ {opp.ticker} - MAXIMUM CONVICTION TRADE")
                logger.warning(f"   Entry: ${opp.entry_price:.2f}")
                logger.warning(f"   Target: ${opp.target_price:.2f} (+{opp.expected_return:.1f}%)")
                logger.warning(f"   Stop: ${opp.stop_loss:.2f}")
                logger.warning(f"   R/R: {opp.risk_reward:.1f}:1")
                logger.warning(f"   Score: {opp.score}/100")
                logger.warning(f"   Position: 1.8x normal (triple confluence bonus)")
                
                # Calculate position with 1.8x multiplier
                base_shares = opp.shares
                enhanced_shares = int(base_shares * 1.8)
                enhanced_value = enhanced_shares * opp.entry_price
                
                logger.warning(f"   Shares: {enhanced_shares} (base: {base_shares})")
                logger.warning(f"   Value: ${enhanced_value:,.2f}")

                allowed, gated_shares, gate_reason = self._apply_overlay_and_portfolio_gates(
                    opp, "LONG", enhanced_shares
                )
                if not allowed:
                    logger.warning(f"   ❌ RISK/PORTFOLIO DENIED (triple): {gate_reason}")
                    continue
                enhanced_shares = gated_shares
                enhanced_value = enhanced_shares * opp.entry_price

                corr_ok, corr_reason = self._check_portfolio_correlation_guard(opp.ticker, "LONG")
                if not corr_ok:
                    logger.warning(f"   ❌ CORRELATION BLOCKED (triple): {corr_reason}")
                    self._log_decision_event(
                        ticker=opp.ticker,
                        strategy=getattr(opp, 'strategy', 'VOYAGER'),
                        direction='LONG',
                        decision="PORTFOLIO_DENIED",
                        reason=str(corr_reason),
                        opp=opp,
                        shares=enhanced_shares,
                        execution_denied=1,
                        execution_deny_reason=str(corr_reason),
                        notes="triple_confluence_correlation_guard",
                        correlation_blocked=1,
                    )
                    continue

                # Concentration check before triple confluence order
                conc_ok, conc_reason = self._check_concentration(opp.ticker, enhanced_value)
                if not conc_ok:
                    logger.warning(f"   ❌ CONCENTRATION DENIED (triple): {conc_reason}")
                    self._log_decision_event(
                        ticker=opp.ticker,
                        strategy=getattr(opp, 'strategy', 'VOYAGER'),
                        direction='LONG',
                        decision="PORTFOLIO_DENIED",
                        reason=conc_reason,
                        opp=opp,
                        shares=enhanced_shares,
                        execution_denied=1,
                        execution_deny_reason=conc_reason,
                        notes="triple_confluence_concentration",
                    )
                    continue

                if bool(getattr(self, "_opening_window_block", False)):
                    minutes_since_open = self._as_float(getattr(self, "_opening_window_minutes", None), 0.0)
                    logger.info(
                        "   ⏸  OPENING WINDOW: %.1f min since open — triple confluence entry deferred",
                        float(minutes_since_open or 0.0),
                    )
                    self._log_decision_event(
                        ticker=opp.ticker,
                        strategy=getattr(opp, 'strategy', 'VOYAGER'),
                        direction='LONG',
                        decision="EXECUTION_DENIED",
                        reason="opening_window_15min",
                        opp=opp,
                        shares=enhanced_shares,
                        execution_denied=1,
                        execution_deny_reason="opening_window_15min",
                        notes=f"triple_confluence=1|minutes_since_open={float(minutes_since_open or 0.0):.2f}",
                    )
                    continue

                if self.trading_client:
                    if not self._security_entry_allowed(opp=opp, direction='LONG', shares=enhanced_shares):
                        continue
                    try:
                        from alpaca.trading.requests import (
                            MarketOrderRequest, TakeProfitRequest, StopLossRequest
                        )
                        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
                        from execution_policy import require_bracket_available, require_not_halted_or_frozen

                        stop_price = round(float(opp.stop_loss), 2)
                        target_price = round(float(opp.target_price), 2)

                        quote_data = self.data_feed.get_real_time_quote(opp.ticker)
                        halt_check = require_not_halted_or_frozen(quote_data, max_quote_age_seconds=30)
                        if not halt_check.allowed:
                            logger.warning(f"   ❌ EXECUTION DENIED: {halt_check.reason}")
                            self._log_decision_event(
                                ticker=opp.ticker,
                                strategy=getattr(opp, 'strategy', 'VOYAGER'),
                                direction='LONG',
                                decision="EXECUTION_DENIED",
                                reason=halt_check.reason,
                                opp=opp,
                                shares=enhanced_shares,
                                execution_denied=1,
                                execution_deny_reason=halt_check.reason,
                                notes="triple_confluence_halt_or_quote",
                            )
                        else:
                            bracket_check = require_bracket_available(
                                "LONG", stop_price, target_price, entry=opp.entry_price
                            )
                            if not bracket_check.allowed:
                                logger.warning(f"   ❌ BRACKET DENIED: {bracket_check.reason}")
                                self._log_decision_event(
                                    ticker=opp.ticker,
                                    strategy=getattr(opp, 'strategy', 'VOYAGER'),
                                    direction='LONG',
                                    decision="EXECUTION_DENIED",
                                    reason=bracket_check.reason,
                                    opp=opp,
                                    shares=enhanced_shares,
                                    execution_denied=1,
                                    execution_deny_reason=bracket_check.reason,
                                    notes="triple_confluence_bracket",
                                )
                            else:
                                order_request = MarketOrderRequest(
                                    symbol=opp.ticker,
                                    qty=enhanced_shares,
                                    side=OrderSide.BUY,
                                    time_in_force=TimeInForce.GTC,
                                    order_class=OrderClass.BRACKET,
                                    stop_loss=StopLossRequest(stop_price=stop_price),
                                    take_profit=TakeProfitRequest(limit_price=target_price)
                                )
                                order = self.trading_client.submit_order(order_request)
                                self._log_decision_event(
                                    ticker=opp.ticker,
                                    strategy=getattr(opp, 'strategy', 'VOYAGER'),
                                    direction='LONG',
                                    decision="ENTRY_SUBMITTED",
                                    reason="triple_confluence_bracket_order_submitted",
                                    opp=opp,
                                    shares=enhanced_shares,
                                    order_submitted=1,
                                    order_id=str(order.id),
                                    position_opened=1,
                                    notes=f"status={order.status}|triple_confluence=1",
                                )
                                logger.warning(f"   ✅ TRIPLE CONFLUENCE BRACKET ORDER EXECUTED!")
                                logger.warning(f"      Order ID: {order.id}")
                                logger.warning(f"      Stop:     ${stop_price:.2f}")
                                logger.warning(f"      Target:   ${target_price:.2f}")
                    except Exception as e:
                        logger.error(f"   ❌ Triple confluence order failed: {e}")
                        self._log_decision_event(
                            ticker=opp.ticker,
                            strategy=getattr(opp, 'strategy', 'VOYAGER'),
                            direction='LONG',
                            decision="EXECUTION_DENIED",
                            reason=str(e),
                            opp=opp,
                            shares=enhanced_shares,
                            execution_denied=1,
                            execution_deny_reason=str(e),
                            notes="triple_confluence_submit_order_exception",
                        )
                        import traceback
                        logger.error(traceback.format_exc())
                else:
                    logger.warning(f"   📝 PAPER MODE - Trade logged (no trading client)")
                    self._log_decision_event(
                        ticker=opp.ticker,
                        strategy=getattr(opp, 'strategy', 'VOYAGER'),
                        direction='LONG',
                        decision="PAPER_ENTRY",
                        reason="paper_mode",
                        opp=opp,
                        shares=enhanced_shares,
                        position_opened=1,
                        notes="paper_mode|triple_confluence=1",
                    )
                
                # Record execution
                trade = TradeExecution(
                    ticker=opp.ticker,
                    direction='LONG',
                    entry_price=opp.entry_price,
                    target_price=opp.target_price,
                    stop_loss=opp.stop_loss,
                    position_size=enhanced_shares,
                    position_value=enhanced_value,
                    risk_amount=opp.risk_amount * 1.8,
                    confluence_level=3,
                    timestamp=datetime.now()
                )
                self.executed_trades.append(trade)
                self._record_entry_performance(opp, enhanced_shares)
                self.log_trade_to_journal({
                    'ticker': opp.ticker,
                    'strategy': getattr(opp, 'strategy', 'VOYAGER'),
                    'direction': 'LONG',
                    'shares': enhanced_shares,
                    'entry': opp.entry_price,
                    'stop': opp.stop_loss,
                    'target': opp.target_price,
                    'score': getattr(opp, 'score', 0),
                    'grade': getattr(opp, 'grade', 'N/A'),
                    'growth_mode': getattr(opp, 'growth_mode', False),
                    'tier_breakdown': getattr(opp, 'tier_breakdown', {}) or {},
                    'confidence_summary': getattr(opp, 'confidence_summary', {}) or {},
                    'pathway_qualification': getattr(opp, 'pathway_qualification', {}) or {},
                    'position_pct': (enhanced_value / self.account_size) * 100 if self.account_size else 0,
                    'risk_pct': ((enhanced_shares * abs(opp.entry_price - opp.stop_loss)) / self.account_size) * 100 if self.account_size else 0,
                })
        
        logger.warning("="*80)

    def _execute_all_approved_trades(
        self,
        long_opportunities: List,
        short_opportunities: List,
        confluence_result: Dict
    ):
        """Execute ALL approved trades (longs and shorts) with proper bracket orders"""

        if not long_opportunities and not short_opportunities:
            return

        # Build confluence lookup tables
        triple_tickers = set()
        double_tickers = {}

        if confluence_result:
            triple_conf = confluence_result.get('triple', [])
            if isinstance(triple_conf, list):
                for item in triple_conf:
                    if hasattr(item, 'ticker'):
                        triple_tickers.add(item.ticker)
                    elif isinstance(item, dict):
                        triple_tickers.add(item.get('ticker', ''))

            double_conf = confluence_result.get('double', {})
            for combo, items in double_conf.items():
                for item in items:
                    if hasattr(item, 'ticker'):
                        double_tickers[item.ticker] = combo
                    elif isinstance(item, dict):
                        double_tickers[item.get('ticker', '')] = combo

        logger.info("")
        logger.info("="*80)
        logger.info("📊 EXECUTING ALL APPROVED TRADES")
        logger.info("="*80)

        execution_queue = []
        for opp in long_opportunities:
            execution_queue.append((opp, str(getattr(opp, "direction", "LONG") or "LONG").upper()))
        for opp in short_opportunities:
            execution_queue.append((opp, str(getattr(opp, "direction", "SHORT") or "SHORT").upper()))

        execution_queue.sort(
            key=lambda item: (
                float(getattr(item[0], "machine_rank_score", 0.0) or 0.0),
                float(getattr(item[0], "score", 0.0) or 0.0),
                float(getattr(item[0], "risk_reward", 0.0) or 0.0),
            ),
            reverse=True,
        )

        attempted_total = len(execution_queue)
        executed_long = 0
        executed_short = 0
        blocked_total = 0
        for opp, direction in execution_queue:
            executed, _reason = self._execute_single_trade(opp, direction, triple_tickers, double_tickers)
            if executed:
                if direction == "SHORT":
                    executed_short += 1
                else:
                    executed_long += 1
            else:
                blocked_total += 1

        executed_total = executed_long + executed_short
        logger.info("")
        logger.info("="*80)
        logger.info(
            f"✅ Executed {executed_total}/{attempted_total} trades "
            f"({executed_long} long, {executed_short} short)"
        )
        if blocked_total:
            logger.info(f"⛔ Blocked {blocked_total} approved candidates this cycle")
        logger.info("="*80)

    def log_trade_to_journal(self, trade_details: Dict):
        """Log trade with weighted scoring metadata."""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'ticker': trade_details['ticker'],
            'strategy': trade_details.get('strategy', 'VOYAGER'),
            'direction': trade_details.get('direction', 'LONG'),
            'shares': trade_details.get('shares', trade_details.get('position_size', 0)),
            'entry': trade_details.get('entry', trade_details.get('entry_price')),
            'stop': trade_details.get('stop', trade_details.get('stop_loss')),
            'target': trade_details.get('target', trade_details.get('target_price')),
            'score': trade_details.get('score', 0),
            'grade': trade_details.get('grade', 'N/A'),
            'growth_mode': trade_details.get('growth_mode', False),
            'tier_1_pct': trade_details.get('tier_breakdown', {}).get('tier_1', {}).get('pct', 0),
            'tier_2_pct': trade_details.get('tier_breakdown', {}).get('tier_2', {}).get('pct', 0),
            'confidence_summary': trade_details.get('confidence_summary', {}),
            'pathway_qualification': trade_details.get('pathway_qualification', {}),
            'primary_pathway': trade_details.get('pathway_qualification', {}).get('primary_pathway'),
            'pathways_used': trade_details.get('pathway_qualification', {}).get('pathways_passed', []),
            'position_pct': trade_details.get('position_pct', 0),
            'risk_pct': trade_details.get('risk_pct', 0)
        }
        with open('trade_journal.jsonl', 'a') as f:
            f.write(json.dumps(entry) + '\n')

    def _execute_single_trade(
        self,
        opp,
        direction: str,
        triple_tickers: set,
        double_tickers: dict,
        pilot_ctx: Optional[Dict] = None,
    ):
        """Execute a single trade (LONG or SHORT) with a bracket order"""

        ticker = opp.ticker
        self._get_phase3_context(opp)
        pilot_ctx = pilot_ctx or {}
        is_pilot = 1 if pilot_ctx else None
        pilot_policy = pilot_ctx.get("policy_name") if pilot_ctx else None
        pilot_threshold = self._as_float(pilot_ctx.get("shadow_threshold"), None) if pilot_ctx else None
        pilot_decision_reason = pilot_ctx.get("decision_reason") if pilot_ctx else None
        strategy_name = str(getattr(opp, "strategy", "VOYAGER" if direction == "LONG" else "SHORT") or "").upper().strip()

        if self._is_strategy_frozen_for_short_validation(strategy_name):
            self._log_validation_freeze(strategy_name)
            return False, "short_validation_mode_frozen"

        if bool(getattr(opp, "research_only", False)) or bool(getattr(opp, "dormant", False)):
            block_reason = str(getattr(opp, "execution_block_reason", "") or "research_only_dormant")
            macro_gate_ctx = getattr(opp, "macro_gate_context", {}) or {}
            spy_close = macro_gate_ctx.get("spy_close")
            spy_ma200 = macro_gate_ctx.get("spy_ma200")
            vix_level = macro_gate_ctx.get("vix_level")
            logger.info(f"   [DORMANT] {ticker} execution blocked — {block_reason}")
            if macro_gate_ctx.get("applies"):
                spy_close_text = f"{float(spy_close):.2f}" if spy_close is not None else "n/a"
                spy_ma200_text = f"{float(spy_ma200):.2f}" if spy_ma200 is not None else "n/a"
                logger.info(
                    "   Short macro gate: FAILED | SPY %s vs 200MA %s | VIX %.1f",
                    spy_close_text,
                    spy_ma200_text,
                    float(vix_level or 0.0),
                )
            self._log_decision_event(
                ticker=ticker,
                strategy=strategy_name,
                direction=direction,
                decision="RESEARCH_ONLY_BLOCKED",
                reason=block_reason,
                opp=opp,
                shares=getattr(opp, "shares", 0),
                execution_denied=1,
                execution_deny_reason=block_reason,
                notes="short_macro_gate_dormant",
            )
            return False, block_reason

        # Triple confluence trades already submitted at 1.8x — skip
        if ticker in triple_tickers:
            logger.info(f"\nℹ️  {ticker} handled as triple confluence (1.8x size)")
            return False, "TRIPLE_ALREADY_HANDLED"

        # Determine multiplier
        if ticker in double_tickers:
            multiplier = 1.3
            confluence_type = "🔗 DOUBLE CONFLUENCE"
            combo = double_tickers[ticker]
            logger.info(f"\n{confluence_type}: {ticker} ({combo})")
        else:
            multiplier = 1.0
            confluence_type = "📊 SINGLE STRATEGY"
            combo = None
            logger.info(f"\n{confluence_type}: {ticker}")

        expected_return = getattr(opp, 'expected_return', None)
        if expected_return is None and opp.entry_price:
            if direction == 'LONG':
                expected_return = ((opp.target_price - opp.entry_price) / opp.entry_price) * 100
            else:
                expected_return = ((opp.entry_price - opp.target_price) / opp.entry_price) * 100

        logger.info(f"   Direction: {direction}")
        logger.info(f"   Entry: ${opp.entry_price:.2f}")
        logger.info(f"   Target: ${opp.target_price:.2f} ({expected_return:+.1f}%)")
        logger.info(f"   Stop: ${opp.stop_loss:.2f}")
        logger.info(f"   Score: {opp.score}/100")
        logger.info(f"   R/R: {opp.risk_reward:.1f}:1")
        rank_score = float(getattr(opp, "machine_rank_score", 0.0) or 0.0)
        if rank_score > 0:
            logger.info(f"   Machine Rank: {rank_score:.1f}")
            rank_reason = str(getattr(opp, "machine_rank_reason", "") or "").strip()
            if rank_reason:
                logger.info(f"   Rank Why: {rank_reason}")
        logger.info(f"   Position Multiplier: {multiplier}x")

        base_shares = opp.shares
        enhanced_shares = int(base_shares * multiplier)
        if direction == "SHORT" and bool(getattr(opp, "_vix_pilot_forced", False)):
            watch_multiplier = self._get_vix_watch_short_size_multiplier()
            watch_max_position_pct = self._get_vix_watch_short_max_position_pct()
            reduced_shares = max(1, int(enhanced_shares * watch_multiplier))
            if self.account_size and opp.entry_price:
                max_notional = float(self.account_size) * (watch_max_position_pct / 100.0)
                notional_cap_shares = max(1, int(max_notional / float(opp.entry_price)))
                reduced_shares = min(reduced_shares, notional_cap_shares)
            enhanced_shares = max(1, int(reduced_shares))
            is_pilot = 1
            pilot_policy = pilot_policy or str(getattr(opp, "_vix_watch_policy", "VIX_SHORT_WATCH"))
            if pilot_threshold is None:
                pilot_threshold = self._as_float(getattr(opp, "_vix_watch_threshold", None), 25.0)
            pilot_decision_reason = (
                f"vix_watch_reduced_risk:"
                f"vix={self._as_float(getattr(opp, '_vix_watch_level', None), 0.0):.1f};"
                f"size_multiplier={watch_multiplier:.2f};"
                f"max_position_pct={watch_max_position_pct:.1f}"
            )
            logger.info(
                "   VIX watch reduced-risk sizing: shares %s (base: %s, size_multiplier: %.2f, max_position_pct: %.1f%%)",
                enhanced_shares,
                base_shares,
                watch_multiplier,
                watch_max_position_pct,
            )
        if direction == "LONG" and bool(getattr(opp, "_voyager_crisis_accumulation", False)):
            logger.info("   Crisis accumulation mode: reduced first tranche active")
        enhanced_value = enhanced_shares * opp.entry_price

        allowed, gated_shares, gate_reason = self._apply_overlay_and_portfolio_gates(
            opp, direction, enhanced_shares
        )
        if not allowed:
            logger.warning(f"   ❌ RISK/PORTFOLIO DENIED: {gate_reason}")
            return False, gate_reason or "RISK_PORTFOLIO_DENIED"
        enhanced_shares = gated_shares
        enhanced_value = enhanced_shares * opp.entry_price

        logger.info(f"   Shares: {enhanced_shares} (base: {base_shares})")
        logger.info(f"   Value: ${enhanced_value:,.2f}")

        # Phase 5: execution-vs-scanner RR drift visibility.
        scanner_rr = float(getattr(opp, "risk_reward", 0) or 0)
        execution_rr = self._calc_execution_rr(direction, opp.entry_price, opp.stop_loss, opp.target_price)
        if scanner_rr > 0 and execution_rr > 0 and execution_rr < (scanner_rr * 0.7):
            rr_key = f"{ticker}:{self.scan_count}"
            if rr_key not in self._warned_rr_alignment:
                self._warned_rr_alignment.add(rr_key)
                logger.warning(
                    f"[RR_ALIGNMENT] {getattr(opp, 'strategy', direction)} {ticker}: "
                    f"scanner_rr={scanner_rr:.2f} but execution_rr={execution_rr:.2f} "
                    f"— stop/target calculation divergence"
                )

        # Correlation guard — optional, additive hard block before order submission.
        corr_ok, corr_reason = self._check_portfolio_correlation_guard(ticker, direction)
        if not corr_ok:
            logger.warning(f"   ❌ CORRELATION BLOCKED: {corr_reason}")
            self._log_decision_event(
                ticker=ticker,
                strategy=getattr(opp, 'strategy', direction),
                direction=direction,
                decision="PORTFOLIO_DENIED",
                reason=str(corr_reason),
                opp=opp,
                shares=enhanced_shares,
                execution_denied=1,
                execution_deny_reason=str(corr_reason),
                notes="correlation_guard",
                correlation_blocked=1,
                is_pilot=is_pilot,
                pilot_policy=pilot_policy,
                pilot_threshold=pilot_threshold,
                pilot_decision_reason=pilot_decision_reason or str(corr_reason),
            )
            return False, str(corr_reason)

        # Concentration check — hard block before any order submission
        conc_ok, conc_reason = self._check_concentration(ticker, enhanced_value)
        if not conc_ok:
            logger.warning(f"   ❌ CONCENTRATION DENIED: {conc_reason}")
            self._log_decision_event(
                ticker=ticker,
                strategy=getattr(opp, 'strategy', direction),
                direction=direction,
                decision="PORTFOLIO_DENIED",
                reason=conc_reason,
                opp=opp,
                shares=enhanced_shares,
                execution_denied=1,
                execution_deny_reason=conc_reason,
                notes="concentration_check",
                is_pilot=is_pilot,
                pilot_policy=pilot_policy,
                pilot_threshold=pilot_threshold,
                pilot_decision_reason=pilot_decision_reason or conc_reason,
            )
            return False, conc_reason or "CONCENTRATION_DENIED"

        if bool(getattr(self, "_opening_window_block", False)):
            minutes_since_open = self._as_float(getattr(self, "_opening_window_minutes", None), 0.0)
            logger.info(
                "   ⏸  OPENING WINDOW: %.1f min since open — entry deferred (no orders before 9:45 ET)",
                float(minutes_since_open or 0.0),
            )
            self._log_decision_event(
                ticker=ticker,
                strategy=getattr(opp, 'strategy', direction),
                direction=direction,
                decision="EXECUTION_DENIED",
                reason="opening_window_15min",
                opp=opp,
                shares=enhanced_shares,
                execution_denied=1,
                execution_deny_reason="opening_window_15min",
                notes=f"minutes_since_open={float(minutes_since_open or 0.0):.2f}",
                is_pilot=is_pilot,
                pilot_policy=pilot_policy,
                pilot_threshold=pilot_threshold,
                pilot_decision_reason=pilot_decision_reason or "opening_window_15min",
            )
            return False, "opening_window_15min"

        if direction == "SHORT" and not self._is_short_live_enabled():
            logger.info("   ⏸  SHORT shadow-only mode: live short order submission disabled")
            self._log_decision_event(
                ticker=ticker,
                strategy=getattr(opp, 'strategy', direction),
                direction=direction,
                decision="EXECUTION_DENIED",
                reason="short_shadow_only_mode",
                opp=opp,
                shares=enhanced_shares,
                execution_denied=1,
                execution_deny_reason="short_shadow_only_mode",
                notes="SHORT_LIVE_ENABLED=0",
                is_pilot=is_pilot,
                pilot_policy=pilot_policy,
                pilot_threshold=pilot_threshold,
                pilot_decision_reason=pilot_decision_reason or "short_shadow_only_mode",
            )
            return False, "short_shadow_only_mode"

        if self.trading_client:
            if not self._security_entry_allowed(opp=opp, direction=direction, shares=enhanced_shares):
                return False, "SECURITY_DENIED"
            try:
                from alpaca.trading.requests import (
                    LimitOrderRequest, MarketOrderRequest, TakeProfitRequest, StopLossRequest
                )
                from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
                from execution_policy import require_bracket_available

                stop_price = round(float(opp.stop_loss), 2)
                target_price = round(float(opp.target_price), 2)

                # Quote / halt check
                quote_data = self.data_feed.get_real_time_quote(ticker)
                halt_check = require_not_halted_or_frozen(quote_data, max_quote_age_seconds=30)
                if not halt_check.allowed:
                    logger.warning(f"   ❌ EXECUTION DENIED: {halt_check.reason}")
                    self._log_decision_event(
                        ticker=ticker,
                        strategy=getattr(opp, 'strategy', direction),
                        direction=direction,
                        decision="EXECUTION_DENIED",
                        reason=halt_check.reason,
                        opp=opp,
                        shares=enhanced_shares,
                        execution_denied=1,
                        execution_deny_reason=halt_check.reason,
                        notes="halt_or_quote_check",
                        is_pilot=is_pilot,
                        pilot_policy=pilot_policy,
                        pilot_threshold=pilot_threshold,
                        pilot_decision_reason=pilot_decision_reason or halt_check.reason,
                    )
                    return False, halt_check.reason or "HALT_OR_QUOTE_DENIED"

                # Bracket sanity check (direction-aware: SHORT needs stop > entry > target)
                bracket_check = require_bracket_available(
                    direction, stop_price, target_price, entry=opp.entry_price
                )
                if not bracket_check.allowed:
                    logger.warning(f"   ❌ BRACKET DENIED: {bracket_check.reason}")
                    self._log_decision_event(
                        ticker=ticker,
                        strategy=getattr(opp, 'strategy', direction),
                        direction=direction,
                        decision="EXECUTION_DENIED",
                        reason=bracket_check.reason,
                        opp=opp,
                        shares=enhanced_shares,
                        execution_denied=1,
                        execution_deny_reason=bracket_check.reason,
                        notes="bracket_check",
                        is_pilot=is_pilot,
                        pilot_policy=pilot_policy,
                        pilot_threshold=pilot_threshold,
                        pilot_decision_reason=pilot_decision_reason or bracket_check.reason,
                    )
                    return False, bracket_check.reason or "BRACKET_DENIED"

                if direction == 'SHORT':
                    order_request = LimitOrderRequest(
                        symbol=ticker,
                        qty=enhanced_shares,
                        side=OrderSide.SELL,
                        limit_price=round(float(opp.entry_price), 2),
                        time_in_force=TimeInForce.DAY,
                        order_class=OrderClass.BRACKET,
                        stop_loss=StopLossRequest(stop_price=stop_price),
                        take_profit=TakeProfitRequest(limit_price=target_price)
                    )
                else:
                    order_request = MarketOrderRequest(
                        symbol=ticker,
                        qty=enhanced_shares,
                        side=OrderSide.BUY,
                        time_in_force=TimeInForce.GTC,
                        order_class=OrderClass.BRACKET,
                        stop_loss=StopLossRequest(stop_price=stop_price),
                        take_profit=TakeProfitRequest(limit_price=target_price)
                    )

                order = self.trading_client.submit_order(order_request)
                self._log_decision_event(
                    ticker=ticker,
                    strategy=getattr(opp, 'strategy', direction),
                    direction=direction,
                    decision="ENTRY_SUBMITTED",
                    reason="bracket_order_submitted",
                    opp=opp,
                    shares=enhanced_shares,
                    order_submitted=1,
                    order_id=str(order.id),
                    position_opened=1,
                    notes=f"status={order.status}",
                    is_pilot=is_pilot,
                    pilot_policy=pilot_policy,
                    pilot_threshold=pilot_threshold,
                    pilot_decision_reason=pilot_decision_reason or "ENTRY_SUBMITTED",
                )

                logger.warning(f"   ✅ BRACKET ORDER EXECUTED!")
                logger.warning(f"      Order ID: {order.id}")
                logger.warning(f"      Status:   {order.status}")
                logger.warning(f"      Stop:     ${stop_price:.2f}")
                logger.warning(f"      Target:   ${target_price:.2f}")
                logger.info(f"   Mode: {self.execution_mode}")

            except Exception as e:
                logger.error(f"   ❌ Order failed: {e}")
                self._log_decision_event(
                    ticker=ticker,
                    strategy=getattr(opp, 'strategy', direction),
                    direction=direction,
                    decision="EXECUTION_DENIED",
                    reason=str(e),
                    opp=opp,
                    shares=enhanced_shares,
                    execution_denied=1,
                    execution_deny_reason=str(e),
                    notes="submit_order_exception",
                    is_pilot=is_pilot,
                    pilot_policy=pilot_policy,
                    pilot_threshold=pilot_threshold,
                    pilot_decision_reason=pilot_decision_reason or str(e),
                )
                import traceback
                logger.error(traceback.format_exc())
                return False, str(e)
        else:
            logger.info(f"   📝 PAPER MODE - Trade logged (no trading client)")
            self._log_decision_event(
                ticker=ticker,
                strategy=getattr(opp, 'strategy', direction),
                direction=direction,
                decision="PAPER_ENTRY",
                reason="paper_mode",
                opp=opp,
                shares=enhanced_shares,
                position_opened=1,
                notes="paper_mode",
                is_pilot=is_pilot,
                pilot_policy=pilot_policy,
                pilot_threshold=pilot_threshold,
                pilot_decision_reason=pilot_decision_reason or "PAPER_ENTRY",
            )

        # Record execution
        trade = {
            'ticker': ticker,
            'direction': direction,
            'strategy': getattr(opp, 'strategy', 'VOYAGER' if direction == 'LONG' else 'SHORT'),
            'entry_price': opp.entry_price,
            'target_price': opp.target_price,
            'stop_loss': opp.stop_loss,
            'position_size': enhanced_shares,
            'position_value': enhanced_value,
            'confluence_type': confluence_type,
            'multiplier': multiplier,
            'combo': combo,
            'timestamp': datetime.now()
        }
        self.executed_trades.append(trade)
        self.active_positions[ticker] = {
            'ticker': ticker,
            'strategy': getattr(opp, 'strategy', direction),
            'direction': direction,
            'entry_price': opp.entry_price,
            'stop_loss': opp.stop_loss,
            'initial_stop_loss': opp.stop_loss,
            'target_price': opp.target_price,
            'shares': enhanced_shares,
            'market_value': enhanced_value,
            'sector': getattr(self.risk_overlay.get_ticker_sector(ticker), "upper", lambda: self.risk_overlay.get_ticker_sector(ticker))() if hasattr(self.risk_overlay, 'get_ticker_sector') else "UNKNOWN",
            'beta': self._get_ticker_beta(ticker),
            'is_pilot': bool(is_pilot),
            'pilot_policy': pilot_policy,
            'pilot_threshold': pilot_threshold,
        }
        self._sync_positions_to_portfolio_coordinator()
        self._record_entry_performance(
            opp,
            enhanced_shares,
            is_pilot=is_pilot,
            pilot_policy=pilot_policy,
            pilot_threshold=pilot_threshold,
        )
        self.log_trade_to_journal({
            'ticker': ticker,
            'strategy': trade['strategy'],
            'direction': direction,
            'shares': enhanced_shares,
            'entry': opp.entry_price,
            'stop': opp.stop_loss,
            'target': opp.target_price,
            'score': getattr(opp, 'score', 0),
            'grade': getattr(opp, 'grade', 'N/A'),
            'growth_mode': getattr(opp, 'growth_mode', False),
            'tier_breakdown': getattr(opp, 'tier_breakdown', {}) or {},
            'confidence_summary': getattr(opp, 'confidence_summary', {}) or {},
            'pathway_qualification': getattr(opp, 'pathway_qualification', {}) or {},
            'position_pct': (enhanced_value / self.account_size) * 100 if self.account_size else 0,
            'risk_pct': ((enhanced_shares * abs(opp.entry_price - opp.stop_loss)) / self.account_size) * 100 if self.account_size else 0,
        })
        return True, "EXECUTED"
    
    def _check_concentration(self, ticker: str, new_position_value: float) -> tuple:
        """
        Pre-trade portfolio concentration check.

        Rules (applied against self.account_size = live equity):
          - Max single ticker exposure: 20% of total equity
          - Max total gross deployed exposure: 80% of total equity

        Returns (allowed: bool, reason: str).
        """
        MAX_SINGLE_PCT = 0.20   # 20% max per ticker
        MAX_GROSS_EXPOSURE_PCT = 0.80   # 80% max total invested

        if self.account_size <= 0:
            return True, ""

        max_single_value = self.account_size * MAX_SINGLE_PCT

        # Raw value cap — catches cases where Alpaca positions can't be fetched
        if new_position_value > max_single_value:
            return False, (
                f"{ticker} new position ${new_position_value:,.0f} exceeds "
                f"single-stock cap ${max_single_value:,.0f} "
                f"({MAX_SINGLE_PCT:.0%} of ${self.account_size:,.0f})"
            )

        if self.trading_client:
            try:
                positions = self.trading_client.get_all_positions()
                total_market_value = sum(abs(float(p.market_value)) for p in positions)

                # Check total gross deployed exposure.
                gross_exposure_pct = total_market_value / self.account_size
                if gross_exposure_pct >= MAX_GROSS_EXPOSURE_PCT:
                    return False, (
                        f"gross exposure {gross_exposure_pct:.0%} already at max "
                        f"{MAX_GROSS_EXPOSURE_PCT:.0%} — no new positions"
                    )

                # Find existing exposure in this ticker
                existing_value = 0.0
                for p in positions:
                    if p.symbol == ticker:
                        existing_value = abs(float(p.market_value))
                        break

                combined_value = existing_value + new_position_value
                combined_pct = combined_value / self.account_size
                if combined_pct > MAX_SINGLE_PCT:
                    return False, (
                        f"{ticker} combined exposure would be "
                        f"${combined_value:,.0f} ({combined_pct:.0%} of equity) — "
                        f"max is {MAX_SINGLE_PCT:.0%}"
                    )

            except Exception as e:
                logger.warning(f"   ⚠️  Concentration check error ({e}) — applying raw cap only")

        return True, ""

    def run_scan_cycle(self):
        """
        Run a complete scan cycle with adaptive universes
        """
        
        logger.info("")
        logger.info("="*80)
        logger.info(f"🔍 SCAN CYCLE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("="*80)
        
        self.last_scan_time = datetime.now()
        self.scan_count += 1
        self._phase3_ticker_cache = {}
        self._write_progress_heartbeat("scan:start", force=True)

        try:
            # STEP 0: Reconcile exits — detect positions closed by Alpaca bracket orders
            # since last scan and write realized exit metadata to trades table.
            self._write_progress_heartbeat("scan:reconcile", force=True)
            self._reconcile_closed_positions()

            # STEP 0a: Gap/halts protection — force market exit on oversized loss.
            self._write_progress_heartbeat("scan:circuit_breaker")
            self._run_circuit_breaker_check()

            # STEP 0b: Trailing stop ratchet + local breach exits for winners.
            self._write_progress_heartbeat("scan:trailing_stop")
            self._run_trailing_stop_check()

            # STEP 0c: Thesis health check — update INTACT/WARN/BROKEN per open position
            self._write_progress_heartbeat("scan:thesis_health")
            self._check_open_position_theses()

            vix_level = self._get_vix()
            breadth_context = self._get_market_breadth_context()
            market_breadth_pct = self._as_float(
                (breadth_context or {}).get("market_breadth_pct"),
                None,
            )
            if market_breadth_pct is not None:
                logger.info(
                    "📊 Market breadth: composite %.1f%% (stock + sector participation)",
                    float(market_breadth_pct),
                )
            self._opening_window_block, self._opening_window_minutes = self._get_opening_window_state()
            if self._opening_window_block:
                logger.info(
                    "⏸ OPENING WINDOW ACTIVE: %.1f min since open — scanning allowed, entries blocked until 09:45 ET",
                    float(self._opening_window_minutes or 0.0),
                )

            self.portfolio.update_regime(self._build_regime_snapshot(vix_level))
            VIX_CRASH_THRESHOLD = 35.0
            if vix_level >= VIX_CRASH_THRESHOLD:
                logger.warning(
                    "   🚨 CRASH PROTOCOL ACTIVE: VIX=%.1f >= %.0f — all new entries suspended. Managing existing positions only.",
                    float(vix_level or 0.0),
                    VIX_CRASH_THRESHOLD,
                )
                self._write_heartbeat(
                    market_status="CRASH_PROTOCOL",
                    is_trading=False,
                    stage="scan:crash_protocol",
                )
                return

            # STEP 1: Build adaptive universes and detect confluence
            universe_data = self._build_adaptive_universes()
            self._current_regime_context = self._normalize_regime_context(
                self._build_decision_regime_context(
                    vix_level,
                    market_breadth_pct=market_breadth_pct,
                )
            )
            universe_data = self._apply_sniper_universe_gate(universe_data, vix_level)
            self._start_decision_run(universe_data, regime_context=self._current_regime_context)
            self._write_progress_heartbeat("scan:regime_ready", force=True)
            cycle_now_et = datetime.now(self.et_tz)
            
            # STEP 2: Run Voyager scan
            voyager_opportunities = []
            short_opportunities = []
            if self.voyager:
                if not self._should_run_strategy_scan("VOYAGER", cycle_now_et):
                    logger.info("📍 %s", self._strategy_cadence_hold_message("VOYAGER", cycle_now_et))
                else:
                    logger.info("")
                    logger.info("📍 Running Voyager scan...")
                    try:
                        self._mark_strategy_scan_started("VOYAGER", cycle_now_et)
                        setattr(self.voyager, "_progress_callback", self._make_progress_callback("voyager"))
                        self._write_progress_heartbeat("scan:voyager", force=True)
                        voyager_seed_universe = sorted(universe_data.get('voyager_universe', set()))
                        regime_status = str((self._current_regime_context or {}).get("status", "UNKNOWN") or "UNKNOWN").upper()
                        regime_volatility = str((self._current_regime_context or {}).get("volatility", "UNKNOWN") or "UNKNOWN").upper()
                        suppress_voyager_longs = self._should_suppress_voyager_long_scan(
                            regime_status,
                            regime_volatility,
                            market_breadth_pct=market_breadth_pct,
                        )
                        if suppress_voyager_longs:
                            if market_breadth_pct is not None and market_breadth_pct < 40.0:
                                logger.info(
                                    "VOYAGER long scan suppressed: composite breadth %.1f%% < 40%% — accumulation breadth too weak",
                                    float(market_breadth_pct),
                                )
                            else:
                                logger.info(
                                    "VOYAGER long scan suppressed: HIGH VIX + %s regime not suitable for accumulation entries",
                                    regime_status,
                                )
                        voyager_results = self.voyager.scan_complete(
                            raw_universe_override=voyager_seed_universe if voyager_seed_universe else None,
                            suppress_long_pipeline=suppress_voyager_longs,
                        )
                        voyager_opportunities = self._apply_voyager_crisis_accumulation_controls(
                            voyager_results.get('long_opportunities', []),
                            vix_level,
                        )
                        voyager_opportunities = self._apply_voyager_breadth_controls(
                            voyager_opportunities,
                            market_breadth_pct,
                        )
                        short_opportunities = self._apply_short_candidate_controls(
                            voyager_results.get('short_opportunities', []),
                            vix_level,
                            source_label="Voyager short candidates",
                            default_strategy="VOYAGER",
                        )

                        voyager_rejects = self._extract_voyager_long_rejects()
                        self._log_scanner_rejects(voyager_rejects, 'VOYAGER', 'LONG')
                        
                        logger.info(f"   ✅ Voyager: {len(voyager_opportunities)} long, {len(short_opportunities)} short")
                        if voyager_rejects:
                            logger.info(f"   🧾 Voyager rejects logged: {len(voyager_rejects)}")
                        self._write_progress_heartbeat("scan:voyager:done", force=True)
                        
                        # Log opportunities with confluence info
                        if universe_data['confluence']:
                            triple_tickers = {opp.ticker for opp in universe_data['confluence'].get('triple', [])}
                            double_confluence = universe_data['confluence'].get('double', {})
                            
                            for opp in voyager_opportunities:
                                if opp.ticker in triple_tickers:
                                    logger.warning(f"      💎 {opp.ticker} - TRIPLE CONFLUENCE - Score {opp.score}, R/R {opp.risk_reward:.1f}:1")
                                else:
                                    # Check for double confluence
                                    in_double = False
                                    for combo, opps in double_confluence.items():
                                        if any(o.ticker == opp.ticker for o in opps):
                                            logger.info(f"      🔗 {opp.ticker} - Double ({combo}) - Score {opp.score}, R/R {opp.risk_reward:.1f}:1")
                                            in_double = True
                                            break
                                    
                                    if not in_double:
                                        logger.info(f"      📊 {opp.ticker} - Score {opp.score}, R/R {opp.risk_reward:.1f}:1")
                        
                    except Exception as e:
                        logger.error(f"   ❌ Voyager scan error: {e}")
                        import traceback
                        logger.error(traceback.format_exc())

            # STEP 2b: Run dedicated Sniper scan (VIX-gated, momentum breakouts)
            sniper_opportunities = []
            sniper_universe = universe_data.get('sniper_universe', set())
            if self.sniper_scanner and sniper_universe:
                if not self._should_run_strategy_scan("SNIPER", cycle_now_et):
                    logger.info("🎯 %s", self._strategy_cadence_hold_message("SNIPER", cycle_now_et))
                else:
                    logger.info("")
                    logger.info("🎯 Running Sniper scan (momentum breakouts)...")
                    try:
                        self._mark_strategy_scan_started("SNIPER", cycle_now_et)
                        setattr(self.sniper_scanner, "_progress_callback", self._make_progress_callback("sniper"))
                        self._write_progress_heartbeat("scan:sniper", force=True)
                        sniper_results = self.sniper_scanner.scan_universe(list(sniper_universe))
                        sniper_opportunities = sniper_results
                        self._log_scanner_rejects(
                            getattr(self.sniper_scanner, '_scan_rejects', []), 'SNIPER', 'LONG'
                        )
                        logger.info(f"   ✅ Sniper: {len(sniper_opportunities)} breakout setups")
                        for opp in sniper_opportunities:
                            logger.info(
                                f"      {opp['ticker']} - Score {opp['score']}, "
                                f"R/R {opp['risk_reward']:.1f}:1, "
                                f"ATR contraction {opp['metrics']['atr_contraction_ratio']:.2f}x"
                            )
                        self._write_progress_heartbeat("scan:sniper:done", force=True)
                    except Exception as e:
                        logger.error(f"   ❌ Sniper scan error: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
            elif not sniper_universe:
                logger.info("🎯 Sniper: no universe (regime-gated or universe empty)")

            # STEP 2c: Run dedicated Remora scan (event/catalyst opportunities)
            # Gated to 9:30 AM–2:30 PM ET — catalysts can emerge beyond the first hour,
            # but we still avoid the late-day noise window.
            remora_opportunities = []
            remora_universe = universe_data.get('remora_universe', set())
            if self.remora_scanner and remora_universe:
                if not self._is_remora_open_window(cycle_now_et):
                    import datetime as _dt
                    _now_et = cycle_now_et or _dt.datetime.now(self.et_tz)
                    logger.info(
                        f"⚡ Remora: skipped — outside open window "
                        f"({_now_et.strftime('%H:%M')} ET, window is 09:30–14:30). "
                        f"Set REMORA_OPEN_WINDOW_ONLY=0 to disable gate."
                    )
                elif not self._should_run_strategy_scan("REMORA", cycle_now_et):
                    logger.info("⚡ %s", self._strategy_cadence_hold_message("REMORA", cycle_now_et))
                else:
                    logger.info("")
                    logger.info("⚡ Running Remora scan (catalyst moves)...")
                    try:
                        self._mark_strategy_scan_started("REMORA", cycle_now_et)
                        setattr(self.remora_scanner, "_progress_callback", self._make_progress_callback("remora"))
                        self._write_progress_heartbeat("scan:remora", force=True)
                        remora_results = self.remora_scanner.scan_for_catalysts(list(remora_universe))
                        remora_opportunities = remora_results
                        self._log_scanner_rejects(
                            getattr(self.remora_scanner, '_scan_rejects', []), 'REMORA', 'LONG'
                        )
                        logger.info(f"   ✅ Remora: {len(remora_opportunities)} catalyst setups")
                        for opp in remora_opportunities:
                            logger.info(
                                f"      {opp['ticker']} - Score {opp['score']:.1f}, "
                                f"entry ${opp['entry']:.2f}, target ${opp['target']:.2f}"
                            )
                        self._write_progress_heartbeat("scan:remora:done", force=True)
                    except Exception as e:
                        logger.error(f"   ❌ Remora scan error: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
            elif not remora_universe:
                logger.info("⚡ Remora: no universe (universe empty)")

            # STEP 2d: Run dedicated Short scan (bearish deterioration)
            dedicated_short_opportunities = []
            short_universe = universe_data.get('short_universe', set())
            if self.short_scanner and short_universe:
                if not self._should_run_strategy_scan("SHORT", cycle_now_et):
                    logger.info("📉 %s", self._strategy_cadence_hold_message("SHORT", cycle_now_et))
                else:
                    logger.info("")
                    logger.info("📉 Running dedicated Short scan (deterioration)...")
                    try:
                        self._mark_strategy_scan_started("SHORT", cycle_now_et)
                        setattr(self.short_scanner, "_progress_callback", self._make_progress_callback("short"))
                        self._write_progress_heartbeat("scan:short", force=True)
                        if self._is_short_regime_paused(vix_level):
                            logger.info(
                                "   ⚠️  Short scanner PAUSED: VIX=%.1f >= 28.0 (panic regime — squeeze risk too high for new short entries)",
                                float(vix_level or 0.0),
                            )
                            dedicated_short_opportunities = []
                        else:
                            short_results = self.short_scanner.scan_for_shorts(list(short_universe))
                            self._log_scanner_rejects(
                                getattr(self.short_scanner, '_scan_rejects', []), 'SHORT', 'SHORT'
                            )
                            dedicated_short_opportunities = self._apply_short_candidate_controls(
                                short_results,
                                vix_level,
                                source_label="Short scanner",
                                default_strategy="SHORT",
                            )
                            logger.info(f"   ✅ Short: {len(dedicated_short_opportunities)} dedicated short setups")
                            for opp in dedicated_short_opportunities:
                                logger.info(
                                    f"      {opp.ticker} - Score {float(getattr(opp, 'score', 0) or 0):.1f}, "
                                    f"entry ${float(getattr(opp, 'entry_price', 0) or 0):.2f}, "
                                    f"target ${float(getattr(opp, 'target_price', 0) or 0):.2f}"
                                )
                        self._write_progress_heartbeat("scan:short:done", force=True)
                    except Exception as e:
                        logger.error(f"   ❌ Short scan error: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
            elif not short_universe:
                logger.info("📉 Short: no universe (universe empty)")

            # STEP 2e: Contrarian scan (regime-gated fear/correction strategy)
            contrarian_opp_objects = []
            regime_overall = str((self._current_regime_context or {}).get("status", "UNKNOWN") or "UNKNOWN").upper()
            regime_vix = str((self._current_regime_context or {}).get("volatility", "UNKNOWN") or "UNKNOWN").upper()
            contrarian_regime_ok = self._is_contrarian_regime_eligible(regime_overall, regime_vix)
            if not self.contrarian_scanner:
                if not self._contrarian_import_warned:
                    logger.warning("⚠️  [CONTRARIAN] Scanner unavailable — import failed, skipping scan")
                    self._contrarian_import_warned = True
            elif not contrarian_regime_ok:
                logger.info(
                    f"[CONTRARIAN] Regime not eligible — skipping scan "
                    f"(regime={regime_overall}, vix={regime_vix})"
                )
            else:
                contrarian_universe = list(universe_data.get('contrarian_universe', set()))
                if not contrarian_universe:
                    logger.info("📉 Contrarian: no universe (universe empty)")
                elif not self._should_run_strategy_scan("CONTRARIAN", cycle_now_et):
                    logger.info("[CONTRARIAN] %s", self._strategy_cadence_hold_message("CONTRARIAN", cycle_now_et))
                else:
                    logger.info("")
                    logger.info(
                        f"📉 Running Contrarian scan (regime={regime_overall}, "
                        f"vix={regime_vix}, VIX={vix_level:.1f})..."
                    )
                    try:
                        self._mark_strategy_scan_started("CONTRARIAN", cycle_now_et)
                        setattr(self.contrarian_scanner, "_progress_callback", self._make_progress_callback("contrarian"))
                        self._write_progress_heartbeat("scan:contrarian", force=True)
                        contrarian_results = self.contrarian_scanner.scan(contrarian_universe, vix_level)
                        self._log_scanner_rejects(
                            getattr(self.contrarian_scanner, '_scan_rejects', []), 'CONTRARIAN', 'LONG'
                        )
                        for o in contrarian_results:
                            sig = o['signal']
                            risk = sig['entry_price'] - sig['stop_loss']
                            if risk > 0 and self.account_size > 0:
                                max_risk_shares = int((self.account_size * 0.005) / risk)
                                max_size_shares = int((self.account_size * 0.15 * o['size_mult']) / sig['entry_price'])
                                shares = max(1, min(max_risk_shares, max_size_shares))
                            else:
                                shares = 1
                            expected_ret = ((sig['target_price'] - sig['entry_price']) / sig['entry_price']) * 100
                            contrarian_opp_objects.append(_SniperOpp(
                                ticker=o['ticker'],
                                direction='LONG',
                                entry_price=sig['entry_price'],
                                target_price=sig['target_price'],
                                stop_loss=sig['stop_loss'],
                                score=int(o['quality_score']),
                                risk_reward=sig['risk_reward_ratio'],
                                shares=shares,
                                expected_return=expected_ret,
                                strategy='CONTRARIAN',
                            ))
                            logger.info(
                                f"      {o['ticker']} - Score {o['quality_score']:.0f}, "
                                f"R/R {sig['risk_reward_ratio']:.1f}:1, "
                                f"entry=${sig['entry_price']:.2f}"
                            )
                        logger.info(f"   ✅ Contrarian: {len(contrarian_opp_objects)} setups")
                        status = getattr(self.contrarian_scanner, "_cycle_status", {}) or {}
                        if status and status.get("state") != "SCANNED":
                            logger.info(
                                "[CONTRARIAN] standby reason=%s context=%s",
                                status.get("reason"),
                                (status.get("washout_context") or {}).get("reason")
                                or (status.get("washout_context") or {}),
                            )
                        self._write_progress_heartbeat("scan:contrarian:done", force=True)
                    except Exception as e:
                        logger.error(f"   ❌ Contrarian scan error: {e}")
                        import traceback
                        logger.error(traceback.format_exc())

            # PILOT OBSERVE BLOCK (Phase 4.3) — paper analysis only unless PILOT_EXECUTE_PAPER=1
            _pilot_candidates = []
            try:
                from pilot_policy_controller import PilotPolicyController as _PilotCtrl
                _pilot_ctrl = _PilotCtrl(execution_mode=self.execution_mode, db_path=self.db_path)
                if _pilot_ctrl.is_pilot_enabled():
                    self._write_progress_heartbeat("scan:pilot_observe")
                    _pilot_candidates = _pilot_ctrl.get_pilot_candidates(
                        reject_rows=self._collect_all_scan_rejects(),
                        existing_pilot_count=self._count_open_pilot_positions(),
                        run_id=self.current_run_id or self.run_id,
                    )
                    logger.info(f"[PILOT] {_pilot_ctrl.get_observe_summary(_pilot_candidates)}")
            except ImportError:
                pass
            except Exception as _pilot_exc:
                logger.debug(f"[PILOT] observe block error (non-fatal): {_pilot_exc}")

            # STEP 3: Execute high-conviction trades
            # Convert Sniper dict results to execution-compatible objects
            sniper_opp_objects = []
            for opp in sniper_opportunities:
                normalized = self._normalize_opportunity(opp, 'LONG', 'SNIPER')
                if normalized is not None:
                    sniper_opp_objects.append(normalized)

            remora_opp_objects = []
            for opp in remora_opportunities:
                normalized = self._normalize_opportunity(opp, 'LONG', 'REMORA')
                if normalized is not None:
                    remora_opp_objects.append(normalized)

            short_opportunities = self._merge_unique_opportunities(
                short_opportunities,
                dedicated_short_opportunities,
                'SHORT',
                'SHORT',
            )

            all_long_opps = voyager_opportunities + sniper_opp_objects + remora_opp_objects + contrarian_opp_objects
            all_long_opps, short_opportunities = self._apply_machine_ranking(
                all_long_opps,
                short_opportunities,
            )
            signal_confluence = self._build_signal_confluence(
                voyager_opportunities=voyager_opportunities,
                sniper_opportunities=sniper_opp_objects,
                remora_opportunities=remora_opp_objects,
            )

            if signal_confluence:
                triple_count = int(signal_confluence.get('stats', {}).get('triple_count', 0) or 0)
                if triple_count > 0:
                    logger.warning("="*80)
                    logger.warning(f"💎 TRIPLE CONFLUENCE DETECTED: {triple_count} stocks!")
                    logger.warning("="*80)
                    for opp in signal_confluence.get('triple', [])[:15]:
                        direction = getattr(opp, 'direction', 'LONG')
                        conviction = getattr(opp, 'conviction_score', 0)
                        logger.warning(
                            f"   ⭐⭐⭐ {opp.ticker} - MAXIMUM CONVICTION [{direction}] "
                            f"- Conviction {conviction}/100"
                        )
                    logger.warning("="*80)
                else:
                    logger.info("   ℹ️  No signal-level triple confluence detected")

            if all_long_opps or short_opportunities:
                # First, execute triple confluence if any (Voyager only)
                if signal_confluence:
                    self._execute_triple_confluence_trades(
                        voyager_opportunities,
                        signal_confluence
                    )

                # THEN, execute ALL other approved trades (Voyager longs, Sniper longs, shorts)
                self._execute_all_approved_trades(
                    all_long_opps,
                    short_opportunities,
                    signal_confluence
                )
                self._write_progress_heartbeat("scan:execution", force=True)

            # Phase 4.4: optional controlled pilot execution (paper only).
            # No effect unless PILOT_ENABLE=1 and PILOT_EXECUTE_PAPER=1.
            self._execute_pilot_candidates(_pilot_candidates)
            self._write_progress_heartbeat("scan:pilot_execute")

            if self._options_master is not None:
                try:
                    setattr(self._options_master, "_progress_callback", self._make_progress_callback("options"))
                    self._write_progress_heartbeat("scan:options", force=True)
                    approved_for_options = all_long_opps + short_opportunities
                    options_summary = self._options_master.run_options_cycle(
                        regime_snapshot=self._current_regime_context,
                        approved_opportunities=approved_for_options,
                        reject_rows=self._collect_all_scan_rejects(),
                        equity_positions=getattr(self, 'active_positions', {}),
                        run_id=self.current_run_id or self.run_id,
                    )
                    if any(options_summary.values()):
                        logger.info(
                            "[OPTIONS] managed=%s closed=%s opened=%s skipped=%s eligible=%s errors=%s",
                            options_summary.get("managed", 0),
                            options_summary.get("closed", 0),
                            options_summary.get("opened", 0),
                            options_summary.get("skipped", 0),
                            options_summary.get("eligible_underlyings", 0),
                            options_summary.get("errors", 0),
                        )
                    self._write_progress_heartbeat("scan:options:done", force=True)
                except Exception as options_exc:
                    logger.debug(f"[OPTIONS] cycle error (non-fatal): {options_exc}")
            
            logger.info("")
            logger.info("="*80)
            logger.info("✅ Scan cycle complete")
            logger.info("="*80)
            logger.info("")
            self._write_heartbeat(
                market_status="OPEN" if self.is_market_hours() else "CLOSED",
                is_trading=True,
                stage="scan:complete",
            )
            
        except Exception as e:
            logger.error(f"❌ Error in scan cycle: {e}")
            import traceback
            logger.error(traceback.format_exc())
        finally:
            self._finalize_decision_run()
    
    def run_daemon(self):
        """
        Run as daemon - continuous operation during market hours
        """
        
        logger.info("")
        logger.info("="*80)
        logger.info("🤖 DAEMON MODE - STARTING")
        logger.info("="*80)
        logger.info(f"⏱️  Daemon loop interval: {self.scan_interval_minutes} minutes")
        logger.info(
            "⏱️  Strategy cadences: Voyager=%sm Sniper=%sm Remora=%sm Short=%sm Contrarian=%sm",
            self._get_strategy_scan_cadence_minutes("VOYAGER"),
            self._get_strategy_scan_cadence_minutes("SNIPER"),
            self._get_strategy_scan_cadence_minutes("REMORA"),
            self._get_strategy_scan_cadence_minutes("SHORT"),
            self._get_strategy_scan_cadence_minutes("CONTRARIAN"),
        )
        logger.info(f"🕐 Market hours: {self.market_open.strftime('%H:%M')} - {self.market_close.strftime('%H:%M')} ET")
        logger.info("="*80)
        logger.info("")
        
        self.running = True
        
        # Set up signal handlers
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal")
            self.shutdown()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Main loop
        while self.running:
            try:
                now_et = datetime.now(self.et_tz)
                
                # Check if market hours
                if self.is_market_hours():
                    self._write_heartbeat(market_status="OPEN", is_trading=True)
                    logger.info(f"✅ Market is OPEN - Running scan")
                    self.run_scan_cycle()

                    # Keep-alive sleep: write heartbeat every 60s so the dashboard
                    # never shows DEAD during the inter-scan wait.
                    # Also reconcile closed positions every 5 min so realized
                    # analytics lag at most ~5 min rather than a full scan interval.
                    _RECONCILE_INTERVAL = 300  # seconds
                    target_seconds = self.scan_interval_minutes * 60
                    elapsed = 0
                    logger.info(f"⏱️  Next scan in {self.scan_interval_minutes} minutes...")
                    while elapsed < target_seconds and self.running:
                        chunk = min(60, target_seconds - elapsed)
                        time.sleep(chunk)
                        elapsed += chunk
                        self._write_heartbeat(market_status="OPEN", is_trading=True)
                        self._run_trailing_stop_check()
                        self._run_circuit_breaker_check()
                        if time.time() - self._last_reconcile_ts >= _RECONCILE_INTERVAL:
                            self._reconcile_closed_positions()

                else:
                    # Outside market hours
                    current_time = now_et.time()

                    if current_time < self.market_open:
                        wait_minutes = 30
                        self._write_heartbeat(market_status="PRE_MARKET", is_trading=False)
                        logger.info(f"⏰ Pre-market: Market opens at {self.market_open.strftime('%H:%M')} ET")
                        logger.info(f"   Checking again in {wait_minutes} minutes...")
                    else:
                        wait_minutes = 60
                        self._write_heartbeat(market_status="CLOSED", is_trading=False)
                        logger.info(f"🌙 After hours: Market closed")
                        logger.info(f"   Next check in {wait_minutes} minutes...")

                    # Keep-alive sleep for off-hours (60s chunks, heartbeat each iteration)
                    target_seconds = wait_minutes * 60
                    elapsed = 0
                    while elapsed < target_seconds and self.running:
                        chunk = min(60, target_seconds - elapsed)
                        time.sleep(chunk)
                        elapsed += chunk
                        off_status = "PRE_MARKET" if current_time < self.market_open else "CLOSED"
                        self._write_heartbeat(market_status=off_status, is_trading=False)
            
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received")
                self.shutdown()
                break
            
            except Exception as e:
                logger.error(f"Error in daemon loop: {e}")
                import traceback
                logger.error(traceback.format_exc())
                logger.info("Waiting 5 minutes before retry...")
                time.sleep(300)
    
    def shutdown(self):
        """Graceful shutdown"""
        self._write_heartbeat(market_status="STOPPED", is_trading=False)
        
        logger.info("")
        logger.info("="*80)
        logger.info("🛑 SHUTTING DOWN")
        logger.info("="*80)
        
        self.running = False
        
        # Log summary
        logger.info(f"📊 Session summary:")
        logger.info(f"   Total trades executed: {len(self.executed_trades)}")
        
        triple_trades = []
        for t in self.executed_trades:
            if isinstance(t, dict):
                ctype = str(t.get('confluence_type', ''))
                if 'TRIPLE' in ctype:
                    triple_trades.append(t)
            else:
                if getattr(t, 'confluence_level', 0) == 3:
                    triple_trades.append(t)
        if triple_trades:
            logger.info(f"   Triple confluence trades: {len(triple_trades)}")
            for trade in triple_trades:
                if isinstance(trade, dict):
                    logger.info(f"      {trade.get('ticker')} @ ${trade.get('entry_price', 0):.2f}")
                else:
                    logger.info(f"      {trade.ticker} @ ${trade.entry_price:.2f}")
        
        logger.info("")
        logger.info("✅ Shutdown complete")
        logger.info("="*80)
        logger.info("")


def main():
    """Main entry point"""
    raw_scan_interval = os.getenv("SCAN_INTERVAL_MINUTES", "5")
    try:
        scan_interval_minutes = max(1, int(float(raw_scan_interval)))
    except (TypeError, ValueError):
        scan_interval_minutes = 5
    
    # Check for test mode
    test_mode = '--test' in sys.argv
    
    if test_mode:
        logger.info("🧪 TEST MODE - Running single scan cycle")
        
        # account_size is fallback; live equity is fetched from Alpaca at init
        trader = UnifiedMasterTraderV3(
            account_size=100000,
            execution_mode='PAPER',
            enable_voyager=True,
            enable_adaptive_universes=True,
            scan_interval_minutes=scan_interval_minutes,
            verbose=True
        )

        trader.run_scan_cycle()
        
        logger.info("✅ Test complete")
        return
    
    # Production daemon mode
    # account_size is the fallback if Alpaca equity can't be fetched;
    # actual live equity is read from the broker at startup.
    trader = UnifiedMasterTraderV3(
        account_size=100000,
        execution_mode='PAPER',  # Change to 'LIVE' when ready
        enable_voyager=True,
        enable_adaptive_universes=True,
        scan_interval_minutes=scan_interval_minutes,
        verbose=True
    )
    
    trader.run_daemon()


if __name__ == "__main__":
    main()
