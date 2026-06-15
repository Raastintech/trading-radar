"""
SHORT Scanner V1 - Bearish Deterioration Plays
6-12 week bearish opportunities
"""

import logging
import os
import statistics

from enhanced_strategy_scoring import (
    STRATEGY_CONFIGS,
    apply_post_score_adjustment,
    calculate_strategy_score,
    calculate_position_size as enhanced_position_size,
)
from strategy_check_mapper import StrategyCheckMapper
from vix_snapshot import get_vix_snapshot

try:
    from sector_resolver import resolve_sector_etf
    _HAS_SECTOR_RESOLVER = True
except ImportError:
    resolve_sector_etf = None
    _HAS_SECTOR_RESOLVER = False
try:
    from fundamental_data_fetcher import FundamentalDataFetcher
    HAS_FUNDAMENTAL_FETCHER = True
except ImportError:
    FundamentalDataFetcher = None
    HAS_FUNDAMENTAL_FETCHER = False

try:
    from options_intelligence import get_options_score_adj as _get_options_score_adj
    _HAS_OPTIONS_SIGNAL = True
except ImportError:
    _get_options_score_adj = None
    _HAS_OPTIONS_SIGNAL = False

logger = logging.getLogger(__name__)


class ShortScanner:
    """Scanner for SHORT opportunities"""

    def __init__(self, trading_client, account_equity):
        self.trading_client = trading_client
        self.data_feed = getattr(trading_client, 'data_feed', trading_client)
        self.account_equity = account_equity
        self._scan_rejects = []  # populated each scan; read by UnifiedMasterTraderV3
        # Live short geometry is now adaptive rather than a blunt 6%/18% frame.
        # The defaults are grounded in the stronger short_tight_3_0 shadow cohort.
        self.short_stop_floor_pct = self._safe_float(os.getenv("SHORT_STOP_FLOOR_PCT"), 0.025)
        self.short_stop_cap_pct = self._safe_float(os.getenv("SHORT_STOP_CAP_PCT"), 0.04)
        self.short_stop_atr_mult = self._safe_float(os.getenv("SHORT_STOP_ATR_MULT"), 1.8)
        self.short_target_floor_pct = self._safe_float(os.getenv("SHORT_TARGET_FLOOR_PCT"), 0.12)
        self.short_target_atr_mult = self._safe_float(os.getenv("SHORT_TARGET_ATR_MULT"), 4.0)
        self.short_target_extension_pct = self._safe_float(os.getenv("SHORT_TARGET_EXTENSION_PCT"), 0.18)
        self.short_min_avg_volume = self._safe_float(os.getenv("SHORT_MIN_AVG_VOLUME"), 500_000)
        self.short_min_price = self._safe_float(os.getenv("SHORT_MIN_PRICE"), 10.0)
        self.short_min_market_cap_usd = self._safe_float(os.getenv("SHORT_MIN_MARKET_CAP_M"), 500.0) * 1_000_000.0
        self.short_exhaustion_range_pos_max = self._safe_float(os.getenv("SHORT_EXHAUSTION_RANGE_POS_MAX"), 0.20)
        self.short_exhaustion_drawdown_min_pct = self._safe_float(os.getenv("SHORT_EXHAUSTION_DRAWDOWN_MIN_PCT"), 12.0)
        self.short_exhaustion_rsi_max = self._safe_float(os.getenv("SHORT_EXHAUSTION_RSI_MAX"), 38.0)
        self.short_exhaustion_below_ma50_min_pct = self._safe_float(os.getenv("SHORT_EXHAUSTION_BELOW_MA50_MIN_PCT"), 10.0)
        self.short_exhaustion_return_min_pct = self._safe_float(os.getenv("SHORT_EXHAUSTION_RETURN_MIN_PCT"), -12.0)

    @staticmethod
    def _nearest_level_above(entry: float, levels):
        valid = sorted(
            {
                round(float(level), 6)
                for level in (levels or [])
                if level is not None and float(level) > float(entry) + 0.01
            }
        )
        return valid[0] if valid else None

    @staticmethod
    def _nearest_level_below(entry: float, levels):
        valid = sorted(
            {
                round(float(level), 6)
                for level in (levels or [])
                if level is not None and 0.0 < float(level) < float(entry) - 0.01
            },
            reverse=True,
        )
        return valid[0] if valid else None

    @staticmethod
    def _margin_decay_confirmation(fundamentals: dict) -> dict:
        revenue_growth_yoy = fundamentals.get('revenue_growth_yoy')
        try:
            revenue_growth_yoy = float(revenue_growth_yoy) if revenue_growth_yoy is not None else None
        except (TypeError, ValueError):
            revenue_growth_yoy = None

        confirming_signals = []
        if fundamentals.get('guidance_trend') == 'cutting':
            confirming_signals.append('guidance_cut')
        if fundamentals.get('estimate_revisions') == 'cutting':
            confirming_signals.append('estimates_declining')
        if fundamentals.get('debt_stress'):
            confirming_signals.append('debt_stress')

        high_growth_reinvestment_risk = revenue_growth_yoy is not None and revenue_growth_yoy > 0.15
        margin_decay_confirmed = (not high_growth_reinvestment_risk) or bool(confirming_signals)
        return {
            'revenue_growth_yoy': revenue_growth_yoy,
            'high_growth_reinvestment_risk': high_growth_reinvestment_risk,
            'margin_decay_confirmed': margin_decay_confirmed,
            'confirming_signals': confirming_signals,
        }

    @staticmethod
    def _normalize_pathways(pathways) -> list[str]:
        normalized = sorted(
            {
                str(pathway).strip().upper()
                for pathway in (pathways or [])
                if str(pathway).strip()
            }
        )
        return normalized

    @staticmethod
    def evaluate_margin_revenue_macro_gate(
        pathways_passed,
        *,
        spy_close: float | None,
        spy_ma200: float | None,
        vix_level: float | None,
    ) -> dict:
        def _maybe_float(value):
            try:
                if value is None or str(value).strip() == "":
                    return None
                return float(value)
            except Exception:
                return None

        normalized_pathways = ShortScanner._normalize_pathways(pathways_passed)
        applies = normalized_pathways == ["MARGIN", "REVENUE"]
        spy_close_value = _maybe_float(spy_close)
        spy_ma200_value = _maybe_float(spy_ma200)
        vix_value = ShortScanner._safe_float(vix_level, 0.0) or 0.0
        spy_below_ma200 = bool(
            spy_close_value is not None
            and spy_ma200_value is not None
            and spy_close_value < spy_ma200_value
        )
        vix_confirmed = vix_value >= 20.0
        passed = (not applies) or (spy_below_ma200 and vix_confirmed)

        if not applies:
            reason = "not_applicable"
        elif spy_ma200_value is None or spy_close_value is None:
            reason = "spy_200ma_unavailable"
        elif not spy_below_ma200 and not vix_confirmed:
            reason = "spy_above_200ma_and_vix_below_20"
        elif not spy_below_ma200:
            reason = "spy_above_200ma"
        elif not vix_confirmed:
            reason = "vix_below_20"
        else:
            reason = "passed"

        return {
            "gate_name": "short_margin_revenue_macro_and",
            "applies": applies,
            "passed": passed,
            "pathways_passed": normalized_pathways,
            "pathway_label": " + ".join(normalized_pathways) if normalized_pathways else "NONE",
            "spy_close": spy_close_value,
            "spy_ma200": spy_ma200_value,
            "spy_below_ma200": spy_below_ma200,
            "vix_level": float(vix_value),
            "vix_confirmed": vix_confirmed,
            "reason": reason,
        }

    @staticmethod
    def _safe_float(value, default):
        try:
            if value is None or str(value).strip() == "":
                return float(default)
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def _derive_candidate_state(bars, entry: float):
        if not bars or len(bars) < 21 or entry <= 0:
            return "UNKNOWN", None, None
        base_slice = bars[-21:-1]
        if len(base_slice) < 10:
            return "UNKNOWN", None, None
        breakdown_support = min(float(bar.get("low", 0.0) or 0.0) for bar in base_slice)
        if breakdown_support <= 0:
            return "UNKNOWN", None, None
        support_distance_pct = ((float(entry) - breakdown_support) / float(entry)) * 100.0
        if float(entry) < breakdown_support:
            state = "TRIGGERING"
        elif support_distance_pct <= 1.5:
            state = "IMMINENT"
        elif support_distance_pct <= 4.0:
            state = "PRE_BREAKDOWN"
        else:
            state = "EARLY_TOP"
        return state, round(float(support_distance_pct), 4), round(float(breakdown_support), 4)

    def scan_for_shorts(self, universe: list):
        """Scan for short opportunities with fundamental confirmation"""

        opportunities = []
        self._scan_rejects = []  # reset each call
        progress = getattr(self, "_progress_callback", None)
        vix_snapshot = get_vix_snapshot(data_feed=self.data_feed)
        vix_level = self._safe_float(vix_snapshot.get("vix_level"), 25.0)
        benchmark_cache = {}
        spy_close = None
        spy_ma200 = None
        try:
            spy_bars = self.data_feed.get_daily_bars("SPY", days_back=260, adjustment="all")
            spy_closes = [float(bar.get("close", 0.0) or 0.0) for bar in (spy_bars or []) if float(bar.get("close", 0.0) or 0.0) > 0.0]
            if spy_closes:
                spy_close = float(spy_closes[-1])
                if len(spy_closes) >= 200:
                    spy_ma200 = sum(spy_closes[-200:]) / 200.0
        except Exception as exc:
            logger.warning(f"SHORT macro gate could not load SPY context: {exc}")

        total = len(universe)
        for idx, ticker in enumerate(universe, start=1):
            if callable(progress):
                try:
                    progress(f"ticker:{idx}/{total}:{ticker}")
                except Exception:
                    pass
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=90, adjustment="all")
                if not bars or len(bars) < 30:
                    self._scan_rejects.append({
                        'ticker': ticker, 'reason': 'data_insufficient',
                        'score': 0.0, 'grade': 'F', 'rr': 0.0,
                        'options_pcr': None, 'options_gamma': None,
                    })
                    continue

                current = bars[-1]
                entry = current['close']
                closes = [b['close'] for b in bars]
                candidate_state, support_distance_pct, breakdown_support = self._derive_candidate_state(bars, entry)
                avg_volume_20 = (
                    sum(float(b.get('volume', 0.0) or 0.0) for b in bars[-20:]) / min(20, len(bars))
                    if bars else 0.0
                )
                if entry < self.short_min_price:
                    self._scan_rejects.append({
                        'ticker': ticker,
                        'reason': 'price_too_low_for_short',
                        'score': 0.0,
                        'grade': 'F',
                        'rr': 0.0,
                        'entry': entry,
                        'candidate_state': candidate_state,
                        'support_distance_pct': support_distance_pct,
                        'breakdown_support': breakdown_support,
                        'avg_volume': avg_volume_20,
                        'options_pcr': None,
                        'options_gamma': None,
                    })
                    continue
                if avg_volume_20 < self.short_min_avg_volume:
                    self._scan_rejects.append({
                        'ticker': ticker,
                        'reason': 'avg_volume_too_low_for_short',
                        'score': 0.0,
                        'grade': 'F',
                        'rr': 0.0,
                        'entry': entry,
                        'candidate_state': candidate_state,
                        'support_distance_pct': support_distance_pct,
                        'breakdown_support': breakdown_support,
                        'avg_volume': avg_volume_20,
                        'options_pcr': None,
                        'options_gamma': None,
                    })
                    continue
                ma_50 = sum(closes[-50:]) / min(50, len(closes))
                ma_200 = sum(closes[-min(200, len(closes)):]) / min(200, len(closes))
                stop, target, calculated_rr, geometry_ctx = self._build_short_geometry(
                    bars=bars,
                    entry=entry,
                    ma_200=ma_200,
                )
                rs_ctx = self._compute_relative_weakness(
                    ticker=ticker,
                    bars=bars,
                    benchmark_cache=benchmark_cache,
                )
                rs_spy = rs_ctx['relative_strength_pct']
                institutional_distribution = rs_ctx['institutional_distribution']
                volume_on_decline = rs_ctx['volume_on_decline']
                support_broken = entry < min(b['low'] for b in bars[-20:-1])
                sector_weakness = rs_ctx['sector_weakness']
                si_safe = True
                below_mas = entry < ma_50 and entry < ma_200
                downtrend = entry <= min(closes[-5:])
                downgrades = False
                insider_selling = False

                # Real options signal — for SHORT, high PCR (put-dominated) confirms bearish sentiment
                opt_sig = _get_options_score_adj(ticker) if _HAS_OPTIONS_SIGNAL else {
                    "adj": 0.0, "pcr": None, "gamma": "NEUTRAL", "source": "unavailable", "note": "module unavailable"
                }
                pcr = opt_sig.get("pcr")
                # put_flow: real put dominance (PCR > 1.0 = more puts than calls = bearish confirmation for shorts)
                # Fallback: volume_on_decline is a reasonable proxy when chain data unavailable
                put_flow = (pcr > 1.0) if pcr is not None else volume_on_decline
                # For SHORT strategy, score adj is INVERTED: high PCR = good for shorts (+), low PCR = bad (-)
                short_options_adj = -(opt_sig.get("adj", 0.0))  # invert: bearish PCR → positive adj for shorts
                accounting_concerns = False
                regulatory_risk = False
                sector = rs_ctx.get('benchmark_ticker', 'SPY')
                sector_wave = False
                inst_data_age = 30

                # PRE-FILTER: require at least one technical deterioration signal
                # before spending time on fundamental fetches and full scoring.
                # Healthy large caps above all MAs with intact support are not short candidates.
                tech_deterioration = sum([
                    entry < ma_50,        # below 50-day MA
                    support_broken,       # breached 20-day low
                    entry < ma_200,       # below 200-day MA
                ])
                if tech_deterioration == 0:
                    logger.debug(f"SHORT pre-filter rejected {ticker}: no technical deterioration (above 50MA/200MA, support intact)")
                    self._scan_rejects.append({
                        'ticker': ticker, 'reason': 'universe_prefilter_failed',
                        'score': 0.0, 'grade': 'F', 'rr': calculated_rr,
                        'entry': entry, 'stop': stop, 'target': target,
                        'candidate_state': candidate_state,
                        'support_distance_pct': support_distance_pct,
                        'breakdown_support': breakdown_support,
                        'geometry_ctx': geometry_ctx,
                        'rs_context': rs_ctx,
                        'options_pcr': opt_sig.get('pcr'),
                        'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
                    })
                    continue

                exhaustion_ctx = self._assess_downside_exhaustion(
                    bars=bars,
                    entry=entry,
                    ma_50=ma_50,
                    rs_ctx=rs_ctx,
                )
                if exhaustion_ctx.get('reject'):
                    self._scan_rejects.append({
                        'ticker': ticker,
                        'reason': 'downside_exhaustion_risk',
                        'score': 0.0,
                        'grade': 'F',
                        'rr': calculated_rr,
                        'entry': entry, 'stop': stop, 'target': target,
                        'candidate_state': candidate_state,
                        'support_distance_pct': support_distance_pct,
                        'breakdown_support': breakdown_support,
                        'geometry_ctx': geometry_ctx,
                        'rs_context': rs_ctx,
                        'exhaustion_ctx': exhaustion_ctx,
                        'options_pcr': opt_sig.get('pcr'),
                        'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
                    })
                    continue

                # Fetch fundamental deterioration data
                fundamentals = {}
                if HAS_FUNDAMENTAL_FETCHER:
                    try:
                        logger.info(f"📉 Fetching SHORT fundamentals for {ticker}...")
                        fundamentals = FundamentalDataFetcher.get_short_fundamentals(ticker)
                    except Exception as e:
                        logger.warning(f"⚠️ Could not fetch fundamentals for {ticker}: {e}")
                        fundamentals = {
                            'revenue_growth_yoy': None,
                            'revenue_deceleration': False,
                            'margin_compression': False,
                            'profit_margin_declining': False,
                            'fcf_negative': False,
                            'guidance_trend': 'stable',
                            'debt_stress': False,
                            'valuation_rich': False,
                            'market_cap': None,
                            'short_interest_safe': True,
                            'estimate_revisions': 'stable'
                        }

                market_cap = fundamentals.get('market_cap')
                if market_cap is not None and float(market_cap) < self.short_min_market_cap_usd:
                    self._scan_rejects.append({
                        'ticker': ticker,
                        'reason': 'market_cap_too_small_for_short',
                        'score': 0.0,
                        'grade': 'F',
                        'rr': calculated_rr,
                        'entry': entry,
                        'stop': stop,
                        'target': target,
                        'candidate_state': candidate_state,
                        'support_distance_pct': support_distance_pct,
                        'breakdown_support': breakdown_support,
                        'avg_volume': avg_volume_20,
                        'market_cap': market_cap,
                        'geometry_ctx': geometry_ctx,
                        'rs_context': rs_ctx,
                        'options_pcr': opt_sig.get('pcr'),
                        'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
                        })
                    continue

                margin_guard = self._margin_decay_confirmation(fundamentals)
                fundamentals_deteriorating = any([
                    fundamentals.get('revenue_deceleration'),
                    margin_guard.get('margin_decay_confirmed') and fundamentals.get('margin_compression'),
                    margin_guard.get('margin_decay_confirmed') and fundamentals.get('profit_margin_declining'),
                    fundamentals.get('fcf_negative'),
                    fundamentals.get('debt_stress'),
                    fundamentals.get('guidance_trend') == 'cutting',
                    fundamentals.get('estimate_revisions') == 'cutting',
                ])
                downgrades = (
                    fundamentals.get('guidance_trend') == 'cutting'
                    or fundamentals.get('estimate_revisions') == 'cutting'
                )
                si_safe = fundamentals.get('short_interest_safe', si_safe)
                sector_wave = sector_weakness and rs_spy <= -3.0

                scanner_data = {
                    # Existing fields...
                    'risk_reward': calculated_rr,
                    'rs_spy': rs_spy,
                    'inst_selling': institutional_distribution,
                    'fundamentals_weak': fundamentals_deteriorating,
                    'revenue_growth_yoy': margin_guard.get('revenue_growth_yoy'),

                    # Fundamental fields
                    'revenue_deceleration': fundamentals.get('revenue_deceleration'),
                    'margin_compression': fundamentals.get('margin_compression'),
                    'profit_margin_declining': fundamentals.get('profit_margin_declining'),
                    'fcf_negative': fundamentals.get('fcf_negative'),
                    'guidance_trend': fundamentals.get('guidance_trend'),
                    'debt_stress': fundamentals.get('debt_stress'),
                    'valuation_rich': fundamentals.get('valuation_rich'),
                    'short_interest_safe': fundamentals.get('short_interest_safe', si_safe),
                    'estimate_revisions': fundamentals.get('estimate_revisions'),

                    # Outlier short alpha fields
                    'earnings_window_safe': fundamentals.get('earnings_window_safe', True),
                    'short_interest_low': fundamentals.get('short_interest_low', False),
                    'short_interest_pct': fundamentals.get('short_interest_pct', 0),
                    'days_to_earnings': fundamentals.get('days_to_earnings', 999),

                    # Rest of fields...
                    'vix_elevated': vix_level > 20,
                    'volume_on_down': volume_on_decline,
                    'support_broken': support_broken,
                    'sector_weak': sector_weakness,
                    'below_mas': below_mas,
                    'lower_lows': downtrend,
                    'downgrades': downgrades,
                    'put_buying': put_flow,
                    'insider_selling': insider_selling,
                    'accounting_flags': accounting_concerns,
                    'regulatory_risk': regulatory_risk,
                    'sector': sector,
                    'sector_wave': sector_wave,
                    'inst_data_age_days': inst_data_age
                }

                checks = StrategyCheckMapper.map_short_checks(scanner_data)
                metadata = StrategyCheckMapper.build_metadata(scanner_data, 'SHORT')
                score_result = calculate_strategy_score('SHORT', checks, metadata, regime_context={'vix': vix_level})
                # Apply inverted options score adjustment: high PCR confirms shorts, low PCR warns
                if short_options_adj != 0.0:
                    score_result = apply_post_score_adjustment('SHORT', score_result, short_options_adj)
                    score_result.setdefault("options_signal", opt_sig)
                    logger.debug(f"SHORT {ticker}: options adj {short_options_adj:+.1f} ({opt_sig['note']}) → score {score_result['normalized_score']:.1f}")

                logger.info(f"\n📉 SHORT: {ticker}")
                logger.info(f"   Score: {score_result['normalized_score']:.1f}/100")
                logger.info(f"   Recommendation: {score_result['recommendation']}")
                pathway_info = score_result.get('pathway_qualification', {}) or {}
                if pathway_info:
                    logger.info(f"   Primary Pathway: {pathway_info.get('primary_pathway') or '—'}")
                    logger.info(f"   Pathways Used: {pathway_info.get('pathways_passed', [])}")
                macro_gate_ctx = self.evaluate_margin_revenue_macro_gate(
                    pathway_info.get('pathways_passed') or [],
                    spy_close=spy_close,
                    spy_ma200=spy_ma200,
                    vix_level=vix_level,
                )
                if macro_gate_ctx.get('applies'):
                    gate_status = "PASSED" if macro_gate_ctx.get('passed') else "FAILED"
                    spy_close_text = (
                        f"{float(macro_gate_ctx['spy_close']):.2f}"
                        if macro_gate_ctx.get('spy_close') is not None
                        else "n/a"
                    )
                    spy_ma200_text = (
                        f"{float(macro_gate_ctx['spy_ma200']):.2f}"
                        if macro_gate_ctx.get('spy_ma200') is not None
                        else "n/a"
                    )
                    logger.info(
                        "   Macro Gate [MARGIN+REVENUE AND]: %s | SPY %s vs 200MA %s | VIX %.1f",
                        gate_status,
                        spy_close_text,
                        spy_ma200_text,
                        float(macro_gate_ctx.get('vix_level') or 0.0),
                    )

                # Only short on STRONG SHORT or SHORT
                if score_result['recommendation'] not in ['STRONG SHORT', 'SHORT']:
                    pq = score_result.get('pathway_qualification', {}) or {}
                    pathways_passed = pq.get('pathways_passed', [])
                    rr_failed = calculated_rr < 2.5
                    execution_threshold = float(STRATEGY_CONFIGS['SHORT']['grade_thresholds']['A'])
                    score_failed = float(score_result.get('normalized_score', 0.0) or 0.0) < execution_threshold
                    pathway_failed = not bool(pq.get('qualified', bool(pathways_passed)))
                    raw_rejection_reason = str(score_result.get('rejection_reason') or '')
                    # First-failing-gate: RR → pathway → score (analytics attribution only)
                    if rr_failed:
                        normalized_reason = 'risk_reward_too_low'
                    elif raw_rejection_reason.startswith('Failed fatal check:'):
                        normalized_reason = raw_rejection_reason.replace('Failed fatal check: ', 'failed_fatal_check_', 1)
                        normalized_reason = normalized_reason.split(' ', 1)[0].strip().lower()
                    elif pathway_failed:
                        normalized_reason = 'no_short_pathway_qualified'
                    elif score_failed:
                        normalized_reason = 'score_below_threshold'
                    else:
                        normalized_reason = 'score_below_threshold'
                    # gates_failed: multi-gate annotation for calibration analytics
                    # Does NOT affect execution — purely for diagnostics
                    gates_failed = []
                    if rr_failed:
                        gates_failed.append('rr')
                    if pathway_failed:
                        gates_failed.append('pathway')
                    if raw_rejection_reason.startswith('Failed fatal check:'):
                        gates_failed.append('fatal')
                    if score_failed:
                        gates_failed.append('score')
                    self._scan_rejects.append({
                        'ticker': ticker,
                        'reason': normalized_reason,
                        'score': score_result['normalized_score'],
                        'grade': score_result.get('grade', 'F'),
                        'rr': calculated_rr,
                        'entry': entry, 'stop': stop, 'target': target,
                        'candidate_state': candidate_state,
                        'support_distance_pct': support_distance_pct,
                        'breakdown_support': breakdown_support,
                        'geometry_ctx': geometry_ctx,
                        'rs_context': rs_ctx,
                        'primary_pathway': pq.get('primary_pathway'),
                        'pathways_failed': pq.get('pathways_failed', []),
                        'qualification_note': pq.get('qualification_note'),
                        'gates_failed': gates_failed,
                        'macro_gate_context': macro_gate_ctx,
                        'options_pcr': opt_sig.get('pcr'),
                        'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
                    })
                else:
                    position = enhanced_position_size(
                        account_equity=self.account_equity,
                        entry_price=entry,
                        stop_price=stop,
                        score_result=score_result
                    )

                    if position['valid']:
                        research_only = bool(macro_gate_ctx.get('applies') and not macro_gate_ctx.get('passed'))
                        execution_block_reason = 'margin_revenue_macro_gate_failed' if research_only else None
                        if research_only:
                            logger.info(
                                "   [DORMANT] %s research_only — SHORT MARGIN+REVENUE macro gate failed (%s)",
                                ticker,
                                macro_gate_ctx.get('reason', 'gate_failed'),
                            )
                        opportunities.append({
                            'ticker': ticker,
                            'direction': 'SHORT',
                            'strategy': 'SHORT',
                            'score': score_result['normalized_score'],
                            'shares': position['shares'],
                            'entry': entry,
                            'stop': stop,
                            'target': target,
                            'candidate_state': candidate_state,
                            'support_distance_pct': support_distance_pct,
                            'breakdown_support': breakdown_support,
                            'grade': score_result.get('grade', 'N/A'),
                            'growth_mode': score_result.get('growth_mode_eligible', False),
                            'tier_breakdown': score_result.get('tier_breakdown', {}),
                            'confidence_summary': score_result.get('confidence_summary', {}),
                            'pathway_qualification': score_result.get('pathway_qualification', {}),
                            'geometry_ctx': geometry_ctx,
                            'rs_context': rs_ctx,
                            'research_only': research_only,
                            'dormant': research_only,
                            'execution_block_reason': execution_block_reason,
                            'macro_gate_context': macro_gate_ctx,
                            'options_pcr': opt_sig.get('pcr'),
                            'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
                            'options_score_adj': short_options_adj,
                        })
                    else:
                        self._scan_rejects.append({
                            'ticker': ticker, 'reason': 'position_sizing_failed',
                            'score': score_result['normalized_score'],
                            'grade': score_result.get('grade', 'F'),
                            'rr': calculated_rr,
                            'entry': entry, 'stop': stop, 'target': target,
                            'candidate_state': candidate_state,
                            'support_distance_pct': support_distance_pct,
                            'breakdown_support': breakdown_support,
                            'geometry_ctx': geometry_ctx,
                            'rs_context': rs_ctx,
                            'options_pcr': opt_sig.get('pcr'),
                            'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
                        })

            except Exception as e:
                logger.error(f"Error scanning {ticker}: {e}")
                self._scan_rejects.append({
                    'ticker': ticker, 'reason': 'data_error',
                    'score': 0.0, 'grade': 'F', 'rr': 0.0,
                    'options_pcr': None, 'options_gamma': None,
                })
                continue

        return opportunities

    def evaluate_short_opportunity(self, ticker: str):
        """Single-ticker compatibility wrapper."""
        results = self.scan_for_shorts([ticker])
        return results[0] if results else {'approved': False}

    def _build_short_geometry(self, bars, entry: float, ma_200: float):
        """
        Build structure-anchored short stop/target geometry.

        Stops should live above actual overhead structure, not at a mechanical
        fixed percent. Targets should align with the nearest meaningful support
        beneath entry, not an algebraic extension that ignores price history.
        """
        atr_14 = self._calc_atr(bars, period=14)
        completed_bars = list(bars[:-1]) if len(bars) > 1 else list(bars)
        prior_day_high = float(completed_bars[-1]['high']) if completed_bars else float(bars[-1]['high'])
        prior_window = completed_bars[:-1] if len(completed_bars) > 1 else []
        recent_high_10 = max(float(b['high']) for b in prior_window[-10:]) if prior_window else prior_day_high

        structural_stop_levels = [prior_day_high, recent_high_10]
        structural_stop = self._nearest_level_above(entry, structural_stop_levels)
        atr_stop = entry + max(atr_14 * self.short_stop_atr_mult, entry * self.short_stop_floor_pct)
        if structural_stop is not None:
            stop = structural_stop
            stop_source = 'prior_day_high' if abs(structural_stop - prior_day_high) < 1e-6 else 'recent_high_10'
        else:
            stop = max(atr_stop, entry + 0.01)
            stop_source = 'atr_fallback'

        support_levels = {}
        for window in (10, 20, 40, 60):
            segment = completed_bars[-window:] if completed_bars else []
            if segment:
                support_levels[f'recent_low_{window}'] = min(float(b['low']) for b in segment)
        if ma_200 < entry:
            support_levels['ma_200'] = float(ma_200)

        structural_target = self._nearest_level_below(entry, support_levels.values())
        if structural_target is not None:
            target = structural_target
            target_source = next(
                (name for name, level in support_levels.items() if abs(float(level) - structural_target) < 1e-6),
                'structural_support',
            )
        else:
            target = max(0.01, entry - max(atr_14 * self.short_target_atr_mult, entry * self.short_target_floor_pct))
            target_source = 'atr_fallback'

        calculated_rr = ((entry - target) / (stop - entry)) if stop > entry else 0.0
        geometry_ctx = {
            'atr_14': round(float(atr_14), 4),
            'recent_high_10': round(float(recent_high_10), 4),
            'prior_day_high': round(float(prior_day_high), 4),
            'atr_stop': round(float(atr_stop), 4),
            'stop_source': stop_source,
            'target_source': target_source,
            'support_levels': {name: round(float(level), 4) for name, level in support_levels.items()},
        }
        return float(stop), float(target), float(calculated_rr), geometry_ctx

    def _assess_downside_exhaustion(self, bars, entry: float, ma_50: float, rs_ctx: dict) -> dict:
        closes = [float(b.get('close', 0.0) or 0.0) for b in bars]
        window = bars[-20:] if len(bars) >= 20 else list(bars)
        current = bars[-1] if bars else {}
        if not window:
            return {'reject': False}

        low_20 = min(float(b.get('low', 0.0) or 0.0) for b in window)
        high_20 = max(float(b.get('high', 0.0) or 0.0) for b in window)
        range_span = max(high_20 - low_20, 0.01)
        range_position = (float(entry) - low_20) / range_span
        drawdown_from_high_pct = ((high_20 - float(entry)) / high_20) * 100.0 if high_20 > 0 else 0.0
        pct_below_ma50 = ((ma_50 - float(entry)) / ma_50) * 100.0 if ma_50 > 0 and entry < ma_50 else 0.0
        ticker_return_pct = float(rs_ctx.get('ticker_return_pct', 0.0) or 0.0)
        volume_on_decline = bool(rs_ctx.get('volume_on_decline', False))
        distribution_days_10 = int(rs_ctx.get('distribution_days_10', 0) or 0)
        rsi_14 = self._calc_rsi(closes, period=14)
        current_open = float(current.get('open', 0.0) or 0.0)
        current_low = float(current.get('low', 0.0) or 0.0)

        near_lows = range_position <= self.short_exhaustion_range_pos_max
        stretched_down = (
            drawdown_from_high_pct >= self.short_exhaustion_drawdown_min_pct
            or pct_below_ma50 >= self.short_exhaustion_below_ma50_min_pct
            or ticker_return_pct <= self.short_exhaustion_return_min_pct
        )
        oversold = rsi_14 is not None and rsi_14 <= self.short_exhaustion_rsi_max
        bottoming_bounce = (
            current_open > 0
            and float(entry) >= current_open
            and current_low <= (low_20 * 1.02)
        )
        fresh_sell_pressure = volume_on_decline

        reject = near_lows and stretched_down and (
            bottoming_bounce
            or (not fresh_sell_pressure and oversold)
        )

        return {
            'reject': reject,
            'range_position_20': round(range_position, 3),
            'drawdown_from_high_20_pct': round(drawdown_from_high_pct, 2),
            'pct_below_ma50': round(pct_below_ma50, 2),
            'ticker_return_pct': round(ticker_return_pct, 2),
            'distribution_days_10': distribution_days_10,
            'volume_on_decline': volume_on_decline,
            'rsi_14': round(rsi_14, 2) if rsi_14 is not None else None,
            'bottoming_bounce': bottoming_bounce,
            'fresh_sell_pressure': fresh_sell_pressure,
        }

    def _compute_relative_weakness(self, ticker: str, bars, benchmark_cache: dict, lookback: int = 20) -> dict:
        """
        Build a real short thesis from sector-relative underperformance.

        `rs_spy` is still the downstream field name for compatibility, but the
        actual measure here is 20-day excess return vs the ticker's sector ETF,
        with SPY as a conservative fallback. Institutional distribution is
        derived from repeated high-volume down days or a genuine breakdown
        versus benchmark, not a hardcoded placeholder.
        """
        closes = [float(b.get('close', 0.0) or 0.0) for b in bars]
        current = bars[-1]
        avg_volume_20 = (
            sum(float(b.get('volume', 0.0) or 0.0) for b in bars[-20:]) / min(20, len(bars))
            if bars else 0.0
        )
        volume_on_decline = (
            float(current.get('close', 0.0) or 0.0) < float(current.get('open', 0.0) or 0.0)
            and float(current.get('volume', 0.0) or 0.0) >= avg_volume_20 * 1.15
        )
        distribution_days_10 = sum(
            1
            for b in bars[-10:]
            if float(b.get('close', 0.0) or 0.0) < float(b.get('open', 0.0) or 0.0)
            and float(b.get('volume', 0.0) or 0.0) >= avg_volume_20 * 1.05
        )

        benchmark_ticker = 'SPY'
        resolver_source = 'fallback'
        resolver_confidence = 0.0
        if _HAS_SECTOR_RESOLVER and resolve_sector_etf is not None:
            try:
                sector_payload = resolve_sector_etf(ticker) or {}
                benchmark_ticker = (
                    sector_payload.get('sector_etf')
                    or sector_payload.get('fallback_sector_etf')
                    or 'SPY'
                )
                resolver_source = sector_payload.get('source', 'fallback')
                resolver_confidence = float(sector_payload.get('resolver_confidence', 0.0) or 0.0)
            except Exception:
                pass

        benchmark_bars = self._get_benchmark_bars(
            benchmark_ticker,
            benchmark_cache,
            days_back=max(lookback * 3, 65),
        )
        if (not benchmark_bars or len(benchmark_bars) < (lookback + 2)) and benchmark_ticker != 'SPY':
            benchmark_ticker = 'SPY'
            benchmark_bars = self._get_benchmark_bars('SPY', benchmark_cache, days_back=max(lookback * 3, 65))

        benchmark_closes = [float(b.get('close', 0.0) or 0.0) for b in benchmark_bars] if benchmark_bars else []
        ticker_roll = self._rolling_return_series(closes, lookback)
        benchmark_roll = self._rolling_return_series(benchmark_closes, lookback)

        ticker_return = 0.0
        benchmark_return = 0.0
        current_excess = 0.0
        excess_zscore = 0.0
        if ticker_roll and benchmark_roll:
            aligned = min(len(ticker_roll), len(benchmark_roll))
            ticker_roll = ticker_roll[-aligned:]
            benchmark_roll = benchmark_roll[-aligned:]
            excess_roll = [t - b for t, b in zip(ticker_roll, benchmark_roll)]
            ticker_return = float(ticker_roll[-1])
            benchmark_return = float(benchmark_roll[-1])
            current_excess = float(excess_roll[-1])
            if len(excess_roll) >= 5:
                mean_excess = statistics.mean(excess_roll)
                stdev_excess = statistics.pstdev(excess_roll)
                if stdev_excess > 0:
                    excess_zscore = (current_excess - mean_excess) / stdev_excess

        benchmark_ma50 = 0.0
        benchmark_below_ma50 = False
        if len(benchmark_closes) >= 50:
            benchmark_ma50 = sum(benchmark_closes[-50:]) / 50.0
            benchmark_below_ma50 = benchmark_closes[-1] < benchmark_ma50

        institutional_distribution = (
            distribution_days_10 >= 3
            or (
                current_excess <= -5.0
                and excess_zscore <= -1.5
                and volume_on_decline
            )
        )
        sector_weakness = benchmark_return <= -2.0 or benchmark_below_ma50

        return {
            'benchmark_ticker': benchmark_ticker,
            'resolver_source': resolver_source,
            'resolver_confidence': round(resolver_confidence, 2),
            'ticker_return_pct': round(ticker_return, 2),
            'benchmark_return_pct': round(benchmark_return, 2),
            'relative_strength_pct': round(current_excess, 2),
            'excess_zscore': round(excess_zscore, 2),
            'distribution_days_10': distribution_days_10,
            'volume_on_decline': volume_on_decline,
            'institutional_distribution': institutional_distribution,
            'sector_weakness': sector_weakness,
            'benchmark_below_ma50': benchmark_below_ma50,
            'benchmark_ma50': round(benchmark_ma50, 2) if benchmark_ma50 else None,
        }

    def _get_benchmark_bars(self, ticker: str, cache: dict, days_back: int):
        symbol = (ticker or 'SPY').upper()
        if symbol in cache:
            return cache[symbol]
        try:
            cache[symbol] = self.data_feed.get_daily_bars(symbol, days_back=days_back, adjustment="all") or []
        except Exception:
            cache[symbol] = []
        return cache[symbol]

    @staticmethod
    def _rolling_return_series(closes, window: int):
        if not closes or len(closes) <= window:
            return []
        series = []
        for idx in range(window, len(closes)):
            start = float(closes[idx - window] or 0.0)
            end = float(closes[idx] or 0.0)
            if start <= 0:
                continue
            series.append(((end / start) - 1.0) * 100.0)
        return series

    @staticmethod
    def _calc_atr(bars, period: int = 14) -> float:
        if not bars or len(bars) < 2:
            return 0.0
        trs = []
        for i in range(1, len(bars)):
            hi = float(bars[i].get('high', 0.0) or 0.0)
            lo = float(bars[i].get('low', 0.0) or 0.0)
            prev_close = float(bars[i - 1].get('close', 0.0) or 0.0)
            tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
            trs.append(tr)
        if not trs:
            return 0.0
        window = trs[-max(1, int(period)):]
        return float(sum(window) / len(window))

    @staticmethod
    def _calc_rsi(closes, period: int = 14):
        if not closes or len(closes) <= period:
            return None
        gains = []
        losses = []
        for idx in range(1, len(closes)):
            delta = float(closes[idx] or 0.0) - float(closes[idx - 1] or 0.0)
            gains.append(max(delta, 0.0))
            losses.append(max(-delta, 0.0))
        gains = gains[-period:]
        losses = losses[-period:]
        if not gains or not losses:
            return None
        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)
        if avg_loss <= 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
