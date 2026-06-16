#!/usr/bin/env python3
"""
research/catalyst_sanity.py — Catalyst and social signal sanity validator.

Validates analyst headlines, social signals, and catalyst items before they
can upgrade a research candidate. Prevents stale, syndicated, or malformed
data from creating false high-priority labels.

Output labels:
  FRESH_COMPANY_SPECIFIC   — fresh, specific to the company, non-duplicate
  SECTOR_SPILLOVER         — catalyst is sector-wide, not company-specific
  DUPLICATE_OR_SYNDICATED  — same headline seen across multiple sources
  STALE                    — catalyst is too old to be actionable
  MALFORMED                — invalid price targets, garbled text, bad ticker match
  HYPE_CROWDED             — social attention already widely crowded
  NEEDS_MANUAL_SOURCE_CHECK — cannot automatically classify; manual review needed

A catalyst can upgrade a name ONLY when:
  - source is fresh (within freshness_hours_limit)
  - catalyst is company-specific (not sector spillover)
  - not duplicate/syndicated
  - tape is not badly extended
  - data confidence is not LOW

Research-only. No trade recommendations. No provider calls.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Output labels ─────────────────────────────────────────────────────────────
FRESH_COMPANY_SPECIFIC = "FRESH_COMPANY_SPECIFIC"
SECTOR_SPILLOVER = "SECTOR_SPILLOVER"
DUPLICATE_OR_SYNDICATED = "DUPLICATE_OR_SYNDICATED"
STALE = "STALE"
MALFORMED = "MALFORMED"
HYPE_CROWDED = "HYPE_CROWDED"
NEEDS_MANUAL_SOURCE_CHECK = "NEEDS_MANUAL_SOURCE_CHECK"

CATALYST_SANITY_LABELS = (
    FRESH_COMPANY_SPECIFIC,
    SECTOR_SPILLOVER,
    DUPLICATE_OR_SYNDICATED,
    STALE,
    MALFORMED,
    HYPE_CROWDED,
    NEEDS_MANUAL_SOURCE_CHECK,
)

# Default thresholds
DEFAULT_FRESHNESS_HOURS = 72       # headline older than this is STALE
INTRADAY_FRESHNESS_HOURS = 6       # for earnings-day content
PRICE_TARGET_MIN = 0.01            # sanity range for analyst targets
PRICE_TARGET_MAX = 100_000.0

# Sector-spillover keyword patterns (suggest catalyst is macro/sector, not company)
_SECTOR_KEYWORDS = frozenset({
    "sector", "industry", "all banks", "all tech", "all retailers", "macro",
    "fed rate", "interest rate", "inflation data", "market-wide", "broad market",
    "etf upgrade", "sector upgrade", "sector downgrade", "industry downgrade",
})

# Known syndication markers in headlines
_SYNDICATION_MARKERS = frozenset({
    "pr newswire", "business wire", "globe newswire", "accesswire",
    "syndicated", "reprinted", "sponsored",
})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _contains_any(text: str, keywords: frozenset) -> bool:
    t = _normalize(text)
    return any(k in t for k in keywords)


def _age_hours(published_at: Optional[str]) -> Optional[float]:
    """Return age in hours of a timestamp string (ISO format)."""
    if not published_at:
        return None
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() / 3600.0
    except Exception:
        return None


def validate_catalyst(
    *,
    headline: Optional[str] = None,
    ticker: Optional[str] = None,
    published_at: Optional[str] = None,
    source: Optional[str] = None,
    price_target: Optional[float] = None,
    analyst_action: Optional[str] = None,
    sector: Optional[str] = None,
    social_score: Optional[float] = None,
    crowded: bool = False,
    tape_extended: bool = False,
    data_confidence: Optional[str] = None,
    earnings_day: bool = False,
    seen_in_sources: int = 1,
) -> Dict[str, Any]:
    """
    Validate a single catalyst or social signal.

    Returns dict:
      label          — one of CATALYST_SANITY_LABELS
      can_upgrade    — True if this catalyst can upgrade priority (all gates pass)
      age_hours      — age of the catalyst
      issues         — list of detected problems
      freshness_ok   — True if within freshness window
    """
    issues: List[str] = []

    # Crowded social check (highest priority disqualifier)
    if crowded or (social_score is not None and social_score > 0.9):
        return {
            "label": HYPE_CROWDED,
            "can_upgrade": False,
            "age_hours": _age_hours(published_at),
            "issues": ["social_attention_already_crowded"],
            "freshness_ok": False,
        }

    # Tape extension check
    if tape_extended:
        issues.append("tape_extended")

    # Data confidence gate
    if data_confidence == "LOW":
        issues.append("data_confidence_LOW")
    if data_confidence == "INVALID":
        issues.append("data_confidence_INVALID")

    # Freshness check
    age = _age_hours(published_at)
    limit = INTRADAY_FRESHNESS_HOURS if earnings_day else DEFAULT_FRESHNESS_HOURS
    freshness_ok = age is not None and age <= limit

    if age is None:
        issues.append("published_at_missing")
    elif age > limit:
        issues.append(f"stale_{age:.0f}h_ago")

    if not freshness_ok:
        return {
            "label": STALE,
            "can_upgrade": False,
            "age_hours": age,
            "issues": issues,
            "freshness_ok": False,
        }

    # Malformation checks
    if price_target is not None:
        if price_target < PRICE_TARGET_MIN or price_target > PRICE_TARGET_MAX:
            issues.append(f"malformed_price_target_{price_target}")
            return {
                "label": MALFORMED,
                "can_upgrade": False,
                "age_hours": age,
                "issues": issues,
                "freshness_ok": freshness_ok,
            }

    if headline:
        if len(headline.strip()) < 10:
            issues.append("headline_too_short")
            return {
                "label": MALFORMED,
                "can_upgrade": False,
                "age_hours": age,
                "issues": issues,
                "freshness_ok": freshness_ok,
            }
        # Ticker mismatch check
        if ticker and ticker.upper() not in headline.upper():
            # Might be company name — soft flag only
            issues.append("ticker_not_in_headline")

    # Syndication check
    source_norm = _normalize(source or "")
    if _contains_any(source_norm, _SYNDICATION_MARKERS):
        issues.append("syndicated_source")
        return {
            "label": DUPLICATE_OR_SYNDICATED,
            "can_upgrade": False,
            "age_hours": age,
            "issues": issues,
            "freshness_ok": freshness_ok,
        }

    # Multiple-source appearance = syndicated content
    if seen_in_sources > 3:
        issues.append(f"seen_in_{seen_in_sources}_sources")
        return {
            "label": DUPLICATE_OR_SYNDICATED,
            "can_upgrade": False,
            "age_hours": age,
            "issues": issues,
            "freshness_ok": freshness_ok,
        }

    # Sector spillover check
    headline_text = f"{headline or ''} {sector or ''}"
    if _contains_any(headline_text, _SECTOR_KEYWORDS):
        issues.append("sector_spillover_keywords")
        return {
            "label": SECTOR_SPILLOVER,
            "can_upgrade": False,
            "age_hours": age,
            "issues": issues,
            "freshness_ok": freshness_ok,
        }

    # Hard gate: tape extended or low confidence blocks upgrade even if catalyst is fresh
    if tape_extended or data_confidence in ("LOW", "INVALID"):
        label = NEEDS_MANUAL_SOURCE_CHECK
        can_upgrade = False
    else:
        label = FRESH_COMPANY_SPECIFIC
        can_upgrade = len(issues) == 0

    return {
        "label": label,
        "can_upgrade": can_upgrade,
        "age_hours": age,
        "issues": issues,
        "freshness_ok": freshness_ok,
    }


def validate_social_signal(
    *,
    ticker: Optional[str] = None,
    social_score: Optional[float] = None,
    source: Optional[str] = None,
    crowded: bool = False,
    age_hours: Optional[float] = None,
    tape_extended: bool = False,
    data_confidence: Optional[str] = None,
    is_company_specific: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Validate a social attention signal.

    Social signals can only upgrade a name when source is fresh, not already
    crowded, company-specific, and tape is not extended.
    """
    issues: List[str] = []

    if crowded or (social_score is not None and social_score > 0.85):
        return {
            "label": HYPE_CROWDED,
            "can_upgrade": False,
            "issues": ["already_crowded"],
        }

    if age_hours is not None and age_hours > DEFAULT_FRESHNESS_HOURS:
        return {
            "label": STALE,
            "can_upgrade": False,
            "issues": [f"stale_{age_hours:.0f}h"],
        }

    if tape_extended:
        issues.append("tape_extended")
    if data_confidence in ("LOW", "INVALID"):
        issues.append(f"confidence_{data_confidence}")
    if is_company_specific is False:
        issues.append("not_company_specific")

    can_upgrade = (
        not crowded
        and not tape_extended
        and data_confidence not in ("LOW", "INVALID")
        and is_company_specific is not False
        and len(issues) == 0
    )

    return {
        "label": FRESH_COMPANY_SPECIFIC if can_upgrade else NEEDS_MANUAL_SOURCE_CHECK,
        "can_upgrade": can_upgrade,
        "issues": issues,
    }
