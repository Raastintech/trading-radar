#!/usr/bin/env python3
"""
research/daily_alpha_radar_report.py — Daily Alpha Radar Report generator.

Reads existing research sidecars (scanner, coverage, changes, forward tracker,
10x radar, market heartbeat) and generates a clean, quality-gated daily report.

This module:
  - Applies strict priority gating (priority_label)
  - Applies quality-adjusted consensus scoring
  - Applies options coverage guard (no options promotion when coverage < 50%)
  - Validates catalyst/social signals via catalyst_sanity
  - Removes ALL legacy strategy language (VOYAGER, SNIPER, SHORT_A, etc.)
  - Separates TRUE_10X_RESEARCH from ASYMMETRIC_RECOVERY_WATCH

Output:
  docs/research/DAILY_ALPHA_RADAR_REPORT.md
  cache/research/daily_alpha_radar_latest.json

Research-only. No trade recommendations. No provider calls.

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/daily_alpha_radar_report.py
  ./scripts/run_research_cycle.sh daily-alpha-radar
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None  # type: ignore[assignment]

if load_dotenv is not None and os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if env_path:
        load_dotenv(env_path, override=False)
    load_dotenv(ROOT / ".env", override=False)

os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DB_PATH", str(ROOT / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(ROOT / "cache"))
os.environ.setdefault("LOG_DIR", str(ROOT / "logs"))

import core.config as cfg
from core.research_mode import SYSTEM_MODE, RESEARCH_ONLY_BANNER
from research.research_scoring import (
    earliness_detail,
    priority_label,
    quality_adjusted_consensus,
    TOP_RESEARCH,
    HIGH_PRIORITY_RESEARCH,
    WATCHLIST_RESEARCH,
    RESET_WATCH,
    RECLAIM_WATCH,
    CONFLICTED_SIGNAL,
    EXTENDED_CROWDED,
    DATA_QUARANTINE,
    INVALID_PRIORITY,
    PRIORITY_LABELS,
    UNKNOWN_EARLINESS,
    INVALIDATED,
    EXTENDED,
    LATE,
)
from research.ten_x_candidate_radar import TRUE_10X_RESEARCH, ASYMMETRIC_RECOVERY_WATCH, THEME_ONLY
from research.catalyst_sanity import FRESH_COMPANY_SPECIFIC
from research.research_watchlist_forward_tracker import (
    SAMPLE_TOO_EARLY, SAMPLE_PROVISIONAL, SAMPLE_MEANINGFUL, SAMPLE_ROBUST,
)
from research.research_candidate_enrichment import (
    classify_quarantine_subtype,
    QUARANTINE_INVALID,
    QUARANTINE_INSUFFICIENT_HISTORY,
    QUARANTINE_LOW_LIQUIDITY,
    QUARANTINE_DATA_INCOMPLETE,
    QUARANTINE_DATA_QUARANTINE,
)

VERSION = "DAILY_ALPHA_RADAR_V1"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
OUT_JSON = RESEARCH_DIR / "daily_alpha_radar_latest.json"
REPORT_PATH = ROOT / "docs" / "research" / "DAILY_ALPHA_RADAR_REPORT.md"

# Options coverage thresholds
OPTIONS_COVERAGE_DISABLED_THRESHOLD = 0.50   # below this: options overlay disabled
OPTIONS_COVERAGE_PARTIAL_THRESHOLD = 0.80    # below this: partial overlay with warning

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("daily_alpha_radar_report")


# ── Sidecar loaders ──────────────────────────────────────────────────────────

def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_sidecars() -> Dict[str, Optional[Dict[str, Any]]]:
    return {
        "scanner": _load_json(RESEARCH_DIR / "research_scanner_latest.json"),
        "coverage": _load_json(RESEARCH_DIR / "research_coverage_latest.json"),
        "changes": _load_json(RESEARCH_DIR / "research_changes_latest.json"),
        "forward": _load_json(RESEARCH_DIR / "research_forward_latest.json"),
        "ten_x": _load_json(RESEARCH_DIR / "ten_x_candidates_latest.json"),
        "heartbeat": _load_json(cfg.CACHE_DIR / "research" / "market_heartbeat_latest.json"),
        "sector_leadership": _load_json(cfg.CACHE_DIR / "research" / "sector_leadership_latest.json"),
    }


# ── Options coverage guard ───────────────────────────────────────────────────

def _options_coverage_state(scanner_watchlist: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Check how many watchlist tickers have options snapshot data."""
    total = len(scanner_watchlist)
    if total == 0:
        return {"coverage_pct": 0.0, "overlay_enabled": False, "state": "DISABLED_NO_TICKERS"}

    options_dir = cfg.CACHE_DIR / "options_chains"
    covered = 0
    for item in scanner_watchlist:
        sym = item.get("ticker", "")
        if not sym:
            continue
        patterns = [f"{sym}_*.parquet", f"{sym}_*.json", f"{sym}.json"]
        if options_dir.exists() and any(bool(list(options_dir.glob(p))) for p in patterns):
            covered += 1

    coverage_pct = covered / total if total > 0 else 0.0

    if coverage_pct < OPTIONS_COVERAGE_DISABLED_THRESHOLD:
        state = "DISABLED"
        overlay_enabled = False
    elif coverage_pct < OPTIONS_COVERAGE_PARTIAL_THRESHOLD:
        state = "PARTIAL"
        overlay_enabled = True
    else:
        state = "NORMAL"
        overlay_enabled = True

    return {
        "covered": covered,
        "total": total,
        "coverage_pct": round(coverage_pct, 3),
        "overlay_enabled": overlay_enabled,
        "state": state,
    }


# ── Coverage audit cross-reference ──────────────────────────────────────────

def _coverage_by_ticker(coverage: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not coverage:
        return {}
    return {t["ticker"]: t for t in coverage.get("tickers", [])}


# ── Item enrichment and priority gating ─────────────────────────────────────

def _enrich_with_priority(
    item: Dict[str, Any],
    coverage_map: Dict[str, Dict[str, Any]],
    options_state: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply quality gates and priority label to a single watchlist item."""
    ticker = item.get("ticker", "")
    cov = coverage_map.get(ticker, {})

    data_confidence = cov.get("confidence") or item.get("data_confidence")
    # Use coverage-sidecar bars for validity; fall back to item's own enriched fields
    if cov:
        ticker_valid = cov.get("price_bars", 0) >= 20
        liquidity_ok = cov.get("price_bars", 0) >= 60
    else:
        ticker_valid = item.get("ticker_valid")   # Phase 4A.3 enrichment field
        liquidity_ok = item.get("liquidity_ok")   # Phase 4A.3 enrichment field

    # Earliness detail
    ed = earliness_detail(
        rs_63=item.get("rs_63d_vs_spy"),
        rs_20=item.get("rs_20d_vs_spy"),
        above_ma50=item.get("above_ma50"),
        above_ma200=item.get("above_ma200"),
        dd_from_high_pct=item.get("dd_from_high_pct"),
        vol_trend_ratio=item.get("vol_trend_ratio"),
        extension_vs_ma200_pct=item.get("extension_vs_ma200_pct"),
    )
    earliness = ed["label"]
    missing_fields = ed["missing_fields"] if earliness == UNKNOWN_EARLINESS else []
    extension_state = ed["extension_state"]

    # Conflict detection
    conflict_flags: List[str] = []
    watchlist_label = item.get("watchlist_label", "")
    # Extended label but in early_accumulation category = conflict
    if watchlist_label in ("EXTENDED", "CROWDED") and item.get("category") == "early_accumulation":
        conflict_flags.append("extended_but_in_early_accumulation")
    # Risky but has catalyst
    if watchlist_label == "RISKY" and item.get("has_analyst_upgrade"):
        conflict_flags.append("risky_with_catalyst")

    # Phase 4A.4: catalyst / social sanity gate.
    # If the scanner validated the signal and it failed (can_upgrade=False), remove
    # that category from the consensus count so unvalidated signals can't inflate
    # DOUBLE_CONFIRMATION or MULTI_CONFIRMATION.  Only add a conflict flag when the
    # item's primary category is the failed one (not when it also has a clean signal
    # from another category that already explains its presence on the board).
    all_cats = item.get("all_categories", [item.get("category", "")])
    catalyst_can_upgrade = item.get("catalyst_can_upgrade")  # None = not checked (pass through)
    _social_catalyst_cats = {"social_arb_attention", "catalyst_watch"}
    if catalyst_can_upgrade is False:
        effective_cats = [c for c in all_cats if c not in _social_catalyst_cats]
        if not effective_cats:
            # Item is purely social/catalyst and signal failed — keep original for
            # display but add conflict flag to downgrade consensus.
            effective_cats = all_cats
            if item.get("category") in _social_catalyst_cats:
                conflict_flags.append("catalyst_not_validated")
    else:
        effective_cats = all_cats

    # Social-only check (uses effective_cats so failed social signals don't count)
    social_only = set(effective_cats) <= {"social_arb_attention"} and len(effective_cats) > 0

    # Quality-adjusted consensus
    qac = quality_adjusted_consensus(
        categories=effective_cats,
        research_score=item.get("research_score"),
        data_confidence=data_confidence,
        earliness=earliness,
        extension_state=extension_state,
        conflict_flags=conflict_flags or None,
        social_only=social_only,
        liquidity_ok=liquidity_ok,
    )

    # Final priority label
    pri, downgrade_reasons = priority_label(
        data_confidence=data_confidence,
        ticker_valid=ticker_valid,
        liquidity_ok=liquidity_ok,
        earliness=earliness,
        consensus=qac["consensus_label"],
        extension_vs_ma200_pct=item.get("extension_vs_ma200_pct"),
        conflict_flags=conflict_flags or None,
        missing_fields=missing_fields or None,
        adj_consensus_score=qac["quality_adjusted_score"],
    )

    # Options: never promote based on options when overlay is disabled
    options_note = None
    if not options_state.get("overlay_enabled"):
        options_note = "OPTIONS_DATA_UNAVAILABLE"

    enriched = dict(item)
    enriched.update({
        "priority_label": pri,
        "downgrade_reasons": downgrade_reasons,
        "earliness_label": earliness,
        "earliness_score": ed["earliness_score"],
        "missing_earliness_fields": missing_fields,
        "extension_state": extension_state,
        "data_confidence": data_confidence or "UNKNOWN",
        "quality_adjusted_consensus_score": qac["quality_adjusted_score"],
        "raw_consensus_score": qac["raw_consensus_score"],
        "consensus_label": qac["consensus_label"],
        "conflict_flags": conflict_flags,
        "options_context": options_note,
        "ticker_valid": ticker_valid,
        "liquidity_ok": liquidity_ok,
        # Phase 4A.4: catalyst/social validation result (from scanner pass)
        "catalyst_sanity_label": item.get("catalyst_sanity_label"),
        "catalyst_can_upgrade": item.get("catalyst_can_upgrade"),
        "catalyst_sanity_issues": item.get("catalyst_sanity_issues", []),
    })

    # Phase 4A.3: classify quarantine sub-type for breakdown display
    if pri in (DATA_QUARANTINE, INVALID_PRIORITY):
        enriched["quarantine_subtype"] = classify_quarantine_subtype(enriched)

    return enriched


# ── Section bucketing ────────────────────────────────────────────────────────

def _bucket_items(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "top_research": [],
        "high_priority": [],
        "early_accumulation": [],
        "reclaim_watch": [],
        "reset_watch": [],
        "conflicted": [],
        "quarantine": [],
        "extended_crowded": [],
        "watchlist": [],
    }
    for item in items:
        pri = item.get("priority_label", WATCHLIST_RESEARCH)
        if pri == TOP_RESEARCH:
            buckets["top_research"].append(item)
        elif pri == HIGH_PRIORITY_RESEARCH:
            # Sub-bucket by category for early accumulation section
            if item.get("category") == "early_accumulation":
                buckets["early_accumulation"].append(item)
            else:
                buckets["high_priority"].append(item)
        elif pri == RECLAIM_WATCH:
            buckets["reclaim_watch"].append(item)
        elif pri == RESET_WATCH:
            buckets["reset_watch"].append(item)
        elif pri == CONFLICTED_SIGNAL:
            buckets["conflicted"].append(item)
        elif pri in (DATA_QUARANTINE, INVALID_PRIORITY):
            buckets["quarantine"].append(item)
        elif pri == EXTENDED_CROWDED:
            buckets["extended_crowded"].append(item)
        else:
            buckets["watchlist"].append(item)

    for key in buckets:
        buckets[key].sort(key=lambda x: x.get("quality_adjusted_consensus_score", 0), reverse=True)

    return buckets


# ── Report assembly ──────────────────────────────────────────────────────────

def _fmt_item(item: Dict[str, Any], include_details: bool = True) -> str:
    ticker = item.get("ticker", "?")
    pri = item.get("priority_label", "?")
    earliness = item.get("earliness_label", "?")
    e_score = item.get("earliness_score")
    consensus = item.get("consensus_label", "?")
    qscore = item.get("quality_adjusted_consensus_score", 0)
    confidence = item.get("data_confidence", "?")
    ext_state = item.get("extension_state", "?")
    sector = item.get("sector") or ""
    liq_ok = item.get("liquidity_ok")
    ticker_valid = item.get("ticker_valid")
    conflicts = item.get("conflict_flags", [])
    reasons = item.get("downgrade_reasons", [])
    missing = item.get("missing_fields") or item.get("missing_earliness_fields") or []
    why = item.get("why_appeared", "")
    confirms = item.get("confirms_if", "")
    invalidates = item.get("invalidates_if", "")
    q_sub = item.get("quarantine_subtype", "")

    parts = [f"**{ticker}**"]
    parts.append(f"| priority={pri} | earliness={earliness} | consensus={consensus}")
    parts.append(f"| qscore={qscore:.0f} | confidence={confidence} | ext={ext_state}")
    if e_score is not None:
        parts.append(f"| escore={e_score:.0f}")
    if sector:
        parts.append(f"| sector={sector}")
    if liq_ok is False:
        parts.append("| liq=LOW")
    if ticker_valid is False:
        parts.append("| INVALID_TICKER")

    line = " ".join(parts)
    if not include_details:
        sub_note = f" [{q_sub}]" if q_sub else ""
        return f"- {line}{sub_note}"

    lines = [f"- {line}"]
    if q_sub:
        lines.append(f"  - *Quarantine reason:* {q_sub}")
    # Phase 4A.4: show catalyst/social sanity verdict when it influenced priority
    cat_label = item.get("catalyst_sanity_label")
    cat_issues = item.get("catalyst_sanity_issues") or []
    if cat_label and cat_label != FRESH_COMPANY_SPECIFIC:
        issue_str = f" ({', '.join(cat_issues[:2])})" if cat_issues else ""
        lines.append(f"  - *Catalyst sanity:* {cat_label}{issue_str}")
    if conflicts:
        lines.append(f"  - **CONFLICTS:** {', '.join(conflicts)}")
    if reasons:
        lines.append(f"  - *Downgraded:* {', '.join(reasons[:3])}")
    if missing:
        lines.append(f"  - *Missing fields:* {', '.join(missing[:5])}")
    if why:
        lines.append(f"  - *Why appeared:* {why[:100]}")
    if confirms:
        lines.append(f"  - *Confirms if:* {confirms[:80]}")
    if invalidates:
        lines.append(f"  - *Invalidates if:* {invalidates[:80]}")
    return "\n".join(lines)


def _fmt_section(title: str, items: List[Dict[str, Any]], max_items: int = 15, details: bool = True) -> List[str]:
    lines = [f"\n## {title}", ""]
    if not items:
        lines.append("*(none)*")
        return lines
    for item in items[:max_items]:
        lines.append(_fmt_item(item, include_details=details))
    if len(items) > max_items:
        lines.append(f"*... and {len(items) - max_items} more (see JSON sidecar)*")
    return lines


def _fmt_forward_tracker(forward: Optional[Dict[str, Any]]) -> List[str]:
    lines = ["\n## Forward Tracker Status", ""]
    if not forward:
        lines.append("*Forward tracker sidecar not available. Run `research-forward-tracker` first.*")
        return lines

    overall = forward.get("overall", {})
    status = overall.get("sample_status", SAMPLE_TOO_EARLY)
    n = overall.get("matured_entries", 0)
    verdict = overall.get("verdict", "NEED_MORE_DATA")

    lines.append(f"**Overall:** n={n} matured | sample_status={status} | verdict={verdict}")
    lines.append("")

    if status == SAMPLE_TOO_EARLY:
        lines.append(
            f"> ⚠ TOO_EARLY: {n} matured observations. "
            f"Need ≥10 for provisional read, ≥30 for meaningful, ≥100 for robust. "
            "No bucket has interpretable evidence yet."
        )
        lines.append("")

    by_label = forward.get("verdicts_by_label", [])
    if by_label:
        lines.append("| Bucket | n_total | n_matured | sample_status | verdict |")
        lines.append("|--------|---------|-----------|---------------|---------|")
        for v in by_label:
            ss = v.get("sample_status", SAMPLE_TOO_EARLY)
            lines.append(
                f"| {v['bucket']:<22} | {v['total_entries']:7d} | {v['matured_entries']:9d} "
                f"| {ss:<13} | {v['verdict']} |"
            )
    return lines


def _fmt_market_context(heartbeat: Optional[Dict[str, Any]]) -> List[str]:
    lines = ["\n## Market Context", ""]
    if not heartbeat:
        lines.append("*Market heartbeat sidecar not available. Run `market-heartbeat` first.*")
        return lines
    regime = heartbeat.get("regime_label", "UNKNOWN")
    trend = heartbeat.get("trend_label", "UNKNOWN")
    generated = heartbeat.get("generated_at", "unknown")[:10]
    lines.append(f"**Regime:** {regime} | **Trend:** {trend} | *as of {generated}*")

    sector_rs = heartbeat.get("sector_rs", {})
    if sector_rs:
        lines.append("")
        lines.append("**Sector RS vs SPY (20d):**")
        top_sectors = sorted(sector_rs.items(), key=lambda x: x[1] or 0, reverse=True)[:5]
        for sec, rs in top_sectors:
            bar = "+" if (rs or 0) > 0 else ""
            lines.append(f"  - {sec}: {bar}{rs:.1f}%" if rs is not None else f"  - {sec}: n/a")
    return lines


def _fmt_data_coverage(coverage: Optional[Dict[str, Any]], options_state: Dict[str, Any]) -> List[str]:
    lines = ["\n## Data Coverage", ""]
    if not coverage:
        lines.append("*Coverage audit not available. Run `research-coverage` first.*")
    else:
        counts = coverage.get("confidence_counts", {})
        total = coverage.get("total_tickers", 0)
        actionable = coverage.get("actionable_pct", 0)
        lines.append(f"**Tickers:** {total} total | Actionable (HIGH+MEDIUM): {actionable:.1f}%")
        lines.append(
            f"  HIGH={counts.get('HIGH', 0)} | MEDIUM={counts.get('MEDIUM', 0)} | "
            f"LOW={counts.get('LOW', 0)} | INVALID={counts.get('INVALID', 0)}"
        )
    lines.append("")
    opt_state = options_state.get("state", "UNKNOWN")
    opt_pct = options_state.get("coverage_pct", 0) * 100
    lines.append(f"**Options Coverage:** {opt_pct:.0f}% | Overlay: {opt_state}")
    if opt_state == "DISABLED":
        lines.append(
            "  > ⚠ OPTIONS_DATA_UNAVAILABLE: coverage below 50% threshold. "
            "No candidate will be promoted based on options data."
        )
    elif opt_state == "PARTIAL":
        lines.append("  > ⚠ Partial options coverage — overlay active with warning.")
    return lines


def _fmt_scanner_field_coverage(field_coverage: Dict[str, Any], quarantine_breakdown: Dict[str, int]) -> List[str]:
    """Phase 4A.3: Scanner enrichment coverage stats and quarantine breakdown."""
    lines = ["\n## Scanner Field Coverage", ""]
    n = field_coverage.get("total", 0)
    if n == 0:
        lines.append("*No candidates to measure.*")
        return lines

    def pct(k: str) -> str:
        v = field_coverage.get(k, 0)
        return f"{v}/{n} ({100*v//n}%)" if n else "—"

    lines.append(f"| Field | Coverage |")
    lines.append(f"|-------|----------|")
    lines.append(f"| `above_ma200` populated | {pct('above_ma200_populated')} |")
    lines.append(f"| `above_ma50` populated | {pct('above_ma50_populated')} |")
    lines.append(f"| `rs_63d_vs_spy` populated | {pct('rs_63d_populated')} |")
    lines.append(f"| `sector` populated | {pct('sector_populated')} |")
    lines.append(f"| `liquidity_ok` populated | {pct('liquidity_ok_populated')} |")
    lines.append(f"| Earliness non-UNKNOWN | {pct('earliness_non_unknown')} |")
    lines.append("")

    if quarantine_breakdown:
        lines.append("**Quarantine breakdown:**")
        for sub, cnt in sorted(quarantine_breakdown.items(), key=lambda x: -x[1]):
            lines.append(f"  - {sub}: {cnt}")

    return lines


def _fmt_changes(changes: Optional[Dict[str, Any]]) -> List[str]:
    lines = ["\n## What Changed Today", ""]
    if not changes:
        lines.append("*Change detector sidecar not available. Run `research-changes` first.*")
        return lines
    summary = changes.get("summary", "no changes")
    lines.append(f"**Summary:** {summary}")
    change_list = changes.get("changes", [])
    if change_list:
        lines.append("")
        for c in change_list[:20]:
            ct = c.get("change_type", "?")
            t = c.get("ticker", "?")
            detail = c.get("score_delta", c.get("prev_label", ""))
            lines.append(f"  - {ct}: **{t}** {detail}")
    return lines


def _fmt_safety_confirmations() -> List[str]:
    return [
        "\n## Safety Confirmations",
        "",
        "- **RESEARCH_ONLY_MODE:** All execution flags are False",
        "- **NO TRADE-ACTION GUIDANCE** generated in this report",
        "- **NO DIRECTIONAL CALLS** — all candidates require manual research",
        "- **NO EXECUTION PARAMETERS** of any kind in any output",
        "- **NO PAPER-LEDGER WRITES** emitted",
        "- **NO ALPACA INTERACTION** — all data from local cache",
        "- **NO BROKER EXECUTION** — system remains fully decommissioned from auto-trading",
        "- All research candidates require independent human validation before any action",
    ]


# ── Main report builder ──────────────────────────────────────────────────────

def build_daily_radar(sidecars: Dict[str, Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    logger.info("Daily Alpha Radar %s starting", VERSION)

    scanner = sidecars.get("scanner") or {}
    coverage = sidecars.get("coverage")
    changes = sidecars.get("changes")
    forward = sidecars.get("forward")
    ten_x = sidecars.get("ten_x")
    heartbeat = sidecars.get("heartbeat")

    watchlist = scanner.get("watchlist", [])
    coverage_map = _coverage_by_ticker(coverage)
    options_state = _options_coverage_state(watchlist)

    # Enrich all items with priority labels
    enriched = [_enrich_with_priority(item, coverage_map, options_state) for item in watchlist]

    # Bucket by priority
    buckets = _bucket_items(enriched)

    # 10x candidates — already labeled by ten_x_candidate_radar
    ten_x_candidates = (ten_x or {}).get("candidates", [])
    true_10x = [c for c in ten_x_candidates if c.get("label") == TRUE_10X_RESEARCH]
    asymmetric = [c for c in ten_x_candidates if c.get("label") == ASYMMETRIC_RECOVERY_WATCH]
    theme_only = [c for c in ten_x_candidates if c.get("label") == THEME_ONLY]

    # Social/catalyst anomalies: surface items from social_arb category with freshness
    social_items = [
        item for item in enriched
        if item.get("category") == "social_arb_attention"
        and item.get("watchlist_label") not in ("CROWDED", "NO_SOCIAL_DATA")
    ]

    # Priority counts
    priority_counts: Dict[str, int] = {}
    for item in enriched:
        p = item.get("priority_label", WATCHLIST_RESEARCH)
        priority_counts[p] = priority_counts.get(p, 0) + 1

    # Phase 4A.3: Quarantine subtype breakdown
    quarantine_breakdown: Dict[str, int] = {}
    for item in enriched:
        sub = item.get("quarantine_subtype", "")
        if sub:
            quarantine_breakdown[sub] = quarantine_breakdown.get(sub, 0) + 1

    # Phase 4A.3: Enrichment field coverage stats
    n_total = len(enriched)
    field_coverage = {
        "above_ma200_populated": sum(1 for i in enriched if i.get("above_ma200") is not None),
        "above_ma50_populated": sum(1 for i in enriched if i.get("above_ma50") is not None),
        "rs_63d_populated": sum(1 for i in enriched if i.get("rs_63d_vs_spy") is not None),
        "sector_populated": sum(1 for i in enriched if i.get("sector")),
        "liquidity_ok_populated": sum(1 for i in enriched if i.get("liquidity_ok") is not None),
        "earliness_non_unknown": sum(
            1 for i in enriched if i.get("earliness_label") not in (UNKNOWN_EARLINESS, None)
        ),
        "total": n_total,
    }

    result = {
        "version": VERSION,
        "generated_at": now,
        "system_mode": SYSTEM_MODE,
        "research_only": True,
        "total_candidates": n_total,
        "priority_counts": priority_counts,
        "quarantine_breakdown": quarantine_breakdown,
        "field_coverage": field_coverage,
        "options_coverage": options_state,
        "buckets": {k: len(v) for k, v in buckets.items()},
        "ten_x_counts": {
            TRUE_10X_RESEARCH: len(true_10x),
            ASYMMETRIC_RECOVERY_WATCH: len(asymmetric),
            THEME_ONLY: len(theme_only),
        },
        "guardrails": {
            "no_trade_recommendation": True,
            "no_buy_sell": True,
            "no_entry_stop_target": True,
            "no_paper_signal": True,
            "no_alpaca_interaction": True,
            "no_broker_execution": True,
            "options_overlay_enabled": options_state["overlay_enabled"],
        },
    }
    logger.info(
        "Priority counts: %s",
        " ".join(f"{k}={v}" for k, v in sorted(priority_counts.items())),
    )
    return result, buckets, enriched, true_10x, asymmetric, social_items, ten_x_candidates


def generate_report(
    result: Dict[str, Any],
    buckets: Dict[str, List[Dict[str, Any]]],
    enriched: List[Dict[str, Any]],
    true_10x: List[Dict[str, Any]],
    asymmetric: List[Dict[str, Any]],
    social_items: List[Dict[str, Any]],
    sidecars: Dict[str, Optional[Dict[str, Any]]],
) -> str:
    now_str = result["generated_at"][:10]
    options_state = result["options_coverage"]

    lines: List[str] = [
        RESEARCH_ONLY_BANNER,
        "",
        f"# Daily Alpha Radar — {now_str}",
        "",
        f"**Version:** {result['version']} | **Mode:** {result['system_mode']} | **Research-Only**",
        "",
        f"*Candidates: {result['total_candidates']} scanned | "
        f"TOP_RESEARCH: {result['priority_counts'].get(TOP_RESEARCH, 0)} | "
        f"HIGH_PRIORITY: {result['priority_counts'].get(HIGH_PRIORITY_RESEARCH, 0)} | "
        f"DATA_QUARANTINE: {result['priority_counts'].get(DATA_QUARANTINE, 0)}*",
        "",
        "---",
    ]

    # 1. Market Context
    lines.extend(_fmt_market_context(sidecars.get("heartbeat")))

    # 2. Data Coverage
    lines.extend(_fmt_data_coverage(sidecars.get("coverage"), options_state))

    # 2b. Scanner Field Coverage (Phase 4A.3)
    lines.extend(_fmt_scanner_field_coverage(result.get("field_coverage", {}), result.get("quarantine_breakdown", {})))

    # 3. What Changed Today
    lines.extend(_fmt_changes(sidecars.get("changes")))

    # 4. Top Research Candidates
    top = buckets["top_research"] + buckets["high_priority"]
    top.sort(key=lambda x: x.get("quality_adjusted_consensus_score", 0), reverse=True)
    lines.extend(_fmt_section("Top Research Candidates — Quality Adjusted", top, max_items=15))

    # 5. Early Accumulation — Clean Only
    lines.extend(_fmt_section(
        "Early Accumulation — Clean Only",
        buckets["early_accumulation"],
        max_items=10,
    ))

    # 6. Reclaim / Reset Watch
    reclaim_reset = buckets["reclaim_watch"] + buckets["reset_watch"]
    reclaim_reset.sort(key=lambda x: x.get("quality_adjusted_consensus_score", 0), reverse=True)
    lines.extend(_fmt_section("Reclaim / Reset Watch", reclaim_reset, max_items=12))

    # 7. Conflicted Signals
    lines.extend(_fmt_section(
        "Conflicted Signals",
        buckets["conflicted"],
        max_items=10,
    ))

    # 8. Data Quarantine
    lines.extend(_fmt_section(
        "Data Quarantine",
        buckets["quarantine"],
        max_items=15,
        details=False,
    ))

    # 9. Social / Catalyst Anomalies
    lines.extend(_fmt_section(
        "Social / Catalyst Anomalies",
        social_items,
        max_items=10,
    ))

    # 10. True 10x Research Candidates
    lines.extend(["\n## True 10x Research Candidates", ""])
    lines.append(
        "> ⚠ TRUE_10X_RESEARCH requires: confirmed speculative/structural theme + "
        "small-cap base + price recovery evidence. High risk. Manual deep research required."
    )
    lines.append("")
    if not true_10x:
        lines.append("*(none — stricter criteria require theme + small-cap + confirmed price recovery)*")
    else:
        for c in true_10x[:10]:
            themes = ", ".join(c.get("themes", [])[:3]) or "—"
            dd = c.get("dd_from_high_pct")
            rs63 = c.get("rs_63d_vs_spy")
            lines.append(
                f"- **{c['ticker']}** | themes=[{themes}] | dd={dd}% | rs63={rs63} "
                f"| score={c.get('research_score', 0):.0f}"
            )
            lines.append(f"  - *{c.get('research_note', '')}*")

    # 11. Asymmetric Recovery Watch
    lines.extend(["\n## Asymmetric Recovery Watch", ""])
    lines.append(
        "> ASYMMETRIC_RECOVERY_WATCH: price/volume recovery signals only. "
        "Theme/fundamental thesis unconfirmed. Not the same as TRUE_10X_RESEARCH."
    )
    lines.append("")
    if not asymmetric:
        lines.append("*(none)*")
    else:
        for c in asymmetric[:10]:
            dd = c.get("dd_from_high_pct")
            rs63 = c.get("rs_63d_vs_spy")
            vt = c.get("vol_trend_ratio")
            lines.append(
                f"- **{c['ticker']}** | dd={dd}% | rs63={rs63} | vol_trend={vt} "
                f"| score={c.get('research_score', 0):.0f} | [price/volume only — no confirmed thesis]"
            )

    # 12. Extended / Crowded / Avoid
    lines.extend(_fmt_section(
        "Extended / Crowded / Avoid",
        buckets["extended_crowded"],
        max_items=10,
        details=False,
    ))

    # 13. Forward Tracker Status
    lines.extend(_fmt_forward_tracker(sidecars.get("forward")))

    # 14. Safety Confirmations
    lines.extend(_fmt_safety_confirmations())

    lines.append("")
    lines.append("---")
    lines.append(f"*Generated: {result['generated_at']} | {result['version']}*")
    lines.append("")

    return "\n".join(lines)


def run_daily_radar() -> Dict[str, Any]:
    sidecars = _load_sidecars()
    missing = [k for k, v in sidecars.items() if v is None and k not in ("heartbeat", "sector_leadership")]
    if missing:
        logger.warning("Missing sidecars: %s — run the corresponding research commands first", missing)

    result, buckets, enriched, true_10x, asymmetric, social_items, ten_x_all = build_daily_radar(sidecars)
    report_md = generate_report(result, buckets, enriched, true_10x, asymmetric, social_items, sidecars)

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    REPORT_PATH.write_text(report_md, encoding="utf-8")
    logger.info("wrote %s", REPORT_PATH)

    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    logger.info("wrote %s", OUT_JSON)

    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Daily Alpha Radar Report (research-only)")
    parser.parse_args()

    print(RESEARCH_ONLY_BANNER)
    result = run_daily_radar()

    print(f"\nDaily Alpha Radar complete.")
    print(f"Candidates: {result['total_candidates']}")
    for pri, cnt in sorted(result.get("priority_counts", {}).items()):
        print(f"  {pri:<28} {cnt}")
    print(f"Options overlay: {result['options_coverage']['state']}")
    print(f"Report: {REPORT_PATH}")
    print("\nRESEARCH ONLY — NO TRADE RECOMMENDATIONS.")


if __name__ == "__main__":
    main()
