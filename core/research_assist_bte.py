"""
Market Posture (research assist).

Produces compact market-direction context for the dashboard's manual research
layer using already-cached regime, VIX, and universe snapshot data.

This module is unrelated to the legacy Breakout Timing Engine blueprint
(``docs/strategy/BREAKOUT_TIMING_ENGINE_BLUEPRINT.md``).  It does not evaluate
confirmed Sniper breakouts, does not output ENTER/WAIT/SKIP, and does not
compute breakout probabilities or timing windows.  It is cache-only,
advisory-only, and does not feed paper evidence, governance, or execution.

User-facing surfaces have been relabelled to "Market Posture".  The legacy
``build_research_bte`` / ``ResearchBTEOutput`` symbol names are preserved as
the primary identifiers to keep current call sites stable; the
``build_market_posture`` / ``MarketPostureOutput`` aliases below are the
preferred names for new code.

TODO: a future pass may rename the file to ``core/market_posture.py`` and
flip the canonical symbol names; do not attempt that as part of a label-only
rename pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

_ACTIVE_RESEARCH_STRATEGIES = {"SNIPER", "VOYAGER", "SHORT"}


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


def _unique_by_symbol(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_symbol: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").upper().strip()
        if not sym:
            continue
        prev = by_symbol.get(sym)
        if prev is None or _f(row.get("final_score")) > _f(prev.get("final_score")):
            by_symbol[sym] = row
    return list(by_symbol.values())


def _direction_score(row: Dict[str, Any]) -> float:
    return (
        _f(row.get("return_5d_pct")) * 0.35
        + _f(row.get("return_20d_pct")) * 0.25
        + max(_f(row.get("volume_ratio_5d")) - 1.0, 0.0) * 8.0
        + _f(row.get("final_score")) * 6.0
    )


def _sleeve_resemblance(strategy: str) -> str:
    s = str(strategy or "").upper()
    if s == "SNIPER":
        return "Sniper v6"
    if s == "VOYAGER":
        return "Voyager"
    if s == "SHORT":
        return "Short A"
    return "No active sleeve resemblance"


def _focus_actionability(row: Dict[str, Any], bte_bias: str) -> Dict[str, str]:
    """
    Research-assist only. This is not strategy logic and does not affect
    scanners, paper evidence, or execution.
    """
    strategy = str(row.get("strategy") or "").upper()
    r5 = _f(row.get("return_5d_pct"))
    r20 = _f(row.get("return_20d_pct"))
    vol = _f(row.get("volume_ratio_5d"), 1.0)
    score = _f(row.get("final_score"))

    actionable = "Yes"
    status = "WATCH"
    gate = "setup is broadly consistent"
    tag = "aligned now"

    if strategy == "SNIPER":
        if r5 >= 10 or r20 >= 30:
            actionable, status, gate, tag = "No", "Late / Extended", "late extension is inconsistent with today's playbook", "extended"
        elif vol < 0.9:
            actionable, status, gate, tag = "No", "WATCH", "confirmation volume is missing", "wait for confirmation"
        elif r5 <= 1.0:
            actionable, status, gate, tag = "No", "Watch Pullback", "early breakout pressure is present but trigger quality is not there yet", "early setup"
        else:
            # Phase 1F Task 3: research panels must not lead with
            # buy-now language. The wording is display-only; the
            # actionable_now=Yes flag is unchanged, so downstream sleeve
            # logic / governance see the same value.
            actionable, status, gate, tag = "Yes", "Research-aligned candidate", "momentum and participation are aligned", "research-aligned"
    elif strategy == "VOYAGER":
        if r5 >= 8 or r20 >= 25:
            actionable, status, gate, tag = "No", "Late / Extended", "too stretched for a constructive accumulation-style entry", "extended"
        elif r5 < -2:
            actionable, status, gate, tag = "No", "Watch Pullback", "pullback is underway but needs stabilization", "pullback watch"
        elif score < 0.4:
            actionable, status, gate, tag = "No", "WATCH", "structure resembles Voyager but quality is not high enough yet", "not actionable yet"
        else:
            actionable, status, gate, tag = "Yes", "WATCH", "constructive trend structure fits a long-side research watch", "research-aligned"
    elif strategy == "SHORT":
        actionable, status, gate, tag = "No", "Avoid", "short-side setup is inconsistent with today's long-favoring posture", "not actionable yet"
    else:
        actionable, status, gate, tag = "No", "No active sleeve fit", "no active-sleeve resemblance", "not actionable yet"

    if bte_bias == "bullish" and strategy in {"SNIPER", "VOYAGER"} and actionable == "Yes":
        # Tag was renamed in Phase 1F; recognize both legacy and current
        # values so cached snapshots from a prior build still match.
        if tag in {"research-aligned", "aligned now"} and r5 < 0:
            status, gate, tag = "Watch Pullback", "market posture favors longs, but this name is still pulling back", "pullback watch"
            actionable = "No"
    if bte_bias == "defensive" and strategy in {"SNIPER", "VOYAGER"}:
        actionable, status, gate, tag = "No", "WATCH", "market posture is too defensive for aggressive long entries", "not actionable yet"

    return {
        "actionable_now": actionable,
        "status": status,
        "gating_reason": gate,
        "compliance_tag": tag,
    }


def build_research_bte(
    *,
    universe_snapshot: Optional[Dict[str, Any]],
    regime: Optional[Dict[str, Any]],
    vix: Optional[float],
) -> ResearchBTEOutput:
    """
    Build a compact manual-research market-direction hint.

    Inputs are cache/local dashboard objects. No provider calls are made here.
    """
    snap = universe_snapshot or {}
    reg = regime or {}
    rows = _unique_by_symbol(snap.get("strategy_candidates") or [])

    ready_long = [
        r for r in rows
        if str(r.get("direction") or "").upper() == "LONG"
        and str(r.get("readiness") or "").upper() in {"READY_NOW", "WATCH"}
    ]
    ready_short = [
        r for r in rows
        if str(r.get("direction") or "").upper() == "SHORT"
        and str(r.get("readiness") or "").upper() in {"READY_NOW", "WATCH"}
    ]
    developing = [
        r for r in rows
        if str(r.get("readiness") or "").upper() == "DEVELOPING"
    ]

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

    factors.append(f"ready long={len(ready_long)}")
    factors.append(f"ready short={len(ready_short)}")
    if developing:
        factors.append(f"developing={len(developing)}")
    if len(ready_long) >= 25:
        factors.append("healthy participation")
    elif len(ready_long) <= 5:
        cautions.append("thin long participation")
    if len(ready_short) >= max(10, len(ready_long) * 0.6):
        cautions.append("short participation elevated")

    long_pressure = sum(max(_direction_score(r), 0.0) for r in ready_long[:20])
    short_pressure = sum(max(-_direction_score(r), 0.0) for r in ready_short[:20])
    if "BULL" in regime_name:
        long_pressure += 8.0
    if "BEAR" in regime_name:
        short_pressure += 8.0
    if vix_val >= 28:
        short_pressure += 5.0
    elif 0 <= vix_val < 22:
        long_pressure += 4.0

    if long_pressure > short_pressure * 1.35 and ready_long:
        state = "constructive"
        bias = "bullish"
    elif short_pressure > long_pressure * 1.35 and ready_short:
        state = "defensive"
        bias = "defensive"
    else:
        state = "mixed"
        bias = "mixed"

    spread = abs(long_pressure - short_pressure)
    if spread >= 20:
        confidence = "medium"
    elif spread >= 8:
        confidence = "low-medium"
    else:
        confidence = "low"
    if state == "constructive" and "BULL" in regime_name and 0 <= vix_val < 22 and len(ready_long) >= 25:
        confidence = "high"
    elif state == "defensive" and ("BEAR" in regime_name or vix_val >= 28):
        confidence = "high"

    playbook: List[str] = []
    risk_flag = "none"
    if state == "constructive":
        playbook.append("favor long pullbacks")
        playbook.append("favor early breakouts")
        playbook.append("avoid late chase")
    elif state == "defensive":
        playbook.append("reduce size")
        playbook.append("favor only top liquidity")
        playbook.append("avoid weak longs")
    else:
        playbook.append("be selective")
        playbook.append("favor liquid names")
        playbook.append("wait for confirmation")
    if vix_val >= 24:
        playbook.append("reduce size ahead of volatility")
    if len(ready_long) > 0 and len(ready_short) > len(ready_long) * 0.7:
        risk_flag = "two-way tape; longs can fail fast"
    elif vix_val >= 28:
        risk_flag = "elevated volatility can invalidate constructive setups"
    elif cautions:
        risk_flag = cautions[0]

    # Focus list construction.  We decorate the full active-strategy
    # candidate set with actionability up front, then apply per-bucket
    # quotas that mirror the dashboard's "Ready Now / Pullback Watch /
    # Extended Leaders" sections.  Earlier versions pre-sorted by
    # absolute direction score and trimmed to the top 8, which biased
    # the pool toward names that had already run and left the rendered
    # panel dominated by Extended Leaders.
    active_pool = [
        r for r in rows
        if str(r.get("strategy") or "").upper() in _ACTIVE_RESEARCH_STRATEGIES
    ]
    decorated = [
        {
            "symbol": r.get("symbol"),
            "strategy": r.get("strategy"),
            "readiness": r.get("readiness"),
            "direction": r.get("direction"),
            "score": round(_direction_score(r), 2),
            "_dollar_vol": _f(r.get("avg_dollar_volume_20")),
            "sleeve_resemblance": _sleeve_resemblance(r.get("strategy")),
            **_focus_actionability(r, bias),
        }
        for r in active_pool
    ]

    def _row_bucket(r: Dict[str, Any]) -> str:
        status = str(r.get("status") or "").lower()
        tag = str(r.get("compliance_tag") or "")
        if r.get("actionable_now") == "Yes" and tag == "aligned now":
            return "ready"
        if status == "extended" or tag == "extended":
            return "extended"
        if status in {"pullback watch", "watch"} or tag in {
            "pullback watch", "early setup", "wait for confirmation", "not actionable yet"
        }:
            return "pullback"
        return "other"

    def _bucket_sort_key(r: Dict[str, Any]) -> tuple:
        return (-_f(r.get("score")), -_f(r.get("_dollar_vol")), str(r.get("symbol") or ""))

    ready    = sorted([r for r in decorated if _row_bucket(r) == "ready"],    key=_bucket_sort_key)
    pullback = sorted([r for r in decorated if _row_bucket(r) == "pullback"], key=_bucket_sort_key)
    extended = sorted([r for r in decorated if _row_bucket(r) == "extended"], key=_bucket_sort_key)

    # Quotas mirror the dashboard's per-bucket cap of 2.  Prefer
    # actionable names; reserve at most one slot for an Extended Leader
    # so that bucket informs without dominating.  Backfill from
    # higher-priority buckets if any quota is short.
    target = 5
    quota = {"ready": 2, "pullback": 2, "extended": 1}
    picks: List[Dict[str, Any]] = []
    picks += ready[: quota["ready"]]
    picks += pullback[: quota["pullback"]]
    picks += extended[: quota["extended"]]

    if len(picks) < target:
        used = {(r.get("symbol"), r.get("strategy")) for r in picks}
        leftover = (
            ready[quota["ready"]:]
            + pullback[quota["pullback"]:]
            + extended[quota["extended"]:]
        )
        for r in leftover:
            key = (r.get("symbol"), r.get("strategy"))
            if key in used:
                continue
            picks.append(r)
            used.add(key)
            if len(picks) >= target:
                break

    def _final_key(r: Dict[str, Any]) -> tuple:
        rank = {"ready": 0, "pullback": 1, "extended": 2}.get(_row_bucket(r), 3)
        return (rank, -_f(r.get("score")), str(r.get("symbol") or ""))

    picks.sort(key=_final_key)
    focus_names = picks[:target]
    for r in focus_names:
        r.pop("_dollar_vol", None)

    ready_long_pool = sorted(
        ready_long,
        key=lambda r: (
            -_direction_score(r),
            -_f(r.get("avg_dollar_volume_20")),
            str(r.get("symbol") or ""),
        ),
    )
    ready_long_names = [
        {
            "symbol": r.get("symbol"),
            "strategy": r.get("strategy"),
            "readiness": r.get("readiness"),
            "score": round(_direction_score(r), 2),
            "return_5d_pct": round(_f(r.get("return_5d_pct")), 2),
            "return_20d_pct": round(_f(r.get("return_20d_pct")), 2),
            "volume_ratio_5d": round(_f(r.get("volume_ratio_5d")), 2),
        }
        for r in ready_long_pool[:5]
    ]

    built_at = str((snap.get("summary") or {}).get("built_at") or "")
    fallback = bool((snap.get("summary") or {}).get("fallback_used"))
    if fallback:
        data_quality = "degraded: universe fallback"
    elif not rows:
        data_quality = "degraded: no universe candidates"
    elif built_at:
        data_quality = f"snapshot {built_at[:19]}"
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
        ready_long_names=ready_long_names,
        data_quality=data_quality,
        methodology=(
            "cache-only advisory using regime, VIX, active universe readiness, "
            "5d/20d returns, relative volume, and explicit dollar-volume fields"
        ),
    )


# Preferred public names matching the new "Market Posture" labelling.  The
# legacy ``build_research_bte`` / ``ResearchBTEOutput`` symbols remain the
# canonical implementations to keep existing imports stable; new call sites
# should prefer the aliases below.
MarketPostureOutput = ResearchBTEOutput
build_market_posture = build_research_bte
