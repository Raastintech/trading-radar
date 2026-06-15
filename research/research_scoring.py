#!/usr/bin/env python3
"""
research/research_scoring.py — Shared scoring utilities for the research engine.

Provides two pure, testable scoring functions consumed by research_scanner.py,
stock_research_card.py, and ten_x_candidate_radar.py:

  earliness_label() — 7-bucket lifecycle position of a ticker in its trend
  consensus_label() — signal confirmation breadth across scanner categories

Research-only. No trade recommendations. No provider calls.
"""
from __future__ import annotations

from typing import List, Optional

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

# ── Consensus labels ──────────────────────────────────────────────────────────
SINGLE_SIGNAL = "SINGLE_SIGNAL"
DOUBLE_CONFIRMATION = "DOUBLE_CONFIRMATION"
MULTI_CONFIRMATION = "MULTI_CONFIRMATION"
HIGH_PRIORITY_RESEARCH = "HIGH_PRIORITY_RESEARCH"

CONSENSUS_LABELS = (SINGLE_SIGNAL, DOUBLE_CONFIRMATION, MULTI_CONFIRMATION, HIGH_PRIORITY_RESEARCH)


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
                       Base breakout in progress, not yet fully confirmed.
      DEVELOPING     — above both MAs; RS non-negative; extension < 15%.
                       Established uptrend with room to grow.
      RECLAIM_WATCH  — below MA50/MA200 but within ~20% of high and not deeply negative.
                       Watch for reclaim catalyst.
      RESET_WATCH    — above MA200, pulled back below MA50.
                       Healthy consolidation within an uptrend.
      EXTENDED       — >15% above MA200. Stretched; wait for a reset.
      LATE           — >20% above MA200 AND rs_63 > 20. Parabolic move.
      INVALIDATED    — below MA200 with rs_63 < -10. Active downtrend.
      UNKNOWN        — insufficient price data to classify.
    """
    if above_ma50 is None or above_ma200 is None:
        return UNKNOWN_EARLINESS

    ext = extension_vs_ma200_pct

    # Parabolic / late-stage first — overrides other positional checks
    if ext is not None and ext > 20 and rs_63 is not None and rs_63 > 20:
        return LATE

    # Extended (stretched but not necessarily parabolic)
    if ext is not None and ext > 15:
        return EXTENDED

    if not above_ma200:
        if above_ma50:
            # Above short-term MA but below long-term MA — emerging from base
            rs_improving = rs_63 is not None and rs_63 > 0
            vol_rising = vol_trend_ratio is not None and vol_trend_ratio > 1.05
            if rs_improving or vol_rising:
                return EARLY
            # Above MA50, below MA200, no confirming signals
            return RECLAIM_WATCH
        # Below MA200 — check invalidation
        if rs_63 is not None and rs_63 < -10:
            return INVALIDATED
        # Below MA200 but RS not deeply negative → possible base/reclaim
        return RECLAIM_WATCH

    # Above MA200 from here
    if not above_ma50:
        # Pulled back below MA50 but still above MA200 — healthy reset
        return RESET_WATCH

    # Above both MAs — uptrend
    return DEVELOPING


def consensus_label(
    categories: List[str],
    research_score: Optional[float] = None,
) -> str:
    """
    Classify how many independent scanner categories surfaced a ticker.

    Args:
        categories: list of category keys the ticker appeared in
                    (e.g. ["early_accumulation", "catalyst_watch"])
        research_score: optional composite score; HIGH_PRIORITY requires ≥70

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
