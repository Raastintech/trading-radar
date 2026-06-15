"""
VOYAGER PRODUCTION v2.0 COMPLETE - Institutional Intelligence Scanner

The OUTLIER system for 10-20x opportunities.

PHILOSOPHY:
- Follow institutions when they're right (ALIGNED)
- Exploit institutional flaws when visible (EXPLOITING)
- Be BOLD when system confirms perfect setups
- Safe base, aggressive peaks

ARCHITECTURE:
Layer 1: Universe Validation
Layer 2: Data Integrity Gate
Layer 3: Regime Analysis
Layer 4: Discovery (LONG + SHORT)
Layer 5: Scoring (0-100 scale)
Layer 6: Risk Validation
Layer 7: Reporting

NO FAKE SIGNALS. NO MANIPULATION. REAL EDGE.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
import os
from alpaca_data import AlpacaDataFeed
import statistics
import logging
from adaptive_analysis import (
    AdaptiveDataAnalyzer, AdaptiveRequirements, DataSufficiency,
    create_adaptive_analyzer, get_adaptive_requirements
)
from voyager_adaptive_universe import VoyagerAdaptiveUniverse
try:
    from fundamental_data_fetcher import FundamentalDataFetcher
    HAS_FUNDAMENTAL_FETCHER = True
except ImportError:
    FundamentalDataFetcher = None
    HAS_FUNDAMENTAL_FETCHER = False
try:
    from enhanced_strategy_scoring import (
        apply_post_score_adjustment,
        calculate_strategy_score,
        calculate_position_size as enhanced_position_size,
    )
    from strategy_check_mapper import StrategyCheckMapper
    HAS_ENHANCED_SCORING = True
except ImportError as e:
    apply_post_score_adjustment = None
    calculate_strategy_score = None
    enhanced_position_size = None
    StrategyCheckMapper = None
    HAS_ENHANCED_SCORING = False
    _ENHANCED_SCORING_IMPORT_ERROR = e
else:
    _ENHANCED_SCORING_IMPORT_ERROR = None

try:
    from options_intelligence import get_options_score_adj as _get_options_score_adj
    _HAS_OPTIONS_SIGNAL = True
except ImportError:
    _get_options_score_adj = None
    _HAS_OPTIONS_SIGNAL = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

if HAS_ENHANCED_SCORING:
    logger.info("✅ Enhanced weighted scoring enabled")
else:
    logger.warning(f"⚠️  Enhanced scoring not available: {_ENHANCED_SCORING_IMPORT_ERROR}")

if HAS_FUNDAMENTAL_FETCHER:
    logger.info("✅ Fundamental data fetcher enabled (yfinance)")
else:
    logger.warning("⚠️ Fundamental data fetcher not available")


def _safe_position_size(
    account_equity: float,
    entry_price: float,
    stop_price: float,
    score_result: Dict,
) -> Dict:
    """Use enhanced position sizing when available, otherwise a simple legacy fallback."""
    if HAS_ENHANCED_SCORING and enhanced_position_size is not None:
        return enhanced_position_size(
            account_equity=account_equity,
            entry_price=entry_price,
            stop_price=stop_price,
            score_result=score_result,
        )

    risk_per_share = abs(entry_price - stop_price)
    if account_equity <= 0 or entry_price <= 0 or risk_per_share <= 0:
        return {'valid': False, 'error': 'invalid_inputs', 'shares': 0}

    max_risk_dollars = account_equity * 0.0075
    max_position_dollars = account_equity * 0.15
    shares_by_risk = int(max_risk_dollars / risk_per_share)
    shares_by_position = int(max_position_dollars / entry_price)
    shares = min(shares_by_risk, shares_by_position)
    if shares <= 0:
        return {'valid': False, 'error': 'calculated_shares_le_zero', 'shares': 0}

    position_value = shares * entry_price
    risk_at_stop = shares * risk_per_share
    return {
        'valid': True,
        'shares': shares,
        'position_value': position_value,
        'position_pct': (position_value / account_equity) * 100,
        'risk_at_stop': risk_at_stop,
        'risk_pct': (risk_at_stop / account_equity) * 100,
        'growth_mode': False,
    }


# ============================================================================
# DATA MODELS
# ============================================================================

class DataStatus(Enum):
    """Data quality status"""
    OK = "OK"
    NO_DATA = "NO_DATA"
    INSUFFICIENT_BARS = "INSUFFICIENT_BARS"
    STALE = "STALE"
    API_ERROR = "API_ERROR"


class VIXRegime(Enum):
    """VIX regime classification"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXTREME = "EXTREME"


class ExecutionMode(Enum):
    """Execution mode"""
    LIVE = "LIVE"
    SIMULATED = "SIMULATED"
    WEEKEND = "WEEKEND"
    BACKTEST = "BACKTEST"


class OpportunityMode(Enum):
    """Opportunity type"""
    ALIGNED = "ALIGNED"           # Follow institutions
    EXPLOITING = "EXPLOITING"     # Exploit institutional flaws
    ULTIMATE = "ULTIMATE"         # Perfect setup (10-20x potential)


@dataclass
class TickerDataHealth:
    """Data health for single ticker"""
    ticker: str
    status: DataStatus
    bars_count: int
    last_bar_date: Optional[str]
    is_tradable: bool
    failure_reason: Optional[str]
    data_quality_score: float


@dataclass
class RegimeContext:
    """Market regime with properly separated risk concepts"""
    vix: float
    vix_regime: VIXRegime
    vix_source: str
    vix_confidence: str
    recommendation: str
    
    # Scoring thresholds
    min_score_long: int
    min_score_short: int
    
    # Risk per position (at stop)
    max_risk_per_long_pct: float
    max_risk_per_short_pct: float
    
    # Exposure caps (notional)
    max_long_exposure_per_position_pct: float
    max_short_exposure_per_position_pct: float
    
    # Total book caps
    max_total_long_exposure_pct: float
    max_total_short_exposure_pct: float
    max_total_long_risk_pct: float
    max_total_short_risk_pct: float


@dataclass
class FunnelStats:
    """Track where opportunities get filtered"""
    stage: str
    input_count: int
    output_count: int
    rejected_count: int
    rejection_reasons: Dict[str, int] = field(default_factory=dict)
    
    @property
    def pass_rate(self) -> float:
        if self.input_count == 0:
            return 0.0
        return (self.output_count / self.input_count) * 100


@dataclass
class Opportunity:
    """Complete opportunity data"""
    ticker: str
    direction: str  # "LONG" or "SHORT"
    mode: OpportunityMode
    score: int
    
    # Entry/exit
    entry_price: float
    target_price: float
    stop_loss: float
    
    # Position sizing
    shares: int
    position_value: float
    risk_amount: float
    risk_pct: float
    exposure_pct: float
    
    # Returns
    target_return_pct: float
    risk_reward: float
    
    # Supporting data
    reasons: List[str]
    metrics: Dict

    # Risk status
    execution_status: str  # "APPROVED" or "SIMULATED"

    strategy: str = "VOYAGER"
    grade: str = "N/A"
    growth_mode: bool = False
    tier_breakdown: Dict = field(default_factory=dict)
    confidence_summary: Dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


# ============================================================================
# LAYER 1: UNIVERSE VALIDATION (from foundation)
# ============================================================================

class UniverseValidator:
    """Pre-filters universe for tradability"""
    
    MIN_PRICE = 5.0
    MIN_AVG_VOLUME = 100000
    MIN_DAILY_DOLLAR_VOLUME = 1_000_000
    
    def __init__(self, data_feed):
        self.data_feed = data_feed
        self.dead_tickers = set(['SQ', 'FSR', 'SPLK'])
        logger.info("Universe Validator initialized")
    
    def validate_universe(self, raw_universe: Set[str]) -> Tuple[Set[str], Dict]:
        """Validate universe before analysis"""
        
        logger.info(f"Validating {len(raw_universe)} tickers...")
        
        clean_universe = set()
        stats = {
            'total_input': len(raw_universe),
            'dead_tickers_removed': 0,
            'low_liquidity_removed': 0,
            'api_error_removed': 0,
            'passed': 0
        }
        
        for ticker in raw_universe:
            if ticker in self.dead_tickers:
                stats['dead_tickers_removed'] += 1
                continue
            
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=5, adjustment="all")
                if not bars or len(bars) < 2:
                    stats['api_error_removed'] += 1
                    self.dead_tickers.add(ticker)
                    continue
                
                price = bars[-1]['close']
                volume = bars[-1]['volume']
                
                if price < self.MIN_PRICE or volume < self.MIN_AVG_VOLUME:
                    stats['low_liquidity_removed'] += 1
                    continue
                
                if price * volume < self.MIN_DAILY_DOLLAR_VOLUME:
                    stats['low_liquidity_removed'] += 1
                    continue
                
                clean_universe.add(ticker)
                stats['passed'] += 1
            
            except:
                stats['api_error_removed'] += 1
                self.dead_tickers.add(ticker)
        
        logger.info(f"✅ Universe validation: {stats['passed']}/{stats['total_input']} passed")
        return clean_universe, stats


# ============================================================================
# LAYER 2: DATA INTEGRITY GATE (from foundation)
# ============================================================================

class DataIntegrityGate:
    """Validates data quality before analysis"""
    
    MIN_BARS_LIVE = 200
    MIN_BARS_SIMULATED = 150
    MAX_STALE_DAYS = 7
    
    def __init__(self, data_feed, execution_mode=None):
        self.data_feed = data_feed
        self.execution_mode = execution_mode
        self.min_bars_required = self.MIN_BARS_SIMULATED  # 150 for all modes
        # Data integrity defaults (conservative for daily-bar tradability checks)
        self.max_stale_days = getattr(self, "max_stale_days", 2)
        self.max_gap_days = getattr(self, "max_gap_days", 7)
        self.max_gap_ratio = getattr(self, "max_gap_ratio", 0.08)
        self.gap_check_window = getattr(self, "gap_check_window", 90)
        
        logger.info(f"Data Integrity Gate initialized (min bars: {self.min_bars_required})")
    
    def validate_batch(self, tickers: List[str]) -> Dict[str, TickerDataHealth]:
        """Validate batch of tickers"""
        
        logger.info(f"Running Data Integrity Gate on {len(tickers)} tickers...")
        if not tickers:
            logger.warning("Data Integrity Gate received empty ticker batch")
            return {}
        
        health_reports = {}
        
        for ticker in tickers:
            health = self._validate_ticker(ticker)
            health_reports[ticker] = health
            
            if health.status != DataStatus.OK:
                logger.warning(f"{ticker}: {health.status.value} - {health.failure_reason}")
        
        passed = sum(1 for h in health_reports.values() if h.status == DataStatus.OK)
        total = len(health_reports)
        pass_rate = (passed / total * 100) if total else 0.0
        logger.info(f"✅ Data Integrity Gate: {passed}/{total} passed ({pass_rate:.1f}%)")
        
        return health_reports
    
    def _parse_ts(self, ts):
        """Parse bar timestamp to aware datetime (UTC). Returns None if invalid."""
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(ts, str):
            s = ts.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                dt = datetime.fromisoformat(s)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                return None
        return None

    def _validate_ticker(self, ticker: str) -> TickerDataHealth:
        """Validate single ticker (count + staleness + gap density)."""

        try:
            bars = self.data_feed.get_daily_bars(ticker, days_back=252, adjustment="all")

            if not bars:
                return TickerDataHealth(ticker, DataStatus.NO_DATA, 0, None, False, "NO_DATA", 0)

            bars_count = len(bars)
            last_ts_raw = bars[-1].get("timestamp")
            last_ts = self._parse_ts(last_ts_raw)

            if bars_count < self.min_bars_required:
                return TickerDataHealth(
                    ticker, DataStatus.INSUFFICIENT_BARS, bars_count,
                    last_ts_raw, False,
                    f"INSUFFICIENT_BARS_{bars_count}_LT_{self.min_bars_required}", 30
                )

            if last_ts is None:
                return TickerDataHealth(
                    ticker, DataStatus.API_ERROR, bars_count,
                    last_ts_raw, False,
                    "TIMESTAMP_PARSE_FAILED", 0
                )

            now_utc = datetime.now(timezone.utc)
            age_days = (now_utc - last_ts).total_seconds() / 86400.0
            max_days = float(getattr(self, "max_stale_days", 2)) + 2.0
            if age_days > max_days:
                return TickerDataHealth(
                    ticker, DataStatus.STALE, bars_count,
                    last_ts_raw, False,
                    f"DATA_STALE_{age_days:.1f}D_GT_{max_days:.1f}D", 0
                )

            window = int(getattr(self, "gap_check_window", 90))
            recent = bars[-window:] if bars_count >= window else bars

            dts = [self._parse_ts(b.get("timestamp")) for b in recent]
            if any(dt is None for dt in dts):
                return TickerDataHealth(
                    ticker, DataStatus.API_ERROR, bars_count,
                    last_ts_raw, False,
                    "RECENT_TIMESTAMP_PARSE_FAILED", 0
                )

            max_gap_days = float(getattr(self, "max_gap_days", 7))
            gap_events = 0
            missing_days_est = 0

            for prev, cur in zip(dts[:-1], dts[1:]):
                delta_days = (cur - prev).total_seconds() / 86400.0
                if delta_days > max_gap_days:
                    gap_events += 1
                if delta_days > 4.0:
                    missing_days_est += max(0, int(round(delta_days)) - 1)

            gap_ratio = missing_days_est / max(1, len(dts))

            max_gap_ratio = float(getattr(self, "max_gap_ratio", 0.08))
            if gap_events > 0 or gap_ratio > max_gap_ratio:
                return TickerDataHealth(
                    ticker, DataStatus.API_ERROR, bars_count,
                    last_ts_raw, False,
                    f"GAP_DENSITY_HIGH_events={gap_events}_ratio={gap_ratio:.2f}", 0
                )

            base_score = min(100, 50 + (bars_count / self.min_bars_required) * 50)
            freshness_penalty = min(20, age_days * 5)
            quality_score = max(0, int(base_score - freshness_penalty))

            return TickerDataHealth(
                ticker, DataStatus.OK, bars_count,
                last_ts_raw, True, None, quality_score
            )

        except Exception as e:
            return TickerDataHealth(ticker, DataStatus.API_ERROR, 0, None, False, f"API_ERROR_{e}", 0)


# ============================================================================
# LAYER 3: REGIME ANALYZER (from foundation, with VXX fix)
# ============================================================================

class RegimeAnalyzer:
    """Analyzes market regime and adjusts parameters"""
    
    VIX_LOW = 15
    VIX_MEDIUM = 22
    VIX_HIGH = 30
    
    def __init__(self, data_feed):
        self.data_feed = data_feed
        logger.info("Regime Analyzer initialized")
    
    def analyze_regime(self) -> RegimeContext:
        """Analyze regime with RETAIL-APPROPRIATE parameters"""
        
        vix, vix_source, vix_confidence = self._get_vix_with_source()
        
        if vix < self.VIX_LOW:
            vix_regime = VIXRegime.LOW
        elif vix < self.VIX_MEDIUM:
            vix_regime = VIXRegime.MEDIUM
        elif vix < self.VIX_HIGH:
            vix_regime = VIXRegime.HIGH
        else:
            vix_regime = VIXRegime.EXTREME
        
        # Regime-adjusted parameters
        if vix_regime == VIXRegime.LOW and vix_source == "LIVE":
            recommendation = "FAVORABLE"
            min_score_long, min_score_short = 60, 65
            # RISK per position (STRICT)
            max_risk_long, max_risk_short = 0.0150, 0.0075
            # EXPOSURE per position (RETAIL-AGGRESSIVE)
            max_long_exp, max_short_exp = 0.25, 0.20
            # TOTAL BOOK limits (RETAIL-AGGRESSIVE)
            max_total_long_exp, max_total_short_exp = 1.00, 0.40
            max_total_long_risk, max_total_short_risk = 0.08, 0.04
        
        elif vix_regime == VIXRegime.MEDIUM or (vix_regime == VIXRegime.LOW and vix_source != "LIVE"):
            recommendation = "FAVORABLE"
            min_score_long, min_score_short = 65, 70
            # RISK per position (STRICT)
            max_risk_long, max_risk_short = 0.0100, 0.0050
            # EXPOSURE per position (RETAIL-AGGRESSIVE)
            max_long_exp, max_short_exp = 0.20, 0.15
            # TOTAL BOOK limits (RETAIL-AGGRESSIVE)
            max_total_long_exp, max_total_short_exp = 0.80, 0.30
            max_total_long_risk, max_total_short_risk = 0.06, 0.03
        
        elif vix_regime == VIXRegime.HIGH:
            recommendation = "CAUTION"
            min_score_long, min_score_short = 75, 60
            # RISK per position (STRICT)
            max_risk_long, max_risk_short = 0.0075, 0.0050
            # EXPOSURE per position (RETAIL)
            max_long_exp, max_short_exp = 0.15, 0.12
            # TOTAL BOOK limits (RETAIL)
            max_total_long_exp, max_total_short_exp = 0.60, 0.25
            max_total_long_risk, max_total_short_risk = 0.04, 0.025
        
        else:
            recommendation = "DEFENSIVE" if vix_source in ["LIVE", "VXX_PROXY"] else "UNKNOWN"
            min_score_long, min_score_short = 85, 55
            # RISK per position (VERY STRICT)
            max_risk_long, max_risk_short = 0.0050, 0.0035
            # EXPOSURE per position (RETAIL)
            max_long_exp, max_short_exp = 0.12, 0.10
            # TOTAL BOOK limits (DEFENSIVE but not institutional)
            max_total_long_exp, max_total_short_exp = 0.40, 0.25
            max_total_long_risk, max_total_short_risk = 0.025, 0.02
        
        logger.info(f"Regime: VIX {vix:.1f} ({vix_regime.value}) - {recommendation}")
        if vix_source != "LIVE":
            logger.warning(f"⚠️  VIX is {vix_source} (confidence: {vix_confidence})")
        
        return RegimeContext(
            vix=vix, vix_regime=vix_regime, vix_source=vix_source,
            vix_confidence=vix_confidence, recommendation=recommendation,
            min_score_long=min_score_long, min_score_short=min_score_short,
            max_risk_per_long_pct=max_risk_long, max_risk_per_short_pct=max_risk_short,
            max_long_exposure_per_position_pct=max_long_exp,
            max_short_exposure_per_position_pct=max_short_exp,
            max_total_long_exposure_pct=max_total_long_exp,
            max_total_short_exposure_pct=max_total_short_exp,
            max_total_long_risk_pct=max_total_long_risk,
            max_total_short_risk_pct=max_total_short_risk
        )
    
    def _get_vix_with_source(self) -> Tuple[float, str, str]:
        """Get VIX with tiered fallback: yfinance ^VIX → yfinance VXX proxy → DEFENSIVE"""

        # Primary: yfinance ^VIX (CBOE index — not available on Alpaca equity feed)
        try:
            import yfinance as yf
            vix_data = yf.download('^VIX', period='5d', progress=False, threads=False)
            if vix_data is not None and len(vix_data) >= 1:
                close_col = vix_data['Close']
                raw = close_col.iloc[-1]
                vix = float(raw.item() if hasattr(raw, 'item') else raw)
                if 5.0 < vix < 100.0:
                    logger.info(f"VIX: {vix:.1f} (yfinance ^VIX — LIVE)")
                    return (vix, "LIVE", "HIGH")
        except Exception as e:
            logger.warning(f"yfinance ^VIX failed: {e}")

        # Secondary: yfinance VXX with continuous mapping (not coarse bucket rounding)
        try:
            import yfinance as yf
            vxx_data = yf.download('VXX', period='5d', progress=False, threads=False)
            if vxx_data is not None and len(vxx_data) >= 1:
                close_col = vxx_data['Close']
                raw = close_col.iloc[-1]
                vxx_price = float(raw.item() if hasattr(raw, 'item') else raw)
                vix_proxy = max(10.0, min(80.0, vxx_price * 0.85))
                logger.warning(f"VIX via VXX proxy: VXX=${vxx_price:.2f} → VIX≈{vix_proxy:.1f}")
                return (vix_proxy, "VXX_PROXY", "MEDIUM")
        except Exception as e:
            logger.warning(f"yfinance VXX proxy failed: {e}")

        logger.error("All VIX sources failed — using DEFENSIVE fallback 28.0")
        return (28.0, "FALLBACK", "LOW")


# ============================================================================
# LAYER 4: SHORT DISCOVERY
# ============================================================================

class ShortDiscovery:
    """
    Discovers SHORT opportunities with ADAPTIVE data handling
    """

    MIN_DISTANCE_FROM_MA = 20.0
    
    def __init__(self, data_feed):
        self.data_feed = data_feed
        self.adaptive_analyzer = create_adaptive_analyzer()
        self.last_rejected_reasons: Dict[str, str] = {}
        logger.info("Short Discovery initialized (ADAPTIVE)")
    
    def discover(self, validated_tickers: List[str]) -> Tuple[List[Dict], Dict[str, int]]:
        """Discover short candidates with adaptive data handling"""

        candidates = []
        rejections = {}
        self.last_rejected_reasons = {}

        # Pre-fetch SPY once — shared across all per-ticker RS rollover calculations
        # to avoid N separate API calls (one per ticker) with varying days_back keys
        try:
            self._spy_bars_for_scan = self.data_feed.get_daily_bars('SPY', days_back=265, adjustment="all")
        except Exception:
            self._spy_bars_for_scan = None

        for ticker in validated_tickers:
            try:
                # Fetch available data (don't force strict history requirement)
                bars = self.data_feed.get_daily_bars(ticker, days_back=252, adjustment="all")
                
                if not bars:
                    rejections['no_data'] = rejections.get('no_data', 0) + 1
                    self.last_rejected_reasons[ticker] = 'no_data'
                    continue

                # ADAPTIVE: Analyze what we have
                requirements = self.adaptive_analyzer.analyze_data_requirements(bars)

                # Skip if insufficient
                if self.adaptive_analyzer.should_skip_ticker(requirements):
                    rejections['insufficient_data'] = rejections.get('insufficient_data', 0) + 1
                    self.last_rejected_reasons[ticker] = 'insufficient_data'
                    continue
                
                # Analyze with adaptive requirements
                analysis = self._analyze_short_candidate(ticker, bars, requirements)
                
                if analysis['qualified']:
                    candidates.append(analysis)
                else:
                    reason = analysis.get('rejection_reason', 'unknown')
                    rejections[reason] = rejections.get(reason, 0) + 1
                    self.last_rejected_reasons[ticker] = reason
            
            except Exception as e:
                rejections['analysis_error'] = rejections.get('analysis_error', 0) + 1
                self.last_rejected_reasons[ticker] = 'analysis_error'

        # Debug visibility for short overextension levels
        for candidate in candidates:
            logger.debug(
                "DEBUG SHORT: %s - %.1f%% above 200-MA",
                candidate['ticker'],
                candidate['metrics']['pct_above_200ma']
            )
        
        return candidates, rejections
    
    def _analyze_short_candidate(self, ticker: str, bars: List,
                                 requirements: AdaptiveRequirements) -> Dict:
        """
        Analyze with TIERED CRITERIA (3 of 4, not all 4)
        
        Criteria:
        1. Overextended (>20% above 200-MA)
        2. RS rollover (declining vs SPY)
        3. Exhaustion (volume divergence)
        4. Confirmation (breakdown)
        """
        
        current_price = bars[-1]['close']
        
        ma_200 = self.adaptive_analyzer.calculate_ma(bars, requirements, 'long')
        distance_from_ma = ((current_price - ma_200) / ma_200) * 100

        rs_analysis = self._calculate_rs_rollover_adaptive(ticker, bars, requirements)
        exhaustion = self._check_exhaustion(bars)
        confirmation = self._check_confirmation_adaptive(bars, requirements)

        # TIERED SCORING: Count how many criteria are met
        criteria_met = 0
        criteria_details = {}

        if distance_from_ma >= self.MIN_DISTANCE_FROM_MA:
            criteria_met += 1
            criteria_details['overextended'] = True
        else:
            criteria_details['overextended'] = False

        if rs_analysis and rs_analysis['slope'] < -0.0001:
            criteria_met += 1
            criteria_details['rs_rollover'] = True
        else:
            criteria_details['rs_rollover'] = False

        if exhaustion.get('exhausted', False):
            criteria_met += 1
            criteria_details['exhaustion'] = True
        else:
            criteria_details['exhaustion'] = False

        if confirmation.get('confirmed', False):
            criteria_met += 1
            criteria_details['confirmation'] = True
        else:
            criteria_details['confirmation'] = False

        # Need 3 of 4 criteria
        if criteria_met < 3:
            if not criteria_details['overextended']:
                return {'qualified': False, 'rejection_reason': 'not_overextended'}
            if not criteria_details['rs_rollover']:
                return {'qualified': False, 'rejection_reason': 'rs_not_declining'}
            if not criteria_details['exhaustion']:
                return {'qualified': False, 'rejection_reason': 'no_exhaustion'}
            return {'qualified': False, 'rejection_reason': 'no_confirmation'}

        # QUALIFIED (3/4 or 4/4)
        reasons = []

        if criteria_details['overextended']:
            reasons.append(f"{distance_from_ma:.0f}% above 200-MA (overextended)")

        if criteria_details['rs_rollover'] and rs_analysis:
            reasons.append(f"RS declining (slope: {rs_analysis['slope']:.4f})")

        if criteria_details['exhaustion']:
            reasons.append(f"Volume exhaustion (ratio: {exhaustion.get('divergence_ratio', 0):.2f})")

        if criteria_details['confirmation']:
            reasons.append(f"Confirmed: {confirmation.get('trigger', 'N/A')}")

        if criteria_met == 4:
            reasons.append("🎯 All 4 criteria met (highest conviction)")
        else:
            reasons.append(f"✅ {criteria_met}/4 criteria met (qualified)")

        if requirements.sufficiency != DataSufficiency.EXCELLENT:
            reasons.append(f"Data: {requirements.sufficiency.value} ({requirements.bars_available} bars)")

        return {
            'qualified': True,
            'ticker': ticker,
            'reasons': reasons,
            'metrics': {
                'entry_price': current_price,
                'pct_above_200ma': distance_from_ma,
                'ma_200': ma_200,
                'rs_ratio_current': rs_analysis['ratio_current'] if rs_analysis else 0,
                'rs_slope': rs_analysis['slope'] if rs_analysis else 0,
                'vol_divergence_ratio': exhaustion.get('divergence_ratio', 0),
                'rsi': self._calculate_rsi(bars[-14:]),
                'confirmation_trigger': confirmation.get('trigger', 'N/A'),
                'criteria_met': criteria_met,
                'criteria_details': criteria_details,
                'data_quality': requirements.sufficiency.value,
                'bars_used': requirements.bars_available
            },
            'adaptive_requirements': requirements
        }
    
    def _calculate_rs_rollover_adaptive(self, ticker: str, bars: List,
                                        requirements: AdaptiveRequirements) -> Optional[Dict]:
        """Calculate RS with adaptive lookback"""

        try:
            # Use pre-fetched SPY bars from discover() if available; fall back to API
            spy_bars = getattr(self, '_spy_bars_for_scan', None) or \
                       self.data_feed.get_daily_bars('SPY', days_back=len(bars) + 10, adjustment="all")
            
            if not spy_bars:
                return None
            
            rs_result = self.adaptive_analyzer.calculate_rs_ratio(
                bars, spy_bars, requirements
            )
            
            if not rs_result:
                return None
            
            return {
                'ratio_current': rs_result['ratio_current'],
                'slope': rs_result['slope'],
                'declining': rs_result['slope'] < 0,
                'confidence': rs_result['slope_confidence']
            }
        
        except:
            return None
    
    def _calculate_slope(self, values: List[float]) -> float:
        """Calculate linear regression slope"""
        
        n = len(values)
        if n < 2:
            return 0
        
        x = list(range(n))
        x_mean = statistics.mean(x)
        y_mean = statistics.mean(values)
        
        numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        
        return numerator / denominator if denominator != 0 else 0
    
    def _check_exhaustion(self, bars: List) -> Dict:
        """Check for volume exhaustion (up-day divergence)"""
        
        try:
            recent_20 = bars[-20:]
            recent_10 = recent_20[-10:]
            prior_10 = recent_20[-20:-10]
            
            recent_green = [b for b in recent_10 if b['close'] > b['open']]
            prior_green = [b for b in prior_10 if b['close'] > b['open']]
            
            if not recent_green or not prior_green:
                return {'exhausted': False}
            
            recent_green_vol = statistics.mean([b['volume'] for b in recent_green])
            prior_green_vol = statistics.mean([b['volume'] for b in prior_green])
            
            divergence_ratio = recent_green_vol / prior_green_vol
            
            return {
                'exhausted': divergence_ratio < 1.0,
                'divergence_ratio': divergence_ratio
            }
        
        except:
            return {'exhausted': False}
    
    def _check_confirmation_adaptive(self, bars: List,
                                     requirements: AdaptiveRequirements) -> Dict:
        """Check confirmation with adaptive MAs"""
        
        try:
            current = bars[-1]
            
            ma_20 = self.adaptive_analyzer.calculate_ma(bars, requirements, 'short')
            ma_50 = self.adaptive_analyzer.calculate_ma(bars, requirements, 'medium')
            
            if current['close'] < ma_20:
                return {
                    'confirmed': True,
                    'trigger': f"Below {requirements.ma_short_period}-MA (${ma_20:.2f})"
                }
            
            if current['close'] < ma_50:
                return {
                    'confirmed': True,
                    'trigger': f"Below {requirements.ma_medium_period}-MA (${ma_50:.2f})"
                }
            
            recent_high = max([b['high'] for b in bars[-10:-1]])
            if bars[-2]['close'] > recent_high and current['close'] < recent_high:
                return {'confirmed': True, 'trigger': "Failed breakout"}
            
            return {'confirmed': False, 'trigger': "No confirmation"}
        
        except:
            return {'confirmed': False, 'trigger': "Error"}
    
    def _calculate_rsi(self, bars: List) -> float:
        """Calculate RSI (Wilder's method)"""
        
        if len(bars) < 14:
            return 50.0
        
        try:
            changes = [bars[i]['close'] - bars[i-1]['close'] for i in range(1, len(bars))]
            gains = [c if c > 0 else 0 for c in changes]
            losses = [abs(c) if c < 0 else 0 for c in changes]
            
            avg_gain = statistics.mean(gains[:14])
            avg_loss = statistics.mean(losses[:14])
            
            for i in range(14, len(gains)):
                avg_gain = (avg_gain * 13 + gains[i]) / 14
                avg_loss = (avg_loss * 13 + losses[i]) / 14
            
            if avg_loss == 0:
                return 100.0
            
            rs = avg_gain / avg_loss
            return 100 - (100 / (1 + rs))
        
        except:
            return 50.0


# ============================================================================
# LAYER 5: SHORT SCORING
# ============================================================================

class ShortScorer:
    """
    Scores SHORT opportunities (0-100)
    
    Weights (from spec):
    - Overextension (distance above 200MA): 25
    - RS rollover strength: 25
    - Exhaustion/divergence: 20
    - Breakdown confirmation: 15
    - Liquidity feasibility: 15
    - Optional 13F cluster selling: +10 (bonus)
    """
    
    def score(self, candidate: Dict) -> Tuple[int, List[str]]:
        """Score with bonus for meeting all 4 criteria"""
        
        metrics = candidate['metrics']
        requirements = candidate.get('adaptive_requirements')
        score = 0
        score_breakdown = []
        
        # 1. Overextension (25 points)
        distance = metrics['pct_above_200ma']
        if distance > 50:
            overext_score = 25
        elif distance > 35:
            overext_score = 20
        elif distance > 25:
            overext_score = 15
        else:
            overext_score = 10
        
        score += overext_score
        score_breakdown.append(f"Overextension: {overext_score}/25")
        
        # 2. RS rollover strength (25 points)
        rs_slope = abs(metrics['rs_slope'])
        if rs_slope > 0.002:
            rs_score = 25
        elif rs_slope > 0.001:
            rs_score = 20
        elif rs_slope > 0.0005:
            rs_score = 15
        else:
            rs_score = 10
        
        score += rs_score
        score_breakdown.append(f"RS rollover: {rs_score}/25")
        
        # 3. Exhaustion/divergence (20 points)
        divergence = metrics.get('vol_divergence_ratio', 1.0)
        if divergence < 0.7:
            exh_score = 20
        elif divergence < 0.85:
            exh_score = 15
        elif divergence < 1.0:
            exh_score = 10
        else:
            exh_score = 5
        
        score += exh_score
        score_breakdown.append(f"Exhaustion: {exh_score}/20")
        
        # 4. Breakdown confirmation (15 points)
        criteria_details = metrics.get('criteria_details', {})
        if criteria_details.get('confirmation', False):
            conf_score = 15
        else:
            conf_score = 10  # Partial credit if passed with 3/4
        score += conf_score
        score_breakdown.append(f"Confirmation: {conf_score}/15")
        
        # 5. Liquidity (15 points)
        # Proxy: if passed universe validation, assume good liquidity
        liq_score = 15
        score += liq_score
        score_breakdown.append(f"Liquidity: {liq_score}/15")

        # BONUS: All 4 criteria met
        criteria_met = metrics.get('criteria_met', 0)
        if criteria_met == 4:
            bonus = 5
            score += bonus
            score_breakdown.append(f"All criteria bonus: +{bonus}")
        
        # ADAPTIVE: Adjust score based on data quality
        if requirements:
            adjusted_score = self._apply_quality_adjustment(score, requirements)
            if requirements.sufficiency != DataSufficiency.EXCELLENT:
                score_breakdown.append(
                    f"Quality adjustment: {score} → {adjusted_score} "
                    f"({requirements.sufficiency.value})"
                )
            score = adjusted_score

        return score, score_breakdown

    def _apply_quality_adjustment(self, base_score: int,
                                  requirements: AdaptiveRequirements) -> int:
        """Apply data quality adjustment to score"""
        adjusted = int(base_score * requirements.confidence_multiplier)
        return max(0, adjusted)

# ============================================================================
# LAYER 6: SHORT RISK GATE
# ============================================================================

class ShortRiskGate:
    """Validates SHORT opportunities with Growth Mode and improved targets"""
    
    # ATR multiplier for stops (tighter for shorts)
    ATR_MULTIPLIER_SHORT = 2.5
    SWING_HIGH_PERIOD = 10

    # Growth Mode thresholds (slightly different for shorts)
    GROWTH_MODE_MIN_SCORE = 80
    GROWTH_MODE_MIN_CRITERIA = 4
    GROWTH_MODE_MAX_EXPOSURE = 0.20
    GROWTH_MODE_MAX_RISK = 0.0100
    
    def __init__(self, account_size: float, execution_mode: ExecutionMode,
                 growth_mode: bool = False):
        self.account_size = account_size
        self.execution_mode = execution_mode
        self.growth_mode = growth_mode
        self.adaptive_analyzer = create_adaptive_analyzer()
        logger.info(f"Short Risk Gate initialized (Growth Mode: {growth_mode})")
    
    def validate_batch(self, candidates: List[Dict], regime: RegimeContext, 
                      data_feed) -> Tuple[List[Opportunity], Dict[str, int]]:
        """Validate with detailed rejection tracking"""
        
        approved = []
        rejections = {}
        
        for candidate in candidates:
            try:
                opportunity, rejection_reason = self._validate_candidate_detailed(
                    candidate, regime, data_feed
                )
                
                if opportunity:
                    candidate['approved'] = True
                    candidate['rejected'] = False
                    candidate['rejection_reason'] = None
                    approved.append(opportunity)
                else:
                    candidate['approved'] = False
                    candidate['rejected'] = True
                    candidate['rejection_reason'] = rejection_reason or 'validation_failed'
                    if rejection_reason:
                        rejections[rejection_reason] = rejections.get(rejection_reason, 0) + 1
                    else:
                        rejections['validation_failed'] = rejections.get('validation_failed', 0) + 1
            
            except Exception as e:
                logger.error(f"Short risk validation error: {e}")
                rejections['validation_error'] = rejections.get('validation_error', 0) + 1
        
        return approved, rejections
    
    def _validate_candidate(self, candidate: Dict, regime: RegimeContext, 
                           data_feed) -> Optional[Opportunity]:
        """Backward-compatible wrapper"""
        opportunity, _ = self._validate_candidate_detailed(candidate, regime, data_feed)
        return opportunity

    def _validate_candidate_detailed(self, candidate: Dict, regime: RegimeContext,
                                     data_feed) -> Tuple[Optional[Opportunity], Optional[str]]:
        """Validate SHORT with Growth Mode and improved targets"""
        
        ticker = candidate['ticker']
        metrics = candidate['metrics']
        score = candidate.get('score', 0)
        score_result = candidate.get('score_result', {})
        entry = metrics['entry_price']
        
        # Get adaptive requirements from candidate
        requirements = candidate.get('adaptive_requirements')

        # ADAPTIVE: Fetch appropriate number of bars
        if requirements:
            days_to_fetch = min(requirements.bars_available, 30)
        else:
            days_to_fetch = 30

        bars = data_feed.get_daily_bars(ticker, days_back=days_to_fetch, adjustment="all")

        if not bars:
            return (None, "no_bars_for_risk_calc")

        if len(bars) < 10:
            return (None, "insufficient_bars_for_atr")

        # Calculate ATR adaptively
        if requirements:
            atr = self.adaptive_analyzer.calculate_atr(bars, requirements)
        else:
            atr_period = min(14, len(bars) - 1)
            atr = self._calculate_atr(bars[-atr_period:])

        if atr == 0:
            return (None, "atr_calculation_failed")

        # Calculate stop (max of 2.5x ATR or 10-day swing high)
        atr_stop = entry + (atr * self.ATR_MULTIPLIER_SHORT)
        
        # ADAPTIVE: Swing high calculation
        swing_period = min(self.SWING_HIGH_PERIOD, len(bars) - 1)
        if swing_period < 5:
            return (None, "insufficient_bars_for_swing")

        recent_bars = bars[-swing_period:]
        swing_high = max([b['high'] for b in recent_bars])
        
        stop = max(atr_stop, swing_high)
        
        # Target (tiered mean reversion, not always full 200-MA move)
        ma_200 = metrics.get('ma_200')
        distance_from_ma = metrics.get('pct_above_200ma')
        if ma_200 is None or distance_from_ma is None:
            return (None, "missing_target_ma_200")
        
        if distance_from_ma > 40:
            target = ma_200
        elif distance_from_ma > 25:
            target = entry - (entry - ma_200) * 0.5
        else:
            target = entry * 0.90

        if target >= entry:
            return (None, "invalid_short_target")
        
        # Calculate returns
        downside_pct = ((entry - target) / entry) * 100
        risk_pct_price = ((stop - entry) / entry) * 100

        if risk_pct_price <= 0:
            return (None, "invalid_stop_loss")

        # Risk/Reward
        risk_reward = downside_pct / risk_pct_price
        
        # Minimum R/R requirement
        if risk_reward < 2.0:
            logger.debug(f"{ticker}: R/R too low ({risk_reward:.1f}:1)")
            return (None, f"risk_reward_too_low_{risk_reward:.1f}")
        
        # GROWTH MODE CHECK
        criteria_met = metrics.get('criteria_met', 0)
        weighted_growth = bool(score_result.get('growth_mode_eligible'))
        qualifies_for_growth = (
            self.growth_mode and (
                weighted_growth or (
                    score >= self.GROWTH_MODE_MIN_SCORE and
                    criteria_met >= self.GROWTH_MODE_MIN_CRITERIA
                )
            )
        )

        if qualifies_for_growth:
            max_risk_pct = self.GROWTH_MODE_MAX_RISK
            max_exposure_pct = self.GROWTH_MODE_MAX_EXPOSURE
            hard_cap_exposure = self.GROWTH_MODE_MAX_EXPOSURE
            mode_label = "GROWTH MODE"
            logger.info(
                f"{ticker}: Qualifies for GROWTH MODE SHORT "
                f"(Score {score}, {criteria_met}/4 criteria)"
            )
        else:
            max_risk_pct = regime.max_risk_per_short_pct
            max_exposure_pct = regime.max_short_exposure_per_position_pct
            hard_cap_exposure = max_exposure_pct * 1.2
            mode_label = "STANDARD"

        position = _safe_position_size(
            account_equity=self.account_size,
            entry_price=entry,
            stop_price=stop,
            score_result={'growth_mode_eligible': qualifies_for_growth}
        )
        if not position.get('valid'):
            return (None, position.get('error', 'position_sizing_failed'))

        shares = position['shares']
        position_value = position['position_value']
        actual_risk_amount = position['risk_at_stop']
        actual_risk_pct = position['risk_pct']
        risk_per_share = stop - entry

        # PRIMARY CHECK: Risk-at-stop (STRICT)
        if actual_risk_pct > max_risk_pct * 100:
            logger.warning(
                f"{ticker}: Risk too high - "
                f"${actual_risk_amount:,.0f} ({actual_risk_pct:.2f}%) "
                f"exceeds max {max_risk_pct*100:.2f}%"
            )
            return (None, f"risk_at_stop_exceeded_{actual_risk_pct:.2f}pct")

        # SECONDARY CHECK: Exposure cap
        exposure_pct = position_value / self.account_size
        if exposure_pct > hard_cap_exposure:
            logger.warning(
                f"{ticker}: Exposure very high - "
                f"${position_value:,.0f} ({exposure_pct*100:.1f}%) "
                f"exceeds hard cap {hard_cap_exposure*100:.0f}%"
            )
            return (None, f"exposure_hard_cap_exceeded_{int(exposure_pct*100)}pct")

        self._print_risk_analysis(
            ticker, entry, stop, target, shares, position_value,
            exposure_pct, actual_risk_amount, actual_risk_pct,
            risk_reward, regime
        )
        
        # Determine execution status
        if self.execution_mode == ExecutionMode.LIVE:
            execution_status = "APPROVED"
        else:
            execution_status = "SIMULATED"
        
        # Determine mode
        mode = OpportunityMode.ULTIMATE if qualifies_for_growth else OpportunityMode.EXPLOITING
        
        warnings = []
        if qualifies_for_growth:
            warnings.append("🔥 GROWTH MODE - All 4 criteria met, aggressive sizing")
        
        opportunity = Opportunity(
            ticker=ticker,
            direction="SHORT",
            mode=mode,
            score=score,
            entry_price=entry,
            target_price=target,
            stop_loss=stop,
            shares=shares,
            position_value=position_value,
            risk_amount=actual_risk_amount,
            risk_pct=actual_risk_pct,
            exposure_pct=exposure_pct * 100,
            target_return_pct=downside_pct,
            risk_reward=risk_reward,
            reasons=candidate['reasons'],
            metrics=metrics,
            strategy="SHORT",
            grade=score_result.get('grade', 'N/A'),
            growth_mode=qualifies_for_growth,
            tier_breakdown=score_result.get('tier_breakdown', {}),
            confidence_summary=score_result.get('confidence_summary', {}),
            execution_status=execution_status,
            warnings=warnings
        )
        
        logger.info(
            f"{ticker}: ✅ APPROVED SHORT ({mode_label}) - "
            f"Risk {actual_risk_pct:.2f}%, Exposure {exposure_pct*100:.1f}%, "
            f"R/R {risk_reward:.1f}:1"
        )
        return (opportunity, None)
    
    def _calculate_atr(self, bars: List) -> float:
        """Calculate ATR"""
        
        if len(bars) < 2:
            return 0
        
        try:
            true_ranges = []
            for i in range(1, len(bars)):
                high = bars[i]['high']
                low = bars[i]['low']
                prev_close = bars[i-1]['close']
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            return statistics.mean(true_ranges)
        except:
            return 0

    def _print_risk_analysis(self, ticker: str, entry: float, stop: float,
                             target: float, shares: int, position_value: float,
                             exposure_pct: float, risk_amount: float,
                             risk_pct: float, risk_reward: float,
                             regime: RegimeContext):
        """Print COMPLETE short risk analysis for transparency"""
        print("\n" + "="*70)
        print(f"🔍 RISK ANALYSIS: {ticker} (SHORT)")
        print("="*70)

        print(f"\n📊 POSITION DETAILS:")
        print(f"   Entry: ${entry:.2f}")
        print(f"   Target: ${target:.2f} ({((target-entry)/entry*100):+.1f}%)")
        print(f"   Stop: ${stop:.2f} ({((stop-entry)/entry*100):+.1f}%)")
        print(f"   Stop Distance: ${abs(entry-stop):.2f}")

        print(f"\n💼 POSITION SIZING:")
        print(f"   Shares Required: {shares:,}")
        print(f"   Position Notional: ${position_value:,.0f} ({exposure_pct*100:.1f}% of account)")
        print(f"   Max Allowed Notional: ${self.account_size * regime.max_short_exposure_per_position_pct:,.0f} "
              f"({regime.max_short_exposure_per_position_pct*100:.0f}% guideline)")

        print(f"\n🎯 RISK ASSESSMENT:")
        print(f"   Risk at Stop: ${risk_amount:,.0f} ({risk_pct:.2f}% of account)")
        print(f"   Max Allowed Risk: ${self.account_size * regime.max_risk_per_short_pct:,.0f} "
              f"({regime.max_risk_per_short_pct*100:.2f}%)")
        print(f"   Risk/Reward: {risk_reward:.1f}:1")

        print(f"\n✅ VALIDATION:")
        risk_ok = risk_pct <= regime.max_risk_per_short_pct * 100
        exposure_ok = exposure_pct <= regime.max_short_exposure_per_position_pct * 1.2
        print(f"   Risk Check: {'✅ PASS' if risk_ok else '❌ FAIL'}")
        print(f"   Exposure Check: {'✅ PASS' if exposure_ok else '⚠️  HIGH'}")
        print(f"   R/R Check: {'✅ PASS' if risk_reward >= 2.0 else '❌ FAIL'}")
        print("="*70 + "\n")


# ============================================================================
# LAYER 7: LONG DISCOVERY
# ============================================================================

class LongDiscovery:
    """
    Discovers LONG opportunities (Institutional Leader mode)
    
    Criteria:
    - RS ratio vs SPY slope > 0 (improving strength)
    - Close > 50-MA (trend structure)
    - Preferably > 200-MA or reclaiming
    - Liquidity floor met
    - Accumulation quality + low distribution pressure
    """
    
    MIN_RS_SLOPE = 0.0  # Must be positive
    MIN_ACCUMULATION_SCORE = 58.0
    MAX_DISTRIBUTION_SCORE = 62.0
    LATE_STAGE_RANGE_POSITION_PCT = 85.0
    LATE_STAGE_MAX_OFF_HIGH_PCT = 8.0
    LATE_STAGE_MIN_EXTENSION_PCT = 10.0
    MIN_MARKET_CAP_USD = 2_000_000_000
    SECTOR_RS_LOOKBACK_DAYS = 63
    SECTOR_ETF_MAP = {
        "BASIC MATERIALS": "XLB",
        "COMMUNICATION SERVICES": "XLC",
        "CONSUMER CYCLICAL": "XLY",
        "CONSUMER DEFENSIVE": "XLP",
        "CONSUMER STAPLES": "XLP",
        "ENERGY": "XLE",
        "FINANCIAL SERVICES": "XLF",
        "FINANCIAL": "XLF",
        "HEALTHCARE": "XLV",
        "INDUSTRIALS": "XLI",
        "MATERIALS": "XLB",
        "REAL ESTATE": "XLRE",
        "TECHNOLOGY": "XLK",
        "UTILITIES": "XLU",
    }

    def __init__(self, data_feed):
        self.data_feed = data_feed
        self.adaptive_analyzer = create_adaptive_analyzer()
        self.last_rejected_reasons: Dict[str, str] = {}
        self._fundamentals_cache: Dict[str, Dict] = {}
        self._sector_return_cache: Dict[str, Dict[str, Optional[float]]] = {}
        self.min_market_cap_usd = self._env_float(
            "VOYAGER_MIN_MARKET_CAP_B",
            self.MIN_MARKET_CAP_USD / 1_000_000_000.0,
        ) * 1_000_000_000.0
        logger.info("Long Discovery initialized (ADAPTIVE)")

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        try:
            raw = os.getenv(name)
            if raw is None or str(raw).strip() == "":
                return float(default)
            return float(raw)
        except Exception:
            return float(default)

    def _get_voyager_fundamentals(self, ticker: str) -> Dict:
        ticker = str(ticker or "").upper().strip()
        if ticker in self._fundamentals_cache:
            return dict(self._fundamentals_cache[ticker])

        payload = {"market_cap": None, "sector": None, "industry": None}
        if HAS_FUNDAMENTAL_FETCHER and FundamentalDataFetcher is not None:
            try:
                fetched = FundamentalDataFetcher.get_voyager_fundamentals(ticker) or {}
                if fetched:
                    payload.update(fetched)
            except Exception as exc:
                logger.debug("LongDiscovery fundamentals unavailable for %s: %s", ticker, exc)

        self._fundamentals_cache[ticker] = dict(payload)
        return dict(payload)

    @classmethod
    def _resolve_sector_etf(cls, sector: Optional[str]) -> Optional[str]:
        sector_key = str(sector or "").strip().upper()
        if not sector_key:
            return None
        return cls.SECTOR_ETF_MAP.get(sector_key)

    @classmethod
    def _calculate_lookback_return_pct(
        cls, bars: List[Dict[str, float]], lookback_days: int
    ) -> Optional[float]:
        if len(bars) <= int(lookback_days):
            return None
        try:
            current_close = float(bars[-1]["close"])
            prior_close = float(bars[-(int(lookback_days) + 1)]["close"])
        except (KeyError, TypeError, ValueError, IndexError):
            return None
        if current_close <= 0 or prior_close <= 0:
            return None
        return ((current_close / prior_close) - 1.0) * 100.0

    def _calculate_sector_relative_strength(
        self,
        ticker: str,
        stock_bars: List[Dict[str, float]],
        sector: Optional[str],
    ) -> Dict[str, Optional[float]]:
        sector_etf = self._resolve_sector_etf(sector)
        stock_return_pct = self._calculate_lookback_return_pct(
            stock_bars,
            self.SECTOR_RS_LOOKBACK_DAYS,
        )
        if stock_return_pct is None or not sector_etf or self.data_feed is None:
            return {
                "sector": sector,
                "sector_etf": sector_etf,
                "stock_return_63d_pct": stock_return_pct,
                "sector_return_63d_pct": None,
                "sector_rs_score": None,
            }

        cached_sector = self._sector_return_cache.get(sector_etf)
        if cached_sector is None:
            sector_bars = self.data_feed.get_daily_bars(
                sector_etf,
                days_back=max(126, self.SECTOR_RS_LOOKBACK_DAYS + 21),
                adjustment="all",
            ) or []
            sector_return_pct = self._calculate_lookback_return_pct(
                sector_bars,
                self.SECTOR_RS_LOOKBACK_DAYS,
            )
            cached_sector = {
                "sector_return_63d_pct": sector_return_pct,
            }
            self._sector_return_cache[sector_etf] = dict(cached_sector)

        sector_return_pct = cached_sector.get("sector_return_63d_pct")
        sector_rs_score = None
        if sector_return_pct is not None:
            sector_rs_score = round(float(stock_return_pct) - float(sector_return_pct), 2)

        return {
            "sector": sector,
            "sector_etf": sector_etf,
            "stock_return_63d_pct": round(float(stock_return_pct), 2),
            "sector_return_63d_pct": (
                round(float(sector_return_pct), 2) if sector_return_pct is not None else None
            ),
            "sector_rs_score": sector_rs_score,
        }
    
    def discover(self, validated_tickers: List[str]) -> Tuple[List[Dict], Dict[str, int]]:
        """Discover long candidates"""

        candidates = []
        rejections = {}
        self.last_rejected_reasons = {}

        # Pre-fetch SPY once — shared across all per-ticker RS improvement calculations
        try:
            self._spy_bars_for_scan = self.data_feed.get_daily_bars('SPY', days_back=265, adjustment="all")
        except Exception:
            self._spy_bars_for_scan = None

        for ticker in validated_tickers:
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=252, adjustment="all")
                
                if not bars:
                    rejections['no_data'] = rejections.get('no_data', 0) + 1
                    self.last_rejected_reasons[ticker] = 'no_data'
                    continue

                requirements = self.adaptive_analyzer.analyze_data_requirements(bars)
                if self.adaptive_analyzer.should_skip_ticker(requirements):
                    rejections['insufficient_data'] = rejections.get('insufficient_data', 0) + 1
                    self.last_rejected_reasons[ticker] = 'insufficient_data'
                    continue
                
                analysis = self._analyze_long_candidate(ticker, bars, requirements)
                
                if analysis['qualified']:
                    candidates.append(analysis)
                else:
                    reason = analysis.get('rejection_reason', 'unknown')
                    rejections[reason] = rejections.get(reason, 0) + 1
                    self.last_rejected_reasons[ticker] = reason
            
            except Exception as e:
                rejections['analysis_error'] = rejections.get('analysis_error', 0) + 1
                self.last_rejected_reasons[ticker] = 'analysis_error'
        
        return candidates, rejections
    
    def _analyze_long_candidate(self, ticker: str, bars: List,
                                requirements: AdaptiveRequirements) -> Dict:
        """Analyze with CONSOLIDATION AWARENESS (not just RS improvement)"""
        
        current_price = bars[-1]['close']
        
        # Calculate RS improvement
        rs_analysis = self._calculate_rs_improvement(ticker, bars, requirements)
        
        if not rs_analysis:
            return {'qualified': False, 'rejection_reason': 'rs_calculation_failed'}

        # Calculate moving averages FIRST (needed for RS evaluation)
        ma_50 = self.adaptive_analyzer.calculate_ma(bars, requirements, 'medium')
        ma_200 = self.adaptive_analyzer.calculate_ma(bars, requirements, 'long')

        # Check 200-MA relationship
        distance_from_200ma = ((current_price - ma_200) / ma_200) * 100

        fundamentals = self._get_voyager_fundamentals(ticker)
        market_cap = fundamentals.get('market_cap')
        if market_cap is not None and float(market_cap) < float(self.min_market_cap_usd):
            return {'qualified': False, 'rejection_reason': 'market_cap_too_small'}
        sector = fundamentals.get('sector')
        sector_rs = self._calculate_sector_relative_strength(ticker, bars, sector)

        # ============================================================================
        # FILTER 0: MAXIMUM OVEREXTENSION — not an accumulation zone
        # ============================================================================
        # Stocks >25% above their 200-MA have already been accumulated by institutions.
        # The real accumulation zone is near the 200-MA, not extended from it.
        # Example: OXY at $62 (+37% above 200-MA) is distribution phase.
        #          OXY at $35 (near 200-MA) was the true accumulation zone.
        # This filter would have caught OXY and rejected the $62 entry.
        if distance_from_200ma > 25.0:
            return {'qualified': False, 'rejection_reason': 'overextended_for_long_entry'}

        # ============================================================================
        # FILTER 0b: DECLINING FROM PEAK (lower highs while extended)
        # ============================================================================
        # If price is >15% above 200-MA AND the stock is making lower highs over the
        # past 30 bars, it has peaked and is entering distribution — not accumulation.
        # A stock pulling back TOWARD the 200-MA from below 15% extension is fine —
        # that could be setting up a base. A stock 20% above its MA making lower highs
        # is distributing into strength.
        if distance_from_200ma > 15.0 and len(bars) >= 30:
            recent_high = max(b['high'] for b in bars[-10:])
            prior_high = max(b['high'] for b in bars[-30:-10])
            if prior_high > 0 and recent_high < prior_high * 0.92:
                return {'qualified': False, 'rejection_reason': 'declining_from_peak'}

        # Check accumulation/distribution flow regime
        accumulation = self._check_accumulation(bars)
        accumulation_score = float(accumulation.get('accumulation_score', 0.0) or 0.0)
        distribution_score = float(accumulation.get('distribution_score', 0.0) or 0.0)
        zone_assessment = self._assess_accumulation_zone(
            bars=bars,
            current_price=current_price,
            ma_50=ma_50,
            ma_200=ma_200,
            accumulation=accumulation,
        )

        # ============================================================================
        # FILTER 0c: LATE-STAGE EXTENSION — too close to highs for fresh accumulation
        # ============================================================================
        # If price is already deep into the upper end of its 6-month range while
        # materially extended above the 200-MA, the base has likely already
        # resolved. That is no longer a clean accumulation entry for Voyager.
        if zone_assessment.get('late_stage'):
            return {'qualified': False, 'rejection_reason': 'late_stage_extension'}

        # ============================================================================
        # FILTER 1 (REVISED): RS IMPROVEMENT OR CONSOLIDATION
        # ============================================================================
        rs_slope = rs_analysis['slope']

        # CASE 1: Strong RS improvement (original requirement)
        if rs_slope > self.MIN_RS_SLOPE:
            rs_status = "improving"
            rs_reason = f"RS improving (slope: {rs_slope:.4f})"

        # CASE 2: CONSOLIDATION with strength (NEW - OUTLIER LOGIC!)
        elif rs_slope >= -0.0003:
            if current_price > ma_200 and accumulation['accumulating']:
                rs_status = "consolidating"
                rs_reason = (
                    f"RS consolidating from strength "
                    f"(slope: {rs_slope:.4f}, above 200-MA)"
                )
            else:
                return {'qualified': False, 'rejection_reason': 'rs_declining_weak'}

        # CASE 3: Actually declining (reject)
        else:
            return {'qualified': False, 'rejection_reason': 'rs_declining'}

        # ============================================================================
        # FILTER 2: Must be above 50-MA
        # ============================================================================
        if current_price < ma_50:
            return {'qualified': False, 'rejection_reason': 'below_50ma'}

        # ============================================================================
        # FILTER 3: If below 200-MA, must be reclaiming (within 5%)
        # ============================================================================
        if current_price < ma_200 and distance_from_200ma < -5:
            return {'qualified': False, 'rejection_reason': 'too_far_below_200ma'}

        # ============================================================================
        # FILTER 4: Should show some accumulation
        # ============================================================================
        if not accumulation['accumulating']:
            return {'qualified': False, 'rejection_reason': 'no_accumulation'}

        # ============================================================================
        # FILTER 5: Avoid fresh distribution regimes (institutional selling)
        # ============================================================================
        if accumulation.get('distributing') and distribution_score >= self.MAX_DISTRIBUTION_SCORE:
            return {'qualified': False, 'rejection_reason': 'distribution_pressure'}

        # ============================================================================
        # QUALIFIED!
        # ============================================================================
        reasons = []
        reasons.append(rs_reason)
        reasons.append(f"Above 50-MA (${ma_50:.2f})")
        
        if current_price > ma_200:
            reasons.append(f"Above 200-MA (+{distance_from_200ma:.1f}%)")
        else:
            reasons.append(f"Reclaiming 200-MA ({distance_from_200ma:.1f}%)")
        
        if accumulation['accumulating']:
            reasons.append(
                f"Accumulation regime (score: {accumulation_score:.0f}, "
                f"ratio: {accumulation['volume_ratio']:.2f})"
            )
        reasons.append(
            f"Distribution pressure: {distribution_score:.0f} "
            f"(days A/D: {accumulation.get('accumulation_days', 0)}/{accumulation.get('distribution_days', 0)})"
        )
        reasons.append(
            f"Zone: {zone_assessment.get('zone_label', 'UNKNOWN')} "
            f"(range pos {zone_assessment.get('range_position_pct', 0):.0f}%, "
            f"off 6m high {zone_assessment.get('off_120d_high_pct', 0):.1f}%)"
        )
        sector_rs_score = sector_rs.get('sector_rs_score')
        if sector_rs_score is not None:
            direction = "beating" if sector_rs_score >= 0 else "trailing"
            reasons.append(
                f"63d sector RS: {direction} {sector_rs.get('sector_etf') or 'sector'} "
                f"by {abs(float(sector_rs_score)):.1f} pts"
            )

        if rs_status == "consolidating":
            reasons.append("🎯 Base-building (institutional constraint = retail opportunity)")

        return {
            'qualified': True,
            'ticker': ticker,
            'reasons': reasons,
            'metrics': {
                'entry_price': current_price,
                'rs_ratio_current': rs_analysis['ratio_current'],
                'rs_slope': rs_slope,
                'rs_status': rs_status,
                'ma_50': ma_50,
                'ma_200': ma_200,
                'close_vs_50ma': ((current_price - ma_50) / ma_50) * 100,
                'close_vs_200ma': distance_from_200ma,
                'accumulation_ratio': accumulation.get('volume_ratio', 0),
                'accumulation_score': accumulation_score,
                'distribution_score': distribution_score,
                'distribution_ratio': accumulation.get('distribution_ratio', 0),
                'accumulation_days': accumulation.get('accumulation_days', 0),
                'distribution_days': accumulation.get('distribution_days', 0),
                'flow_regime': accumulation.get('flow_regime', 'MIXED'),
                'obv_slope_norm': accumulation.get('obv_slope_norm', 0),
                'adl_slope_norm': accumulation.get('adl_slope_norm', 0),
                'avg_volume': accumulation.get('avg_volume', 0),
                'rsi': self._calculate_rsi(bars[-14:]),
                'zone_label': zone_assessment.get('zone_label'),
                'zone_score': zone_assessment.get('zone_score'),
                'true_accumulation_zone': zone_assessment.get('true_accumulation_zone'),
                'late_stage': zone_assessment.get('late_stage'),
                'range_position_pct': zone_assessment.get('range_position_pct'),
                'off_120d_high_pct': zone_assessment.get('off_120d_high_pct'),
                'range_120d_high': zone_assessment.get('range_120d_high'),
                'range_120d_low': zone_assessment.get('range_120d_low'),
                'market_cap': market_cap,
                'sector': sector,
                'sector_etf': sector_rs.get('sector_etf'),
                'stock_return_63d_pct': sector_rs.get('stock_return_63d_pct'),
                'sector_return_63d_pct': sector_rs.get('sector_return_63d_pct'),
                'sector_rs_score': sector_rs.get('sector_rs_score'),
            },
            'adaptive_requirements': requirements
        }

    def _assess_accumulation_zone(
        self,
        bars: List,
        current_price: float,
        ma_50: float,
        ma_200: float,
        accumulation: Dict,
    ) -> Dict:
        """
        Classify whether price is in a real long-term accumulation zone or already
        too far advanced in its base/run cycle.

        This is used both for live Voyager gating and for research review.
        """
        lookback = bars[-120:] if len(bars) >= 120 else list(bars)
        highs = [float(b['high']) for b in lookback] if lookback else [float(current_price or 0.0)]
        lows = [float(b['low']) for b in lookback] if lookback else [float(current_price or 0.0)]
        range_high = max(highs) if highs else float(current_price or 0.0)
        range_low = min(lows) if lows else float(current_price or 0.0)
        range_span = max(range_high - range_low, 1e-6)

        close_vs_50ma = ((current_price - ma_50) / ma_50 * 100.0) if ma_50 else 0.0
        close_vs_200ma = ((current_price - ma_200) / ma_200 * 100.0) if ma_200 else 0.0
        range_position_pct = ((current_price - range_low) / range_span) * 100.0
        off_high_pct = ((range_high - current_price) / max(range_high, 1e-6)) * 100.0
        accumulation_score = float(accumulation.get('accumulation_score', 0.0) or 0.0)
        distribution_score = float(accumulation.get('distribution_score', 0.0) or 0.0)

        prime_accumulation = (
            close_vs_200ma <= 5.0
            and range_position_pct <= 70.0
            and off_high_pct >= 10.0
            and accumulation_score >= 60.0
            and distribution_score <= 45.0
        )
        early_breakout = (
            close_vs_200ma <= 10.0
            and range_position_pct <= 80.0
            and off_high_pct >= 7.0
            and accumulation_score >= self.MIN_ACCUMULATION_SCORE
            and distribution_score <= 50.0
        )
        reclaim_attempt = (
            -5.0 <= close_vs_200ma < 0.0
            and accumulation_score >= self.MIN_ACCUMULATION_SCORE
            and distribution_score <= 50.0
        )
        late_stage = (
            close_vs_200ma >= self.LATE_STAGE_MIN_EXTENSION_PCT
            and range_position_pct >= self.LATE_STAGE_RANGE_POSITION_PCT
            and off_high_pct <= self.LATE_STAGE_MAX_OFF_HIGH_PCT
        )

        if late_stage:
            zone_label = 'LATE_STAGE_EXTENSION'
        elif prime_accumulation:
            zone_label = 'PRIME_ACCUMULATION'
        elif early_breakout:
            zone_label = 'EARLY_BREAKOUT'
        elif reclaim_attempt:
            zone_label = 'RECLAIM_ATTEMPT'
        else:
            zone_label = 'TRANSITION'

        zone_score = 65.0
        zone_score += max(0.0, 12.0 - abs(close_vs_200ma)) * 1.4
        zone_score += max(0.0, off_high_pct - 5.0) * 0.8
        zone_score -= max(0.0, range_position_pct - 70.0) * 0.9
        zone_score += max(-10.0, min(15.0, (accumulation_score - distribution_score) * 0.35))
        zone_score = round(max(0.0, min(100.0, zone_score)), 1)

        return {
            'zone_label': zone_label,
            'zone_score': zone_score,
            'true_accumulation_zone': zone_label in {'PRIME_ACCUMULATION', 'EARLY_BREAKOUT', 'RECLAIM_ATTEMPT'},
            'late_stage': late_stage,
            'range_position_pct': round(range_position_pct, 1),
            'off_120d_high_pct': round(off_high_pct, 1),
            'range_120d_high': round(range_high, 4),
            'range_120d_low': round(range_low, 4),
            'close_vs_50ma': round(close_vs_50ma, 2),
            'close_vs_200ma': round(close_vs_200ma, 2),
        }

    def _calculate_rs_improvement(self, ticker: str, bars: List,
                                  requirements: AdaptiveRequirements) -> Optional[Dict]:
        """Compatibility wrapper for RS improvement calculation"""
        return self._calculate_rs_improvement_adaptive(ticker, bars, requirements)
    
    def _calculate_rs_improvement_adaptive(self, ticker: str, bars: List,
                                           requirements: AdaptiveRequirements) -> Optional[Dict]:
        """Calculate RS ratio vs SPY and check for improvement with adaptive lookback"""

        try:
            # Use pre-fetched SPY bars from discover() if available; fall back to API
            spy_bars = getattr(self, '_spy_bars_for_scan', None) or \
                       self.data_feed.get_daily_bars('SPY', days_back=len(bars) + 10, adjustment="all")
            
            if not spy_bars:
                return None
            
            rs_result = self.adaptive_analyzer.calculate_rs_ratio(
                bars, spy_bars, requirements
            )
            if not rs_result:
                return None
            
            return {
                'ratio_current': rs_result['ratio_current'],
                'slope': rs_result['slope'],
                'improving': rs_result['slope'] > 0,
                'confidence': rs_result['slope_confidence']
            }
        
        except:
            return None
    
    def _calculate_slope(self, values: List[float]) -> float:
        """Calculate linear regression slope"""
        
        n = len(values)
        if n < 2:
            return 0
        
        x = list(range(n))
        x_mean = statistics.mean(x)
        y_mean = statistics.mean(values)
        
        numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        
        return numerator / denominator if denominator != 0 else 0
    
    def _check_accumulation(self, bars: List) -> Dict:
        """
        Institutional flow model for long entries.

        Uses only OHLCV so it is stable in production:
        - Up/down volume dominance
        - OBV slope
        - Accumulation/Distribution line slope (CLV * volume)
        - Count of high-volume accumulation/distribution days
        - Trend damage checks for early distribution
        """

        def _clip(v: float, lo: float, hi: float) -> float:
            return max(lo, min(hi, v))

        try:
            if len(bars) < 35:
                return {
                    'accumulating': False,
                    'distributing': False,
                    'volume_ratio': 0.0,
                    'distribution_ratio': 0.0,
                    'accumulation_score': 0.0,
                    'distribution_score': 0.0,
                    'accumulation_days': 0,
                    'distribution_days': 0,
                    'flow_regime': 'INSUFFICIENT',
                    'obv_slope_norm': 0.0,
                    'adl_slope_norm': 0.0,
                    'avg_volume': 0.0,
                }

            window = bars[-60:] if len(bars) >= 60 else bars[-len(bars):]
            closes = [float(b['close']) for b in window]
            highs = [float(b['high']) for b in window]
            lows = [float(b['low']) for b in window]
            volumes = [float(b['volume']) for b in window]

            avg_volume = statistics.mean(volumes[-20:]) if len(volumes) >= 20 else statistics.mean(volumes)

            up_vol = 0.0
            down_vol = 0.0
            accumulation_days = 0
            distribution_days = 0

            obv = 0.0
            adl = 0.0
            obv_series = [0.0]
            adl_series = [0.0]

            for i in range(1, len(window)):
                close = closes[i]
                prev_close = closes[i - 1]
                high = highs[i]
                low = lows[i]
                vol = volumes[i]

                if close > prev_close:
                    up_vol += vol
                    obv += vol
                elif close < prev_close:
                    down_vol += vol
                    obv -= vol

                rng = max(high - low, 1e-6)
                clv = ((close - low) - (high - close)) / rng
                adl += clv * vol

                high_vol = vol >= (avg_volume * 1.10)
                if close > prev_close and clv > 0.20 and high_vol:
                    accumulation_days += 1
                if close < prev_close and clv < -0.20 and high_vol:
                    distribution_days += 1

                obv_series.append(obv)
                adl_series.append(adl)

            volume_ratio = up_vol / max(down_vol, 1.0)
            distribution_ratio = down_vol / max(up_vol, 1.0)

            obv_slope = self._calculate_slope(obv_series[-20:]) if len(obv_series) >= 20 else self._calculate_slope(obv_series)
            adl_slope = self._calculate_slope(adl_series[-20:]) if len(adl_series) >= 20 else self._calculate_slope(adl_series)
            obv_slope_norm = obv_slope / max(avg_volume, 1.0)
            adl_slope_norm = adl_slope / max(avg_volume, 1.0)

            ma20 = statistics.mean(closes[-20:]) if len(closes) >= 20 else closes[-1]
            ma50 = statistics.mean(closes[-50:]) if len(closes) >= 50 else ma20
            trend_damage = 0.0
            if closes[-1] < ma20:
                trend_damage += 4.0
            if closes[-1] < ma50:
                trend_damage += 6.0

            acc_score = 0.0
            acc_score += _clip((volume_ratio - 0.9) * 24.0, 0.0, 30.0)
            acc_score += _clip(obv_slope_norm * 380.0, 0.0, 18.0)
            acc_score += _clip(adl_slope_norm * 360.0, 0.0, 18.0)
            acc_score += _clip((accumulation_days - distribution_days + 2.0) * 2.5, 0.0, 20.0)
            acc_score += 14.0 if closes[-1] > ma50 else (8.0 if closes[-1] > ma20 else 0.0)
            accumulation_score = round(_clip(acc_score, 0.0, 100.0), 1)

            dist_score = 0.0
            dist_score += _clip((distribution_ratio - 0.9) * 24.0, 0.0, 30.0)
            dist_score += _clip((-obv_slope_norm) * 380.0, 0.0, 18.0)
            dist_score += _clip((-adl_slope_norm) * 360.0, 0.0, 18.0)
            dist_score += _clip((distribution_days - accumulation_days + 1.0) * 2.5, 0.0, 20.0)
            dist_score += trend_damage
            distribution_score = round(_clip(dist_score, 0.0, 100.0), 1)

            accumulating = (
                accumulation_score >= self.MIN_ACCUMULATION_SCORE
                and distribution_score <= self.MAX_DISTRIBUTION_SCORE
            )
            distributing = distribution_score >= self.MAX_DISTRIBUTION_SCORE

            if accumulation_score >= 65 and distribution_score <= 45:
                flow_regime = 'ACCUMULATION'
            elif distribution_score >= 65:
                flow_regime = 'DISTRIBUTION'
            else:
                flow_regime = 'MIXED'

            return {
                'accumulating': accumulating,
                'distributing': distributing,
                'volume_ratio': round(volume_ratio, 3),
                'distribution_ratio': round(distribution_ratio, 3),
                'accumulation_score': accumulation_score,
                'distribution_score': distribution_score,
                'accumulation_days': int(accumulation_days),
                'distribution_days': int(distribution_days),
                'flow_regime': flow_regime,
                'obv_slope_norm': round(obv_slope_norm, 5),
                'adl_slope_norm': round(adl_slope_norm, 5),
                'avg_volume': avg_volume,
            }

        except Exception:
            return {
                'accumulating': False,
                'distributing': False,
                'volume_ratio': 0.0,
                'distribution_ratio': 0.0,
                'accumulation_score': 0.0,
                'distribution_score': 0.0,
                'accumulation_days': 0,
                'distribution_days': 0,
                'flow_regime': 'ERROR',
                'obv_slope_norm': 0.0,
                'adl_slope_norm': 0.0,
                'avg_volume': 0.0,
            }
    
    def _calculate_rsi(self, bars: List) -> float:
        """Calculate RSI"""
        
        if len(bars) < 14:
            return 50.0
        
        try:
            changes = [bars[i]['close'] - bars[i-1]['close'] for i in range(1, len(bars))]
            gains = [c if c > 0 else 0 for c in changes]
            losses = [abs(c) if c < 0 else 0 for c in changes]
            
            avg_gain = statistics.mean(gains[:14])
            avg_loss = statistics.mean(losses[:14])
            
            for i in range(14, len(gains)):
                avg_gain = (avg_gain * 13 + gains[i]) / 14
                avg_loss = (avg_loss * 13 + losses[i]) / 14
            
            if avg_loss == 0:
                return 100.0
            
            rs = avg_gain / avg_loss
            return 100 - (100 / (1 + rs))
        except:
            return 50.0


# ============================================================================
# LAYER 8: LONG SCORING
# ============================================================================

class LongScorer:
    """
    Scores LONG opportunities (0-100)
    
    Weights:
    - RS improvement/strength: 30
    - Trend structure (50/200): 25
    - Accumulation proxy: 20
    - Base quality/volatility: 15
    - Institutional confirmation: 10 (optional)
    """
    
    def score(self, candidate: Dict) -> Tuple[int, List[str]]:
        """
        Score with REDUCED RS sensitivity
        
        RS slope has less dominance; trend structure gets more weight.
        """
        
        metrics = candidate['metrics']
        requirements = candidate.get('adaptive_requirements')
        score = 0
        score_breakdown = []
        
        # 1. RS strength (25 points)
        rs_slope = metrics['rs_slope']
        rs_status = metrics.get('rs_status', 'improving')
        if rs_slope > 0.002:
            rs_score = 25
        elif rs_slope > 0.001:
            rs_score = 22
        elif rs_slope > 0.0005:
            rs_score = 20
        elif rs_slope > 0.0002:
            rs_score = 18
        elif rs_slope > 0:
            rs_score = 16
        elif rs_slope >= -0.0003 and rs_status == "consolidating":
            rs_score = 15
        else:
            rs_score = 10
        
        score += rs_score
        score_breakdown.append(f"RS strength: {rs_score}/25")
        
        # 2. Trend structure (30 points)
        # Score HIGHEST when price is near the 200-MA — the true accumulation zone.
        # Being close to (or just above) the 200-MA means price is at the base where
        # institutional accumulation happens. Scores fall as price extends upward
        # because extended names have already been accumulated — the edge is gone.
        # Max valid entry is 25% above 200-MA (enforced by filter above).
        close_vs_50 = metrics['close_vs_50ma']
        close_vs_200 = metrics['close_vs_200ma']

        if close_vs_200 <= 5:        # At/near 200-MA: prime accumulation zone
            trend_score = 30
        elif close_vs_200 <= 10:     # 5-10% above: early breakout from base
            trend_score = 26
        elif close_vs_200 <= 15:     # 10-15% above: moderate extension, still valid
            trend_score = 20
        elif close_vs_200 <= 20:     # 15-20% above: getting extended, lower conviction
            trend_score = 13
        elif close_vs_200 <= 25:     # 20-25% above: borderline (hard cap is 25%)
            trend_score = 7
        else:                        # >25%: caught by filter, safety net only
            trend_score = 0

        score += trend_score
        score_breakdown.append(f"Trend structure: {trend_score}/30")
        
        # 3. Accumulation quality vs distribution pressure (20 points)
        acc_quality = metrics.get('accumulation_score')
        dist_pressure = metrics.get('distribution_score', 50)

        if acc_quality is None:
            # Fallback for legacy candidates
            acc_ratio = metrics.get('accumulation_ratio', 1.0)
            if acc_ratio > 1.8:
                acc_score = 20
            elif acc_ratio > 1.5:
                acc_score = 18
            elif acc_ratio > 1.3:
                acc_score = 15
            elif acc_ratio > 1.2:
                acc_score = 12
            else:
                acc_score = 8
        else:
            if acc_quality >= 80:
                acc_score = 20
            elif acc_quality >= 70:
                acc_score = 18
            elif acc_quality >= 62:
                acc_score = 15
            elif acc_quality >= 55:
                acc_score = 12
            else:
                acc_score = 7

            if dist_pressure >= 75:
                acc_score = max(0, acc_score - 8)
            elif dist_pressure >= 65:
                acc_score = max(0, acc_score - 4)
            elif dist_pressure >= 58:
                acc_score = max(0, acc_score - 2)

        score += acc_score
        score_breakdown.append(f"Accumulation: {acc_score}/20")
        
        # 4. Base quality (15 points)
        # Simple proxy: RSI in healthy range (not extreme)
        rsi = metrics.get('rsi', 50)
        if 45 <= rsi <= 65:  # Healthy range
            quality_score = 15
        elif 40 <= rsi <= 70:
            quality_score = 12
        else:
            quality_score = 8
        
        score += quality_score
        score_breakdown.append(f"Quality: {quality_score}/15")
        
        # 5. Institutional confirmation (10 points)
        # Would integrate 13F here if available
        # For now, give base score if passed all filters
        inst_score = 10
        score += inst_score
        score_breakdown.append(f"Institutional: {inst_score}/10")

        # BONUS: Consolidation near the 200-MA (base-building at the right level)
        # A stock consolidating within 15% of its 200-MA is building a proper base.
        # A stock "consolidating" at 20%+ above its MA has already run — no bonus.
        if rs_status == "consolidating" and close_vs_200 <= 15:
            bonus = 5
            score += bonus
            score_breakdown.append(f"Base-building bonus: +{bonus}")

        # ADAPTIVE: Adjust score based on data quality
        if requirements:
            adjusted_score = self._apply_quality_adjustment(score, requirements)
            if requirements.sufficiency != DataSufficiency.EXCELLENT:
                score_breakdown.append(
                    f"Quality adjustment: {score} → {adjusted_score} "
                    f"({requirements.sufficiency.value})"
                )
            score = adjusted_score
        
        return score, score_breakdown

    def _apply_quality_adjustment(self, base_score: int,
                                  requirements: AdaptiveRequirements) -> int:
        """Apply data quality adjustment to score"""
        adjusted = int(base_score * requirements.confidence_multiplier)
        return max(0, adjusted)


# ============================================================================
# LAYER 9: LONG RISK GATE
# ============================================================================

class LongRiskGate:
    """Validates LONG opportunities with optional GROWTH MODE"""
    
    ATR_MULTIPLIER_LONG = 2.0
    SWING_LOW_PERIOD = 10
    MIN_STOP_DISTANCE_PCT = 0.01  # Reject sub-1% stops; they are noise, not thesis room.

    # Growth Mode thresholds
    GROWTH_MODE_MIN_SCORE = 85
    GROWTH_MODE_MIN_ACCUMULATION = 2.0
    GROWTH_MODE_MAX_EXPOSURE = 0.25  # 25%
    GROWTH_MODE_MAX_RISK = 0.0125    # 1.25%
    
    def __init__(self, account_size: float, execution_mode: ExecutionMode,
                 growth_mode: bool = False):
        self.account_size = account_size
        self.execution_mode = execution_mode
        self.growth_mode = growth_mode
        self.adaptive_analyzer = create_adaptive_analyzer()
        logger.info(f"Long Risk Gate initialized (Growth Mode: {growth_mode})")
    
    def validate_batch(self, candidates: List[Dict], regime: RegimeContext,
                      data_feed) -> Tuple[List[Opportunity], Dict[str, int]]:
        """Validate batch of long candidates with DETAILED rejection reasons"""
        
        approved = []
        rejections = {}
        
        for candidate in candidates:
            try:
                opportunity, rejection_reason = self._validate_candidate_detailed(
                    candidate, regime, data_feed
                )
                
                if opportunity:
                    candidate['approved'] = True
                    candidate['rejected'] = False
                    candidate['rejection_reason'] = None
                    approved.append(opportunity)
                else:
                    candidate['approved'] = False
                    candidate['rejected'] = True
                    candidate['rejection_reason'] = rejection_reason or 'validation_failed'
                    if rejection_reason:
                        rejections[rejection_reason] = rejections.get(rejection_reason, 0) + 1
                    else:
                        rejections['validation_failed'] = rejections.get('validation_failed', 0) + 1
            
            except Exception as e:
                logger.error(f"Risk validation error: {e}")
                rejections['validation_error'] = rejections.get('validation_error', 0) + 1
        
        return approved, rejections
    
    def _validate_candidate(self, candidate: Dict, regime: RegimeContext,
                           data_feed) -> Optional[Opportunity]:
        """Backward-compatible wrapper"""
        opportunity, _ = self._validate_candidate_detailed(candidate, regime, data_feed)
        return opportunity

    def _validate_candidate_detailed(self, candidate: Dict, regime: RegimeContext,
                                     data_feed) -> Tuple[Optional[Opportunity], Optional[str]]:
        """
        Validate with GROWTH MODE support
        
        Returns:
            (opportunity, rejection_reason)
            If approved: (Opportunity object, None)
            If rejected: (None, "specific_reason")
        """
        
        ticker = candidate['ticker']
        metrics = candidate['metrics']
        score = candidate.get('score', 0)
        score_result = candidate.get('score_result', {})
        entry = metrics['entry_price']
        
        # Get adaptive requirements from candidate
        requirements = candidate.get('adaptive_requirements')
        
        # ADAPTIVE: Get bars based on what we know is available
        if requirements:
            days_to_fetch = min(requirements.bars_available, 30)
        else:
            days_to_fetch = 30

        bars = data_feed.get_daily_bars(ticker, days_back=days_to_fetch, adjustment="all")

        if not bars:
            return (None, "no_bars_for_risk_calc")

        # ADAPTIVE: Use all available bars for ATR if needed
        if len(bars) < 10:
            return (None, "insufficient_bars_for_atr")

        # Calculate ATR with adaptive analyzer
        if requirements:
            atr = self.adaptive_analyzer.calculate_atr(bars, requirements)
        else:
            atr_period = min(14, len(bars) - 1)
            atr = self._calculate_atr(bars[-atr_period:])
        
        if atr == 0:
            return (None, "atr_calculation_failed")

        # Calculate stop (min of 2x ATR or 10-day swing low)
        atr_stop = entry - (atr * self.ATR_MULTIPLIER_LONG)
        
        # ADAPTIVE: Swing low period based on data available
        swing_period = min(self.SWING_LOW_PERIOD, len(bars) - 1)
        if swing_period < 5:
            return (None, "insufficient_bars_for_swing")

        recent_bars = bars[-swing_period:]
        swing_low = min([b['low'] for b in recent_bars])
        
        stop = min(atr_stop, swing_low)
        
        # Simple target (50% above entry for institutional plays)
        target = entry * 1.50
        
        # Calculate returns
        upside_pct = ((target - entry) / entry) * 100
        risk_pct_price = ((entry - stop) / entry) * 100

        if risk_pct_price <= 0:
            return (None, "invalid_stop_loss")
        if risk_pct_price < (self.MIN_STOP_DISTANCE_PCT * 100):
            logger.debug(f"{ticker}: stop too tight ({risk_pct_price:.2f}% < {self.MIN_STOP_DISTANCE_PCT * 100:.2f}%)")
            return (None, f"stop_distance_too_tight_{risk_pct_price:.2f}pct")

        # Risk/Reward
        risk_reward = upside_pct / risk_pct_price
        
        # Minimum R/R requirement
        if risk_reward < 2.0:
            logger.debug(f"{ticker}: R/R too low ({risk_reward:.1f}:1)")
            return (None, f"risk_reward_too_low_{risk_reward:.1f}")
        
        # GROWTH MODE CHECK
        accumulation = metrics.get('accumulation_ratio', 0)
        weighted_growth = bool(score_result.get('growth_mode_eligible'))
        qualifies_for_growth = (
            self.growth_mode and (
                weighted_growth or (
                    score >= self.GROWTH_MODE_MIN_SCORE and
                    accumulation >= self.GROWTH_MODE_MIN_ACCUMULATION
                )
            )
        )

        if qualifies_for_growth:
            max_risk_pct = self.GROWTH_MODE_MAX_RISK
            max_exposure_pct = self.GROWTH_MODE_MAX_EXPOSURE
            hard_cap_exposure = self.GROWTH_MODE_MAX_EXPOSURE
            mode_label = "GROWTH MODE"
            logger.info(
                f"{ticker}: Qualifies for GROWTH MODE "
                f"(Score {score}, Acc {accumulation:.2f})"
            )
        else:
            max_risk_pct = regime.max_risk_per_long_pct
            max_exposure_pct = regime.max_long_exposure_per_position_pct
            hard_cap_exposure = max_exposure_pct * 1.2
            mode_label = "STANDARD"

        position = _safe_position_size(
            account_equity=self.account_size,
            entry_price=entry,
            stop_price=stop,
            score_result={'growth_mode_eligible': qualifies_for_growth}
        )
        if not position.get('valid'):
            return (None, position.get('error', 'position_sizing_failed'))

        shares = position['shares']
        position_value = position['position_value']
        actual_risk_amount = position['risk_at_stop']
        actual_risk_pct = position['risk_pct']
        risk_per_share = entry - stop

        # PRIMARY CHECK: Risk-at-stop (STRICT)
        if actual_risk_pct > max_risk_pct * 100:
            logger.warning(
                f"{ticker}: Risk too high - "
                f"${actual_risk_amount:,.0f} ({actual_risk_pct:.2f}%) "
                f"exceeds max {max_risk_pct*100:.2f}%"
            )
            return (None, f"risk_at_stop_exceeded_{actual_risk_pct:.2f}pct")

        # SECONDARY CHECK: Exposure cap
        exposure_pct = position_value / self.account_size
        if exposure_pct > hard_cap_exposure:
            logger.warning(
                f"{ticker}: Exposure very high - "
                f"${position_value:,.0f} ({exposure_pct*100:.1f}%) "
                f"exceeds hard cap {hard_cap_exposure*100:.0f}%"
            )
            return (None, f"exposure_hard_cap_exceeded_{int(exposure_pct*100)}pct")

        self._print_risk_analysis(
            ticker, entry, stop, target, shares, position_value,
            exposure_pct, actual_risk_amount, actual_risk_pct,
            risk_reward, regime, mode_label, max_exposure_pct
        )
        
        # Determine execution status
        if self.execution_mode == ExecutionMode.LIVE:
            execution_status = "APPROVED"
        else:
            execution_status = "SIMULATED"
        
        # Determine mode
        mode = OpportunityMode.ULTIMATE if qualifies_for_growth else OpportunityMode.ALIGNED
        
        warnings = []
        if qualifies_for_growth:
            warnings.append("🔥 GROWTH MODE - High conviction, aggressive sizing")
        
        opportunity = Opportunity(
            ticker=ticker,
            direction="LONG",
            mode=mode,
            score=score,
            entry_price=entry,
            target_price=target,
            stop_loss=stop,
            shares=shares,
            position_value=position_value,
            risk_amount=actual_risk_amount,
            risk_pct=actual_risk_pct,
            exposure_pct=exposure_pct * 100,
            target_return_pct=upside_pct,
            risk_reward=risk_reward,
            reasons=candidate['reasons'],
            metrics=metrics,
            strategy="VOYAGER",
            grade=score_result.get('grade', 'N/A'),
            growth_mode=qualifies_for_growth,
            tier_breakdown=score_result.get('tier_breakdown', {}),
            confidence_summary=score_result.get('confidence_summary', {}),
            execution_status=execution_status,
            warnings=warnings
        )
        
        logger.info(
            f"{ticker}: ✅ APPROVED ({mode_label}) - "
            f"Risk {actual_risk_pct:.2f}% (max {max_risk_pct*100:.2f}%), "
            f"Exposure {exposure_pct*100:.1f}% (cap {max_exposure_pct*100:.0f}%), "
            f"R/R {risk_reward:.1f}:1"
        )
        return (opportunity, None)
    
    def _calculate_atr(self, bars: List) -> float:
        """Calculate ATR"""
        
        if len(bars) < 2:
            return 0
        
        try:
            true_ranges = []
            for i in range(1, len(bars)):
                high = bars[i]['high']
                low = bars[i]['low']
                prev_close = bars[i-1]['close']
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            return statistics.mean(true_ranges)
        except:
            return 0

    def _print_risk_analysis(self, ticker: str, entry: float, stop: float,
                             target: float, shares: int, position_value: float,
                             exposure_pct: float, risk_amount: float,
                             risk_pct: float, risk_reward: float,
                             regime: RegimeContext, mode_label: str,
                             guideline_exposure: float):
        """Print COMPLETE risk analysis with Growth Mode context"""
        print("\n" + "="*70)
        print(f"🔍 RISK ANALYSIS: {ticker} ({mode_label})")
        print("="*70)

        print(f"\n📊 POSITION DETAILS:")
        print(f"   Entry: ${entry:.2f}")
        print(f"   Target: ${target:.2f} ({((target-entry)/entry*100):+.1f}%)")
        print(f"   Stop: ${stop:.2f} ({((stop-entry)/entry*100):+.1f}%)")
        print(f"   Stop Distance: ${abs(entry-stop):.2f}")

        print(f"\n💼 POSITION SIZING:")
        print(f"   Shares Required: {shares:,}")
        print(f"   Position Notional: ${position_value:,.0f} ({exposure_pct*100:.1f}% of account)")
        print(f"   Max Allowed Notional: ${self.account_size * guideline_exposure:,.0f} ({guideline_exposure*100:.0f}% guideline)")

        print(f"\n🎯 RISK ASSESSMENT:")
        print(f"   Risk at Stop: ${risk_amount:,.0f} ({risk_pct:.2f}% of account)")
        if mode_label == "GROWTH MODE":
            print(f"   Max Allowed Risk: ${self.account_size * self.GROWTH_MODE_MAX_RISK:,.0f} ({self.GROWTH_MODE_MAX_RISK*100:.2f}%)")
        else:
            print(f"   Max Allowed Risk: ${self.account_size * regime.max_risk_per_long_pct:,.0f} "
                  f"({regime.max_risk_per_long_pct*100:.2f}%)")
        print(f"   Risk/Reward: {risk_reward:.1f}:1")

        print(f"\n✅ VALIDATION:")
        if mode_label == "GROWTH MODE":
            risk_ok = risk_pct <= self.GROWTH_MODE_MAX_RISK * 100
        else:
            risk_ok = risk_pct <= regime.max_risk_per_long_pct * 100
        exposure_ok = exposure_pct <= guideline_exposure
        print(f"   Risk Check: {'✅ PASS' if risk_ok else '❌ FAIL'}")
        print(f"   Exposure Check: {'✅ PASS' if exposure_ok else '⚠️  HIGH'}")
        print(f"   R/R Check: {'✅ PASS' if risk_reward >= 2.0 else '❌ FAIL'}")
        
        if mode_label == "GROWTH MODE":
            print(f"\n🔥 GROWTH MODE ACTIVATED:")
            print(f"   Max Exposure: {self.GROWTH_MODE_MAX_EXPOSURE*100:.0f}% (vs standard {regime.max_long_exposure_per_position_pct*100:.0f}%)")
            print(f"   Max Risk: {self.GROWTH_MODE_MAX_RISK*100:.2f}% (vs standard {regime.max_risk_per_long_pct*100:.2f}%)")
        print("="*70 + "\n")


# ============================================================================
# FUNNEL TRACKING
# ============================================================================

class OpportunityFunnel:
    """Track opportunity filtering through pipeline"""
    
    def __init__(self, opportunity_type: str):
        self.opportunity_type = opportunity_type
        self.stages: List[FunnelStats] = []
    
    def record_stage(self, stage_name: str, input_count: int,
                    output_count: int, rejection_reasons: Dict[str, int] = None):
        """Record filtering stage"""
        
        rejected = input_count - output_count
        
        stage_stats = FunnelStats(
            stage=stage_name,
            input_count=input_count,
            output_count=output_count,
            rejected_count=rejected,
            rejection_reasons=rejection_reasons or {}
        )
        
        self.stages.append(stage_stats)
        
        logger.info(f"{self.opportunity_type} - {stage_name}: "
                   f"{output_count}/{input_count} passed ({stage_stats.pass_rate:.1f}%)")
    
    def print_funnel_report(self):
        """Print detailed funnel analysis"""
        
        print("\n" + "="*70)
        print(f"📊 {self.opportunity_type} OPPORTUNITY FUNNEL")
        print("="*70)
        
        if not self.stages:
            print("No stages recorded")
            return
        
        for i, stage in enumerate(self.stages, 1):
            print(f"\n{i}. {stage.stage}")
            print(f"   Input: {stage.input_count}")
            print(f"   Output: {stage.output_count}")
            print(f"   Rejected: {stage.rejected_count}")
            print(f"   Pass Rate: {stage.pass_rate:.1f}%")
            
            if stage.rejection_reasons:
                print(f"   Rejection Breakdown:")
                for reason, count in sorted(stage.rejection_reasons.items(),
                                           key=lambda x: x[1], reverse=True):
                    print(f"      {reason}: {count}")
        
        if self.stages:
            first_stage = self.stages[0]
            last_stage = self.stages[-1]
            
            overall_pass_rate = (last_stage.output_count / first_stage.input_count * 100) if first_stage.input_count > 0 else 0
            
            print("\n" + "-"*70)
            print(f"OVERALL: {last_stage.output_count}/{first_stage.input_count} "
                  f"survived funnel ({overall_pass_rate:.1f}%)")
            print("="*70 + "\n")


# ============================================================================
# COMPLETE VOYAGER PRODUCTION v2.0
# ============================================================================

class VoyagerProductionV2Complete:
    """
    Complete Voyager Production System
    
    The OUTLIER system for 10-20x opportunities

    GROWTH MODE:
    For accounts $20k-$50k, enable aggressive scaling on high-conviction plays.
    When enabled, allows up to 25% exposure (vs 15-18% standard) for:
    - Score 85+ (highest conviction)
    - Accumulation 2.0+ (strong institutional buying)
    - Risk at stop <= 1.25% (controlled risk)
    """
    
    DEFAULT_EMAIL = "hedayat.raastin@gmail.com"
    DEFAULT_ACCOUNT_SIZE = 100000
    
    def __init__(self, account_size: float = None, verbose: bool = False,
                 growth_mode: bool = False):
        """Initialize complete Voyager"""
        
        self.account_size = account_size or self.DEFAULT_ACCOUNT_SIZE
        self.account_equity = self.account_size
        self.verbose = verbose
        self.growth_mode = growth_mode
        self.regime = None
        self.triple_tickers: Set[str] = set()
        
        # Determine execution mode
        self.execution_mode = self._determine_execution_mode()
        
        if verbose:
            print("\n" + "="*70)
            print("🚀 VOYAGER PRODUCTION v2.0 COMPLETE")
            print("="*70)
            print("The OUTLIER System - 10-20x Opportunities")
            print(f"Execution Mode: {self.execution_mode.value}")
            print(f"Account Size: ${self.account_size:,.0f}")
            if growth_mode:
                print("🔥 GROWTH MODE: ENABLED")
                print("   High-conviction plays: Up to 25% exposure")
                print("   Requirements: Score 85+, Accumulation 2.0+, Risk <=1.25%")
            else:
                print("📊 STANDARD MODE")
                print("   Conservative exposure caps (15-18%)")
            print("="*70 + "\n")
        
        # Initialize all layers
        self.data_feed = AlpacaDataFeed()
        self.universe_validator = UniverseValidator(self.data_feed)
        self.data_gate = DataIntegrityGate(self.data_feed, self.execution_mode)
        self.regime_analyzer = RegimeAnalyzer(self.data_feed)
        
        # Discovery & Scoring
        self.short_discovery = ShortDiscovery(self.data_feed)
        self.short_scorer = ShortScorer()
        self.short_risk_gate = ShortRiskGate(
            self.account_size, self.execution_mode, growth_mode=growth_mode
        )
        
        self.long_discovery = LongDiscovery(self.data_feed)
        self.long_scorer = LongScorer()
        self.long_risk_gate = LongRiskGate(
            self.account_size, self.execution_mode, growth_mode=growth_mode
        )
        
        logger.info("✅ Voyager Production v2.0 Complete initialized")

    @staticmethod
    def _voyager_long_regime_compatible(regime: Optional[RegimeContext]) -> bool:
        """
        Voyager longs can operate in FAVORABLE and CAUTION regimes.

        For a 6-18 month strategy, elevated volatility should reduce conviction,
        not act like a blanket veto. Only EXTREME / DEFENSIVE / UNKNOWN regimes
        are treated as incompatible.
        """
        if regime is None:
            return False
        if getattr(regime, "recommendation", "UNKNOWN") not in {"FAVORABLE", "CAUTION"}:
            return False
        return getattr(regime, "vix_regime", None) != VIXRegime.EXTREME

    @staticmethod
    def _voyager_inst_thresholds(regime: Optional[RegimeContext]) -> Tuple[float, float]:
        """
        Calibrate the accumulation proxy to the current volatility regime.

        In stressed tape, strong names will often show only relative accumulation,
        not the absolute accumulation readings seen in calm markets.
        """
        vix_now = float(getattr(regime, "vix", 0.0) or 0.0)
        if vix_now > 30:
            return (40.0, 1.05)
        if vix_now > 20:
            return (50.0, 1.10)
        return (60.0, 1.20)

    @classmethod
    def _voyager_inst_buying_signal(
        cls,
        accumulation_score: float,
        accumulation_ratio: float,
        regime: Optional[RegimeContext],
    ) -> bool:
        inst_threshold, ratio_threshold = cls._voyager_inst_thresholds(regime)
        return (
            float(accumulation_score or 0.0) >= inst_threshold
            or float(accumulation_ratio or 0.0) > ratio_threshold
        )

    @staticmethod
    def _voyager_long_target_price(entry: float, fundamentals: Optional[Dict[str, Any]]) -> Optional[float]:
        if not entry or entry <= 0:
            return None
        fundamentals = fundamentals or {}
        analyst_target = fundamentals.get("analyst_target_price")
        try:
            analyst_target = float(analyst_target)
        except (TypeError, ValueError):
            analyst_target = None
        if analyst_target and analyst_target > entry * 1.10:
            return min(analyst_target, entry * 3.0)
        return entry * 1.50

    def _attach_voyager_long_trade_levels(self, ticker: str, candidate: Dict) -> Tuple[float, Optional[float], Optional[float], float]:
        """
        Attach execution levels to long candidates before scoring so rejects keep
        complete RR telemetry.
        """
        metrics = candidate.get('metrics', {}) if isinstance(candidate.get('metrics'), dict) else {}
        entry = metrics.get('entry_price', 0)
        try:
            entry = float(entry)
        except Exception:
            entry = 0.0

        risk_reward, stop_loss = self._estimate_long_risk_reward(ticker, candidate)
        # target_price is derived inside _estimate_long_risk_reward (analyst-based or fallback 1.50x).
        # Reconstruct here for attaching to the candidate dict; uses the same fundamentals cache.
        if entry > 0:
            fundamentals = self._get_voyager_fundamentals(ticker)
            target_value = self._voyager_long_target_price(entry, fundamentals)
            target_price = round(target_value, 4) if target_value is not None else None
        else:
            target_price = None
        long_risk_gate = getattr(self, "long_risk_gate", None)
        min_stop_distance_pct = float(
            getattr(long_risk_gate, "MIN_STOP_DISTANCE_PCT", LongRiskGate.MIN_STOP_DISTANCE_PCT)
            or LongRiskGate.MIN_STOP_DISTANCE_PCT
        )
        if (
            entry > 0
            and stop_loss is not None
            and ((entry - float(stop_loss)) / entry) < min_stop_distance_pct
        ):
            risk_reward = 0.0

        candidate['risk_reward'] = float(risk_reward or 0.0)
        candidate['stop_loss'] = stop_loss if stop_loss is not None else candidate.get('stop_loss')
        candidate['target_price'] = target_price if target_price is not None else candidate.get('target_price')
        if isinstance(metrics, dict):
            metrics['risk_reward'] = float(risk_reward or 0.0)
            metrics['stop_loss'] = stop_loss
            metrics['target_price'] = target_price
            metrics['entry_price'] = entry if entry > 0 else metrics.get('entry_price')

        return entry, stop_loss, target_price, float(risk_reward or 0.0)

    def _get_voyager_fundamentals(self, ticker: str) -> Dict:
        """Fetch Voyager fundamentals once per cycle and fail closed on missing data."""
        cache = getattr(self, "_voyager_fundamentals_cache", None)
        if cache is None:
            cache = {}
            self._voyager_fundamentals_cache = cache

        ticker = str(ticker or "").upper().strip()
        if ticker in cache:
            return cache[ticker]

        fundamentals = {
            'revenue_growth_yoy': None,
            'analyst_target_price': None,
            'gross_margin': 0.0,
            'fcf_positive': False,
            'fcf_inflection': False,
            'margin_improving': False,
            'rule_of_40_pass': False,
            'debt_manageable': False,
            'valuation_reasonable': False,
            'market_cap': None,
            'sector': None,
            'industry': None,
            'inst_ownership_high': False,
            'guidance_trend': 'stable',
            'data_source': None,
            'data_quality': 'error',
        }

        if HAS_FUNDAMENTAL_FETCHER and FundamentalDataFetcher is not None:
            try:
                fetched = FundamentalDataFetcher.get_voyager_fundamentals(ticker) or {}
                for key, value in fetched.items():
                    if key in fundamentals and value is not None:
                        fundamentals[key] = value
            except Exception as exc:
                logger.debug("Voyager fundamentals unavailable for %s: %s", ticker, exc)

        cache[ticker] = fundamentals
        return fundamentals

    def _estimate_long_risk_reward(self, ticker: str, candidate: Dict) -> Tuple[float, float]:
        """Estimate current long R/R using the same stop/target logic as the risk gate."""
        metrics = candidate.get('metrics', {})
        entry = metrics.get('entry_price', 0)
        requirements = candidate.get('adaptive_requirements')

        if entry <= 0:
            return (0.0, 0.0)

        days_to_fetch = min(getattr(requirements, 'bars_available', 30), 30) if requirements else 30
        bars = self.data_feed.get_daily_bars(ticker, days_back=days_to_fetch, adjustment="all")
        if not bars or len(bars) < 10:
            return (0.0, 0.0)

        atr = self.long_risk_gate.adaptive_analyzer.calculate_atr(bars, requirements) if requirements else self.long_risk_gate._calculate_atr(bars[-14:])
        if atr <= 0:
            return (0.0, 0.0)

        swing_period = min(self.long_risk_gate.SWING_LOW_PERIOD, len(bars) - 1)
        swing_low = min(b['low'] for b in bars[-swing_period:])
        stop = min(entry - (atr * self.long_risk_gate.ATR_MULTIPLIER_LONG), swing_low)
        if stop >= entry:
            return (0.0, stop)
        min_stop_distance_pct = float(getattr(self.long_risk_gate, "MIN_STOP_DISTANCE_PCT", 0.01) or 0.01)
        if ((entry - stop) / entry) < min_stop_distance_pct:
            return (0.0, stop)

        # Use analyst consensus price target when available and meaningful.
        # A fixed 50% target underestimates R/R for high-conviction growth stocks
        # where sell-side consensus implies 80-150% upside.
        # Bounds: analyst target must be > 10% above entry (sanity) and capped at 3x
        # entry so one outlier analyst doesn't create an unrealistic target.
        fundamentals = self._get_voyager_fundamentals(ticker)
        target = self._voyager_long_target_price(entry, fundamentals)
        if target is None:
            return (0.0, stop)

        risk = entry - stop
        reward = target - entry
        if risk <= 0:
            return (0.0, stop)

        return (reward / risk, stop)

    def _estimate_short_risk_reward(self, ticker: str, candidate: Dict) -> Tuple[float, float, float]:
        """Estimate current short R/R using the same stop/target logic as the short risk gate."""
        metrics = candidate.get('metrics', {})
        entry = metrics.get('entry_price', 0)
        requirements = candidate.get('adaptive_requirements')
        ma_200 = metrics.get('ma_200')
        distance_from_ma = metrics.get('pct_above_200ma')

        if entry <= 0 or ma_200 is None or distance_from_ma is None:
            return (0.0, 0.0, 0.0)

        days_to_fetch = min(getattr(requirements, 'bars_available', 30), 30) if requirements else 30
        bars = self.data_feed.get_daily_bars(ticker, days_back=days_to_fetch, adjustment="all")
        if not bars or len(bars) < 10:
            return (0.0, 0.0, 0.0)

        atr = self.short_risk_gate.adaptive_analyzer.calculate_atr(bars, requirements) if requirements else self.short_risk_gate._calculate_atr(bars[-14:])
        if atr <= 0:
            return (0.0, 0.0, 0.0)

        swing_period = min(self.short_risk_gate.SWING_HIGH_PERIOD, len(bars) - 1)
        swing_high = max(b['high'] for b in bars[-swing_period:])
        stop = max(entry + (atr * self.short_risk_gate.ATR_MULTIPLIER_SHORT), swing_high)

        if distance_from_ma > 40:
            target = ma_200
        elif distance_from_ma > 25:
            target = entry - (entry - ma_200) * 0.5
        else:
            target = entry * 0.90

        risk = stop - entry
        reward = entry - target
        if risk <= 0 or reward <= 0:
            return (0.0, stop, target)

        return (reward / risk, stop, target)

    @staticmethod
    def _format_pathway_debug_value(value) -> str:
        if value is None:
            return "None"
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, (int, float)):
            return f"{float(value):.2f}"
        return str(value)

    def _log_voyager_pathway_failure_debug(
        self,
        ticker: str,
        checks: Dict,
        pathway_info: Optional[Dict],
    ) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        pathway_info = pathway_info or {}
        if pathway_info.get("qualified", True):
            return

        growth = self._format_pathway_debug_value(checks.get("revenue_growth_strong"))
        inflection_growth = self._format_pathway_debug_value(checks.get("revenue_growth_moderate"))
        inflection_fcf = self._format_pathway_debug_value(checks.get("fcf_inflection"))
        inflection_margin = self._format_pathway_debug_value(checks.get("margin_expanding"))
        leverage_rule40 = self._format_pathway_debug_value(checks.get("rule_of_40_pass"))
        leverage_growth = self._format_pathway_debug_value(checks.get("revenue_growth_base"))
        quality_margin = self._format_pathway_debug_value(checks.get("gross_margin_exceptional"))
        quality_fcf = self._format_pathway_debug_value(checks.get("fcf_positive"))
        quality_growth = self._format_pathway_debug_value(checks.get("revenue_growth_minimal"))

        logger.debug(
            "VOYAGER PATHWAY FAIL — %s: "
            "GROWTH=%s (need 0.15) | "
            "GROWTH_INFLECTION=revenue=%s (need 0.10)/fcf=%s/margin=%s | "
            "OPERATING_LEVERAGE=rule40=%s/revenue=%s (need 0.08) | "
            "QUALITY=gross_margin=%s (need 0.65)/fcf=%s/revenue=%s (need 0.05)",
            ticker,
            growth,
            inflection_growth,
            inflection_fcf,
            inflection_margin,
            leverage_rule40,
            leverage_growth,
            quality_margin,
            quality_fcf,
            quality_growth,
        )

    def _build_voyager_long_score(self, candidate: Dict, regime: RegimeContext) -> Dict:
        """Translate Voyager long candidate metrics into weighted scoring inputs."""
        ticker = candidate['ticker']
        metrics = candidate.get('metrics', {})
        _, _, _, risk_reward = self._attach_voyager_long_trade_levels(ticker, candidate)
        close_vs_50 = metrics.get('close_vs_50ma', 0)
        close_vs_200 = metrics.get('close_vs_200ma', 0)
        rs_slope = metrics.get('rs_slope', 0)
        accumulation = metrics.get('accumulation_ratio', 0)
        accumulation_score = metrics.get('accumulation_score', 0)
        distribution_score = metrics.get('distribution_score', 50)
        flow_regime = metrics.get('flow_regime', 'MIXED')

        # Real options signal — neutral fallback when chain data unavailable
        opt_sig = _get_options_score_adj(ticker) if _HAS_OPTIONS_SIGNAL else {
            "adj": 0.0, "pcr": None, "gamma": "NEUTRAL", "source": "unavailable", "note": "module unavailable"
        }
        pcr = opt_sig.get("pcr")
        # options_flow must come from real chain data. Do not backfill it with
        # price/volume proxies that masquerade as options intelligence.
        options_flow_real = (pcr < 0.85) if pcr is not None else False
        fundamentals = self._get_voyager_fundamentals(ticker)

        scanner_data = {
            'risk_reward': risk_reward,
            'rs_spy': rs_slope,
            'inst_buying': self._voyager_inst_buying_signal(accumulation_score, accumulation, regime),
            'regime_compatible': self._voyager_long_regime_compatible(regime),
            'revenue_growth_yoy': fundamentals.get('revenue_growth_yoy'),
            'gross_margin': fundamentals.get('gross_margin', 0.0),
            'fcf_positive': fundamentals.get('fcf_positive', False),
            'fcf_inflection': fundamentals.get('fcf_inflection', False),
            'margin_improving': fundamentals.get('margin_improving', False),
            'rule_of_40_pass': fundamentals.get('rule_of_40_pass', False),
            'debt_manageable': fundamentals.get('debt_manageable', False),
            'valuation_reasonable': fundamentals.get('valuation_reasonable', False),
            'inst_ownership_high': fundamentals.get('inst_ownership_high', False),
            'guidance_trend': fundamentals.get('guidance_trend', 'stable'),
            'avg_volume': metrics.get('avg_volume', 0),
            # Voyager should rank names earlier in the base-building cycle, not
            # after they are already materially extended from their accumulation zone.
            'true_accumulation_zone': metrics.get('true_accumulation_zone', False),
            'zone_score': metrics.get('zone_score', 0.0),
            'close_vs_200ma': close_vs_200,
            'sector_rs_score': metrics.get('sector_rs_score'),
            'sector': metrics.get('sector') or fundamentals.get('sector') or 'UNKNOWN',
            'sector_wave': close_vs_200 > 10,
            # External-data bonuses fail closed until real feeds are integrated.
            'insider_buying': False,
            'analyst_upgrades': False,
            'call_flow': options_flow_real,
            'positive_news': False,
        }
        checks = StrategyCheckMapper.map_voyager_checks(scanner_data) if StrategyCheckMapper else {}
        metadata = StrategyCheckMapper.build_metadata(scanner_data, 'VOYAGER') if StrategyCheckMapper else {}
        regime_ctx = {"vix": float(getattr(regime, "vix", 0.0) or 0.0)}
        score_result = calculate_strategy_score('VOYAGER', checks, metadata, regime_context=regime_ctx)
        self._log_voyager_pathway_failure_debug(
            ticker,
            checks,
            score_result.get("pathway_qualification"),
        )
        # Apply options score adjustment (+/-5 boost/penalty, not a veto)
        if opt_sig["adj"] != 0.0:
            score_result = apply_post_score_adjustment('VOYAGER', score_result, opt_sig["adj"])
            score_result.setdefault("options_signal", opt_sig)
            logger.debug(f"Voyager {ticker}: options adj {opt_sig['adj']:+.1f} ({opt_sig['note']}) → score {score_result['normalized_score']:.1f}")
        return score_result

    def _build_voyager_short_score(self, candidate: Dict, regime: RegimeContext) -> Dict:
        """Translate Voyager short candidate metrics into weighted short-scoring inputs."""
        ticker = candidate['ticker']
        metrics = candidate.get('metrics', {})
        risk_reward, _, _ = self._estimate_short_risk_reward(ticker, candidate)
        criteria = metrics.get('criteria_details', {})

        # Real options signal — put-bias = PCR > 1.20; fallback to exhaustion proxy
        opt_sig = _get_options_score_adj(ticker) if _HAS_OPTIONS_SIGNAL else {
            "adj": 0.0, "pcr": None, "gamma": "NEUTRAL", "source": "unavailable", "note": "module unavailable"
        }
        pcr = opt_sig.get("pcr")
        options_put_bias_real = (pcr > 1.20) if pcr is not None else criteria.get('exhaustion', False)

        checks = {
            'risk_reward_ratio': risk_reward,
            'relative_strength_spy': -10.0 if criteria.get('rs_rollover') else 0.0,
            'institutional_distribution': metrics.get('vol_divergence_ratio', 1.0) < 1.0,
            'fundamental_deterioration': criteria.get('confirmation', False),
            'vix_regime_favorable': regime.vix >= 20,
            'volume_on_decline': metrics.get('vol_divergence_ratio', 1.0) < 0.9,
            'broken_support': criteria.get('confirmation', False),
            'sector_weakness': criteria.get('rs_rollover', False),
            'short_interest_low': True,
            'price_below_moving_averages': True,
            'downtrend_intact': criteria.get('overextended', False) and criteria.get('rs_rollover', False),
            'analyst_downgrades': criteria.get('rs_rollover', False),
            'options_put_bias': options_put_bias_real,
            'insider_selling': metrics.get('vol_divergence_ratio', 1.0) < 0.85,
            'accounting_concerns': False,
            'regulatory_risk': False,
        }
        metadata = {
            'institutional_data_age_days': 30,
            'sector_bonus_eligible': metrics.get('criteria_met', 0) >= 4,
            'sector': 'UNKNOWN',
        }
        return calculate_strategy_score('SHORT', checks, metadata)

    def _calculate_legacy_score(self, candidate: Dict) -> int:
        """Legacy single-candidate score for compatibility evaluation."""
        score, _ = self.long_scorer.score(candidate)
        return score

    def _calculate_opportunity_score(self, *args, **kwargs) -> int:
        """Compatibility wrapper used by single-ticker evaluation."""
        candidate = kwargs.get('candidate')
        if candidate is not None:
            return self._calculate_legacy_score(candidate)
        if args and isinstance(args[0], dict):
            return self._calculate_legacy_score(args[0])
        return 0

    def evaluate_opportunity(self, ticker: str, reason: str):
        """
        Evaluate opportunity with enhanced weighted scoring

        Returns:
            Dict with approval decision and details
        """
        try:
            logger.info(f"\n{'='*80}")
            logger.info(f"🎯 EVALUATING: {ticker}")
            logger.info(f"   Reason: {reason}")
            logger.info(f"{'='*80}")

            bars = self.data_feed.get_daily_bars(ticker, days_back=252, adjustment="all")
            if not bars:
                return {'approved': False, 'reason': 'no_data'}

            requirements = self.long_discovery.adaptive_analyzer.analyze_data_requirements(bars)
            if self.long_discovery.adaptive_analyzer.should_skip_ticker(requirements):
                return {'approved': False, 'reason': 'insufficient_data'}

            candidate = self.long_discovery._analyze_long_candidate(ticker, bars, requirements)
            if not candidate.get('qualified'):
                return {'approved': False, 'reason': candidate.get('rejection_reason', 'not_qualified')}

            self.regime = self.regime_analyzer.analyze_regime()
            regime = self.regime
            legacy_score = self._calculate_opportunity_score(candidate=candidate)
            candidate['score'] = legacy_score
            metrics = candidate.get('metrics', {})
            current_price, stop_price, target_price, r_r_ratio = self._attach_voyager_long_trade_levels(ticker, candidate)
            entry_price = current_price
            calculated_stop = stop_price
            calculated_target = target_price
            rs_spy = metrics.get('rs_slope', 0.0)
            accumulation_score = metrics.get('accumulation_score', 0.0)
            distribution_score = metrics.get('distribution_score', 50.0)
            institutional_buying = self._voyager_inst_buying_signal(
                accumulation_score,
                metrics.get('accumulation_ratio', 0.0),
                regime,
            )
            fundamentals_deteriorating = metrics.get('rsi', 50) >= 75
            avg_volume = statistics.mean(b['volume'] for b in bars[-20:]) if len(bars) >= 20 else bars[-1]['volume']
            sector_strength = metrics.get('close_vs_200ma', 0) > 0
            uptrend_quality = metrics.get('close_vs_50ma', 0) > 0 and metrics.get('close_vs_200ma', 0) > 0
            ma_50 = metrics.get('ma_50', current_price)
            ma_200 = metrics.get('ma_200', current_price)
            above_mas = current_price > ma_50 and current_price > ma_200
            earnings_quality = metrics.get('rsi', 50) < 75
            clean_support_resistance = abs(metrics.get('close_vs_50ma', 0)) < 20
            timeframe_alignment = metrics.get('rs_status') in {"improving", "consolidating"}
            insider_buying = False
            analyst_upgrades = False
            call_flow = False
            positive_news = False
            sector = 'Unknown'

            # NEW: Fetch fundamental data
            fundamentals = {}
            if HAS_FUNDAMENTAL_FETCHER:
                try:
                    logger.info(f"📊 Fetching fundamentals for {ticker}...")
                    fundamentals = FundamentalDataFetcher.get_voyager_fundamentals(ticker)
                except Exception as e:
                    logger.warning(f"⚠️ Could not fetch fundamentals for {ticker}: {e}")
                    fundamentals = {
                        'revenue_growth_yoy': None,
                        'gross_margin': 0,
                        'fcf_positive': False,
                        'fcf_inflection': False,
                        'margin_improving': False,
                        'rule_of_40_pass': False,
                        'debt_manageable': True,
                        'valuation_reasonable': True,
                        'inst_ownership_high': False,
                        'guidance_trend': 'stable',
                        'analyst_target_price': None,
                    }

            # Build scanner_data with fundamentals
            scanner_data = {
                # Existing technical/flow fields...
                'risk_reward': r_r_ratio if 'r_r_ratio' in locals() else 0,
                'rs_spy': rs_spy if 'rs_spy' in locals() else 0,
                'inst_buying': institutional_buying if 'institutional_buying' in locals() else False,
                'regime_compatible': self._voyager_long_regime_compatible(self.regime),

                # NEW: Fundamental fields
                'revenue_growth_yoy': fundamentals.get('revenue_growth_yoy'),
                'gross_margin': fundamentals.get('gross_margin'),
                'fcf_positive': fundamentals.get('fcf_positive'),
                'fcf_inflection': fundamentals.get('fcf_inflection'),
                'margin_improving': fundamentals.get('margin_improving'),
                'rule_of_40_pass': fundamentals.get('rule_of_40_pass'),
                'debt_manageable': fundamentals.get('debt_manageable'),
                'valuation_reasonable': fundamentals.get('valuation_reasonable'),
                'inst_ownership_high': fundamentals.get('inst_ownership_high'),
                'guidance_trend': fundamentals.get('guidance_trend'),

                # Rest of existing fields...
                'avg_volume': avg_volume,
                'true_accumulation_zone': metrics.get('true_accumulation_zone', False),
                'zone_score': metrics.get('zone_score', 0.0),
                'close_vs_200ma': metrics.get('close_vs_200ma', 0.0),
                'sector_rs_score': metrics.get('sector_rs_score'),
                'uptrend_quality': uptrend_quality if 'uptrend_quality' in locals() else False,
                'above_mas': above_mas if 'above_mas' in locals() else False,
                'earnings_quality': earnings_quality if 'earnings_quality' in locals() else False,
                'clean_levels': clean_support_resistance if 'clean_support_resistance' in locals() else False,
                'timeframe_align': timeframe_alignment if 'timeframe_alignment' in locals() else False,
                'insider_buying': insider_buying if 'insider_buying' in locals() else False,
                'analyst_upgrades': analyst_upgrades if 'analyst_upgrades' in locals() else False,
                'call_flow': call_flow if 'call_flow' in locals() else False,
                'positive_news': positive_news if 'positive_news' in locals() else False,
                'sector': metrics.get('sector') or fundamentals.get('sector') or sector,
                'sector_wave': ticker in self.triple_tickers,
                'inst_data_age_days': 30
            }

            if HAS_ENHANCED_SCORING:
                checks = StrategyCheckMapper.map_voyager_checks(scanner_data)
                metadata = StrategyCheckMapper.build_metadata(scanner_data, 'VOYAGER')
                score_result = calculate_strategy_score('VOYAGER', checks, metadata)
                self._log_voyager_pathway_failure_debug(
                    ticker,
                    checks,
                    score_result.get("pathway_qualification"),
                )
                logger.info(f"\n📊 SCORING COMPARISON:")
                logger.info(f"   Legacy Score: {legacy_score}/100")
                logger.info(f"   Weighted Score: {score_result['normalized_score']:.1f}/100 ({score_result['grade']})")
                logger.info(f"   Recommendation: {score_result['recommendation']}")
                logger.info(f"   Growth Mode: {'YES 🚀' if score_result['growth_mode_eligible'] else 'No'}")
                pathway_info = score_result.get('pathway_qualification', {}) or {}
                if pathway_info:
                    logger.info(f"   Primary Pathway: {pathway_info.get('primary_pathway') or '—'}")
                    logger.info(f"   Pathways Used: {pathway_info.get('pathways_passed', [])}")
                logger.info(f"\n   Tier Breakdown:")
                logger.info(f"      Tier 1: {score_result['tier_1_score']:.0f}/{score_result['tier_1_max']} ({score_result['tier_breakdown']['tier_1']['pct']:.0f}%)")
                logger.info(f"      Tier 2: {score_result['tier_2_score']:.0f} ({score_result['tier_breakdown']['tier_2']['pct']:.0f}%)")
                logger.info(f"      Tier 3: {score_result['tier_3_score']:.0f} ({score_result['tier_breakdown']['tier_3']['pct']:.0f}%)")
                logger.info(f"      Tier 4: {score_result['tier_4_score']:.0f} ({score_result['tier_breakdown']['tier_4']['pct']:.0f}%)")

                if score_result['recommendation'] in ['STRONG BUY', 'BUY']:
                    logger.info(f"✅ APPROVED by weighted scoring")
                    position = enhanced_position_size(
                        account_equity=self.account_equity,
                        entry_price=current_price if 'current_price' in locals() else entry_price,
                        stop_price=stop_price if 'stop_price' in locals() else calculated_stop,
                        score_result=score_result
                    )

                    if position['valid']:
                        return {
                            'approved': True,
                            'ticker': ticker,
                            'strategy': 'VOYAGER',
                            'score': score_result['normalized_score'],
                            'legacy_score': legacy_score,
                            'grade': score_result['grade'],
                            'shares': position['shares'],
                            'entry': current_price if 'current_price' in locals() else entry_price,
                            'stop': stop_price if 'stop_price' in locals() else calculated_stop,
                            'target': target_price if 'target_price' in locals() else calculated_target,
                            'position_pct': position['position_pct'],
                            'risk_pct': position['risk_pct'],
                            'growth_mode': score_result['growth_mode_eligible'],
                            'confidence_summary': score_result['confidence_summary'],
                            'tier_breakdown': score_result['tier_breakdown'],
                            'pathway_qualification': score_result.get('pathway_qualification', {})
                        }
                    else:
                        logger.warning(f"❌ Position sizing failed: {position['error']}")
                        return {'approved': False, 'reason': 'invalid_position_size'}

                elif score_result['recommendation'] == 'CONSIDER':
                    logger.info(f"⚠️  WATCHLIST candidate (score: {score_result['normalized_score']:.1f})")
                    return {
                        'approved': False,
                        'watchlist': True,
                        'score': score_result['normalized_score'],
                        'reason': 'consider_grade'
                    }

                else:
                    logger.info(f"❌ REJECTED: {score_result.get('rejection_reason', 'Score too low')}")
                    return {
                        'approved': False,
                        'score': score_result['normalized_score'],
                        'reason': score_result.get('rejection_reason', 'score_too_low')
                    }

            if legacy_score < 75:
                logger.info(f"📊 Using legacy scoring: {legacy_score}/100")
                return {'approved': False, 'reason': 'legacy_score_too_low'}

            logger.info(f"📊 Using legacy scoring: {legacy_score}/100")
            return {'approved': True, 'legacy_score': legacy_score}

        except Exception as e:
            logger.error(f"❌ Error evaluating {ticker}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {'approved': False, 'reason': f'error: {e}'}
    
    def _determine_execution_mode(self) -> ExecutionMode:
        """Determine execution mode"""
        is_weekend = datetime.now().weekday() >= 5
        return ExecutionMode.WEEKEND if is_weekend else ExecutionMode.LIVE
    
    def scan_complete(
        self,
        raw_universe_override: Optional[List[str]] = None,
        suppress_long_pipeline: bool = False,
    ) -> Dict:
        """
        Run complete production scan with FULL pipelines
        
        Returns comprehensive results
        """
        
        print("\n" + "="*70)
        print("🔍 VOYAGER PRODUCTION COMPLETE SCAN")
        print("="*70)
        print(f"Mode: {self.execution_mode.value}")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*70 + "\n")
        
        progress = getattr(self, "_progress_callback", None)

        def _emit_progress(detail: str, force: bool = False) -> None:
            if callable(progress):
                try:
                    progress(detail, force=force)
                except Exception:
                    pass

        # Initialize funnels
        long_funnel = OpportunityFunnel("LONG")
        short_funnel = OpportunityFunnel("SHORT")
        _emit_progress("scan_complete:start", force=True)
        
        # LAYER 1: Universe Validation
        print("LAYER 1: Universe Validation")
        print("-" * 70)
        
        raw_universe = self._get_raw_universe(raw_universe_override=raw_universe_override)
        clean_universe, validation_stats = self.universe_validator.validate_universe(raw_universe)
        _emit_progress("layer1_universe_validation")
        
        print(f"✅ {validation_stats['passed']}/{validation_stats['total_input']} tickers validated\n")
        if not clean_universe:
            logger.warning("Universe validation produced 0 tradable tickers")
            regime = self.regime_analyzer.analyze_regime()
            self._print_regime_summary(regime)
            self._print_opportunities_report([], [], regime)
            return {
                'execution_mode': self.execution_mode.value,
                'regime': regime,
                'long_opportunities': [],
                'short_opportunities': [],
                'long_funnel': long_funnel,
                'short_funnel': short_funnel,
                'stats': {
                    'validated_tickers': 0,
                    'long_found': 0,
                    'short_found': 0
                }
            }
        
        # LAYER 2: Data Integrity Gate
        print("LAYER 2: Data Integrity Gate")
        print("-" * 70)
        
        health_reports = self.data_gate.validate_batch(list(clean_universe))
        _emit_progress("layer2_data_integrity")
        
        validated_tickers = [
            ticker for ticker, health in health_reports.items()
            if health.status == DataStatus.OK
        ]
        
        print(f"✅ {len(validated_tickers)}/{len(clean_universe)} passed integrity gate\n")
        if not validated_tickers:
            logger.warning("Data Integrity Gate produced 0 valid tickers")
            regime = self.regime_analyzer.analyze_regime()
            self._print_regime_summary(regime)
            self._print_opportunities_report([], [], regime)
            return {
                'execution_mode': self.execution_mode.value,
                'regime': regime,
                'long_opportunities': [],
                'short_opportunities': [],
                'long_funnel': long_funnel,
                'short_funnel': short_funnel,
                'stats': {
                    'validated_tickers': 0,
                    'long_found': 0,
                    'short_found': 0
                }
            }
        
        # LAYER 3: Regime Analysis
        print("LAYER 3: Regime Analysis")
        print("-" * 70)
        
        regime = self.regime_analyzer.analyze_regime()
        _emit_progress("layer3_regime")
        
        self._print_regime_summary(regime)
        
        # EXECUTION MODE WARNING
        if self.execution_mode != ExecutionMode.LIVE:
            print("="*70)
            print(f"⚠️  EXECUTION MODE: {self.execution_mode.value}")
            print("⚠️  OUTPUTS ARE SIMULATED - NOT APPROVED FOR LIVE EXECUTION")
            print("⚠️  Risk checks STILL RUN but marked as simulation")
            print("="*70 + "\n")
        
        # LAYER 4-6: SHORT PIPELINE
        print("="*70)
        print("📉 SHORT PIPELINE")
        print("="*70)
        
        short_opportunities = self._run_short_pipeline(
            validated_tickers, regime, short_funnel
        )
        _emit_progress("short_pipeline")
        
        # LAYER 4-6: LONG PIPELINE
        print("\n" + "="*70)
        print("📈 LONG PIPELINE")
        print("="*70)
        if suppress_long_pipeline:
            print("⏸ LONG PIPELINE SUPPRESSED")
            print("   HIGH VIX + adverse regime not suitable for accumulation entries\n")
            self._last_long_candidates = []
            long_opportunities = []
            _emit_progress("long_pipeline:suppressed", force=True)
        else:
            long_opportunities = self._run_long_pipeline(
                validated_tickers, regime, long_funnel
            )
            _emit_progress("long_pipeline", force=True)
        
        # Print funnels
        if self.verbose:
            long_funnel.print_funnel_report()
            short_funnel.print_funnel_report()
        
        # LAYER 7: REPORTING
        self._print_opportunities_report(long_opportunities, short_opportunities, regime)
        
        return {
            'execution_mode': self.execution_mode.value,
            'regime': regime,
            'long_opportunities': long_opportunities,
            'short_opportunities': short_opportunities,
            'long_funnel': long_funnel,
            'short_funnel': short_funnel,
            'stats': {
                'validated_tickers': len(validated_tickers),
                'long_found': len(long_opportunities),
                'short_found': len(short_opportunities)
            }
        }
    
    def _run_short_pipeline(self, validated_tickers: List[str],
                           regime: RegimeContext, funnel: OpportunityFunnel) -> List[Opportunity]:
        """Run complete SHORT pipeline"""
        
        print("\nSTEP 1: Discovery")
        print("-" * 70)
        
        short_candidates, discovery_rejections = self.short_discovery.discover(validated_tickers)
        
        funnel.record_stage("Discovery", len(validated_tickers),
                           len(short_candidates), discovery_rejections)
        
        print(f"✅ {len(short_candidates)} short candidates discovered\n")
        
        if not short_candidates:
            return []
        
        # STEP 2: Scoring
        print("STEP 2: Scoring")
        print("-" * 70)
        
        scored_candidates = []
        score_rejections = {}
        
        for candidate in short_candidates:
            progress = getattr(self, "_progress_callback", None)
            if callable(progress):
                try:
                    progress(f"short_pipeline:score:{candidate.get('ticker', 'UNKNOWN')}")
                except Exception:
                    pass
            score_result = self._build_voyager_short_score(candidate, regime)
            score = score_result['normalized_score']
            candidate['score'] = score
            candidate['score_result'] = score_result
            candidate['score_breakdown'] = score_result['confidence_summary']

            if score_result['recommendation'] in {'STRONG SHORT', 'SHORT'} and score_result['accepted_for_ranking']:
                scored_candidates.append(candidate)
            else:
                candidate['approved'] = False
                candidate['rejected'] = True
                candidate['rejection_reason'] = score_result.get('rejection_reason', score_result['recommendation'])
                score_rejections['below_min_score'] = score_rejections.get('below_min_score', 0) + 1
        
        funnel.record_stage("Scoring", len(short_candidates),
                           len(scored_candidates), score_rejections)
        
        print(f"✅ {len(scored_candidates)} passed score threshold ({regime.min_score_short}+)\n")
        
        if not scored_candidates:
            return []
        
        # STEP 3: Risk Gate
        print("STEP 3: Risk Validation")
        print("-" * 70)
        
        approved, risk_rejections = self.short_risk_gate.validate_batch(
            scored_candidates, regime, self.data_feed
        )
        
        funnel.record_stage("Risk Gate", len(scored_candidates),
                           len(approved), risk_rejections)
        
        print(f"✅ {len(approved)} SHORT opportunities approved\n")

        # Sort by score
        approved.sort(key=lambda x: x.score, reverse=True)
        
        return approved
    
    def _run_long_pipeline(self, validated_tickers: List[str],
                           regime: RegimeContext, funnel: OpportunityFunnel) -> List[Opportunity]:
        """Run complete LONG pipeline with ALL candidate tracking"""

        print("\nSTEP 1: Discovery")
        print("-" * 70)

        long_candidates, discovery_rejections = self.long_discovery.discover(validated_tickers)

        funnel.record_stage("Discovery", len(validated_tickers),
                            len(long_candidates), discovery_rejections)

        print(f"✅ {len(long_candidates)} long candidates discovered\n")

        # Track all rejected/processed candidates for detailed display
        all_candidates_for_display: List[Dict] = []

        discovered_tickers = {c.get('ticker') for c in long_candidates}
        discovery_reason_map = self.long_discovery.last_rejected_reasons
        for ticker in validated_tickers:
            progress = getattr(self, "_progress_callback", None)
            if callable(progress):
                try:
                    progress(f"long_pipeline:discovery:{ticker}")
                except Exception:
                    pass
            if ticker not in discovered_tickers:
                rejection_reason = self._find_rejection_reason(
                    ticker, discovery_reason_map, discovery_rejections
                )
                all_candidates_for_display.append({
                    'ticker': ticker,
                    'stage': 'Discovery',
                    'status': 'rejected',
                    'rejection_reason': rejection_reason,
                    'score': 0,
                    'reasons': [f"Failed Discovery: {rejection_reason}"]
                })

        if not long_candidates:
            self._last_long_candidates = all_candidates_for_display
            self._print_all_candidates_detailed(all_candidates_for_display, [], regime.min_score_long)
            return []

        # STEP 2: Scoring
        print("STEP 2: Scoring")
        print("-" * 70)

        scored_candidates = []
        score_rejections = {}

        for candidate in long_candidates:
            progress = getattr(self, "_progress_callback", None)
            if callable(progress):
                try:
                    progress(f"long_pipeline:score:{candidate.get('ticker', 'UNKNOWN')}")
                except Exception:
                    pass
            score_result = self._build_voyager_long_score(candidate, regime)
            score = score_result['normalized_score']
            candidate['score'] = score
            candidate['score_result'] = score_result
            candidate['score_breakdown'] = score_result['confidence_summary']

            if score_result['recommendation'] in {'STRONG BUY', 'BUY'} and score_result['accepted_for_ranking']:
                scored_candidates.append(candidate)
                candidate['status'] = 'passed_scoring'
                candidate['stage'] = 'Scoring'
            else:
                score_rejections['below_min_score'] = score_rejections.get('below_min_score', 0) + 1
                candidate['status'] = 'rejected'
                candidate['rejection_reason'] = score_result.get('rejection_reason', score_result['recommendation'])
                candidate['stage'] = 'Scoring'
                candidate['approved'] = False
                candidate['rejected'] = True
                all_candidates_for_display.append(candidate)

        funnel.record_stage("Scoring", len(long_candidates),
                            len(scored_candidates), score_rejections)

        print(f"✅ {len(scored_candidates)} passed score threshold ({regime.min_score_long}+)\n")

        if not scored_candidates:
            self._last_long_candidates = all_candidates_for_display
            self._print_all_candidates_detailed(all_candidates_for_display, [], regime.min_score_long)
            return []

        # STEP 3: Risk Gate
        print("STEP 3: Risk Validation")
        print("-" * 70)

        approved, risk_rejections = self.long_risk_gate.validate_batch(
            scored_candidates, regime, self.data_feed
        )

        funnel.record_stage("Risk Gate", len(scored_candidates),
                            len(approved), risk_rejections)

        print(f"✅ {len(approved)} LONG opportunities approved\n")

        approved_tickers = {opp.ticker for opp in approved}
        for candidate in scored_candidates:
            if candidate.get('ticker') in approved_tickers:
                candidate['status'] = 'approved'
                candidate['stage'] = 'Risk Gate'
                candidate['approved'] = True
                candidate['rejected'] = False
                candidate['rejection_reason'] = None
            else:
                candidate['status'] = 'rejected'
                candidate['stage'] = 'Risk Gate'
                candidate['approved'] = False
                candidate['rejected'] = True
                candidate['rejection_reason'] = candidate.get('rejection_reason', 'risk_validation_failed')

            all_candidates_for_display.append(candidate)

        self._last_long_candidates = all_candidates_for_display
        self._print_all_candidates_detailed(all_candidates_for_display, [], regime.min_score_long)

        approved.sort(key=lambda x: x.score, reverse=True)
        return approved

    def _find_rejection_reason(self, ticker: str, per_ticker_reasons: Dict[str, str],
                               rejection_summary: Dict[str, int]) -> str:
        """
        Determine specific rejection reason for a ticker (best effort).
        """
        if ticker in per_ticker_reasons:
            return per_ticker_reasons[ticker]
        if rejection_summary:
            most_common = max(rejection_summary.items(), key=lambda x: x[1])
            return most_common[0]
        return "unknown"

    def _print_all_candidates_detailed(self, long_candidates: List[Dict],
                                       short_candidates: List[Dict],
                                       long_min_score: int = 75):
        """Print ALL candidates including rejected ones with details"""

        print("\n" + "="*70)
        print("📋 ALL CANDIDATES - COMPLETE ANALYSIS")
        print("="*70)

        if long_candidates:
            approved = [c for c in long_candidates if c.get('status') == 'approved']
            rejected_scoring = [c for c in long_candidates if c.get('stage') == 'Scoring']
            rejected_risk = [c for c in long_candidates if c.get('stage') == 'Risk Gate' and c.get('status') == 'rejected']
            rejected_discovery = [c for c in long_candidates if c.get('stage') == 'Discovery']

            print(f"\n📈 LONG CANDIDATES BREAKDOWN:")
            print(f"   ✅ Approved: {len(approved)}")
            print(f"   ⚠️  Rejected (Risk Gate): {len(rejected_risk)}")
            print(f"   ⚠️  Rejected (Scoring): {len(rejected_scoring)}")
            print(f"   ❌ Rejected (Discovery): {len(rejected_discovery)}")
            print("-" * 70)

            if approved:
                print(f"\n✅ APPROVED ({len(approved)}):")
                for i, candidate in enumerate(approved, 1):
                    print(f"\n{i}. {candidate.get('ticker')} - Score: {candidate.get('score', 'N/A')}/100")
                    print("   Status: APPROVED ✅")
                    print("   Reasons:")
                    for reason in candidate.get('reasons', [])[:3]:
                        print(f"      • {reason}")

            # Close calls should be near-threshold only.
            # Score=0 pathway hard-fails are useful diagnostics, but not "close".
            scoring_close = []
            scoring_hard_fails = []
            for c in rejected_scoring:
                score_val = c.get('score', 0) or 0
                if score_val > 0 and score_val >= (long_min_score - 15):
                    scoring_close.append(c)
                else:
                    scoring_hard_fails.append(c)

            close_calls = scoring_close + rejected_risk
            if close_calls:
                close_calls.sort(key=lambda x: x.get('score', 0), reverse=True)
                print(f"\n⚠️  CLOSE CALLS - Passed Discovery ({len(close_calls)}):")
                for i, candidate in enumerate(close_calls, 1):
                    ticker = candidate.get('ticker')
                    score = candidate.get('score', 0)
                    stage = candidate.get('stage', 'Unknown')
                    rejection = candidate.get('rejection_reason', 'unknown')
                    print(f"\n{i}. {ticker} - Score: {score}/100")
                    print(f"   Stage Reached: {stage}")
                    print(f"   Rejection: {rejection}")
                    if stage == 'Scoring':
                        gap = max(0, long_min_score - score)
                        print(f"   Gap to Approval: {gap} points (needed {long_min_score}+)")
                    print("   Reasons:")
                    for reason in candidate.get('reasons', [])[:3]:
                        print(f"      • {reason}")

            if scoring_hard_fails:
                print(f"\nℹ️  SCORING HARD-FAILS ({len(scoring_hard_fails)}):")
                print("   Reached Scoring but failed pathway/fatal checks (not near-threshold).")
                reason_groups: Dict[str, List[str]] = {}
                for candidate in scoring_hard_fails:
                    reason = candidate.get('rejection_reason', 'unknown')
                    reason_groups.setdefault(reason, []).append(candidate.get('ticker', 'UNKNOWN'))
                for reason, tickers in reason_groups.items():
                    print(f"   {reason}: {len(tickers)}")

            if rejected_discovery:
                print(f"\n❌ REJECTED IN DISCOVERY ({len(rejected_discovery)}):")
                print("   These tickers failed initial filters:")
                rejection_groups: Dict[str, List[str]] = {}
                for candidate in rejected_discovery:
                    reason = candidate.get('rejection_reason', 'unknown')
                    rejection_groups.setdefault(reason, []).append(candidate.get('ticker', 'UNKNOWN'))
                for reason, tickers in rejection_groups.items():
                    print(f"\n   {reason}: ({len(tickers)} tickers)")
                    print(f"      {', '.join(tickers[:10])}")
                    if len(tickers) > 10:
                        print(f"      ... and {len(tickers) - 10} more")

        if short_candidates:
            approved_s = [c for c in short_candidates if c.get('status') == 'approved']
            rejected_scoring_s = [c for c in short_candidates if c.get('stage') == 'Scoring']
            rejected_risk_s = [c for c in short_candidates if c.get('stage') == 'Risk Gate' and c.get('status') == 'rejected']
            rejected_discovery_s = [c for c in short_candidates if c.get('stage') == 'Discovery']

            print(f"\n📉 SHORT CANDIDATES BREAKDOWN:")
            print(f"   ✅ Approved: {len(approved_s)}")
            print(f"   ⚠️  Rejected (Risk Gate): {len(rejected_risk_s)}")
            print(f"   ⚠️  Rejected (Scoring): {len(rejected_scoring_s)}")
            print(f"   ❌ Rejected (Discovery): {len(rejected_discovery_s)}")
            print("-" * 70)

            if approved_s:
                print(f"\n✅ APPROVED ({len(approved_s)}):")
                for i, candidate in enumerate(approved_s, 1):
                    print(f"\n{i}. {candidate.get('ticker')} - Score: {candidate.get('score', 'N/A')}/100")
                    print("   Status: APPROVED ✅")
                    print("   Reasons:")
                    for reason in candidate.get('reasons', [])[:3]:
                        print(f"      • {reason}")

            close_calls_s = rejected_scoring_s + rejected_risk_s
            if close_calls_s:
                close_calls_s.sort(key=lambda x: x.get('score', 0), reverse=True)
                print(f"\n⚠️  CLOSE CALLS - Passed Discovery ({len(close_calls_s)}):")
                for i, candidate in enumerate(close_calls_s, 1):
                    ticker = candidate.get('ticker')
                    score = candidate.get('score', 0)
                    stage = candidate.get('stage', 'Unknown')
                    rejection = candidate.get('rejection_reason', 'unknown')
                    print(f"\n{i}. {ticker} - Score: {score}/100")
                    print(f"   Stage Reached: {stage}")
                    print(f"   Rejection: {rejection}")
                    print("   Reasons:")
                    for reason in candidate.get('reasons', [])[:3]:
                        print(f"      • {reason}")

            if rejected_discovery_s:
                print(f"\n❌ REJECTED IN DISCOVERY ({len(rejected_discovery_s)}):")
                rejection_groups_s: Dict[str, List[str]] = {}
                for candidate in rejected_discovery_s:
                    reason = candidate.get('rejection_reason', 'unknown')
                    rejection_groups_s.setdefault(reason, []).append(candidate.get('ticker', 'UNKNOWN'))
                for reason, tickers in rejection_groups_s.items():
                    print(f"\n   {reason}: ({len(tickers)} tickers)")
                    print(f"      {', '.join(tickers[:10])}")
                    if len(tickers) > 10:
                        print(f"      ... and {len(tickers) - 10} more")

        print("\n" + "="*70 + "\n")
    
    def _print_regime_summary(self, regime: RegimeContext):
        """Print regime summary"""
        
        print(f"VIX: {regime.vix:.1f} ({regime.vix_regime.value}) [{regime.vix_source}]")
        if regime.vix_source != "LIVE":
            print(f"     Confidence: {regime.vix_confidence}")
        
        print(f"Recommendation: {regime.recommendation}")
        print()
        
        print("Scoring Thresholds:")
        print(f"  Min Score Long/Short: {regime.min_score_long}/{regime.min_score_short}")
        print()
        
        print("Risk Limits ($ lost at stop):")
        print(f"  Max risk per long: {regime.max_risk_per_long_pct*100:.2f}%")
        print(f"  Max risk per short: {regime.max_risk_per_short_pct*100:.2f}%")
        print(f"  Max total long risk: {regime.max_total_long_risk_pct*100:.1f}%")
        print(f"  Max total short risk: {regime.max_total_short_risk_pct*100:.1f}%")
        print()
        
        print("Exposure Caps (notional position size):")
        print(f"  Max long per position: {regime.max_long_exposure_per_position_pct*100:.0f}%")
        print(f"  Max short per position: {regime.max_short_exposure_per_position_pct*100:.0f}%")
        print(f"  Max total long exposure: {regime.max_total_long_exposure_pct*100:.0f}%")
        print(f"  Max total short exposure: {regime.max_total_short_exposure_pct*100:.0f}%")
        print()

        print("💡 RISK FRAMEWORK EXPLAINED:")
        print("-" * 70)
        print("PRIMARY CONSTRAINT (Strict):")
        print(f"  Risk at Stop: {regime.max_risk_per_long_pct*100:.2f}% per long")
        print(f"                {regime.max_risk_per_short_pct*100:.2f}% per short")
        print("  → This is the HARD LIMIT on dollars lost if stopped out")
        print()
        print("SECONDARY CONSTRAINT (Guideline with 20% buffer):")
        print(f"  Exposure Guideline: {regime.max_long_exposure_per_position_pct*100:.0f}% per long")
        print(f"                      {regime.max_short_exposure_per_position_pct*100:.0f}% per short")
        print(f"  Hard Cap: {regime.max_long_exposure_per_position_pct*1.2*100:.0f}% / {regime.max_short_exposure_per_position_pct*1.2*100:.0f}%")
        print("  → Position notional can exceed guideline if risk is managed")
        print("-" * 70)
        print()

    def print_all_candidates(self, long_candidates: List[Dict], short_candidates: List[Dict]):
        """Print all candidates discovered with their status"""

        print("\n" + "="*70)
        print("📋 ALL DISCOVERED CANDIDATES")
        print("="*70)

        if long_candidates:
            print(f"\n📈 LONG CANDIDATES ({len(long_candidates)} found):")
            print("-" * 70)

            for i, candidate in enumerate(long_candidates, 1):
                ticker = candidate.get('ticker', 'UNKNOWN')
                score = candidate.get('score', 'N/A')
                reasons = candidate.get('reasons', [])

                print(f"\n{i}. {ticker} - Score: {score}")
                print("   Reasons:")
                for reason in reasons[:3]:
                    print(f"      • {reason}")

                if candidate.get('rejected'):
                    print(f"   ❌ Rejected: {candidate.get('rejection_reason', 'unknown')}")
                elif candidate.get('approved'):
                    print("   ✅ APPROVED")

        if short_candidates:
            print(f"\n📉 SHORT CANDIDATES ({len(short_candidates)} found):")
            print("-" * 70)

            for i, candidate in enumerate(short_candidates, 1):
                ticker = candidate.get('ticker', 'UNKNOWN')
                score = candidate.get('score', 'N/A')
                reasons = candidate.get('reasons', [])
                print(f"\n{i}. {ticker} - Score: {score}")
                if reasons:
                    print("   Reasons:")
                    for reason in reasons[:3]:
                        print(f"      • {reason}")

                if candidate.get('rejected'):
                    print(f"   ❌ Rejected: {candidate.get('rejection_reason', 'unknown')}")
                elif candidate.get('approved'):
                    print("   ✅ APPROVED")

        print("\n" + "="*70 + "\n")
    
    def _print_opportunities_report(self, long_opps: List[Opportunity],
                                   short_opps: List[Opportunity],
                                   regime: RegimeContext):
        """Print complete opportunities report"""
        
        print("\n" + "="*70)
        print("🎯 VOYAGER OPPORTUNITIES REPORT")
        print("="*70)
        print(f"LONG Opportunities: {len(long_opps)}")
        print(f"SHORT Opportunities: {len(short_opps)}")
        print("="*70)
        
        # Print top LONG opportunities
        if long_opps:
            print("\n📈 TOP LONG OPPORTUNITIES")
            print("="*70)
            
            for i, opp in enumerate(long_opps[:10], 1):
                self._print_opportunity(opp, i)
        
        # Print top SHORT opportunities
        if short_opps:
            print("\n📉 TOP SHORT OPPORTUNITIES")
            print("="*70)
            
            for i, opp in enumerate(short_opps[:10], 1):
                self._print_opportunity(opp, i)
        
        # Summary
        if not long_opps and not short_opps:
            print("\n❌ No opportunities found")
            print(f"   Regime: {regime.recommendation}")
            print(f"   Min Scores: Long {regime.min_score_long}, Short {regime.min_score_short}")
            print("   This is normal in selective regimes")
    
    def _print_opportunity(self, opp: Opportunity, num: int):
        """Print single opportunity"""
        
        print(f"\n{num}. {opp.ticker} - {opp.direction} ({opp.mode.value})")
        print(f"   Score: {opp.score}/100")
        print(f"   Status: {opp.execution_status}")
        print()
        
        print(f"   📊 Trade Setup:")
        print(f"      Entry: ${opp.entry_price:.2f}")
        print(f"      Target: ${opp.target_price:.2f} ({opp.target_return_pct:+.1f}%)")
        print(f"      Stop: ${opp.stop_loss:.2f}")
        print(f"      Risk/Reward: {opp.risk_reward:.1f}:1")
        print()
        
        print(f"   💼 Position Sizing:")
        print(f"      Shares: {opp.shares:,}")
        print(f"      Position Value: ${opp.position_value:,.0f} ({opp.exposure_pct:.1f}% of account)")
        print(f"      Risk at Stop: ${opp.risk_amount:,.0f} ({opp.risk_pct:.2f}% of account)")
        print()
        
        print(f"   📝 Thesis:")
        for reason in opp.reasons:
            print(f"      • {reason}")
        
        print("\n" + "-"*70)
    
    def _get_raw_universe(self, raw_universe_override: Optional[List[str]] = None) -> Set[str]:
        """
        Get universe using Voyager Adaptive Universe (500-1000 stocks → 20-50 best candidates).

        Runs full 4-step pipeline: universe → pre-filter → pattern scan → ranked candidates.
        """
        if raw_universe_override is not None:
            override = {
                str(symbol or "").upper().strip()
                for symbol in raw_universe_override
                if str(symbol or "").strip()
            }
            if override:
                logger.info("Voyager external universe override: %s candidates", len(override))
                return override
            logger.warning("Voyager external universe override was empty")
            return set()

        try:
            builder = VoyagerAdaptiveUniverse(self.data_feed)
            universe_data = builder.build_universe(scan_type='full')

            all_candidates = set()
            for c in universe_data.get('long_candidates', []):
                ticker = c['ticker'] if isinstance(c, dict) else c
                all_candidates.add(ticker)
            for c in universe_data.get('short_candidates', []):
                ticker = c['ticker'] if isinstance(c, dict) else c
                all_candidates.add(ticker)

            logger.info(f"Voyager Adaptive Universe: {len(all_candidates)} candidates")

            if all_candidates:
                return all_candidates

            logger.warning("Adaptive universe returned empty; using fallback")
            return self._get_fallback_universe()

        except Exception as e:
            logger.error(f"Adaptive universe failed: {e}")
            return self._get_fallback_universe()

    def _get_fallback_universe(self) -> Set[str]:
        """
        Fallback universe if builder fails.

        Much larger than legacy 28-stock static list.
        """
        return {
            # Mega caps
            'AAPL', 'MSFT', 'GOOGL', 'GOOG', 'AMZN', 'NVDA', 'META', 'TSLA',
            'BRK.B', 'V', 'UNH', 'JNJ', 'XOM', 'WMT', 'JPM', 'MA', 'PG',
            'HD', 'CVX', 'MRK',
            # Large caps
            'ABBV', 'KO', 'PEP', 'COST', 'AVGO', 'TMO', 'ABT', 'DHR',
            'NKE', 'MCD', 'LLY', 'ACN', 'NEE', 'TXN', 'ORCL', 'CRM',
            'ADBE', 'CSCO', 'AMD', 'QCOM', 'INTC', 'NOW', 'AMAT', 'ADI',
            'INTU', 'ISRG', 'BKNG', 'GILD', 'ADP', 'MDLZ',
            # Growth tech
            'SNOW', 'CRWD', 'ZS', 'DDOG', 'NET', 'PLTR', 'RBLX', 'MDB',
            'PANW', 'WDAY', 'TEAM', 'MNDY', 'HUBS', 'ZM', 'OKTA', 'CFLT',
            'S', 'DOCN', 'ESTC', 'GTLB', 'FROG', 'PATH', 'BILL', 'SQ',
            'SHOP', 'UBER', 'LYFT', 'DASH', 'ABNB', 'COIN',
            # Small/mid growth
            'SMCI', 'IONQ', 'ENVX', 'RKLB', 'RDDT', 'ARM', 'ASML',
            'SOFI', 'HOOD', 'NU', 'AFRM', 'UPST', 'OPEN', 'COMP',
            'CELH', 'CVNA', 'BROS', 'DUOL', 'CHWY', 'ETSY',
            # Biotech
            'MRNA', 'BNTX', 'NVAX', 'VRTX', 'REGN', 'GILD', 'BIIB',
            'AMGN', 'ILMN', 'ALNY', 'CRSP', 'BEAM', 'EDIT', 'NTLA', 'SAGE',
            # China ADRs
            'BABA', 'JD', 'PDD', 'BIDU', 'NIO', 'XPEV', 'LI', 'BILI',
            'NTES', 'VIPS', 'TME', 'IQ', 'HUYA', 'DOYU', 'YMM',
            # EV/Auto
            'RIVN', 'LCID', 'F', 'GM', 'STLA', 'TM', 'HMC', 'RACE', 'VWAGY',
            # Meme/Volatile
            'GME', 'AMC', 'BBBY', 'SPCE', 'WISH', 'CLOV', 'TLRY', 'SNDL', 'BB', 'NOK',
            # Energy
            'XLE', 'XOP', 'OXY', 'SLB', 'HAL', 'EOG', 'PXD', 'COP', 'DVN', 'FANG',
            # Finance
            'XLF', 'BAC', 'C', 'WFC', 'GS', 'MS', 'SCHW', 'BLK', 'SPGI', 'CME',
            # ETFs
            'SPY', 'QQQ', 'IWM', 'DIA', 'XLK', 'XLV', 'XLY', 'XLP', 'XLI', 'XLU'
        }


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    print("="*70)
    print("🚀 VOYAGER PRODUCTION v2.0 COMPLETE - TESTING")
    print("="*70)
    
    # Determine if Growth Mode should be enabled
    # Recommended for accounts $20k-$50k
    account_size = 100000
    growth_mode = account_size <= 50000
    
    # Initialize
    voyager = VoyagerProductionV2Complete(
        account_size=account_size,
        verbose=True,
        growth_mode=growth_mode
    )
    
    # Run complete scan
    results = voyager.scan_complete()
    
    print("\n" + "="*70)
    print("✅ VOYAGER PRODUCTION v2.0 COMPLETE - READY!")
    print("="*70)
    print(f"Long opportunities: {len(results['long_opportunities'])}")
    print(f"Short opportunities: {len(results['short_opportunities'])}")
    print("="*70)
