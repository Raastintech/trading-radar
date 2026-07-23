"""Research Command Center — read-only data adapter.

Normalizes the research engine's JSON sidecars into dashboard models:
status strip, heatmap leaderboard rows, and per-ticker detail.

Doctrine:
  - RESEARCH_ONLY.  No signals, no execution, no order language.
  - CACHE-ONLY.  Reads artifacts written by the research cycle; never calls
    providers, never invokes scanners, never writes anything anywhere.
  - CRED-FREE.  No dependency on the env-validated config module, so it
    runs without any env vars.
  - HONEST.  Missing/immature/suspect data is surfaced as explicit fallback
    states, never hidden or interpolated.

Fallback states (per row `warnings` and board-level `fallback`):
  NO_DATA, MISSING_ARTIFACT, IMMATURE_FORWARD_EVIDENCE,
  INSUFFICIENT_HISTORY, STALE_PRICE, SUSPECT_FEED, BENCHMARK_MISSING
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dashboards.research_command_center.presentation import (
    decorate_scan_payload)

ROOT = Path(__file__).resolve().parents[2]

CURRENT_FOR_LATEST_SCAN = "CURRENT_FOR_LATEST_SCAN"
STALE_FOR_LATEST_SCAN = "STALE_FOR_LATEST_SCAN"
SOURCE_UNKNOWN = "SOURCE_UNKNOWN"


def _parse_dep_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _scan_hash(scanner: Dict[str, Any], routed: Dict[str, Any]) -> Optional[str]:
    integ = scanner.get("scan_integrity") or {}
    return (scanner.get("scan_universe_manifest_hash")
            or integ.get("manifest_hash")
            or routed.get("scanner_manifest_hash")
            or routed.get("manifest_universe_hash"))


def _dependency_audit(artifact_kind: str, artifact_timestamp: Optional[str],
                      scanner_doc: Optional[Dict[str, Any]],
                      routed_doc: Optional[Dict[str, Any]],
                      recorded: Optional[Dict[str, Any]] = None
                      ) -> Dict[str, Any]:
    scanner = scanner_doc or {}
    routed = routed_doc or {}
    previous = recorded or {}
    source_scan_ts = scanner.get("generated_at") or routed.get("scan_timestamp")
    source_routing_ts = routed.get("generated_at")
    source_scan_hash = _scan_hash(scanner, routed)
    source_routing_hash = (routed.get("scanner_manifest_hash")
                           or routed.get("manifest_universe_hash"))

    artifact_dt = _parse_dep_ts(artifact_timestamp)
    scan_dt = _parse_dep_ts(source_scan_ts)
    routing_dt = _parse_dep_ts(source_routing_ts)
    stale_reasons: List[str] = []
    if artifact_dt and scan_dt and artifact_dt < scan_dt:
        stale_reasons.append(
            f"artifact {artifact_timestamp} older than source scan {source_scan_ts}")
    if artifact_dt and routing_dt and artifact_dt < routing_dt:
        stale_reasons.append(
            f"artifact {artifact_timestamp} older than program routing {source_routing_ts}")
    if previous.get("source_scan_timestamp") and source_scan_ts \
            and previous.get("source_scan_timestamp") != source_scan_ts:
        stale_reasons.append("recorded source scan differs from latest scan")
    if previous.get("source_program_routing_timestamp") and source_routing_ts \
            and previous.get("source_program_routing_timestamp") != source_routing_ts:
        stale_reasons.append("recorded program routing differs from latest routing")
    if previous.get("source_scan_hash") and source_scan_hash \
            and previous.get("source_scan_hash") != source_scan_hash:
        stale_reasons.append("recorded source scan hash differs from latest hash")

    if stale_reasons:
        status = STALE_FOR_LATEST_SCAN
    elif artifact_dt and (scan_dt or routing_dt):
        status = CURRENT_FOR_LATEST_SCAN
    else:
        status = SOURCE_UNKNOWN
    return {
        "artifact_kind": artifact_kind,
        "artifact_timestamp": artifact_timestamp,
        "source_scan_timestamp": source_scan_ts,
        "source_scan_hash": source_scan_hash,
        "source_program_routing_timestamp": source_routing_ts,
        "source_program_routing_hash": source_routing_hash,
        "status": status,
        "stale_for_latest_scan": status == STALE_FOR_LATEST_SCAN,
        "stale_reasons": stale_reasons,
    }


RESEARCH_ONLY_FOOTER = "Research only — not a signal or recommendation."

# Fallback / data-quality states
NO_DATA = "NO_DATA"
MISSING_ARTIFACT = "MISSING_ARTIFACT"
IMMATURE_FORWARD_EVIDENCE = "IMMATURE_FORWARD_EVIDENCE"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
STALE_PRICE = "STALE_PRICE"
SUSPECT_FEED = "SUSPECT_FEED"
BENCHMARK_MISSING = "BENCHMARK_MISSING"

# A price older than this (calendar days) is flagged STALE_PRICE.  Matches the
# scanner's own --max-stale-days default; display-only, changes no behavior.
STALE_PRICE_DAYS = 7

# Scanner sidecar older than this (hours) renders the freshness chip STALE.
FRESHNESS_STALE_HOURS = 18.0


@dataclass
class ArtifactStore:
    """Resolved paths to every artifact the dashboard reads.  Injectable root
    so tests point at a tmp dir and can simulate missing artifacts."""

    root: Path = field(default_factory=lambda: ROOT)

    @property
    def research_dir(self) -> Path:
        return self.root / "cache" / "research"

    @property
    def scanner_json(self) -> Path:
        return self.research_dir / "research_scanner_latest.json"

    @property
    def forward_json(self) -> Path:
        return self.research_dir / "research_forward_latest.json"

    @property
    def radar_json(self) -> Path:
        return self.research_dir / "daily_alpha_radar_latest.json"

    @property
    def summary_json(self) -> Path:
        return self.research_dir / "nightly_operator_summary_latest.json"

    @property
    def universe_json(self) -> Path:
        return self.research_dir / "research_universe_build_latest.json"

    @property
    def history_jsonl(self) -> Path:
        return self.root / "data" / "research" / "research_watchlist_history.jsonl"

    @property
    def moment_of_truth_json(self) -> Path:
        return self.research_dir / "moment_of_truth_2026_06_27.json"

    @property
    def prices_dir(self) -> Path:
        return self.root / "cache" / "prices"

    @property
    def prices_deep_dir(self) -> Path:
        return self.root / "cache" / "prices_deep"

    @property
    def fundamentals_dir(self) -> Path:
        return self.root / "cache" / "fundamentals"

    @property
    def journal_jsonl(self) -> Path:
        return self.root / "data" / "research" / "journal.jsonl"

    @property
    def journal_audit_json(self) -> Path:
        return self.research_dir / "journal_audit_latest.json"

    @property
    def research_programs_json(self) -> Path:
        return self.research_dir / "research_program_validation_latest.json"

    @property
    def quarantine_report_json(self) -> Path:
        return self.research_dir / "quarantine_cause_report_latest.json"

    @property
    def latest_scan_programs_json(self) -> Path:
        return self.research_dir / "latest_scan_programs_latest.json"

    @property
    def high_conviction_json(self) -> Path:
        return self.research_dir / "high_conviction_alpha_latest.json"

    @property
    def high_conviction_forward_json(self) -> Path:
        return self.research_dir / "high_conviction_forward_latest.json"

    @property
    def forward_milestones_json(self) -> Path:
        return self.research_dir / "forward_evidence_milestones_latest.json"

    @property
    def high_conviction_history_jsonl(self) -> Path:
        return self.root / "data" / "research" / "high_conviction_history.jsonl"

    @property
    def scanner_recall_diagnostics_json(self) -> Path:
        return self.research_dir / "scanner_recall_diagnostics_latest.json"

    @property
    def emerging_outlier_json(self) -> Path:
        return self.research_dir / "emerging_outlier_watch_latest.json"

    @property
    def emerging_outlier_forward_json(self) -> Path:
        return self.research_dir / "emerging_outlier_forward_latest.json"

    @property
    def filter_audit_json(self) -> Path:
        return self.research_dir / "high_conviction_filter_audit_latest.json"

    # ── market-context + system-trust artifacts (same canonical files the
    #    TUI reads; the dashboard never invokes providers or the orchestrator)
    @property
    def market_heartbeat_json(self) -> Path:
        return self.research_dir / "market_heartbeat_latest.json"

    @property
    def regime_forecast_json(self) -> Path:
        return self.research_dir / "regime_forecast_latest.json"

    @property
    def forward_resolution_json(self) -> Path:
        return self.research_dir / "forward_resolution_health_latest.json"

    @property
    def provider_health_json(self) -> Path:
        return self.research_dir / "fmp_provider_health_latest.json"

    @property
    def options_coverage_json(self) -> Path:
        return self.research_dir / "options_coverage_report_latest.json"

    @property
    def mcp_analysis_json(self) -> Path:
        return self.research_dir / "mcp_analysis_latest.json"

    @property
    def price_refresh_json(self) -> Path:
        return self.research_dir / "universe_price_refresh_latest.json"

    @property
    def discovery_coverage_json(self) -> Path:
        return self.research_dir / "discovery_coverage_latest.json"

    @property
    def alpha_board_json(self) -> Path:
        return self.research_dir / "alpha_discovery_board_latest.json"

    @property
    def scan_manifest_json(self) -> Path:
        return self.research_dir / "scan_universe_manifest_latest.json"

    @property
    def scan_exclusion_impact_json(self) -> Path:
        return self.research_dir / "scan_exclusion_impact_latest.json"

    @property
    def legacy_universe_snapshot_json(self) -> Path:
        return self.root / "cache" / "universe" / "universe_snapshot_latest.json"


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _downstream_dependency_audit(artifact_kind: str,
                                 payload: Dict[str, Any],
                                 store: "ArtifactStore") -> Dict[str, Any]:
    return _dependency_audit(
        artifact_kind=artifact_kind,
        artifact_timestamp=payload.get("generated_at")
        or payload.get("artifact_timestamp"),
        scanner_doc=_load_json(store.scanner_json),
        routed_doc=_load_json(store.latest_scan_programs_json),
        recorded=payload.get("source_dependencies") or {},
    )


def _age_hours(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    except Exception:
        return None


def _days_since(date_s: Optional[str]) -> Optional[int]:
    if not date_s:
        return None
    try:
        d = datetime.strptime(str(date_s)[:10], "%Y-%m-%d").date()
        return (datetime.now(timezone.utc).date() - d).days
    except Exception:
        return None


# ── Status strip ─────────────────────────────────────────────────────────────


def build_journal_audit(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Latest journal-audit verdict for the dashboard.  Cache-only read of
    the sidecar written by research/journal_audit_reviewer.py — the
    dashboard never invokes the auditor or any LLM.  ``promote_to_signal``
    is re-forced false at display time as a belt-and-suspenders guard."""
    store = store or ArtifactStore()
    audit = _load_json(store.journal_audit_json)
    if audit is None:
        return {"present": False, "promote_to_signal": False}
    flaws = audit.get("flaws_detected") or []
    return {
        "present": True,
        "generated_at": audit.get("generated_at"),
        "age_hours": _age_hours(audit.get("generated_at")),
        "audit_source": audit.get("audit_source"),
        "research_verdict": audit.get("research_verdict"),
        "alpha_discovery_quality": audit.get("alpha_discovery_quality"),
        "engine_health": audit.get("engine_health"),
        "promote_to_signal": False,
        "one_line_summary": audit.get("one_line_summary"),
        "flaw_count": len(flaws) if isinstance(flaws, list) else 0,
        "top_flaws": [
            {"severity": f.get("severity"), "area": f.get("area"),
             "issue": f.get("issue")}
            for f in flaws[:3] if isinstance(f, dict)
        ],
        "recommended_tasks": (audit.get("recommended_claude_code_tasks")
                              or [])[:5],
    }


def build_research_programs(
        store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Phase 5 program routing/validation for the dashboard.  Cache-only
    read of the sidecar written by research/research_programs.py — the
    dashboard never runs the validation itself.  Exposes, per program:
    name, expected holding period, evidence maturity, and the
    pre-registered verdict, plus per-label routing so a candidate's
    program and holding period are always displayable."""
    store = store or ArtifactStore()
    report = _load_json(store.research_programs_json)
    if report is None:
        return {"present": False, "research_only": True}
    programs = {}
    label_routing = {}
    for pid, p in (report.get("programs") or {}).items():
        cov = p.get("horizon_coverage") or {}
        matured = sorted(int(h) for h in (p.get("horizons") or {}))
        programs[pid] = {
            "name": p.get("name"),
            "expected_holding_period_td":
                p.get("expected_holding_period_td"),
            "verdict": p.get("verdict"),
            "verdict_reason": p.get("verdict_reason"),
            "n_episodes": p.get("n_episodes"),
            "matured_horizons_td": matured,
            "primary_not_collected_td":
                cov.get("primary_not_collected") or [],
            "diagnostic_signal": p.get("diagnostic_signal"),
        }
        for label, lb in (p.get("label_verdicts") or {}).items():
            label_routing[label] = {
                "program": pid,
                "expected_holding_period_td":
                    lb.get("expected_holding_period_td"),
                "verdict": lb.get("verdict"),
                "diagnostic_signal": lb.get("diagnostic_signal"),
                "lifecycle_stage": lb.get("lifecycle_stage"),
            }
    return {
        "present": True,
        "generated_at": report.get("generated_at"),
        "age_hours": _age_hours(report.get("generated_at")),
        "research_only": True,
        "deduped_episodes": report.get("deduped_episodes"),
        "duplication_factor": report.get("duplication_factor"),
        "programs": programs,
        "label_routing": label_routing,
    }


def build_latest_research_scan(
        store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Latest-scan candidates grouped by research program.  Cache-only
    read of the sidecar written by research/latest_scan_programs.py —
    strictly the most recent production scan, never historical
    aggregates.  research_only is re-forced true per candidate at
    display time."""
    store = store or ArtifactStore()
    payload = _load_json(store.latest_scan_programs_json)
    if payload is None or not payload.get("present"):
        return {"present": False, "research_only": True}
    for block in (payload.get("programs") or {}).values():
        for c in block.get("candidates") or []:
            c["research_only"] = True
    payload["age_hours"] = _age_hours(payload.get("generated_at"))
    # Cosmetic display metadata (badges, compact evidence label, top-N
    # split, data-issue separation) — additive, in-memory only; canonical
    # fields and candidate order are untouched.
    return decorate_scan_payload(payload)


def build_high_conviction(
        store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """High-Conviction Alpha Shortlist for the dashboard.  Cache-only read
    of the sidecar written by research/high_conviction_alpha.py — the
    dashboard never runs the qualification itself.  ``research_only`` is
    re-forced true on every record; membership is never validated alpha."""
    store = store or ArtifactStore()
    payload = _load_json(store.high_conviction_json)
    if payload is None or not payload.get("present"):
        return {"present": False, "research_only": True,
                "shortlist_status": (payload or {}).get(
                    "shortlist_status") or "NO_HIGH_CONVICTION_CANDIDATES"}
    for key in ("shortlist", "quality_but_extended", "improving_but_unproven",
                "rejected"):
        for c in payload.get(key) or []:
            c["research_only"] = True
    # attach the separate forward-validation verdict for context (display
    # only — it is a distinct hypothesis, never merged with the shortlist)
    fwd = _load_json(store.high_conviction_forward_json) or {}
    payload["forward_validation"] = {
        "present": bool(fwd.get("present")),
        "verdict": fwd.get("verdict"),
        "verdict_reason": fwd.get("verdict_reason"),
        "n_history_rows": fwd.get("n_history_rows"),
    }
    deps = _downstream_dependency_audit(
        "high_conviction_alpha", payload, store)
    payload["source_dependencies"] = deps
    payload["cadence_status"] = deps["status"]
    payload["stale_for_latest_scan"] = deps["stale_for_latest_scan"]
    payload["cadence_warning"] = (
        "High-Conviction list stale vs latest scan"
        if deps["stale_for_latest_scan"] else None)
    payload["age_hours"] = _age_hours(payload.get("generated_at"))
    return payload


def build_emerging_outlier(
        store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Emerging Outlier Watch for the dashboard.  Cache-only read of the
    sidecar written by research/emerging_outlier_watch.py — a SEPARATE,
    higher-risk lane, never merged into the high-conviction shortlist.
    research_only is re-forced on every record."""
    store = store or ArtifactStore()
    payload = _load_json(store.emerging_outlier_json)
    if payload is None or not payload.get("present"):
        return {"present": False, "research_only": True,
                "watch_status": (payload or {}).get("watch_status")
                or "NO_EMERGING_OUTLIERS", "watch": []}
    for w in payload.get("watch") or []:
        w["research_only"] = True
    fwd = _load_json(store.emerging_outlier_forward_json) or {}
    payload["forward_validation"] = {
        "present": bool(fwd.get("present")), "verdict": fwd.get("verdict"),
        "verdict_reason": fwd.get("verdict_reason")}
    deps = _downstream_dependency_audit(
        "emerging_outlier_watch", payload, store)
    payload["source_dependencies"] = deps
    payload["cadence_status"] = deps["status"]
    payload["stale_for_latest_scan"] = deps["stale_for_latest_scan"]
    payload["cadence_warning"] = (
        "Emerging Outlier list stale vs latest scan"
        if deps["stale_for_latest_scan"] else None)
    payload["age_hours"] = _age_hours(payload.get("generated_at"))
    return payload


# ── consolidated market-intelligence payload (cache-only) ───────────────────
# Wires the high-value TUI market/evidence/system-state metrics into the
# dashboard from the SAME canonical artifacts the TUI reads.  Every metric
# carries its own freshness.  No provider or LLM call, no orchestrator.

_MARKET_STALE_HOURS = 30.0        # a market artifact older than this is stale


def _staleness(generated_at: Optional[str],
               limit_h: float = _MARKET_STALE_HOURS) -> Dict[str, Any]:
    age = _age_hours(generated_at)
    return {"generated_at": generated_at, "age_hours":
            round(age, 1) if age is not None else None,
            "stale": bool(age is not None and age > limit_h),
            "available": generated_at is not None}


def _vol_state(vol: Dict[str, Any]) -> str:
    vix = vol.get("vix")
    avg = vol.get("vix_avg_20")
    if vix is None:
        return "Unknown"
    if vix >= 25:
        return "Elevated"
    if avg is not None and vix > avg * 1.1:
        return "Rising"
    if vix < 13:
        return "Low"
    return "Normal"


def build_market_context(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Market-environment strip (Level 1).  Reads regime_forecast +
    market_heartbeat — the canonical TUI sources.  Deterministic
    context-tension detection from existing fields (never LLM)."""
    store = store or ArtifactStore()
    rf = _load_json(store.regime_forecast_json) or {}
    hb = _load_json(store.market_heartbeat_json) or {}
    head = rf.get("headline") or {}
    vol = rf.get("volatility") or {}
    breadth = rf.get("breadth") or {}
    rot = rf.get("sector_rotation") or {}
    risk = hb.get("risk_signal") or {}

    leading = rot.get("leading") or []
    weakening = rot.get("weakening") or []
    breadth_pct = breadth.get("sector_breadth_pct_above_ma20")
    breadth_state = ("Unknown" if breadth_pct is None
                     else "Broad" if breadth_pct >= 0.7
                     else "Mixed" if breadth_pct >= 0.5 else "Narrow / Weak")
    # heartbeat + bias display
    hb_label = str(hb.get("heartbeat_label") or "").replace("_", " ").title()
    risk_signal = str(risk.get("signal") or "NEUTRAL")
    bias_display = {"RISK_ON": "Risk-On", "RISK_OFF": "Risk-Off",
                    "NEUTRAL": "Neutral / Mixed"}.get(risk_signal, risk_signal)

    forecast = {
        "bias_5d": head.get("bias_5d"),
        "bias_10d": head.get("bias_10d"),
        "confidence": head.get("confidence"),
        "forecast_state": rf.get("forecast_state"),
        "validation_status": rf.get("validation_status"),
        "invalidation_breached": head.get("invalidation_breached"),
        "invalidation_breach_reasons": head.get("invalidation_breach_reasons")
        or [],
    }

    # ── deterministic Context Tension (presentation-layer only) ──
    tension: List[str] = []
    constructive = str(head.get("bias_5d") or "").lower() == "constructive" \
        or str(head.get("bias_10d") or "").lower() == "constructive"
    if constructive and not leading:
        tension.append("Constructive short-term forecast, but no sector "
                       "leadership is confirmed.")
    if constructive and breadth_pct is not None and breadth_pct < 0.6:
        tension.append("Constructive forecast, but market breadth remains "
                       "narrow.")
    if head.get("invalidation_breached"):
        tension.append("Regime invalidation breached: "
                       + "; ".join(head.get("invalidation_breach_reasons")
                                   or ["see forecast"]) + ".")
    if hb.get("heartbeat_label") == "DEFENSIVE_ROTATION" and constructive:
        tension.append("Forecast is constructive, but the market heartbeat "
                       "shows defensive rotation.")
    if risk_signal == "RISK_ON" and not leading:
        tension.append("Risk-on posture, but no sector leadership.")

    return {
        "present": bool(rf or hb),
        "research_only": True,
        "regime": {
            "current": head.get("current_regime")
            or "Unknown",
            "confidence": head.get("confidence"),
            "probabilities": rf.get("regime_probabilities") or [],
        },
        "forecast": forecast,
        "heartbeat": {
            "label": hb_label or "Unknown",
            "drivers": (hb.get("heartbeat_reasons") or [])[:3],
            "risk": risk.get("risk_off_signals") or [],
        },
        "bias": {"posture": bias_display,
                 "net_score": risk.get("net_score"),
                 "trend_score": rf.get("trend_score")},
        "breadth": {"state": breadth_state, "pct_above_ma20": breadth_pct},
        "volatility": {"state": _vol_state(vol), "vix": vol.get("vix"),
                       "vix_avg_20": vol.get("vix_avg_20")},
        "leadership": {"leading": leading, "weak": weakening,
                       "label": ", ".join(leading) if leading
                       else "No clear leader"},
        "context_tension": tension,
        "freshness": _staleness(rf.get("built_at") or rf.get("generated_at")),
        "heartbeat_freshness": _staleness(hb.get("generated_at")),
    }


def build_research_confidence(
        store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Evidence & Confidence panel (Level 3).  Distinguishes engine state,
    research state, and promotion state; summarizes forward maturity and the
    Moment-of-Truth verdict.  Cache-only."""
    store = store or ArtifactStore()
    mot = _load_json(store.moment_of_truth_json) or {}
    fres = _load_json(store.forward_resolution_json) or {}
    programs = build_research_programs(store)
    status_fwd = _load_json(store.forward_json) or {}

    prog_verdicts = {pid: p.get("verdict")
                     for pid, p in (programs.get("programs") or {}).items()}

    # resolution coverage = matured / (matured + open) for the forecast lane
    fmat, fopen = fres.get("forecast_matured"), fres.get("forecast_open")
    res_cov = None
    if isinstance(fmat, (int, float)) and isinstance(fopen, (int, float)) \
            and (fmat + fopen) > 0:
        res_cov = round(100.0 * fmat / (fmat + fopen), 1)

    tracker_resolution = status_fwd.get("resolution_coverage") or {}
    tracker_resolution_warnings = status_fwd.get("resolution_warnings") or []

    return {
        "present": bool(programs.get("present") or mot),
        "research_only": True,
        "engine_state": "Operational",   # the dashboard rendered, so engine is up
        "research_state": "Research Only",
        "promotion_state": "Research Only",
        "moment_of_truth": {
            "verdict": mot.get("verdict"),
            "reason": mot.get("verdict_reason"),
            "next_decision": mot.get("phase_4b_blocked_reason"),
            "freshness": _staleness(mot.get("generated_at"),
                                    limit_h=24 * 30),  # MoT is a slow cadence
        },
        "program_verdicts": prog_verdicts,
        "forward_tracking": {
            "resolution_coverage_pct": res_cov,
            "next_maturity_due": fres.get("next_maturity_due"),
            "forecast_matured": fmat,
            "forecast_open": fopen,
            "lens_matured": fres.get("lens_matured"),
            "lens_open": fres.get("lens_open"),
            # combined open/matured across the tracked lanes, for the
            # Forward Evidence header Open-vs-Matured summary
            "total_open": (fopen or 0) + (fres.get("lens_open") or 0),
            "total_matured": (fmat or 0) + (fres.get("lens_matured") or 0),
            "unresolved_missing_price": fres.get("unresolved_missing_price"),
            "resolver_status": fres.get("resolver_status"),
            "watchlist_resolution_coverage": tracker_resolution,
            "watchlist_resolution_warnings": tracker_resolution_warnings,
            "watchlist_resolution_warning_count": len(tracker_resolution_warnings),
            "unresolved_repair_recommendation":
                status_fwd.get("unresolved_repair_recommendation"),
            "freshness": _staleness(fres.get("generated_at")),
        },
        "benchmark_readiness": build_status_benchmark_readiness(store),
        "freshness": _staleness(fres.get("generated_at")),
    }


def build_status_benchmark_readiness(store: ArtifactStore) -> Optional[str]:
    summary = _load_json(store.summary_json) or {}
    return (summary.get("forward_evidence") or {}).get("benchmark_readiness")


def build_system_trust(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Research System Trust panel (Level 5).  One overall status
    (TRUSTED / PARTIAL / DEGRADED / BLOCKED) from scan integrity, provider
    health, options overlay, and data freshness.  MCP state is included as a
    conditional operational diagnostic — it is the MCP-audit session state,
    NOT market data — and is never promoted to the market strip."""
    store = store or ArtifactStore()
    integ = build_scan_integrity(store)
    provider = _load_json(store.provider_health_json) or {}
    options = _load_json(store.options_coverage_json) or {}
    mcp = _load_json(store.mcp_analysis_json) or {}

    readiness = str(integ.get("readiness") or "UNKNOWN")
    provider_status = str(provider.get("overall_status") or "UNKNOWN")

    # overall: BLOCKED if scan blocked; DEGRADED if scan degraded or provider
    # not OK; PARTIAL if a non-critical source stale/disabled; else TRUSTED.
    if readiness == "BLOCKED":
        overall = "BLOCKED"
    elif readiness == "DEGRADED" or provider_status not in ("OK", "UNKNOWN"):
        overall = "DEGRADED"
    elif (options.get("overlay_state") == "DISABLED"
          or _staleness(provider.get("generated_at")).get("stale")):
        overall = "PARTIAL"
    else:
        overall = "TRUSTED"

    return {
        "present": True,
        "research_only": True,
        "overall": overall,
        "scan_integrity": {"readiness": integ.get("readiness"),
                           "same_session_coverage_pct":
                           integ.get("same_session_coverage_pct"),
                           "universe_coverage_pct":
                           integ.get("universe_coverage_pct")},
        "data_freshness": build_status_data_freshness(store),
        "options_overlay": {
            "state": options.get("overlay_state"),
            "coverage_pct": options.get("coverage_pct"),
            # DISABLED here is a known coverage limitation, not a crash
            "informational": options.get("overlay_state") == "DISABLED",
            "selection_status": options.get("selection_status"),
            "selection_note": options.get("selection_note"),
            "used_in_selection": options.get("used_in_selection"),
            "note": options.get("structural_cause"),
            "freshness": _staleness(options.get("generated_at")),
        },
        "provider_health": {"status": provider_status,
                            "freshness": _staleness(provider.get("generated_at"))},
        "mcp_state": {
            # MCP-audit session state {NORMAL/CONFLICTED/FRAGILE/STALE/BLOCKED}
            # — internal research-orchestration audit, operational-only.
            "state": mcp.get("state"),
            "session": mcp.get("session"),
            "operational_only": True,
            "affects_market_interpretation": False,
            "freshness": _staleness(mcp.get("generated_at")),
        },
    }


def build_status_data_freshness(store: ArtifactStore) -> Optional[str]:
    scanner = _load_json(store.scanner_json) or {}
    integ = (scanner.get("scan_integrity") or {})
    return integ.get("readiness")


def build_intel(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Consolidated cache-only intelligence payload for the dashboard."""
    store = store or ArtifactStore()
    return {
        "market_context": build_market_context(store),
        "research_confidence": build_research_confidence(store),
        "system_trust": build_system_trust(store),
        "research_only": True,
    }


def build_scan_integrity(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Scan Integrity model (P0, 2026-07-10 pipeline repair).

    Separates three things the old single FRESH/STALE label conflated:
      * artifact freshness        — when the scan artifact was generated
      * market-data session freshness — whether candidate + benchmark bars
        are all on the required completed trading session
      * universe coverage         — how much of the eligible market the
        discovery pool actually covers

    Readiness (READY / DEGRADED / BLOCKED) comes from the scanner's own
    scan_integrity block — the authority — enriched with refresh, discovery
    and stale-source detail.  Cache-only reads; degrades to explicit unknowns.
    """
    store = store or ArtifactStore()
    scanner = _load_json(store.scanner_json) or {}
    refresh = _load_json(store.price_refresh_json) or {}
    discovery = _load_json(store.discovery_coverage_json) or {}
    board = _load_json(store.alpha_board_json) or {}
    manifest = _load_json(store.scan_manifest_json) or {}
    exclusion_impact = _load_json(store.scan_exclusion_impact_json) or {}

    integrity = scanner.get("scan_integrity") or {}
    readiness = integrity.get("readiness") or "UNKNOWN"

    # Stale-source consumers: the legacy universe snapshot's status per the
    # P0-B gates.  The adapter is a pure artifact reader (no core imports),
    # so staleness comes from artifacts: the alpha board self-reports its
    # legacy-snapshot verdict (incl. session age), and the fallback is an
    # ISO-date comparison against the scanner's required session.
    snap = _load_json(store.legacy_universe_snapshot_json) or {}
    snap_as_of = str(snap.get("generated_at") or "")[:10] or None
    board_seed = (board.get("seed_source_info") or {})
    board_legacy = board_seed.get("legacy_snapshot") or {}
    snap_age_sessions = board_legacy.get("source_age_sessions")
    snap_stale = None  # None = snapshot absent (nothing to leak), not stale
    if snap:
        if isinstance(board_legacy.get("stale"), bool):
            snap_stale = board_legacy["stale"]
        else:
            required = (integrity.get("required_market_session")
                        or scanner.get("required_market_session"))
            if snap_as_of and required:
                snap_stale = snap_as_of < str(required)  # ISO dates compare
    stale_source_consumers = {
        "legacy_universe_snapshot": {
            "source_as_of": snap_as_of,
            "source_age_sessions": snap_age_sessions,
            "stale": snap_stale,
        },
        "alpha_discovery_seed_source": board_seed.get("seed_source") or "unknown",
        "alpha_discovery_legacy_status": (board_seed.get("legacy_snapshot") or {}).get("status"),
        "gated_consumers": ["market_posture", "regime_breadth", "liquid_top_lens_tier"],
    }

    impact_wording = exclusion_impact.get("impact_wording")
    if not impact_wording and readiness == "DEGRADED":
        excluded = manifest.get("excluded_count")
        if excluded:
            impact_wording = (
                f"Usable with caveats — {excluded} names excluded after "
                "delta refresh.")

    return {
        "readiness": readiness,
        "readiness_reasons": integrity.get("readiness_reasons") or [],
        # market-data session freshness
        "required_market_session": integrity.get("required_market_session")
        or scanner.get("required_market_session"),
        "benchmark_session": integrity.get("benchmark_session") or {},
        "benchmarks_aligned": integrity.get("benchmarks_aligned"),
        "same_session_coverage_pct": integrity.get("same_session_coverage_pct"),
        "session_mismatch_excluded": integrity.get("session_mismatch_excluded"),
        # artifact freshness (separate axis)
        "artifact_generated_at": scanner.get("generated_at"),
        "artifact_age_hours": (round(_age_hours(scanner.get("generated_at")), 1)
                               if _age_hours(scanner.get("generated_at")) is not None else None),
        # refresh detail
        "refresh": {
            "required_market_session": refresh.get("required_market_session"),
            "refresh_attempted": refresh.get("refresh_attempted"),
            "refresh_succeeded": refresh.get("refresh_succeeded"),
            "coverage_shortfall": refresh.get("coverage_shortfall"),
            "fmp_budget_status": (refresh.get("fmp_budget") or {}).get("budget_cap_status"),
        },
        # validated scan-universe manifest (P0 follow-up)
        "manifest": {
            "hash": integrity.get("manifest_hash")
            or scanner.get("scan_universe_manifest_hash"),
            "readiness": manifest.get("scan_readiness"),
            "delta_refresh_needed": manifest.get("delta_refresh_needed"),
            "delta_refresh_succeeded": manifest.get("delta_refresh_succeeded"),
            "aligned_final_universe_count": manifest.get("aligned_final_universe_count"),
            "excluded_by_reason": {
                k: len(v) for k, v in (manifest.get("excluded_by_reason") or {}).items()},
        },
        # universe coverage (discovery)
        "universe_coverage_pct": discovery.get("coverage_pct"),
        "eligible_security_master_count": discovery.get("eligible_security_master_count"),
        "missing_history_count": discovery.get("missing_history_count"),
        "newly_admitted_today": discovery.get("newly_admitted_today") or [],
        # hygiene
        "dead_symbol_exclusions": (refresh.get("excluded_dead_count")
                                   or (scanner.get("scan_integrity") or {}).get("dead_symbol_excluded_count")),
        "stale_source_consumers": stale_source_consumers,
        "impact_wording": impact_wording,
        "exclusion_impact": {
            "present": bool(exclusion_impact),
            "generated_at": exclusion_impact.get("generated_at"),
            "excluded_count": exclusion_impact.get("excluded_count"),
            "summary": exclusion_impact.get("summary") or {},
            "warnings": exclusion_impact.get("warnings") or [],
            "artifact": "cache/research/scan_exclusion_impact_latest.json",
        },
    }


def build_status(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Top status strip model.  Every field degrades to an explicit unknown."""
    store = store or ArtifactStore()
    summary = _load_json(store.summary_json)
    scanner = _load_json(store.scanner_json)
    forward = _load_json(store.forward_json)
    radar = _load_json(store.radar_json)

    missing = [
        name for name, obj in [
            ("nightly_operator_summary", summary),
            ("research_scanner", scanner),
            ("research_forward", forward),
            ("daily_alpha_radar", radar),
        ] if obj is None
    ]

    overall = (summary or {}).get("overall_status") or {}
    market = (summary or {}).get("market_context") or {}
    fwd = (forward or {}).get("overall") or {}

    tracker_verdict = fwd.get("verdict") or (summary or {}).get(
        "forward_evidence", {}).get("verdict")
    # Operating verdict is intentionally conservative: anything short of an
    # affirmative PROMISING tracker verdict stays NEED_MORE_DATA.  Mirrors the
    # 2026-07-02 delta-report doctrine; display-only.
    operating_verdict = "PASS" if tracker_verdict == "PROMISING" else "NEED_MORE_DATA"
    phase_4b = {
        "status": "UNBLOCKED" if operating_verdict == "PASS" else "BLOCKED",
        "reason": (
            "forward evidence insufficient"
            + (f" (tracker verdict: {tracker_verdict})" if tracker_verdict else "")
        ) if operating_verdict != "PASS" else "gates met",
    }

    scanner_age_h = _age_hours((scanner or {}).get("generated_at"))
    artifact_freshness = "UNKNOWN"
    if scanner_age_h is not None:
        artifact_freshness = "FRESH" if scanner_age_h <= FRESHNESS_STALE_HOURS else "STALE"

    # P0: a scan is not FRESH merely because the artifact is recent — the
    # combined label also requires same-session market data (scan integrity
    # READY).  DEGRADED surfaces reduced-but-honest coverage.
    scan_integrity = build_scan_integrity(store)
    readiness = scan_integrity.get("readiness")
    if artifact_freshness == "FRESH" and readiness == "READY":
        freshness = "FRESH"
    elif artifact_freshness == "FRESH" and readiness == "DEGRADED":
        freshness = "DEGRADED"
    elif artifact_freshness == "FRESH" and readiness == "BLOCKED":
        freshness = "BLOCKED"
    else:
        freshness = artifact_freshness  # STALE / UNKNOWN, or UNGATED runs

    quarantine = (radar or {}).get("quarantine_breakdown") or {}

    return {
        "mode": "RESEARCH_ONLY",
        "research_only": True,
        "nightly_status": overall.get("overall") or "UNKNOWN",
        "nightly_completed": bool(overall.get("nightly_completed")),
        "market_regime": market.get("regime") or "UNKNOWN",
        "phase_4b": phase_4b,
        "tracker_verdict": tracker_verdict or "UNKNOWN",
        "operating_verdict": operating_verdict,
        "matured_5d": fwd.get("matured_5d_entries"),
        "matured_10d": fwd.get("matured_entries"),
        "total_tracked": fwd.get("total_entries"),
        "sample_status": fwd.get("sample_status"),
        "quarantine_breakdown": quarantine or {},
        "benchmark_readiness": (summary or {}).get("forward_evidence", {}).get(
            "benchmark_readiness") or "UNKNOWN",
        "data_freshness": freshness,
        "artifact_freshness": artifact_freshness,
        "scan_integrity": scan_integrity,
        "scanner_age_hours": round(scanner_age_h, 1) if scanner_age_h is not None else None,
        "stale_skipped": (scanner or {}).get("stale_price_skipped_count"),
        "suspect_skipped": (scanner or {}).get("data_suspect_skipped_count"),
        "quarantine_count": sum(quarantine.values()) if quarantine else None,
        "warnings": (summary or {}).get("warnings") or [],
        "journal_audit": build_journal_audit(store),
        "market_context": build_market_context(store),
        "research_confidence": build_research_confidence(store),
        "system_trust": build_system_trust(store),
        "high_conviction": build_high_conviction(store),
        "emerging_outlier": build_emerging_outlier(store),
        "research_programs": build_research_programs(store),
        "latest_research_scan": build_latest_research_scan(store),
        "missing_artifacts": missing,
        "fallback": MISSING_ARTIFACT if missing else None,
        "generated_at": (scanner or {}).get("generated_at"),
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Forward evidence per ticker ──────────────────────────────────────────────


def _latest_forward_by_ticker(store: ArtifactStore) -> Dict[str, Dict[str, Any]]:
    """Most recent history entry per ticker (prefers entries with returns)."""
    out: Dict[str, Dict[str, Any]] = {}
    path = store.history_jsonl
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = str(e.get("ticker") or "").upper()
            if not t:
                continue
            prev = out.get(t)
            # Prefer the entry with the most matured horizons; tie-break on
            # the later appearance_date.
            def _rank(x: Dict[str, Any]):
                horizons = sum(1 for k in ("ret_5d", "ret_10d", "ret_20d")
                               if x.get(k) is not None)
                return (horizons, str(x.get("appearance_date") or ""))
            if prev is None or _rank(e) >= _rank(prev):
                out[t] = e
    except Exception:
        return out
    return out


def _forward_block(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    e = entry or {}
    return {
        "appearance_date": e.get("appearance_date"),
        "returns": {
            "5d": e.get("ret_5d"),
            "10d": e.get("ret_10d"),
            "20d": e.get("ret_20d"),
        },
        "benchmarks": {
            "spy": e.get("ret_5d_vs_spy") if e.get("ret_5d_vs_spy") is not None else e.get("ret_10d_vs_spy"),
            "qqq": e.get("ret_5d_vs_qqq") if e.get("ret_5d_vs_qqq") is not None else e.get("ret_10d_vs_qqq"),
            "sector": e.get("ret_5d_vs_sector") if e.get("ret_5d_vs_sector") is not None else e.get("ret_10d_vs_sector"),
        },
        "benchmarks_10d": {
            "spy": e.get("ret_10d_vs_spy"),
            "qqq": e.get("ret_10d_vs_qqq"),
            "sector": e.get("ret_10d_vs_sector"),
        },
        "sector_etf": e.get("benchmark_sector_etf"),
        "forward_maturity": {
            "has_5d": e.get("ret_5d") is not None,
            "has_10d": e.get("ret_10d") is not None,
            "has_20d": e.get("ret_20d") is not None,
        },
    }


# ── Leaderboard ──────────────────────────────────────────────────────────────


def _priority_by_ticker(radar: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Radar priority label per ticker.  The artifact stores
    `priority_tickers` as a dict {label: [tickers]}; older snapshots used a
    list of [label, tickers] pairs — accept both."""
    out: Dict[str, str] = {}
    raw = (radar or {}).get("priority_tickers") or {}
    pairs = raw.items() if isinstance(raw, dict) else raw
    for pair in pairs:
        try:
            label, tickers = pair
        except Exception:
            continue
        for t in tickers or []:
            out.setdefault(str(t).upper(), str(label))
    return out


def _source_by_ticker(universe: Optional[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for e in ((universe or {}).get("universe_full") or []):
        t = str(e.get("ticker") or "").upper()
        if t:
            out[t] = str(e.get("source") or "unknown")
    return out


def _row_quality(item: Dict[str, Any], warnings: List[str]) -> str:
    if SUSPECT_FEED in warnings or INSUFFICIENT_HISTORY in warnings:
        return "QUARANTINE"
    if warnings:
        return "WARN"
    conf = str(item.get("data_confidence") or "").upper()
    if conf == "LOW":
        return "WARN"
    return "OK"


def build_leaderboard(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Heatmap leaderboard rows from the scanner watchlist + overlays."""
    store = store or ArtifactStore()
    scanner = _load_json(store.scanner_json)
    if scanner is None:
        return {"rows": [], "fallback": MISSING_ARTIFACT,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    radar = _load_json(store.radar_json)
    universe = _load_json(store.universe_json)
    fwd_by_ticker = _latest_forward_by_ticker(store)
    prio = _priority_by_ticker(radar)
    src = _source_by_ticker(universe)
    suspect = {s.upper() for s in scanner.get("data_suspect_skipped_sample") or []}
    stale_px = {s.upper() for s in scanner.get("stale_price_skipped_sample") or []}

    rows: List[Dict[str, Any]] = []
    for item in scanner.get("watchlist") or []:
        t = str(item.get("ticker") or "").upper()
        if not t:
            continue
        fwd_entry = fwd_by_ticker.get(t)
        fwd = _forward_block(fwd_entry)

        warnings: List[str] = []
        price = item.get("latest_close")
        price_age = _days_since(item.get("latest_price_date"))
        if price is None:
            warnings.append(NO_DATA)
        elif price_age is not None and price_age > STALE_PRICE_DAYS:
            warnings.append(STALE_PRICE)
        if t in stale_px:
            if STALE_PRICE not in warnings:
                warnings.append(STALE_PRICE)
        if t in suspect:
            warnings.append(SUSPECT_FEED)
        if item.get("insufficient_history_for_ma200") or (
                item.get("bars_available") is not None
                and item["bars_available"] < 63):
            warnings.append(INSUFFICIENT_HISTORY)
        if not any(fwd["forward_maturity"].values()):
            warnings.append(IMMATURE_FORWARD_EVIDENCE)
        elif all(v is None for v in fwd["benchmarks"].values()):
            warnings.append(BENCHMARK_MISSING)

        rows.append({
            "ticker": t,
            "company": item.get("company_name"),
            "price": price,
            "price_date": item.get("latest_price_date"),
            "returns": {
                "1d": None,  # trailing 1d not published by the scanner sidecar
                "5d": fwd["returns"]["5d"],
                "10d": fwd["returns"]["10d"],
                "20d": fwd["returns"]["20d"],
            },
            "benchmarks": fwd["benchmarks"],
            "rs_20d_vs_spy": item.get("rs_20d_vs_spy"),
            "rs_63d_vs_spy": item.get("rs_63d_vs_spy"),
            "sector": item.get("sector"),
            "sector_etf": item.get("company_sector_etf") or fwd.get("sector_etf"),
            "label": prio.get(t) or item.get("watchlist_label"),
            "watchlist_label": item.get("watchlist_label"),
            "scanner_category": item.get("category"),
            "all_categories": item.get("all_categories") or [],
            "source": src.get(t, "unknown"),
            "confidence": item.get("data_confidence"),
            "research_score": item.get("research_score"),
            "data_quality": _row_quality(item, warnings),
            "warnings": warnings,
            "forward_maturity": fwd["forward_maturity"],
            "research_only_footer": RESEARCH_ONLY_FOOTER,
        })

    rows.sort(key=lambda r: -(r.get("research_score") or 0))
    return {
        "rows": rows,
        "count": len(rows),
        "generated_at": scanner.get("generated_at"),
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Ticker detail ────────────────────────────────────────────────────────────


def build_ticker_detail(ticker: str,
                        store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Right-drawer model for one ticker.  Always returns a dict; unknown
    tickers get an explicit NO_DATA fallback."""
    store = store or ArtifactStore()
    t = str(ticker or "").upper()
    scanner = _load_json(store.scanner_json)
    if scanner is None:
        return {"ticker": t, "fallback": MISSING_ARTIFACT,
                "manual_review_required": True,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    item = next((w for w in scanner.get("watchlist") or []
                 if str(w.get("ticker") or "").upper() == t), None)
    if item is None:
        return {"ticker": t, "fallback": NO_DATA,
                "manual_review_required": True,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    board = build_leaderboard(store)
    row = next((r for r in board["rows"] if r["ticker"] == t), None) or {}
    fwd_entry = _latest_forward_by_ticker(store).get(t)
    fwd = _forward_block(fwd_entry)

    social_status = None
    cats = item.get("all_categories") or [item.get("category")]
    if "social_arb_attention" in cats:
        social_status = "SOCIAL_ATTENTION_SIGNAL"

    return {
        "ticker": t,
        "company": item.get("company_name"),
        "sector": item.get("sector"),
        "industry": item.get("industry"),
        "sector_etf": row.get("sector_etf"),
        "price": item.get("latest_close"),
        "price_date": item.get("latest_price_date"),
        "label": row.get("label"),
        "watchlist_label": item.get("watchlist_label"),
        "scanner_category": item.get("category"),
        "all_categories": cats,
        "source": row.get("source", "unknown"),
        "reason": item.get("why_appeared"),
        "confirms_if": item.get("confirms_if"),
        "invalidates_if": item.get("invalidates_if"),
        "confidence": item.get("data_confidence"),
        "research_score": item.get("research_score"),
        "rs_20d_vs_spy": item.get("rs_20d_vs_spy"),
        "rs_63d_vs_spy": item.get("rs_63d_vs_spy"),
        "market_cap": item.get("market_cap"),
        "extension_state": item.get("extension_state"),
        "bars_available": item.get("bars_available"),
        "data_quality": row.get("data_quality", "UNKNOWN"),
        "warnings": row.get("warnings", []),
        "forward": fwd,
        "social_status": social_status,
        "claude_review": None,  # not published by any current artifact
        "manual_review_required": True,
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 2: per-ticker chart series ─────────────────────────────────────────
#
# Read-only research visuals.  No trade markers of any kind — the payload
# carries prices, volume, moving averages, date-aligned relative-strength
# series, and the ticker's forward-vs-benchmark evidence block.

SERIES_MAX_BARS = 260  # ~1 trading year; keeps the JSON payload small


def _read_price_frame(store: ArtifactStore, symbol: str):
    """Merged deep+shallow OHLCV frame for one symbol, or None.

    pandas is imported lazily so the status/leaderboard paths (and their
    tests) never need it.
    """
    import pandas as pd  # lazy — chart path only

    def _read(path: Path):
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            if df is None or df.empty:
                return None
            if "close" not in df.columns and "Close" in df.columns:
                df = df.rename(columns={"Close": "close"})
            if "close" not in df.columns:
                return None
            df.index = pd.to_datetime(df.index)
            return df.sort_index()
        except Exception:
            return None

    sym = symbol.upper()
    shallow = _read(store.prices_dir / f"{sym}.parquet")
    deep = _read(store.prices_deep_dir / f"{sym}.parquet")
    if shallow is None and deep is None:
        return None
    if shallow is None:
        return deep
    if deep is None:
        return shallow
    try:
        import pandas as pd
        combined = pd.concat([deep, shallow])
        combined = combined[~combined.index.duplicated(keep="last")]
        return combined.sort_index()
    except Exception:
        return shallow


def _rel_strength_series(t_close, b_close) -> List[Optional[float]]:
    """Date-aligned cumulative relative return of ticker vs benchmark, %.

    rel[t] = ((P_t / P_0) / (B_t / B_0) - 1) * 100 over the ticker's dates;
    dates missing from the benchmark yield None (rendered as gaps, never
    interpolated).
    """
    b = b_close.reindex(t_close.index)
    out: List[Optional[float]] = []
    p0 = float(t_close.iloc[0])
    b0 = None
    for i in range(len(t_close)):
        bv = b.iloc[i]
        if bv is None or (bv != bv):  # NaN
            out.append(None)
            continue
        if b0 is None:
            b0 = float(bv)
            p0 = float(t_close.iloc[i])
        try:
            rel = ((float(t_close.iloc[i]) / p0) / (float(bv) / b0) - 1.0) * 100.0
            out.append(round(rel, 3))
        except Exception:
            out.append(None)
    return out


def build_ticker_series(ticker: str,
                        store: Optional[ArtifactStore] = None,
                        max_bars: int = SERIES_MAX_BARS) -> Dict[str, Any]:
    """Chart payload for one ticker: dates, close, volume, MA50/MA200,
    RS vs SPY / QQQ / sector ETF, and the forward-vs-benchmark block."""
    store = store or ArtifactStore()
    t = str(ticker or "").upper()

    df = _read_price_frame(store, t)
    if df is None:
        return {"ticker": t, "fallback": NO_DATA,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    close_full = df["close"].astype(float)
    ma50_full = close_full.rolling(50).mean()
    ma200_full = close_full.rolling(200).mean()

    df = df.tail(max_bars)
    close = close_full.tail(max_bars)
    dates = [str(d)[:10] for d in df.index]

    def _col(series) -> List[Optional[float]]:
        return [None if (v != v) else round(float(v), 4)
                for v in series.tail(max_bars).tolist()]

    volume = None
    for col in ("volume", "Volume"):
        if col in df.columns:
            volume = [None if (v != v) else float(v)
                      for v in df[col].astype(float).tolist()]
            break

    # Sector ETF from the scanner watchlist entry (display metadata only)
    sector_etf = None
    scanner = _load_json(store.scanner_json)
    if scanner is not None:
        item = next((w for w in scanner.get("watchlist") or []
                     if str(w.get("ticker") or "").upper() == t), None)
        if item:
            sector_etf = item.get("company_sector_etf")

    def _rs(bench_symbol: Optional[str]) -> Optional[List[Optional[float]]]:
        if not bench_symbol:
            return None
        bdf = _read_price_frame(store, bench_symbol)
        if bdf is None:
            return None
        return _rel_strength_series(close, bdf["close"].astype(float))

    warnings: List[str] = []
    price_age = _days_since(dates[-1] if dates else None)
    if price_age is not None and price_age > STALE_PRICE_DAYS:
        warnings.append(STALE_PRICE)

    rs_spy = _rs("SPY")
    rs_qqq = _rs("QQQ")
    rs_sector = _rs(sector_etf)
    if rs_spy is None:
        warnings.append(BENCHMARK_MISSING)

    fwd = _forward_block(_latest_forward_by_ticker(store).get(t))

    return {
        "ticker": t,
        "sector_etf": sector_etf,
        "dates": dates,
        "close": _col(close_full),
        "volume": volume,
        "ma50": _col(ma50_full),
        "ma200": _col(ma200_full),
        "rs_vs_spy": rs_spy,
        "rs_vs_qqq": rs_qqq,
        "rs_vs_sector": rs_sector,
        "forward": fwd,
        "bars": len(dates),
        "warnings": warnings,
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 3: forward-evidence cohort analytics ───────────────────────────────
#
# Two provenance tiers, kept visually separate in the UI:
#   1. `by_label` — the tracker's OWN published verdicts, passed through
#      unmodified (single source of truth; the dashboard never recomputes
#      or overrides a published verdict).
#   2. `computed` — cohorts the tracker does not publish (by scanner
#      category, by universe source, pre/post price-fix split), computed
#      here from the history JSONL with explicit caveats.
#
# Sample-maturity ladder mirrors research_watchlist_forward_tracker.py
# (constants duplicated, not imported — the dashboard stays decoupled):
#   TOO_EARLY < 10, PROVISIONAL 10-29, MEANINGFUL 30-99, ROBUST >= 100.

SAMPLE_THRESHOLD_PROVISIONAL = 10
SAMPLE_THRESHOLD_MEANINGFUL = 30
SAMPLE_THRESHOLD_ROBUST = 100

# Cohort boundary from the 2026-07-02 delta report: entries before this date
# were SELECTED by a scanner running on stale prices; pooled analytics would
# blend two different engines.  Display-only constant.
PRICE_FIX_BOUNDARY = "2026-07-02"


def _sample_status(n_matured: int) -> str:
    if n_matured < SAMPLE_THRESHOLD_PROVISIONAL:
        return "TOO_EARLY"
    if n_matured < SAMPLE_THRESHOLD_MEANINGFUL:
        return "PROVISIONAL"
    if n_matured < SAMPLE_THRESHOLD_ROBUST:
        return "MEANINGFUL"
    return "ROBUST"


def _median(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _cohort_stats(name: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Descriptive stats for one cohort.  10d is the primary horizon (matches
    the tracker); 5d shown alongside.  No verdicts — computed cohorts carry
    stats + maturity only, never a verdict of their own."""
    def _vals(field: str) -> List[float]:
        return [float(e[field]) for e in entries if e.get(field) is not None]

    r5, r10 = _vals("ret_5d"), _vals("ret_10d")
    vs_spy, vs_qqq, vs_sec = (_vals("ret_10d_vs_spy"), _vals("ret_10d_vs_qqq"),
                              _vals("ret_10d_vs_sector"))
    n10 = len(r10)
    return {
        "cohort": name,
        "n_entries": len(entries),
        "n_matured_5d": len(r5),
        "n_matured_10d": n10,
        "avg_ret_5d": round(sum(r5) / len(r5), 2) if r5 else None,
        "avg_ret_10d": round(sum(r10) / n10, 2) if r10 else None,
        "median_ret_10d": round(_median(r10), 2) if r10 else None,
        "win_rate_10d": round(sum(1 for v in r10 if v > 0) / n10, 3) if n10 else None,
        "mean_vs_spy_10d": round(sum(vs_spy) / len(vs_spy), 2) if vs_spy else None,
        "mean_vs_qqq_10d": round(sum(vs_qqq) / len(vs_qqq), 2) if vs_qqq else None,
        "mean_vs_sector_10d": round(sum(vs_sec) / len(vs_sec), 2) if vs_sec else None,
        "n_with_spy_10d": len(vs_spy),
        "sample_status": _sample_status(n10),
    }


def _all_history_entries(store: ArtifactStore) -> List[Dict[str, Any]]:
    path = store.history_jsonl
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return out


def build_forward_cohorts(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Cohort analytics payload for the Forward Evidence page."""
    store = store or ArtifactStore()
    forward = _load_json(store.forward_json)
    entries = _all_history_entries(store)
    if forward is None and not entries:
        return {"fallback": MISSING_ARTIFACT,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    # Tier 1 — tracker-published label verdicts, passed through unmodified.
    by_label = (forward or {}).get("verdicts_by_label") or []

    # Tier 2 — computed cohorts (stats only, no verdicts).
    def _group(keyfn) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for e in entries:
            k = keyfn(e)
            if k:
                groups.setdefault(str(k), []).append(e)
        return groups

    by_category = [
        _cohort_stats(k, v) for k, v in sorted(
            _group(lambda e: e.get("category")).items())
    ]

    # Source attribution caveat: history entries do not record the universe
    # source at appearance time, so we join on the CURRENT universe map.
    src_map = _source_by_ticker(_load_json(store.universe_json))
    by_source = [
        _cohort_stats(k, v) for k, v in sorted(
            _group(lambda e: src_map.get(str(e.get("ticker") or "").upper())).items())
    ]

    # Pre/post price-fix split (delta-report doctrine: never pool the eras).
    def _era(e) -> Optional[str]:
        d = str(e.get("appearance_date") or "")[:10]
        if not d:
            return None
        return "post_fix" if d >= PRICE_FIX_BOUNDARY else "pre_fix"
    by_era = [_cohort_stats(k, v) for k, v in sorted(_group(_era).items())]

    return {
        "generated_at": (forward or {}).get("generated_at"),
        "overall": (forward or {}).get("overall") or {},
        "by_label": by_label,
        "by_category": by_category,
        "by_source": by_source,
        "by_era": by_era,
        "resolution_coverage": (forward or {}).get("resolution_coverage") or {},
        "resolution_warnings": (forward or {}).get("resolution_warnings") or [],
        "unresolved_repair_recommendation":
            (forward or {}).get("unresolved_repair_recommendation"),
        "price_fix_boundary": PRICE_FIX_BOUNDARY,
        "caveats": [
            "by_label rows are the tracker's published verdicts (unmodified).",
            "by_category / by_source / by_era are descriptive stats computed "
            "from the history ledger — no verdicts are assigned to them.",
            "by_source joins on the CURRENT universe source map; "
            "appearance-time source is not recorded in the ledger.",
            f"Entries before {PRICE_FIX_BOUNDARY} were selected on stale "
            "price data (see delta report) — do not pool the eras.",
        ],
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 3: sector compass ──────────────────────────────────────────────────

SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLU",
               "XLB", "XLRE", "XLC"]
SECTOR_NAMES = {
    "XLK": "Technology", "XLF": "Financials", "XLV": "Health Care",
    "XLE": "Energy", "XLI": "Industrials", "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples", "XLU": "Utilities", "XLB": "Materials",
    "XLRE": "Real Estate", "XLC": "Communication Services",
}


def build_sector_compass(store: Optional[ArtifactStore] = None,
                         spark_bars: int = 64) -> Dict[str, Any]:
    """Sector rotation view: date-aligned RS vs SPY for each sector ETF
    (20d / 63d cumulative relative return + a short spark series)."""
    store = store or ArtifactStore()
    spy = _read_price_frame(store, "SPY")
    if spy is None:
        return {"fallback": NO_DATA, "sectors": [],
                "research_only_footer": RESEARCH_ONLY_FOOTER}
    spy_close = spy["close"].astype(float)

    sectors: List[Dict[str, Any]] = []
    for etf in SECTOR_ETFS:
        df = _read_price_frame(store, etf)
        if df is None:
            sectors.append({"etf": etf, "name": SECTOR_NAMES.get(etf),
                            "fallback": NO_DATA})
            continue
        close = df["close"].astype(float)
        rel_full = _rel_strength_series(close.tail(spark_bars), spy_close)

        def _rs(lookback: int) -> Optional[float]:
            c = close.tail(lookback + 1)
            if len(c) < lookback + 1:
                return None
            rel = _rel_strength_series(c, spy_close)
            return rel[-1] if rel and rel[-1] is not None else None

        sectors.append({
            "etf": etf,
            "name": SECTOR_NAMES.get(etf),
            "rs_20d_vs_spy": _rs(20),
            "rs_63d_vs_spy": _rs(63),
            "spark_rs_vs_spy": rel_full,
            "last_date": str(df.index.max())[:10],
            "fallback": None,
        })

    ranked = [s for s in sectors if s.get("rs_20d_vs_spy") is not None]
    ranked.sort(key=lambda s: -(s["rs_20d_vs_spy"] or 0))
    return {
        "sectors": sectors,
        "leaders": [s["etf"] for s in ranked[:4]],
        "laggards": [s["etf"] for s in ranked[-4:]] if len(ranked) >= 4 else [],
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 3: social attention overview ───────────────────────────────────────


def build_social_overview(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Social attention cohort view: the social forward validator's published
    payload (verdict passed through unmodified) + the scanner's current
    social lane candidates."""
    store = store or ArtifactStore()
    social_path = store.research_dir / "social_attention_forward_latest.json"
    social = _load_json(social_path)
    scanner = _load_json(store.scanner_json)

    lane: List[Dict[str, Any]] = []
    for item in ((scanner or {}).get("categories") or {}).get(
            "social_arb_attention", []):
        if item.get("social_data_available") is False:
            continue
        lane.append({
            "ticker": item.get("ticker"),
            "research_score": item.get("research_score"),
            "watchlist_label": item.get("watchlist_label"),
        })

    if social is None and not lane:
        return {"fallback": MISSING_ARTIFACT,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    validator_age_h = _age_hours((social or {}).get("generated_at"))
    lane_generated_at = (scanner or {}).get("generated_at")
    lane_age_h = _age_hours(lane_generated_at)
    return {
        "generated_at": (social or {}).get("generated_at"),
        "verdict": (social or {}).get("verdict"),
        "verdict_reason": (social or {}).get("verdict_reason"),
        "matured_social_led": (social or {}).get("matured_social_led_primary"),
        "by_cohort": (social or {}).get("by_cohort") or {},
        "comparisons": (social or {}).get("comparisons") or {},
        "history_days": (social or {}).get("history_days"),
        "current_lane": lane,
        # Freshness: the verdict block and the lane come from two different
        # pipelines on different cadences (validator: 21:30 UTC cron; lane:
        # scanner premarket+nightly).  Publish both ages so the UI never
        # presents them as one synchronized snapshot.
        "validator_age_hours": (round(validator_age_h, 1)
                                if validator_age_h is not None else None),
        "lane_generated_at": lane_generated_at,
        "lane_age_hours": round(lane_age_h, 1) if lane_age_h is not None else None,
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 4: data-quality dashboard ──────────────────────────────────────────
#
# This page exists because stale prices silently contaminated the scanner
# for three weeks (2026-06-12 → 2026-07-02).  Everything here is read from
# sidecars the research cycle already writes; ages are computed, thresholds
# are display-only.

SIDECAR_FRESH_HOURS = 26.0   # nightly cadence + slack; display threshold only


def build_data_quality(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    store = store or ArtifactStore()

    scanner = _load_json(store.scanner_json)
    forward = _load_json(store.forward_json)
    radar = _load_json(store.radar_json)
    summary = _load_json(store.summary_json)
    refresh = _load_json(store.research_dir / "universe_price_refresh_latest.json")
    coverage = _load_json(store.research_dir / "research_coverage_latest.json")

    if all(x is None for x in (scanner, forward, radar, summary, refresh, coverage)):
        return {"fallback": MISSING_ARTIFACT,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    # ── provider price refresh (the fix that ended the stale-cache era) ──
    price_refresh = None
    if refresh is not None:
        price_refresh = {
            "generated_at": refresh.get("generated_at"),
            "age_hours": _age_hours(refresh.get("generated_at")),
            "mode": refresh.get("mode"),
            "universe_size": refresh.get("universe_size"),
            "already_fresh": refresh.get("already_fresh"),
            "stale_or_missing": refresh.get("stale_or_missing"),
            "refreshed_ok": refresh.get("refreshed_ok"),
            "refresh_failed": refresh.get("refresh_failed"),
            "truncated_by_budget": refresh.get("truncated_by_budget"),
            "failed": (refresh.get("failed") or [])[:25],
        }

    # ── scanner data guards ──
    guards = None
    if scanner is not None:
        guards = {
            "stale_skipped": scanner.get("stale_price_skipped_count"),
            "stale_sample": scanner.get("stale_price_skipped_sample") or [],
            "suspect_skipped": scanner.get("data_suspect_skipped_count"),
            "suspect_sample": scanner.get("data_suspect_skipped_sample") or [],
            "stale_skip_days": scanner.get("stale_price_skip_days"),
        }

    # ── universe coverage aggregates (per-ticker audit rows) ──
    universe_coverage = None
    if coverage is not None:
        tickers = coverage.get("tickers") or []
        fresh_2d = stale_7d = lt63 = lt300 = 0
        for t in tickers:
            age = t.get("price_age_days")
            bars = t.get("price_bars")
            if age is not None:
                if age <= 2:
                    fresh_2d += 1
                elif age > STALE_PRICE_DAYS:
                    stale_7d += 1
            if bars is not None:
                if bars < 63:
                    lt63 += 1
                if bars < 300:
                    lt300 += 1
        universe_coverage = {
            "generated_at": coverage.get("generated_at"),
            "total_tickers": coverage.get("total_tickers"),
            "confidence_counts": coverage.get("confidence_counts") or {},
            "actionable_pct": coverage.get("actionable_pct"),
            "fresh_within_2d": fresh_2d,
            "stale_over_7d": stale_7d,
            "bars_below_63": lt63,
            "bars_below_300": lt300,
        }

    # ── quarantine + young listings ──
    quarantine = (radar or {}).get("quarantine_breakdown") or {}
    young: List[str] = []
    for item in (scanner or {}).get("watchlist") or []:
        if item.get("insufficient_history_for_ma200") or (
                item.get("bars_available") is not None
                and item["bars_available"] < 63):
            t = str(item.get("ticker") or "").upper()
            if t:
                young.append(t)
    young = sorted(set(young))[:25]

    # ── benchmark coverage (from the forward tracker) ──
    benchmark = (forward or {}).get("benchmark_readiness") or {}

    # ── sidecar health ──
    def _health(name: str, obj: Optional[Dict[str, Any]], path: Path) -> Dict[str, Any]:
        if obj is None:
            return {"name": name, "status": "MISSING", "generated_at": None,
                    "age_hours": None}
        age = _age_hours(obj.get("generated_at"))
        if age is None and path.exists():
            age = (datetime.now(timezone.utc).timestamp()
                   - path.stat().st_mtime) / 3600.0
        status = "UNKNOWN"
        if age is not None:
            status = "FRESH" if age <= SIDECAR_FRESH_HOURS else "STALE"
        return {"name": name, "status": status,
                "generated_at": obj.get("generated_at"),
                "age_hours": round(age, 1) if age is not None else None}

    sidecar_health = [
        _health("research_scanner", scanner, store.scanner_json),
        _health("research_forward", forward, store.forward_json),
        _health("daily_alpha_radar", radar, store.radar_json),
        _health("nightly_operator_summary", summary, store.summary_json),
        _health("universe_price_refresh", refresh,
                store.research_dir / "universe_price_refresh_latest.json"),
        _health("research_coverage", coverage,
                store.research_dir / "research_coverage_latest.json"),
    ]

    return {
        "price_refresh": price_refresh,
        "scanner_guards": guards,
        "universe_coverage": universe_coverage,
        "quarantine_breakdown": quarantine,
        "quarantine_total": sum(quarantine.values()) if quarantine else None,
        "young_listings": young,
        "benchmark_coverage": benchmark,
        "sidecar_health": sidecar_health,
        "warnings": (summary or {}).get("warnings") or [],
        # No warnings-history artifact exists yet; showing a trend would
        # require fabricating one.  Stated honestly instead.
        "warning_trend_note": ("Trend unavailable — the nightly summary does "
                               "not persist a warnings history artifact."),
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 5: fundamental lens (descriptive overlay, never a signal) ──────────
#
# Computed from the cached FMP quarterly statements (4 quarters).  All
# figures are trailing/TTM aggregates; with only four quarters cached, true
# YoY growth is not computable — growth is shown as q/q and vs-3-quarters-ago
# and labeled as such.  The quality label is DESCRIPTIVE (stated rules on
# profitability + runway) and carries no predictive claim.

FUNDAMENTAL_QUALITY_RULES = (
    "PROFITABLE_CASHGEN: TTM net income > 0 and TTM FCF > 0 · "
    "PROFITABLE: TTM net income > 0, TTM FCF <= 0 · "
    "UNPROFITABLE_FUNDED: TTM net income <= 0 but cash covers >= 2 years of "
    "FCF burn (or net cash position) · "
    "UNPROFITABLE_STRESSED: TTM net income <= 0 and cash < 1 year of burn · "
    "UNKNOWN: statements missing"
)


def _sum_field(rows: List[Dict[str, Any]], field: str) -> Optional[float]:
    vals = [r.get(field) for r in rows if r.get(field) is not None]
    return float(sum(vals)) if vals else None


def _ratio(num: Optional[float], den: Optional[float],
           scale: float = 100.0) -> Optional[float]:
    if num is None or den is None or den == 0:
        return None
    return round(num / den * scale, 2)


def build_fundamentals(ticker: str,
                       store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    store = store or ArtifactStore()
    t = str(ticker or "").upper()
    raw = _load_json(store.fundamentals_dir / f"{t}.json")
    if raw is None:
        return {"ticker": t, "fallback": NO_DATA,
                "quality_label": "UNKNOWN",
                "quality_rules": FUNDAMENTAL_QUALITY_RULES,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    def _rows(stmt: str) -> List[Dict[str, Any]]:
        rows = raw.get(stmt) or []
        try:
            return sorted(rows, key=lambda r: str(r.get("date") or ""),
                          reverse=True)
        except Exception:
            return rows

    income, balance, cashflow = _rows("income"), _rows("balance"), _rows("cashflow")

    # TTM aggregates over the available (max 4) quarters
    ttm_rev = _sum_field(income, "revenue")
    ttm_gross = _sum_field(income, "grossProfit")
    ttm_opinc = _sum_field(income, "operatingIncome")
    ttm_ni = _sum_field(income, "netIncome")
    ttm_fcf = _sum_field(cashflow, "freeCashFlow")
    ttm_ocf = _sum_field(cashflow, "operatingCashFlow")
    ttm_sbc = _sum_field(cashflow, "stockBasedCompensation")

    # Growth: q/q and vs 3 quarters back (NOT YoY — only 4 quarters cached)
    def _growth(idx: int) -> Optional[float]:
        if len(income) <= idx:
            return None
        newest, older = income[0].get("revenue"), income[idx].get("revenue")
        if newest is None or older in (None, 0):
            return None
        return round((float(newest) / float(older) - 1.0) * 100.0, 2)

    rev_growth_qoq = _growth(1)
    rev_growth_3q = _growth(3)

    # Balance sheet (latest quarter)
    b0 = balance[0] if balance else {}
    cash = b0.get("cashAndShortTermInvestments")
    total_debt = b0.get("totalDebt")
    net_debt = b0.get("netDebt")

    # Dilution: diluted share count now vs 3 quarters back
    dilution_pct = None
    if len(income) >= 4:
        s_new = income[0].get("weightedAverageShsOutDil")
        s_old = income[3].get("weightedAverageShsOutDil")
        if s_new and s_old:
            dilution_pct = round((float(s_new) / float(s_old) - 1.0) * 100.0, 2)

    # Valuation multiples need a market cap — taken from the scanner
    # watchlist entry when the ticker is on the current board.
    market_cap = None
    earnings_date = None
    scanner = _load_json(store.scanner_json)
    if scanner is not None:
        item = next((w for w in scanner.get("watchlist") or []
                     if str(w.get("ticker") or "").upper() == t), None)
        if item:
            market_cap = item.get("market_cap")
        for c in (scanner.get("categories") or {}).get("catalyst_watch", []):
            if str(c.get("ticker") or "").upper() == t:
                earnings_date = c.get("earnings_date")
                break

    ps = _ratio(market_cap, ttm_rev, 1.0)
    pe = _ratio(market_cap, ttm_ni, 1.0) if (ttm_ni or 0) > 0 else None
    pfcf = _ratio(market_cap, ttm_fcf, 1.0) if (ttm_fcf or 0) > 0 else None

    # Descriptive quality label (rules published alongside)
    if ttm_ni is None:
        quality = "UNKNOWN"
    elif ttm_ni > 0:
        quality = "PROFITABLE_CASHGEN" if (ttm_fcf or 0) > 0 else "PROFITABLE"
    else:
        burn = abs(ttm_fcf) if (ttm_fcf or 0) < 0 else None
        if (net_debt is not None and net_debt < 0) or (
                burn and cash is not None and cash >= 2 * burn):
            quality = "UNPROFITABLE_FUNDED"
        elif burn and cash is not None and cash < burn:
            quality = "UNPROFITABLE_STRESSED"
        else:
            quality = "UNPROFITABLE_FUNDED" if burn is None else "UNPROFITABLE_WATCH"

    return {
        "ticker": t,
        "quarters_available": len(income),
        "latest_statement_date": income[0].get("date") if income else None,
        "ttm_revenue": ttm_rev,
        "rev_growth_qoq_pct": rev_growth_qoq,
        "rev_growth_3q_pct": rev_growth_3q,
        "gross_margin_pct": _ratio(ttm_gross, ttm_rev),
        "operating_margin_pct": _ratio(ttm_opinc, ttm_rev),
        "net_margin_pct": _ratio(ttm_ni, ttm_rev),
        "ttm_net_income": ttm_ni,
        "ttm_fcf": ttm_fcf,
        "ttm_ocf": ttm_ocf,
        "ttm_sbc": ttm_sbc,
        "cash_and_st_investments": cash,
        "total_debt": total_debt,
        "net_debt": net_debt,
        "dilution_3q_pct": dilution_pct,
        "market_cap": market_cap,
        "ps_ttm": ps,
        "pe_ttm": pe,
        "p_fcf_ttm": pfcf,
        "earnings_date": earnings_date,
        "analyst_estimate_trend": None,        # no cached artifact — honest null
        "institutional_ownership": None,       # no cached artifact — honest null
        "quality_label": quality,
        "quality_rules": FUNDAMENTAL_QUALITY_RULES,
        "growth_caveat": ("Growth is q/q and vs-3-quarters-ago; true YoY is "
                          "not computable from the 4 cached quarters."),
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 6: research journal (append-only manual notes) ────────────────────
#
# The ONLY write path in the entire dashboard, and it touches exactly one
# file: data/research/journal.jsonl (append-only, one JSON object per line).
# Write authorization lives in the server layer; this module only validates,
# sanitizes, reads, and appends.

import re as _re
import uuid as _uuid

JOURNAL_STATUSES = ["NEEDS_REVIEW", "WATCHING", "REJECTED_RESEARCH",
                    "FOLLOW_UP", "DATA_QUALITY_CONCERN", "ARCHIVED"]
JOURNAL_LIMITS = {
    "ticker_max": 12, "title_max": 120, "note_max": 5000,
    "tags_max": 12, "tag_len_max": 32, "source_view_max": 64,
}
_TICKER_RE = _re.compile(r"^[A-Z0-9.\-]{1,12}$")
_TAG_RE = _re.compile(r"^[A-Za-z0-9_\-. ]{1,32}$")
# Whitelisted evidence-snapshot keys (anything else is dropped on write)
_SNAPSHOT_KEYS = ["label", "scanner_category", "source", "sector",
                  "sector_etf", "data_quality", "forward_5d", "forward_10d",
                  "vs_spy", "vs_qqq", "vs_sector", "fundamental_quality"]


def _strip_html(s: str) -> str:
    """Remove tags and neutralize angle brackets so stored notes can never
    carry markup into any renderer."""
    s = _re.sub(r"<[^>]*>", " ", s)
    return s.replace("<", "(").replace(">", ")").strip()


def validate_journal_entry(payload: Any) -> tuple:
    """Validate + sanitize a journal POST body.  Returns (entry, None) on
    success or (None, error_message)."""
    if not isinstance(payload, dict):
        return None, "payload must be a JSON object"

    ticker = str(payload.get("ticker") or "").strip().upper()
    if not _TICKER_RE.match(ticker):
        return None, ("ticker required: uppercase letters/numbers/dot/dash, "
                      f"max {JOURNAL_LIMITS['ticker_max']} chars")

    title = _strip_html(str(payload.get("title") or ""))
    if not title or len(title) > JOURNAL_LIMITS["title_max"]:
        return None, f"title required, max {JOURNAL_LIMITS['title_max']} chars"

    note = _strip_html(str(payload.get("note") or ""))
    if not note or len(note) > JOURNAL_LIMITS["note_max"]:
        return None, f"note required, max {JOURNAL_LIMITS['note_max']} chars"

    status = str(payload.get("status") or "").strip().upper()
    if status not in JOURNAL_STATUSES:
        return None, f"status must be one of {JOURNAL_STATUSES}"

    raw_tags = payload.get("tags") or []
    if not isinstance(raw_tags, list) or len(raw_tags) > JOURNAL_LIMITS["tags_max"]:
        return None, f"tags must be a list of at most {JOURNAL_LIMITS['tags_max']}"
    tags = []
    for t in raw_tags:
        t = _strip_html(str(t))
        if not _TAG_RE.match(t):
            return None, "invalid tag (max 32 chars; letters/numbers/_-. )"
        tags.append(t)

    source_view = _strip_html(str(payload.get("source_view") or ""))[
        :JOURNAL_LIMITS["source_view_max"]] or None

    snapshot = None
    raw_snap = payload.get("evidence_snapshot")
    if isinstance(raw_snap, dict):
        snapshot = {}
        for k in _SNAPSHOT_KEYS:
            v = raw_snap.get(k)
            if v is None:
                snapshot[k] = None
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                snapshot[k] = v
            else:
                snapshot[k] = _strip_html(str(v))[:120]

    return {
        "id": str(_uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": None,
        "ticker": ticker,
        "title": title,
        "note": note,
        "status": status,
        "tags": tags,
        "source_view": source_view,
        "evidence_snapshot": snapshot,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }, None


def append_journal_entry(entry: Dict[str, Any],
                         store: Optional[ArtifactStore] = None) -> None:
    """Append one validated entry.  This is the dashboard's only write and
    it may only ever touch journal_jsonl."""
    store = store or ArtifactStore()
    path = store.journal_jsonl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_journal(store: Optional[ArtifactStore] = None,
                 ticker: Optional[str] = None,
                 status: Optional[str] = None,
                 tag: Optional[str] = None,
                 limit: int = 100) -> Dict[str, Any]:
    """Read journal entries, newest first, with simple filters."""
    store = store or ArtifactStore()
    path = store.journal_jsonl
    entries: List[Dict[str, Any]] = []
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            entries = []

    if ticker:
        t = ticker.strip().upper()
        entries = [e for e in entries if str(e.get("ticker") or "").upper() == t]
    if status:
        s = status.strip().upper()
        entries = [e for e in entries if str(e.get("status") or "").upper() == s]
    if tag:
        tg = tag.strip().lower()
        entries = [e for e in entries
                   if tg in [str(x).lower() for x in (e.get("tags") or [])]]

    entries.sort(key=lambda e: str(e.get("created_at") or ""), reverse=True)
    try:
        limit = max(1, min(int(limit), 1000))
    except Exception:
        limit = 100
    return {
        "entries": entries[:limit],
        "count": len(entries),
        "journal_path": "data/research/journal.jsonl",
        "statuses": JOURNAL_STATUSES,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }
