#!/usr/bin/env python3
"""Latest-scan candidates by research program — operator visibility layer.

The command center's program cards showed validation metadata only; this
module answers the second operator question: *what did the latest scan
actually detect for each program?*

Candidate LIST comes strictly from the latest scanner artifact (the most
recent production research cycle — never historical aggregates).  Each
candidate is then enriched read-only:
  - routing: primary program from its watchlist label
    (research_programs.classify_label); secondary programs from its other
    matched categories, with an explicit deterministic routing reason.
  - scan status vs the previous successful scan (membership history in
    the forward-tracker ledger): NEW / REPEAT / RETURNING, plus per-
    program EXITED lists.
  - evidence maturity: the candidate's OWN first episode in the ledger —
    which of its program's horizons have matured (per-candidate, not an
    aggregate).
  - lifecycle stage + program verdict from the program-validation sidecar
    (pre-registered gates — never computed here).
  - fundamental quality from the cached overlay (display only).

Doctrine: RESEARCH-ONLY / CACHE-ONLY / READ-ONLY.  Zero changes to
candidate selection, thresholds, ranking, scores, gates, or verdicts.
HIGH priority never implies validated alpha.  Writes only its sidecar
(cache/research/latest_scan_programs_latest.json) + log twin.

Usage:
  ./scripts/run_research_cycle.sh latest-scan-programs
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.research_programs import (  # noqa: E402
    PROGRAMS, TRACKED_HORIZONS, classify_label)

SCANNER_REL = Path("cache") / "research" / "research_scanner_latest.json"
MANIFEST_REL = (Path("cache") / "research"
                / "scan_universe_manifest_latest.json")
RADAR_REL = Path("cache") / "research" / "daily_alpha_radar_latest.json"
PROGRAM_REL = (Path("cache") / "research"
               / "research_program_validation_latest.json")
LEDGER_REL = (Path("data") / "research"
              / "research_watchlist_history.jsonl")
SIDECAR_REL = (Path("cache") / "research"
               / "latest_scan_programs_latest.json")
LOG_REL = Path("logs") / "latest_scan_programs_latest.txt"

PROGRAM_ORDER = ("TACTICAL", "SWING", "LONG_TERM")
STALE_SCAN_HOURS = 18.0        # matches the dashboard freshness chip
TERMINAL_CAP = 5               # candidates printed per program

# Scanner category -> program, for SECONDARY matches and unmapped-label
# fallback.  Mirrors the label routing (categories and labels share the
# same hypothesis space); display/routing metadata only.
CATEGORY_PROGRAMS: Dict[str, str] = {
    "catalyst_watch": "TACTICAL",
    "social_arb_attention": "TACTICAL",
    "early_accumulation": "SWING",
    "beaten_down_recovery": "SWING",
    "sector_theme_leaders": "SWING",
    "sector_theme_leader": "SWING",
    "rs_momentum_leader": "SWING",
    "long_term_asymmetric": "LONG_TERM",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) \
            if path.exists() else None
    except Exception:
        return None


# ── ledger membership + per-candidate episode maturity ───────────────────────


def _ledger_index(root: Path):
    """(membership by date, earliest episode row per ticker+label)."""
    by_date: Dict[str, set] = {}
    first_row: Dict[tuple, Dict[str, Any]] = {}
    path = root / LEDGER_REL
    if not path.exists():
        return by_date, first_row
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        date = rec.get("appearance_date")
        ticker = rec.get("ticker")
        if not date or not ticker:
            continue
        by_date.setdefault(date, set()).add(ticker)
        key = (ticker, rec.get("watchlist_label"))
        if key not in first_row \
                or date < first_row[key].get("appearance_date", "9999"):
            first_row[key] = rec
    return by_date, first_row


def _evidence_maturity(episode: Optional[Dict[str, Any]],
                       program_id: str) -> Dict[str, str]:
    """MATURED/PENDING per program horizon for THIS candidate's first
    episode (per-candidate ledger read, not an aggregate)."""
    prog = PROGRAMS.get(program_id) or {}
    horizons = sorted(set(prog.get("primary_horizons_td") or ())
                      | set(prog.get("diagnostic_horizons_td") or ()))
    out: Dict[str, str] = {}
    for h in horizons:
        if h not in TRACKED_HORIZONS:
            out[f"{h}d"] = "NOT_COLLECTED"
        elif episode and episode.get(f"ret_{h}d") is not None:
            out[f"{h}d"] = "MATURED"
        else:
            out[f"{h}d"] = "PENDING"
    return out


# ── routing ──────────────────────────────────────────────────────────────────


def route_candidate(item: Dict[str, Any]) -> Dict[str, Any]:
    """Exactly one primary program per candidate, deterministically:
    the scanner's own primary watchlist label wins; other matched
    categories become secondary programs with an explicit reason."""
    label = item.get("watchlist_label") or ""
    route = classify_label(label)
    primary = route.get("program")
    reason_bits: List[str] = []
    if primary in PROGRAMS:
        reason_bits.append(
            f"primary label {label} routes to {primary} "
            f"({route.get('reason')})")
    else:
        category = item.get("category") or ""
        primary = CATEGORY_PROGRAMS.get(category, "TACTICAL")
        route = classify_label(label)  # keep UNROUTED metadata visible
        reason_bits.append(
            f"label {label or '?'} is unmapped — routed by category "
            f"{category or '?'} to {primary}")
    secondary: List[str] = []
    for cat in item.get("all_categories") or []:
        prog = CATEGORY_PROGRAMS.get(cat)
        if prog and prog != primary and prog not in secondary:
            secondary.append(prog)
    if secondary:
        reason_bits.append(
            "also matched categories mapping to "
            + ", ".join(secondary)
            + " — the primary watchlist label wins deterministically")
    return {
        "program": primary,
        "secondary_programs": secondary,
        "routing_reason": "; ".join(reason_bits),
        "hypothesis": route.get("hypothesis"),
        "holding_period_td": route.get("expected_holding_period_td")
        or (PROGRAMS.get(primary) or {}).get("expected_holding_period_td"),
        "confidence": route.get("confidence"),
    }


# ── payload build ────────────────────────────────────────────────────────────


def build_latest_scan(root: Optional[Path] = None,
                      scanner_doc: Optional[Dict[str, Any]] = None,
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()
    scanner = scanner_doc if scanner_doc is not None \
        else _load_json(root / SCANNER_REL)
    if not scanner or not isinstance(scanner.get("watchlist"), list):
        return {"kind": "latest_scan_programs", "present": False,
                "scan_success": False, "research_only": True,
                "reason": "scanner artifact missing or malformed"}

    scan_ts = scanner.get("generated_at") or ""
    scan_date = scan_ts[:10]
    try:
        age_h = (now - datetime.fromisoformat(
            scan_ts.replace("Z", "+00:00"))).total_seconds() / 3600.0
    except Exception:
        age_h = None

    # ── scan-integrity verification (P0 linkage): the candidates shown
    # must be traceable to the validated universe + session that produced
    # them.  Any mismatch is a visible warning — never silently current.
    manifest = _load_json(root / MANIFEST_REL) or {}
    scanner_hash = scanner.get("scan_universe_manifest_hash")
    manifest_hash = manifest.get("universe_hash")
    manifest_hash_match: Optional[bool] = (
        (scanner_hash == manifest_hash)
        if scanner_hash and manifest_hash else None)
    market_as_of = scanner.get("market_as_of_date")
    required_session = manifest.get("required_market_session")
    market_session_match: Optional[bool] = (
        (market_as_of == required_session)
        if market_as_of and required_session else None)
    scan_readiness = (scanner.get("scan_integrity") or {}).get("readiness")

    integrity_warnings: List[str] = []
    if age_h is not None and age_h > STALE_SCAN_HOURS:
        integrity_warnings.append(
            f"SCAN STALE ({round(age_h, 1)}h old) — rerun research-scanner")
    if manifest_hash_match is False:
        integrity_warnings.append(
            f"scanner universe hash {scanner_hash} does not match the "
            f"current manifest {manifest_hash} — candidates may be from an "
            "older universe")
    if market_session_match is False:
        integrity_warnings.append(
            f"scanner market_as_of_date {market_as_of} does not match the "
            f"manifest required session {required_session}")
    if scan_readiness in ("DEGRADED", "BLOCKED"):
        reasons = "; ".join((scanner.get("scan_integrity") or {})
                            .get("readiness_reasons") or [])
        integrity_warnings.append(
            f"scan integrity {scan_readiness}: {reasons}")

    program_report = _load_json(root / PROGRAM_REL) or {}
    prog_blocks = program_report.get("programs") or {}
    label_meta: Dict[str, Dict[str, Any]] = {}
    for pid, p in prog_blocks.items():
        for lbl, lb in (p.get("label_verdicts") or {}).items():
            label_meta[lbl] = {"lifecycle_stage": lb.get("lifecycle_stage"),
                               "program_verdict": p.get("verdict")}

    radar = _load_json(root / RADAR_REL) or {}
    radar_priority: Dict[str, str] = {}
    if str(radar.get("generated_at") or "")[:10] == scan_date:
        for bucket, tickers in (radar.get("priority_tickers")
                                or {}).items():
            for t in tickers or []:
                radar_priority.setdefault(t, bucket)

    by_date, first_row = _ledger_index(root)
    prior_dates = sorted(d for d in by_date if d < scan_date)
    prev_date = prior_dates[-1] if prior_dates else None
    prev_set = by_date.get(prev_date, set()) if prev_date else set()
    earlier_set = set().union(*(by_date[d] for d in prior_dates[:-1])) \
        if len(prior_dates) > 1 else set()

    try:
        from dashboards.research_command_center.data_adapter import (
            ArtifactStore, build_fundamentals)
        store = ArtifactStore(root=root)

        def _quality(t: str) -> Optional[str]:
            f = build_fundamentals(t, store)
            return None if f.get("fallback") else f.get("quality_label")
    except Exception:
        def _quality(t: str) -> Optional[str]:
            return None

    programs: Dict[str, Dict[str, Any]] = {
        pid: {"candidate_count": 0, "new_count": 0, "repeat_count": 0,
              "returning_count": 0, "exited_count": 0,
              "candidates": [], "exited": []}
        for pid in PROGRAM_ORDER}

    latest_tickers = set()
    for item in scanner["watchlist"]:
        ticker = item.get("ticker")
        if not ticker:
            continue
        latest_tickers.add(ticker)
        route = route_candidate(item)
        pid = route["program"] if route["program"] in programs \
            else "TACTICAL"
        if ticker in prev_set:
            status = "REPEAT"
        elif ticker in earlier_set:
            status = "RETURNING"
        else:
            status = "NEW"
        label = item.get("watchlist_label")
        episode = first_row.get((ticker, label))
        meta = label_meta.get(label) or {}
        block = programs[pid]
        block["candidates"].append({
            "ticker": ticker,
            "detected_at": (episode or {}).get("appearance_date")
            or scan_date,
            "scan_status": status,
            "program": pid,
            "secondary_programs": route["secondary_programs"],
            "routing_reason": route["routing_reason"],
            "holding_period": f"{route['holding_period_td']} trading days",
            "label": label,
            "research_hypothesis": route["hypothesis"],
            "detection_reason": (item.get("why_appeared") or "")[:160],
            "priority": radar_priority.get(ticker),
            "confidence": route["confidence"],
            "data_confidence": item.get("data_confidence")
            or item.get("trust_level"),
            "research_score": item.get("research_score"),
            "fundamental_quality": _quality(ticker),
            "lifecycle_stage": meta.get("lifecycle_stage"),
            "evidence_maturity": _evidence_maturity(episode, pid),
            "current_benchmark": "SPY/QQQ/IWM + sector ETF",
            "program_verdict": meta.get("program_verdict")
            or "INSUFFICIENT_MATURE_EVIDENCE",
            # P0 as-of transparency: candidate bar date, benchmark bar
            # date, same-session validation, and the source scan time —
            # passed through from the scanner artifact, display-only.
            "bar_as_of_date": item.get("bar_as_of_date"),
            "benchmark_as_of_date": item.get("benchmark_as_of_date"),
            "same_session": item.get("same_session"),
            "source_scan_generated_at": scanner.get("generated_at"),
            # Display-only passthrough (Social Attention v1.1 Step A) so the
            # presentation layer can tell News Catalyst apart from Social
            # Attention Radar and surface EXHAUSTION_RISK instead of
            # rendering every social_arb_attention row identically.
            "scanner_category": item.get("category"),
            "crowd_stage": item.get("crowd_stage"),
            "signal_origin": item.get("signal_origin"),
            "research_only": True,
        })
        block["candidate_count"] += 1
        block[f"{status.lower()}_count"] += 1

    # rank within each program by research score (display order only)
    for block in programs.values():
        block["candidates"].sort(
            key=lambda c: -(c.get("research_score") or 0))
        for i, c in enumerate(block["candidates"], 1):
            c["rank"] = i

    # exited: in the previous scan, absent from the latest
    for ticker in sorted(prev_set - latest_tickers):
        prev_label = next(
            (lbl for (t, lbl), rec in first_row.items() if t == ticker),
            None)
        pid = classify_label(prev_label or "").get("program")
        if pid not in programs:
            pid = "TACTICAL"
        programs[pid]["exited"].append(
            {"ticker": ticker, "last_label": prev_label})
        programs[pid]["exited_count"] += 1

    return {
        "kind": "latest_scan_programs",
        "version": 1,
        "present": True,
        "generated_at": now.isoformat(),
        "research_only": True,
        "scan_timestamp": scan_ts,
        "scan_age_hours": round(age_h, 2) if age_h is not None else None,
        "scan_stale": bool(age_h is not None
                           and age_h > STALE_SCAN_HOURS),
        "scan_success": True,
        "source_artifact": str(SCANNER_REL),
        # P0 integrity linkage: which validated universe + session
        # produced these candidates, and whether they still match.
        "market_as_of_date": market_as_of,
        "manifest_required_session": required_session,
        "scanner_manifest_hash": scanner_hash,
        "manifest_universe_hash": manifest_hash,
        "manifest_hash_match": manifest_hash_match,
        "market_session_match": market_session_match,
        "scan_readiness": scan_readiness,
        "integrity_warnings": integrity_warnings,
        "previous_scan_date": prev_date,
        "candidate_count": len(latest_tickers),
        "program_count": sum(1 for b in programs.values()
                             if b["candidate_count"]),
        "exclusions": {
            "stale_price_skipped":
                scanner.get("stale_price_skipped_count"),
            "data_suspect_skipped":
                scanner.get("data_suspect_skipped_count"),
        },
        "guardrails": {"no_selection_change": True,
                       "no_trade_recommendation": True,
                       "priority_is_not_validated_alpha": True},
        "programs": programs,
    }


# ── rendering ────────────────────────────────────────────────────────────────


def _session_header_lines(payload: Dict[str, Any]) -> List[str]:
    """Scan-integrity header shared by the compact and verbose renders —
    session linkage and warnings are safety-critical, never elided."""
    session_bits = [
        f"market as-of {payload.get('market_as_of_date') or '?'}"]
    if payload.get("manifest_hash_match") is not None:
        session_bits.append(
            "universe hash "
            + ("MATCH" if payload["manifest_hash_match"] else "MISMATCH")
            + f" ({payload.get('scanner_manifest_hash') or '?'})")
    if payload.get("scan_readiness"):
        session_bits.append(f"readiness {payload['scan_readiness']}")
    lines = ["  " + " · ".join(session_bits)]
    for warning in payload.get("integrity_warnings") or []:
        lines.append(f"  ⚠ {warning}")
    return lines


def render_terminal(payload: Dict[str, Any], cap: int = TERMINAL_CAP,
                    verbose: bool = False) -> str:
    """Compact top-candidates view by default; ``verbose=True`` restores
    the full per-candidate metadata block (also used for the log twin)."""
    if verbose:
        return render_terminal_verbose(payload, cap=cap)
    if not payload.get("present"):
        return ("=== TOP RESEARCH CANDIDATES ===\n"
                "  (no successful scan artifact)")
    from dashboards.research_command_center.presentation import (
        candidate_badges, common_reason, data_issue_reasons,
        evidence_summary, short_reason, split_candidates, verdict_display)
    lines = ["=== TOP RESEARCH CANDIDATES ==="]
    lines += _session_header_lines(payload)
    for pid in PROGRAM_ORDER:
        block = (payload.get("programs") or {}).get(pid) or {}
        n = block.get("candidate_count", 0)
        hold = (PROGRAMS.get(pid) or {}).get(
            "expected_holding_period_td", "?")
        counts = (f"{n} active · {block.get('new_count', 0)} new · "
                  f"{block.get('repeat_count', 0)} repeat · "
                  f"{block.get('returning_count', 0)} returning · "
                  f"{block.get('exited_count', 0)} exited")
        top, rest, issues = split_candidates(block, cap)
        verdict = next((c.get("program_verdict")
                        for c in block.get("candidates") or []), None)
        lines.append(f"\n{pid} {hold}td — {counts}"
                     + (f" · validation: {verdict_display(verdict)}"
                        if verdict else ""))
        if not n:
            lines.append("  No candidates detected in the latest scan.")
            continue
        shared = common_reason(block)
        if shared:
            lines.append(f"  common reason: {shared[:100]}")
        for i, c in enumerate(top, 1):
            badges = "  ".join(b["text"].replace(" ", "_")
                               for b in candidate_badges(c)
                               if b["text"] != c.get("scan_status"))
            lines.append(
                f"  {i}. {c['ticker']:<6} score={c.get('research_score')}"
                f"  {c['scan_status']}  {c.get('label') or '?'}"
                + (f"  {badges}" if badges else ""))
            detail = ("" if shared else f"{short_reason(c, 100)}  | ")
            lines.append(
                f"     {detail}"
                f"{evidence_summary(c.get('evidence_maturity'))}")
        if issues:
            lines.append(
                f"  data issues ({len(issues)}): "
                + ", ".join(
                    f"{c['ticker']} ({'/'.join(data_issue_reasons(c))})"
                    for c in issues))
        if rest:
            lines.append(f"  ... and {len(rest)} more")
    lines.append("\nUse --all or --program-details for full candidate "
                 "metadata.")
    return "\n".join(lines)


def render_terminal_verbose(payload: Dict[str, Any],
                            cap: int = TERMINAL_CAP) -> str:
    if not payload.get("present"):
        return ("=== LATEST RESEARCH CANDIDATES BY PROGRAM ===\n"
                "  (no successful scan artifact)")
    lines = ["=== LATEST RESEARCH CANDIDATES BY PROGRAM ==="]
    lines += _session_header_lines(payload)
    for pid in PROGRAM_ORDER:
        block = (payload.get("programs") or {}).get(pid) or {}
        n = block.get("candidate_count", 0)
        counts = (f"new {block.get('new_count', 0)} · repeat "
                  f"{block.get('repeat_count', 0)} · returning "
                  f"{block.get('returning_count', 0)} · exited "
                  f"{block.get('exited_count', 0)}")
        lines.append(f"\n{pid} — {n} candidate{'s' if n != 1 else ''} "
                     f"({counts})")
        if not n:
            lines.append("  No candidates detected in the latest scan.")
            continue
        for c in block["candidates"][:cap]:
            session_tag = ("same-session" if c.get("same_session")
                           else "SESSION_MISMATCH"
                           if c.get("same_session") is False else "?")
            lines += [
                f"  {c['ticker']:<8} {c['scan_status']:<9} "
                f"hold={c['holding_period'].split(' ')[0]}td  "
                f"score={c.get('research_score')}"
                + (f"  priority={c['priority']}" if c.get("priority")
                   else ""),
                f"           reason={c['detection_reason'][:90]}",
                f"           lifecycle={c.get('lifecycle_stage') or '?'}  "
                f"verdict={c.get('program_verdict')}",
                f"           bar={c.get('bar_as_of_date') or '?'}  "
                f"bench={c.get('benchmark_as_of_date') or '?'}  "
                f"{session_tag}",
            ]
        if n > cap:
            lines.append(f"  ... and {n - cap} more (see sidecar)")
    return "\n".join(lines)


def write_outputs(payload: Dict[str, Any],
                  root: Optional[Path] = None) -> Path:
    root = Path(root) if root else REPO_ROOT
    sidecar = root / SIDECAR_REL
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2)
                       + "\n", encoding="utf-8")
    log = root / LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)
    # The log twin is the audit copy — always the full verbose render.
    log.write_text(render_terminal(payload, cap=50, verbose=True) + "\n",
                   encoding="utf-8")
    return sidecar


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Latest-scan candidates by research program "
                    "(cache-only; visibility layer, no selection change).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all", action="store_true",
                        help="verbose render of every candidate")
    parser.add_argument("--program-details", action="store_true",
                        help="verbose render with full candidate metadata")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    payload = build_latest_scan(root)
    verbose = args.all or args.program_details
    print(render_terminal(payload,
                          cap=10 ** 6 if args.all else TERMINAL_CAP,
                          verbose=verbose))
    if not args.dry_run:
        print(f"\nwritten: {write_outputs(payload, root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
