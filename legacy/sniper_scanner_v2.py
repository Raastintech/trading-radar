"""
Sniper Scanner V2 - Dedicated Momentum Breakout Detection

PHILOSOPHY: Follow institutional momentum in 1-50 day windows.
- Detect consolidation (ATR contraction) before a breakout
- Confirm with volume surge on breakout day (2x+ average)
- Validate RS acceleration vs SPY (institutional interest building)

This is NOT filtered Voyager. Voyager uses RS trend + MA structure
for 6-18 month moves. Sniper uses ATR contraction + volume surge
for 1-50 day momentum breakouts. Different signals, different timeframe.

Regime gating is handled by the unified master trader. This scanner should
only award score weight to signals backed by live market or event data.
"""

from typing import Dict, List, Optional
import logging
import statistics
try:
    from enhanced_strategy_scoring import (
        apply_post_score_adjustment,
        calculate_strategy_score,
        calculate_position_size as enhanced_position_size,
    )
    from strategy_check_mapper import StrategyCheckMapper
    HAS_ENHANCED_SCORING = True
except ImportError:
    HAS_ENHANCED_SCORING = False
    apply_post_score_adjustment = None
    calculate_strategy_score = None
    enhanced_position_size = None
    StrategyCheckMapper = None

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

try:
    from vix_snapshot import get_vix_snapshot as _get_vix_snapshot
    _HAS_VIX_SNAPSHOT = True
except ImportError:
    _get_vix_snapshot = None
    _HAS_VIX_SNAPSHOT = False

logger = logging.getLogger(__name__)


class SniperScannerV2:
    """
    Dedicated Sniper momentum breakout scanner.

    Detects:
    1. ATR contraction -> consolidation base before move
    2. Volume surge on breakout day (2x+ 20-day average)
    3. RS acceleration vs SPY (institutional interest building)
    4. Price closing in upper 70%+ of day range (strong close)

    Returns opportunity dicts compatible with the Voyager Opportunity dataclass
    fields used by the execution layer (entry_price, stop_loss, target_price,
    score, risk_reward, shares, direction).
    """

    # Signal thresholds
    MIN_ATR_CONTRACTION_RATIO = 0.80   # recent ATR must be < 80% of prior ATR
    MIN_VOLUME_SURGE_RATIO = 2.0       # breakout day volume >= 2x 20-day avg
    MIN_RS_ACCELERATION = 0.01         # RS must improve >= 1% recent vs prior
    MIN_RISK_REWARD = 2.5              # minimum R:R to qualify
    MIN_SCORE = 70                     # minimum score to qualify

    # Position sizing: ATR-based stop below consolidation low
    STOP_ATR_BUFFER = 0.02             # 2% below consolidation low for stop
    TARGET_ATR_MULTIPLIER = 4.0        # target = entry + 4 * ATR minimum
    PIVOT_LOOKBACK_DAYS = 10           # prior base / pivot window
    PIVOT_STOP_ATR_BUFFER = 0.5        # stop can sit 0.5 ATR below breakout pivot
    MAX_STOP_ATR_DISTANCE = 1.5        # cap breakout risk at 1.5 ATR below entry
    MAX_BREAKOUT_EXTENSION_ATR = 1.0   # reject late breakout closes > 1 ATR above pivot

    def __init__(self, data_feed, account_equity: float = 100000):
        self.data_feed = data_feed
        self.account_equity = account_equity
        self._scan_rejects = []  # populated each scan_universe(); read by UnifiedMasterTraderV3
        self._earnings_adapter = OptionsEarningsAdapter() if _HAS_EARNINGS_ADAPTER else None
        self._earnings_cache: Dict[str, bool] = {}
        logger.info("SniperScannerV2 initialized — momentum breakout detection")

    def _safe_earnings_window(self, ticker: str, min_days: int = 7) -> bool:
        """Only award earnings safety when we can verify the calendar."""
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

    def _build_regime_context(self) -> Dict[str, float]:
        """Best-effort regime context for score overrides."""
        current_vix = 0.0
        if _HAS_VIX_SNAPSHOT and _get_vix_snapshot is not None:
            try:
                snapshot = _get_vix_snapshot(data_feed=self.data_feed) or {}
                current_vix = float(snapshot.get("vix_level") or 0.0)
            except Exception:
                current_vix = 0.0
        return {"vix": current_vix}

    def evaluate_sniper_opportunity(
        self,
        ticker: str,
        bars: Optional[List[Dict]] = None,
        spy_bars: Optional[List[Dict]] = None,
        regime_context: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """Evaluate a ticker with weighted Sniper scoring."""
        if bars is None:
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=60)
            except Exception as e:
                logger.debug(f"SniperV2 {ticker}: data error — {e}")
                return None

        if not bars or len(bars) < 30:
            self._scan_rejects.append({
                'ticker': ticker, 'reason': 'data_insufficient',
                'score': 0.0, 'grade': 'F', 'rr': 0.0,
            })
            return None

        recent_atr = self._calculate_atr(bars[-10:])
        prior_atr = self._calculate_atr(bars[-30:-10])
        if prior_atr <= 0:
            return None

        contraction_ratio = recent_atr / prior_atr
        atr_contraction_pct = max(0.0, 1.0 - contraction_ratio)

        avg_volume_20 = sum(b['volume'] for b in bars[-21:-1]) / 20
        if avg_volume_20 <= 0:
            return None

        current_bar = bars[-1]
        volume_ratio = current_bar['volume'] / avg_volume_20

        if spy_bars is None:
            try:
                spy_bars = self.data_feed.get_daily_bars('SPY', days_back=60)
            except Exception:
                spy_bars = None

        rs_acceleration = self._calculate_rs_acceleration(bars, spy_bars) if spy_bars and len(spy_bars) >= 30 else 0.0
        day_range = current_bar['high'] - current_bar['low']
        close_position = ((current_bar['close'] - current_bar['low']) / day_range) if day_range > 0 else 0.5

        geometry = self._build_breakout_trade_geometry(bars, recent_atr)
        if not geometry:
            return None
        entry_price = geometry['entry_price']
        stop_loss = geometry['stop_loss']
        target_price = geometry['target_price']
        risk_reward = geometry['risk_reward']
        consolidation_low = geometry['consolidation_low']
        breakout_pivot = geometry['breakout_pivot']
        breakout_extension_atr = geometry['breakout_extension_atr']

        closes = [b['close'] for b in bars]
        ma_20 = sum(closes[-20:]) / min(20, len(closes))
        ma_50 = sum(closes[-50:]) / min(50, len(closes))
        base_slice = self._prior_breakout_window(bars)
        range_10 = max(b['high'] for b in base_slice) - min(b['low'] for b in base_slice)
        base_tight = (range_10 / entry_price) if entry_price > 0 else 1.0

        # Real options signal — neutral fallback when chain data unavailable
        opt_sig = _get_options_score_adj(ticker) if _HAS_OPTIONS_SIGNAL else {
            "adj": 0.0, "pcr": None, "gamma": "NEUTRAL", "source": "unavailable", "note": "module unavailable"
        }
        pcr = opt_sig.get("pcr")
        # Score options activity only from real chain data.
        unusual_options_real = (pcr < 0.75) if pcr is not None else False
        earnings_window_safe = self._safe_earnings_window(ticker)
        regime_context = regime_context or self._build_regime_context()

        scanner_data = {
            'risk_reward': risk_reward,
            'atr_contraction_pct': atr_contraction_pct,
            'volume_ratio': volume_ratio,
            'rs_improving': rs_acceleration > self.MIN_RS_ACCELERATION,
            'base_quality': base_tight <= 0.12,
            'breakout_clean': close_position >= 0.70,
            'institutional_ownership': False,
            'vix_favorable': True,
            'pattern_quality': contraction_ratio < 0.80,
            'gap_likely': current_bar['close'] > current_bar['open'],
            'sector_momentum': rs_acceleration > 0,
            'safe_earnings_window': earnings_window_safe,
            'unusual_options': unusual_options_real,
            'short_interest_confirmed': False,
            'catalyst_near': False,
            'indicators_confirm': entry_price > ma_20 and entry_price > ma_50,
            'sector': 'UNKNOWN',
            'sector_wave': volume_ratio >= 3.0 and rs_acceleration > 0.02,
        }
        checks = StrategyCheckMapper.map_sniper_checks(scanner_data)
        metadata = StrategyCheckMapper.build_metadata(scanner_data, 'SNIPER')
        score_result = calculate_strategy_score('SNIPER', checks, metadata, regime_context=regime_context)
        # Apply options score adjustment (+/-5 boost/penalty, not a veto)
        if opt_sig["adj"] != 0.0:
            score_result = apply_post_score_adjustment('SNIPER', score_result, opt_sig["adj"])
            score_result.setdefault("options_signal", opt_sig)
            logger.debug(f"SniperV2 {ticker}: options adj {opt_sig['adj']:+.1f} ({opt_sig['note']}) → score {score_result['normalized_score']:.1f}")

        if score_result['recommendation'] not in {'STRONG BUY', 'BUY'}:
            pq = score_result.get('pathway_qualification', {}) or {}
            normalized_reason = (
                'risk_reward_too_low' if risk_reward < self.MIN_RISK_REWARD
                else 'score_below_threshold'
            )
            _rr_failed = risk_reward < self.MIN_RISK_REWARD
            _pathway_failed = not pq.get('pathways_passed', [])
            _score_failed = score_result['normalized_score'] < 60.0
            _sniper_gates = []
            if _rr_failed: _sniper_gates.append('rr')
            if _pathway_failed: _sniper_gates.append('pathway')
            if _score_failed: _sniper_gates.append('score')
            self._scan_rejects.append({
                'ticker': ticker,
                'reason': normalized_reason,
                'score': score_result['normalized_score'],
                'grade': score_result.get('grade', 'F'),
                'rr': risk_reward,
                'entry': entry_price, 'stop': stop_loss, 'target': target_price,
                'primary_pathway': pq.get('primary_pathway'),
                'pathways_failed': pq.get('pathways_failed', []),
                'gates_failed': sorted(set(_sniper_gates)),
                'options_pcr': opt_sig.get('pcr'),
                'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
            })
            return None

        position = enhanced_position_size(
            account_equity=self.account_equity,
            entry_price=entry_price,
            stop_price=stop_loss,
            score_result=score_result
        )
        if not position.get('valid'):
            return None

        reasons = [
            f"Weighted score: {score_result['normalized_score']:.1f}/100 ({score_result['grade']})",
            f"ATR contraction: {contraction_ratio:.2f}x",
            f"Volume surge: {volume_ratio:.1f}x avg",
            f"RS acceleration: +{rs_acceleration:.2%} vs SPY",
            f"R/R: {risk_reward:.1f}:1",
            f"Options: {opt_sig['note']}",
        ]

        return {
            'approved': True,
            'ticker': ticker,
            'direction': 'LONG',
            'strategy': 'SNIPER',
            'pattern_type': 'MOMENTUM_BREAKOUT',
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'target_price': target_price,
            'risk_reward': risk_reward,
            'score': score_result['normalized_score'],
            'grade': score_result['grade'],
            'shares': position['shares'],
            'position_pct': position['position_pct'],
            'risk_pct': position['risk_pct'],
            'growth_mode': score_result['growth_mode_eligible'],
            'tier_breakdown': score_result['tier_breakdown'],
            'confidence_summary': score_result['confidence_summary'],
            'pathway_qualification': score_result.get('pathway_qualification', {}),
            'score_result': score_result,
            'reasons': reasons,
            'options_pcr': opt_sig.get('pcr'),
            'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
            'options_score_adj': opt_sig.get('adj', 0.0),
            'metrics': {
                'atr_contraction_ratio': round(contraction_ratio, 3),
                'atr_contraction_pct': round(atr_contraction_pct, 3),
                'volume_surge_ratio': round(volume_ratio, 2),
                'rs_acceleration': round(rs_acceleration, 4),
                'close_position': round(close_position, 2),
                'recent_atr': round(recent_atr, 2),
                'consolidation_low': round(consolidation_low, 2),
                'breakout_pivot': round(breakout_pivot, 2),
                'breakout_extension_atr': round(breakout_extension_atr, 2),
            },
        }

    def scan_ticker(
        self,
        ticker: str,
        spy_bars: Optional[List[Dict]] = None,
        regime_context: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """
        Scan a single ticker for a momentum breakout setup.

        spy_bars: pre-fetched SPY daily bars — pass from scan_universe() to avoid
                  fetching SPY once per ticker (100x duplication per universe scan).

        Returns opportunity dict or None if no setup found.
        """
        try:
            bars = self.data_feed.get_daily_bars(ticker, days_back=60)
        except Exception as e:
            logger.debug(f"SniperV2 {ticker}: data error — {e}")
            return None

        result = self.evaluate_sniper_opportunity(
            ticker,
            bars=bars,
            spy_bars=spy_bars,
            regime_context=regime_context,
        )
        if not result:
            return None

        logger.info(
            f"SniperV2 BREAKOUT: {ticker} — score {result['score']:.1f}, "
            f"entry ${result['entry_price']:.2f}, stop ${result['stop_loss']:.2f}, "
            f"target ${result['target_price']:.2f}"
        )
        return result

    def scan_universe(self, tickers: List[str]) -> List[Dict]:
        """Scan a list of tickers and return all breakout opportunities, sorted by score."""
        self._scan_rejects = []  # reset for this scan pass
        progress = getattr(self, "_progress_callback", None)
        # Pre-fetch SPY once — reused across all ticker scans to avoid 100x duplication
        try:
            shared_spy_bars = self.data_feed.get_daily_bars('SPY', days_back=60)
        except Exception:
            shared_spy_bars = None
        shared_regime_context = self._build_regime_context()

        opportunities = []
        total = len(tickers)
        for idx, ticker in enumerate(tickers, start=1):
            if callable(progress):
                try:
                    progress(f"ticker:{idx}/{total}:{ticker}")
                except Exception:
                    pass
            try:
                opp = self.scan_ticker(
                    ticker,
                    spy_bars=shared_spy_bars,
                    regime_context=shared_regime_context,
                )
                if opp:
                    opportunities.append(opp)
                # scoring rejects appended inside evaluate_sniper_opportunity()
            except Exception as e:
                logger.debug(f"SniperV2 scan error {ticker}: {e}")
                self._scan_rejects.append({
                    'ticker': ticker, 'reason': 'data_error',
                    'score': 0.0, 'grade': 'F', 'rr': 0.0,
                    'gates_failed': ['data'],
                })

        opportunities.sort(key=lambda x: x['score'], reverse=True)
        logger.info(f"SniperV2 scan complete: {len(opportunities)} breakouts from {len(tickers)} tickers")
        return opportunities

    def scan_for_breakouts(self, universe: List[str]):
        """Scan for breakout setups with weighted scoring."""
        opportunities = []

        # Fetch VIX once per scan so the regime_context can be passed into scoring.
        # When VIX > 20 the atr_contraction fatal gate is relaxed — broad volatility
        # prevents consolidation coils from forming regardless of setup quality.
        _regime_context = self._build_regime_context()

        try:
            shared_spy_bars = self.data_feed.get_daily_bars('SPY', days_back=60)
        except Exception:
            shared_spy_bars = None

        for ticker in universe:
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=60)
                if not bars or len(bars) < 30 or not HAS_ENHANCED_SCORING:
                    continue

                recent_atr = self._calculate_atr(bars[-10:])
                prior_atr = self._calculate_atr(bars[-30:-10])
                if prior_atr <= 0:
                    continue

                atr_contraction = max(0.0, 1.0 - (recent_atr / prior_atr))
                avg_volume_20 = sum(b['volume'] for b in bars[-21:-1]) / 20
                if avg_volume_20 <= 0:
                    continue

                current_bar = bars[-1]
                volume_surge = current_bar['volume'] / avg_volume_20
                rs_raw = self._calculate_rs_acceleration(bars, shared_spy_bars) if shared_spy_bars and len(shared_spy_bars) >= 30 else 0.0
                rs_acceleration = rs_raw > self.MIN_RS_ACCELERATION
                geometry = self._build_breakout_trade_geometry(bars, recent_atr)
                if not geometry:
                    continue
                entry = geometry['entry_price']
                stop = geometry['stop_loss']
                target = geometry['target_price']
                r_r_ratio = (target - entry) / (entry - stop)
                closes = [b['close'] for b in bars]
                ma_20 = sum(closes[-20:]) / min(20, len(closes))
                ma_50 = sum(closes[-50:]) / min(50, len(closes))
                base_slice = self._prior_breakout_window(bars)
                range_10 = max(b['high'] for b in base_slice) - min(b['low'] for b in base_slice)
                base_tight = (range_10 / entry) <= 0.12 if entry > 0 else False
                day_range = current_bar['high'] - current_bar['low']
                close_position = ((current_bar['close'] - current_bar['low']) / day_range) if day_range > 0 else 0.5
                clean_breakout = close_position >= 0.70

                opt_sig_b = _get_options_score_adj(ticker) if _HAS_OPTIONS_SIGNAL else {
                    "adj": 0.0, "pcr": None, "gamma": "NEUTRAL", "source": "unavailable", "note": "module unavailable"
                }
                pcr_b = opt_sig_b.get("pcr")
                unusual_options_b = (pcr_b < 0.75) if pcr_b is not None else False
                earnings_window_safe = self._safe_earnings_window(ticker)

                scanner_data = {
                    'risk_reward': r_r_ratio,
                    'atr_contraction_pct': atr_contraction,
                    'volume_ratio': volume_surge,
                    'rs_improving': rs_acceleration,
                    'base_quality': base_tight,
                    'breakout_clean': clean_breakout,
                    'institutional_ownership': False,
                    'vix_favorable': True,
                    'pattern_quality': atr_contraction >= 0.20,
                    'gap_likely': current_bar['close'] > current_bar['open'],
                    'sector_momentum': rs_raw > 0,
                    'safe_earnings_window': earnings_window_safe,
                    'unusual_options': unusual_options_b,
                    'short_interest_confirmed': False,
                    'catalyst_near': False,
                    'indicators_confirm': entry > ma_20 and entry > ma_50,
                    'sector': 'Unknown',
                    'sector_wave': volume_surge >= 3.0 and rs_raw > 0.02
                }

                checks = StrategyCheckMapper.map_sniper_checks(scanner_data)
                metadata = StrategyCheckMapper.build_metadata(scanner_data, 'SNIPER')
                score_result = calculate_strategy_score('SNIPER', checks, metadata, regime_context=_regime_context)

                logger.info(f"\n🎯 SNIPER: {ticker}")
                logger.info(f"   Score: {score_result['normalized_score']:.1f}/100 ({score_result['grade']})")
                logger.info(f"   Recommendation: {score_result['recommendation']}")

                if score_result['recommendation'] in ['STRONG BUY', 'BUY']:
                    position = enhanced_position_size(
                        account_equity=self.account_equity,
                        entry_price=entry,
                        stop_price=stop,
                        score_result=score_result
                    )

                    if position['valid']:
                        opportunities.append({
                            'ticker': ticker,
                            'strategy': 'SNIPER',
                            'score': score_result['normalized_score'],
                            'grade': score_result['grade'],
                            'shares': position['shares'],
                            'entry': entry,
                            'stop': stop,
                            'target': target,
                            'growth_mode': score_result['growth_mode_eligible']
                        })
                else:
                    pq_b = score_result.get('pathway_qualification', {}) or {}
                    _bf_normalized_reason = (
                        'risk_reward_too_low' if r_r_ratio < self.MIN_RISK_REWARD
                        else 'score_below_threshold'
                    )
                    _bf_rr_failed = r_r_ratio < self.MIN_RISK_REWARD
                    _bf_pathway_failed = not pq_b.get('pathways_passed', [])
                    _bf_score_failed = score_result['normalized_score'] < 60.0
                    _bf_gates = []
                    if _bf_rr_failed: _bf_gates.append('rr')
                    if _bf_pathway_failed: _bf_gates.append('pathway')
                    if _bf_score_failed: _bf_gates.append('score')
                    self._scan_rejects.append({
                        'ticker': ticker,
                        'reason': _bf_normalized_reason,
                        'score': score_result['normalized_score'],
                        'grade': score_result.get('grade', 'F'),
                        'rr': r_r_ratio,
                        'entry': entry, 'stop': stop, 'target': target,
                        'primary_pathway': pq_b.get('primary_pathway'),
                        'pathways_failed': pq_b.get('pathways_failed', []),
                        'gates_failed': sorted(set(_bf_gates)),
                        'options_pcr': opt_sig_b.get('pcr'),
                        'options_gamma': opt_sig_b.get('gamma', 'NEUTRAL'),
                    })

            except Exception as e:
                logger.error(f"Error scanning {ticker}: {e}")
                continue

        return opportunities

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _calculate_atr(self, bars: List[Dict]) -> float:
        """Average True Range over the given bars."""
        if not bars:
            return 0.0

        tr_values = []
        for i, bar in enumerate(bars):
            if i == 0:
                tr = bar['high'] - bar['low']
            else:
                prev_close = bars[i - 1]['close']
                tr = max(
                    bar['high'] - bar['low'],
                    abs(bar['high'] - prev_close),
                    abs(bar['low'] - prev_close),
                )
            tr_values.append(tr)

        return statistics.mean(tr_values) if tr_values else 0.0

    def _prior_breakout_window(self, bars: List[Dict]) -> List[Dict]:
        """Use the completed base prior to the breakout bar, not the full window including the breakout bar."""
        if len(bars) <= 1:
            return list(bars or [])
        lookback = max(5, int(self.PIVOT_LOOKBACK_DAYS))
        prior = bars[-(lookback + 1):-1]
        return prior if prior else bars[:-1]

    def _build_breakout_trade_geometry(self, bars: List[Dict], recent_atr: float) -> Optional[Dict]:
        """
        Build breakout-aligned entry/stop/target levels.

        Sniper should trade the breakout pivot, not the full base low as default
        risk anchor. The stop is set to the tightest defensible invalidation
        level among:
        - below the prior base low
        - below the breakout pivot by 0.5 ATR
        - no wider than 1.5 ATR below entry
        """
        if not bars or len(bars) < max(12, self.PIVOT_LOOKBACK_DAYS + 2):
            return None

        current_bar = bars[-1]
        entry_price = float(current_bar['close'])
        if entry_price <= 0 or recent_atr <= 0:
            return None

        base_slice = self._prior_breakout_window(bars)
        if len(base_slice) < 5:
            return None

        consolidation_low = min(float(b['low']) for b in base_slice)
        breakout_pivot = max(float(b['high']) for b in base_slice)
        breakout_extension = max(entry_price - breakout_pivot, 0.0)
        breakout_extension_atr = breakout_extension / recent_atr if recent_atr > 0 else 0.0
        if entry_price <= breakout_pivot:
            return None
        if breakout_extension_atr > self.MAX_BREAKOUT_EXTENSION_ATR:
            return None

        base_stop = consolidation_low * (1.0 - self.STOP_ATR_BUFFER)
        pivot_stop = breakout_pivot - (recent_atr * self.PIVOT_STOP_ATR_BUFFER)
        atr_cap_stop = entry_price - (recent_atr * self.MAX_STOP_ATR_DISTANCE)
        stop_loss = round(max(base_stop, pivot_stop, atr_cap_stop), 2)
        if stop_loss >= entry_price:
            return None

        base_height = max(breakout_pivot - consolidation_low, 0.0)
        target_distance = max(recent_atr * self.TARGET_ATR_MULTIPLIER, base_height)
        target_price = round(entry_price + target_distance, 2)
        if target_price <= entry_price:
            return None

        risk_per_share = entry_price - stop_loss
        if risk_per_share <= 0:
            return None
        reward_per_share = target_price - entry_price
        risk_reward = reward_per_share / risk_per_share

        return {
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'target_price': target_price,
            'risk_reward': risk_reward,
            'consolidation_low': consolidation_low,
            'breakout_pivot': breakout_pivot,
            'breakout_extension_atr': breakout_extension_atr,
            'base_height': base_height,
        }

    def _calculate_rs_acceleration(
        self, stock_bars: List[Dict], spy_bars: List[Dict]
    ) -> float:
        """
        Measure RS acceleration: recent 10-day RS vs prior 10-day RS vs SPY.
        Positive = stock outperforming SPY more now than before = accelerating.
        """
        def _return(bars_slice: List[Dict]) -> float:
            if len(bars_slice) < 2:
                return 0.0
            start = bars_slice[0]['close']
            end = bars_slice[-1]['close']
            return (end - start) / start if start > 0 else 0.0

        # Align lengths
        min_len = min(len(stock_bars), len(spy_bars))
        stock_bars = stock_bars[-min_len:]
        spy_bars = spy_bars[-min_len:]

        if min_len < 25:
            return 0.0

        recent_stock = _return(stock_bars[-10:])
        recent_spy = _return(spy_bars[-10:])
        recent_rs = recent_stock - recent_spy

        prior_stock = _return(stock_bars[-25:-10])
        prior_spy = _return(spy_bars[-25:-10])
        prior_rs = prior_stock - prior_spy

        return recent_rs - prior_rs

    def _calculate_score(
        self,
        contraction_ratio: float,
        volume_ratio: float,
        rs_acceleration: float,
        close_position: float,
        risk_reward: float,
    ) -> int:
        """Score breakout quality 0-100."""
        score = 0

        # ATR contraction (25 pts) — tighter is better
        if contraction_ratio < 0.50:
            score += 25
        elif contraction_ratio < 0.60:
            score += 20
        elif contraction_ratio < 0.70:
            score += 15
        elif contraction_ratio < 0.80:
            score += 10

        # Volume surge (30 pts)
        if volume_ratio >= 4.0:
            score += 30
        elif volume_ratio >= 3.0:
            score += 25
        elif volume_ratio >= 2.5:
            score += 20
        elif volume_ratio >= 2.0:
            score += 15

        # RS acceleration (25 pts)
        if rs_acceleration >= 0.05:
            score += 25
        elif rs_acceleration >= 0.03:
            score += 20
        elif rs_acceleration >= 0.02:
            score += 15
        elif rs_acceleration >= 0.01:
            score += 10
        elif rs_acceleration > 0:
            score += 5

        # Strong close (10 pts)
        if close_position >= 0.90:
            score += 10
        elif close_position >= 0.80:
            score += 8
        elif close_position >= 0.70:
            score += 5

        # R/R bonus (10 pts)
        if risk_reward >= 4.0:
            score += 10
        elif risk_reward >= 3.0:
            score += 7
        elif risk_reward >= 2.5:
            score += 4

        return min(score, 100)
