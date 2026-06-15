"""
EOD Scanner V3 - Complete Multi-Strategy Integration

Finds tomorrow's setup opportunities after market close (4:00 PM ET+)

INTEGRATED STRATEGIES:
- Voyager: Strategic growth setups (6-18 months)
- Sniper:  Tactical momentum setups (3-30 days)
- Remora:  Opportunistic inefficiency setups (2-48 hours)
- Short:   Bearish deterioration plays (6-12 weeks)
- Reaper:  Contrarian fear-opportunity setups (VIX >= 28 only)

WEIGHTED SCORING:
- All setups scored via calculate_strategy_score() for consistency with the live trader
- Voyager + Short include fundamental data from FundamentalDataFetcher (yfinance)
- Scores are normalized 0-100 via strategy tier weights

TRIPLE CONFLUENCE DETECTION:
- Identifies stocks in ALL THREE long universes (Voyager + Sniper + Remora) with setups
- Maximum conviction opportunities for tomorrow
"""

from datetime import datetime, time as dt_time, timedelta
import pytz
from typing import List, Dict, Optional, Tuple
import logging

from alpaca_data import AlpacaDataFeed
from alpaca_client_factory import build_trading_client
from universe_snapshot_builder import UniverseSnapshotBuilder
from voyager_adaptive_universe import VoyagerAdaptiveUniverse
from sniper_adaptive_universe import SniperAdaptiveUniverse
from remora_adaptive_universe import RemoraAdaptiveUniverse
from triple_confluence_detector import TripleConfluenceDetector
from enhanced_strategy_scoring import calculate_strategy_score
from strategy_check_mapper import StrategyCheckMapper
from fundamental_data_fetcher import FundamentalDataFetcher
from contrarian_scanner import ContrarianScanner
from vix_snapshot import get_vix_snapshot

try:
    from options_intelligence import get_options_score_adj as _get_options_score_adj
    _HAS_OPTIONS_SIGNAL = True
except ImportError:
    _get_options_score_adj = None
    _HAS_OPTIONS_SIGNAL = False

try:
    from options_earnings_adapter import OptionsEarningsAdapter
    _HAS_EARNINGS_ADAPTER = True
except ImportError:
    OptionsEarningsAdapter = None
    _HAS_EARNINGS_ADAPTER = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EODScannerV3:
    """
    End-of-Day Scanner V3 — Multi-Strategy Tomorrow's Setups

    Active: After 4:00 PM ET
    Integrates: Voyager + Sniper + Remora + Short + Reaper
    Scores:     All setups via calculate_strategy_score() (same as live trader)
    Detects:    Triple confluence setups for maximum conviction
    """

    MARKET_CLOSE = dt_time(16, 0)   # 4:00 PM ET

    def __init__(self, use_adaptive_universes: bool = True):
        logger.info("=" * 80)
        logger.info("🌙 EOD SCANNER V3 - MULTI-STRATEGY TOMORROW'S SETUPS")
        logger.info("=" * 80)

        self.data_feed = AlpacaDataFeed()
        self.trading_client = build_trading_client(logger=logger)

        self.use_adaptive = use_adaptive_universes
        self.universes: Dict[str, set] = {
            'voyager': set(),
            'sniper':  set(),
            'remora':  set(),
            'short':   set(),
            'contrarian': set(),
        }
        self.universe_snapshot: Optional[Dict] = None

        # Per-scan state reset in scan_for_tomorrows_setups()
        self._spy_bars_cache: Optional[List[Dict]] = None
        self._vix_level: float = 0.0
        self._earnings_adapter = OptionsEarningsAdapter() if _HAS_EARNINGS_ADAPTER else None
        self._earnings_cache: Dict[str, bool] = {}

        if use_adaptive_universes:
            logger.info("🔍 Building adaptive universes for EOD scan...")
            self._build_adaptive_universes()
        else:
            logger.info("📊 Using static universe...")
            static = set(self._build_static_universe())
            for key in ('voyager', 'sniper', 'remora', 'short', 'contrarian'):
                self.universes[key] = static

        self.confluence_detector = TripleConfluenceDetector()

        logger.info("=" * 80)
        logger.info("✅ EOD Scanner V3 Ready")
        logger.info("=" * 80)

    # ── Universe builders ─────────────────────────────────────────────────────

    def _build_adaptive_universes(self):
        """Build adaptive universes for all strategies."""
        self.universe_snapshot = None

        try:
            snapshot_builder = UniverseSnapshotBuilder(
                self.data_feed,
                trading_client=self.trading_client,
            )
            snapshot = snapshot_builder.build_snapshot()
            base_universe = set(snapshot.get('base_universe', []))
            if base_universe:
                self.universe_snapshot = snapshot
                self.universes['voyager'] = set(snapshot.get('voyager_universe', []))
                self.universes['sniper'] = set(snapshot.get('sniper_universe', []))
                self.universes['remora'] = set(snapshot.get('remora_universe', []))
                self.universes['short'] = set(snapshot.get('short_universe', []))
                self.universes['contrarian'] = set(snapshot.get('contrarian_universe', []))
                summary = snapshot.get('summary', {})
                logger.info("   ✅ Shared base universe: %s stocks", len(base_universe))
                logger.info("      Source assets: %s", summary.get('source_assets', 0))
                logger.info("      Voyager: %s", len(self.universes['voyager']))
                logger.info("      Sniper: %s", len(self.universes['sniper']))
                logger.info("      Remora: %s", len(self.universes['remora']))
                logger.info("      Short: %s", len(self.universes['short']))
                logger.info("      Reaper: %s", len(self.universes['contrarian']))
                return

            logger.warning(
                "   ⚠️  Shared dynamic universe unavailable, falling back to legacy builders: %s",
                snapshot.get('fallback_reason') or 'unknown',
            )
        except Exception as exc:
            logger.warning("   ⚠️  Shared dynamic universe build failed: %s", exc)

        try:
            logger.info("   Building Voyager universe...")
            voyager_builder = VoyagerAdaptiveUniverse(self.data_feed)
            voyager_result = voyager_builder.build_universe(scan_type='quick')

            voyager_long  = voyager_result.get('long_candidates', [])
            voyager_short = voyager_result.get('short_candidates', [])

            for c in voyager_long:
                ticker = c.get('ticker', '') if isinstance(c, dict) else c
                if ticker:
                    self.universes['voyager'].add(ticker)

            for c in voyager_short:
                ticker = c.get('ticker', '') if isinstance(c, dict) else c
                if ticker:
                    self.universes['short'].add(ticker)

            logger.info(f"      ✅ Voyager: {len(self.universes['voyager'])} stocks")
        except Exception as e:
            logger.error(f"      ❌ Voyager universe error: {e}")

        try:
            logger.info("   Building Sniper universe...")
            sniper_builder = SniperAdaptiveUniverse(self.data_feed)
            sniper_result  = sniper_builder.build_universe()
            self.universes['sniper'] = set(sniper_result.get('tickers', []))
            # Add sniper universe to short pool (look for breakdowns in the same pool)
            self.universes['short'] |= self.universes['sniper']
            logger.info(f"      ✅ Sniper: {len(self.universes['sniper'])} stocks")
        except Exception as e:
            logger.error(f"      ❌ Sniper universe error: {e}")

        try:
            logger.info("   Building Remora universe...")
            remora_builder = RemoraAdaptiveUniverse(self.data_feed)
            remora_result  = remora_builder.build_universe()
            self.universes['remora'] = set(remora_result.get('tickers', []))
            logger.info(f"      ✅ Remora: {len(self.universes['remora'])} stocks")
        except Exception as e:
            logger.error(f"      ❌ Remora universe error: {e}")

        self.universes['contrarian'] = set(
            self.universes['voyager'] | self.universes['sniper']
        )
        total_unique = len(
            self.universes['voyager'] | self.universes['sniper'] | self.universes['remora']
        )
        logger.info(f"   📊 Total unique long coverage: {total_unique} stocks")
        logger.info(f"   📉 Short universe: {len(self.universes['short'])} stocks")
        logger.info(f"   💀 Reaper universe: {len(self.universes['contrarian'])} stocks")

    def _build_static_universe(self) -> List[str]:
        return [
            'SPY', 'QQQ', 'IWM',
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA',
            'AMD', 'COIN', 'PLTR', 'SNOW', 'NET', 'DDOG', 'CRWD',
            'NFLX', 'SHOP', 'SQ', 'ROKU',
            'JPM', 'BAC', 'XOM', 'CVX',
            'XLK', 'XLF', 'XLE', 'XLV',
        ]

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _fetch_spy_bars(self) -> List[Dict]:
        """Return SPY daily bars, cached for the scan cycle."""
        if self._spy_bars_cache is None:
            try:
                self._spy_bars_cache = self.data_feed.get_daily_bars('SPY', days_back=65) or []
            except Exception:
                self._spy_bars_cache = []
        return self._spy_bars_cache

    def _get_vix(self) -> float:
        """Fetch current VIX via the shared snapshot helper."""
        snapshot = get_vix_snapshot(self.data_feed)
        level = snapshot.get("vix_level")
        return float(level) if isinstance(level, (int, float)) else 0.0

    def _safe_earnings_window(self, ticker: str, min_days: int = 7) -> bool:
        """Only award earnings safety when the event date can be verified."""
        ticker = str(ticker or "").upper().strip()
        if not ticker:
            return False
        cached = self._earnings_cache.get(ticker)
        if cached is not None:
            return cached
        if self._earnings_adapter is None:
            self._earnings_cache[ticker] = False
            return False
        try:
            days = self._earnings_adapter.days_to_earnings(ticker)
            safe = days is not None and days > int(min_days)
        except Exception:
            safe = False
        self._earnings_cache[ticker] = safe
        return safe

    @staticmethod
    def _sanitize_voyager_fundamentals(fundamentals: Optional[Dict]) -> Dict:
        defaults = {
            'revenue_growth_yoy': 0.0,
            'gross_margin': 0.0,
            'fcf_positive': False,
            'fcf_inflection': False,
            'margin_improving': False,
            'rule_of_40_pass': False,
            'debt_manageable': False,
            'valuation_reasonable': False,
            'inst_ownership_high': False,
            'guidance_trend': 'stable',
        }
        if not isinstance(fundamentals, dict):
            return defaults
        sanitized = dict(defaults)
        if fundamentals.get('data_source') == 'error':
            return sanitized
        for key in sanitized:
            value = fundamentals.get(key)
            if value is not None:
                sanitized[key] = value
        return sanitized

    def _build_voyager_geometry(self, bars: List[Dict], entry: float) -> Tuple[float, float, float]:
        """Mirror the live long risk gate instead of hardcoding 5:1 R/R."""
        if not bars or len(bars) < 10 or entry <= 0:
            return (0.0, 0.0, 0.0)
        atr_14 = self._calc_atr(bars, 14)
        if atr_14 <= 0:
            return (0.0, 0.0, 0.0)
        swing_period = min(10, len(bars))
        swing_low = min(float(b['low']) for b in bars[-swing_period:])
        stop = min(entry - (atr_14 * 2.0), swing_low)
        target = entry * 1.50
        if stop >= entry or target <= entry:
            return (0.0, 0.0, 0.0)
        rr = (target - entry) / (entry - stop)
        return (float(stop), float(target), float(rr))

    def _build_short_geometry(self, bars: List[Dict], entry: float, ma_200: float) -> Tuple[float, float, float]:
        """Mirror the live short geometry instead of hardcoding a fixed-percent frame."""
        if not bars or len(bars) < 10 or entry <= 0:
            return (0.0, 0.0, 0.0)
        atr_14 = self._calc_atr(bars, 14)
        if atr_14 <= 0:
            return (0.0, 0.0, 0.0)

        completed_bars = list(bars[:-1]) if len(bars) > 1 else list(bars)
        prior_day_high = float(completed_bars[-1]['high']) if completed_bars else float(bars[-1]['high'])
        prior_window = completed_bars[:-1] if len(completed_bars) > 1 else []
        recent_high_10 = max(float(b['high']) for b in prior_window[-10:]) if prior_window else prior_day_high
        stop_candidates = sorted(
            float(level) for level in (prior_day_high, recent_high_10) if float(level) > entry + 0.01
        )
        if stop_candidates:
            stop = stop_candidates[0]
        else:
            stop = entry + max(atr_14 * 1.8, entry * 0.025)

        support_candidates = []
        for window in (10, 20, 40, 60):
            segment = completed_bars[-window:] if completed_bars else []
            if segment:
                support_candidates.append(min(float(b['low']) for b in segment))
        if ma_200 < entry:
            support_candidates.append(float(ma_200))
        lower_supports = sorted((level for level in support_candidates if 0.0 < level < entry - 0.01), reverse=True)
        if lower_supports:
            target = lower_supports[0]
        else:
            target = max(0.01, entry - max(atr_14 * 4.0, entry * 0.12))

        if stop <= entry or target >= entry:
            return (0.0, 0.0, 0.0)
        rr = (entry - target) / (stop - entry)
        return (float(stop), float(target), float(rr))

    @staticmethod
    def _display_rec(result: Dict, research_mode: bool = False) -> str:
        """
        Human-readable recommendation label for EOD display.
        research_mode results get a WATCH/MONITOR label instead of REJECT,
        since the live-trading fatals don't apply to EOD research scanning.
        """
        if not research_mode:
            return result.get('recommendation', '—')
        score = result.get('normalized_score', 0)
        if score >= 60:
            return 'WATCH'
        if score >= 45:
            return 'MONITOR'
        return 'LOW'

    @staticmethod
    def _calc_atr(bars: List[Dict], period: int = 14) -> float:
        """Average True Range over `period` days."""
        if len(bars) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(bars)):
            hi, lo, prev_c = bars[i]['high'], bars[i]['low'], bars[i - 1]['close']
            trs.append(max(hi - lo, abs(hi - prev_c), abs(lo - prev_c)))
        return sum(trs[-period:]) / period

    @staticmethod
    def _calc_rs_spy(ticker_bars: List[Dict], spy_bars: List[Dict], period: int = 20) -> float:
        """Relative return of ticker vs SPY over `period` days (percentage points)."""
        try:
            if len(ticker_bars) < period + 1 or len(spy_bars) < period + 1:
                return 0.0
            t_ret = (ticker_bars[-1]['close'] - ticker_bars[-period - 1]['close']) / ticker_bars[-period - 1]['close']
            s_ret = (spy_bars[-1]['close'] - spy_bars[-period - 1]['close']) / spy_bars[-period - 1]['close']
            return (t_ret - s_ret) * 100
        except (ZeroDivisionError, IndexError):
            return 0.0

    @staticmethod
    def _avg_volume(bars: List[Dict], period: int = 20) -> float:
        vols = [b.get('volume', 0) for b in bars[-period:] if b.get('volume', 0) > 0]
        return sum(vols) / len(vols) if vols else 0.0

    @staticmethod
    def _sma(bars: List[Dict], period: int) -> float:
        prices = [b['close'] for b in bars[-period:]]
        return sum(prices) / len(prices) if prices else 0.0

    # ── Strategy scanners ─────────────────────────────────────────────────────

    def _scan_voyager_setups(self, universe: set) -> List[Dict]:
        """
        Voyager: Strategic growth setups.
        Technical pre-filter → fundamental data → weighted scoring.
        """
        if not universe:
            return []

        logger.info(f"\n   Scanning VOYAGER universe ({len(universe)} stocks)...")
        spy_bars = self._fetch_spy_bars()
        regime_compatible = self._vix_level < 25 or self._vix_level == 0.0
        setups = []

        for ticker in universe:
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=65)
                if not bars or len(bars) < 20:
                    continue

                current = bars[-1]
                entry   = current['close']
                if entry <= 0:
                    continue

                atr     = self._calc_atr(bars, 14)
                rs_spy  = self._calc_rs_spy(bars, spy_bars, 20)
                avg_vol = self._avg_volume(bars, 20)
                sma20   = self._sma(bars, 20)

                # Pre-filter: must outperform SPY (fatal in scoring) and have liquidity
                if rs_spy < 0 or avg_vol < 200_000:
                    continue

                # Institutional accumulation proxy: 20-day up-volume dominance + price trend
                up_vol   = sum(b['volume'] for b in bars[-20:] if b['close'] > b['open'])
                down_vol = sum(b['volume'] for b in bars[-20:] if b['close'] <= b['open']) or 1
                price_trending = entry > sma20
                inst_buying = (up_vol >= down_vol * 0.9) and price_trending

                stop, target, rr = self._build_voyager_geometry(bars, entry)
                if rr <= 0:
                    continue

                # Setup type for display
                high_20 = max(b['high'] for b in bars[-20:])
                low_20  = min(b['low']  for b in bars[-20:])
                range_20_pct = ((high_20 - low_20) / low_20) * 100 if low_20 > 0 else 0
                near_high    = entry > high_20 * 0.93
                above_sma20  = entry > sma20

                setup_type = (
                    'BREAKOUT'     if near_high and range_20_pct < 15
                    else 'CONTINUATION' if above_sma20
                    else 'ACCUMULATION'
                )

                # Fetch fundamentals (only for technically qualified candidates)
                fundamentals = self._sanitize_voyager_fundamentals(
                    FundamentalDataFetcher.get_voyager_fundamentals(ticker)
                )

                scanner_data = {
                    'risk_reward':          rr,
                    'rs_spy':               rs_spy,
                    'inst_buying':          inst_buying,
                    'regime_compatible':    regime_compatible,
                    'avg_volume':           avg_vol,
                    **fundamentals,
                }

                checks   = StrategyCheckMapper.map_voyager_checks(scanner_data)
                metadata = StrategyCheckMapper.build_metadata(scanner_data, 'VOYAGER')
                # research_mode=True: EOD scanner scores all tiers even on fatal failures.
                # Lets technically strong stocks surface even when yfinance data is incomplete.
                result   = calculate_strategy_score('VOYAGER', checks, metadata, research_mode=True)

                if result['normalized_score'] < 45:
                    continue

                setups.append({
                    'ticker':         ticker,
                    'type':           setup_type,
                    'strategy':       'VOYAGER',
                    'price':          entry,
                    'stop':           round(stop, 2),
                    'target':         round(target, 2),
                    'risk_reward':    round(rr, 1),
                    'score':          round(result['normalized_score'], 1),
                    'grade':          result['grade'],
                    'recommendation': self._display_rec(result, research_mode=True),
                    'rs_spy':         round(rs_spy, 1),
                    'timeframe':      '6-18 months',
                    'pathway':        (result.get('pathway_qualification') or {}).get('primary_pathway', '—'),
                })

            except Exception:
                pass

        setups.sort(key=lambda x: x['score'], reverse=True)
        logger.info(f"      Found {len(setups)} setups")
        return setups

    def _scan_sniper_setups(self, universe: set) -> List[Dict]:
        """
        Sniper: Tactical momentum breakouts.
        Technical indicators only — no fundamentals required.
        """
        if not universe:
            return []

        logger.info(f"\n   Scanning SNIPER universe ({len(universe)} stocks)...")
        spy_bars = self._fetch_spy_bars()
        vix_favorable = (self._vix_level > 0 and self._vix_level < 20) or self._vix_level == 0.0
        setups = []

        for ticker in universe:
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=40)
                if not bars or len(bars) < 20:
                    continue

                current = bars[-1]
                entry   = current['close']
                if entry <= 0:
                    continue

                # ATR contraction: 5-day ATR vs 20-day ATR (squeeze)
                atr_5  = self._calc_atr(bars[-7:],   5)
                atr_20 = self._calc_atr(bars,        20)
                atr_contraction_pct = (1 - atr_5 / atr_20) if atr_20 > 0 else 0.0
                atr_contraction_pct = max(0.0, atr_contraction_pct)

                # Volume ratio: 5-day avg vs 20-day avg
                vol_5  = self._avg_volume(bars, 5)
                vol_20 = self._avg_volume(bars, 20)
                volume_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0

                # RS vs SPY: improving = 10d RS > 30d RS
                rs_20 = self._calc_rs_spy(bars, spy_bars, 20)
                rs_10 = self._calc_rs_spy(bars, spy_bars, 10)
                rs_improving = rs_10 > rs_20 and rs_10 > 0

                # Base quality: 10-day range < 8% of price
                high_10 = max(b['high'] for b in bars[-10:])
                low_10  = min(b['low']  for b in bars[-10:])
                range_10_pct = ((high_10 - low_10) / low_10) * 100 if low_10 > 0 else 100
                base_quality = range_10_pct < 8.0

                # Breakout: close within 3% of 20-day high
                high_20      = max(b['high'] for b in bars[-20:])
                breakout_clean = entry > high_20 * 0.97

                # R/R: Sniper — mirrors sniper_scanner_v2.py exactly
                # stop = consolidation low * 0.98 (2% buffer), target = entry + 3 * recent ATR
                atr_5   = self._calc_atr(bars[-7:], min(5, len(bars) - 1))  # already computed above
                consolidation_low = min(b['low'] for b in bars[-10:])
                stop    = consolidation_low * 0.98
                target  = entry + atr_5 * 3.0
                rr      = (target - entry) / (entry - stop) if stop < entry else 0.0

                setup_type = 'BREAKOUT' if breakout_clean else ('SQUEEZE' if atr_contraction_pct > 0.20 else 'MOMENTUM')

                opt_sig = _get_options_score_adj(ticker) if _HAS_OPTIONS_SIGNAL else {
                    "adj": 0.0, "pcr": None, "gamma": "NEUTRAL", "source": "unavailable", "note": "module unavailable"
                }
                pcr = opt_sig.get("pcr")
                unusual_options_real = (pcr < 0.75) if pcr is not None else False
                earnings_window_safe = self._safe_earnings_window(ticker)

                scanner_data = {
                    'risk_reward':          rr,
                    'atr_contraction_pct':  atr_contraction_pct,
                    'volume_ratio':         volume_ratio,
                    'rs_improving':         rs_improving,
                    'base_quality':         base_quality,
                    'breakout_clean':       breakout_clean,
                    'institutional_ownership': False,
                    'vix_favorable':        vix_favorable,
                    'pattern_quality':      atr_contraction_pct >= 0.20,
                    'gap_likely':           current['close'] > current['open'],
                    'sector_momentum':      rs_20 > 0,
                    'safe_earnings_window': earnings_window_safe,
                    'unusual_options':      unusual_options_real,
                    'short_interest_confirmed': False,
                    'catalyst_near':        False,
                    'indicators_confirm':   entry > high_20 * 0.95,
                }

                checks   = StrategyCheckMapper.map_sniper_checks(scanner_data)
                metadata = StrategyCheckMapper.build_metadata(scanner_data, 'SNIPER')
                result   = calculate_strategy_score('SNIPER', checks, metadata)

                if result['recommendation'] == 'REJECT' or result['normalized_score'] < 35:
                    continue

                setups.append({
                    'ticker':         ticker,
                    'type':           setup_type,
                    'strategy':       'SNIPER',
                    'price':          entry,
                    'stop':           round(stop, 2),
                    'target':         round(target, 2),
                    'risk_reward':    round(rr, 1),
                    'score':          round(result['normalized_score'], 1),
                    'grade':          result['grade'],
                    'recommendation': result['recommendation'],
                    'rs_spy':         round(rs_20, 1),
                    'atr_squeeze':    round(atr_contraction_pct * 100, 1),
                    'vol_ratio':      round(volume_ratio, 2),
                    'timeframe':      '3-30 days',
                })

            except Exception:
                pass

        setups.sort(key=lambda x: x['score'], reverse=True)
        logger.info(f"      Found {len(setups)} setups")
        return setups

    def _scan_remora_setups(self, universe: set) -> List[Dict]:
        """
        Remora: Opportunistic volume/catalyst plays — HUNTING when VIX <= 30.

        Primary goal: exploit institutional inefficiencies and execution flaws.
        When large institutions move size, they leave price footprints (volume spikes,
        gaps, momentum runs). Remora identifies these and rides the follow-through.

        EOD context: uses daily close data to flag stocks with above-average volume
        or momentum activity today, suggesting an institutional move in progress.
        Live trader detects these intraday; EOD scanner uses 1-day close-data proxies.

        VIX gate: ACTIVE when VIX <= 30 (institutional activity still readable).
        Above VIX 30, market chaos overwhelms the inefficiency signal — stand down.
        """
        if not universe:
            return []

        # VIX gate: ACTIVE at VIX <= 30, STANDBY above 30
        if self._vix_level > 30.0:
            logger.info(f"\n   REMORA: Standing down (VIX={self._vix_level:.1f} > 30 — chaos threshold)")
            return []

        # Expand to include Sniper universe for broader opportunity capture.
        # Remora's edge (institutional inefficiency) can appear in larger-cap stocks too.
        search_universe = universe | self.universes.get('sniper', set())
        logger.info(
            f"\n   Scanning REMORA universe ({len(search_universe)} stocks, "
            f"VIX={self._vix_level:.1f} — HUNTING)..."
        )
        setups = []

        for ticker in search_universe:
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=15)
                if not bars or len(bars) < 5:
                    continue

                current  = bars[-1]
                prev     = bars[-2]
                entry    = current['close']
                if entry <= 0:
                    continue

                # Volume: today vs prior 5-day average (exclude today from avg)
                vol_5        = self._avg_volume(bars[:-1], 5)
                volume_ratio = current['volume'] / vol_5 if vol_5 > 0 else 1.0

                # Pre-filter: require at least 1.5× volume or a gap/fast-move
                gap_pct      = (current['open'] - prev['close']) / prev['close'] * 100 if prev['close'] > 0 else 0
                day_move_pct = abs(current['close'] - prev['close']) / prev['close'] * 100 if prev['close'] > 0 else 0
                gap_clean    = gap_pct > 1.0   # >1% required — below this is daily open noise (sub-1σ drift)
                fast_move    = day_move_pct > 1.5   # 1.5% move signals institutional activity

                if volume_ratio < 1.5 and not (gap_clean or fast_move):
                    continue   # no elevated activity — skip

                # Catalyst proxy: EOD equivalent — elevated volume OR meaningful gap
                # (Live trader uses real-time news/block-trade detection; EOD uses volume + price)
                catalyst_major = volume_ratio >= 1.5 and (gap_clean or fast_move)

                # Do not award institutional-participation score weight from an
                # EOD proxy. Without live block-trade evidence, fail closed.
                block_trades   = False

                # Close position strength (upper 50% of range = bullish close)
                day_range    = current['high'] - current['low']
                close_pos    = (current['close'] - current['low']) / day_range if day_range > 0 else 0.5
                rs_spike     = close_pos > 0.6 and fast_move

                # R/R: stop below today's low, target 2× risk
                day_lo = current['low']
                if day_lo < entry:
                    stop   = round(day_lo * 0.995, 2)   # slight buffer below day low
                    target = round(entry + 2.0 * (entry - stop), 2)
                    rr     = 2.0
                else:
                    stop   = round(entry * 0.97, 2)
                    target = round(entry * 1.06, 2)
                    rr     = 2.0

                opt_sig = _get_options_score_adj(ticker) if _HAS_OPTIONS_SIGNAL else {
                    "adj": 0.0, "pcr": None, "gamma": "NEUTRAL", "source": "unavailable", "note": "module unavailable"
                }
                pcr = opt_sig.get("pcr")
                call_flow = (pcr < 0.80) if pcr is not None else False

                scanner_data = {
                    'catalyst_major':   catalyst_major,
                    'volume_ratio':     volume_ratio,
                    'risk_reward':      rr,
                    'gap_clean':        gap_clean,
                    'block_trades':     block_trades,
                    'fast_move':        fast_move,
                    'rs_spike':         rs_spike,
                    'smart_money_calls': call_flow,
                    'fundamental_news': False,
                }

                checks   = StrategyCheckMapper.map_remora_checks(scanner_data)
                metadata = StrategyCheckMapper.build_metadata(scanner_data, 'REMORA')
                # research_mode=True: EOD close data can't confirm all intraday catalysts.
                # Score all tiers — volume_explosion fatal (3×) is a live-trading bar.
                result   = calculate_strategy_score('REMORA', checks, metadata, research_mode=True)

                if result['normalized_score'] < 38:
                    continue

                if volume_ratio >= 3.0 and gap_clean:
                    setup_type = 'GAP_CATALYST'
                elif volume_ratio >= 2.0:
                    setup_type = 'VOLUME_SURGE'
                elif gap_clean:
                    setup_type = 'GAP_SETUP'
                else:
                    setup_type = 'MOMENTUM'

                setups.append({
                    'ticker':         ticker,
                    'type':           setup_type,
                    'strategy':       'REMORA',
                    'price':          entry,
                    'stop':           stop,
                    'target':         target,
                    'risk_reward':    rr,
                    'score':          round(result['normalized_score'], 1),
                    'grade':          result['grade'],
                    'recommendation': self._display_rec(result, research_mode=True),
                    'volume_ratio':   round(volume_ratio, 1),
                    'gap_pct':        round(gap_pct, 2),
                    'timeframe':      '2-48 hours',
                })

            except Exception:
                pass

        setups.sort(key=lambda x: x['score'], reverse=True)
        logger.info(f"      Found {len(setups)} setups")
        return setups

    def _scan_short_setups(self, universe: set) -> List[Dict]:
        """
        Short: Bearish deterioration plays.
        Technical weakness + fundamental deterioration → weighted scoring.
        """
        if not universe:
            return []

        logger.info(f"\n   Scanning SHORT universe ({len(universe)} stocks)...")
        spy_bars = self._fetch_spy_bars()
        vix_elevated = self._vix_level > 20
        setups = []

        for ticker in universe:
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=95)
                if not bars or len(bars) < 30:
                    continue

                current = bars[-1]
                entry   = current['close']
                if entry <= 0:
                    continue

                closes  = [b['close'] for b in bars]
                ma50    = self._sma(bars, min(50, len(bars)))
                ma200   = self._sma(bars, min(200, len(bars)))
                atr14   = self._calc_atr(bars, 14)

                # Technical pre-filter: price must be showing weakness
                below_ma50 = entry < ma50
                below_ma200 = entry < ma200
                if not below_ma50:
                    continue   # fast reject — not technically weak

                rs_spy = self._calc_rs_spy(bars, spy_bars, 20)
                if rs_spy > -5.0:
                    continue   # must underperform SPY by >= 5% (fatal threshold in scoring)

                # Institutional distribution proxy: volume on down-days (20-day lookback)
                up_vol   = sum(b['volume'] for b in bars[-20:] if b['close'] > b['open']) or 1
                down_vol = sum(b['volume'] for b in bars[-20:] if b['close'] <= b['open'])
                inst_selling = down_vol >= up_vol * 0.9   # down-volume dominant

                # Support broken: below lowest low of last 20 days (excluding today)
                recent_lows    = [b['low'] for b in bars[-21:-1]]
                support_level  = min(recent_lows) if recent_lows else entry
                support_broken = entry < support_level

                fundamentals_weak = below_ma50 and below_ma200

                stop, target, rr = self._build_short_geometry(bars, entry, ma200)
                if rr <= 0:
                    continue

                sector_weak = below_ma50 and below_ma200

                # Fetch short fundamentals
                fundamentals = FundamentalDataFetcher.get_short_fundamentals(ticker)

                scanner_data = {
                    'risk_reward':          rr,
                    'rs_spy':               rs_spy,
                    'inst_selling':         inst_selling,
                    'fundamentals_weak':    fundamentals_weak,
                    'vix_elevated':         vix_elevated,
                    'support_broken':       support_broken,
                    'sector_weak':          sector_weak,
                    'inst_data_age_days':   30,
                    **fundamentals,
                }

                checks   = StrategyCheckMapper.map_short_checks(scanner_data)
                metadata = StrategyCheckMapper.build_metadata(scanner_data, 'SHORT')
                result   = calculate_strategy_score('SHORT', checks, metadata)

                if result['recommendation'] == 'REJECT':
                    continue

                setup_type = (
                    'BREAKDOWN'    if support_broken and below_ma200
                    else 'DISTRIBUTION' if inst_selling and below_ma50
                    else 'DETERIORATION'
                )

                setups.append({
                    'ticker':               ticker,
                    'type':                 setup_type,
                    'strategy':             'SHORT',
                    'direction':            'SHORT',
                    'price':                entry,
                    'stop':                 round(stop, 2),
                    'target':               round(target, 2),
                    'risk_reward':          round(rr, 1),
                    'score':                round(result['normalized_score'], 1),
                    'grade':                result['grade'],
                    'recommendation':       result['recommendation'],
                    'rs_spy':               round(rs_spy, 1),
                    'below_ma50':           below_ma50,
                    'below_ma200':          below_ma200,
                    'timeframe':            '6-12 weeks',
                    'pathway':              (result.get('pathway_qualification') or {}).get('primary_pathway', '—'),
                    'days_to_earnings':     fundamentals.get('days_to_earnings', 999),
                    'short_interest_pct':   fundamentals.get('short_interest_pct', 0),
                    'undiscovered':         fundamentals.get('short_interest_low', False),
                })

            except Exception:
                pass

        setups.sort(key=lambda x: x['score'], reverse=True)
        logger.info(f"      Found {len(setups)} setups")
        return setups

    def _scan_contrarian_setups(self) -> List[Dict]:
        """
        Reaper (Contrarian): VIX-gated fear-opportunity setups.
        Only runs when VIX >= 28.
        """
        if self._vix_level < ContrarianScanner.VIX_TRIGGER:
            logger.info(
                f"\n   REAPER: Standing down "
                f"(VIX={self._vix_level:.1f} < {ContrarianScanner.VIX_TRIGGER:.0f})"
            )
            return []

        combined_universe = list(self.universes.get('contrarian', set()))
        if not combined_universe:
            logger.info("\n   REAPER: No routed contrarian universe for this scan")
            return []
        logger.info(
            f"\n   Scanning REAPER universe "
            f"({len(combined_universe)} stocks, VIX={self._vix_level:.1f})..."
        )

        try:
            scanner  = ContrarianScanner()
            raw      = scanner.scan(combined_universe, self._vix_level)
        except Exception as e:
            logger.error(f"      ❌ Reaper scan error: {e}")
            return []

        setups = []
        for o in raw:
            sig = o.get('signal', {})
            entry  = sig.get('entry_price', 0)
            stop   = sig.get('stop_loss', 0)
            target = sig.get('target_price', 0)
            rr     = sig.get('risk_reward_ratio', 0)
            setups.append({
                'ticker':         o['ticker'],
                'type':           'CONTRARIAN',
                'strategy':       'REAPER',
                'direction':      'LONG',
                'price':          entry,
                'stop':           round(stop, 2),
                'target':         round(target, 2),
                'risk_reward':    round(rr, 1),
                'score':          round(o['quality_score'], 1),
                'grade':          'B' if o['quality_score'] >= 60 else 'C',
                'recommendation': 'CONSIDER',
                'size_mult':      o.get('size_mult', 0.5),
                'timeframe':      '2-10 days',
            })

        setups.sort(key=lambda x: x['score'], reverse=True)
        logger.info(f"      Found {len(setups)} setups")
        return setups

    # ── Scan orchestration ────────────────────────────────────────────────────

    def is_after_market_close(self) -> Tuple[bool, str]:
        et_tz = pytz.timezone('America/New_York')
        now_et = datetime.now(et_tz)
        weekday = now_et.weekday()

        if weekday >= 5:
            return (True, f"Weekend ({now_et.strftime('%A')}) - can scan anytime")

        current_time = now_et.time()
        if current_time >= self.MARKET_CLOSE:
            return (True, f"After market close ({now_et.strftime('%I:%M %p')} ET)")
        else:
            return (False, f"Market still open ({now_et.strftime('%I:%M %p')} ET)")

    def scan_for_tomorrows_setups(self) -> Dict:
        """
        Scan for tomorrow's setup opportunities across all strategies.
        Returns complete setup analysis with weighted scores and confluence detection.
        """
        logger.info("")
        logger.info("=" * 80)
        logger.info("🌙 EOD SCAN - TOMORROW'S MULTI-STRATEGY SETUPS")
        logger.info("=" * 80)

        et_tz  = pytz.timezone('America/New_York')
        now_et = datetime.now(et_tz)
        logger.info(f"⏰ Scan Time: {now_et.strftime('%I:%M %p')} ET")
        logger.info("")

        # Reset per-scan caches
        self._spy_bars_cache = None
        self._vix_level = self._get_vix()
        if self._vix_level > 0:
            sniper_gate  = "STANDBY (VIX >= 20)" if self._vix_level >= 20 else "HUNTING"
            remora_gate  = "STANDBY (VIX > 30)"  if self._vix_level > 30  else "HUNTING"
            reaper_gate  = f"HUNTING (VIX >= {ContrarianScanner.VIX_TRIGGER:.0f})" if self._vix_level >= ContrarianScanner.VIX_TRIGGER else f"STANDBY (VIX < {ContrarianScanner.VIX_TRIGGER:.0f})"
            logger.info(f"   📊 VIX: {self._vix_level:.1f}  |  Sniper: {sniper_gate}  |  Remora: {remora_gate}  |  Reaper: {reaper_gate}")

        voyager_setups     = self._scan_voyager_setups(self.universes['voyager'])
        sniper_setups      = self._scan_sniper_setups(self.universes['sniper'])
        remora_setups      = self._scan_remora_setups(self.universes['remora'])
        short_setups       = self._scan_short_setups(self.universes['short'])
        contrarian_setups  = self._scan_contrarian_setups()

        triple_confluence = self._detect_triple_confluence_setups(
            voyager_setups, sniper_setups, remora_setups
        )

        logger.info("")
        logger.info("=" * 80)
        logger.info("📊 EOD SCAN RESULTS")
        logger.info("=" * 80)
        logger.info(f"   📍 Voyager setups:    {len(voyager_setups)}")
        logger.info(f"   🎯 Sniper setups:     {len(sniper_setups)}")
        logger.info(f"   🦈 Remora setups:     {len(remora_setups)}")
        logger.info(f"   📉 Short setups:      {len(short_setups)}")
        logger.info(f"   💀 Reaper setups:     {len(contrarian_setups)}")

        if triple_confluence:
            logger.warning(f"   💎 TRIPLE CONFLUENCE: {len(triple_confluence)} stocks!")
            for setup in triple_confluence:
                logger.warning(f"      ⭐⭐⭐ {setup['ticker']} ({setup['type']})")

        logger.info("=" * 80)

        return {
            'voyager_setups':    voyager_setups,
            'sniper_setups':     sniper_setups,
            'remora_setups':     remora_setups,
            'short_setups':      short_setups,
            'contrarian_setups': contrarian_setups,
            'triple_confluence': triple_confluence,
            'scan_time':         now_et.strftime('%Y-%m-%d %I:%M %p ET'),
            'tomorrow_date':     (now_et + timedelta(days=1)).strftime('%Y-%m-%d'),
            'vix_level':         self._vix_level,
        }

    # ── Confluence detection ──────────────────────────────────────────────────

    def _detect_triple_confluence_setups(
        self,
        voyager_setups: List[Dict],
        sniper_setups:  List[Dict],
        remora_setups:  List[Dict],
    ) -> List[Dict]:
        """
        Detect setups that appear in ALL THREE long strategies.
        Maximum conviction for tomorrow.
        """
        v_tickers = {s['ticker'] for s in voyager_setups}
        s_tickers = {s['ticker'] for s in sniper_setups}
        r_tickers = {s['ticker'] for s in remora_setups}
        triple    = v_tickers & s_tickers & r_tickers

        if not triple:
            return []

        result = []
        for ticker in triple:
            all_setups = [
                s for s in (voyager_setups + sniper_setups + remora_setups)
                if s['ticker'] == ticker
            ]
            best = max(all_setups, key=lambda x: x['score'])
            combined = best.copy()
            combined.update({
                'confluence':          'TRIPLE',
                'conviction':          'MAXIMUM',
                'position_multiplier': 1.8,
                'strategies':          ['VOYAGER', 'SNIPER', 'REMORA'],
                # Average score across all three strategies
                'score': round(sum(s['score'] for s in all_setups) / len(all_setups), 1),
            })
            result.append(combined)

        result.sort(key=lambda x: x['score'], reverse=True)
        return result

    # ── Output ────────────────────────────────────────────────────────────────

    def print_setups(self, scan_results: Dict):
        """Print formatted setup results with full scoring detail."""

        voyager_setups    = scan_results.get('voyager_setups', [])
        sniper_setups     = scan_results.get('sniper_setups', [])
        remora_setups     = scan_results.get('remora_setups', [])
        short_setups      = scan_results.get('short_setups', [])
        contrarian_setups = scan_results.get('contrarian_setups', [])
        triple_confluence = scan_results.get('triple_confluence', [])
        tomorrow_date     = scan_results.get('tomorrow_date', 'Tomorrow')
        vix_level         = scan_results.get('vix_level', 0)

        print("\n" + "=" * 80)
        print(f"🏆 TOMORROW'S OPPORTUNITIES ({tomorrow_date})")
        if vix_level > 0:
            sniper_gate = "STANDBY (institutions sidelining)" if vix_level >= 20 else "HUNTING"
            remora_gate = "STANDBY (VIX > 30)"                if vix_level > 30  else "HUNTING"
            reaper_gate = f"HUNTING"                          if vix_level >= ContrarianScanner.VIX_TRIGGER else "STANDBY"
            print(f"   VIX {vix_level:.1f}  |  Sniper: {sniper_gate}  |  Remora: {remora_gate}  |  Reaper: {reaper_gate}")
        print("=" * 80 + "\n")

        # Triple confluence first
        if triple_confluence:
            print("💎 TRIPLE CONFLUENCE SETUPS (MAXIMUM CONVICTION)")
            print("=" * 80)
            print("Voyager + Sniper + Remora all agree — strategic + tactical + opportunistic")
            print("-" * 80)
            for i, setup in enumerate(triple_confluence, 1):
                print(f"\n{i}. ⭐⭐⭐ {setup['ticker']} — {setup['type']}")
                print(f"   Score: {setup['score']:.0f}/100 ({setup.get('grade', '—')})")
                print(f"   Price: ${setup['price']:.2f}  Stop: ${setup['stop']:.2f}  Target: ${setup['target']:.2f}  R/R: {setup.get('risk_reward', 0):.1f}×")
                print(f"   Conviction: {setup['conviction']}  Position: {setup['position_multiplier']}× normal")
            print("\n" + "=" * 80 + "\n")

        # Voyager setups
        if voyager_setups:
            print("📍 VOYAGER SETUPS  (Strategic Growth — 6-18 months)")
            print("-" * 80)
            for i, s in enumerate(voyager_setups[:8], 1):
                pathway = s.get('pathway', '—')
                rs      = s.get('rs_spy', 0)
                print(f"{i:2}. {s['ticker']:6}  {s['type']:15}  Score: {s['score']:5.1f}  Grade: {s['grade']}  {s['recommendation']}")
                print(f"     ${s['price']:.2f}  Stop: ${s['stop']:.2f}  Target: ${s['target']:.2f}  R/R: {s['risk_reward']:.1f}×  RS/SPY: {rs:+.1f}%  Pathway: {pathway}")
            print()

        # Sniper setups
        if sniper_setups:
            print("🎯 SNIPER SETUPS  (Tactical Momentum — 3-30 days)")
            print("-" * 80)
            for i, s in enumerate(sniper_setups[:8], 1):
                squeeze = s.get('atr_squeeze', 0)
                volr    = s.get('vol_ratio', 1)
                print(f"{i:2}. {s['ticker']:6}  {s['type']:15}  Score: {s['score']:5.1f}  Grade: {s['grade']}  {s['recommendation']}")
                print(f"     ${s['price']:.2f}  Stop: ${s['stop']:.2f}  Target: ${s['target']:.2f}  R/R: {s['risk_reward']:.1f}×  Squeeze: {squeeze:.0f}%  Vol×: {volr:.1f}")
            print()
        elif vix_level >= 20:
            print("🎯 SNIPER SETUPS  (Tactical Momentum — 3-30 days)")
            print(f"   ⏸  STANDBY — VIX {vix_level:.1f} >= 20  (institutions sidelining; breakouts unreliable)")
            print()

        # Remora setups
        if remora_setups:
            print(f"🦈 REMORA SETUPS  (Opportunistic — 2-48 hours)  VIX {vix_level:.1f} — HUNTING")
            print("-" * 80)
            for i, s in enumerate(remora_setups[:5], 1):
                volr = s.get('volume_ratio', 1)
                print(f"{i:2}. {s['ticker']:6}  {s['type']:15}  Score: {s['score']:5.1f}  Grade: {s['grade']}  {s['recommendation']}")
                print(f"     ${s['price']:.2f}  Stop: ${s['stop']:.2f}  Target: ${s['target']:.2f}  R/R: {s['risk_reward']:.1f}×  Vol×: {volr:.1f}")
            print()
        elif vix_level > 30:
            print("🦈 REMORA SETUPS  (Opportunistic — 2-48 hours)")
            print(f"   ⏸  STANDBY — VIX {vix_level:.1f} > 30  (market chaos; institutional footprints unreadable)")
            print()

        # Short setups
        if short_setups:
            print("📉 SHORT SETUPS  (Bearish Deterioration — 6-12 weeks)")
            print("-" * 80)
            for i, s in enumerate(short_setups[:8], 1):
                rs       = s.get('rs_spy', 0)
                pathway  = s.get('pathway', '—')
                dte      = s.get('days_to_earnings', 999)
                si_pct   = s.get('short_interest_pct', 0)
                undiscov = " ★UNDISCOVERED" if s.get('undiscovered') else ""
                below    = "↓MA50" if s.get('below_ma50') else ""
                below   += " ↓MA200" if s.get('below_ma200') else ""
                dte_str  = f"  EarningsIn: {dte}d" if dte < 999 else ""
                si_str   = f"  SI: {si_pct:.1f}%"
                print(f"{i:2}. {s['ticker']:6}  {s['type']:15}  Score: {s['score']:5.1f}  Grade: {s['grade']}  {s['recommendation']}{undiscov}")
                print(f"     ${s['price']:.2f}  Stop: ${s['stop']:.2f}  Target: ${s['target']:.2f}  R/R: {s['risk_reward']:.1f}×  RS/SPY: {rs:+.1f}%  {below}  Pathway: {pathway}{dte_str}{si_str}")
            print()

        # Reaper setups
        if contrarian_setups:
            print(f"💀 REAPER SETUPS  (Contrarian Fear — VIX={vix_level:.1f})")
            print("-" * 80)
            for i, s in enumerate(contrarian_setups[:5], 1):
                print(f"{i:2}. {s['ticker']:6}  {s['type']:15}  Score: {s['score']:5.1f}  Grade: {s['grade']}  Size: {s.get('size_mult', 0.5)}×")
                print(f"     ${s['price']:.2f}  Stop: ${s['stop']:.2f}  Target: ${s['target']:.2f}  R/R: {s['risk_reward']:.1f}×")
            print()

        # Summary
        print("=" * 80)
        print("📊 EOD SUMMARY")
        print("=" * 80)
        print(f"💎 Triple Confluence:  {len(triple_confluence)}")
        print(f"📍 Voyager setups:     {len(voyager_setups)}")
        print(f"🎯 Sniper setups:      {len(sniper_setups)}")
        print(f"🦈 Remora setups:      {len(remora_setups)}")
        print(f"📉 Short setups:       {len(short_setups)}")
        print(f"💀 Reaper setups:      {len(contrarian_setups)}")
        if vix_level > 0:
            print(f"📊 VIX:                {vix_level:.1f}")
        print("=" * 80 + "\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='EOD Scanner V3')
    parser.add_argument('--static',  action='store_true', help='Use static universe')
    parser.add_argument('--force',   action='store_true', help='Run even if market is open')
    parser.add_argument('tickers',   nargs='*', metavar='TICKER',
                        help='One or more tickers to scan (e.g. AAPL MSFT TSLA). '
                             'Overrides adaptive universe — all strategies run on these tickers.')
    args = parser.parse_args()

    scanner = EODScannerV3(use_adaptive_universes=not args.static and not args.tickers)

    is_after, reason = scanner.is_after_market_close()

    if not (is_after or args.force or args.tickers):
        print(f"\nℹ️  {reason}")
        print("EOD scanner is best run after 4:00 PM ET.")
        print("Use --force to run anyway, or pass tickers directly: ./scan.sh AAPL MSFT\n")
        return None

    if args.tickers:
        # ── Targeted ticker scan ─────────────────────────────────────────────
        tickers = {t.upper() for t in args.tickers}
        print(f"\n🔍 Scanning {len(tickers)} ticker(s): {', '.join(sorted(tickers))}\n")
        scanner._vix_level  = scanner._get_vix()
        scanner._spy_bars_cache = None

        vix = scanner._vix_level
        sniper_gate = "STANDBY (VIX >= 20)" if vix >= 20 else "HUNTING"
        remora_gate = "STANDBY (VIX > 30)"  if vix > 30  else "HUNTING"
        reaper_gate = f"HUNTING"             if vix >= ContrarianScanner.VIX_TRIGGER else "STANDBY"
        print(f"   VIX {vix:.1f}  |  Sniper: {sniper_gate}  |  Remora: {remora_gate}  |  Reaper: {reaper_gate}\n")

        # Run all strategies against the same ticker set
        voyager_setups    = scanner._scan_voyager_setups(tickers)
        sniper_setups     = scanner._scan_sniper_setups(tickers)
        remora_setups     = scanner._scan_remora_setups(tickers)
        short_setups      = scanner._scan_short_setups(tickers)
        contrarian_setups = scanner._scan_contrarian_setups() if vix >= ContrarianScanner.VIX_TRIGGER else []

        triple_confluence = scanner._detect_triple_confluence_setups(
            voyager_setups, sniper_setups, remora_setups
        )

        results = {
            'voyager_setups':    voyager_setups,
            'sniper_setups':     sniper_setups,
            'remora_setups':     remora_setups,
            'short_setups':      short_setups,
            'contrarian_setups': contrarian_setups,
            'triple_confluence': triple_confluence,
            'tomorrow_date':     'Targeted Scan',
            'vix_level':         vix,
        }
    else:
        # ── Full adaptive universe scan ──────────────────────────────────────
        msg = reason if is_after else "Forced run (--force)"
        print(f"\n✅ {msg} - running EOD scan...\n")
        results = scanner.scan_for_tomorrows_setups()

    scanner.print_setups(results)
    return results


if __name__ == "__main__":
    main()
