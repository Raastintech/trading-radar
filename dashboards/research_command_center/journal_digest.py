"""Research Journal daily digest builder — Phase 6 journal format.

Builds exactly one deterministic "Daily Research Digest — YYYY-MM-DD" note
from the latest cached research artifacts so the operator does not need to
read every report manually.

Doctrine:
  - RESEARCH_ONLY.  No trade language, no execution, no promotion of ideas.
  - CACHE-ONLY.  Reads JSON sidecars, docs, and logs already written by the
    research cycle; never calls providers, never fetches live data.
  - CRED-FREE.  No dependency on the env-validated config module.
  - APPEND-ONLY.  The only write path in this module is an explicit append
    to data/research/journal.jsonl; nothing else is ever touched.
  - HONEST.  Missing artifacts surface as MISSING_ARTIFACT warnings and
    null snapshot fields — never fabricated.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dashboards.research_command_center.data_adapter import (
    ArtifactStore,
    RESEARCH_ONLY_FOOTER,
    _load_json,
    build_fundamentals,
    build_status,
)

DIGEST_TICKER = "DIGEST"
DIGEST_SOURCE_VIEW = "journal_digest_script"
DIGEST_TITLE_PREFIX = "Daily Research Digest — "
BASE_TAGS = ["daily-digest", "research-summary", "data-quality",
             "forward-evidence"]

# Stale/suspect skips above this fraction of the universe are a material
# data-quality concern (display/status only — changes no scanner behavior).
SKIP_FRACTION_CONCERN = 0.05

# Immature verdicts that add the needs-more-data tag.
IMMATURE_VERDICTS = {"NEED_MORE_DATA", "INCONCLUSIVE"}

# Days after the moment-of-truth restatement before post-fix forward
# evidence is described as maturing rather than too early.
POST_FIX_EARLY_DAYS = 14

# Display-only sanity threshold. Operating or gross margin above revenue
# usually means the cached statements need normalization before the
# fundamental overlay is reliable.
FUNDAMENTAL_MARGIN_ANOMALY_PCT = 100.0

# A dilution swing this large is far more likely a provider reporting gap
# than a real buyback — mirrors high_conviction_alpha.SUSPECT_BUYBACK_3Q_PCT
# so the review queue flags the same tickers HC already treats as
# data-suspect (2026-07-30: RYAN dilution_3q_pct -52.7%/3q).
DILUTION_DATA_SUSPECT_PCT = -25.0


# ── small formatting helpers ─────────────────────────────────────────────────


def _fmt(v: Any, suffix: str = "") -> str:
    if v is None:
        return "n/a"
    return f"{v}{suffix}"


def _join(tickers: List[str], cap: int = 8) -> str:
    tickers = [str(t) for t in tickers if t]
    if not tickers:
        return "none"
    shown = ", ".join(tickers[:cap])
    extra = len(tickers) - cap
    return shown + (f" (+{extra} more)" if extra > 0 else "")


def _dedupe(seq: List[str]) -> List[str]:
    seen, out = set(), []
    for t in seq:
        t = str(t or "").upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _docs_and_logs_inventory(store: ArtifactStore) -> Dict[str, bool]:
    """Presence of the text-report companions (read-only existence probe)."""
    root = store.root
    return {
        "docs/research/NIGHTLY_OPERATOR_SUMMARY.md":
            (root / "docs" / "research" / "NIGHTLY_OPERATOR_SUMMARY.md").exists(),
        "docs/research/DAILY_ALPHA_RADAR_REPORT.md":
            (root / "docs" / "research" / "DAILY_ALPHA_RADAR_REPORT.md").exists(),
        "logs/research_scanner_latest.txt":
            (root / "logs" / "research_scanner_latest.txt").exists(),
        "logs/research_universe_build_latest.txt":
            (root / "logs" / "research_universe_build_latest.txt").exists(),
    }


# ── input collection ─────────────────────────────────────────────────────────


def collect_inputs(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Load every artifact the digest reads.  Missing files come back as
    None and are reported, never fabricated."""
    store = store or ArtifactStore()
    summary = _load_json(store.summary_json)
    radar = _load_json(store.radar_json)
    scanner = _load_json(store.scanner_json)
    forward = _load_json(store.forward_json)
    universe = _load_json(store.universe_json)
    truth = _load_json(store.moment_of_truth_json)
    programs = _load_json(store.research_programs_json)
    quarantine_causes = _load_json(store.quarantine_report_json)
    latest_scan = _load_json(store.latest_scan_programs_json)
    high_conviction = _load_json(store.high_conviction_json)
    emerging_outlier = _load_json(store.emerging_outlier_json)
    filter_audit = _load_json(store.filter_audit_json)
    options_coverage = _load_json(store.options_coverage_json)
    recall_diagnostics = _load_json(store.scanner_recall_diagnostics_json)
    forward_milestones = _load_json(store.forward_milestones_json)
    cohort_attribution = _load_json(store.cohort_attribution_json)
    research_operating_policy = _load_json(store.research_operating_policy_json)
    alpha_root_cause = _load_json(store.alpha_root_cause_json)
    alpha_focus = _load_json(store.alpha_focus_json)

    missing = [name for name, obj in [
        ("nightly_operator_summary_latest.json", summary),
        ("daily_alpha_radar_latest.json", radar),
        ("research_scanner_latest.json", scanner),
        ("research_forward_latest.json", forward),
        ("research_universe_build_latest.json", universe),
        ("moment_of_truth_2026_06_27.json", truth),
    ] if obj is None]
    # research_program_validation_latest.json is intentionally NOT in the
    # alarm list — section 4b reports its absence with the rerun command,
    # and a missing Phase-5 sidecar must not flip the digest status.

    inventory = _docs_and_logs_inventory(store)
    missing += [name for name, present in inventory.items() if not present]

    return {
        "store": store,
        "status": build_status(store),
        "summary": summary,
        "radar": radar,
        "scanner": scanner,
        "forward": forward,
        "universe": universe,
        "truth": truth,
        "programs": programs,
        "quarantine_causes": quarantine_causes,
        "latest_scan": latest_scan,
        "high_conviction": high_conviction,
        "emerging_outlier": emerging_outlier,
        "filter_audit": filter_audit,
        "options_coverage": options_coverage,
        "recall_diagnostics": recall_diagnostics,
        "forward_milestones": forward_milestones,
        "cohort_attribution": cohort_attribution,
        "research_operating_policy": research_operating_policy,
        "alpha_root_cause": alpha_root_cause,
        "alpha_focus": alpha_focus,
        "missing": missing,
    }


def _top_review_candidates(inputs: Dict[str, Any], top_n: int) -> List[str]:
    """Ordered, deduped review list: high-priority first, then the nightly
    best-research names, then scanner top scores."""
    summary = inputs["summary"] or {}
    scanner = inputs["scanner"] or {}
    alpha = summary.get("alpha_snapshot") or {}
    best = summary.get("best_research_names") or {}

    ordered: List[str] = list(alpha.get("high_priority_tickers") or [])
    for group in ("primary", "secondary", "watchlist"):
        ordered += [r.get("ticker") for r in best.get(group) or []]

    rows = sorted(scanner.get("watchlist") or [],
                  key=lambda w: w.get("research_score") or 0, reverse=True)
    ordered += [w.get("ticker") for w in rows]
    return _dedupe(ordered)[:max(1, top_n)]


def _reset_reclaim(inputs: Dict[str, Any]) -> List[str]:
    alpha = (inputs["summary"] or {}).get("alpha_snapshot") or {}
    tickers = list(alpha.get("reset_watch_tickers") or []) + \
        list(alpha.get("reclaim_watch_tickers") or [])
    if not tickers:
        pri = (inputs["radar"] or {}).get("priority_tickers") or {}
        tickers = list(pri.get("RESET_WATCH") or []) + \
            list(pri.get("RECLAIM_WATCH") or [])
    return _dedupe(tickers)


def _best_research_tickers(inputs: Dict[str, Any]) -> List[str]:
    """Current nightly board order, without applying any new ranking."""
    best = ((inputs.get("summary") or {}).get("best_research_names") or {})
    ordered: List[str] = []
    for group in ("primary", "secondary", "watchlist"):
        ordered += [r.get("ticker") for r in best.get(group) or []]
    return _dedupe(ordered)


# ── status + tags ────────────────────────────────────────────────────────────


def _data_quality_concern(inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Material data-quality problems (missing artifacts, stale sidecars,
    failed nightly, heavy stale/suspect skips)."""
    status = inputs["status"]
    scanner = inputs["scanner"] or {}
    concerns: List[str] = []
    if inputs["missing"]:
        concerns.append(
            f"MISSING_ARTIFACT: {len(inputs['missing'])} artifact(s) absent "
            f"({_join(inputs['missing'], cap=4)})")
    if status.get("data_freshness") == "STALE":
        concerns.append(
            f"Scanner sidecar stale ({_fmt(status.get('scanner_age_hours'), 'h')} old)")
    if status.get("nightly_status") not in ("PASS", "UNKNOWN"):
        concerns.append(f"Nightly status {status.get('nightly_status')}")
    universe_size = scanner.get("universe_size") or 0
    skipped = (scanner.get("stale_price_skipped_count") or 0) + \
        (scanner.get("data_suspect_skipped_count") or 0)
    if universe_size and skipped / universe_size > SKIP_FRACTION_CONCERN:
        concerns.append(
            f"Stale+suspect skips {skipped}/{universe_size} exceed "
            f"{SKIP_FRACTION_CONCERN:.0%} of universe")
    # P0 scan integrity (2026-07-10): mixed-session comparisons are a
    # CRITICAL data-quality flaw — a scan whose benchmarks are off the
    # required session, or that lost material coverage to SESSION_MISMATCH
    # exclusions, must surface as a concern regardless of artifact age.
    si = status.get("scan_integrity") or {}
    if si.get("readiness") == "BLOCKED":
        concerns.append(
            "CRITICAL: benchmark session mismatch — scan is BLOCKED "
            f"(required session {si.get('required_market_session')}, "
            f"benchmarks {si.get('benchmark_session')})")
    mism = si.get("session_mismatch_excluded") or 0
    if universe_size and mism / universe_size > SKIP_FRACTION_CONCERN:
        concerns.append(
            f"SESSION_MISMATCH exclusions {mism}/{universe_size} exceed "
            f"{SKIP_FRACTION_CONCERN:.0%} of universe")
    # The frozen legacy snapshot itself is expected (gated consumers degrade
    # visibly) — it becomes a concern only when a scan ran without the
    # session gate, because then nothing guards against stale context.
    legacy = ((si.get("stale_source_consumers") or {})
              .get("legacy_universe_snapshot") or {})
    if legacy.get("stale") and si.get("readiness") in ("UNGATED", "UNKNOWN"):
        concerns.append(
            "CRITICAL: scan ran ungated while frozen universe snapshot is "
            f"stale (as-of {legacy.get('source_as_of')}, "
            f"{legacy.get('source_age_sessions')} sessions old)")
    return bool(concerns), concerns


def _select_status(inputs: Dict[str, Any],
                   high_priority: List[str]) -> Tuple[str, List[str]]:
    concern, concerns = _data_quality_concern(inputs)
    warnings = [
        warning for warning in
        ((inputs["summary"] or {}).get("warnings") or [])
        if not _is_legacy_recall_warning(warning)
    ]
    verdict = inputs["status"].get("tracker_verdict") or "UNKNOWN"
    phase4b_blocked = (inputs["status"].get("phase_4b") or {}).get(
        "status") != "UNBLOCKED"
    if concern:
        return "DATA_QUALITY_CONCERN", concerns
    if high_priority or warnings:
        return "NEEDS_REVIEW", concerns
    if verdict != "PROMISING" or phase4b_blocked:
        return "FOLLOW_UP", concerns
    return "WATCHING", concerns


def _build_tags(inputs: Dict[str, Any], high_priority: List[str]) -> List[str]:
    tags = list(BASE_TAGS)
    if high_priority:
        tags.append("high-priority")
    if (inputs["status"].get("phase_4b") or {}).get("status") != "UNBLOCKED":
        tags.append("phase4b-blocked")
    if (inputs["status"].get("tracker_verdict") or "").upper() in IMMATURE_VERDICTS:
        tags.append("needs-more-data")
    return tags


# ── idempotency key ──────────────────────────────────────────────────────────


def compute_digest_key(inputs: Dict[str, Any], date_s: str) -> str:
    """Deterministic key: date + hash of key artifact timestamps/counts."""
    basis = {
        "summary_at": (inputs["summary"] or {}).get("generated_at"),
        "radar_at": (inputs["radar"] or {}).get("generated_at"),
        "scanner_at": (inputs["scanner"] or {}).get("generated_at"),
        "forward_at": (inputs["forward"] or {}).get("generated_at"),
        "universe_at": (inputs["universe"] or {}).get("generated_at"),
        "total_candidates": (inputs["radar"] or {}).get("total_candidates"),
        "total_tracked": (inputs["forward"] or {}).get("total_history_entries"),
    }
    blob = json.dumps(basis, sort_keys=True).encode("utf-8")
    return f"daily_digest:{date_s}:{hashlib.sha256(blob).hexdigest()[:12]}"


def find_existing_digest(store: ArtifactStore,
                         digest_key: str) -> Optional[Dict[str, Any]]:
    path = store.journal_jsonl
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("digest_key") == digest_key:
                return entry
    except Exception:
        return None
    return None


# ── note body ────────────────────────────────────────────────────────────────


def _section_data_quality(inputs: Dict[str, Any],
                          concerns: List[str]) -> List[str]:
    status = inputs["status"]
    scanner = inputs["scanner"] or {}
    universe_size = scanner.get("universe_size")
    stale = scanner.get("stale_price_skipped_count")
    suspect = scanner.get("data_suspect_skipped_count")
    refresh_healthy = "unknown"
    if universe_size:
        frac = ((stale or 0) + (suspect or 0)) / universe_size
        refresh_healthy = ("healthy" if frac <= SKIP_FRACTION_CONCERN
                           else f"degraded ({frac:.1%} of universe skipped)")
    si = status.get("scan_integrity") or {}
    aligned_pct = si.get("same_session_coverage_pct")
    lines = [
        "## 1. Data Quality",
        f"- Freshness: {status.get('data_freshness')} "
        f"(scanner artifact age {_fmt(status.get('scanner_age_hours'), 'h')})",
        f"- Required market session: {si.get('required_market_session') or 'unknown'} "
        f"| scan readiness: {si.get('readiness') or 'unknown'} "
        f"| benchmarks aligned: {si.get('benchmarks_aligned')}",
        f"- Same-session candidate alignment: "
        f"{f'{aligned_pct}%' if aligned_pct is not None else 'unknown'} "
        f"| session-mismatch excluded: {_fmt(si.get('session_mismatch_excluded'))}",
        f"- Stale-price skipped: {_fmt(stale)} | suspect-feed skipped: "
        f"{_fmt(suspect)} | quarantined: {_fmt(status.get('quarantine_count'))}",
        f"- Missing artifacts: {len(inputs['missing'])}"
        + (f" ({_join(inputs['missing'], cap=4)})" if inputs["missing"] else ""),
        f"- Price refresh: {refresh_healthy}",
    ]
    legacy = ((si.get("stale_source_consumers") or {})
              .get("legacy_universe_snapshot") or {})
    if legacy.get("stale") is not None:
        lines.append(
            "- Frozen-source status: legacy universe snapshot "
            + (f"STALE (as-of {legacy.get('source_as_of')}, "
               f"{legacy.get('source_age_sessions')} sessions) — consumers gated"
               if legacy.get("stale") else "fresh"))
    cov = si.get("universe_coverage_pct")
    if cov is not None:
        newly = si.get("newly_admitted_today") or []
        lines.append(
            f"- Discovery-universe coverage: {cov}% of eligible market"
            + (f" | newly admitted: {_join(newly, cap=6)}" if newly else ""))
    # Quarantine causes (when the cause report exists): stating the
    # per-cause breakdown here keeps the journal audit from re-proposing
    # a quarantine diagnostic that already runs nightly.
    qrep = inputs.get("quarantine_causes")
    if qrep and qrep.get("n_quarantined"):
        cause_counts = qrep.get("cause_counts") or {}
        cause_str = ", ".join(f"{v}x {k}"
                              for k, v in sorted(cause_counts.items()))
        lines.append(f"- Quarantine causes: {cause_str} — see "
                     "quarantine-cause report")
    # Options overlay is a STRUCTURAL coverage gate (the snapshot-collector
    # universe is capped by design as a provider-budget decision), never a
    # candidate-level deficiency.  Stating it here as a first-class data-
    # quality line keeps the state visible even when the warning list is
    # full, and keeps the journal audit from re-flagging it as a defect.
    lines.append(_options_overlay_line(inputs))
    warnings = [
        warning for warning in
        ((inputs["summary"] or {}).get("warnings") or [])
        if not _is_legacy_recall_warning(warning)
    ]
    for w in (concerns + warnings)[:5]:
        lines.append(f"- Warning: {w}")
    if not concerns and not warnings:
        lines.append("- No major warnings.")
    return lines


def _is_legacy_recall_warning(warning: Any) -> bool:
    text = str(warning or "").lower()
    return "recall" in text and ("legacy" in text or "decommissioned" in text)


def _options_overlay_line(inputs: Dict[str, Any]) -> str:
    """One-line options-overlay status for the data-quality section.
    Display only — reads the options-coverage sidecar; missing sidecar is
    reported honestly, never fabricated."""
    oc = inputs.get("options_coverage")
    if not oc or not oc.get("overlay_state"):
        return ("- Options overlay: state unknown (options-coverage report "
                "missing) — run ./scripts/run_research_cycle.sh nightly")
    state = str(oc.get("overlay_state"))
    cov = oc.get("coverage_pct")
    covered = oc.get("covered")
    total = oc.get("watchlist_total")
    line = (f"- Options overlay: {state} — watchlist coverage "
            f"{_fmt(cov, '%')} ({_fmt(covered)}/{_fmt(total)} names)")
    if state != "ENABLED":
        line += (" | structural gate: snapshot-collector universe is capped "
                 "by design (provider-budget decision), NOT a candidate-"
                 "level defect — see options-coverage report")
    return line


def _tier_tagged(tickers: List[str], store) -> List[str]:
    """Suffix names whose fundamental tier is below PROFITABLE_CASHGEN so
    review order is visible in the name lists themselves.  Display only —
    no score, ranking, or scanner behavior reads this."""
    if store is None:
        return list(tickers)
    out = []
    for t in tickers:
        tag = None
        try:
            f = build_fundamentals(t, store)
            label = f.get("quality_label")
            if (not f.get("fallback") and label
                    and label not in ("PROFITABLE_CASHGEN", "UNKNOWN")):
                tag = label
        except Exception:
            tag = None
        out.append(f"{t} ({tag})" if tag else str(t))
    return out


def _section_scanner(inputs: Dict[str, Any], top: List[str],
                     reset_reclaim: List[str]) -> List[str]:
    radar = inputs["radar"] or {}
    summary = inputs["summary"] or {}
    scanner = inputs["scanner"] or {}
    alpha = summary.get("alpha_snapshot") or {}
    counts = radar.get("priority_counts") or {}
    high = list(alpha.get("high_priority_tickers")
                or radar.get("priority_tickers", {}).get(
                    "HIGH_PRIORITY_RESEARCH") or [])
    quarantine = (counts.get("DATA_QUARANTINE")
                  if counts.get("DATA_QUARANTINE") is not None
                  else alpha.get("data_quarantine_count"))
    store = inputs.get("store")
    lines = [
        "## Scanner / Why Names Appeared (discovery only)",
        f"- Total candidates: {_fmt(radar.get('total_candidates') or scanner.get('watchlist_size'))}",
        f"- Broad-scanner candidates (discovery only — see Alpha Focus / "
        f"Operator Focus above for prioritized names): "
        f"{_join(_tier_tagged(high, store))}",
        f"- Reset/reclaim watch: {_join(reset_reclaim)}",
        f"- Extended/crowded: {_fmt(counts.get('EXTENDED_CROWDED') or alpha.get('extended_crowded_count'))}"
        f" | data quarantine / young listing: {_fmt(quarantine)}",
        f"- Top research names: {_join(_tier_tagged(top, store))}",
        f"- Live-board evidence: {_fmt(scanner.get('watchlist_size'))} current "
        f"candidates as of {scanner.get('market_as_of_date') or 'unknown'}; "
        "current why-appeared evidence is shown below.",
    ]
    by_ticker = {str(w.get("ticker") or "").upper(): w
                 for w in scanner.get("watchlist") or []}
    reasons = 0
    for t in top:
        row = by_ticker.get(t)
        why = (row or {}).get("why_appeared")
        if why and reasons < 5:
            lines.append(f"- {t}: {why}")
            reasons += 1
    if not reasons:
        lines.append("- Why-appeared detail unavailable (scanner artifact missing).")
    return lines


# Sector-ETF symbol -> sector-name fragment, for comparing the market
# context's ETF-denominated leading/weak lists against company sector
# names.  Local copy — the hard-separation rule forbids dashboards
# importing research-layer modules.  Fragments match by case-insensitive
# substring so "Health" covers both "Healthcare" and "Health Care".
_SECTOR_ETF_NAMES = {
    "XLK": "Technology", "XLU": "Utilities", "XLB": "Materials",
    "XLE": "Energy", "XLF": "Financial", "XLV": "Health",
    "XLI": "Industrials", "XLY": "Consumer Cyclical",
    "XLP": "Consumer Defensive", "XLC": "Communication Services",
    "XLRE": "Real Estate", "SMH": "Technology",
}


def _sectors_matching(etfs: List[str], sector_names: List[str]) -> List[str]:
    """Sector names (from company profiles) that fall under any of the
    given sector-ETF symbols.  Handles the ETF-symbol vs sector-name
    namespace mismatch that made the old alignment check a no-op."""
    out: List[str] = []
    for name in sector_names:
        for etf in etfs:
            fragment = _SECTOR_ETF_NAMES.get(str(etf).upper(), str(etf))
            if fragment.lower() in name.lower() and name not in out:
                out.append(name)
    return out


def _alignment_label(leading: List[str], weak: List[str],
                     top_sectors: List[str]) -> str:
    """Honest alignment verdict.  The old logic compared sector NAMES
    against ETF SYMBOLS (never matched -> always 'aligned') and claimed
    alignment even when there were no leading sectors to align with.
    Display-only: no ranking, scoring, or gate reads this label."""
    if not top_sectors or not (leading or weak):
        return "unknown"
    in_weak = _sectors_matching(weak, top_sectors)
    in_leading = _sectors_matching(leading, top_sectors)
    if not leading:
        # Nothing to align WITH — say so, and quantify the weak overlap
        # instead of defaulting to a reassuring label.
        if in_weak:
            return (f"not_assessable — no leading sectors; "
                    f"{len(in_weak)} of {len(top_sectors)} top-name "
                    f"sectors sit in weak sectors ({', '.join(in_weak)})")
        return ("not_assessable — no leading sectors to align with "
                "(no top-name sector is weak)")
    if in_weak and in_leading:
        return (f"mixed — leaders: {', '.join(in_leading)}; weak: "
                f"{', '.join(in_weak)}")
    if in_weak:
        return ("misaligned — top names sit in weak sectors: "
                + ", ".join(in_weak))
    if in_leading:
        return "aligned — top names sit in leading sectors: " \
            + ", ".join(in_leading)
    return "unconfirmed — top-name sectors are neither leading nor weak"


def _alignment_for_top(inputs: Dict[str, Any],
                       top: List[str]) -> Tuple[List[str], str]:
    """Top-name sectors + honest alignment label — shared by the sector
    section and the final finding so both always agree."""
    market = (inputs["summary"] or {}).get("market_context") or {}
    scanner = inputs["scanner"] or {}
    leading = market.get("leading_sectors") or []
    weak = market.get("weak_sectors") or []
    by_ticker = {str(w.get("ticker") or "").upper(): w
                 for w in scanner.get("watchlist") or []}
    top_sectors: List[str] = []
    for t in top:
        sector = str((by_ticker.get(t) or {}).get("sector") or "")
        if sector and sector not in top_sectors:
            top_sectors.append(sector)
    return top_sectors, _alignment_label(leading, weak, top_sectors)


def _section_sector_regime(inputs: Dict[str, Any], top: List[str]) -> List[str]:
    market = (inputs["summary"] or {}).get("market_context") or {}
    leading = market.get("leading_sectors") or []
    weak = market.get("weak_sectors") or []
    top_sectors, aligned = _alignment_for_top(inputs, top)
    lines = [
        "## 3. Sector / Regime",
        f"- Regime: {market.get('regime') or 'UNKNOWN'} "
        f"(confidence {market.get('confidence') or 'n/a'}; "
        f"bias 5d {market.get('bias_5d') or 'n/a'} / 10d "
        f"{market.get('bias_10d') or 'n/a'} / 30d {market.get('bias_30d') or 'n/a'})",
        f"- Leading sectors: {_join(leading, cap=5)} | weak sectors: {_join(weak, cap=5)}",
        f"- Top-name sectors: {_join(top_sectors, cap=6)} — alignment: {aligned}",
    ]
    posture = market.get("research_posture")
    if posture:
        lines.append(f"- Posture: {posture}")
    return lines


# Horizons whose first matured episode the ETA readout projects.  20d is
# the shortest still-unmatured horizon; 45d/60d are the swing horizons the
# audit repeatedly flags as having zero episodes.
_ETA_HORIZONS_TD = (20, 45, 60)
# An entry is "approaching" a horizon when it matures within this many
# further trading days.
_ETA_APPROACHING_TD = 5


def _load_spy_trading_dates(store: ArtifactStore) -> List[str]:
    """Real trading-day calendar (SPY close dates), sorted ascending.

    Anchoring the ETA readout to SPY's own cached bars keeps it agreeing
    with the resolver's calendar-mature check (which uses the same
    anchor) instead of a naive weekday count that ignores market
    holidays. Empty on any read failure — callers fall back to
    weekday-only approximation."""
    import pandas as pd  # lazy — ETA path only

    path = store.prices_dir / "SPY.parquet"
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
        date_col = next((c for c in ("date", "Date", "timestamp",
                                     "Timestamp") if c in df.columns), None)
        dates = (df[date_col].astype(str).str[:10] if date_col
                 else df.index.astype(str).str[:10])
        return sorted(dates.tolist())
    except Exception:
        return []


def _add_trading_days(d: date, n: int, spy_dates: Optional[List[str]] = None) -> date:
    """Add n trading days to d.  Uses the real SPY trading calendar for
    the portion already covered by cached price history (exact — matches
    the resolver's own calendar-mature check); falls back to weekday-only
    stepping (approximate, no holiday calendar) once it runs past the
    cached range, since future market holidays aren't knowable in
    advance."""
    spy_dates = spy_dates or []
    d_s = d.isoformat()
    idx = next((i for i, sd in enumerate(spy_dates) if sd >= d_s), None)
    if idx is not None:
        if idx + n < len(spy_dates):
            return datetime.strptime(spy_dates[idx + n], "%Y-%m-%d").date()
        remaining = n - (len(spy_dates) - 1 - idx)
        cur = datetime.strptime(spy_dates[-1], "%Y-%m-%d").date()
    else:
        remaining, cur = n, d
    added = 0
    while added < remaining:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


def _trading_days_between(a: date, b: date, spy_dates: Optional[List[str]] = None) -> int:
    """Elapsed trading days in (a, b].  Exact against the real SPY
    calendar for the cached range; weekday-only approximation for any
    portion of (a, b] beyond the last cached bar."""
    if b <= a:
        return 0
    spy_dates = spy_dates or []
    a_s, b_s = a.isoformat(), b.isoformat()
    counted = [sd for sd in spy_dates if a_s < sd <= b_s]
    if spy_dates and b_s <= spy_dates[-1]:
        return len(counted)
    days = len(counted)
    cur = (datetime.strptime(spy_dates[-1], "%Y-%m-%d").date()
           if spy_dates and spy_dates[-1] > a_s else a)
    while cur < b:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def _read_appearance_dates(path: Path) -> List[date]:
    """appearance_date values from a history JSONL (read-only; missing or
    malformed rows are skipped, never fabricated)."""
    if not path.exists():
        return []
    out: List[date] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            d = str(entry.get("appearance_date") or "")[:10]
            out.append(datetime.strptime(d, "%Y-%m-%d").date())
        except Exception:
            continue
    return out


def _maturity_eta_lines(inputs: Dict[str, Any],
                        today: Optional[date] = None) -> List[str]:
    """Maturity-ETA readout: when the first 20d/45d/60d tracker episodes
    and the first 10d shortlist episodes are expected to mature, plus how
    many tracked entries are approaching each horizon.  Makes the
    zero-episode state a dated expectation instead of an open question."""
    store = inputs.get("store")
    if store is None:
        return []
    today = today or datetime.now(timezone.utc).date()
    lines: List[str] = []
    spy_dates = _load_spy_trading_dates(store)

    appearances = _read_appearance_dates(store.history_jsonl)
    if appearances:
        earliest = min(appearances)
        mbh = ((inputs["forward"] or {}).get("overall") or {}).get(
            "matured_by_horizon") or {}
        parts = []
        for h in _ETA_HORIZONS_TD:
            if (mbh.get(f"{h}d") or 0) > 0:
                continue  # already maturing — no ETA needed
            eta = _add_trading_days(earliest, h, spy_dates)
            approaching = sum(
                1 for a in appearances
                if h - _ETA_APPROACHING_TD
                <= _trading_days_between(a, today, spy_dates) < h)
            eta_s = (f"≈{eta.isoformat()}" if eta > today
                     else f"due (earliest cohort passed {h}td "
                          f"{eta.isoformat()} — resolution pending)")
            parts.append(f"{h}d {eta_s}"
                         + (f" ({approaching} entries within "
                            f"{_ETA_APPROACHING_TD}td)" if approaching else ""))
        if parts:
            label = ("first episode, real trading calendar" if spy_dates
                     else "first episode, weekday-approx — SPY calendar "
                          "unavailable")
            lines.append(f"- Maturity ETA ({label}): " + " | ".join(parts))

    hc_dates = _read_appearance_dates(store.high_conviction_history_jsonl)
    if hc_dates:
        fv = _load_json(store.high_conviction_forward_json) or {}
        eta10 = _add_trading_days(min(hc_dates), 10, spy_dates)
        full = ((fv.get("cohorts") or {}).get("full_shortlist") or {})
        matured_10d = (((full.get("10d") or {}).get("raw") or {}).get("n")
                       or 0)
        if matured_10d == 0 and eta10 == today:
            lines.append(
                "- First HC 10d maturity expected today; not yet resolved "
                "in current forward artifact.")
        elif matured_10d == 0 and eta10 < today:
            lines.append(
                f"- First HC 10d maturity was expected {eta10.isoformat()}; "
                "not yet resolved in current forward artifact.")
        elif matured_10d == 0:
            lines.append(
                f"- Shortlist maturity ETA: first 10d episodes "
                f"≈{eta10.isoformat()} "
                f"({fv.get('n_history_rows') or len(hc_dates)} episodes "
                "tracked — verdict needs ≥10 matured)")
    return lines


def _forward_incomplete_warnings(inputs: Dict[str, Any]) -> List[str]:
    forward = inputs.get("forward") or {}
    warnings: List[str] = []
    for msg in (
        [forward.get("incomplete_evidence_warning")]
        + list(forward.get("incomplete_evidence_warnings") or [])
    ):
        msg = str(msg or "").strip()
        if msg and msg not in warnings:
            warnings.append(msg)
    return warnings


def _section_forward(inputs: Dict[str, Any]) -> List[str]:
    status = inputs["status"]
    truth = inputs["truth"] or {}
    fwd = (inputs["forward"] or {}).get("overall") or {}
    phase4b = status.get("phase_4b") or {}
    post_fix = "unknown (moment-of-truth artifact missing)"
    truth_at = str(truth.get("generated_at") or "")[:10]
    if truth_at:
        try:
            days = (datetime.now(timezone.utc).date()
                    - datetime.strptime(truth_at, "%Y-%m-%d").date()).days
            post_fix = (f"{days}d since {truth_at} restatement — "
                        + ("still too early"
                           if days < POST_FIX_EARLY_DAYS else "maturing"))
        except Exception:
            pass
    # Phase 5.1 (A6 fix): 20d+ horizons ARE collected by the tracker;
    # report their real matured counts instead of "not tracked".
    mbh = fwd.get("matured_by_horizon") or {}

    def _mat(h: str) -> str:
        v = mbh.get(h)
        return _fmt(v) if v is not None else "n/a (old artifact)"

    lines = [
        "## 4. Forward Evidence",
        f"- Tracked entries: {_fmt(status.get('total_tracked'))} | "
        f"matured 5d: {_fmt(status.get('matured_5d'))} | matured 10d: "
        f"{_fmt(status.get('matured_10d'))} | matured 20d: {_mat('20d')} "
        f"| matured 45d: {_mat('45d')} | matured 60d: {_mat('60d')}",
        f"- Benchmark readiness: {status.get('benchmark_readiness')} | "
        f"sample status: {_fmt(fwd.get('sample_status'))}",
        f"- Tracker verdict: {status.get('tracker_verdict')}"
        + (f" | moment-of-truth verdict: {truth.get('verdict')}"
           if truth.get("verdict") else ""),
        f"- Phase 4B: {phase4b.get('status') or 'UNKNOWN'} "
        f"({phase4b.get('reason') or 'n/a'})",
        f"- Post-fix evidence: {post_fix}",
    ]
    for warning in _forward_incomplete_warnings(inputs):
        lines.append(f"- {warning}")
    lines += _maturity_eta_lines(inputs)
    lines += _milestone_lines(inputs)
    return lines


def _milestone_lines(inputs: Dict[str, Any]) -> List[str]:
    """Forward-evidence re-audit hook readout.  On the crossing date the
    line leads with RE-AUDIT DUE so the nightly audit re-examines the
    forward verdicts with the newly matured sample; afterwards the
    crossing stays visible as a dated fact."""
    fm = inputs.get("forward_milestones")
    if not fm:
        return []
    lines: List[str] = []
    due = set(fm.get("reaudit_due_for") or [])
    for m in fm.get("milestones") or []:
        if not m.get("crossed"):
            continue
        label = m.get("label") or m.get("id")
        detail = f"{label} (value {m.get('value')}, crossed {m.get('crossed_at')})"
        if m.get("id") in due:
            lines.append(f"- RE-AUDIT DUE — forward-evidence milestone crossed "
                         f"today: {detail} — re-examine tracker / program / "
                         "shortlist verdicts against the newly matured sample")
        else:
            lines.append(f"- Forward-evidence milestone: {detail}")
    return lines


def _section_cohort_attribution(inputs: Dict[str, Any]) -> List[str]:
    ca = inputs.get("cohort_attribution") or {}
    lines = ["## Cohort Attribution"]
    if not ca:
        return lines + [
            "- Cohort attribution sidecar missing; run research/cohort_attribution.py.",
            "- Do not infer cohort-level alpha from the broad forward verdict alone.",
        ]
    dash = ca.get("dashboard_summary") or {}
    drag = dash.get("biggest_drag_cohort") or {}
    best = dash.get("best_current_cohort") or {}
    hc = dash.get("high_conviction") or {}
    eo = dash.get("emerging_outlier") or {}
    warning = dash.get("sample_maturity_warning")
    lines.append(
        f"- Dragging performance: {drag.get('label') or 'n/a'} "
        f"(10d mean {drag.get('mean_return_pct')}, "
        f"win {drag.get('win_rate')}).")
    lines.append(
        f"- Best current cohort: {best.get('label') or 'n/a'} "
        f"(10d mean {best.get('mean_return_pct')}, "
        f"win {best.get('win_rate')}).")
    lines.append(
        f"- High-Conviction 10d sample: {hc.get('matured_count')} "
        f"({hc.get('result') or hc.get('status')}).")
    lines.append(
        f"- Emerging Outlier 10d sample: {eo.get('matured_count')} "
        f"({eo.get('result') or eo.get('status')}).")
    if warning:
        lines.append(f"- Sample maturity: {warning}")
    lines.append(
        "- Do not conclude HC/EO alpha until their own matured samples clear "
        "the pre-registered evidence floor.")
    return lines


def _section_research_operating_policy(inputs: Dict[str, Any]) -> List[str]:
    policy = inputs.get("research_operating_policy") or {}
    lines = ["## Research Operating Policy"]
    if not policy:
        return lines + [
            "- Operating policy sidecar missing; run research/research_operating_policy.py.",
            "- Treat cohort attribution as diagnostic only until policy guidance is available.",
        ]
    dash = policy.get("dashboard_summary") or {}

    def labels(key: str) -> str:
        vals = [str(i.get("label") or "") for i in (dash.get(key) or [])[:3]]
        vals = [v for v in vals if v]
        return ", ".join(vals) if vals else "none"

    lines.append(f"- Operator attention today: {labels("focus_now")}.")
    lines.append(f"- Use caution: {labels("use_caution")}.")
    lines.append(f"- Discovery only: {labels("discovery_only")}.")
    lines.append(f"- Do not conclude yet: {labels("do_not_conclude_yet")}.")
    if dash.get("immature_reminder"):
        lines.append(f"- Immature evidence: {dash.get("immature_reminder")}")
    lines.append(
        "- Policy is presentation guidance only; it does not remove candidates, "
        "alter ranking, change scores, or promote research to signals.")
    return lines


def _section_alpha_root_cause(inputs: Dict[str, Any]) -> List[str]:
    rc = inputs.get("alpha_root_cause") or {}
    lines = ["## Alpha Root-Cause"]
    if not rc or rc.get("fallback"):
        return lines + [
            "- Root-cause audit sidecar missing; run "
            "./scripts/run_research_cycle.sh alpha-failure-root-cause.",
            "- Do not infer a root cause until the audit has run at least once.",
        ]
    dash = rc.get("dashboard_summary") or {}
    lines.append(f"- Top likely failure cause: {dash.get('top_likely_failure_cause') or 'n/a'}.")
    lines.append(f"- Best surviving cohort: {dash.get('best_surviving_cohort') or 'n/a'}.")
    lines.append(f"- Worst harmful cohort: {dash.get('worst_harmful_cohort') or 'n/a'}.")
    lines.append(
        f"- Next decision date: {dash.get('next_decision_date') or 'n/a'} "
        f"(current recommendation: {dash.get('current_recommendation') or 'n/a'}).")
    lines.append(
        "- Diagnostic only — no scanner, scoring, routing, gate, threshold, "
        "factor-weight, High-Conviction, Emerging Outlier, or program-verdict change.")
    return lines


def _section_alpha_focus(inputs: Dict[str, Any]) -> List[str]:
    af = inputs.get("alpha_focus") or {}
    lines = ["## Alpha Focus"]
    if not af or af.get("fallback"):
        return lines + [
            "- Alpha Focus sidecar missing; run ./scripts/run_research_cycle.sh alpha-focus.",
            "- Do not infer a focus list until the filter has run at least once.",
        ]
    counts = af.get("counts") or {}
    lines.append(
        f"- Today ({af.get('market_as_of_date') or 'unknown'}): "
        f"{counts.get('review_now')} review now / {counts.get('higher_risk_eo_review')} higher-risk EO / "
        f"{counts.get('wait_for_reset')} wait-for-reset / {counts.get('deprioritized')} deprioritized "
        f"of {counts.get('total_today')} candidates "
        f"(HC overlap {counts.get('high_conviction_overlap')}, EO overlap {counts.get('emerging_outlier_overlap')}).")
    review_now = [r.get("ticker") for r in (af.get("review_now") or [])[:5]]
    lines.append(f"- Review Now names: {_join(review_now) if review_now else 'none'}.")
    eo_review = [r.get("ticker") for r in (af.get("higher_risk_eo_review") or [])[:5]]
    lines.append(f"- Higher-Risk EO Review names: {_join(eo_review) if eo_review else 'none'}.")
    reasons = af.get("deprioritized_by_reason_summary") or {}
    for reason, summary in sorted(reasons.items()):
        lines.append(f"- Deprioritized ({reason}): {summary.get('count')} — {summary.get('description')}")
    lines.append(
        "- " + (af.get("purpose_statement")
        or "Same-day manual research prioritization layer only — no new score, no new "
           "forward-evidence ledger, no scanner/gate/HC/EO/program-verdict change."))
    lines.append(
        "- Deprioritized names still appear, unaffected, in their original High-Conviction / "
        "Emerging Outlier sections — they are not permanently excluded.")
    return lines


def _section_research_programs(inputs: Dict[str, Any]) -> List[str]:
    """Phase 5.1 — per-program summary so Tactical, Swing, and Long-Term
    findings are never blended into one conclusion.  Reads only the
    program-validation sidecar (cache) plus today's scanner watchlist."""
    lines = ["## 4b. Research Programs"]
    report = inputs.get("programs")
    if not report:
        lines.append("- Program validation artifact missing — run "
                     "./scripts/run_research_cycle.sh research-programs")
        return lines

    programs = report.get("programs") or {}
    label_to: Dict[str, str] = {}
    for pid, p in programs.items():
        for label in p.get("labels") or []:
            label_to[label] = pid
    watchlist = (inputs.get("scanner") or {}).get("watchlist") or []
    counts: Dict[str, int] = {}
    for item in watchlist:
        pid = label_to.get(item.get("watchlist_label") or "", "UNROUTED")
        counts[pid] = counts.get(pid, 0) + 1

    for pid in ("TACTICAL", "SWING", "LONG_TERM"):
        p = programs.get(pid) or {}
        horizons = p.get("horizons") or {}
        matured = " ".join(
            f"{h}d:{v.get('n_episodes')}"
            for h, v in sorted(horizons.items(), key=lambda kv: int(kv[0])))
        gap = (p.get("horizon_coverage") or {}).get(
            "primary_not_collected") or []
        line = (f"- {pid} (hold "
                f"{p.get('expected_holding_period_td') or '?'}td): "
                f"candidates today {counts.get(pid, 0)} | verdict "
                f"{p.get('verdict') or 'UNKNOWN'} | matured episodes "
                f"{matured or 'none'}")
        if gap:
            line += f" | primary horizons not collected: {gap}"
        lines.append(line)
        diag = p.get("diagnostic_signal")
        if diag and diag not in ("NONE", None):
            lines.append(
                f"  - diagnostic read at "
                f"{p.get('diagnostic_horizon_td')}d: {diag}"
                + (f" ({p.get('diagnostic_excess_vs_qqq_pct'):+}% vs QQQ,"
                   f" win {p.get('diagnostic_win_rate_pct')}%)"
                   if p.get("diagnostic_excess_vs_qqq_pct") is not None
                   else ""))
    if counts.get("UNROUTED"):
        lines.append(f"- UNROUTED candidates: {counts['UNROUTED']} "
                     "(labels missing from the program map)")
    return lines


def _metric_anomalies_for(ticker: str,
                          store: Optional[ArtifactStore]) -> List[str]:
    """Fundamental values that require manual normalization before use."""
    if store is None:
        return []
    f = build_fundamentals(ticker, store)
    if f.get("fallback"):
        return []
    anomalies: List[str] = []
    om = f.get("operating_margin_pct")
    if om is not None and om > FUNDAMENTAL_MARGIN_ANOMALY_PCT:
        anomalies.append(f"operating margin {om:.0f}% exceeds revenue")
    gm = f.get("gross_margin_pct")
    if gm is not None and gm > FUNDAMENTAL_MARGIN_ANOMALY_PCT:
        anomalies.append(f"gross margin {gm:.0f}% exceeds revenue")
    return anomalies


def _risk_level_for(inputs: Dict[str, Any], ticker: str) -> str:
    """Existing Business Deterioration Risk from HC/EO artifacts."""
    ticker = str(ticker or "").upper()
    hc = inputs.get("high_conviction") or {}
    eo = inputs.get("emerging_outlier") or {}
    rows = (
        list(hc.get("shortlist") or [])
        + list(hc.get("quality_but_extended") or [])
        + list(hc.get("rejected") or [])
        + list(eo.get("watch") or [])
    )
    for row in rows:
        if str(row.get("ticker") or "").upper() != ticker:
            continue
        risk = (row.get("business_deterioration_risk")
                or row.get("dead_horse_risk"))
        if risk:
            return str(risk).upper()
    return "UNKNOWN"


def _risk_notes_for(inputs: Dict[str, Any], ticker: str) -> List[str]:
    notes = _red_flags_for(ticker, inputs.get("store"))
    if _risk_level_for(inputs, ticker) == "HIGH":
        notes.append("Business Deterioration Risk High")
    if _metric_anomalies_for(ticker, inputs.get("store")):
        notes.append("Fundamental Metric Anomaly")
    return list(dict.fromkeys(notes))


def _priority_red_flag_for(inputs: Dict[str, Any], ticker: str) -> bool:
    """Material red flags for the compact top block, not every caution."""
    notes = _risk_notes_for(inputs, ticker)
    risk = _risk_level_for(inputs, ticker)
    material_terms = ("stressed", "negative GM", "dilution", "Metric Anomaly")
    return risk in ("MEDIUM", "HIGH") or any(
        term in note for term in material_terms for note in notes)


def _section_todays_operator_focus(inputs: Dict[str, Any]) -> List[str]:
    """Small, source-ordered action block for today's human review."""
    hc = inputs.get("high_conviction") or {}
    eo = inputs.get("emerging_outlier") or {}
    # A data-suspect fundamental (e.g. RYAN-style implausible dilution)
    # must not sit in the clean Review First line — it belongs only in
    # Red-flag review below, never in both.
    review_first = [
        c.get("ticker") for c in (hc.get("shortlist") or [])[:5]
        if not _dilution_data_suspect(c.get("ticker"), inputs.get("store"))
    ]
    eo_watch = [w.get("ticker") for w in (eo.get("watch") or [])]
    # A ticker with material red-flag risk notes must not also sit in the
    # clean Higher-risk emerging review line — it belongs only in
    # Red-flag review below, never in both (BBNX 2026-08-19 duplicate).
    higher_risk = [
        ticker for ticker in eo_watch
        if not _priority_red_flag_for(inputs, ticker)
    ][:5]
    wait_reset = [c.get("ticker") for c in
                  (hc.get("quality_but_extended") or [])[:5]]
    red_flags = [
        ticker for ticker in _dedupe(_best_research_tickers(inputs) + eo_watch)
        if _priority_red_flag_for(inputs, ticker)
    ][:4]
    lines = [
        "## Today's Operator Focus",
        f"- Review first: {_join(review_first, cap=5)}",
        f"- Higher-risk emerging review: {_join(higher_risk, cap=5)}",
        f"- Wait for reset: {_join(wait_reset, cap=5)}",
        f"- Red-flag review only: {_join(red_flags, cap=4)}",
    ]
    for ticker in wait_reset:
        anomalies = _metric_anomalies_for(ticker, inputs.get("store"))
        if anomalies:
            lines.append(
                f"- Metric anomaly — {ticker}: Fundamental Metric Anomaly / "
                "Manual Normalization Required: "
                + "; ".join(anomalies) + ".")
    verdict = (inputs.get("status") or {}).get("tracker_verdict") or "UNKNOWN"
    lines.append(
        "- Do not conclude alpha: HC/EO immature; broad forward verdict "
        f"{verdict}.")
    return lines


_DETERIORATION_LABELS = {"LOW": "Low", "MEDIUM": "Medium", "HIGH": "High",
                         "UNKNOWN": "Unknown"}


def _deterioration_label(risk: Optional[str]) -> str:
    """Neutral display label for the internal deterioration-risk value.
    The user-facing term is 'Business Deterioration Risk', never
    'dead-horse'; the canonical value is preserved in the payload."""
    return _DETERIORATION_LABELS.get(str(risk or "").upper(), "Unknown")


def _section_high_conviction(inputs: Dict[str, Any]) -> List[str]:
    """High-Conviction Alpha Shortlist — the selective quality/growth/
    value/momentum layer.  Membership is NOT validated alpha; the
    rejected high-score names are surfaced with reasons so the operator
    sees WHY a strong-signal name did not qualify."""
    lines = ["## 4b1. High-Conviction Alpha Shortlist"]
    hc = inputs.get("high_conviction")
    if not hc or not hc.get("present"):
        lines.append("- Shortlist sidecar missing — run "
                     "./scripts/run_research_cycle.sh high-conviction-alpha")
        return lines
    cts = hc.get("counts") or {}
    lines.append(
        f"- Evaluated {cts.get('evaluated', 0)} | qualified "
        f"{cts.get('qualified', 0)} | quality-but-extended "
        f"{cts.get('quality_but_extended', 0)} | rejected "
        f"{cts.get('rejected', 0)}")
    lines.append("- Membership is research-only and NOT validated alpha.")
    shortlist = hc.get("shortlist") or []
    if not shortlist:
        lines.append("- NO_HIGH_CONVICTION_CANDIDATES — nothing met the "
                     "qualification standards in the latest scan.")
    for i, s in enumerate(shortlist, 1):
        cs = s.get("component_scores") or {}
        val = (cs.get("value_score") if cs.get("value_score") is not None
               else s.get("valuation_status"))
        lines.append(
            f"  {i}. {s['ticker']} [{s['classification']}] "
            f"{s['program']} score {s['high_conviction_score']} "
            f"(quality {cs.get('quality_score')}, growth "
            f"{cs.get('growth_score')}, momentum "
            f"{cs.get('price_momentum_score')}, value {val}) — "
            f"{', '.join(s.get('why_selected') or []) or '—'}"
            + (f" | risk: {', '.join(s['main_risks'])}"
               if s.get("main_risks") else "")
            + f" | Business Deterioration Risk: "
            f"{_deterioration_label(s.get('dead_horse_risk'))}")
    ext = hc.get("quality_but_extended") or []
    if ext:
        lines.append("- Quality but extended (wait for reset): "
                     + ", ".join(f"{c['ticker']} "
                                 f"({c['high_conviction_score']})"
                                 for c in ext[:8]))
    # rejected high-score names + why (the whole point: strong signal that
    # did not clear the quality/risk bar)
    rej = sorted((hc.get("rejected") or []),
                 key=lambda c: -(c.get("research_score") or 0))
    high_signal_rejects = [c for c in rej
                           if (c.get("research_score") or 0) >= 90][:6]
    if high_signal_rejects:
        lines.append("- Strong-signal names rejected by quality/risk:")
        for c in high_signal_rejects:
            lines.append(f"  - {c['ticker']} (scan score "
                         f"{c.get('research_score')}): "
                         f"{'; '.join(c.get('exclusions') or [])}")
    fwd = inputs.get("high_conviction") or {}
    fv = _load_json(inputs["store"].high_conviction_forward_json) \
        if inputs.get("store") else None
    if fv and fv.get("present"):
        lines.append(f"- Forward validation (separate hypothesis): "
                     f"{fv.get('verdict')} — {fv.get('verdict_reason')}")
    return lines


def _section_emerging_outlier(inputs: Dict[str, Any]) -> List[str]:
    """Emerging Outlier Watch — the separate, higher-risk lane for credible
    pre-profit / turnaround names.  Never merged with the shortlist; the
    audit flags below check it did not admit a momentum-only or
    high-deterioration name."""
    lines = ["## 4b2. Emerging Outlier Watch"]
    eo = inputs.get("emerging_outlier")
    if not eo or not eo.get("present"):
        lines.append("- Emerging-outlier sidecar missing — run "
                     "./scripts/run_research_cycle.sh emerging-outlier")
        return lines
    watch = eo.get("watch") or []
    lines.append(f"- Evaluated {eo.get('counts', {}).get('evaluated', 0)} | "
                 f"emerging outliers {len(watch)} | separate higher-risk "
                 "lane, research-only (not validated alpha).")
    if not watch:
        lines.append("- NO_EMERGING_OUTLIERS — nothing met the multi-factor "
                     "emergence bar.")
    for w in watch[:8]:
        dims = ", ".join(d["dimension"].lower()
                         for d in w.get("emergence_dimensions") or [])
        lines.append(
            f"  - {w['ticker']} ({w['program']}): {w.get('why_not_high_conviction')}"
            f" — why watched: {dims}"
            f" | business-deterioration {w.get('business_deterioration_risk')}")
    if len(watch) > 8:
        lines.append(f"  - … and {len(watch) - 8} more")
    # audit deterministic flags for this lane
    flags = _emerging_audit_flags(watch)
    for f in flags:
        lines.append(f"- AUDIT FLAG: {f}")
    fa = inputs.get("filter_audit")
    if fa and fa.get("present"):
        oa = fa.get("one_rule_away") or {}
        lines.append(
            f"- V1 filter audit: {fa.get('n_candidates')} evaluated | "
            f"one-rule-away {oa.get('count', 0)} "
            f"{oa.get('by_gate', {})} | gate decomposition matches V1: "
            f"{fa.get('gate_decomposition_matches_v1')}")
    return lines


def _emerging_audit_flags(watch: List[Dict[str, Any]]) -> List[str]:
    """Deterministic checks the digest surfaces (mirrored by the journal
    auditor): the lane must not admit momentum-only names, high-
    deterioration names, or names lacking substantive evidence."""
    flags: List[str] = []
    for w in watch:
        if w.get("business_deterioration_risk") == "HIGH":
            flags.append(f"{w['ticker']} entered with HIGH "
                         "business-deterioration risk")
        if (w.get("n_substantive_dimensions") or 0) < 1:
            flags.append(f"{w['ticker']} lacks substantive "
                         "(fundamental/balance) emergence evidence")
    return flags


def _avoid_rationale_for(ticker: str,
                         rejected_by_ticker: Dict[str, Dict[str, Any]],
                         avoid_set: set, inputs: Dict[str, Any]) -> str:
    """Explicit rationale when a top-candidate ticker also sits on an
    avoid/red-flag list elsewhere in the same digest, so the two never
    silently contradict each other (feedback-queue task 77c2e0840b8e).
    Read-only: consults existing HC/EO/risk data, changes no score,
    ranking, or candidate membership."""
    reasons: List[str] = []
    rejected = rejected_by_ticker.get(ticker)
    if rejected is not None:
        excl = rejected.get("exclusions") or []
        risk = (rejected.get("dead_horse_risk")
                or rejected.get("business_deterioration_risk"))
        detail = ", ".join(excl) if excl else "quality/risk overlay reject"
        reason = f"High-Conviction REJECTED ({detail}"
        if risk:
            reason += f"; deterioration risk {risk}"
        reasons.append(reason + ")")
    if ticker in avoid_set:
        reasons.append("Data Quality: Avoid / Data Issue list (quarantined)")
    if rejected is None:
        notes = _risk_notes_for(inputs, ticker)
        if notes:
            reasons.append("Risk / Red-Flag Review: " + ", ".join(notes))
    return "; ".join(reasons)


def _section_top_candidates(inputs: Dict[str, Any]) -> List[str]:
    """Concise top-candidates recap (one line per program, existing
    score/priority order, one short reason each).  Purely a reading aid
    placed before the full latest-scan section — the detailed records
    below remain the audit copy."""
    lines = ["## 4c. Top Candidates by Program"]
    payload = inputs.get("latest_scan")
    if not payload or not payload.get("present"):
        lines.append("- Latest-scan sidecar missing — run "
                     "./scripts/run_research_cycle.sh latest-scan-programs")
        return lines
    from dashboards.research_command_center.presentation import (
        short_reason, split_candidates)
    rejected_by_ticker = {
        str(r.get("ticker") or "").upper(): r
        for r in (inputs.get("high_conviction") or {}).get("rejected") or []
    }
    avoid_set = {str(t).upper() for t in
                (inputs.get("radar") or {}).get("priority_tickers", {})
                .get("DATA_QUARANTINE") or []}
    for pid in ("TACTICAL", "SWING", "LONG_TERM"):
        block = (payload.get("programs") or {}).get(pid) or {}
        top, _, issues = split_candidates(block, top_n=3)
        if not top:
            lines.append(f"- {pid}: no candidates in the latest scan.")
            continue
        bits = [f"{c['ticker']} (score {c.get('research_score')}, "
                f"{c['scan_status']}) — {short_reason(c, 60)}"
                for c in top]
        lines.append(f"- {pid}: " + "; ".join(bits))
        if issues:
            lines.append(f"  - {pid} data issues (excluded from top): "
                         + ", ".join(c["ticker"] for c in issues))
        for c in top:
            ticker = str(c.get("ticker") or "").upper()
            rationale = _avoid_rationale_for(ticker, rejected_by_ticker,
                                             avoid_set, inputs)
            if rationale:
                lines.append(f"  - {ticker}: also on an avoid/red-flag "
                             f"list — {rationale}")
    return lines


def _section_latest_scan_programs(inputs: Dict[str, Any]) -> List[str]:
    """Latest-scan candidates per program (what the most recent cycle
    detected) — deliberately separate from the forward-evidence and
    program-maturity summaries above it."""
    lines = ["## 4d. Latest Scan by Research Program"]
    payload = inputs.get("latest_scan")
    if not payload or not payload.get("present"):
        lines.append("- Latest-scan sidecar missing — run "
                     "./scripts/run_research_cycle.sh latest-scan-programs")
        return lines
    lines.append(
        f"- Market as-of: {payload.get('market_as_of_date') or 'unknown'} "
        f"| universe hash match: {payload.get('manifest_hash_match')} "
        f"| scan readiness: {payload.get('scan_readiness') or 'unknown'}")
    for w in payload.get("integrity_warnings") or []:
        lines.append(f"- Warning: {w}")
    if payload.get("scan_stale") and not payload.get("integrity_warnings"):
        lines.append(f"- Warning: latest scan is STALE "
                     f"({payload.get('scan_age_hours')}h old)")
    for pid in ("TACTICAL", "SWING", "LONG_TERM"):
        block = (payload.get("programs") or {}).get(pid) or {}
        n = block.get("candidate_count", 0)
        lines.append(
            f"- {pid}: {n} candidates | new {block.get('new_count', 0)} "
            f"| repeat {block.get('repeat_count', 0)} | returning "
            f"{block.get('returning_count', 0)} | exited "
            f"{block.get('exited_count', 0)}")
        if not n:
            lines.append("  - No candidates detected in the latest scan.")
            continue
        for c in (block.get("candidates") or [])[:3]:
            session_tag = ("same-session" if c.get("same_session")
                           else "SESSION_MISMATCH"
                           if c.get("same_session") is False else "session ?")
            lines.append(
                f"  - {c['ticker']} [{c['scan_status']}] hold "
                f"{c.get('holding_period')} | "
                f"lifecycle {c.get('lifecycle_stage') or '?'} | verdict "
                f"{c.get('program_verdict')} | bar "
                f"{c.get('bar_as_of_date') or '?'} vs bench "
                f"{c.get('benchmark_as_of_date') or '?'} ({session_tag}) | "
                f"{(c.get('detection_reason') or '')[:70]}")
        exited = [e.get("ticker") for e in block.get("exited") or []][:6]
        if exited:
            lines.append(f"  - Exited since previous scan: "
                         f"{', '.join(exited)}"
                         + (" (+more)" if block.get("exited_count", 0) > 6
                            else ""))
    return lines


def _fundamental_line(ticker: str, store: ArtifactStore) -> str:
    f = build_fundamentals(ticker, store)
    if f.get("fallback"):
        return f"- {ticker}: fundamentals unavailable (no cached statements)"
    flags = []
    dil = f.get("dilution_3q_pct")
    if dil is not None and dil > 10:
        flags.append(f"high dilution {dil:+.1f}%/3q")
    gm = f.get("gross_margin_pct")
    if gm is not None and gm < 0:
        flags.append("negative gross margin")
    net_debt = f.get("net_debt")
    cash_clue = "n/a"
    if net_debt is not None:
        cash_clue = "net cash" if net_debt < 0 else "net debt"
    parts = [
        f"quality {f.get('quality_label')}",
        f"GM {_fmt(gm, '%')}",
        f"OM {_fmt(f.get('operating_margin_pct'), '%')}",
        cash_clue,
    ]
    if dil is not None:
        parts.append(f"dilution 3q {dil:+.1f}%")
    line = f"- {ticker}: " + " | ".join(parts)
    anomalies = _metric_anomalies_for(ticker, store)
    if anomalies:
        line += (" — Fundamental Metric Anomaly / Manual Normalization "
                 "Required: " + "; ".join(anomalies))
    if flags:
        line += " — RED FLAG: " + "; ".join(flags)
    return line


def _section_fundamentals(inputs: Dict[str, Any], top: List[str]) -> List[str]:
    lines = ["## 5. Fundamental Overlay"]
    if not top:
        lines.append("- No review candidates available.")
        return lines
    for t in top[:10]:
        lines.append(_fundamental_line(t, inputs["store"]))
    return lines


def _red_flags_for(ticker: str, store: ArtifactStore) -> List[str]:
    """Compact fundamental red flags for review-queue annotation.
    Display only — mirrors _fundamental_line's criteria plus the
    unprofitable tier the audit uses; no score or ranking reads this."""
    f = build_fundamentals(ticker, store)
    if f.get("fallback"):
        return []
    flags: List[str] = []
    quality = str(f.get("quality_label") or "")
    if quality == "UNPROFITABLE_STRESSED":
        flags.append("unprofitable stressed")
    elif quality.startswith("UNPROFITABLE"):
        flags.append("unprofitable")
    om = f.get("operating_margin_pct")
    if om is not None and om < 0:
        flags.append(f"OM {om:+.1f}%")
    gm = f.get("gross_margin_pct")
    if gm is not None and gm < 0:
        flags.append("negative GM")
    dil = f.get("dilution_3q_pct")
    if dil is not None and dil < DILUTION_DATA_SUSPECT_PCT:
        flags.append(f"dilution_3q_pct {dil:+.0f}%/3q data-suspect "
                     "(outside plausible range, not a real buyback)")
    elif dil is not None and dil > 10:
        flags.append(f"dilution {dil:+.1f}%/3q")
    return flags


def _dilution_data_suspect(ticker: str,
                           store: Optional[ArtifactStore]) -> bool:
    """True when dilution_3q_pct is implausible enough to be a data gap,
    not a real buyback — used to force review-queue risk routing even for
    an otherwise-qualified High-Conviction shortlist member."""
    if store is None:
        return False
    f = build_fundamentals(ticker, store)
    if f.get("fallback"):
        return False
    dil = f.get("dilution_3q_pct")
    return dil is not None and dil < DILUTION_DATA_SUSPECT_PCT


def _review_queue_groups(inputs: Dict[str, Any], top: List[str],
                         high: List[str],
                         reset_reclaim: List[str]) -> Dict[str, List[str]]:
    """Presentation-only review buckets built from existing artifact fields."""
    hc = inputs.get("high_conviction") or {}
    eo = inputs.get("emerging_outlier") or {}
    radar = inputs.get("radar") or {}
    alpha = ((inputs.get("summary") or {}).get("alpha_snapshot") or {})
    shortlist = _dedupe([r.get("ticker") for r in hc.get("shortlist") or []])
    emerging = _dedupe([r.get("ticker") for r in eo.get("watch") or []])
    rejected = _dedupe([r.get("ticker") for r in hc.get("rejected") or []])
    wait_reset = _dedupe(
        [r.get("ticker") for r in hc.get("quality_but_extended") or []]
        + list(alpha.get("extended_crowded_tickers") or [])
        + list((radar.get("priority_tickers") or {}).get(
            "EXTENDED_CROWDED") or []))
    avoid = _dedupe(list((radar.get("priority_tickers") or {}).get(
        "DATA_QUARANTINE") or []))
    board = _dedupe(
        _best_research_tickers(inputs) + list(high) + list(top)
        + list(reset_reclaim))
    risk_pool = _dedupe(
        _best_research_tickers(inputs) + rejected + emerging + board
        + shortlist)
    risk = [
        ticker for ticker in risk_pool
        if _risk_notes_for(inputs, ticker)
        and (ticker not in shortlist
             or _dilution_data_suspect(ticker, inputs.get("store")))
        and ticker not in wait_reset
        and ticker not in avoid
    ]

    reserved = set(risk + wait_reset + avoid)
    opportunity_pool = _dedupe(shortlist + list(reset_reclaim) + board)

    def opportunity_key(ticker: str) -> Tuple[int, int, int, int, int]:
        if ticker in shortlist:
            return (0, shortlist.index(ticker), 0, 0, 0)
        f = build_fundamentals(ticker, inputs.get("store"))
        profitable = str(f.get("quality_label") or "").startswith("PROFITABLE")
        reset = ticker in reset_reclaim
        known_cap = (f.get("market_cap") or 0) >= 300_000_000
        low_risk = _risk_level_for(inputs, ticker) in ("LOW", "UNKNOWN")
        return (1, 0 if profitable else 1, 0 if reset else 1,
                0 if known_cap else 1, 0 if low_risk else 1)

    opportunity = []
    for ticker in sorted(opportunity_pool, key=opportunity_key):
        f = build_fundamentals(ticker, inputs.get("store"))
        profitable = str(f.get("quality_label") or "").startswith("PROFITABLE")
        if (ticker not in reserved
                and not _risk_notes_for(inputs, ticker)
                and (ticker in shortlist or ticker in reset_reclaim
                     or profitable)):
            opportunity.append(ticker)
    watch_only = [
        ticker for ticker in board
        if ticker not in reserved and ticker not in opportunity
    ]
    return {
        "opportunity": opportunity,
        "risk": risk,
        "watch_only": watch_only,
        "avoid": avoid,
        "wait_reset": wait_reset,
    }


def _section_review_queue(inputs: Dict[str, Any], top: List[str],
                          high: List[str],
                          reset_reclaim: List[str]) -> List[str]:
    groups = _review_queue_groups(inputs, top, high, reset_reclaim)
    lines = [
        "## 6. Journal Review Queue",
        f"- Opportunity Review: {_join(groups['opportunity'], cap=10)}",
        f"- Risk / Red-Flag Review: {_join(groups['risk'], cap=10)}",
    ]
    risk_notes = []
    for ticker in groups["risk"][:10]:
        notes = _risk_notes_for(inputs, ticker)
        if notes:
            risk_notes.append(f"{ticker} ({', '.join(notes)})")
    if risk_notes:
        lines.append("  - Risk details: " + "; ".join(risk_notes))
    lines += [
        f"- Watch Only: {_join(groups['watch_only'], cap=10)}",
        f"- Avoid / Data Issue: {_join(groups['avoid'], cap=10)}",
        f"- Wait for Reset: {_join(groups['wait_reset'], cap=10)}",
    ]
    for ticker in groups["wait_reset"][:10]:
        anomalies = _metric_anomalies_for(ticker, inputs.get("store"))
        if anomalies:
            lines.append(
                f"  - {ticker}: Fundamental Metric Anomaly / Manual "
                "Normalization Required: " + "; ".join(anomalies))
    return lines


def _section_diagnostics_appendix(inputs: Dict[str, Any]) -> List[str]:
    """Legacy diagnostics stay available without dominating daily priority."""
    rd = inputs.get("recall_diagnostics")
    warnings = [
        warning for warning in
        ((inputs.get("summary") or {}).get("warnings") or [])
        if _is_legacy_recall_warning(warning)
    ]
    filters = (rd or {}).get("reject_counts_by_filter") or []
    if not filters and not warnings:
        return []
    top_filters = ", ".join(
        f"{r.get('filter')} ({r.get('rejected_n')} rejected/"
        f"{r.get('winners_missed')} winners missed)"
        for r in filters[:3])
    lines = [
        "<details>",
        "<summary>Technical diagnostics appendix</summary>",
        "",
        "### Legacy / Decommissioned Recall Diagnostics",
    ]
    for warning in warnings:
        lines.append(f"- {warning}")
    if top_filters:
        lines.append(
            f"- Historical recall diagnostic as of {(rd or {}).get('asof_date')}: "
            f"{top_filters}.")
    lines += [
        "- Technical context only. Daily priority is based on the live board "
        "and current scanner evidence; gates remain unchanged.",
        "",
        "</details>",
    ]
    return lines


def _final_structural_notes(inputs: Dict[str, Any], top: List[str]) -> str:
    """Known structural context appended to the final finding: an overlay
    that is off by design and a non-aligned sector read must be stated
    explicitly instead of leaving the audit to infer them."""
    notes: List[str] = []
    oc = inputs.get("options_coverage") or {}
    state = str(oc.get("overlay_state") or "")
    if state and state != "ENABLED":
        notes.append(
            f"options overlay is {state} (structural coverage gate on the "
            "capped snapshot-collector universe — corroborating options "
            "evidence is absent for all candidates, not a candidate defect)")
    _, aligned = _alignment_for_top(inputs, top)
    head = aligned.split(" — ")[0]
    if head not in ("aligned", "unknown"):
        notes.append(f"top-name sector alignment reads {head}")
    if not notes:
        return ""
    return "Known structural context: " + "; ".join(notes) + "."


def _final_finding_headline_names(inputs: Dict[str, Any],
                                  high: List[str]) -> Tuple[str, List[str]]:
    """Names the Final Finding should cite when it needs manual review.
    Prefers Alpha Focus's Review Now list (the doctrine-prioritized,
    root-cause-filtered set) over the raw broad-scanner high-priority
    list, so the digest's headline conclusion doesn't outrank Alpha Focus
    / Operator Focus with unfiltered scanner names.  Falls back to the
    broad-scanner list only when Alpha Focus hasn't run yet."""
    af = inputs.get("alpha_focus") or {}
    if af and not af.get("fallback"):
        review_now = [r.get("ticker") for r in af.get("review_now") or []]
        if review_now:
            return "Alpha Focus review-now names", review_now
    return "high-priority names", high


def _section_final_finding(inputs: Dict[str, Any], status: str,
                           high: List[str], concerns: List[str],
                           top: List[str]) -> List[str]:
    verdict = inputs["status"].get("tracker_verdict") or "UNKNOWN"
    phase4b = (inputs["status"].get("phase_4b") or {}).get("status")
    incomplete = _forward_incomplete_warnings(inputs)
    if status == "DATA_QUALITY_CONCERN":
        finding = ("Data quality needs attention before the research output "
                   f"can be relied on: {concerns[0] if concerns else 'see warnings above'}.")
    elif status == "NEEDS_REVIEW":
        label, names = _final_finding_headline_names(inputs, high)
        finding = ("Pipeline is operational; "
                   + (f"{label} ({_join(names, cap=4)}) need manual "
                      "fundamental review" if names
                      else "open warnings need operator review")
                   + f", while forward evidence remains {verdict} and Phase 4B "
                   f"stays {phase4b}.")
    elif status == "FOLLOW_UP":
        finding = ("Pipeline is healthy and candidates are stable, but forward "
                   f"evidence is still immature ({verdict}) and Phase 4B "
                   f"remains {phase4b}.")
    elif incomplete:
        finding = ("Pipeline is healthy operationally, but forward evidence is "
                   f"incomplete: {incomplete[0]}")
    else:
        finding = ("Pipeline is healthy, forward evidence supports the current "
                   "research board, and no urgent issues surfaced today.")
    lines = ["## 7. Final Finding", finding]
    structural = _final_structural_notes(inputs, top)
    if structural:
        lines.append(structural)
    return lines


def build_note(inputs: Dict[str, Any], *, status: str, concerns: List[str],
               top: List[str], high: List[str],
               reset_reclaim: List[str]) -> str:
    sections = (
        ["# Daily Research Digest", ""]
        + _section_todays_operator_focus(inputs) + [""]
        + _section_data_quality(inputs, concerns) + [""]
        + _section_sector_regime(inputs, top) + [""]
        + _section_forward(inputs) + [""]
        + _section_cohort_attribution(inputs) + [""]
        + _section_research_operating_policy(inputs) + [""]
        + _section_alpha_root_cause(inputs) + [""]
        + _section_alpha_focus(inputs) + [""]
        + _section_research_programs(inputs) + [""]
        + _section_high_conviction(inputs) + [""]
        + _section_emerging_outlier(inputs) + [""]
        # Broad scanner + its raw candidate/scan listings are discovery-only
        # (priority hierarchy tier F) — grouped here, after Alpha Focus/HC/EO,
        # so unfiltered scanner names never outrank the prioritized sections
        # above them.
        + _section_scanner(inputs, top, reset_reclaim) + [""]
        + _section_top_candidates(inputs) + [""]
        + _section_latest_scan_programs(inputs) + [""]
        + _section_fundamentals(inputs, top) + [""]
        + _section_review_queue(inputs, top, high, reset_reclaim) + [""]
        + _section_final_finding(inputs, status, high, concerns, top) + [""]
        + _section_diagnostics_appendix(inputs) + [""]
        + [RESEARCH_ONLY_FOOTER]
    )
    return "\n".join(sections)


# ── entry assembly ───────────────────────────────────────────────────────────


def build_digest_entry(store: Optional[ArtifactStore] = None,
                       date_override: Optional[str] = None,
                       top_n: int = 10) -> Dict[str, Any]:
    """Assemble one Phase-6-format journal entry.  Pure read — never writes."""
    store = store or ArtifactStore()
    inputs = collect_inputs(store)
    date_s = date_override or datetime.now(timezone.utc).date().isoformat()

    alpha = (inputs["summary"] or {}).get("alpha_snapshot") or {}
    high = list(alpha.get("high_priority_tickers")
                or (inputs["radar"] or {}).get("priority_tickers", {}).get(
                    "HIGH_PRIORITY_RESEARCH") or [])
    top = _top_review_candidates(inputs, top_n)
    reset_reclaim = _reset_reclaim(inputs)
    status, concerns = _select_status(inputs, high)
    st = inputs["status"]
    counts = (inputs["radar"] or {}).get("priority_counts") or {}

    note = build_note(inputs, status=status, concerns=concerns, top=top,
                      high=high, reset_reclaim=reset_reclaim)

    snapshot = {
        "mode": "RESEARCH_ONLY",
        "phase4b_status": (st.get("phase_4b") or {}).get("status"),
        "verdict": st.get("tracker_verdict"),
        "market_regime": st.get("market_regime"),
        "data_quality": ("CONCERN" if status == "DATA_QUALITY_CONCERN"
                         else st.get("data_freshness")),
        "total_candidates": (inputs["radar"] or {}).get("total_candidates"),
        "high_priority": high,
        "reset_reclaim": reset_reclaim,
        "extended_crowded_count": (counts.get("EXTENDED_CROWDED")
                                   if counts.get("EXTENDED_CROWDED") is not None
                                   else alpha.get("extended_crowded_count")),
        "data_quarantine_count": st.get("quarantine_count"),
        "matured_5d": st.get("matured_5d"),
        "matured_10d": st.get("matured_10d"),
        "benchmark_status": st.get("benchmark_readiness"),
        "top_review_candidates": top,
    }

    return {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": None,
        "ticker": DIGEST_TICKER,
        "title": f"{DIGEST_TITLE_PREFIX}{date_s}",
        "note": note,
        "status": status,
        "tags": _build_tags(inputs, high),
        "source_view": DIGEST_SOURCE_VIEW,
        "evidence_snapshot": snapshot,
        "digest_key": compute_digest_key(inputs, date_s),
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


def append_digest_entry(entry: Dict[str, Any], store: ArtifactStore) -> None:
    """The module's only write: append one line to journal.jsonl."""
    path = store.journal_jsonl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
