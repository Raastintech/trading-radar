"""
Strategy Check Mapper
Maps scanner outputs to weighted scoring checks for all 4 strategies

Usage:
    from strategy_check_mapper import StrategyCheckMapper

    checks = StrategyCheckMapper.map_voyager_checks(scanner_data)
    metadata = StrategyCheckMapper.build_metadata(scanner_data, 'VOYAGER')
"""

from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


class StrategyCheckMapper:
    """Map scanner data to weighted scoring checks"""

    @staticmethod
    def map_voyager_checks(scanner_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map Voyager checks - GROWTH-FIRST, QUALITY-CONFIRMED

        Pathways (priority order):
        1. GROWTH: Strong revenue growth >=15%
        2. GROWTH_INFLECTION: Moderate growth (10-14%) + improving economics
        3. OPERATING_LEVERAGE: Rule of 40 + base growth >=8%
        4. QUALITY: Exceptional margins + FCF + minimal growth >=5%

        Philosophy: Growth-first, quality-confirmed, asymmetry-seeking
        """
        revenue_growth = scanner_data.get('revenue_growth_yoy', 0)

        return {
            # Tier 1 (unchanged)
            'risk_reward_ratio': scanner_data.get('risk_reward', 0),
            'relative_strength_spy': scanner_data.get('rs_spy', 0),
            'institutional_accumulation': scanner_data.get('inst_buying', False),
            'vix_regime_match': scanner_data.get('regime_compatible', False),

            # Tier 2 - GROWTH-FIRST PATHWAYS
            'revenue_growth_strong': revenue_growth,
            'revenue_growth_moderate': revenue_growth,
            'margin_expanding': scanner_data.get('margin_improving', False),
            'fcf_inflection': scanner_data.get('fcf_inflection', False),
            'rule_of_40_pass': scanner_data.get('rule_of_40_pass', False),
            'revenue_growth_base': revenue_growth,
            'gross_margin_exceptional': scanner_data.get('gross_margin', 0),
            'fcf_positive': scanner_data.get('fcf_positive', False),
            'revenue_growth_minimal': revenue_growth,
            'volume_liquidity': scanner_data.get('avg_volume', 0),

            # Tier 3 - Quality confirmation
            'true_accumulation_zone': scanner_data.get('true_accumulation_zone', False),
            'base_proximity_200ma': scanner_data.get('close_vs_200ma', 0),
            'zone_score_quality': scanner_data.get('zone_score', 0),
            'valuation_sanity': scanner_data.get('valuation_reasonable', False),
            'debt_manageable': scanner_data.get('debt_manageable', False),
            'sector_rs_score': scanner_data.get('sector_rs_score'),
            'institutional_quality': scanner_data.get('inst_ownership_high', False),

            # Tier 4 Bonus (unchanged)
            'options_flow': scanner_data.get('call_flow', False),
            # sector_alpha_bonus handled via metadata
        }

    @staticmethod
    def map_sniper_checks(scanner_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map Sniper scanner output to weighted scoring checks

        Required scanner_data keys:
        - risk_reward: float (R/R ratio, min 2.5:1)
        - atr_contraction_pct: float (e.g., 0.35 for 35% squeeze)
        - volume_ratio: float (e.g., 2.8 for 2.8x volume)
        - rs_improving: bool (RS acceleration)
        """

        return {
            # Tier 1 Critical (47 points max)
            'risk_reward_ratio': scanner_data.get('risk_reward', 0),
            'atr_contraction': scanner_data.get('atr_contraction_pct', 0),
            'volume_surge_breakout': scanner_data.get('volume_ratio', 0),
            'relative_strength_acceleration': scanner_data.get('rs_improving', False),

            # Tier 2 High (30 points max)
            'clean_base_formation': scanner_data.get('base_quality', False),
            'breakout_conviction': scanner_data.get('breakout_clean', False),
            'institutional_ownership': bool(scanner_data.get('institutional_ownership', False)),
            'vix_regime_match': scanner_data.get('vix_favorable', False),

            # Tier 3 Medium (16 points max)
            'price_pattern_quality': scanner_data.get('pattern_quality', False),
            'gap_up_potential': scanner_data.get('gap_likely', False),
            'sector_momentum': scanner_data.get('sector_momentum', False),
            'earnings_proximity': scanner_data.get('safe_earnings_window', False),

            # Tier 4 Bonus (7 points max)
            'options_activity': scanner_data.get('unusual_options', False),
            'technical_indicators': scanner_data.get('indicators_confirm', False)
        }

    @staticmethod
    def map_remora_checks(scanner_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map Remora scanner output to weighted scoring checks

        Required scanner_data keys:
        - catalyst_major: bool (Major catalyst present)
        - volume_ratio: float (e.g., 5.2 for 5.2x, min 3x)
        - risk_reward: float (R/R ratio, min 1.5:1)
        """

        return {
            # Tier 1 Critical (45 points max)
            'catalyst_strength': scanner_data.get('catalyst_major', False),
            'volume_explosion': scanner_data.get('volume_ratio', 0),
            'risk_reward_ratio': scanner_data.get('risk_reward', 0),
            # Metadata flag: scorer uses this to apply 1.5x volume threshold instead of 3.0x
            # for macro-sympathy candidates. Ignored by any tier it doesn't map to.
            'macro_sympathy_active': scanner_data.get('macro_sympathy_active', False),

            # Tier 2 High (30 points max)
            'gap_quality': scanner_data.get('gap_clean', False),
            'institutional_participation': scanner_data.get('block_trades', False),
            'price_action_velocity': scanner_data.get('fast_move', False),

            # Tier 3 Medium (20 points max)
            'relative_strength_spike': scanner_data.get('rs_spike', False),
            'options_flow': scanner_data.get('smart_money_calls', False)
        }

    @staticmethod
    def map_short_checks(scanner_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map SHORT checks - MULTI-PATH DETERIORATION

        Pathways:
        - REVENUE: Declining revenue
        - MARGIN: Compressing margins
        - GUIDANCE: Cuts and negative revisions
        - STRESS: Cash flow and balance sheet problems
        """

        return {
            # Tier 1 (unchanged)
            'risk_reward_ratio': scanner_data.get('risk_reward', 0),
            'relative_strength_spy': scanner_data.get('rs_spy', 0),
            'institutional_distribution': scanner_data.get('inst_selling', False),
            'fundamental_deterioration': scanner_data.get('fundamentals_weak', False),
            'revenue_growth_yoy': scanner_data.get('revenue_growth_yoy'),

            # Tier 2 - DETERIORATION PATHWAYS
            'revenue_declining': scanner_data.get('revenue_deceleration', False),
            'margin_compressing': scanner_data.get('margin_compression', False),
            'profitability_deteriorating': scanner_data.get('profit_margin_declining', False),
            'guidance_cut': scanner_data.get('guidance_trend', 'stable') == 'cutting',
            'estimates_declining': scanner_data.get('estimate_revisions', 'stable') == 'cutting',
            'fcf_negative': scanner_data.get('fcf_negative', False),
            'debt_stress': scanner_data.get('debt_stress', False),
            'vix_elevated': scanner_data.get('vix_elevated', False),

            # Tier 3 - Amplifying factors
            'valuation_still_rich': scanner_data.get('valuation_rich', False),
            'technical_breakdown': scanner_data.get('support_broken', False),
            'sector_weakness': scanner_data.get('sector_weak', False),
            'short_interest_safe': scanner_data.get('short_interest_safe', False),
            'earnings_window_safe': scanner_data.get('earnings_window_safe', False),
            'short_interest_low': scanner_data.get('short_interest_low', False),

            # Tier 4 Bonus (unchanged)
        }

    @staticmethod
    def build_metadata(scanner_data: Dict[str, Any], strategy: str) -> Dict[str, Any]:
        """
        Build metadata for any strategy

        Args:
            scanner_data: Scanner output dict
            strategy: 'VOYAGER', 'SNIPER', 'REMORA', or 'SHORT'

        Returns:
            Metadata dict for calculate_strategy_score()
        """

        metadata = {
            'sector': scanner_data.get('sector', 'Unknown'),
            'sector_bonus_eligible': scanner_data.get('sector_wave', False)
        }

        # Add recency for strategies that need it
        if strategy in ['VOYAGER', 'SHORT']:
            metadata['institutional_data_age_days'] = scanner_data.get('inst_data_age_days', 30)

        return metadata

    @staticmethod
    def log_mapping_debug(ticker: str, scanner_data: Dict[str, Any], checks: Dict[str, Any]):
        """Debug helper - log what was mapped"""

        logger.debug(f"\n{ticker} Check Mapping:")
        logger.debug(f"  Scanner Data Keys: {list(scanner_data.keys())}")
        logger.debug(f"  Checks Keys: {list(checks.keys())}")
        logger.debug(f"  Critical Checks: {[k for k, v in checks.items() if v and 'ratio' in k or 'strength' in k]}")
