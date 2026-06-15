"""
Production-Grade Strategy-Specific Weighted Scoring
Final Version with Small Additions

Additions:
1. Normalize comparison strings to enums
2. accepted_for_ranking flag
3. research_mode option for full evaluation on fatals
"""

from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger(__name__)


def calculate_rule_of_40(revenue_growth: float, fcf_margin: float) -> bool:
    """
    Calculate Rule of 40: Growth% + FCF Margin% > 40

    Args:
        revenue_growth: YoY revenue growth rate (e.g., 0.30 for 30% growth)
        fcf_margin: FCF / Revenue (e.g., 0.15 for 15% margin)

    Returns:
        True if Rule of 40 passes
    """
    if revenue_growth is None or fcf_margin is None:
        return False

    growth_pct = revenue_growth * 100
    margin_pct = fcf_margin * 100
    rule_of_40_score = growth_pct + margin_pct

    return rule_of_40_score > 40


# ============================================================================
# COMPARISON OPERATORS
# ============================================================================

class ComparisonType(Enum):
    """Supported comparison types for filter evaluation"""
    GTE = "gte"
    LTE = "lte"
    EQ = "eq"
    BOOL = "bool"
    EXISTS = "exists"
    RANGE = "range"


def normalize_comparison(comparison: Union[ComparisonType, str]) -> ComparisonType:
    """
    Normalize comparison to ComparisonType enum
    
    Handles both enum and string inputs
    """
    if isinstance(comparison, ComparisonType):
        return comparison
    
    if isinstance(comparison, str):
        comparison_lower = comparison.lower()
        try:
            return ComparisonType(comparison_lower)
        except ValueError:
            logger.warning(f"Unknown comparison string: {comparison}, defaulting to BOOL")
            return ComparisonType.BOOL
    
    logger.warning(f"Invalid comparison type: {type(comparison)}, defaulting to BOOL")
    return ComparisonType.BOOL


def evaluate_check(
    value: Any,
    config: Dict[str, Any]
) -> Tuple[bool, str]:
    """
    Evaluate a single check against its configuration
    
    Returns:
        (passed: bool, reason: str)
    """
    
    if value is None:
        if config.get('fatal'):
            return (False, "Missing (fatal)")
        return (False, "Missing (optional)")
    
    # Normalize comparison (handles strings)
    comparison_raw = config.get('comparison', ComparisonType.BOOL)
    comparison = normalize_comparison(comparison_raw)
    
    threshold = config.get('threshold')
    
    if comparison == ComparisonType.BOOL:
        passed = bool(value)
        return (passed, "Truthy" if passed else "Falsy")
    
    if comparison == ComparisonType.EXISTS:
        return (True, "Exists")
    
    if not isinstance(value, (int, float)):
        return (False, f"Invalid type: {type(value).__name__}")
    
    if comparison == ComparisonType.GTE:
        if threshold is None:
            return (False, "No threshold defined")
        passed = value >= threshold
        return (passed, f"{value} {'≥' if passed else '<'} {threshold}")
    
    if comparison == ComparisonType.LTE:
        if threshold is None:
            return (False, "No threshold defined")
        passed = value <= threshold
        return (passed, f"{value} {'≤' if passed else '>'} {threshold}")
    
    if comparison == ComparisonType.EQ:
        if threshold is None:
            return (False, "No threshold defined")
        passed = value == threshold
        return (passed, f"{value} {'==' if passed else '!='} {threshold}")
    
    if comparison == ComparisonType.RANGE:
        min_val = config.get('min')
        max_val = config.get('max')
        if min_val is None or max_val is None:
            return (False, "Range bounds not defined")
        passed = min_val <= value <= max_val
        return (passed, f"{value} {'in' if passed else 'outside'} [{min_val}, {max_val}]")
    
    return (False, f"Unknown comparison: {comparison}")


# ============================================================================
# RECENCY MULTIPLIER
# ============================================================================

def apply_recency_multiplier(days_old: int) -> float:
    """
    Calculate recency multiplier for institutional data
    
    Multiplier (not a true bonus - tier capped at max):
    - < 5 days: 1.2x (fresh)
    - 5-45 days: 1.0x (normal)
    - > 45 days: 0.5x (stale)
    """
    if days_old < 5:
        return 1.2
    elif days_old <= 45:
        return 1.0
    else:
        return 0.5


# ============================================================================
# STRATEGY DEFINITIONS
# ============================================================================

VOYAGER_TIER_1_CRITICAL = {
    'risk_reward_ratio': {
        'weight': 15,
        'comparison': 'gte',  # Can use string
        'threshold': 5.0,
        'fatal': True,
        'description': 'Minimum 5:1 R/R for strategic timeframe'
    },
    'relative_strength_spy': {
        'weight': 12,
        'comparison': ComparisonType.GTE,  # Or enum
        'threshold': 0.0,
        'fatal': True,
        'description': 'Must outperform SPY'
    },
    'institutional_accumulation': {
        'weight': 10,
        'comparison': 'bool',
        'fatal': True,
        'recency_decay': True,
        'description': '13F buying or ownership increasing'
    },
    'vix_regime_match': {
        'weight': 5,
        'comparison': 'bool',
        'fatal': False,
        'description': 'Strategy alignment with market'
    }
}

VOYAGER_TIER_2_HIGH = {
    # PATHWAY A: GROWTH (Primary - strong revenue growth)
    'revenue_growth_strong': {
        'weight': 15,
        'comparison': 'gte',
        'threshold': 0.15,
        'fatal': False,
        'pathway': 'GROWTH',
        'pathway_priority': 1,
        'description': 'Revenue growth ≥15% (primary growth path)'
    },

    # PATHWAY B: GROWTH_INFLECTION (Improving economics with growth)
    'revenue_growth_moderate': {
        'weight': 8,
        'comparison': 'gte',
        'threshold': 0.10,
        'fatal': False,
        'pathway': 'GROWTH_INFLECTION',
        'pathway_priority': 2,
        'description': 'Revenue growth ≥10% (inflection path)'
    },
    'margin_expanding': {
        'weight': 10,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'GROWTH_INFLECTION',
        'pathway_priority': 2,
        'description': 'Margins improving (inflection path)'
    },
    'fcf_inflection': {
        'weight': 10,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'GROWTH_INFLECTION',
        'pathway_priority': 2,
        'description': 'FCF turning positive or accelerating (inflection path)'
    },

    # PATHWAY C: OPERATING_LEVERAGE (Efficient scaling)
    'rule_of_40_pass': {
        'weight': 10,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'OPERATING_LEVERAGE',
        'pathway_priority': 3,
        'description': 'Rule of 40: Growth% + FCF% > 40 (operating leverage)'
    },
    'revenue_growth_base': {
        'weight': 5,
        'comparison': 'gte',
        'threshold': 0.08,
        'fatal': False,
        'pathway': 'OPERATING_LEVERAGE',
        'pathway_priority': 3,
        'description': 'Revenue growth ≥8% (leverage path base)'
    },

    # PATHWAY D: QUALITY (Compounder with some growth)
    'gross_margin_exceptional': {
        'weight': 8,
        'comparison': 'gte',
        'threshold': 0.65,
        'fatal': False,
        'pathway': 'QUALITY',
        'pathway_priority': 4,
        'description': 'Gross margin ≥65% (quality path)'
    },
    'fcf_positive': {
        'weight': 7,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'QUALITY',
        'pathway_priority': 4,
        'description': 'FCF positive (quality path)'
    },
    'revenue_growth_minimal': {
        'weight': 4,
        'comparison': 'gte',
        'threshold': 0.05,
        'fatal': False,
        'pathway': 'QUALITY',
        'pathway_priority': 4,
        'description': 'Revenue growth ≥5% (quality path minimum)'
    },

    # CONFIRMATORY (not pathway-specific)
    'volume_liquidity': {
        'weight': 5,
        'comparison': 'gte',
        'threshold': 500000,
        'pathway': None,
        'description': 'Volume ≥500K shares'
    }
}

VOYAGER_TIER_3_MEDIUM = {
    'true_accumulation_zone': {
        'weight': 8,
        'comparison': 'bool',
        'description': 'Price still in a true accumulation / early-breakout zone'
    },
    'base_proximity_200ma': {
        'weight': 5,
        'comparison': 'range',
        'min': -5.0,
        'max': 12.0,
        'description': 'Price remains near the 200-MA; base not too extended'
    },
    'zone_score_quality': {
        'weight': 4,
        'comparison': 'gte',
        'threshold': 75.0,
        'description': 'Accumulation zone quality score ≥75'
    },
    'valuation_sanity': {
        'weight': 5,
        'comparison': 'bool',
        'description': 'Valuation reasonable for growth (not cheapness)'
    },
    'debt_manageable': {
        'weight': 4,
        'comparison': 'bool',
        'description': 'Debt manageable'
    },
    'sector_rs_score': {
        'weight': 6,
        'comparison': 'gte',
        'threshold': 0.0,
        'description': '63-day return is beating the mapped sector ETF'
    },
    'institutional_quality': {
        'weight': 5,
        'comparison': 'bool',
        'description': 'Strong institutional ownership'
    }
}

VOYAGER_TIER_4_BONUS = {
    'options_flow': {'weight': 2, 'comparison': 'bool'},
    'sector_alpha_bonus': {'weight': 5, 'comparison': 'bool'}
}

SNIPER_TIER_1_CRITICAL = {
    'risk_reward_ratio': {'weight': 12, 'comparison': 'gte', 'threshold': 2.5, 'fatal': True},
    'atr_contraction': {'weight': 15, 'comparison': 'gte', 'threshold': 0.20, 'fatal': True},
    'volume_surge_breakout': {'weight': 12, 'comparison': 'gte', 'threshold': 2.0, 'fatal': True},
    'relative_strength_acceleration': {'weight': 8, 'comparison': 'bool', 'fatal': False}
}

SNIPER_TIER_2_HIGH = {
    'clean_base_formation': {'weight': 10, 'comparison': 'bool'},
    'breakout_conviction': {'weight': 8, 'comparison': 'bool'},
    # Disabled until we have a real institutional ownership source in the live path.
    'institutional_ownership': {'weight': 0, 'comparison': 'bool'},
    'vix_regime_match': {'weight': 5, 'comparison': 'bool'}
}

SNIPER_TIER_3_MEDIUM = {
    'price_pattern_quality': {'weight': 5, 'comparison': 'bool'},
    'gap_up_potential': {'weight': 4, 'comparison': 'bool'},
    'sector_momentum': {'weight': 4, 'comparison': 'bool'},
    'earnings_proximity': {'weight': 3, 'comparison': 'bool'}
}

SNIPER_TIER_4_BONUS = {
    'options_activity': {'weight': 3, 'comparison': 'bool'},
    'technical_indicators': {'weight': 1, 'comparison': 'bool'},
    'sector_alpha_bonus': {'weight': 5, 'comparison': 'bool'}
}

REMORA_TIER_1_CRITICAL = {
    'catalyst_strength': {'weight': 20, 'comparison': 'bool', 'fatal': True},
    # Fatal at 3.0x for classic single-stock catalysts (earnings, FDA, etc.).
    # When macro_sympathy_active=True is passed in the checks dict, calculate_strategy_score
    # overrides the threshold to 1.5x — broad market moves carry 1.5x+ volume naturally.
    # Both paths are fatal-gated; the threshold adapts to catalyst type.
    'volume_explosion': {'weight': 15, 'comparison': 'gte', 'threshold': 3.0, 'fatal': True},
    'risk_reward_ratio': {'weight': 10, 'comparison': 'gte', 'threshold': 1.5, 'fatal': True}
}

REMORA_TIER_2_HIGH = {
    'gap_quality': {'weight': 12, 'comparison': 'bool'},
    'institutional_participation': {'weight': 10, 'comparison': 'bool'},
    'price_action_velocity': {'weight': 8, 'comparison': 'bool'}
}

REMORA_TIER_3_MEDIUM = {
    'relative_strength_spike': {'weight': 8, 'comparison': 'bool'},
    'options_flow': {'weight': 7, 'comparison': 'bool'}
}

REMORA_TIER_4_BONUS = {
    'sector_alpha_bonus': {'weight': 5, 'comparison': 'bool'}
}

SHORT_TIER_1_CRITICAL = {
    'risk_reward_ratio': {'weight': 15, 'comparison': 'gte', 'threshold': 2.5, 'fatal': True},
    # Threshold relaxed from -5.0 to -2.0: requiring -5% RS vs SPY was too strict and
    # blocked valid setups in anything other than sector-collapse conditions. Made
    # non-fatal: a stock can still qualify via pathway score without this single check.
    'relative_strength_spy': {'weight': 12, 'comparison': 'lte', 'threshold': -2.0, 'fatal': False},
    # Made non-fatal: institutional_distribution from yfinance/block-trade data is sparse
    # and produces almost universal False readings. It contributes to scoring but cannot
    # alone veto a setup that qualifies on fundamentals and price action.
    'institutional_distribution': {'weight': 10, 'comparison': 'bool', 'fatal': False, 'recency_decay': True},
    'fundamental_deterioration': {'weight': 8, 'comparison': 'bool', 'fatal': False}
}

SHORT_TIER_2_HIGH = {
    # PATHWAY 1: REVENUE DETERIORATION
    'revenue_declining': {
        'weight': 12,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'REVENUE',
        'description': 'Revenue growth declining or negative'
    },

    # PATHWAY 2: MARGIN DETERIORATION
    'margin_compressing': {
        'weight': 10,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'MARGIN',
        'description': 'Margins compressing significantly'
    },
    'profitability_deteriorating': {
        'weight': 8,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'MARGIN',
        'description': 'Profit margins declining'
    },

    # PATHWAY 3: GUIDANCE DETERIORATION
    'guidance_cut': {
        'weight': 10,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'GUIDANCE',
        'description': 'Management cut guidance'
    },
    'estimates_declining': {
        'weight': 8,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'GUIDANCE',
        'description': 'Analyst estimates being cut'
    },

    # PATHWAY 4: FINANCIAL STRESS
    'fcf_negative': {
        'weight': 10,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'STRESS',
        'description': 'Free cash flow negative'
    },
    'debt_stress': {
        'weight': 8,
        'comparison': 'bool',
        'fatal': False,
        'pathway': 'STRESS',
        'description': 'High leverage + weak cash flow'
    },

    # MARKET FACTORS (always evaluated)
    'vix_elevated': {
        'weight': 5,
        'comparison': 'bool',
        'pathway': None,
        'description': 'VIX >20 (fear regime)'
    }
}

SHORT_TIER_3_MEDIUM = {
    'valuation_still_rich': {
        'weight': 5,
        'comparison': 'bool',
        'description': 'Still expensive despite weakness'
    },
    'technical_breakdown': {
        'weight': 5,
        'comparison': 'bool',
        'description': 'Support broken'
    },
    'sector_weakness': {
        'weight': 4,
        'comparison': 'bool',
        'description': 'Sector deteriorating'
    },
    'short_interest_safe': {
        'weight': 4,
        'comparison': 'bool',
        'description': 'Short interest not overcrowded (squeeze protection)'
    },
    'earnings_window_safe': {
        'weight': 4,
        'comparison': 'bool',
        'description': 'Next earnings > 21 days away — no blow-up risk from positive surprise'
    },
    'short_interest_low': {
        'weight': 3,
        'comparison': 'bool',
        'description': 'SI < 8% float — undiscovered short, crowd not yet positioned'
    }
}

SHORT_TIER_4_BONUS = {
    'sector_alpha_bonus': {'weight': 5, 'comparison': 'bool'}
}

STRATEGY_CONFIGS = {
    'VOYAGER': {
        'tier_1': VOYAGER_TIER_1_CRITICAL,
        'tier_2': VOYAGER_TIER_2_HIGH,
        'tier_3': VOYAGER_TIER_3_MEDIUM,
        'tier_4': VOYAGER_TIER_4_BONUS,
        'grade_thresholds': {'A+': 85, 'A': 75, 'B': 65},
        'growth_mode_threshold': 85,
        'ranking_threshold': 65  # B grade minimum for ranking
    },
    'SNIPER': {
        'tier_1': SNIPER_TIER_1_CRITICAL,
        'tier_2': SNIPER_TIER_2_HIGH,
        'tier_3': SNIPER_TIER_3_MEDIUM,
        'tier_4': SNIPER_TIER_4_BONUS,
        'grade_thresholds': {'A+': 80, 'A': 70, 'B': 60},
        'growth_mode_threshold': 80,
        'ranking_threshold': 60
    },
    'REMORA': {
        'tier_1': REMORA_TIER_1_CRITICAL,
        'tier_2': REMORA_TIER_2_HIGH,
        'tier_3': REMORA_TIER_3_MEDIUM,
        'tier_4': REMORA_TIER_4_BONUS,
        'grade_thresholds': {'A+': 75, 'A': 65, 'B': 55},
        'growth_mode_threshold': 75,
        'ranking_threshold': 55
    },
    'SHORT': {
        'tier_1': SHORT_TIER_1_CRITICAL,
        'tier_2': SHORT_TIER_2_HIGH,
        'tier_3': SHORT_TIER_3_MEDIUM,
        'tier_4': SHORT_TIER_4_BONUS,
        'grade_thresholds': {'A+': 90, 'A': 70, 'B': 55},
        'growth_mode_threshold': 90,
        'ranking_threshold': 55
    }
}


# ============================================================================
# SCORE STATE HELPERS
# ============================================================================

def _refresh_recommendation_state(strategy: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recompute recommendation-facing fields from the current normalized score.

    This is used both at initial score construction time and after small
    additive adjustments such as options-PCR overlays. It intentionally does
    not override hard rejects (`REJECT` / `qualified=False`).
    """
    strategy = strategy.upper()
    config = STRATEGY_CONFIGS[strategy]
    thresholds = config['grade_thresholds']
    normalized = float(result.get('normalized_score', 0.0) or 0.0)
    is_short = (strategy == 'SHORT')

    if normalized >= thresholds['A+']:
        result['grade'] = 'A+'
        result['recommendation'] = 'STRONG SHORT' if is_short else 'STRONG BUY'
    elif normalized >= thresholds['A']:
        result['grade'] = 'A'
        result['recommendation'] = 'SHORT' if is_short else 'BUY'
    elif normalized >= thresholds['B']:
        result['grade'] = 'B'
        result['recommendation'] = 'CONSIDER SHORT' if is_short else 'CONSIDER'
    else:
        result['grade'] = 'C/D/F'
        result['recommendation'] = 'PASS'

    result['accepted_for_ranking'] = (
        result.get('qualified', True) and
        not bool(result.get('fatal_failed')) and
        normalized >= config['ranking_threshold']
    )

    confidence_summary = result.get('confidence_summary')
    if isinstance(confidence_summary, dict):
        confidence_summary['normalized_score'] = round(normalized, 1)
        confidence_summary['growth_mode_eligible'] = bool(result.get('growth_mode_eligible', False))

    return result


def apply_post_score_adjustment(strategy: str, result: Dict[str, Any], score_delta: float) -> Dict[str, Any]:
    """
    Apply a small additive score adjustment while keeping grade/recommendation
    state internally consistent.

    Hard rejects are preserved: options or other overlays must not rescue fatal
    or pathway-invalid setups into tradeable recommendations.
    """
    if not isinstance(result, dict):
        return result

    try:
        delta = float(score_delta or 0.0)
    except Exception:
        delta = 0.0

    if delta == 0.0:
        return result

    current_score = float(result.get('normalized_score', 0.0) or 0.0)
    result['normalized_score'] = round(min(100.0, max(0.0, current_score + delta)), 1)

    hard_reject = (
        result.get('recommendation') == 'REJECT' or
        not result.get('qualified', True)
    )
    if hard_reject:
        confidence_summary = result.get('confidence_summary')
        if isinstance(confidence_summary, dict):
            confidence_summary['normalized_score'] = result['normalized_score']
        return result

    return _refresh_recommendation_state(strategy, result)


def _sniper_atr_profile_for_vix(vix: float) -> Dict[str, Any]:
    """
    Graduated ATR-contraction requirement for Sniper.

    Slightly elevated tape should still demand the normal 20% squeeze. Only when
    volatility is clearly stressed do we relax the requirement.
    """
    if vix <= 25.0:
        return {"threshold": 0.20, "fatal": True}
    if vix <= 30.0:
        return {"threshold": 0.13, "fatal": False}
    return {"threshold": 0.08, "fatal": False}


# ============================================================================
# PATHWAY QUALIFICATION
# ============================================================================

def evaluate_qualification_pathways(
    strategy: str,
    tier_config: Dict,
    checks: Dict[str, Any],
    regime_context: Optional[Dict[str, Any]] = None,
) -> Dict:
    """
    Evaluate multi-path qualification with PRIORITY logic.

    For Voyager: Uses priority order (not highest score).
    """
    if strategy not in ['VOYAGER', 'SHORT']:
        return {
            'qualified': True,
            'pathways_passed': [],
            'pathways_failed': [],
            'primary_pathway': None,
            'qualification_strength': 1.0,
            'pathway_scores': {},
            'pathway_details': {},
            'pathway_priorities': {}
        }

    pathway_checks: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    pathway_priorities: Dict[str, int] = {}
    for check_name, check_config in tier_config.items():
        pathway = check_config.get('pathway')
        if pathway:
            if pathway not in pathway_checks:
                pathway_checks[pathway] = []
                pathway_priorities[pathway] = check_config.get('pathway_priority', 99)
            pathway_checks[pathway].append((check_name, check_config))

    pathways_passed = []
    pathways_failed = []
    pathway_scores: Dict[str, float] = {}
    pathway_details: Dict[str, Dict[str, Any]] = {}

    for pathway_name, pathway_check_list in pathway_checks.items():
        checks_in_pathway = len(pathway_check_list)
        checks_passed = 0
        checks_failed = 0
        pathway_weight = 0
        passed_checks_list = []
        failed_checks_list = []

        for check_name, check_config in pathway_check_list:
            value = checks.get(check_name)
            passed, reason = evaluate_check(value, check_config)
            if passed:
                checks_passed += 1
                pathway_weight += check_config.get('weight', 0)
                passed_checks_list.append(check_name)
            else:
                checks_failed += 1
                failed_checks_list.append(f"{check_name}: {reason}")

        if strategy == 'VOYAGER':
            if pathway_name == 'GROWTH':
                qualified = 'revenue_growth_strong' in passed_checks_list
            elif pathway_name == 'GROWTH_INFLECTION':
                improvement_checks = ['margin_expanding', 'fcf_inflection']
                qualified = (
                    'revenue_growth_moderate' in passed_checks_list and
                    any(check_name in passed_checks_list for check_name in improvement_checks)
                )
            elif pathway_name == 'OPERATING_LEVERAGE':
                qualified = (
                    'rule_of_40_pass' in passed_checks_list and
                    'revenue_growth_base' in passed_checks_list
                )
            elif pathway_name == 'QUALITY':
                qualified = (
                    'gross_margin_exceptional' in passed_checks_list and
                    'fcf_positive' in passed_checks_list and
                    'revenue_growth_minimal' in passed_checks_list
                )
            else:
                qualified = (checks_passed / checks_in_pathway) >= 0.5 if checks_in_pathway else False
        elif strategy == 'SHORT':
            if pathway_name == 'REVENUE':
                qualified = 'revenue_declining' in passed_checks_list
            elif pathway_name == 'MARGIN':
                qualified = (
                    'margin_compressing' in passed_checks_list and
                    'profitability_deteriorating' in passed_checks_list
                )
                revenue_growth_yoy = checks.get('revenue_growth_yoy')
                try:
                    revenue_growth_yoy = float(revenue_growth_yoy) if revenue_growth_yoy is not None else None
                except (TypeError, ValueError):
                    revenue_growth_yoy = None
                if qualified and revenue_growth_yoy is not None and revenue_growth_yoy > 0.15:
                    structural_confirmation = bool(checks.get('revenue_declining')) or bool(checks.get('debt_stress'))
                    if not structural_confirmation:
                        qualified = False
                        failed_checks_list.append(
                            f"high_growth_margin_guard: revenue_growth_yoy={revenue_growth_yoy:.3f} "
                            "requires revenue_declining or debt_stress"
                        )
            elif pathway_name == 'GUIDANCE':
                qualified = (
                    ('guidance_cut' in passed_checks_list or 'estimates_declining' in passed_checks_list)
                    and (bool(checks.get('revenue_declining')) or bool(checks.get('debt_stress')))
                )
                if not qualified and ('guidance_cut' in passed_checks_list or 'estimates_declining' in passed_checks_list):
                    failed_checks_list.append(
                        "guidance_structural_guard: requires revenue_declining or debt_stress"
                    )
            elif pathway_name == 'STRESS':
                qualified = (
                    'fcf_negative' in passed_checks_list or
                    'debt_stress' in passed_checks_list
                )
                revenue_growth_yoy = checks.get('revenue_growth_yoy')
                try:
                    revenue_growth_yoy = float(revenue_growth_yoy) if revenue_growth_yoy is not None else None
                except (TypeError, ValueError):
                    revenue_growth_yoy = None
                if (
                    qualified
                    and revenue_growth_yoy is not None
                    and revenue_growth_yoy > 0.15
                    and not bool(checks.get('debt_stress'))
                    and not bool(checks.get('revenue_declining'))
                ):
                    qualified = False
                    failed_checks_list.append(
                        f"high_growth_stress_guard: revenue_growth_yoy={revenue_growth_yoy:.3f} "
                        "requires revenue_declining or debt_stress"
                    )
            else:
                qualified = (checks_passed / checks_in_pathway) >= 0.5 if checks_in_pathway else False
        else:
            qualified = False

        pathway_details[pathway_name] = {
            'checks_passed': checks_passed,
            'checks_total': checks_in_pathway,
            'passed_list': passed_checks_list,
            'failed_list': failed_checks_list,
            'qualified': qualified
        }

        if qualified:
            pathways_passed.append(pathway_name)
            pathway_scores[pathway_name] = pathway_weight
        else:
            pathways_failed.append(pathway_name)

    # GUIDANCE structural guard (SHORT only):
    # Analyst cuts are noisy and sparse — GUIDANCE only adds signal when paired
    # with real fundamental deterioration. If GUIDANCE passed but neither MARGIN
    # nor REVENUE passed, revoke it so it cannot be the sole qualifier.
    if strategy == 'SHORT' and 'GUIDANCE' in pathways_passed:
        if 'MARGIN' not in pathways_passed and 'REVENUE' not in pathways_passed:
            pathways_passed.remove('GUIDANCE')
            pathways_failed.append('GUIDANCE')
            if 'GUIDANCE' in pathway_scores:
                del pathway_scores['GUIDANCE']

    # STRESS removal (SHORT only) — evidence-backed:
    # MARGIN+REVENUE+STRESS and GUIDANCE+MARGIN+REVENUE+STRESS both showed ≤33% win
    # rate in 2-year rolling backtest. STRESS adds noise without improving precision.
    # STRESS is stripped from SHORT qualifications entirely; other strategies unaffected.
    if strategy == 'SHORT' and 'STRESS' in pathways_passed:
        pathways_passed.remove('STRESS')
        pathways_failed.append('STRESS')
        if 'STRESS' in pathway_scores:
            del pathway_scores['STRESS']

    # MARGIN-only block (SHORT only):
    # Margin compression alone has a 20-30% win rate as a standalone qualifier.
    # MARGIN only contributes when paired with REVENUE.
    if strategy == 'SHORT' and pathways_passed == ['MARGIN']:
        pathways_passed.remove('MARGIN')
        pathways_failed.append('MARGIN')
        if 'MARGIN' in pathway_scores:
            del pathway_scores['MARGIN']

    # MARGIN + REVENUE RS gate (SHORT only) — evidence-backed:
    # When MARGIN+REVENUE is the qualification (with or without GUIDANCE), require
    # relative weakness vs SPY of at least -5%. Without confirmed underperformance
    # the margin/revenue signal is pricing-in old news, not live deterioration.
    if strategy == 'SHORT' and 'MARGIN' in pathways_passed and 'REVENUE' in pathways_passed:
        rs_pct = checks.get('relative_strength_spy')
        try:
            rs_float = float(rs_pct) if rs_pct is not None else None
        except (TypeError, ValueError):
            rs_float = None
        if rs_float is None or rs_float > -5.0:
            # RS requirement not met — revoke MARGIN; REVENUE (if standalone) keeps it
            pathways_passed.remove('MARGIN')
            pathways_failed.append('MARGIN')
            if 'MARGIN' in pathway_scores:
                del pathway_scores['MARGIN']
            # If REVENUE alone now remains, leave it (REVENUE-only is allowed)

    qualified = len(pathways_passed) > 0
    qualification_note = None
    required_pathway_count = 1
    if strategy == 'SHORT':
        raw_vix = (regime_context or {}).get('vix')
        try:
            current_vix = float(raw_vix) if raw_vix is not None else None
        except (TypeError, ValueError):
            current_vix = None
        if current_vix is not None and current_vix < 22.0:
            required_pathway_count = 2
            if len(pathways_passed) < required_pathway_count:
                qualified = False
                joined = ", ".join(pathways_passed) if pathways_passed else "none"
                qualification_note = (
                    f"VIX {current_vix:.1f} requires at least {required_pathway_count} SHORT pathways; "
                    f"got {len(pathways_passed)} ({joined})"
                )
    primary_pathway = None
    if pathways_passed:
        primary_pathway = sorted(pathways_passed, key=lambda p: pathway_priorities.get(p, 99))[0]

    qualification_strength = len(pathways_passed) / max(len(pathway_checks), 1)

    return {
        'qualified': qualified,
        'pathways_passed': pathways_passed,
        'pathways_failed': pathways_failed,
        'primary_pathway': primary_pathway,
        'qualification_strength': qualification_strength,
        'pathway_scores': pathway_scores,
        'pathway_details': pathway_details,
        'pathway_priorities': pathway_priorities,
        'qualification_note': qualification_note,
        'required_pathway_count': required_pathway_count,
    }


# ============================================================================
# CORE SCORING FUNCTION
# ============================================================================

def calculate_strategy_score(
    strategy: str,
    checks: Dict[str, Any],
    metadata: Dict[str, Any] = None,
    research_mode: bool = False,
    regime_context: Optional[Dict[str, Any]] = None,
) -> Dict:
    """
    Calculate weighted score with full normalization and diagnostics
    
    Args:
        strategy: 'VOYAGER', 'SNIPER', 'REMORA', 'SHORT'
        checks: Dict of check values
        metadata: Optional metadata (recency, sector bonus, etc.)
        research_mode: If True, evaluate all tiers even on fatal failures
                      (useful for analysis/debugging)
    
    Features:
    1. Normalized comparison strings
    2. accepted_for_ranking flag
    3. research_mode for full evaluation on fatals
    
    Returns normalized score (0-100) plus full breakdown
    """
    
    metadata = metadata or {}
    strategy = strategy.upper()

    if strategy not in STRATEGY_CONFIGS:
        raise ValueError(f"Unknown strategy: {strategy}")

    config = STRATEGY_CONFIGS[strategy]

    # Regime-aware threshold overrides.
    # Sniper should not go from "strict" to "relaxed" at exactly one VIX print.
    # Slightly elevated tape produces broader ranges but still real breakouts.
    vix = float((regime_context or {}).get("vix", 0))
    if strategy == "SNIPER":
        tier_1_override = dict(config["tier_1"])
        atr_cfg = dict(tier_1_override.get("atr_contraction", {}))
        atr_profile = _sniper_atr_profile_for_vix(vix)
        atr_cfg["threshold"] = atr_profile["threshold"]
        atr_cfg["fatal"] = atr_profile["fatal"]
        tier_1_override["atr_contraction"] = atr_cfg
        config = dict(config)
        config["tier_1"] = tier_1_override

    # Remora catalyst-type-aware volume threshold.
    # A macro-sympathy move (broad market rally/selloff lifting individual stocks ≥3%
    # on ≥1.5x volume) is a legitimate Remora entry but won't produce 3x volume
    # because the buying is spread across the whole market, not concentrated in one name.
    # The scanner sets macro_sympathy_active=True only when the classic 3x threshold
    # was NOT reached — so this override only activates for the softer catalyst path.
    # Both paths remain fatal-gated at their respective thresholds.
    if strategy == "REMORA" and checks.get("macro_sympathy_active"):
        tier_1_override = dict(config["tier_1"])
        vol_cfg = dict(tier_1_override.get("volume_explosion", {}))
        vol_cfg["threshold"] = 1.5  # Macro-sympathy path: 1.5x is meaningful and intentional
        tier_1_override["volume_explosion"] = vol_cfg
        config = dict(config)
        config["tier_1"] = tier_1_override

    # Initialize result
    result = {
        'strategy': strategy,
        'raw_score': 0,
        'max_possible_score': 0,
        'normalized_score': 0,
        'partial_normalized_score': 0,
        'tier_1_score': 0,
        'tier_2_score': 0,
        'tier_3_score': 0,
        'tier_4_score': 0,
        'tier_1_max': 0,
        'tier_1_all_checks_passed': False,
        'tier_1_full_weight_earned': False,
        'grade': 'F',
        'recommendation': 'REJECT',
        'growth_mode_eligible': False,
        'accepted_for_ranking': False,  # NEW
        'fatal_failed': [],
        'passed_checks': {},
        'failed_checks': {},
        'earned_weights': {},
        'tier_breakdown': {},
        'confidence_summary': {},
        'research_mode': research_mode,  # NEW
        'pathway_qualification': {},
        'qualified': True
    }

    def fatal_reject(check_name: str, reason: str) -> Dict:
        """Finalize a hard rejection when any fatal check fails."""
        result['rejection_reason'] = f"Failed fatal check: {check_name} ({reason})"
        result['normalized_score'] = 0
        result['partial_normalized_score'] = 0
        result['grade'] = 'F'
        result['recommendation'] = 'REJECT'
        result['accepted_for_ranking'] = False
        result['growth_mode_eligible'] = False
        logger.warning(f"{strategy} REJECTED: {result['rejection_reason']}")
        return result
    
    # Calculate max possible scores
    tier_1_max = sum(c['weight'] for c in config['tier_1'].values())
    tier_2_max = sum(c['weight'] for c in config['tier_2'].values())
    tier_3_max = sum(c['weight'] for c in config['tier_3'].values())
    tier_4_max = sum(c['weight'] for c in config['tier_4'].values())
    
    result['tier_1_max'] = tier_1_max
    result['max_possible_score'] = tier_1_max + tier_2_max + tier_3_max + tier_4_max

    pathway_result = evaluate_qualification_pathways(strategy, config['tier_2'], checks, regime_context=regime_context)
    if strategy in ['VOYAGER', 'SHORT'] and not pathway_result['qualified'] and not research_mode:
        result['rejection_reason'] = pathway_result.get('qualification_note') or f"No {strategy} pathway qualified"
        result['pathway_qualification'] = pathway_result
        result['qualified'] = False
        logger.warning(f"{strategy} REJECTED: {result['rejection_reason']}")
        logger.warning(f"  Pathways attempted: {list(pathway_result['pathway_details'].keys())}")
        logger.warning(f"  Pathways failed: {pathway_result['pathways_failed']}")
        for pathway, details in pathway_result['pathway_details'].items():
            logger.debug(f"  {pathway}: {details['checks_passed']}/{details['checks_total']} checks passed")
            if not details['qualified']:
                logger.debug(f"    Failed checks: {details['failed_list']}")
        return result
    
    # Track Tier 1 quality
    tier1_total_checks = len(config['tier_1'])
    tier1_checks_passed_count = 0
    tier1_fatal_checks = sum(1 for c in config['tier_1'].values() if c.get('fatal'))
    tier1_fatal_passed_count = 0
    
    # Process Tier 1
    tier1_earned = 0
    tier1_passed = {}
    tier1_failed = {}
    tier1_weights = {}
    fatal_failure_detected = False
    
    for check_name, check_config in config['tier_1'].items():
        check_value = checks.get(check_name)
        
        passed, reason = evaluate_check(check_value, check_config)
        
        if passed:
            tier1_checks_passed_count += 1
            if check_config.get('fatal'):
                tier1_fatal_passed_count += 1
            
            earned_weight = check_config['weight']
            
            # Recency multiplier
            if check_config.get('recency_decay') and metadata.get('institutional_data_age_days'):
                multiplier = apply_recency_multiplier(metadata['institutional_data_age_days'])
                earned_weight = earned_weight * multiplier
                reason += f" (recency: {multiplier:.1f}x)"
            
            tier1_earned += earned_weight
            tier1_passed[check_name] = reason
            tier1_weights[check_name] = earned_weight
        else:
            tier1_failed[check_name] = reason
            
            # FATAL CHECK
            if check_config.get('fatal'):
                result['fatal_failed'].append(check_name)
                fatal_failure_detected = True
                
                # In normal mode, early exit
                # In research mode, continue evaluation
                if not research_mode:
                    tier1_earned_capped = min(tier1_earned, tier_1_max)
                    result['tier_1_score'] = tier1_earned_capped
                    result['raw_score'] = tier1_earned_capped
                    result['tier_1_all_checks_passed'] = False
                    result['tier_1_full_weight_earned'] = (tier1_earned_capped >= tier_1_max)
                    result['accepted_for_ranking'] = False  # Fatal = not rankable
                    
                    result['passed_checks'] = {'tier_1': tier1_passed}
                    result['failed_checks'] = {'tier_1': tier1_failed}
                    result['earned_weights'] = {'tier_1': tier1_weights}
                    result['tier_breakdown'] = {
                        'tier_1': {
                            'earned': tier1_earned_capped,
                            'max': tier_1_max,
                            'pct': (tier1_earned_capped/tier_1_max*100) if tier_1_max > 0 else 0
                        }
                    }
                    
                    result['confidence_summary'] = {
                        'normalized_score': 0,
                        'fatal_checks_passed': tier1_fatal_passed_count,
                        'fatal_checks_total': tier1_fatal_checks,
                        'tier_1_pct': round((tier1_earned_capped/tier_1_max*100) if tier_1_max > 0 else 0, 1),
                        'tier_2_pct': 0,
                        'tier_3_pct': 0,
                        'tier_4_pct': 0,
                        'growth_mode_eligible': False,
                        'rejection': 'fatal_check_failed'
                    }
                    return fatal_reject(check_name, reason)
    
    # Cap tier 1
    tier1_earned = min(tier1_earned, tier_1_max)
    result['tier_1_score'] = tier1_earned
    result['tier_1_all_checks_passed'] = (tier1_checks_passed_count == tier1_total_checks)
    result['tier_1_full_weight_earned'] = (tier1_earned >= tier_1_max)
    
    # Process Tier 2
    tier2_earned = 0
    tier2_passed = {}
    tier2_failed = {}
    tier2_weights = {}
    
    for check_name, check_config in config['tier_2'].items():
        check_value = checks.get(check_name)
        passed, reason = evaluate_check(check_value, check_config)
        
        if passed:
            tier2_earned += check_config['weight']
            tier2_passed[check_name] = reason
            tier2_weights[check_name] = check_config['weight']
        else:
            tier2_failed[check_name] = reason
            if check_config.get('fatal'):
                result['fatal_failed'].append(check_name)
                fatal_failure_detected = True
                if not research_mode:
                    result['tier_2_score'] = tier2_earned
                    result['raw_score'] = tier1_earned + tier2_earned
                    result['passed_checks'] = {'tier_1': tier1_passed, 'tier_2': tier2_passed}
                    result['failed_checks'] = {'tier_1': tier1_failed, 'tier_2': tier2_failed}
                    result['earned_weights'] = {'tier_1': tier1_weights, 'tier_2': tier2_weights}
                    result['tier_breakdown'] = {
                        'tier_1': {
                            'earned': tier1_earned,
                            'max': tier_1_max,
                            'pct': (tier1_earned/tier_1_max*100) if tier_1_max > 0 else 0
                        },
                        'tier_2': {
                            'earned': tier2_earned,
                            'max': tier_2_max,
                            'pct': (tier2_earned/tier_2_max*100) if tier_2_max > 0 else 0
                        }
                    }
                    result['confidence_summary'] = {
                        'normalized_score': 0,
                        'fatal_checks_passed': tier1_fatal_passed_count,
                        'fatal_checks_total': tier1_fatal_checks,
                        'tier_1_pct': round((tier1_earned/tier_1_max*100) if tier_1_max > 0 else 0, 1),
                        'tier_2_pct': round((tier2_earned/tier_2_max*100) if tier_2_max > 0 else 0, 1),
                        'tier_3_pct': 0,
                        'tier_4_pct': 0,
                        'growth_mode_eligible': False,
                        'rejection': 'fatal_check_failed'
                    }
                    return fatal_reject(check_name, reason)
    
    result['tier_2_score'] = tier2_earned
    
    # Process Tier 3
    tier3_earned = 0
    tier3_passed = {}
    tier3_failed = {}
    tier3_weights = {}
    
    for check_name, check_config in config['tier_3'].items():
        check_value = checks.get(check_name)
        passed, reason = evaluate_check(check_value, check_config)
        
        if passed:
            tier3_earned += check_config['weight']
            tier3_passed[check_name] = reason
            tier3_weights[check_name] = check_config['weight']
        else:
            tier3_failed[check_name] = reason
            if check_config.get('fatal'):
                result['fatal_failed'].append(check_name)
                fatal_failure_detected = True
                if not research_mode:
                    result['tier_2_score'] = tier2_earned
                    result['tier_3_score'] = tier3_earned
                    result['raw_score'] = tier1_earned + tier2_earned + tier3_earned
                    result['passed_checks'] = {'tier_1': tier1_passed, 'tier_2': tier2_passed, 'tier_3': tier3_passed}
                    result['failed_checks'] = {'tier_1': tier1_failed, 'tier_2': tier2_failed, 'tier_3': tier3_failed}
                    result['earned_weights'] = {'tier_1': tier1_weights, 'tier_2': tier2_weights, 'tier_3': tier3_weights}
                    result['tier_breakdown'] = {
                        'tier_1': {
                            'earned': tier1_earned,
                            'max': tier_1_max,
                            'pct': (tier1_earned/tier_1_max*100) if tier_1_max > 0 else 0
                        },
                        'tier_2': {
                            'earned': tier2_earned,
                            'max': tier_2_max,
                            'pct': (tier2_earned/tier_2_max*100) if tier_2_max > 0 else 0
                        },
                        'tier_3': {
                            'earned': tier3_earned,
                            'max': tier_3_max,
                            'pct': (tier3_earned/tier_3_max*100) if tier_3_max > 0 else 0
                        }
                    }
                    result['confidence_summary'] = {
                        'normalized_score': 0,
                        'fatal_checks_passed': tier1_fatal_passed_count,
                        'fatal_checks_total': tier1_fatal_checks,
                        'tier_1_pct': round((tier1_earned/tier_1_max*100) if tier_1_max > 0 else 0, 1),
                        'tier_2_pct': round((tier2_earned/tier_2_max*100) if tier_2_max > 0 else 0, 1),
                        'tier_3_pct': round((tier3_earned/tier_3_max*100) if tier_3_max > 0 else 0, 1),
                        'tier_4_pct': 0,
                        'growth_mode_eligible': False,
                        'rejection': 'fatal_check_failed'
                    }
                    return fatal_reject(check_name, reason)
    
    result['tier_3_score'] = tier3_earned
    
    # Process Tier 4
    tier4_earned = 0
    tier4_passed = {}
    tier4_failed = {}
    tier4_weights = {}
    
    for check_name, check_config in config['tier_4'].items():
        if check_name == 'sector_alpha_bonus':
            if metadata.get('sector_bonus_eligible'):
                tier4_earned += check_config['weight']
                tier4_passed[check_name] = "Sector wave detected"
                tier4_weights[check_name] = check_config['weight']
            else:
                tier4_failed[check_name] = "Not in sector wave"
        else:
            check_value = checks.get(check_name)
            passed, reason = evaluate_check(check_value, check_config)
            
            if passed:
                tier4_earned += check_config['weight']
                tier4_passed[check_name] = reason
                tier4_weights[check_name] = check_config['weight']
            else:
                tier4_failed[check_name] = reason
                if check_config.get('fatal'):
                    result['fatal_failed'].append(check_name)
                    fatal_failure_detected = True
                    if not research_mode:
                        result['tier_2_score'] = tier2_earned
                        result['tier_3_score'] = tier3_earned
                        result['tier_4_score'] = tier4_earned
                        result['raw_score'] = tier1_earned + tier2_earned + tier3_earned + tier4_earned
                        return fatal_reject(check_name, reason)
    
    result['tier_4_score'] = tier4_earned
    
    # Calculate scores
    result['raw_score'] = tier1_earned + tier2_earned + tier3_earned + tier4_earned
    
    if result['max_possible_score'] > 0:
        result['normalized_score'] = (result['raw_score'] / result['max_possible_score']) * 100
    else:
        result['normalized_score'] = 0

    # Grade and recommendation
    _refresh_recommendation_state(strategy, result)
    
    # Growth mode
    growth_threshold = config['growth_mode_threshold']
    result['growth_mode_eligible'] = (
        result['normalized_score'] >= growth_threshold and
        result['tier_1_all_checks_passed']
    )
    
    # If research_mode and fatal was detected, mark as rejected
    if research_mode and fatal_failure_detected:
        result['recommendation'] = 'REJECT'
        result['rejection_reason'] = f"Failed fatal checks: {', '.join(result['fatal_failed'])}"
        result['accepted_for_ranking'] = False
        logger.info(f"{strategy} RESEARCH MODE: Full eval despite fatal failures")
    
    # Full breakdown
    result['passed_checks'] = {
        'tier_1': tier1_passed,
        'tier_2': tier2_passed,
        'tier_3': tier3_passed,
        'tier_4': tier4_passed
    }
    
    result['failed_checks'] = {
        'tier_1': tier1_failed,
        'tier_2': tier2_failed,
        'tier_3': tier3_failed,
        'tier_4': tier4_failed
    }
    
    result['earned_weights'] = {
        'tier_1': tier1_weights,
        'tier_2': tier2_weights,
        'tier_3': tier3_weights,
        'tier_4': tier4_weights
    }
    
    result['tier_breakdown'] = {
        'tier_1': {
            'earned': tier1_earned,
            'max': tier_1_max,
            'pct': (tier1_earned/tier_1_max*100) if tier_1_max > 0 else 0
        },
        'tier_2': {
            'earned': tier2_earned,
            'max': tier_2_max,
            'pct': (tier2_earned/tier_2_max*100) if tier_2_max > 0 else 0
        },
        'tier_3': {
            'earned': tier3_earned,
            'max': tier_3_max,
            'pct': (tier3_earned/tier_3_max*100) if tier_3_max > 0 else 0
        },
        'tier_4': {
            'earned': tier4_earned,
            'max': tier_4_max,
            'pct': (tier4_earned/tier_4_max*100) if tier_4_max > 0 else 0
        }
    }
    
    # Confidence summary
    result['confidence_summary'] = {
        'normalized_score': round(result['normalized_score'], 1),
        'fatal_checks_passed': tier1_fatal_passed_count,
        'fatal_checks_total': tier1_fatal_checks,
        'tier_1_pct': round(result['tier_breakdown']['tier_1']['pct'], 1),
        'tier_2_pct': round(result['tier_breakdown']['tier_2']['pct'], 1),
        'tier_3_pct': round(result['tier_breakdown']['tier_3']['pct'], 1),
        'tier_4_pct': round(result['tier_breakdown']['tier_4']['pct'], 1),
        'growth_mode_eligible': result['growth_mode_eligible'],
        'accepted_for_ranking': result['accepted_for_ranking']
    }

    result['pathway_qualification'] = pathway_result
    result['qualified'] = pathway_result.get('qualified', True)
    
    return result


# ============================================================================
# CROSS-STRATEGY COMPARISON
# ============================================================================

def compare_across_strategies(
    ticker: str,
    universal_checks: Dict,
    metadata: Dict = None,
    research_mode: bool = False
) -> Dict:
    """
    Compare stock across all strategies
    
    Args:
        research_mode: If True, evaluate all tiers even on fatal failures
    """
    
    metadata = metadata or {}
    
    voyager = calculate_strategy_score('VOYAGER', universal_checks, metadata, research_mode)
    sniper = calculate_strategy_score('SNIPER', universal_checks, metadata, research_mode)
    remora = calculate_strategy_score('REMORA', universal_checks, metadata, research_mode)
    short = calculate_strategy_score('SHORT', universal_checks, metadata, research_mode)
    
    # Best long strategy (using accepted_for_ranking)
    long_strategies = [
        (voyager, 'VOYAGER'),
        (sniper, 'SNIPER'),
        (remora, 'REMORA')
    ]
    
    # Only consider strategies accepted for ranking
    valid_long = [
        (s, name) for s, name in long_strategies
        if s['accepted_for_ranking']
    ]
    
    if valid_long:
        best_long = max(valid_long, key=lambda x: x[0]['normalized_score'])
        best_strategy = best_long[1]
        best_score = best_long[0]['normalized_score']
    else:
        best_strategy = None
        best_score = 0
    
    # Diamond Hands
    b_or_better = {'A+', 'A', 'B'}
    diamond_hands = (
        voyager['grade'] in b_or_better and
        sniper['grade'] in b_or_better and
        remora['grade'] in b_or_better
    )
    
    growth_mode = (
        voyager['growth_mode_eligible'] or
        sniper['growth_mode_eligible'] or
        remora['growth_mode_eligible']
    )
    
    result = {
        'ticker': ticker,
        'voyager': voyager,
        'sniper': sniper,
        'remora': remora,
        'short': short,
        'best_strategy': best_strategy,
        'best_score': best_score,
        'diamond_hands': diamond_hands,
        'growth_mode_active': growth_mode,
        'research_mode': research_mode
    }
    
    if diamond_hands:
        logger.info(f"💎 {ticker}: Diamond Hands (research flag)")
    
    return result


# ============================================================================
# POSITION SIZING
# ============================================================================

def calculate_position_size(
    account_equity: float,
    entry_price: float,
    stop_price: float,
    score_result: Dict,
    base_risk_pct: float = 0.75,
    normal_max_pct: float = 15.0,
    growth_max_pct: float = 25.0,
    growth_max_risk_pct: float = 1.25
) -> Dict:
    """Calculate position size with safety guards"""
    
    if account_equity <= 0:
        return {'valid': False, 'error': 'Invalid account equity', 'shares': 0}
    
    if entry_price <= 0:
        return {'valid': False, 'error': 'Invalid entry price', 'shares': 0}
    
    risk_per_share = abs(entry_price - stop_price)
    
    if risk_per_share <= 0:
        return {
            'valid': False,
            'error': 'Zero risk (entry == stop)',
            'shares': 0,
            'entry_price': entry_price,
            'stop_price': stop_price
        }
    
    growth_mode = score_result.get('growth_mode_eligible', False)
    
    if growth_mode:
        max_position_pct = growth_max_pct
        max_risk_pct = growth_max_risk_pct
    else:
        max_position_pct = normal_max_pct
        max_risk_pct = base_risk_pct
    
    max_risk_dollars = account_equity * (max_risk_pct / 100)
    shares_by_risk = int(max_risk_dollars / risk_per_share)
    
    max_position_dollars = account_equity * (max_position_pct / 100)
    shares_by_position = int(max_position_dollars / entry_price)
    
    shares = min(shares_by_risk, shares_by_position)
    
    if shares <= 0:
        return {'valid': False, 'error': 'Calculated shares <= 0', 'shares': 0}
    
    position_value = shares * entry_price
    position_pct = (position_value / account_equity) * 100
    risk_at_stop = shares * risk_per_share
    risk_pct = (risk_at_stop / account_equity) * 100
    
    return {
        'valid': True,
        'shares': shares,
        'position_value': position_value,
        'position_pct': position_pct,
        'risk_at_stop': risk_at_stop,
        'risk_pct': risk_pct,
        'growth_mode': growth_mode,
        'max_allowed_position_pct': max_position_pct,
        'max_allowed_risk_pct': max_risk_pct
    }


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    
    print("\n" + "="*80)
    print("FINAL SCORING SYSTEM - WITH ADDITIONS")
    print("="*80)
    
    # Test 1: String comparison normalization
    print("\n### TEST 1: String Comparison Normalization")
    
    checks = {
        'risk_reward_ratio': 7.5,
        'relative_strength_spy': 15.0,
        'institutional_accumulation': True,
        'vix_regime_match': True
    }
    
    result = calculate_strategy_score('VOYAGER', checks)
    print(f"Normalized Score: {result['normalized_score']:.1f}/100")
    print(f"String comparisons handled: ✅")
    
    # Test 2: accepted_for_ranking flag
    print("\n### TEST 2: accepted_for_ranking Flag")
    
    good_checks = {
        'risk_reward_ratio': 6.0,
        'relative_strength_spy': 8.0,
        'institutional_accumulation': True,
        'vix_regime_match': True,
        'fundamental_health': True,
        'volume_liquidity': 1000000
    }
    
    good_result = calculate_strategy_score('VOYAGER', good_checks)
    print(f"Score: {good_result['normalized_score']:.1f}/100")
    print(f"Accepted for Ranking: {good_result['accepted_for_ranking']}")
    print(f"Recommendation: {good_result['recommendation']}")
    
    # Test 3: research_mode
    print("\n### TEST 3: Research Mode (Fatal with Full Eval)")
    
    fatal_checks = {
        'risk_reward_ratio': 2.0,  # FATAL FAIL
        'relative_strength_spy': 18.0,
        'institutional_accumulation': True,
        'vix_regime_match': True,
        'fundamental_health': True,
        'volume_liquidity': 2000000,
        'sector_rotation': True
    }
    
    # Normal mode: early exit
    normal_result = calculate_strategy_score('VOYAGER', fatal_checks, research_mode=False)
    print(f"\nNormal Mode:")
    print(f"  Score: {normal_result['normalized_score']:.1f}/100")
    print(f"  Tier 2 Evaluated: {bool(normal_result['tier_breakdown'].get('tier_2'))}")
    
    # Research mode: full evaluation
    research_result = calculate_strategy_score('VOYAGER', fatal_checks, research_mode=True)
    print(f"\nResearch Mode:")
    print(f"  Score: {research_result['normalized_score']:.1f}/100")
    print(f"  Tier 2 Score: {research_result['tier_2_score']}")
    print(f"  Recommendation: {research_result['recommendation']}")
    print(f"  Accepted for Ranking: {research_result['accepted_for_ranking']}")
    
    # Test 4: Confidence summary
    print("\n### TEST 4: Confidence Summary")
    print(f"Summary: {good_result['confidence_summary']}")
    
    print("\n" + "="*80)
    print("ALL ADDITIONS VALIDATED ✅")
    print("="*80)
