"""
Market Posture (research assist).

Produces a compact, neutral research-breadth read for the dashboard's manual
research layer using already-cached regime, VIX, and the six-category
research-scanner board (cache/research/research_scanner_latest.json, exposed
to the TUI as DataLayer's "universe_snap" key).

This module is unrelated to the legacy Breakout Timing Engine blueprint
(``docs/strategy/BREAKOUT_TIMING_ENGINE_BLUEPRINT.md``).  It does not evaluate
confirmed Sniper breakouts, does not output ENTER/WAIT/SKIP, and does not
compute breakout probabilities or timing windows.  It is cache-only,
advisory-only, and does not feed paper evidence, governance, or execution.

Prior to the 2026-07 research-scanner migration this module derived a
bullish/defensive market-direction call from per-strategy (SNIPER/VOYAGER/
SHORT) LONG/SHORT candidate coverage, sourced from the now-decommissioned
per-strategy universe-snapshot pipeline (its builder required Alpaca
discovery, since dropped).  That pipeline has had no live writer since the
2026-06-13 trading decommission.  The replacement research-scanner artifact
carries no direction, readiness, or liquidity fields — only research_score,
RS-vs-SPY, volume-trend ratio, and MA50/200 coverage across the six
research categories — so this module now reports a neutral breadth/quality
signal (state ∈ {constructive, cautious, mixed, unknown}) with no long/short
bias, no playbook, and no per-name actionability call.

User-facing surfaces have been relabelled to "Market Posture".  The legacy
``build_research_bte`` / ``ResearchBTEOutput`` symbol names are preserved as
the primary identifiers to keep current call sites stable; the
``build_market_posture`` / ``MarketPostureOutput`` aliases below are the
preferred names for new code.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ResearchBTEOutput:
    state: str
    bias: str
    confidence: str
    factors: List[str]
    cautions: List[str]
    playbook: List[str]
    risk_flag: str
    focus_names: List[Dict[str, Any]]
    ready_long_names: List[Dict[str, Any]]
    data_quality: str
    methodology: str


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _unique_by_symbol(rows: Any) -> List[Dict[str, Any]]:
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        sym = str(row.get("symbol") or "").upper().strip()
        if not sym:
            continue
        prev = by_symbol.get(sym)
        if prev is None or _f(row.get("research_score")) > _f(prev.get("research_score")):
            by_symbol[sym] = row
    return list(by_symbol.values())


def build_research_bte(
    *,
    universe_snapshot: Optional[Dict[str, Any]],
    regime: Optional[Dict[str, Any]],
    vix: Optional[float],
) -> ResearchBTEOutput:
    """
    Build a compact manual-research breadth/quality read.

    Inputs are cache/local dashboard objects. No provider calls are made here.
    """
    snap = universe_snapshot or {}
    reg = regime or {}
    candidates = _unique_by_symbol(snap.get("candidates") or [])
    n = len(candidates)

    regime_name = str(reg.get("regime") or "UNKNOWN").upper()
    vix_val = _f(vix, -1.0)
    factors: List[str] = []
    cautions: List[str] = []

    if "BULL" in regime_name:
        factors.append(f"regime={regime_name}")
    elif "BEAR" in regime_name:
        cautions.append(f"regime={regime_name}")
    else:
        factors.append(f"regime={regime_name}")

    if vix_val < 0:
        cautions.append("VIX unavailable")
    elif vix_val >= 28:
        cautions.append(f"VIX elevated {vix_val:.1f}")
    elif vix_val < 22:
        factors.append(f"VIX supportive {vix_val:.1f}")
    else:
        factors.append(f"VIX neutral {vix_val:.1f}")

    above_ma50 = sum(1 for c in candidates if c.get("above_ma50"))
    above_ma200 = sum(1 for c in candidates if c.get("above_ma200"))
    avg_rs20 = (sum(_f(c.get("rs_20d_vs_spy")) for c in candidates) / n) if n else 0.0

    factors.append(f"breadth={n} names across categories")
    if n:
        factors.append(f"{above_ma50} above MA50")
    if n >= 25:
        factors.append("broad category coverage")
    elif n <= 5:
        cautions.append("thin category coverage")

    category_counts: Dict[str, int] = defaultdict(int)
    for c in candidates:
        for cat in (c.get("categories") or []):
            if cat:
                category_counts[str(cat)] += 1
    thin_categories = sorted(cat for cat, cnt in category_counts.items() if cnt <= 1)
    if thin_categories:
        cautions.append(f"thin coverage: {', '.join(thin_categories[:2])}")

    if not candidates:
        state = "unknown"
    else:
        ma50_ratio = above_ma50 / n
        ma200_ratio = above_ma200 / n
        breadth_score = ma50_ratio * 0.5 + ma200_ratio * 0.3 + (0.2 if avg_rs20 > 0 else 0.0)
        if "BEAR" in regime_name or vix_val >= 28:
            state = "cautious"
        elif breadth_score >= 0.6 and "BULL" in regime_name:
            state = "constructive"
        elif breadth_score <= 0.3:
            state = "cautious"
        else:
            state = "mixed"
    bias = state  # neutral: no separate directional label, see core.fragility._posture_signals

    confidence = "low"
    if candidates:
        ma50_ratio = above_ma50 / n
        if state == "constructive" and ma50_ratio >= 0.7:
            confidence = "medium"
        if state == "cautious" and (vix_val >= 28 or "BEAR" in regime_name):
            confidence = "medium"

    playbook: List[str] = []  # neutral design: no long/short playbook is derived
    risk_flag = "none"
    if vix_val >= 28:
        risk_flag = "elevated volatility — treat breadth signal as low-confidence"
    elif cautions:
        risk_flag = cautions[0]

    ranked = sorted(
        candidates,
        key=lambda c: (-_f(c.get("research_score")), str(c.get("symbol") or "")),
    )
    focus_names = [
        {
            "symbol": c.get("symbol"),
            "research_score": round(_f(c.get("research_score")), 1),
            "categories": c.get("categories") or [],
        }
        for c in ranked[:5]
    ]

    if snap.get("stale"):
        data_quality = "degraded: scan universe artifact stale"
    elif not candidates:
        data_quality = "degraded: no scanner candidates"
    elif snap.get("generated_at"):
        data_quality = f"snapshot {str(snap['generated_at'])[:19]}"
    else:
        data_quality = "snapshot timestamp unavailable"

    return ResearchBTEOutput(
        state=state,
        bias=bias,
        confidence=confidence,
        factors=factors[:5],
        cautions=cautions[:4],
        playbook=playbook[:4],
        risk_flag=risk_flag,
        focus_names=focus_names,
        ready_long_names=[],
        data_quality=data_quality,
        methodology=(
            "cache-only advisory using regime, VIX, and six-category research-scanner "
            "breadth (RS-vs-SPY, MA50/200 coverage, category spread) — "
            "no direction, no sleeve resemblance, no trade recommendation"
        ),
    )


# Preferred public names matching the new "Market Posture" labelling.  The
# legacy ``build_research_bte`` / ``ResearchBTEOutput`` symbols remain the
# canonical implementations to keep existing imports stable; new call sites
# should prefer the aliases below.
MarketPostureOutput = ResearchBTEOutput
build_market_posture = build_research_bte
