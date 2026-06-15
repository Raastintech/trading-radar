"""
research/mcp_audit_orchestrator.py — Phase 2B.1 session orchestration.

Composes the Phase 2B workflows in ``research/mcp_audit_workflows.py``
into one session audit and emits two deterministic artifacts:

  cache/research/mcp_analysis_latest.json    machine-readable summary
  logs/mcp_audit_daily_latest.md             markdown forensic summary
  logs/mcp_audit_daily_<UTC_TS>.md           timestamped copy

Doctrine (must all hold):

  - Read-only. Composes existing cache-only helpers; no DB writes,
    no provider/broker imports, no execution imports.
  - Deterministic. Every line in the markdown summary is derived
    from audit fields with no invented values; the dashboard panel
    reads the JSON sidecar — never invokes MCP, providers, or Claude.
  - Bounded blast radius. Ticker drilldowns are capped at 10,
    deduped, and severity-ordered so a noisy board cannot fan out
    into hundreds of cache writes.
  - Operator-facing wording only. Markdown does not propose trades
    or sleeves; every artifact carries the no-trade disclaimer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import mcp_audit_workflows as w  # noqa: E402


# ── Constants ─────────────────────────────────────────────────────────

MAX_DRILLDOWN_TICKERS = 10

# Anomaly severity ordering — HIGH first when capping the drilldown set.
SEV_HIGH   = "HIGH"
SEV_MEDIUM = "MEDIUM"
SEV_LOW    = "LOW"
_SEV_RANK  = {SEV_HIGH: 0, SEV_MEDIUM: 1, SEV_LOW: 2}

# Overall state classifier. Order is most-severe → least; the
# classifier picks the most-severe applicable label.
STATE_BLOCKED    = "BLOCKED"
STATE_STALE      = "STALE"
STATE_FRAGILE    = "FRAGILE"
STATE_CONFLICTED = "CONFLICTED"
STATE_NORMAL     = "NORMAL"
_STATE_ORDER     = [STATE_BLOCKED, STATE_STALE, STATE_FRAGILE,
                    STATE_CONFLICTED, STATE_NORMAL]

# Markdown is the operator's audit log — must always carry this line.
NO_TRADE_DISCLAIMER = (
    "Research-only. Not trade approval. No execution action was taken."
)

# Deterministic mapping from per-ticker action_label → operator-readable
# verb phrase used on the dashboard's Action line and in the markdown
# "Recommended action" row. Ordering controls the joined display order
# (least-severe → most-severe) so the line reads naturally.
ACTION_LABEL_TO_TEXT: Dict[str, str] = {
    "Research Only":  "research only",
    "Watch Reclaim":  "wait for pullback/reclaim",
    "Missing Data":   "refresh missing artifacts",
    "Avoid Chase":    "avoid chase",
    "Blocked":        "avoid blocked names",
}
ACTION_ORDER: Tuple[str, ...] = (
    "Research Only", "Watch Reclaim", "Missing Data",
    "Avoid Chase", "Blocked",
)

# Compact keyword form for the Mode 2 Research one-liner. Picks the
# single most-severe action present in the drilldown set.
ACTION_LABEL_TO_KEYWORD: Dict[str, str] = {
    "Research Only": "research",
    "Watch Reclaim": "wait reclaim",
    "Missing Data":  "refresh",
    "Avoid Chase":   "avoid chase",
    "Blocked":       "avoid blocked",
}
ACTION_SEVERITY: Tuple[str, ...] = (
    "Blocked", "Avoid Chase", "Missing Data", "Watch Reclaim", "Research Only",
)

# State-only fallback wording (used when no anomaly tickers were drilled).
STATE_TO_ACTION_TEXT: Dict[str, str] = {
    STATE_BLOCKED:    "pause · investigate halt/drift",
    STATE_STALE:      "refresh stale artifacts",
    STATE_FRAGILE:    "wait · treat regime as fragile",
    STATE_CONFLICTED: "research individually",
    STATE_NORMAL:     "continue observation",
}
STATE_TO_ACTION_KEYWORD: Dict[str, str] = {
    STATE_BLOCKED:    "pause",
    STATE_STALE:      "refresh",
    STATE_FRAGILE:    "wait",
    STATE_CONFLICTED: "research",
    STATE_NORMAL:     "observe",
}

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


# ── Paths ─────────────────────────────────────────────────────────────

def _repo_root() -> Path:
    env = os.environ.get("STOCKLENS_ROOT")
    if env:
        p = Path(env).expanduser().resolve()
        if p.exists():
            return p
    return Path(__file__).resolve().parents[1]


def _json_path() -> Path:
    return _repo_root() / "cache" / "research" / "mcp_analysis_latest.json"


def _md_latest_path() -> Path:
    return _repo_root() / "logs" / "mcp_audit_daily_latest.md"


def _md_timestamped_path(ts_utc: datetime) -> Path:
    slug = ts_utc.strftime("%Y%m%dT%H%M%SZ")
    return _repo_root() / "logs" / f"mcp_audit_daily_{slug}.md"


# ── Phase 1G.2 sidecar readers ────────────────────────────────────────


def _read_signal_hygiene_counters() -> Optional[Dict[str, Any]]:
    """Read ``cache/research/signal_hygiene_latest.json`` if present.

    Never raises; returns None when the sidecar is missing or unparseable.
    The dashboard / MCP audit can consult this for duplicate /
    presize_rejected / short_regime_suppressed counters without polling
    the DB. Cache-only.
    """
    path = _repo_root() / "cache" / "research" / "signal_hygiene_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_legacy_decision_policy() -> Optional[Dict[str, Any]]:
    """Read ``cache/research/legacy_decision_policy_latest.json`` if
    present. The orchestrator surfaces the header recommendation and
    counts so the operator sees the policy verdict without opening the
    file. No DB writes happen as part of this read.
    """
    path = _repo_root() / "cache" / "research" / "legacy_decision_policy_latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_json_sidecar(name: str) -> Optional[Dict[str, Any]]:
    """Read a cache/research/<name> JSON sidecar. Never raises."""
    path = _repo_root() / "cache" / "research" / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_phase_1g3_surfaces() -> Dict[str, Any]:
    """Phase 1G.3 — compact, cache-only surfacing of the evidence-hygiene
    artifacts: SHORT_A freeze status, Short Opportunity Radar state, forward
    resolver health, LEADER_RESET event-study verdict, and VOYAGER conversion.
    Cache-only reads; no providers, no MCP, no DB writes.
    """
    radar = _read_json_sidecar("short_opportunity_radar_latest.json")
    fwd = _read_json_sidecar("forward_resolution_health_latest.json")
    les = _read_json_sidecar("leader_reset_event_study_latest.json")
    voy = _read_json_sidecar("voyager_conversion_audit_latest.json")
    # Phase 1G.16 — short DETECTION audit (distinct from the frozen SHORT_A sleeve).
    sda = _read_json_sidecar("short_detection_truth_audit_latest.json")
    sdf = _read_json_sidecar("short_detection_forward_latest.json")
    return {
        "short_a_status": "FROZEN / RESEARCH ONLY (2026-05-24)",
        "short_radar": None if radar is None else {
            "state": radar.get("state"),
            "score": radar.get("short_regime_score"),
            "suppressed_bull_tape": radar.get("suppressed_bull_tape"),
        },
        "short_detection": None if sda is None else {
            "proposed_state": (sda.get("proposed_state") or {}).get("state"),
            "move_classification": (sda.get("tape") or {}).get("move_classification"),
            "qqq_drawdown_10d_pct": (sda.get("tape") or {}).get("qqq_drawdown_10d_pct"),
            "missed_breakdown": bool((sda.get("missed_short_autopsy") or {}).get("n_missed_by_radar", 0) > 0),
            "n_missed_by_radar": (sda.get("missed_short_autopsy") or {}).get("n_missed_by_radar"),
            "rule_too_broad": (sda.get("suppression_diagnosis") or {}).get("rule_too_broad"),
            "redesign_verdict": (sda.get("short_redesign_justified") or {}).get("verdict"),
            "forward_verdict": None if sdf is None else sdf.get("verdict"),
        },
        "forward_resolution": None if fwd is None else {
            "status": fwd.get("resolver_status"),
            "forecast_open": fwd.get("forecast_open"),
            "forecast_matured": fwd.get("forecast_matured"),
            "lens_open": fwd.get("lens_open"),
            "lens_matured": fwd.get("lens_matured"),
            "next_maturity_due": fwd.get("next_maturity_due"),
        },
        "leader_reset_event_study": None if les is None else {
            "verdict": les.get("verdict"),
            "research_ready_n": (les.get("state_counts") or {}).get("RESEARCH_READY"),
        },
        "voyager_conversion": None if voy is None else {
            "approval_to_signal_conversion": voy.get("approval_to_signal_conversion"),
            "approvals": voy.get("approvals"),
            "signals": voy.get("signals"),
        },
    }


def _read_strategy_tournament() -> Optional[Dict[str, Any]]:
    """Phase 1G.4 — compact, cache-only read of the Strategy Tournament sidecar.
    None-safe; reads only the published verdict fields. No providers, no MCP,
    no DB writes, no recompute."""
    t = _read_json_sidecar("strategy_tournament_latest.json")
    if t is None:
        return None
    return {
        "best_candidate": t.get("best_candidate"),
        "verdict": t.get("best_candidate_verdict"),
        "short_side": t.get("short_side"),
        "options_expression": t.get("options_expression"),
        "no_trade_recommendation": t.get("no_trade_recommendation"),
    }


def _read_strategy_lab() -> Optional[Dict[str, Any]]:
    """Phase 1H - compact, cache-only read of the Strategy Research Lab.
    None-safe; reads only published summary fields. No providers, no MCP,
    no DB writes, no recompute."""
    t = _read_json_sidecar("strategy_research_lab_latest.json")
    if t is None or t.get("error"):
        return None
    best = t.get("best_variant")
    verdict = t.get("best_variant_verdict")
    ranked = t.get("ranked_variants") or []
    best_row = ranked[0] if ranked else {}
    rel_vals = []
    if best:
        for w in (t.get("windows") or {}).values():
            m = (((w.get("by_cost") or {}).get("base_cost") or {}).get(best) or {})
            if m.get("rel_spy") is not None and m.get("trade_count"):
                rel_vals.append(float(m.get("rel_spy")))
    rel_spy = round(sum(rel_vals) / len(rel_vals), 6) if rel_vals else None
    wf = (_read_json_sidecar("strategy_walk_forward_exact_latest.json")
          or _read_json_sidecar("strategy_walk_forward_latest.json"))
    # Phase 1H.1 - mode/sampled labels and the decision-tournament paper gate.
    mode = t.get("mode") or ("quick" if str(t.get("run_scope", "")).startswith("QUICK") else "default")
    sampled = bool(t.get("sampled", str(t.get("run_scope", "")).startswith("QUICK")))
    decision = _read_json_sidecar("strategy_lab_decision_latest.json")
    paper_ready = bool(decision and (decision.get("ready_variants") or []))
    return {
        "best_variant": best,
        "verdict": verdict,
        "mode": mode,
        "sampled": sampled,
        "trades": best_row.get("trade_count"),
        "rel_spy": rel_spy,
        "walk_forward": None if wf is None else wf.get("verdict"),
        "status": t.get("status") or "RESEARCH_ONLY",
        "paper_active": False,
        "paper_ready": paper_ready,
        "decision_status": None if decision is None else (decision.get("paper_shadow") or {}).get("status"),
        "paper_shadow": (t.get("paper_shadow") or {}).get("status"),
    }


def _read_rs_theme_triage() -> Optional[Dict[str, Any]]:
    """Phase 1G.9 — compact, cache-only read of the RS/Theme → Lens/Gatekeeper
    triage sidecar. None-safe; reads only published summary/verdict fields. No
    providers, no MCP, no DB writes, no recompute."""
    t = _read_json_sidecar("rs_theme_lens_triage_latest.json")
    if t is None:
        return None
    s = t.get("summary") or {}
    return {
        "evaluated": s.get("candidates_evaluated"),
        "research_watch": s.get("research_watch"),
        "needs_lens": s.get("needs_lens"),
        "needs_gatekeeper": s.get("needs_gatekeeper"),
        "too_extended": s.get("too_extended"),
        "blocked": s.get("blocked"),
        "verdict": t.get("verdict"),
    }


def _read_options_snapshot_health() -> Optional[Dict[str, Any]]:
    """Phase 1J.2 — compact, cache-only read of the options chain snapshot
    collection health sidecar. DATA_COLLECTION_ONLY surface: reports whether
    the daily point-in-time chain collection is running and how far the IVR
    history gates are. No providers, no recompute, never a signal."""
    t = _read_json_sidecar("options_chain_snapshot_health_latest.json")
    if t is None:
        return None
    gates = t.get("ivr_gates") or {}
    return {
        "generated_at": t.get("generated_at"),
        "status": t.get("status"),
        "strategy_status": t.get("strategy_status"),
        "last_snapshot": t.get("last_snapshot_date"),
        "days_collected": t.get("snapshot_days_collected"),
        "missed_days": t.get("missed_day_count"),
        "symbols_latest": len(t.get("symbols_latest_day") or []),
        "contracts_latest": t.get("contracts_latest_day"),
        "ivr_partial_eta": gates.get("partial_eta"),
        "ivr_feasible_eta": gates.get("feasible_eta"),
    }


def _read_options_regime() -> Optional[Dict[str, Any]]:
    """Phase 1G.12 — compact, cache-only read of the Options Regime Lens
    sidecar. None-safe; reads only the published market rollup + per-symbol
    regime labels. No providers, no MCP, no DB writes, no recompute. This is a
    DIAGNOSTIC surface only — never a veto, never an approval."""
    t = _read_json_sidecar("options_regime_lens_latest.json")
    if t is None or t.get("error"):
        return None
    mk = t.get("market") or {}
    per = []
    for s in (t.get("per_symbol") or []):
        if s.get("symbol") not in ("SPY", "QQQ", "IWM"):
            continue
        per.append({
            "symbol": s.get("symbol"),
            "regime": s.get("options_regime"),
            "skew": (s.get("skew") or {}).get("skew_state"),
            "iv": s.get("iv_state"),
            "gamma": (s.get("gamma") or {}).get("gamma_regime"),
        })
    return {
        "generated_at": t.get("generated_at"),
        "feed_configured": t.get("feed_configured"),
        "market_regime": mk.get("market_options_regime"),
        "anchor": mk.get("anchor_symbol"),
        "risk_warning_level": mk.get("risk_warning_level"),
        "skew_state": mk.get("skew_state"),
        "iv_state": mk.get("iv_state"),
        "gamma_regime": mk.get("gamma_regime"),
        "term_structure_state": mk.get("term_structure_state"),
        "per_symbol": per,
    }


def _read_rs_theme_forward() -> Optional[Dict[str, Any]]:
    """Phase 1G.11 — compact, cache-only read of the RS/Theme forward-validation
    sidecar. None-safe; reads only published summary/verdict fields. No providers,
    no MCP, no DB writes, no recompute."""
    t = _read_json_sidecar("rs_theme_forward_validation_latest.json")
    if t is None or t.get("error"):
        return None
    ph = (t.get("by_horizon") or {}).get("5d", {}) or {}
    a = ph.get("A_lens_ready") or {}
    vals = (t.get("comparison") or {}).get("values") or {}
    return {
        "cohort": t.get("cohort_tag"),
        "matured": t.get("n_matured_lens_ready_primary"),
        "lens_ready_5d_rel_spy": a.get("mean_rel_spy"),
        "vs_alpha": vals.get("alpha_board_rel_spy"),
        "vs_random": vals.get("random_rel_spy"),
        "verdict": t.get("verdict"),
    }


def _read_gatekeeper_precision() -> Optional[Dict[str, Any]]:
    """Phase 1G.13 — compact, cache-only read of the Gatekeeper precision-audit
    sidecar. None-safe; reads only published summary/verdict fields. No providers,
    no MCP, no DB writes, no recompute."""
    t = _read_json_sidecar("gatekeeper_precision_audit_latest.json")
    if t is None or t.get("error"):
        return None
    ph = (t.get("by_horizon") or {}).get("5d", {}) or {}
    a = ph.get("A_blocked") or {}
    b = ph.get("B_watch_not_blocked") or {}
    bc = t.get("blocked_cohort") or {}
    v = t.get("verdict") or {}
    return {
        "n_blocked": bc.get("n_total"),
        "n_matured": bc.get("n_matured_primary"),
        "blocked_5d": a.get("mean_rel_spy"),
        "watch_5d": b.get("mean_rel_spy"),
        "verdict": v.get("gatekeeper_verdict"),
        "recommendation": v.get("recommendation"),
    }


def _read_power_trend_extension() -> Optional[Dict[str, Any]]:
    """Phase 1G.14 — compact, cache-only read of the power-trend extension study
    sidecar. None-safe; reads only published summary/verdict fields. No providers,
    no MCP, no DB writes, no recompute."""
    t = _read_json_sidecar("power_trend_extension_latest.json")
    if t is None or t.get("error"):
        return None
    ph = (t.get("by_horizon_label") or {}).get("5d", {}) or {}

    def rel(lbl):
        return (ph.get(lbl) or {}).get("mean_rel_spy")
    v = t.get("verdict") or {}
    return {
        "n_cohort": (t.get("cohort") or {}).get("n_total"),
        "too_extended_5d": rel("ALL_TOO_EXTENDED"),
        "power_trend_5d": rel("POWER_TREND_EXTENSION"),
        "climax_5d": rel("CLIMAX_CHASE_EXTENSION"),
        "verdict": v.get("power_trend_verdict"),
        "recommendation": v.get("recommendation"),
    }


def _read_recall_shadow() -> Optional[Dict[str, Any]]:
    """Path B — compact, cache-only read of the Recall-Repair Shadow forward
    sidecar.  None-safe; reads only published summary/verdict fields.  No
    providers, no MCP, no DB writes, no recompute."""
    t = _read_json_sidecar("recall_repair_shadow_forward_latest.json")
    if t is None or t.get("error"):
        # Still surface the verdict if the sidecar exists but is empty.
        return ({"verdict": t.get("verdict")} if (t and t.get("verdict")) else None)
    h5 = (t.get("by_horizon") or {}).get("5", {}) or {}
    rec20 = (t.get("recall_at_recall_horizon") or {}).get("+20pct", {}) or {}
    return {
        "history_days": t.get("history_days"),
        "mature_ticker_days": t.get("mature_ticker_days_primary"),
        "shadow_rel_spy_5d": h5.get("shadow_rel_spy_avg"),
        "random_rel_spy_5d": h5.get("random_rel_spy_avg"),
        "shadow_recall_20pct": rec20.get("shadow_recall_pct"),
        "funnel_recall_benchmark": rec20.get("funnel_recall_benchmark_pct"),
        "verdict": t.get("verdict"),
    }


def _read_participation() -> Optional[Dict[str, Any]]:
    """Phase 1G.17 — compact, cache-only read of the participation-bottleneck
    audit sidecar. None-safe; reads only published verdict/summary fields.
    No providers, no MCP, no DB writes, no recompute."""
    t = _read_json_sidecar("participation_bottleneck_audit_latest.json")
    if t is None or t.get("error"):
        return None
    v = t.get("verdicts") or {}
    recent = t.get("recent_window") or {}
    last = t.get("last_dates") or {}
    return {
        "state": v.get("participation_state"),
        "reason": v.get("participation_reason"),
        "council": v.get("council_state"),
        "execution": v.get("execution_state"),
        "last_decision": last.get("last_decision"),
        "sniper_flow": (recent.get("SNIPER") or {}).get("opportunities"),
        "voyager_flow": (recent.get("VOYAGER") or {}).get("opportunities"),
        "verdict_basis": v.get("verdict_basis"),
    }


def _read_social_attention() -> Optional[Dict[str, Any]]:
    """Phase 1G.15 — compact, cache-only read of the Social Attention Radar +
    its forward-validation sidecar. None-safe; reads only published summary
    fields. No providers, no MCP, no DB writes, no recompute."""
    radar = _read_json_sidecar("social_attention_radar_latest.json")
    fwd = _read_json_sidecar("social_attention_forward_latest.json")
    if radar is None and fwd is None:
        return None
    c = (radar or {}).get("counts") or {}
    out: Dict[str, Any] = {
        "leads": c.get("leads"),
        "social_led": c.get("social_led"),
        "news_led": c.get("news_led"),
        "stealth": c.get("stealth"),
        "early": c.get("early"),
        "crowded": c.get("crowded"),
    }
    if fwd is not None:
        out["forward_verdict"] = fwd.get("verdict")
        out["history_days"] = fwd.get("history_days")
    return out


def _read_snapshot_maturation() -> Dict[str, Any]:
    """Summarise forecast + stock-lens snapshot maturation status from
    the existing JSONL ledgers under ``data/state/``. Cache-only — never
    touches providers or resolves on the spot.
    """
    root = _repo_root()
    summary = {
        "version": "SNAPSHOT_MATURATION_V1",
        "forecast": None,
        "stock_lens": None,
    }
    for kind, fname in (
        ("forecast", "regime_forecast_forward_log.jsonl"),
        ("stock_lens", "stock_lens_forward_log.jsonl"),
    ):
        path = root / "data" / "state" / fname
        if not path.exists():
            summary[kind] = {"present": False, "path": str(path)}
            continue
        total = 0
        matured = 0
        open_rows = 0
        with_outcomes = 0
        anchor_ages = []
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    total += 1
                    status = row.get("status")
                    if status == "matured":
                        matured += 1
                    else:
                        open_rows += 1
                    outcomes = row.get("outcomes") or {}
                    if any(v is not None for v in outcomes.values()):
                        with_outcomes += 1
                    anc = str(row.get("anchor_date") or "")[:10]
                    if anc:
                        try:
                            from datetime import date as _d
                            d = _d.fromisoformat(anc)
                            anchor_ages.append((datetime.now(timezone.utc).date() - d).days)
                        except Exception:
                            pass
        except Exception:
            summary[kind] = {"present": True, "path": str(path), "error": "read_failed"}
            continue
        oldest = max(anchor_ages) if anchor_ages else None
        # next maturity estimate: forecast/lens use the 20d horizon ×
        # the 1.5 trading-to-calendar fudge factor in core/forecast_forward_tracker
        # so we need ~31 calendar days before a snapshot can resolve.
        need_calendar_days = 31
        next_due_in_days = None
        if anchor_ages:
            most_mature = max(anchor_ages)
            next_due_in_days = max(0, need_calendar_days - most_mature)
        summary[kind] = {
            "present": True,
            "total": total,
            "matured": matured,
            "open": open_rows,
            "with_partial_outcomes": with_outcomes,
            "oldest_open_anchor_age_days": oldest,
            "next_maturity_due_in_days": next_due_in_days,
        }
    return summary


# ── Anomaly extraction ───────────────────────────────────────────────

def _collect_late_chase_anomalies(
    late: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Walk the late-chase candidates and produce a flat anomaly list.

    Each anomaly carries:
      ticker        — uppercase, validated
      severity      — HIGH | MEDIUM | LOW
      reasons       — one-line strings, deterministic from audit data
    """
    out: List[Dict[str, Any]] = []
    if (late or {}).get("status") != "ok":
        return out
    buckets = (late or {}).get("buckets") or {}
    for c in buckets.get("extended") or []:
        reasons = _ticker_reasons(c, extra=["extended"])
        out.append(_anomaly(c.get("ticker"), SEV_HIGH, reasons))
    for c in buckets.get("blocked") or []:
        reasons = _ticker_reasons(c, extra=["gatekeeper_block"])
        out.append(_anomaly(c.get("ticker"), SEV_HIGH, reasons))
    for c in buckets.get("broken") or []:
        reasons = _ticker_reasons(c, extra=["broken_or_avoid"])
        out.append(_anomaly(c.get("ticker"), SEV_HIGH, reasons))
    for c in buckets.get("watch_reclaim") or []:
        score = c.get("alpha_score") or 0
        sev = SEV_MEDIUM if score >= 75 else SEV_LOW
        out.append(_anomaly(c.get("ticker"), sev,
                            _ticker_reasons(c, extra=["watch_reclaim"])))
    for tkr in buckets.get("missing_lens") or []:
        out.append(_anomaly(tkr, SEV_LOW, ["lens_missing"]))
    return [a for a in out if a is not None]


def _ticker_reasons(c: Dict[str, Any], *, extra: Sequence[str]) -> List[str]:
    reasons: List[str] = list(extra)
    for f in (c.get("alpha_flags") or []):
        s = str(f).lower()
        if "speculative_call_chase" in s:
            reasons.append("options:SPECULATIVE_CALL_CHASE")
        elif "bullish_but_late" in s:
            reasons.append("options:BULLISH_BUT_LATE")
    for f in (c.get("lens_flags") or []):
        s = str(f).lower()
        if "speculative_call_chase" in s:
            reasons.append("options:SPECULATIVE_CALL_CHASE")
        elif "bullish_but_late" in s:
            reasons.append("options:BULLISH_BUT_LATE")
    gk = (c.get("gatekeeper_status") or "").upper()
    if gk in {"BLOCK", "AVOID", "REJECT", "WATCH"}:
        reasons.append(f"gatekeeper:{gk}")
    score = c.get("alpha_score")
    if isinstance(score, (int, float)) and score >= 70 \
            and c.get("actionable_now") is False:
        reasons.append("alpha_high_but_not_actionable")
    # Dedupe while preserving order.
    seen: Set[str] = set()
    deduped: List[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    return deduped


def _anomaly(ticker: Any, severity: str, reasons: List[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(ticker, str):
        return None
    t = ticker.strip().upper()
    if not _TICKER_RE.match(t):
        return None
    return {"ticker": t, "severity": severity, "reasons": reasons}


def _select_drilldown_tickers(
    anomalies: List[Dict[str, Any]],
    *,
    extra: Sequence[str] = (),
    cap: int = MAX_DRILLDOWN_TICKERS,
) -> List[str]:
    """Return up to ``cap`` unique tickers, severity-ordered, with any
    explicit ``extra`` tickers prioritised."""
    extras_norm: List[str] = []
    for e in extra:
        if not isinstance(e, str):
            continue
        t = e.strip().upper()
        if _TICKER_RE.match(t) and t not in extras_norm:
            extras_norm.append(t)

    ranked = sorted(
        anomalies,
        key=lambda a: (_SEV_RANK.get(a["severity"], 99), a["ticker"]),
    )
    seen: Set[str] = set()
    picked: List[str] = []
    for t in extras_norm:
        if len(picked) >= cap:
            break
        if t not in seen:
            seen.add(t); picked.append(t)
    for a in ranked:
        if len(picked) >= cap:
            break
        if a["ticker"] not in seen:
            seen.add(a["ticker"]); picked.append(a["ticker"])
    return picked


# ── State classifier ─────────────────────────────────────────────────

def _classify_state(
    *,
    halted: bool,
    active_drift: bool,
    unknown_drift: bool,
    forecast_breached: bool,
    stale_warnings: List[str],
    contradiction_count: int,
) -> str:
    if halted:
        return STATE_BLOCKED
    if active_drift:
        return STATE_BLOCKED
    if unknown_drift:
        return STATE_STALE
    if stale_warnings:
        return STATE_STALE
    if forecast_breached:
        return STATE_FRAGILE
    if contradiction_count > 0:
        return STATE_CONFLICTED
    return STATE_NORMAL


def _recommended_action(
    drilldowns: List[Dict[str, Any]],
    state: str,
) -> str:
    """Pipe-joined verb phrase for the dashboard Action line + markdown
    Recommended-action row. Deterministic: same drilldown set → same
    string. Falls back to state-only wording when no tickers were
    drilled."""
    labels: Set[str] = set()
    for d in drilldowns or []:
        lbl = d.get("action_label")
        if isinstance(lbl, str) and lbl in ACTION_LABEL_TO_TEXT:
            labels.add(lbl)
    if not labels:
        return STATE_TO_ACTION_TEXT.get(state, "continue observation")
    parts = [ACTION_LABEL_TO_TEXT[l] for l in ACTION_ORDER if l in labels]
    return " · ".join(parts)


def _action_keyword(
    drilldowns: List[Dict[str, Any]],
    state: str,
) -> str:
    """Single short keyword (≤ 15 chars) for the Mode 2 Research
    one-liner. Picks the most-severe action_label present, else the
    state-derived keyword."""
    present: Set[str] = set()
    for d in drilldowns or []:
        lbl = d.get("action_label")
        if isinstance(lbl, str):
            present.add(lbl)
    for sev in ACTION_SEVERITY:
        if sev in present:
            return ACTION_LABEL_TO_KEYWORD[sev]
    return STATE_TO_ACTION_KEYWORD.get(state, "observe")


def _extract_state_inputs(
    daily: Dict[str, Any],
    system_health: Dict[str, Any],
) -> Dict[str, Any]:
    verdict = (system_health or {}).get("verdict") or {}
    halted        = bool(verdict.get("halted"))
    active_drift  = bool(verdict.get("active_drift"))
    unknown_drift = bool(verdict.get("unknown_drift"))

    forecast = (daily or {}).get("market_forecast") or {}
    headline = forecast.get("headline") if isinstance(forecast, dict) else None
    forecast_breached = False
    if isinstance(headline, dict):
        forecast_breached = bool(headline.get("invalidation_breached"))

    stale_warnings: List[str] = []
    dash = (daily or {}).get("dashboard_audit") or {}
    for warning in dash.get("warnings") or []:
        kind = warning.get("kind")
        if kind in {"stale_forecast", "stale_alpha_board", "forecast_data_freshness"}:
            stale_warnings.append(kind)
    return {
        "halted":            halted,
        "active_drift":      active_drift,
        "unknown_drift":     unknown_drift,
        "forecast_breached": forecast_breached,
        "stale_warnings":    stale_warnings,
    }


# ── Drilldown summarisers ────────────────────────────────────────────

def _drilldown_summary(audit: Dict[str, Any]) -> Dict[str, Any]:
    """Compress a ticker_consistency_audit bundle into a row for the
    summary. Quotes audit values verbatim — never paraphrases."""
    if (audit or {}).get("status") != "ok":
        return {
            "ticker":       (audit or {}).get("ticker"),
            "status":       (audit or {}).get("status"),
            "action_label": "Missing Data",
            "summary":      "audit could not run",
        }
    ticker = audit.get("ticker")
    cons = audit.get("consistency") or {}
    contradictions = cons.get("contradictions") or []
    stale = cons.get("stale_warnings") or []
    missing = cons.get("missing_artifacts") or []

    lens = audit.get("stock_lens") or {}
    gate = audit.get("executive_gatekeeper") or {}

    entry_state: Optional[str] = None
    options_quality: Optional[str] = None
    lens_label: Optional[str] = None
    if lens.get("status") == "ok":
        lens_label = lens.get("label")
        layers = lens.get("layers") or {}
        ev = layers.get("entry_validator") or {}
        entry_state = ev.get("view")
        opt = layers.get("options") or layers.get("options_pulse") or {}
        if isinstance(opt, dict):
            options_quality = opt.get("options_quality") or opt.get("quality")
    gatekeeper_status: Optional[str] = None
    if gate.get("status") == "ok":
        gatekeeper_status = gate.get("final_status")

    action_label = _action_label_from_signals(
        entry_state=entry_state,
        options_quality=options_quality,
        gatekeeper_status=gatekeeper_status,
        lens_status=lens.get("status"),
        cons_verdict=cons.get("verdict"),
        contradiction_count=len(contradictions),
    )
    return {
        "ticker":              ticker,
        "status":              "ok",
        "verdict":             cons.get("verdict"),
        "contradiction_count": len(contradictions),
        "contradictions":      contradictions,
        "stale_warnings":      stale,
        "missing_artifacts":   missing,
        "entry_state":         entry_state,
        "options_quality":     options_quality,
        "gatekeeper_status":   gatekeeper_status,
        "lens_label":          lens_label,
        "action_label":        action_label,
    }


def _action_label_from_signals(
    *,
    entry_state: Optional[str],
    options_quality: Optional[str],
    gatekeeper_status: Optional[str],
    lens_status: Optional[str],
    cons_verdict: Optional[str],
    contradiction_count: int,
) -> str:
    """Map audit signals → one of:
        Blocked | Avoid Chase | Watch Reclaim | Missing Data | Research Only

    Strictly deterministic. Same inputs → same label."""
    if lens_status not in (None, "ok"):
        return "Missing Data"
    gk = (gatekeeper_status or "").upper()
    if gk in {"BLOCK", "REJECT", "AVOID"}:
        return "Blocked"
    es = (entry_state or "").lower()
    oq = (options_quality or "").upper()
    if "too extended" in es \
            or oq in {"SPECULATIVE_CALL_CHASE", "BULLISH_BUT_LATE"}:
        return "Avoid Chase"
    if "broken" in es or "avoid" in es:
        return "Avoid Chase"
    if "watch" in es or "reclaim" in es:
        return "Watch Reclaim"
    if contradiction_count > 0 or (cons_verdict or "").lower() == "investigate":
        return "Research Only"
    return "Research Only"


# ── Concern collector ────────────────────────────────────────────────

def _collect_top_concerns(
    daily: Dict[str, Any],
    system_health: Dict[str, Any],
    late: Dict[str, Any],
    anomalies: List[Dict[str, Any]],
) -> List[str]:
    """Compose a deterministic concern list from audit fields. Caller
    decides how to render; this returns plain strings."""
    out: List[str] = []
    verdict = (system_health or {}).get("verdict") or {}
    if verdict.get("halted"):
        halt = (system_health or {}).get("halt_state") or {}
        out.append(f"system HALTED — reason: {halt.get('halt_reason') or 'n/a'}")
    if verdict.get("active_drift"):
        out.append("active reconciler drift — broker/book mismatch present")
    if verdict.get("unknown_drift"):
        out.append("reconciler drift state unknown — broker snapshot stale or missing")

    daily = daily or {}
    forecast = daily.get("market_forecast") or {}
    headline = forecast.get("headline") if isinstance(forecast, dict) else None
    if isinstance(headline, dict) and headline.get("invalidation_breached"):
        reasons = headline.get("invalidation_breach_reasons") or []
        if reasons:
            out.append("forecast invalidation breached: " + ", ".join(map(str, reasons)))
        else:
            out.append("forecast invalidation breached")

    dash = daily.get("dashboard_audit") or {}
    for w_item in dash.get("warnings") or []:
        kind = w_item.get("kind")
        if kind == "forecast_data_freshness":
            out.append(
                f"forecast data freshness: {w_item.get('data_freshness_status')} "
                f"({w_item.get('anchor_warning')})"
            )
        elif kind == "stale_forecast":
            out.append(f"stale forecast: age_hours={w_item.get('age_hours')}")
        elif kind == "stale_alpha_board":
            out.append(f"stale alpha board: age_hours={w_item.get('age_hours')}")
        elif kind == "research_delta_needs_action":
            for item in (w_item.get("items") or [])[:3]:
                out.append(f"research delta: {item}")
        elif kind == "strategy_favorability_flip":
            out.append(f"strategy favorability flips: {w_item.get('flips')}")

    hyg_outer = daily.get("paper_hygiene") or {}
    hyg = (hyg_outer.get("hygiene") or {}) if isinstance(hyg_outer, dict) else {}
    if not (hyg.get("summary") or {}).get("ready_to_gate_clean", True):
        out.append("hygiene clean gate is FALSE — clean-epoch not ready")
    for f in (hyg.get("findings") or []):
        if f.get("scope") == "clean" and f.get("severity") in ("ERROR", "WARN"):
            out.append(
                f"hygiene clean {f.get('severity')}: {f.get('code')} n={f.get('count')}"
            )

    # Bucket counts from late-chase audit.
    buckets = (late or {}).get("buckets") or {}
    extended_tk = [c.get("ticker") for c in (buckets.get("extended") or [])][:5]
    blocked_tk  = [c.get("ticker") for c in (buckets.get("blocked") or [])][:5]
    missing_tk  = list(buckets.get("missing_lens") or [])[:5]
    if extended_tk:
        out.append("late-chase / extended: " + ", ".join(map(str, extended_tk)))
    if blocked_tk:
        out.append("gatekeeper blocked: " + ", ".join(map(str, blocked_tk)))
    if missing_tk:
        out.append("stock lens missing: " + ", ".join(map(str, missing_tk)))

    # Options-chase names from the anomaly list.
    options_chase = sorted({
        a["ticker"] for a in anomalies
        if any(r in ("options:SPECULATIVE_CALL_CHASE",
                     "options:BULLISH_BUT_LATE")
               for r in a["reasons"])
    })
    if options_chase:
        out.append("options chase: " + ", ".join(options_chase))

    return out


# ── Executive summary (deterministic) ────────────────────────────────

def _executive_summary(
    *,
    state: str,
    daily: Dict[str, Any],
    system_health: Dict[str, Any],
    late: Dict[str, Any],
    drilldowns: List[Dict[str, Any]],
) -> str:
    """One-paragraph operator brief. Wording is rule-based so identical
    inputs produce identical output."""
    parts: List[str] = []

    # Market posture from forecast headline. When forecast invalidation is
    # breached we collapse the two-sentence form into the compact
    # "Headline posture <regime>, but invalidation breached → fragile."
    # line so the dashboard panel's first-sentence brief reads correctly.
    forecast = (daily or {}).get("market_forecast") or {}
    headline = forecast.get("headline") if isinstance(forecast, dict) else None
    if isinstance(headline, dict):
        regime = headline.get("current_regime") or headline.get("regime")
        bias5  = headline.get("bias_5d")
        conf   = headline.get("confidence")
        if headline.get("invalidation_breached"):
            parts.append(
                f"Headline posture {regime or 'unknown'}, but invalidation "
                f"breached → fragile."
            )
        else:
            parts.append(
                f"Market posture is {regime or 'unknown'} "
                f"(bias 5d={bias5 or 'n/a'}, confidence={conf or 'n/a'})."
            )
    else:
        parts.append("Market forecast artifact is unavailable.")

    # Paper state.
    hyg_outer = (daily or {}).get("paper_hygiene") or {}
    hyg = (hyg_outer.get("hygiene") or {}) if isinstance(hyg_outer, dict) else {}
    summary = hyg.get("summary") or {}
    if summary.get("ready_to_gate_clean"):
        parts.append("Paper state clean-epoch is READY (no active warns/errors).")
    else:
        parts.append("Paper state clean-epoch is NOT READY — review hygiene findings before relying on telemetry.")

    # Long-side chase risk.
    buckets = (late or {}).get("buckets") or {}
    n_extended = len(buckets.get("extended") or [])
    n_blocked  = len(buckets.get("blocked") or [])
    options_chase_n = sum(
        1 for d in drilldowns
        if (d.get("options_quality") or "") in {"SPECULATIVE_CALL_CHASE", "BULLISH_BUT_LATE"}
    )
    if n_extended or options_chase_n:
        parts.append(
            f"Long side is chase-prone: {n_extended} extended name(s), "
            f"{n_blocked} gatekeeper block(s), {options_chase_n} options-quality chase flag(s)."
        )
    else:
        parts.append("Long side does not look chase-prone in the current late-chase board.")

    # Operator next step — strictly state-driven.
    verdict = (system_health or {}).get("verdict") or {}
    if verdict.get("halted") or verdict.get("active_drift"):
        next_step = "investigate the halt or active drift before any further audit decisions"
    elif state == STATE_STALE:
        next_step = "refresh stale artifacts before relying on this report"
    elif state == STATE_FRAGILE:
        next_step = "treat forecast as fragile and prefer waiting over chasing"
    elif state == STATE_CONFLICTED:
        next_step = "research the flagged tickers individually; avoid acting on aggregate labels"
    else:
        next_step = "continue observation; no operator action required by this audit"
    parts.append(f"Recommended operator action: {next_step}.")

    return " ".join(parts)


# ── Markdown rendering ───────────────────────────────────────────────

def _render_markdown(payload: Dict[str, Any]) -> str:
    state = payload["state"]
    verdict = (payload.get("system_health_audit") or {}).get("verdict") or {}
    daily = payload.get("daily_dashboard_audit") or {}
    forecast = daily.get("market_forecast") or {}
    headline = forecast.get("headline") if isinstance(forecast, dict) else None

    hyg_outer = daily.get("paper_hygiene") or {}
    hyg = (hyg_outer.get("hygiene") or {}) if isinstance(hyg_outer, dict) else {}
    summary = hyg.get("summary") or {}

    lines: List[str] = []
    lines.append(f"# MCP Audit Daily Summary — {payload['generated_at']}")
    lines.append("")
    lines.append("## Header")
    lines.append("")
    lines.append(f"- session: `{payload.get('session', 'regular')}`")
    lines.append(f"- generated_at: `{payload['generated_at']}`")
    lines.append(f"- state: `{state}`")
    lines.append(f"- clean_epoch_ready: `{summary.get('ready_to_gate_clean')}`")
    lines.append(f"- ready_to_gate_all: `{summary.get('ready_to_gate_all')}`")
    lines.append(f"- halted: `{verdict.get('halted')}`")
    lines.append(f"- active_drift: `{verdict.get('active_drift')}`  unknown_drift: `{verdict.get('unknown_drift')}`")
    if isinstance(headline, dict):
        regime = headline.get("current_regime") or headline.get("regime")
        lines.append(
            f"- market_regime: `{regime}`  bias_5d: `{headline.get('bias_5d')}`  "
            f"confidence: `{headline.get('confidence')}`  "
            f"invalidation_breached: `{headline.get('invalidation_breached')}`"
        )
    else:
        lines.append("- market_regime: `unavailable`")
    rec = payload.get("recommended_action")
    if rec:
        lines.append(f"- recommended_action: `{rec}`")

    lines.append("")
    lines.append("## Executive research summary")
    lines.append("")
    lines.append(payload["executive_summary"])

    lines.append("")
    lines.append("## Top concerns")
    lines.append("")
    concerns = payload.get("top_concerns") or []
    if not concerns:
        lines.append("- none")
    else:
        for c in concerns:
            lines.append(f"- {c}")

    lines.append("")
    lines.append("## Ticker drilldowns")
    lines.append("")
    drilldowns = payload.get("ticker_drilldowns") or []
    if not drilldowns:
        lines.append("_no anomalies triggered automatic drilldown_")
    else:
        lines.append("| ticker | status | contradictions | entry | options | gatekeeper | action |")
        lines.append("|---|---|---|---|---|---|---|")
        for d in drilldowns:
            lines.append(
                f"| {d.get('ticker') or '?'} "
                f"| {d.get('status', '?')} "
                f"| {d.get('contradiction_count', 0)} "
                f"| {d.get('entry_state') or '—'} "
                f"| {d.get('options_quality') or '—'} "
                f"| {d.get('gatekeeper_status') or '—'} "
                f"| {d.get('action_label') or '—'} |"
            )

    g3 = payload.get("phase_1g3") or {}
    if g3:
        lines.append("")
        lines.append("## Phase 1G.3 — evidence hygiene")
        lines.append("")
        lines.append(f"- SHORT_A: {g3.get('short_a_status')}")
        sr = g3.get("short_radar")
        if sr:
            lines.append(f"- SHORT RADAR: {sr.get('state')} (score {sr.get('score')}/100)")
        sdet = g3.get("short_detection")
        if sdet:
            lines.append(
                f"- SHORT DETECTION: state={sdet.get('proposed_state')} "
                f"qqq_drawdown={sdet.get('qqq_drawdown_10d_pct')} "
                f"missed_breakdown={'yes' if sdet.get('missed_breakdown') else 'no'} "
                f"verdict={sdet.get('forward_verdict')}"
            )
        fr = g3.get("forward_resolution")
        if fr:
            lines.append(
                f"- FORWARD RESOLUTION: {fr.get('status')} "
                f"(forecast {fr.get('forecast_matured')}/{fr.get('forecast_open')} matured, "
                f"next due {fr.get('next_maturity_due')})"
            )
        lr = g3.get("leader_reset_event_study")
        if lr:
            lines.append(
                f"- LEADER_RESET event study: {lr.get('verdict')} "
                f"(RESEARCH_READY n={lr.get('research_ready_n')})"
            )
        vc = g3.get("voyager_conversion")
        if vc:
            lines.append(
                f"- VOYAGER conversion: {vc.get('approval_to_signal_conversion')} "
                f"({vc.get('signals')} signals / {vc.get('approvals')} approvals)"
            )

    tour = payload.get("strategy_tournament")
    if tour:
        lines.append("")
        lines.append("## Phase 1G.4 — strategy tournament")
        lines.append("")
        lines.append(f"- STRATEGY TOURNAMENT: best={tour.get('best_candidate')} "
                     f"verdict={tour.get('verdict')}")
        lines.append(f"  - short_side={tour.get('short_side')} "
                     f"options_expression={tour.get('options_expression')}")
        if tour.get("no_trade_recommendation"):
            lines.append(f"  - {tour.get('no_trade_recommendation')}")

    slab = payload.get("strategy_lab")
    if slab:
        lines.append("")
        lines.append("## Phase 1H - strategy research lab")
        lines.append("")
        lines.append(f"- STRATEGY LAB: mode={slab.get('mode')} sampled={slab.get('sampled')} best={slab.get('best_variant')} verdict={slab.get('verdict')} trades={slab.get('trades')} rel_spy={slab.get('rel_spy')} walk_forward={slab.get('walk_forward')} paper_ready={'yes' if slab.get('paper_ready') else 'no'} status={slab.get('status')} paper_active={slab.get('paper_active')}")

    rtt = payload.get("rs_theme_triage")
    if rtt:
        lines.append("")
        lines.append("## Phase 1G.9 — RS/theme triage")
        lines.append("")
        lines.append(
            f"- RS/THEME TRIAGE: evaluated={rtt.get('evaluated')} "
            f"research_watch={rtt.get('research_watch')} needs_lens={rtt.get('needs_lens')} "
            f"too_extended={rtt.get('too_extended')} blocked={rtt.get('blocked')} "
            f"verdict={rtt.get('verdict')}")

    rtf = payload.get("rs_theme_forward")
    if rtf:
        lines.append("")
        lines.append("## Phase 1G.11 — RS/theme forward validation")
        lines.append("")
        lines.append(
            f"- RS/THEME FORWARD: cohort={rtf.get('cohort')} matured={rtf.get('matured')} "
            f"lens_ready_5d={rtf.get('lens_ready_5d_rel_spy')} "
            f"vs_alpha={rtf.get('vs_alpha')} vs_random={rtf.get('vs_random')} "
            f"verdict={rtf.get('verdict')}")

    gkp = payload.get("gatekeeper_precision")
    if gkp:
        lines.append("")
        lines.append("## Phase 1G.13 — Gatekeeper precision (blocked-winner autopsy)")
        lines.append("")
        lines.append(
            f"- GATEKEEPER PRECISION: blocked_5d={gkp.get('blocked_5d')} "
            f"watch_5d={gkp.get('watch_5d')} n_blocked={gkp.get('n_blocked')} "
            f"matured={gkp.get('n_matured')} verdict={gkp.get('verdict')} "
            f"rec={gkp.get('recommendation')}")

    ptx = payload.get("power_trend_extension")
    if ptx:
        lines.append("")
        lines.append("## Phase 1G.14 — power-trend extension (too-extended audit)")
        lines.append("")
        lines.append(
            f"- POWER TREND STUDY: too_extended_5d={ptx.get('too_extended_5d')} "
            f"power_trend_5d={ptx.get('power_trend_5d')} climax_5d={ptx.get('climax_5d')} "
            f"n={ptx.get('n_cohort')} verdict={ptx.get('verdict')} rec={ptx.get('recommendation')}")

    rcs = payload.get("recall_shadow")
    if rcs:
        lines.append("")
        lines.append("## Path B — Recall-Repair Shadow Lane (research-only)")
        lines.append("")
        lines.append(
            f"- RECALL SHADOW: recall={rcs.get('shadow_recall_20pct')}% "
            f"(funnel≈{rcs.get('funnel_recall_benchmark')}%) "
            f"shadow_5d={rcs.get('shadow_rel_spy_5d')} vs_random={rcs.get('random_rel_spy_5d')} "
            f"history_days={rcs.get('history_days')} verdict={rcs.get('verdict')}")

    prt = payload.get("participation")
    if prt:
        lines.append("")
        lines.append("## Phase 1G.17 — participation bottleneck (diagnostic)")
        lines.append("")
        lines.append(
            f"- PARTICIPATION: state={prt.get('state')} "
            f"last_decision={prt.get('last_decision')} "
            f"sniper_flow={prt.get('sniper_flow')} "
            f"voyager_flow={prt.get('voyager_flow')} "
            f"council={prt.get('council')} reason={prt.get('reason')}")

    sa = payload.get("social_attention")
    if sa:
        lines.append("")
        lines.append("## Phase 1G.15 — Social Attention Radar (research-only)")
        lines.append("")
        lines.append(
            f"- SOCIAL ATTENTION: leads={sa.get('leads')} social_led={sa.get('social_led')} "
            f"stealth={sa.get('stealth')} early={sa.get('early')} crowded={sa.get('crowded')} "
            f"forward_verdict={sa.get('forward_verdict')}")

    org = payload.get("options_regime")
    if org:
        lines.append("")
        lines.append("## Phase 1G.12 — options regime (diagnostic only)")
        lines.append("")
        if not org.get("feed_configured"):
            lines.append("- OPTIONS REGIME: feed not configured — NOT_ENOUGH_DATA")
        else:
            lines.append(
                f"- OPTIONS REGIME: {org.get('market_regime')} "
                f"(anchor {org.get('anchor')}) risk={org.get('risk_warning_level')}")
            lines.append(
                f"  - skew={org.get('skew_state')}  iv={org.get('iv_state')}  "
                f"gamma={org.get('gamma_regime')}  term={org.get('term_structure_state')}")
            for s in (org.get("per_symbol") or []):
                lines.append(
                    f"  - {s.get('symbol')}: regime={s.get('regime')} "
                    f"skew={s.get('skew')} iv={s.get('iv')} gamma={s.get('gamma')}")
        lines.append("  - NOTE: GEX is a proxy; diagnostic only — not a veto or approval.")

    odc = payload.get("options_data_collection")
    if odc:
        lines.append("")
        lines.append("## Phase 1J.2 — options data collection (DATA_COLLECTION_ONLY)")
        lines.append("")
        lines.append(
            f"- OPTIONS DATA: status={'COLLECTING' if odc.get('status') == 'OK' else odc.get('status')} "
            f"last_snapshot={odc.get('last_snapshot')} symbols={odc.get('symbols_latest')} "
            f"contracts={odc.get('contracts_latest')} days_collected={odc.get('days_collected')} "
            f"IVR_partial_eta={odc.get('ivr_partial_eta')} "
            f"strategy_status={odc.get('strategy_status')}")

    lines.append("")
    lines.append("## No-trade disclaimer")
    lines.append("")
    lines.append(NO_TRADE_DISCLAIMER)
    lines.append("")
    return "\n".join(lines)


# ── Writers ──────────────────────────────────────────────────────────

def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


# ── Orchestrator ─────────────────────────────────────────────────────

def run_session_audit(
    *,
    session: str = "regular",
    extra_tickers: Sequence[str] = (),
    drilldown_cap: int = MAX_DRILLDOWN_TICKERS,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run the daily / system / late-chase trio, derive anomalies,
    drill into the top-N tickers, and return the structured analysis
    payload. Does not write files; caller decides via write_artifacts."""
    daily         = w.daily_dashboard_audit()
    system_health = w.system_health_audit()
    late          = w.late_chase_audit()

    anomalies = _collect_late_chase_anomalies(late)
    state_inputs = _extract_state_inputs(daily, system_health)
    state = _classify_state(
        halted=state_inputs["halted"],
        active_drift=state_inputs["active_drift"],
        unknown_drift=state_inputs["unknown_drift"],
        forecast_breached=state_inputs["forecast_breached"],
        stale_warnings=state_inputs["stale_warnings"],
        contradiction_count=len(anomalies),
    )

    tickers = _select_drilldown_tickers(
        anomalies, extra=extra_tickers, cap=drilldown_cap,
    )
    drilldowns: List[Dict[str, Any]] = []
    for tk in tickers:
        bundle = w.ticker_consistency_audit(tk)
        drilldowns.append(_drilldown_summary(bundle))

    top_concerns = _collect_top_concerns(daily, system_health, late, anomalies)

    # Counts surfaced separately so the dashboard panel and tests don't
    # need to walk the full payload.
    buckets = ((late or {}).get("buckets") or {})
    counts = {
        "candidates":     ((late or {}).get("counts") or {}).get("candidates", 0),
        "extended":       len(buckets.get("extended") or []),
        "watch_reclaim":  len(buckets.get("watch_reclaim") or []),
        "blocked":        len(buckets.get("blocked") or []),
        "broken":         len(buckets.get("broken") or []),
        "missing_lens":   len(buckets.get("missing_lens") or []),
        "drilldowns":     len(drilldowns),
        "anomalies":      len(anomalies),
    }

    payload: Dict[str, Any] = {
        "schema":            "mcp_audit_session_v1",
        "generated_at":      (now or datetime.now(timezone.utc)).isoformat(),
        "session":           session,
        "state":             state,
        "state_inputs":      state_inputs,
        "counts":            counts,
        "anomalies":         anomalies,
        "ticker_drilldowns": drilldowns,
        "top_concerns":      top_concerns,
        "executive_summary": _executive_summary(
            state=state, daily=daily, system_health=system_health,
            late=late, drilldowns=drilldowns,
        ),
        # Phase 2B.1 follow-ups — compact action surfaces for the
        # dashboard Risk panel (recommended_action) and the Mode 2
        # Research one-liner (action_keyword). Deterministic from
        # ticker_drilldowns + state; no LLM.
        "recommended_action": _recommended_action(drilldowns, state),
        "action_keyword":     _action_keyword(drilldowns, state),
        "no_trade_disclaimer": NO_TRADE_DISCLAIMER,
        # Carry the underlying audit responses verbatim so downstream
        # tooling can read the originals if needed. These are the same
        # dicts the MCP server would return.
        "daily_dashboard_audit": daily,
        "system_health_audit":   system_health,
        "late_chase_audit":      late,
        # Phase 1G.2 — diagnostic surfaces. Cache-only reads of sidecars
        # written by main.py (signal_hygiene) and research/* scripts
        # (legacy_decision_policy). None of these mutate state.
        "signal_hygiene":          _read_signal_hygiene_counters(),
        "legacy_decision_policy":  _read_legacy_decision_policy(),
        "snapshot_maturation":     _read_snapshot_maturation(),
        # Phase 1G.3 — evidence-hygiene surfaces (SHORT_A freeze, short radar,
        # forward resolver health, LEADER_RESET event study, VOYAGER conversion).
        "phase_1g3":               _read_phase_1g3_surfaces(),
        # Phase 1G.4 — Strategy Tournament best-candidate / verdict (cache-only).
        "strategy_tournament":     _read_strategy_tournament(),
        # Phase 1H — Strategy Research Lab summary (cache-only).
        "strategy_lab":            _read_strategy_lab(),
        # Phase 1G.9 — RS/Theme → Lens/Gatekeeper triage summary (cache-only).
        "rs_theme_triage":         _read_rs_theme_triage(),
        # Phase 1G.11 — RS/Theme forward-validation summary (cache-only).
        "rs_theme_forward":        _read_rs_theme_forward(),
        # Phase 1G.13 — Gatekeeper precision audit / blocked-winner autopsy (cache-only).
        "gatekeeper_precision":    _read_gatekeeper_precision(),
        # Phase 1G.14 — power-trend extension study / too-extended block audit (cache-only).
        "power_trend_extension":   _read_power_trend_extension(),
        "recall_shadow":           _read_recall_shadow(),
        "participation":           _read_participation(),
        # Phase 1G.15 — Social Attention Radar early-crowd summary (cache-only,
        # research-only — never a signal / veto / approval).
        "social_attention":        _read_social_attention(),
        # Phase 1G.12 — Options Regime Lens market rollup (cache-only, diagnostic
        # only — never a veto / approval).
        "options_regime":          _read_options_regime(),
        # Phase 1J.2 — options chain snapshot collection health (cache-only,
        # DATA_COLLECTION_ONLY — no strategy, no signals).
        "options_data_collection": _read_options_snapshot_health(),
    }
    return payload


def write_artifacts(payload: Dict[str, Any]) -> Dict[str, Path]:
    """Atomically write the JSON sidecar and both markdown files."""
    ts = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00"))
    md = _render_markdown(payload)

    json_path     = _json_path()
    md_latest     = _md_latest_path()
    md_timestamp  = _md_timestamped_path(ts)

    _atomic_write_json(json_path,    payload)
    _atomic_write_text(md_latest,    md)
    _atomic_write_text(md_timestamp, md)

    return {
        "json":           json_path,
        "md_latest":      md_latest,
        "md_timestamped": md_timestamp,
    }


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2B.1 MCP audit session orchestrator (cache-only).",
    )
    parser.add_argument(
        "--session", default="regular",
        choices=["open", "regular", "close"],
        help="Session label written into the artifacts.",
    )
    parser.add_argument(
        "--ticker", action="append", default=[],
        help="Force-include a ticker in the drilldown set (may repeat).",
    )
    parser.add_argument(
        "--max-tickers", type=int, default=MAX_DRILLDOWN_TICKERS,
        help=f"Cap on auto-selected drilldown tickers (default {MAX_DRILLDOWN_TICKERS}).",
    )
    parser.add_argument(
        "--print", action="store_true",
        help="Also print the markdown summary to stdout.",
    )
    args = parser.parse_args(argv)

    payload = run_session_audit(
        session=args.session,
        extra_tickers=args.ticker,
        drilldown_cap=args.max_tickers,
    )
    paths = write_artifacts(payload)
    print(f"wrote {paths['json']}")
    print(f"wrote {paths['md_latest']}")
    print(f"wrote {paths['md_timestamped']}")
    if args.print:
        print()
        print(paths["md_latest"].read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
