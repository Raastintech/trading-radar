#!/usr/bin/env python3
"""Scan-exclusion impact report.

Cache-only audit of names removed by the validated scan-universe manifest.
This script does not rerun scanner logic, alter thresholds, infer scores, or
simulate qualification. It reports deterministic artifact reachability:
excluded names, exclusion reasons, source clusters, and whether those names
appear in downstream scanner/routing/High-Conviction/Emerging artifacts.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = Path("cache") / "research"

MANIFEST_REL = RESEARCH_DIR / "scan_universe_manifest_latest.json"
UNIVERSE_BUILD_REL = RESEARCH_DIR / "research_universe_build_latest.json"
SCANNER_REL = RESEARCH_DIR / "research_scanner_latest.json"
ROUTING_REL = RESEARCH_DIR / "latest_scan_programs_latest.json"
HC_REL = RESEARCH_DIR / "high_conviction_alpha_latest.json"
EO_REL = RESEARCH_DIR / "emerging_outlier_watch_latest.json"
SIDECAR_REL = RESEARCH_DIR / "scan_exclusion_impact_latest.json"
LOG_REL = Path("logs") / "scan_exclusion_impact_latest.txt"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _security_type(ticker: str) -> str:
    t = str(ticker or "").upper()
    if t.endswith(("WS", "WT")) or (len(t) >= 5 and t.endswith("W")):
        return "POSSIBLE_WARRANT"
    if len(t) >= 5 and t.endswith(("U", "R")):
        return "POSSIBLE_UNIT_OR_RIGHT"
    if "." in t or "-" in t:
        return "UNSUPPORTED_SHARE_CLASS_FORMAT"
    return "COMMON_STOCK_OR_ADR"


def _market_cap_bucket(value: Optional[float]) -> str:
    try:
        mc = float(value)
    except Exception:
        return "UNKNOWN"
    if mc < 300_000_000:
        return "MICRO_CAP"
    if mc < 2_000_000_000:
        return "SMALL_CAP"
    if mc < 10_000_000_000:
        return "MID_CAP"
    if mc < 200_000_000_000:
        return "LARGE_CAP"
    return "MEGA_CAP"


def _routing_tickers(routing: Dict[str, Any]) -> set:
    out = set()
    for block in (routing.get("programs") or {}).values():
        for c in block.get("candidates") or []:
            t = str(c.get("ticker") or "").upper()
            if t:
                out.add(t)
    return out


def _hc_tickers(hc: Dict[str, Any]) -> set:
    out = set()
    for key in ("shortlist", "quality_but_extended",
                "improving_but_unproven", "rejected"):
        for c in hc.get(key) or []:
            t = str(c.get("ticker") or "").upper()
            if t:
                out.add(t)
    return out


def _eo_tickers(eo: Dict[str, Any]) -> set:
    return {str(c.get("ticker") or "").upper()
            for c in (eo.get("watch") or []) if c.get("ticker")}


def build_report(root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    manifest = _load(root / MANIFEST_REL)
    universe = _load(root / UNIVERSE_BUILD_REL)
    scanner = _load(root / SCANNER_REL)
    routing = _load(root / ROUTING_REL)
    hc = _load(root / HC_REL)
    eo = _load(root / EO_REL)

    source_map = {
        str(e.get("ticker") or "").upper(): e.get("source") or "UNKNOWN"
        for e in universe.get("universe_full") or []
        if e.get("ticker")
    }
    scanner_items = {
        str(e.get("ticker") or "").upper(): e
        for e in scanner.get("watchlist") or []
        if e.get("ticker")
    }
    routing_tickers = _routing_tickers(routing)
    hc_tickers = _hc_tickers(hc)
    eo_tickers = _eo_tickers(eo)

    excluded_by_reason = manifest.get("excluded_by_reason") or {}
    records: List[Dict[str, Any]] = []
    for reason, tickers in sorted(excluded_by_reason.items()):
        for raw in tickers or []:
            ticker = str(raw or "").upper()
            item = scanner_items.get(ticker) or {}
            present_downstream = {
                "scanner_watchlist": ticker in scanner_items,
                "program_routing": ticker in routing_tickers,
                "high_conviction": ticker in hc_tickers,
                "emerging_outlier": ticker in eo_tickers,
            }
            records.append({
                "ticker": ticker,
                "exclusion_reason": reason,
                "universe_source": source_map.get(ticker, "UNKNOWN"),
                "security_type": _security_type(ticker),
                "market_cap": item.get("market_cap"),
                "market_cap_bucket": _market_cap_bucket(item.get("market_cap")),
                "sector": item.get("sector") or "UNKNOWN",
                "data_provider_issue": reason in {
                    "refresh_failed", "no_bar_for_session",
                    "budget_truncated",
                },
                "present_downstream": present_downstream,
                "would_have_entered_pipeline_assessment": (
                    "NOT_MEASURABLE_WITHOUT_RERUNNING_SCANNER_LOGIC"
                ),
                "pipeline_impact": (
                    "DATA_VALIDITY_EXCLUDED_BEFORE_SCANNER_SCORING"
                    if not any(present_downstream.values())
                    else "CHECK_ARTIFACT_INCONSISTENCY"
                ),
                "source_artifact": str(MANIFEST_REL),
            })

    def _count(field: str) -> Dict[str, int]:
        return dict(Counter(str(r.get(field) or "UNKNOWN") for r in records))

    excluded_count = len(records)
    impact_wording = None
    if excluded_count:
        impact_wording = (
            f"Usable with caveats \u2014 {excluded_count} names excluded after "
            "delta refresh."
        )

    warnings = []
    if excluded_count and manifest.get("scan_readiness") == "DEGRADED":
        warnings.append(impact_wording)
    if any(r["pipeline_impact"] == "CHECK_ARTIFACT_INCONSISTENCY"
           for r in records):
        warnings.append(
            "An excluded ticker appears downstream; inspect artifact cadence."
        )

    return {
        "kind": "scan_exclusion_impact_report",
        "version": 1,
        "generated_at": _utcnow(),
        "research_only": True,
        "manifest_generated_at": manifest.get("generated_at"),
        "required_market_session": manifest.get("required_market_session"),
        "scan_readiness": manifest.get("scan_readiness"),
        "readiness_reasons": manifest.get("readiness_reasons") or [],
        "universe_hash": manifest.get("universe_hash"),
        "excluded_count": excluded_count,
        "impact_wording": impact_wording,
        "excluded_tickers": [r["ticker"] for r in records],
        "excluded": records,
        "summary": {
            "by_reason": _count("exclusion_reason"),
            "by_universe_source": _count("universe_source"),
            "by_security_type": _count("security_type"),
            "by_market_cap_bucket": _count("market_cap_bucket"),
            "by_sector": _count("sector"),
            "by_pipeline_impact": _count("pipeline_impact"),
        },
        "warnings": warnings,
        "guardrails": {
            "no_scanner_rerun": True,
            "no_selection_change": True,
            "no_score_or_rank_change": True,
            "no_trade_recommendation": True,
        },
    }


def render_text(report: Dict[str, Any]) -> str:
    lines = [
        "SCAN EXCLUSION IMPACT REPORT (cache-only; research-only)",
        f"generated_at: {report.get('generated_at')}",
        f"manifest: {report.get('manifest_generated_at')}  "
        f"session: {report.get('required_market_session')}  "
        f"readiness: {report.get('scan_readiness')}",
        report.get("impact_wording") or "No manifest exclusions reported.",
        "",
        "SUMMARY",
    ]
    for key, counts in (report.get("summary") or {}).items():
        lines.append(f"  {key}: {counts}")
    if report.get("excluded"):
        lines += ["", "EXCLUDED"]
        for rec in report["excluded"][:80]:
            lines.append(
                f"  {rec['ticker']:8s} {rec['exclusion_reason']:22s} "
                f"source={rec['universe_source']} "
                f"type={rec['security_type']} "
                f"cap={rec['market_cap_bucket']} "
                f"impact={rec['pipeline_impact']}"
            )
        if len(report["excluded"]) > 80:
            lines.append(f"  ... and {len(report['excluded']) - 80} more")
    return "\n".join(lines)


def write_outputs(report: Dict[str, Any], root: Optional[Path] = None) -> Path:
    root = Path(root) if root else REPO_ROOT
    sidecar = root / SIDECAR_REL
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    log = root / LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(render_text(report) + "\n", encoding="utf-8")
    return sidecar


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan-exclusion impact report (cache-only).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    report = build_report(root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    if not args.dry_run:
        print(f"\nwritten: {write_outputs(report, root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
