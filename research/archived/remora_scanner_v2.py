"""
Remora Scanner V2 - With Weighted Scoring
Opportunistic 2-48 hour catalyst plays
"""

from enhanced_strategy_scoring import (
    apply_post_score_adjustment,
    calculate_strategy_score,
    calculate_position_size as enhanced_position_size,
)
from strategy_check_mapper import StrategyCheckMapper
import logging

try:
    from options_intelligence import get_options_score_adj as _get_options_score_adj
    _HAS_OPTIONS_SIGNAL = True
except ImportError:
    _get_options_score_adj = None
    _HAS_OPTIONS_SIGNAL = False

try:
    from live_feed import LiveFeed
    _HAS_LIVE_BLOCK_FEED = True
except ImportError:
    LiveFeed = None
    _HAS_LIVE_BLOCK_FEED = False

logger = logging.getLogger(__name__)


class RemoraScanner:
    """Remora scanner with weighted scoring integration"""

    def __init__(self, trading_client, account_equity):
        self.trading_client = trading_client
        self.data_feed = getattr(trading_client, 'data_feed', trading_client)
        self.account_equity = account_equity
        self._scan_rejects = []  # populated each scan; read by UnifiedMasterTraderV3
        self._block_trade_window_seconds = 900.0

    def _get_block_trade_participation(self, ticker: str):
        """
        Use actual live-feed block prints for institutional participation.

        Fail closed when the feed is unavailable or there is no qualifying tape.
        """
        empty = {
            "participation": False,
            "count": 0,
            "total_notional": 0.0,
            "largest_print": 0.0,
            "source": "unavailable" if not _HAS_LIVE_BLOCK_FEED else "live_feed",
        }
        if not _HAS_LIVE_BLOCK_FEED or LiveFeed is None:
            return empty
        try:
            summary = LiveFeed.get_block_trade_summary(
                ticker,
                window_seconds=self._block_trade_window_seconds,
            ) or {}
            count = int(summary.get("count", 0) or 0)
            total_notional = float(summary.get("total_notional", 0.0) or 0.0)
            largest_print = float(summary.get("largest_print", 0.0) or 0.0)
            participation = (
                (count >= 2 and total_notional >= 500_000.0)
                or largest_print >= 250_000.0
            )
            return {
                "participation": participation,
                "count": count,
                "total_notional": total_notional,
                "largest_print": largest_print,
                "source": "live_feed",
            }
        except Exception:
            return empty

    def scan_for_catalysts(self, universe: list):
        """
        Scan for catalyst-driven opportunities

        Returns:
            List of approved opportunities
        """

        opportunities = []
        self._scan_rejects = []  # reset each call
        progress = getattr(self, "_progress_callback", None)

        total = len(universe)
        for idx, ticker in enumerate(universe, start=1):
            if callable(progress):
                try:
                    progress(f"ticker:{idx}/{total}:{ticker}")
                except Exception:
                    pass
            try:
                bars = self.data_feed.get_daily_bars(ticker, days_back=30, adjustment="all")
                if not bars or len(bars) < 5:
                    self._scan_rejects.append({
                        'ticker': ticker, 'reason': 'data_insufficient',
                        'score': 0.0, 'grade': 'F', 'rr': 0.0,
                        'gates_failed': ['data'],
                        'options_pcr': None,
                        'options_gamma': None,
                    })
                    continue

                current = bars[-1]
                entry = current['close']
                avg_volume = sum(b['volume'] for b in bars[:-1]) / max(1, len(bars) - 1)
                volume_explosion = current['volume'] / avg_volume if avg_volume > 0 else 0
                price_change_pct = ((current['close'] - current.get('open', current['close'])) / current.get('open', current['close'])) * 100 if current.get('open') else 0

                # Phase 5: dynamic stop/target calibration.
                # Prior fixed math (stop=0.95*entry, target=1.15*entry) locked R/R at 3.0 for all rows.
                # This now adapts to realized volatility and catalyst intensity.
                atr_14 = self._calc_atr(bars, period=14)
                base_risk = max(float(entry) * 0.03, atr_14 * 0.9)
                catalyst_boost = min(4.0, max(0.0, volume_explosion - 1.0)) * 0.30
                momentum_boost = min(0.60, abs(price_change_pct) / 100.0)
                target_mult = 1.55 + catalyst_boost + momentum_boost
                stop = max(0.01, entry - base_risk)
                target = entry + (base_risk * target_mult)
                r_r_ratio = ((target - entry) / (entry - stop)) if entry > stop else 0

                # Two valid catalyst definitions:
                # 1. Volume explosion (>= 3x) — classic individual-stock event (earnings, FDA, etc.)
                # 2. Strong directional move (>= 3%) on elevated volume (>= 1.5x) — captures
                #    macro-driven moves where broad rallies/selloffs lift individual names
                #    without per-stock volume explosion. Without this, Remora is blind to any
                #    market-wide move regardless of price action quality.
                volume_explosion_catalyst = volume_explosion >= 3.0
                macro_sympathy_catalyst = abs(price_change_pct) >= 3.0 and volume_explosion >= 1.5
                major_catalyst = volume_explosion_catalyst or macro_sympathy_catalyst
                # macro_sympathy_active: True only when the macro path was the SOLE reason
                # catalyst_strength passed (classic 3x volume was NOT reached).
                # Passed to scorer so it can apply the 1.5x volume_explosion threshold
                # instead of the 3.0x threshold for these candidates.
                macro_sympathy_active = macro_sympathy_catalyst and not volume_explosion_catalyst
                gap_quality = abs(price_change_pct) >= 2.0
                block_summary = self._get_block_trade_participation(ticker)
                institutional_blocks = block_summary["participation"]
                velocity = abs(price_change_pct) >= 3.0
                rs_spike = abs(price_change_pct) >= 5.0
                # News quality must come from a real event/news source. Do not
                # award points from a hardcoded placeholder.
                news_fundamental = False
                sector_sympathy = False
                earnings_surprise = False
                squeeze = False
                sector = "Unknown"
                sector_wave = False

                # Real options signal — smart_money_calls = actual call dominance (PCR < 0.80)
                opt_sig = _get_options_score_adj(ticker) if _HAS_OPTIONS_SIGNAL else {
                    "adj": 0.0, "pcr": None, "gamma": "NEUTRAL", "source": "unavailable", "note": "module unavailable"
                }
                pcr = opt_sig.get("pcr")
                call_flow = (pcr < 0.80) if pcr is not None else False

                scanner_data = {
                    'catalyst_major': major_catalyst,
                    'macro_sympathy_active': macro_sympathy_active,
                    'volume_ratio': volume_explosion,
                    'risk_reward': r_r_ratio,
                    'gap_clean': gap_quality,
                    'block_trades': institutional_blocks,
                    'fast_move': velocity,
                    'rs_spike': rs_spike,
                    'smart_money_calls': call_flow,
                    'fundamental_news': news_fundamental,
                    'sector_following': sector_sympathy,
                    'earnings_beat': earnings_surprise,
                    'squeeze_active': squeeze,
                    'sector': sector,
                    'sector_wave': sector_wave
                }

                checks = StrategyCheckMapper.map_remora_checks(scanner_data)
                metadata = StrategyCheckMapper.build_metadata(scanner_data, 'REMORA')
                score_result = calculate_strategy_score('REMORA', checks, metadata)
                # Apply options score adjustment (+/-5 boost/penalty, not a veto)
                if opt_sig["adj"] != 0.0:
                    score_result = apply_post_score_adjustment('REMORA', score_result, opt_sig["adj"])
                    score_result.setdefault("options_signal", opt_sig)
                    logger.debug(f"Remora {ticker}: options adj {opt_sig['adj']:+.1f} ({opt_sig['note']}) → score {score_result['normalized_score']:.1f}")

                logger.info(f"\n⚡ REMORA: {ticker}")
                logger.info(f"   Score: {score_result['normalized_score']:.1f}/100")
                logger.info(f"   Recommendation: {score_result['recommendation']}")
                pathway_info = score_result.get('pathway_qualification', {}) or {}
                if pathway_info:
                    logger.info(f"   Primary Pathway: {pathway_info.get('primary_pathway') or '—'}")
                    logger.info(f"   Pathways Used: {pathway_info.get('pathways_passed', [])}")

                # Remora only acts on STRONG BUY (fast opportunities)
                if score_result['recommendation'] != 'STRONG BUY':
                    pq = score_result.get('pathway_qualification', {}) or {}
                    self._scan_rejects.append({
                        'ticker': ticker,
                        'reason': 'score_below_threshold',
                        'score': score_result['normalized_score'],
                        'grade': score_result.get('grade', 'F'),
                        'rr': r_r_ratio,
                        'entry': entry, 'stop': stop, 'target': target,
                        'primary_pathway': pq.get('primary_pathway'),
                        'pathways_failed': pq.get('pathways_failed', []),
                        'gates_failed': ['score'],
                        'options_pcr': opt_sig.get('pcr'),
                        'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
                        'block_trade_count': block_summary.get('count', 0),
                        'block_trade_notional': block_summary.get('total_notional', 0.0),
                        'block_trade_source': block_summary.get('source'),
                    })
                else:
                    position = enhanced_position_size(
                        account_equity=self.account_equity,
                        entry_price=entry,
                        stop_price=stop,
                        score_result=score_result
                    )

                    if position['valid']:
                        opportunities.append({
                            'ticker': ticker,
                            'strategy': 'REMORA',
                            'direction': 'LONG',
                            'score': score_result['normalized_score'],
                            'grade': score_result.get('grade', 'N/A'),
                            'shares': position['shares'],
                            'entry': entry,
                            'stop': stop,
                            'target': target,
                            'entry_price': entry,
                            'stop_loss': stop,
                            'target_price': target,
                            'risk_reward': r_r_ratio,
                            'growth_mode': score_result.get('growth_mode_eligible', False),
                            'tier_breakdown': score_result.get('tier_breakdown', {}),
                            'confidence_summary': score_result.get('confidence_summary', {}),
                            'pathway_qualification': score_result.get('pathway_qualification', {}),
                            'options_pcr': opt_sig.get('pcr'),
                            'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
                            'options_score_adj': opt_sig.get('adj', 0.0),
                            'block_trade_count': block_summary.get('count', 0),
                            'block_trade_notional': block_summary.get('total_notional', 0.0),
                            'block_trade_source': block_summary.get('source'),
                        })
                    else:
                        self._scan_rejects.append({
                            'ticker': ticker, 'reason': 'position_sizing_failed',
                            'score': score_result['normalized_score'],
                            'grade': score_result.get('grade', 'F'),
                            'rr': r_r_ratio,
                            'entry': entry, 'stop': stop, 'target': target,
                            'gates_failed': ['score'],
                            'options_pcr': opt_sig.get('pcr'),
                            'options_gamma': opt_sig.get('gamma', 'NEUTRAL'),
                            'block_trade_count': block_summary.get('count', 0),
                            'block_trade_notional': block_summary.get('total_notional', 0.0),
                            'block_trade_source': block_summary.get('source'),
                        })

            except Exception as e:
                logger.error(f"Error scanning {ticker}: {e}")
                self._scan_rejects.append({
                    'ticker': ticker, 'reason': 'data_error',
                    'score': 0.0, 'grade': 'F', 'rr': 0.0,
                    'gates_failed': ['data'],
                    'options_pcr': None,
                    'options_gamma': None,
                })
                continue

        return opportunities

    @staticmethod
    def _calc_atr(bars, period: int = 14) -> float:
        """Small ATR helper for dynamic stop/target sizing."""
        if not bars or len(bars) < 2:
            return 0.0
        trs = []
        for i in range(1, len(bars)):
            hi = float(bars[i].get("high", 0.0) or 0.0)
            lo = float(bars[i].get("low", 0.0) or 0.0)
            prev_close = float(bars[i - 1].get("close", 0.0) or 0.0)
            tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
            trs.append(tr)
        if not trs:
            return 0.0
        window = trs[-max(1, int(period)):]
        return float(sum(window) / len(window))

    def evaluate_remora_opportunity(self, ticker: str):
        """Single-ticker compatibility wrapper."""
        results = self.scan_for_catalysts([ticker])
        return results[0] if results else {'approved': False}
