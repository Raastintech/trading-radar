#!/usr/bin/env python3
"""
research/nightly_operator_summary.py — Nightly Operator Summary generator.

Reads existing research sidecars (no provider calls) and produces a short
human-readable summary after each nightly cycle.  25-50 lines.

Outputs:
  docs/research/NIGHTLY_OPERATOR_SUMMARY.md
  cache/research/nightly_operator_summary_latest.json
  logs/nightly_operator_summary_latest.md

Research-only. No trade recommendations. No provider calls.
No strategy abbreviations (V:, S:, VOYAGER, SNIPER, SHORT_A, etc.).

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/nightly_operator_summary.py
  ./scripts/run_research_cycle.sh nightly-operator-summary
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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

VERSION = "NIGHTLY_OPERATOR_SUMMARY_V1"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
LOG_DIR = cfg.LOG_DIR
DOCS_DIR = ROOT / "docs" / "research"
OUT_JSON = RESEARCH_DIR / "nightly_operator_summary_latest.json"
OUT_MD_LOG = LOG_DIR / "nightly_operator_summary_latest.md"
OUT_MD_DOCS = DOCS_DIR / "NIGHTLY_OPERATOR_SUMMARY.md"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("nightly_operator_summary")

# Priority labels — mirror research_scoring.py values
HIGH_PRIORITY_RESEARCH = "HIGH_PRIORITY_RESEARCH"
TOP_RESEARCH = "TOP_RESEARCH"
RESET_WATCH = "RESET_WATCH"
RECLAIM_WATCH = "RECLAIM_WATCH"
EXTENDED_CROWDED = "EXTENDED_CROWDED"
DATA_QUARANTINE = "DATA_QUARANTINE"


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_sidecars() -> Dict[str, Optional[Dict[str, Any]]]:
    return {
        "forecast": _load_json(RESEARCH_DIR / "regime_forecast_latest.json"),
        "alpha_radar": _load_json(RESEARCH_DIR / "daily_alpha_radar_latest.json"),
        "forward": _load_json(RESEARCH_DIR / "research_forward_latest.json"),
        "scanner_truth": _load_json(RESEARCH_DIR / "scanner_truth_summary_latest.json"),
        "scanner": _load_json(RESEARCH_DIR / "research_scanner_latest.json"),
        "provider_audit": _load_json(RESEARCH_DIR / "provider_freshness_audit_latest.json"),
        "social": _load_json(RESEARCH_DIR / "social_attention_forward_latest.json"),
        "targeted_backfill": _load_json(
            RESEARCH_DIR / "targeted_price_backfill_latest.json"
        ),
    }


def _age_hours(ts_str: Optional[str]) -> Optional[float]:
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 3600
    except Exception:
        return None


def _age_label(hours: Optional[float]) -> str:
    if hours is None:
        return "unknown age"
    if hours < 1:
        return f"{int(hours * 60)}m ago"
    if hours < 24:
        return f"{hours:.1f}h ago"
    return f"{hours / 24:.1f}d ago"


# ── Research posture translator ──────────────────────────────────────────────

def _research_posture(forecast: Optional[Dict[str, Any]]) -> str:
    """Translate regime + alpha_discovery stance into neutral research posture language."""
    if not forecast:
        return "Research posture: unknown (forecast unavailable); human review required"

    head = forecast.get("headline") or {}
    regime = head.get("current_regime", "Unknown")
    bias5 = head.get("bias_5d", "unknown")
    conf = head.get("confidence", "unknown")

    sf = forecast.get("strategy_favorability") or {}
    ad = sf.get("ALPHA_DISCOVERY") or {}
    ad_stance = ad.get("stance", "")
    ad_reason = ad.get("reason", "")

    # Translate stance into human language
    if ad_stance == "favored":
        posture = "constructive for long-side research; pullback and reset candidates highlighted"
    elif ad_stance == "selective":
        if "avoid chasing" in ad_reason or "extended" in ad_reason:
            posture = "selective — avoid extended names; favor reset/reclaim watchlists"
        elif "stalk" in ad_reason or "stress" in ad_reason:
            posture = "stalking mode — do not promote ideas during stress; watchlist only"
        else:
            posture = "selective — research ongoing; require confirmation before review"
    elif ad_stance == "avoid":
        posture = "defensive — research board signal quality poor; defer new reviews"
    elif ad_stance == "allowed":
        posture = "neutral — long-side research allowed; no strong tailwind or headwind"
    else:
        posture = "neutral — awaiting clearer regime signal"

    return f"Research posture: {posture}; human review required before any action"


# ── Section builders ─────────────────────────────────────────────────────────

def _build_overall_status(sidecars: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    forecast = sidecars.get("forecast")
    alpha_radar = sidecars.get("alpha_radar")

    components_present = []
    components_missing = []
    warnings = []

    for name, key in [
        ("Regime Forecast", "forecast"),
        ("Daily Alpha Radar", "alpha_radar"),
        ("Forward Tracker", "forward"),
        ("Scanner Truth", "scanner_truth"),
    ]:
        if sidecars.get(key):
            components_present.append(name)
        else:
            components_missing.append(name)

    # Age checks
    forecast_age = _age_hours((forecast or {}).get("built_at") or (forecast or {}).get("generated_at"))
    if forecast_age and forecast_age > 25:
        warnings.append(f"Regime forecast is {_age_label(forecast_age)} — may be stale")

    radar_age = _age_hours((alpha_radar or {}).get("generated_at"))
    if radar_age and radar_age > 25:
        warnings.append(f"Daily alpha radar is {_age_label(radar_age)} — may be stale")

    if components_missing:
        overall = "WARN"
        reason = f"Missing artifacts: {', '.join(components_missing)}"
    else:
        overall = "PASS"
        reason = "All primary artifacts present"

    return {
        "overall": overall,
        "reason": reason,
        "nightly_completed": True,
        "research_only_safety": "ACTIVE",
        "components_present": components_present,
        "components_missing": components_missing,
        "provider_warnings": warnings,
        "generated_at": now.isoformat(),
    }


def _build_market_context(forecast: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not forecast:
        return {
            "regime": "UNKNOWN",
            "confidence": "unknown",
            "bias_5d": "unknown",
            "bias_10d": "unknown",
            "bias_30d": "unknown",
            "leading_sectors": [],
            "weak_sectors": [],
            "research_posture": "Research posture: unknown (forecast unavailable); human review required",
            "age_label": "unknown",
        }

    head = forecast.get("headline") or {}
    sec_rot = forecast.get("sector_rotation") or {}

    bias30 = "unknown"
    spy_bars = forecast.get("market_trend", {}).get("SPY", {})
    ret20 = spy_bars.get("return_20d_pct")
    if ret20 is not None:
        if ret20 >= 2:
            bias30 = "bullish"
        elif ret20 <= -2:
            bias30 = "bearish"
        else:
            bias30 = "mixed"

    leaders = [r["sector"] for r in (sec_rot.get("rows") or []) if r.get("state") == "Leading"][:4]
    weak = [r["sector"] for r in (sec_rot.get("rows") or []) if r.get("state") in ("Weakening", "Lagging")][:4]

    age_h = _age_hours(forecast.get("built_at") or forecast.get("generated_at"))

    return {
        "regime": head.get("current_regime", "UNKNOWN"),
        "confidence": head.get("confidence", "unknown"),
        "bias_5d": head.get("bias_5d", "unknown"),
        "bias_10d": head.get("bias_10d", "unknown"),
        "bias_30d": bias30,
        "leading_sectors": leaders,
        "weak_sectors": weak,
        "research_posture": _research_posture(forecast),
        "age_label": _age_label(age_h),
    }


def _fmt_tickers(tickers: List[str], cap: int = 4) -> str:
    """Format a ticker list with a display cap and '+N more' suffix."""
    if not tickers:
        return ""
    shown = tickers[:cap]
    remainder = len(tickers) - len(shown)
    s = ": " + ", ".join(shown)
    if remainder > 0:
        s += f", ... +{remainder} more"
    return s


def _build_alpha_snapshot(alpha_radar: Optional[Dict[str, Any]], scanner: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not alpha_radar:
        return {"available": False}

    counts = alpha_radar.get("priority_counts") or {}
    q_breakdown = alpha_radar.get("quarantine_breakdown") or {}
    total = alpha_radar.get("total_candidates", 0)

    # Authoritative per-label ticker lists from the alpha radar sidecar.
    # When the sidecar predates this field, lists are empty (graceful degradation).
    pt = alpha_radar.get("priority_tickers") or {}

    hp_count = counts.get(HIGH_PRIORITY_RESEARCH, 0) + counts.get(TOP_RESEARCH, 0)
    hp_tickers = pt.get(HIGH_PRIORITY_RESEARCH, []) + pt.get(TOP_RESEARCH, [])

    reset_count = counts.get(RESET_WATCH, 0)
    reset_tickers = pt.get(RESET_WATCH, [])

    reclaim_count = counts.get(RECLAIM_WATCH, 0)
    reclaim_tickers = pt.get(RECLAIM_WATCH, [])

    extended_count = counts.get(EXTENDED_CROWDED, 0)
    extended_tickers = pt.get(EXTENDED_CROWDED, [])

    quarantine_count = counts.get(DATA_QUARANTINE, 0)
    quarantine_reasons = [f"{k}: {v}" for k, v in list(q_breakdown.items())[:3] if v > 0]

    return {
        "available": True,
        "total_candidates": total,
        "high_priority_count": hp_count,
        "high_priority_tickers": hp_tickers,
        "reset_watch_count": reset_count,
        "reset_watch_tickers": reset_tickers,
        "reclaim_watch_count": reclaim_count,
        "reclaim_watch_tickers": reclaim_tickers,
        "extended_crowded_count": extended_count,
        "extended_crowded_tickers": extended_tickers,
        "data_quarantine_count": quarantine_count,
        "top_quarantine_reasons": quarantine_reasons,
        "options_state": (alpha_radar.get("options_coverage") or {}).get("state", "UNKNOWN"),
    }


def _build_best_names(
    alpha_snap: Optional[Dict[str, Any]],
    scanner: Optional[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Top research names split into primary (HIGH_PRIORITY) and secondary (reset/reclaim).

    Returns {"primary": [...], "secondary": [...]} so the markdown can render them
    in separate subsections — never mixing priority buckets in one sentence.
    """
    scanner_by_ticker: Dict[str, Any] = {
        item.get("ticker"): item
        for item in ((scanner or {}).get("watchlist") or [])
        if item.get("ticker")
    }

    def _enrich(ticker: str) -> Dict[str, Any]:
        item = scanner_by_ticker.get(ticker, {})
        return {
            "ticker": ticker,
            "label": item.get("watchlist_label") or item.get("priority_label") or "WATCHLIST",
            "reason": item.get("why_appeared") or "—",
            "sector": item.get("sector") or "—",
            "confidence": item.get("data_confidence") or item.get("trust_level") or "—",
        }

    if not alpha_snap or not alpha_snap.get("available"):
        return {"primary": [], "secondary": []}

    hp_tickers = alpha_snap.get("high_priority_tickers", [])[:10]
    secondary_tickers = (
        alpha_snap.get("reset_watch_tickers", []) +
        alpha_snap.get("reclaim_watch_tickers", [])
    )[:5]

    return {
        "primary": [_enrich(t) for t in hp_tickers],
        "secondary": [_enrich(t) for t in secondary_tickers],
    }


def _build_forward_evidence(forward: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not forward:
        return {"available": False}

    overall = forward.get("overall") or {}
    bm = forward.get("benchmark_readiness") or {}

    total = forward.get("total_history_entries", 0)
    new_today = forward.get("new_entries_today", 0)
    matured = overall.get("matured_entries", 0)
    matured_5d = overall.get("matured_5d_entries", 0)
    sample_status = overall.get("sample_status", "TOO_EARLY")
    verdict = overall.get("verdict", "NEED_MORE_DATA")

    spy_ready = bm.get("entries_with_spy_10d", 0)
    qqq_ready = bm.get("entries_with_qqq_10d", 0)
    sector_ready = bm.get("entries_with_sector_10d", 0)
    spy_available = bm.get("spy_available", False)
    qqq_available = bm.get("qqq_available", False)

    if spy_ready >= 10 and qqq_ready >= 10:
        benchmark_readiness = "READY"
        benchmark_detail = f"SPY {spy_ready}, QQQ {qqq_ready} entries with 10d return"
    elif spy_available and qqq_available:
        if spy_ready == 0 and qqq_ready == 0:
            # Series are loaded but no entries have matured to 10d yet — not a data error
            benchmark_readiness = "LOADED"
            benchmark_detail = "benchmark series loaded; entry outcomes pending maturity"
        else:
            benchmark_readiness = "PARTIAL"
            benchmark_detail = f"SPY: {spy_ready} entries with 10d return, QQQ: {qqq_ready}"
    else:
        benchmark_readiness = "NOT_READY"
        benchmark_detail = "benchmark series not yet available"

    alpha_proven = verdict not in ("NEED_MORE_DATA", "TOO_EARLY")

    return {
        "available": True,
        "total_entries": total,
        "new_today": new_today,
        "matured_count": matured,
        "matured_5d_count": matured_5d,
        "benchmark_readiness": benchmark_readiness,
        "benchmark_detail": benchmark_detail,
        "spy_entries_with_10d": spy_ready,
        "qqq_entries_with_10d": qqq_ready,
        "sector_entries_with_10d": sector_ready,
        "spy_available": spy_available,
        "qqq_available": qqq_available,
        "sample_status": sample_status,
        "verdict": verdict,
        "alpha_proven": alpha_proven,
    }


def _build_warnings(
    sidecars: Dict[str, Any],
    market_ctx: Dict[str, Any],
    forward: Dict[str, Any],
    alpha_snap: Dict[str, Any],
) -> List[str]:
    warnings: List[str] = []

    # Scanner recall
    st = sidecars.get("scanner_truth") or {}
    if st:
        recall_pct = st.get("winner_recall_pct")
        main_fail = st.get("main_failure", "")
        baseline = st.get("best_simple_baseline_recall_pct")
        if recall_pct is not None and recall_pct < 5:
            bl_note = f" (simple-RS baseline: {baseline}%)" if baseline else ""
            warnings.append(
                f"Scanner recall low at {recall_pct}% — main miss: {main_fail}{bl_note}"
            )

    # Forward evidence maturity
    if forward.get("available") and forward.get("verdict") == "NEED_MORE_DATA":
        mat = forward.get("matured_count", 0)
        mat5 = forward.get("matured_5d_count", 0)
        total = forward.get("total_entries", 0)
        warnings.append(
            f"Forward evidence immature: {mat5} matured 5d, {mat} matured 10d / {total} total — do not change scoring"
        )

    # Benchmark not ready — distinguish loaded-but-immature from truly missing
    bm_status = forward.get("benchmark_readiness", "")
    bm_detail = forward.get("benchmark_detail", "")
    if bm_status == "LOADED":
        warnings.append(
            "Benchmark series loaded; no entries have matured to 10d yet — no Phase 4B until benchmarked"
        )
    elif bm_status in ("PARTIAL", "NOT_READY"):
        detail_note = f" ({bm_detail})" if bm_detail else ""
        warnings.append(
            f"Benchmark coverage: {bm_status}{detail_note} — no Phase 4B until benchmarked"
        )

    # Provider auth / freshness
    pa = sidecars.get("provider_audit") or {}
    provider_warns = (pa.get("warnings") or [])[:2]
    for w in provider_warns:
        if isinstance(w, str):
            warnings.append(f"Provider: {w}")
        elif isinstance(w, dict):
            warnings.append(f"Provider: {w.get('message', str(w))}")

    # Options coverage
    opt_state = alpha_snap.get("options_state", "")
    if opt_state == "DISABLED":
        warnings.append("Options overlay: DISABLED — insufficient coverage")

    # High quarantine rate
    qt_count = alpha_snap.get("data_quarantine_count", 0)
    total_cands = alpha_snap.get("total_candidates", 0)
    if total_cands > 0 and qt_count / total_cands > 0.4:
        pct = round(qt_count / total_cands * 100)
        warnings.append(f"High data-quarantine rate: {qt_count}/{total_cands} ({pct}%) — deep-cache lag")

    # Targeted backfill status
    bf = sidecars.get("targeted_backfill") or {}
    if bf:
        bf_selected = bf.get("selected_for_backfill", 0)
        bf_used = bf.get("provider_calls_used", 0)
        bf_ok = bf.get("successes", 0)
        bf_fail = bf.get("failures", 0)
        bf_insuf = bf.get("remaining_insufficient")
        if bf_used > 0:
            msg = (f"Backfill: {bf_ok} succeeded, {bf_fail} failed"
                   + (f", {bf_insuf} still insufficient" if bf_insuf else ""))
        else:
            msg = (f"Targeted backfill plan: {bf_selected} tickers need "
                   f">={bf.get('params', {}).get('min_bars', 300)} bars "
                   + (f"— run targeted-backfill --execute to fill" if bf_selected > 0
                      else "— cache depth adequate"))
        warnings.append(msg)

    return warnings[:8]  # cap at 8


def _build_next_actions(
    overall: Dict[str, Any],
    market_ctx: Dict[str, Any],
    forward: Dict[str, Any],
    warnings: List[str],
    alpha_snap: Dict[str, Any],
    sidecars: Optional[Dict[str, Any]] = None,
) -> List[str]:
    actions: List[str] = []

    # Forward evidence actions
    if forward.get("verdict") == "NEED_MORE_DATA":
        actions.append("Do not change scoring until forward outcomes mature.")

    bm_status = forward.get("benchmark_readiness", "")
    if "PARTIAL" in bm_status or bm_status == "NOT_READY":
        actions.append("No Phase 4B until enough benchmarked forward evidence exists.")

    # High-priority research — list at most 4 tickers; use "+N more" if count exceeds cap
    hp = alpha_snap.get("high_priority_count", 0)
    if hp > 0:
        tickers = alpha_snap.get("high_priority_tickers", [])
        cap = 4
        shown = tickers[:cap]
        if shown:
            ts = ", ".join(shown)
            if hp > len(shown):
                ts += f", ... +{hp - len(shown)} more"
        else:
            ts = "see Daily Alpha Radar report"
        actions.append(f"Review {hp} high-priority research name(s) manually: {ts}")

    # Targeted backfill action if plan shows unmet tickers
    bf = (sidecars or {}).get("targeted_backfill") or {}
    if bf and bf.get("selected_for_backfill", 0) > 0 and bf.get("provider_calls_used", 0) == 0:
        n = bf["selected_for_backfill"]
        min_b = bf.get("params", {}).get("min_bars", 300)
        actions.append(
            f"Run targeted-backfill --execute --limit {n} --max-provider-calls 15 "
            f"to fill {n} research names below {min_b}-bar floor."
        )

    # Default
    actions.append("Run nightly again tomorrow.")

    return actions[:5]


# ── Report renderer ──────────────────────────────────────────────────────────

def _render_md(
    now: datetime,
    overall: Dict[str, Any],
    market_ctx: Dict[str, Any],
    alpha_snap: Dict[str, Any],
    best_names: List[Dict[str, Any]],
    forward: Dict[str, Any],
    warnings: List[str],
    actions: List[str],
) -> str:
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M UTC")
    overall_status = overall["overall"]
    overall_flag = "✓" if overall_status == "PASS" else "⚠"

    lines = [
        "=" * 70,
        "  RESEARCH_ONLY_MODE — NO AUTO TRADING — HUMAN REVIEW ONLY",
        "  Broker execution, paper-trade routing, and Alpaca are disabled.",
        "=" * 70,
        "",
        f"# Nightly Operator Summary — {date_str}",
        "",
        f"**Generated:** {time_str}  |  **Mode:** RESEARCH_ONLY  |  **Version:** {VERSION}",
        "",
        "---",
        "",
        "## 1. Overall Status",
        "",
        f"**{overall_flag} {overall_status}** — {overall['reason']}",
        f"- Research-only safety: {overall['research_only_safety']}",
    ]
    if overall["components_missing"]:
        lines.append(f"- Missing artifacts: {', '.join(overall['components_missing'])}")
    if overall["provider_warnings"]:
        for w in overall["provider_warnings"]:
            lines.append(f"- ⚠ {w}")
    lines.append("")

    # Section 2 — Market Context
    regime = market_ctx["regime"]
    conf = str(market_ctx["confidence"]).upper()
    bias5 = market_ctx["bias_5d"]
    bias10 = market_ctx["bias_10d"]
    bias30 = market_ctx["bias_30d"]
    leaders = market_ctx["leading_sectors"]
    weak = market_ctx["weak_sectors"]
    posture = market_ctx["research_posture"]
    fage = market_ctx["age_label"]

    lines += [
        "## 2. Market Context",
        "",
        f"**Regime:** {regime} (conf: {conf}, {fage})",
        f"- 5d: {bias5}  |  10d: {bias10}  |  30d: {bias30}",
    ]
    if leaders:
        lines.append(f"- Leading sectors: {', '.join(leaders)}")
    if weak:
        lines.append(f"- Weak sectors: {', '.join(weak)}")
    lines += [
        f"- {posture}",
        "",
    ]

    # Section 3 — Alpha Radar Snapshot
    lines.append("## 3. Alpha Radar Snapshot")
    lines.append("")
    if not alpha_snap.get("available"):
        lines.append("*Alpha radar artifact unavailable.*")
    else:
        total = alpha_snap["total_candidates"]
        hp = alpha_snap["high_priority_count"]
        hp_tickers = alpha_snap.get("high_priority_tickers", [])
        rw = alpha_snap["reset_watch_count"]
        rw_tickers = alpha_snap.get("reset_watch_tickers", [])
        rc = alpha_snap["reclaim_watch_count"]
        rc_tickers = alpha_snap.get("reclaim_watch_tickers", [])
        ex = alpha_snap["extended_crowded_count"]
        ex_tickers = alpha_snap.get("extended_crowded_tickers", [])
        qt = alpha_snap["data_quarantine_count"]
        qt_reasons = alpha_snap.get("top_quarantine_reasons", [])
        opt = alpha_snap.get("options_state", "UNKNOWN")

        lines.append(f"**Total candidates:** {total}")
        lines.append(f"- HIGH_PRIORITY_RESEARCH: {hp}{_fmt_tickers(hp_tickers)}")
        lines.append(f"- RESET_WATCH: {rw}{_fmt_tickers(rw_tickers)}")
        lines.append(f"- RECLAIM_WATCH: {rc}{_fmt_tickers(rc_tickers)}")
        lines.append(f"- EXTENDED_CROWDED: {ex}{_fmt_tickers(ex_tickers)}")
        qt_str = f" ({'; '.join(qt_reasons)})" if qt_reasons else ""
        lines.append(f"- DATA_QUARANTINE: {qt}{qt_str}")
        lines.append(f"- Options overlay: {opt}")
    lines.append("")

    # Section 4 — Best Research Names
    lines.append("## 4. Best Research Names to Review")
    lines.append("")
    lines.append("*Research candidates only — no trade ideas, no buy/sell signals.*")
    lines.append("")

    primary = best_names.get("primary", []) if isinstance(best_names, dict) else []
    secondary = best_names.get("secondary", []) if isinstance(best_names, dict) else []

    def _render_name_list(items: List[Dict[str, Any]]) -> None:
        for item in items:
            ticker = item["ticker"]
            label = item["label"]
            reason = item["reason"]
            sector = item["sector"]
            conf_lvl = item["confidence"]
            lines.append(f"- **{ticker}** | {label} | sector={sector} | confidence={conf_lvl}")
            lines.append(f"  - Why appeared: {reason}")

    if primary:
        lines.append("**High-priority review:**")
        lines.append("")
        _render_name_list(primary)
    elif not secondary:
        lines.append("*No high-priority research names today.*")

    if secondary:
        if primary:
            lines.append("")
        lines.append("**Secondary reset/reclaim watch:**")
        lines.append("")
        _render_name_list(secondary)

    lines.append("")

    # Section 5 — Forward Evidence
    lines.append("## 5. Forward Evidence")
    lines.append("")
    if not forward.get("available"):
        lines.append("*Forward tracker artifact unavailable.*")
    else:
        total_e = forward["total_entries"]
        new_today = forward["new_today"]
        matured = forward["matured_count"]
        matured_5d = forward.get("matured_5d_count", 0)
        bm_status = forward["benchmark_readiness"]
        bm_detail = forward.get("benchmark_detail", "")
        sample = forward["sample_status"]
        verdict = forward["verdict"]
        alpha_proven = forward["alpha_proven"]

        bm_line = f"- Benchmark readiness: {bm_status}"
        if bm_detail:
            bm_line += f" — {bm_detail}"

        matured_str = f"**Matured 5d:** {matured_5d}  |  **Matured 10d:** {matured}"
        lines += [
            f"**Total entries:** {total_e}  |  **New today:** {new_today}  |  {matured_str}",
            f"- Sample status: {sample}",
            bm_line,
            f"- Verdict: **{verdict}**",
            f"- Alpha proven: {'YES' if alpha_proven else 'NO — insufficient evidence'}",
        ]
    lines.append("")

    # Section 6 — Biggest Warnings
    lines.append("## 6. Biggest Warnings")
    lines.append("")
    if not warnings:
        lines.append("*No material warnings today.*")
    else:
        for w in warnings:
            lines.append(f"- ⚠ {w}")
    lines.append("")

    # Section 7 — Next Operator Actions
    lines.append("## 7. Next Operator Actions")
    lines.append("")
    for i, action in enumerate(actions, 1):
        lines.append(f"{i}. {action}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Research-only engine. Not a signal. Not a recommendation. No live capital.*")

    return "\n".join(lines) + "\n"


# ── Main ─────────────────────────────────────────────────────────────────────

def generate() -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    logger.info("Generating nightly operator summary")

    sidecars = _load_sidecars()

    overall = _build_overall_status(sidecars, now)
    market_ctx = _build_market_context(sidecars.get("forecast"))
    alpha_snap = _build_alpha_snapshot(sidecars.get("alpha_radar"), sidecars.get("scanner"))
    best_names = _build_best_names(alpha_snap, sidecars.get("scanner"))
    forward = _build_forward_evidence(sidecars.get("forward"))
    warnings = _build_warnings(sidecars, market_ctx, forward, alpha_snap)
    actions = _build_next_actions(overall, market_ctx, forward, warnings, alpha_snap,
                                   sidecars=sidecars)

    # Assemble JSON payload
    payload: Dict[str, Any] = {
        "version": VERSION,
        "generated_at": now.isoformat(),
        "system_mode": "RESEARCH_ONLY",
        "research_only": True,
        "guardrails": {
            "no_trade_recommendation": True,
            "no_buy_sell": True,
            "no_entry_stop_target": True,
            "no_paper_signal": True,
            "no_strategy_abbreviations": True,
            "no_alpaca_interaction": True,
        },
        "overall_status": overall,
        "market_context": market_ctx,
        "alpha_snapshot": alpha_snap,
        "best_research_names": best_names,
        "forward_evidence": forward,
        "warnings": warnings,
        "next_actions": actions,
    }

    # Render markdown
    md_text = _render_md(now, overall, market_ctx, alpha_snap, best_names, forward, warnings, actions)

    # Write outputs
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    OUT_MD_LOG.write_text(md_text, encoding="utf-8")
    OUT_MD_DOCS.write_text(md_text, encoding="utf-8")

    logger.info("Nightly operator summary written to %s", OUT_MD_DOCS)
    logger.info("JSON sidecar: %s", OUT_JSON)

    return payload


if __name__ == "__main__":
    result = generate()
    print(result["overall_status"]["overall"], "—", result["overall_status"]["reason"])
    print()
    # Print the markdown to stdout as well
    now = datetime.now(timezone.utc)
    sidecars = _load_sidecars()
    mc = _build_market_context(sidecars.get("forecast"))
    asnap = _build_alpha_snapshot(sidecars.get("alpha_radar"), sidecars.get("scanner"))
    bn = _build_best_names(asnap, sidecars.get("scanner"))
    fwd = _build_forward_evidence(sidecars.get("forward"))
    warns = _build_warnings(sidecars, mc, fwd, asnap)
    acts = _build_next_actions(result["overall_status"], mc, fwd, warns, asnap)
    print(_render_md(now, result["overall_status"], mc, asnap, bn, fwd, warns, acts))
