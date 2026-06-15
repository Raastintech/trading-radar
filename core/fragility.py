"""
core/fragility.py — Phase 1F research fragility overlay.

Diagnostic-only. Reads cached research artifacts (Market Forecast,
Market Posture, Stock Lens, Options, Alpha Discovery, VIX) and decides
whether the *display* layer should lead with a calm "NORMAL" framing or
warn the operator about conflicting research signals.

This module:
  - never calls a provider
  - never reads or mutates the trading DB
  - never gates execution, paper governance, scanning, or scoring
  - never changes sleeve status

It is pure presentation policy: when research views disagree, the
dashboard should *not* lead with bullish language and "BUY candidate"
calls-to-action. That class of mismatch is exactly what produced the
2026-05-15 audit screenshot — Bull Continuation visually prominent
while the headline invalidation was already breached and confidence
was LOW.

States:
  NORMAL      — research views agree, no breaches, regime confirmed.
  CONFLICTED  — exactly one strong disagreement signal (e.g. posture
                bullish but forecast invalidation breached).
  FRAGILE     — multiple signals diverging at once; setups must be
                stalked rather than entered.
  STRESS      — broad risk-off pressure: VIX elevated, forecast
                risk-off dominant, invalidation breached.
  UNKNOWN     — the forecast artifact is missing or unreadable; we
                refuse to claim a state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── Thresholds (in one place so they're easy to find / tune) ────────────────

# VIX bands. Below COOL we ignore the VIX as a fragility input; between
# COOL and HOT it's a single fragility signal; above HOT we escalate to
# STRESS-level pressure on its own.
VIX_COOL  = 18.0
VIX_HOT   = 22.0
VIX_PANIC = 28.0

# Forecast risk-off probability mass that, alone, is enough to push the
# overlay toward STRESS when combined with a breach.
RISK_OFF_DOMINANT = 0.40


@dataclass
class FragilityResult:
    """Display-ready summary of cross-artifact research consistency."""
    status: str = "UNKNOWN"          # NORMAL | CONFLICTED | FRAGILE | STRESS | UNKNOWN
    reasons: List[str] = field(default_factory=list)
    action_hint: str = ""
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status":      self.status,
            "reasons":     list(self.reasons),
            "action_hint": self.action_hint,
            "signals":     dict(self.signals),
        }


# ── Signal extractors ────────────────────────────────────────────────────────

def _norm(v: Any) -> str:
    return str(v or "").strip().lower()


def _forecast_signals(forecast: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Pull the few forecast fields the overlay actually reads."""
    if not forecast:
        return {"available": False}
    head = forecast.get("headline") or {}
    probs = forecast.get("regime_probabilities") or []
    by_regime = {
        _norm(r.get("regime")): float(r.get("probability") or 0)
        for r in probs
    }
    risk_off_p = by_regime.get("risk-off", 0.0)
    bull_p = by_regime.get("bull continuation", 0.0) + by_regime.get(
        "bull pullback / buy-the-dip", 0.0
    )
    return {
        "available":            True,
        "regime":               head.get("current_regime"),
        "confidence":           _norm(head.get("confidence")),
        "breached":             bool(head.get("invalidation_breached")),
        "breach_reasons":       list(head.get("invalidation_breach_reasons") or []),
        "risk_off_probability": risk_off_p,
        "bull_probability":     bull_p,
    }


def _posture_signals(posture: Optional[Any]) -> Dict[str, Any]:
    """Posture comes in as either a dict (cached snapshot) or the BTE
    output object from core.research_assist_bte. We just need bias and
    confidence."""
    if posture is None:
        return {"available": False}
    bias = None
    confidence = None
    # Dict shape
    if isinstance(posture, dict):
        bias = posture.get("bias") or posture.get("posture") or posture.get("regime")
        confidence = posture.get("confidence") or posture.get("posture_confidence")
    # Object shape (research_assist_bte.BteOutput)
    else:
        bias = getattr(posture, "bias", None)
        confidence = getattr(posture, "confidence", None)
    return {
        "available":  True,
        "bias":       _norm(bias),
        "confidence": _norm(confidence),
    }


def _lens_signals(lens: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not lens:
        return {"available": False}
    layers = lens.get("layers") or {}
    entry = (layers.get("entry") or {}).get("view")
    options = (layers.get("options") or {}).get("view")
    sector = (layers.get("sector") or {}).get("view")
    return {
        "available":  True,
        "label":      _norm(lens.get("label")),
        "confidence": _norm(lens.get("confidence")),
        "entry":      _norm(entry),
        "options":    _norm(options),
        "sector":     _norm(sector),
    }


def _alpha_signals(alpha: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not alpha:
        return {"available": False}
    summary = alpha.get("summary") or {}
    return {
        "available":  True,
        "tone":       _norm(summary.get("tone") or alpha.get("tone")),
        "a_count":    int(summary.get("a_count") or alpha.get("a_count") or 0),
    }


def _vix_signals(vix: Optional[float]) -> Dict[str, Any]:
    if vix is None:
        return {"available": False}
    try:
        v = float(vix)
    except (TypeError, ValueError):
        return {"available": False}
    return {
        "available": True,
        "value":     v,
        "hot":       v >= VIX_HOT,
        "panic":     v >= VIX_PANIC,
    }


# ── Action hints ────────────────────────────────────────────────────────────

_ACTION_HINTS: Dict[str, str] = {
    "STRESS":
        "no new entries · monitor only · defend stops",
    "FRAGILE":
        "stalk only · demand reclaim/pullback · avoid chase",
    "CONFLICTED":
        "downsize · prioritize highest-conviction setups · require confirmation",
    "NORMAL":
        "standard playbook · entries permitted within sleeve rules",
    "UNKNOWN":
        "research artifact missing · degrade to manual checks",
}


# ── Public API ──────────────────────────────────────────────────────────────

def evaluate_fragility(
    *,
    forecast: Optional[Dict[str, Any]] = None,
    posture: Optional[Any] = None,
    lens: Optional[Dict[str, Any]] = None,
    alpha: Optional[Dict[str, Any]] = None,
    vix: Optional[float] = None,
) -> FragilityResult:
    """Combine the cached research views into a single display state.

    All inputs are optional: when an artifact is missing the function
    skips that signal rather than raising. The result is never
    intended to gate a trade — it controls *wording* on the dashboard.
    """
    fs = _forecast_signals(forecast)
    ps = _posture_signals(posture)
    ls = _lens_signals(lens)
    al = _alpha_signals(alpha)
    vs = _vix_signals(vix)

    if not fs.get("available"):
        return FragilityResult(
            status="UNKNOWN",
            reasons=["Market Forecast artifact missing"],
            action_hint=_ACTION_HINTS["UNKNOWN"],
            signals={"forecast": fs, "posture": ps, "lens": ls, "alpha": al, "vix": vs},
        )

    reasons: List[str] = []

    # ── Forecast-side signals ────────────────────────────────────────────────
    if fs.get("breached"):
        first = (fs.get("breach_reasons") or [""])[0]
        reasons.append(
            f"forecast invalidation breached"
            + (f" ({first})" if first else "")
        )
    forecast_low_conf = fs.get("confidence") == "low"
    if forecast_low_conf:
        reasons.append("forecast confidence LOW")

    # Forecast risk-off mass alone — even without a breach — implies a
    # tape that disagrees with bullish posture talk.
    if fs.get("risk_off_probability", 0.0) >= RISK_OFF_DOMINANT:
        reasons.append(
            f"forecast risk-off probability {fs['risk_off_probability']*100:.0f}%"
        )

    # ── Posture vs forecast disagreement ────────────────────────────────────
    if ps.get("available") and ps.get("bias") == "bullish":
        if fs.get("breached") or forecast_low_conf:
            reasons.append("posture bullish but regime not confirmed")
        elif fs.get("risk_off_probability", 0.0) >= RISK_OFF_DOMINANT:
            reasons.append(
                "posture bullish but forecast tilts risk-off"
            )

    # ── Lens / options / entry signals (per-ticker) ─────────────────────────
    if ls.get("available"):
        if "bearish hedge" in ls.get("options", ""):
            reasons.append("options layer flagged bearish hedge")
        if ls.get("entry") in {"too extended", "extended", "broken", "avoid"}:
            reasons.append(f"entry layer {ls['entry']}")
        if ls.get("label", "").startswith("bearish") and ps.get("bias") == "bullish":
            reasons.append("stock lens bearish but posture bullish")

    # ── Alpha vs forecast ───────────────────────────────────────────────────
    if al.get("available"):
        if al.get("tone") == "bullish" and fs.get("risk_off_probability", 0.0) >= RISK_OFF_DOMINANT:
            reasons.append("alpha bullish but forecast risk-off dominant")

    # ── VIX ──────────────────────────────────────────────────────────────────
    if vs.get("available"):
        if vs.get("panic"):
            reasons.append(f"VIX {vs['value']:.1f} ≥ {VIX_PANIC:.0f} (panic)")
        elif vs.get("hot"):
            reasons.append(f"VIX {vs['value']:.1f} ≥ {VIX_HOT:.0f}")

    # ── Status decision ─────────────────────────────────────────────────────
    # STRESS: VIX panic, or (breach + risk-off-dominant forecast).
    stress = (
        vs.get("panic")
        or (fs.get("breached") and fs.get("risk_off_probability", 0.0) >= RISK_OFF_DOMINANT)
    )
    if stress:
        status = "STRESS"
    elif len(reasons) >= 2:
        status = "FRAGILE"
    elif len(reasons) == 1:
        status = "CONFLICTED"
    else:
        status = "NORMAL"
        # Keep an explicit "all clear" reason for the operator UI so the
        # NORMAL line doesn't look like missing data.
        reasons = ["all research views aligned"]

    return FragilityResult(
        status=status,
        reasons=reasons,
        action_hint=_ACTION_HINTS[status],
        signals={"forecast": fs, "posture": ps, "lens": ls, "alpha": al, "vix": vs},
    )


# ── Wording helpers exposed for dashboard consumers ─────────────────────────

# Phase 1F Task 5: when Entry Validator returns one of these, no panel
# may use buy-now wording. Centralized so any new panel inherits the
# discipline by importing this set.
NOT_ACTIONABLE_ENTRY_LABELS = frozenset({
    "too extended",
    "extended",
    "broken",
    "avoid",
})


def is_entry_actionable(entry_label: Any) -> bool:
    """True when the entry layer permits actionable language."""
    return _norm(entry_label) not in NOT_ACTIONABLE_ENTRY_LABELS


# Phase 1F Task 3: aggressive labels to neutralize and what to render
# instead. Dashboard call sites should normalize the status text through
# this map rather than emitting "BUY candidate" directly.
_RESEARCH_LABEL_MAP: Dict[str, str] = {
    "buy candidate":     "Research-aligned candidate",
    "buy now":           "Setup candidate · requires validation",
    "ready now":         "Research-aligned candidate",
    "buy candidat":      "Research-aligned candidate",   # truncated form
}


def neutralize_research_label(label: Any, *, entry_label: Any = None) -> str:
    """Translate an aggressive research/dashboard label into the
    Phase 1F vocabulary. When the entry layer is non-actionable, even
    a 'WATCH' candidate is rendered as 'Research Only' to avoid
    visually leading with green text against an extended setup."""
    raw = str(label or "").strip()
    canonical = _RESEARCH_LABEL_MAP.get(raw.lower(), raw)
    if not is_entry_actionable(entry_label):
        # Hard discipline: don't show a green "candidate" badge against
        # an extended / broken / avoid entry. Demote to research-only.
        if canonical.lower() in {"research-aligned candidate",
                                 "setup candidate · requires validation",
                                 "buy candidate"}:
            return "Research Only · entry not actionable"
    return canonical
