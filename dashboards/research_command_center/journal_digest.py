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
from datetime import datetime, timezone
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
    return bool(concerns), concerns


def _select_status(inputs: Dict[str, Any],
                   high_priority: List[str]) -> Tuple[str, List[str]]:
    concern, concerns = _data_quality_concern(inputs)
    warnings = (inputs["summary"] or {}).get("warnings") or []
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
    lines = [
        "## 1. Data Quality",
        f"- Freshness: {status.get('data_freshness')} "
        f"(scanner age {_fmt(status.get('scanner_age_hours'), 'h')})",
        f"- Stale-price skipped: {_fmt(stale)} | suspect-feed skipped: "
        f"{_fmt(suspect)} | quarantined: {_fmt(status.get('quarantine_count'))}",
        f"- Missing artifacts: {len(inputs['missing'])}"
        + (f" ({_join(inputs['missing'], cap=4)})" if inputs["missing"] else ""),
        f"- Price refresh: {refresh_healthy}",
    ]
    warnings = (inputs["summary"] or {}).get("warnings") or []
    for w in (concerns + warnings)[:5]:
        lines.append(f"- Warning: {w}")
    if not concerns and not warnings:
        lines.append("- No major warnings.")
    return lines


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
    lines = [
        "## 2. Scanner / Why Names Appeared",
        f"- Total candidates: {_fmt(radar.get('total_candidates') or scanner.get('watchlist_size'))}",
        f"- High-priority: {_join(high)}",
        f"- Reset/reclaim watch: {_join(reset_reclaim)}",
        f"- Extended/crowded: {_fmt(counts.get('EXTENDED_CROWDED') or alpha.get('extended_crowded_count'))}"
        f" | data quarantine / young listing: {_fmt(quarantine)}",
        f"- Top research names: {_join(top)}",
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


def _section_sector_regime(inputs: Dict[str, Any], top: List[str]) -> List[str]:
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
    aligned = "unknown"
    if top_sectors and (leading or weak):
        in_weak = [s for s in top_sectors if s in weak]
        aligned = ("misaligned — top names sit in weak sectors: "
                   + ", ".join(in_weak)) if in_weak else "aligned"
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


def _section_review_queue(inputs: Dict[str, Any], top: List[str],
                          high: List[str],
                          reset_reclaim: List[str]) -> List[str]:
    alpha = (inputs["summary"] or {}).get("alpha_snapshot") or {}
    radar = inputs["radar"] or {}
    quarantine = list(radar.get("priority_tickers", {}).get("DATA_QUARANTINE")
                      or [])
    extended = list(alpha.get("extended_crowded_tickers")
                    or radar.get("priority_tickers", {}).get(
                        "EXTENDED_CROWDED") or [])
    review_first = _dedupe(high + top[:5])
    watch_only = [t for t in _dedupe(reset_reclaim + top[5:])
                  if t not in review_first]
    return [
        "## 6. Journal Review Queue",
        f"- Review first: {_join(review_first, cap=6)}",
        f"- Watch only: {_join(watch_only, cap=6)}",
        f"- Avoid for now / data issue: {_join(quarantine, cap=6)}",
        f"- Follow up later (extended/crowded — wait for reset): "
        f"{_join(extended, cap=6)}",
    ]


def _section_final_finding(inputs: Dict[str, Any], status: str,
                           high: List[str],
                           concerns: List[str]) -> List[str]:
    verdict = inputs["status"].get("tracker_verdict") or "UNKNOWN"
    phase4b = (inputs["status"].get("phase_4b") or {}).get("status")
    if status == "DATA_QUALITY_CONCERN":
        finding = ("Data quality needs attention before the research output "
                   f"can be relied on: {concerns[0] if concerns else 'see warnings above'}.")
    elif status == "NEEDS_REVIEW":
        finding = ("Pipeline is operational; "
                   + (f"high-priority names ({_join(high, cap=4)}) need manual "
                      "fundamental review" if high
                      else "open warnings need operator review")
                   + f", while forward evidence remains {verdict} and Phase 4B "
                   f"stays {phase4b}.")
    elif status == "FOLLOW_UP":
        finding = ("Pipeline is healthy and candidates are stable, but forward "
                   f"evidence is still immature ({verdict}) and Phase 4B "
                   f"remains {phase4b}.")
    else:
        finding = ("Pipeline is healthy, forward evidence supports the current "
                   "research board, and no urgent issues surfaced today.")
    return ["## 7. Final Finding", finding]


def build_note(inputs: Dict[str, Any], *, status: str, concerns: List[str],
               top: List[str], high: List[str],
               reset_reclaim: List[str]) -> str:
    sections = (
        ["# Daily Research Digest", ""]
        + _section_data_quality(inputs, concerns) + [""]
        + _section_scanner(inputs, top, reset_reclaim) + [""]
        + _section_sector_regime(inputs, top) + [""]
        + _section_forward(inputs) + [""]
        + _section_research_programs(inputs) + [""]
        + _section_fundamentals(inputs, top) + [""]
        + _section_review_queue(inputs, top, high, reset_reclaim) + [""]
        + _section_final_finding(inputs, status, high, concerns) + [""]
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
