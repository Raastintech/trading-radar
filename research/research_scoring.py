#!/usr/bin/env python3
"""
research/research_scoring.py — Shared scoring utilities for the research engine.

Provides pure, testable scoring functions consumed by research_scanner.py,
stock_research_card.py, ten_x_candidate_radar.py, and daily_alpha_radar_report.py:

  earliness_label()             — 7-bucket lifecycle position of a ticker
  earliness_detail()            — richer version: label + missing_fields + extension_state
  consensus_label()             — signal confirmation breadth across scanner categories
  quality_adjusted_consensus()  — consensus score adjusted for data quality
  priority_label()              — final priority classification with downgrade reasons

Research-only. No trade recommendations. No provider calls.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# ── Earliness labels ──────────────────────────────────────────────────────────
EARLY = "EARLY"
DEVELOPING = "DEVELOPING"
RECLAIM_WATCH = "RECLAIM_WATCH"
RESET_WATCH = "RESET_WATCH"
EXTENDED = "EXTENDED"
LATE = "LATE"
INVALIDATED = "INVALIDATED"
UNKNOWN_EARLINESS = "UNKNOWN"

EARLINESS_LABELS = (EARLY, DEVELOPING, RECLAIM_WATCH, RESET_WATCH, EXTENDED, LATE, INVALIDATED, UNKNOWN_EARLINESS)

# ── Extension state labels ────────────────────────────────────────────────────
EXT_NORMAL = "NORMAL"
EXT_STRETCHED = "STRETCHED"
EXT_EXTENDED = "EXTENDED"
EXT_PARABOLIC = "PARABOLIC"

EXTENSION_STATES = (EXT_NORMAL, EXT_STRETCHED, EXT_EXTENDED, EXT_PARABOLIC)

# ── Consensus labels ──────────────────────────────────────────────────────────
SINGLE_SIGNAL = "SINGLE_SIGNAL"
DOUBLE_CONFIRMATION = "DOUBLE_CONFIRMATION"
MULTI_CONFIRMATION = "MULTI_CONFIRMATION"
HIGH_PRIORITY_RESEARCH = "HIGH_PRIORITY_RESEARCH"

CONSENSUS_LABELS = (SINGLE_SIGNAL, DOUBLE_CONFIRMATION, MULTI_CONFIRMATION, HIGH_PRIORITY_RESEARCH)

# ── Priority labels ───────────────────────────────────────────────────────────
TOP_RESEARCH = "TOP_RESEARCH"
# HIGH_PRIORITY_RESEARCH is shared between consensus and priority label sets (same string).
WATCHLIST_RESEARCH = "WATCHLIST_RESEARCH"
# RESET_WATCH and RECLAIM_WATCH are shared between earliness and priority label sets.
CONFLICTED_SIGNAL = "CONFLICTED_SIGNAL"
EXTENDED_CROWDED = "EXTENDED_CROWDED"
DATA_QUARANTINE = "DATA_QUARANTINE"
INVALID_PRIORITY = "INVALID"

PRIORITY_LABELS = (
    TOP_RESEARCH,
    HIGH_PRIORITY_RESEARCH,
    WATCHLIST_RESEARCH,
    RESET_WATCH,
    RECLAIM_WATCH,
    CONFLICTED_SIGNAL,
    EXTENDED_CROWDED,
    DATA_QUARANTINE,
    INVALID_PRIORITY,
)

# ── Required fields for a complete earliness computation ─────────────────────
EARLINESS_REQUIRED_FIELDS = (
    "above_ma50",
    "above_ma200",
)
EARLINESS_ENRICHING_FIELDS = (
    "latest_close",
    "ma20",
    "ret_20d",
    "high_60d_dist",
    "vol_trend_ratio",
    "rs_20d",
    "rs_63d",
    "dd_from_high_pct",
    "extension_vs_ma200_pct",
)


def earliness_label(
    *,
    rs_63: Optional[float] = None,
    rs_20: Optional[float] = None,
    above_ma50: Optional[bool] = None,
    above_ma200: Optional[bool] = None,
    dd_from_high_pct: Optional[float] = None,
    vol_trend_ratio: Optional[float] = None,
    extension_vs_ma200_pct: Optional[float] = None,
) -> str:
    """
    Classify where a ticker sits in its trend lifecycle.

    Requires at minimum above_ma50 and above_ma200; returns UNKNOWN when
    insufficient data.

    Labels (research classification only — not trade signals):
      EARLY          — above MA50, below MA200; RS positive; volume rising.
      DEVELOPING     — above both MAs; RS non-negative; extension < 15%.
      RECLAIM_WATCH  — below MA50/MA200 but within ~20% of high and not deeply negative.
      RESET_WATCH    — above MA200, pulled back below MA50.
      EXTENDED       — >15% above MA200. Stretched; wait for a reset.
      LATE           — >20% above MA200 AND rs_63 > 20. Parabolic move.
      INVALIDATED    — below MA200 with rs_63 < -10. Active downtrend.
      UNKNOWN        — insufficient price data to classify.
    """
    if above_ma50 is None or above_ma200 is None:
        return UNKNOWN_EARLINESS

    ext = extension_vs_ma200_pct

    if ext is not None and ext > 20 and rs_63 is not None and rs_63 > 20:
        return LATE

    if ext is not None and ext > 15:
        return EXTENDED

    if not above_ma200:
        if above_ma50:
            rs_improving = rs_63 is not None and rs_63 > 0
            vol_rising = vol_trend_ratio is not None and vol_trend_ratio > 1.05
            if rs_improving or vol_rising:
                return EARLY
            return RECLAIM_WATCH
        if rs_63 is not None and rs_63 < -10:
            return INVALIDATED
        return RECLAIM_WATCH

    if not above_ma50:
        return RESET_WATCH

    return DEVELOPING


def earliness_detail(
    *,
    rs_63: Optional[float] = None,
    rs_20: Optional[float] = None,
    above_ma50: Optional[bool] = None,
    above_ma200: Optional[bool] = None,
    dd_from_high_pct: Optional[float] = None,
    vol_trend_ratio: Optional[float] = None,
    extension_vs_ma200_pct: Optional[float] = None,
    extension_vs_ma20_pct: Optional[float] = None,
    ret_5d: Optional[float] = None,
    ret_20d: Optional[float] = None,
    latest_close: Optional[float] = None,
    ma20: Optional[float] = None,
) -> Dict:
    """
    Richer version of earliness_label. Returns dict with:
      label               — the earliness label
      missing_fields      — list of fields that would improve the classification
      extension_state     — NORMAL / STRETCHED / EXTENDED / PARABOLIC
      earliness_score     — 0-100 numeric score (higher = earlier in trend)
    """
    label = earliness_label(
        rs_63=rs_63,
        rs_20=rs_20,
        above_ma50=above_ma50,
        above_ma200=above_ma200,
        dd_from_high_pct=dd_from_high_pct,
        vol_trend_ratio=vol_trend_ratio,
        extension_vs_ma200_pct=extension_vs_ma200_pct,
    )

    missing: List[str] = []
    if above_ma50 is None:
        missing.append("above_ma50")
    if above_ma200 is None:
        missing.append("above_ma200")
    if rs_63 is None:
        missing.append("rs_63d")
    if rs_20 is None:
        missing.append("rs_20d")
    if vol_trend_ratio is None:
        missing.append("vol_trend_ratio")
    if dd_from_high_pct is None:
        missing.append("dd_from_high_pct")
    if extension_vs_ma200_pct is None:
        missing.append("extension_vs_ma200_pct")
    if latest_close is None or ma20 is None:
        missing.append("ma20_extension")

    # Extension state (use MA200 if available, fall back to MA20)
    ext = extension_vs_ma200_pct if extension_vs_ma200_pct is not None else extension_vs_ma20_pct
    if ext is None and latest_close is not None and ma20 is not None and ma20 > 0:
        ext = (latest_close / ma20 - 1.0) * 100.0

    if ext is None:
        extension_state = EXT_NORMAL  # assume normal when unknown
    elif ext > 20:
        extension_state = EXT_PARABOLIC
    elif ext > 15:
        extension_state = EXT_EXTENDED
    elif ext > 8:
        extension_state = EXT_STRETCHED
    else:
        extension_state = EXT_NORMAL

    # Earliness score: 0-100 (higher = earlier / more favorable lifecycle position)
    score = 50.0
    if label == EARLY:
        score = 80.0
        if rs_63 is not None and rs_63 > 5:
            score += 10
        if vol_trend_ratio is not None and vol_trend_ratio > 1.1:
            score += 5
    elif label == DEVELOPING:
        score = 65.0
        if rs_63 is not None and rs_63 > 10:
            score += 10
    elif label == RECLAIM_WATCH:
        score = 45.0
    elif label == RESET_WATCH:
        score = 60.0
        if rs_63 is not None and rs_63 > 0:
            score += 5
    elif label == EXTENDED:
        score = 30.0
    elif label == LATE:
        score = 15.0
    elif label == INVALIDATED:
        score = 5.0
    elif label == UNKNOWN_EARLINESS:
        score = 0.0

    return {
        "label": label,
        "missing_fields": missing,
        "extension_state": extension_state,
        "earliness_score": round(min(100.0, max(0.0, score)), 1),
    }


def consensus_label(
    categories: List[str],
    research_score: Optional[float] = None,
) -> str:
    """
    Classify how many independent scanner categories surfaced a ticker.

    Returns one of:
      SINGLE_SIGNAL         — only one scanner category surfaced this ticker
      DOUBLE_CONFIRMATION   — two independent categories agree
      MULTI_CONFIRMATION    — three or more categories agree
      HIGH_PRIORITY_RESEARCH — three+ categories AND research_score ≥ 70
    """
    n = len(set(categories))
    if n <= 1:
        return SINGLE_SIGNAL
    if n == 2:
        return DOUBLE_CONFIRMATION
    if research_score is not None and research_score >= 70:
        return HIGH_PRIORITY_RESEARCH
    return MULTI_CONFIRMATION


def quality_adjusted_consensus(
    *,
    categories: List[str],
    research_score: Optional[float] = None,
    data_confidence: Optional[str] = None,
    earliness: Optional[str] = None,
    extension_state: Optional[str] = None,
    conflict_flags: Optional[List[str]] = None,
    social_only: bool = False,
    has_stale_data: bool = False,
    liquidity_ok: Optional[bool] = None,
) -> Dict:
    """
    Compute quality-adjusted consensus score.

    Unlike raw consensus_label (which counts categories), this function applies
    data quality and signal reliability penalties before assigning a label.

    Returns dict with:
      raw_consensus_score           — raw category count
      quality_adjusted_score        — 0-100 float
      consensus_label               — final consensus label
      downgrade_reasons             — list of reasons the score was reduced
    """
    reasons: List[str] = []

    # Raw consensus score: base on unique categories
    unique_cats = list(set(categories))
    n_cats = len(unique_cats)
    raw_score = min(100.0, 40.0 + n_cats * 20.0)
    if research_score is not None:
        raw_score = max(raw_score, float(research_score))
    raw_score = min(100.0, raw_score)

    # Apply penalties
    adj_score = raw_score

    if data_confidence == "INVALID":
        adj_score -= 50
        reasons.append("confidence_INVALID")
    elif data_confidence == "LOW":
        adj_score -= 20
        reasons.append("confidence_LOW")
    elif data_confidence is None:
        adj_score -= 10
        reasons.append("confidence_unknown")

    if earliness == UNKNOWN_EARLINESS:
        adj_score -= 30
        reasons.append("earliness_UNKNOWN")
    elif earliness in (LATE, INVALIDATED):
        adj_score -= 25
        reasons.append(f"earliness_{earliness}")
    elif earliness == EXTENDED:
        adj_score -= 15
        reasons.append("earliness_EXTENDED")

    if extension_state in (EXT_EXTENDED, EXT_PARABOLIC):
        adj_score -= 15
        reasons.append(f"extension_{extension_state}")

    if conflict_flags:
        adj_score -= 20
        reasons.extend([f"conflict:{f}" for f in conflict_flags[:2]])

    if social_only:
        adj_score -= 15
        reasons.append("social_only_confirmation")

    if has_stale_data:
        adj_score -= 10
        reasons.append("stale_data")

    if liquidity_ok is False:
        adj_score -= 10
        reasons.append("liquidity_unknown")

    adj_score = max(0.0, min(100.0, adj_score))

    # Final consensus label
    if adj_score >= 70 and n_cats >= 3:
        final_label = HIGH_PRIORITY_RESEARCH
    elif adj_score >= 55 and n_cats >= 2:
        final_label = DOUBLE_CONFIRMATION
    elif adj_score >= 40 and n_cats >= 3:
        final_label = MULTI_CONFIRMATION
    elif n_cats >= 2 and not reasons:
        final_label = DOUBLE_CONFIRMATION
    else:
        final_label = SINGLE_SIGNAL

    return {
        "raw_consensus_score": round(raw_score, 1),
        "quality_adjusted_score": round(adj_score, 1),
        "consensus_label": final_label,
        "downgrade_reasons": reasons,
    }


def priority_label(
    *,
    data_confidence: Optional[str] = None,
    ticker_valid: Optional[bool] = None,
    liquidity_ok: Optional[bool] = None,
    earliness: Optional[str] = None,
    consensus: Optional[str] = None,
    extension_vs_ma200_pct: Optional[float] = None,
    extension_vs_ma20_pct: Optional[float] = None,
    conflict_flags: Optional[List[str]] = None,
    missing_fields: Optional[List[str]] = None,
    adj_consensus_score: Optional[float] = None,
) -> Tuple[str, List[str]]:
    """
    Compute the final priority label for a research candidate.

    Applies strict quality gates before assigning HIGH_PRIORITY_RESEARCH or
    TOP_RESEARCH. Names that fail data quality, have UNKNOWN earliness, are
    extended, or have conflicting signals are downgraded to the appropriate
    lower-priority bucket.

    Returns:
        (priority_label_str, downgrade_reasons_list)
    """
    reasons: List[str] = []

    # Hard gate 1: invalid ticker / invalid coverage
    if ticker_valid is False:
        return INVALID_PRIORITY, ["ticker_validity_unknown"]
    if data_confidence == "INVALID":
        return INVALID_PRIORITY, ["data_confidence_INVALID"]

    # Hard gate 2: DATA_QUARANTINE — serious data gaps
    quarantine_reasons: List[str] = []
    if earliness == UNKNOWN_EARLINESS:
        quarantine_reasons.append("earliness_UNKNOWN")
    if earliness == INVALIDATED:
        quarantine_reasons.append("ticker_INVALIDATED")
    if missing_fields:
        quarantine_reasons.append(f"missing_fields:{','.join(sorted(missing_fields)[:3])}")
    if data_confidence is None:
        quarantine_reasons.append("confidence_unknown")
    if quarantine_reasons:
        return DATA_QUARANTINE, quarantine_reasons

    # CONFLICTED_SIGNAL
    if conflict_flags:
        return CONFLICTED_SIGNAL, list(conflict_flags[:3])

    # Extension checks (before high-priority path)
    ext = extension_vs_ma200_pct if extension_vs_ma200_pct is not None else extension_vs_ma20_pct
    high_extension = ext is not None and ext > 15
    very_high_extension = ext is not None and ext > 20

    if earliness in (EXTENDED, LATE) or very_high_extension:
        # High consensus + extended → RESET_WATCH (watch for pullback entry)
        if consensus in (DOUBLE_CONFIRMATION, MULTI_CONFIRMATION, HIGH_PRIORITY_RESEARCH):
            return RESET_WATCH, ["extension_high_consensus"]
        return EXTENDED_CROWDED, ["too_extended"]

    if high_extension:
        return RESET_WATCH, [f"extension_{ext:.0f}pct_above_ma200"]

    # Earliness-based routing for non-extended names
    if earliness == RECLAIM_WATCH:
        return RECLAIM_WATCH, []
    if earliness == RESET_WATCH:
        return RESET_WATCH, []

    # Quality gates for HIGH_PRIORITY and TOP_RESEARCH
    quality_ok = (
        data_confidence in ("HIGH", "MEDIUM")
        and ticker_valid is not False
        and liquidity_ok is not False
        and earliness not in (UNKNOWN_EARLINESS, LATE, INVALIDATED)
        and consensus in (DOUBLE_CONFIRMATION, MULTI_CONFIRMATION, HIGH_PRIORITY_RESEARCH)
        and not conflict_flags
        and not missing_fields
        and not high_extension
    )

    if not quality_ok:
        if data_confidence in ("LOW", None):
            reasons.append(f"confidence_{data_confidence or 'unknown'}")
        if liquidity_ok is False:
            reasons.append("liquidity_unknown")
        if earliness in (UNKNOWN_EARLINESS, LATE, INVALIDATED):
            reasons.append(f"earliness_{earliness or 'unknown'}")
        if consensus == SINGLE_SIGNAL:
            reasons.append("only_single_signal")
        elif consensus is None:
            reasons.append("consensus_unknown")
        if missing_fields:
            reasons.append(f"missing_fields:{','.join(sorted(missing_fields)[:2])}")
        return WATCHLIST_RESEARCH, reasons

    # Both quality gates passed — assign highest labels
    is_top = (
        data_confidence == "HIGH"
        and consensus == HIGH_PRIORITY_RESEARCH
        and (adj_consensus_score is None or adj_consensus_score >= 70)
    )
    if is_top:
        return TOP_RESEARCH, []

    return HIGH_PRIORITY_RESEARCH, []
