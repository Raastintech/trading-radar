"""
research/rs_theme_lens_triage.py — Phase 1G.9.

RS/Theme → Lens/Gatekeeper triage (RESEARCH-ONLY / CACHE-ONLY).

Phase 1G.8 found that 333/356 proposed-dynamic early leaders are killed by the
current Voyager/Sniper structural score-gates, but most of those rejections are
CACHE-DEPTH artifacts (shallow ~111-bar parquets fail the 260-bar gate), not
real structure failures. The 1G.8 score-gate audit's own recommendation was to
"route forward-validated RS/theme early leaders to the Stock Lens/Gatekeeper as
a research-only second surface that BYPASSES the voyager/sniper score gates,
rather than loosening those gates."

This module is that second surface. It assembles a small candidate set from the
RS recall lane + LEADING-theme leaders + proposed-dynamic early leaders, enriches
each with the existing Stock Lens / Executive Gatekeeper / options artifacts, and
assigns a research triage label describing how the operator should route it:

  NEEDS_LENS · NEEDS_GATEKEEPER · LENS_READY · TOO_EXTENDED ·
  LOW_QUALITY_NOISE · RESEARCH_WATCH · BLOCKED · NOT_ENOUGH_DATA

It also decomposes the Voyager/Sniper gate rejections into root causes (cache
artifact vs gate-design mismatch vs real quality reject) so we can tell whether
routing these names would surface real candidates or just create noise.

Outputs:
  cache/research/rs_theme_lens_triage_latest.json
  logs/rs_theme_lens_triage_latest.txt
  docs/research/RS_THEME_LENS_TRIAGE.md
  data/research/rs_theme_lens_triage_history.jsonl   (append-only forward ledger)

HARD INVARIANTS (Phase 1G.9):
  - Never modifies core/universe.py, strategy gates, execution, or governance.
  - No provider calls, no DB writes, no paper signals, no trade proposals.
  - No trade language (no BUY/SELL/SHORT). Triage labels are routing verbs only.
  - Reuses the live-mirrored gate filters read-only; tunes nothing to past winners.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from research.scanner_truth import dataio
from research.scanner_truth.filters import (alpha_market_cap_eligible,
                                            sniper_breakout, voyager_structural)
from research.universe_dynamic_selection import (EARLY_STAGES, HISTORY_PATH,
                                                 PROPOSED_VERSION, _aligned,
                                                 _theme_states, classify_stage,
                                                 early_leader_score, features)

RC = dataio.RESEARCH_CACHE
DOCS_DIR = dataio.REPO / "docs" / "research"
TRIAGE_HISTORY = dataio.HISTORY_DIR / "rs_theme_lens_triage_history.jsonl"

# Candidate-assembly knobs (research defaults; documented, not tuned to winners).
DEFAULT_CAP = 30
RS_LANE_TOP = 25                  # top RS recall-lane leaders to consider
THEME_LEADERS_PER_THEME = 8       # leaders per LEADING/EMERGING watched theme
PROPOSED_EARLY_TOP = 30           # proposed-dynamic early leaders to consider

# Freshness thresholds (hours). Lens is rebuilt nightly/weekly so a week is
# generous; the gatekeeper-refresh cadence is roughly daily, so 72h is lenient.
LENS_STALE_HOURS = 168.0          # 7 days
GATEKEEPER_STALE_HOURS = 72.0     # 3 days

# Stage groupings.
LATE_STAGES = {"LATE_EXTENDED", "PARABOLIC"}
NOISE_STAGES = {"LOW_QUALITY_NOISE", "BROKEN"}
TRIAGE_EARLY_STAGES = EARLY_STAGES | {"BREAKOUT_CONFIRMED", "PULLBACK_RECLAIM"}

# Lens labels that are constructive enough to keep routing forward.
_CONSTRUCTIVE_LENS_PREFIX = "bullish"
LATE_EXTENSION_TOO_EXTENDED = 70.0   # late_extension_score >= this ⇒ chasing

# ── Gate-reason classification (Task 3) ───────────────────────────────────────
# Raw reason codes come from research/scanner_truth/filters.py (live-mirrored).
CACHE_DEPTH_REASONS = {"insufficient_history_260", "insufficient_history_75"}
# Sniper structure that an EARLY leader is not supposed to satisfy yet (it has
# not broken out / contracted) — failing these is a gate-design mismatch for an
# early-stage name, not a real quality problem.
GATE_DESIGN_REASONS = {"no_breakout", "no_atr_contraction", "volume_insufficient",
                       "ma50_not_rising", "dvol_fading"}
# Genuine structural quality rejections.
REAL_QUALITY_REASONS = {"too_extended", "below_ma200_floor", "price_below_min",
                        "price_above_max"}
# Map raw reason → the Task-3 reporting bucket the spec asked for.
REASON_BUCKET = {
    "insufficient_history_260": "insufficient_history_260",
    "insufficient_history_75": "insufficient_history_260",
    "below_ma200_floor": "ma200_missing",
    "volume_insufficient": "volume_insufficient",
    "no_atr_contraction": "no_atr_contraction",
    "no_breakout": "no_breakout",
    "too_extended": "too_extended",
    "dvol_fading": "unknown",
    "ma50_not_rising": "unknown",
    "price_below_min": "unknown",
    "price_above_max": "unknown",
}


# ── cache loaders (all None-safe, read-only) ──────────────────────────────────

def _load_json(path: Path) -> Optional[Dict]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _age_hours(ts: Optional[str], now: datetime) -> Optional[float]:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return round((now - t).total_seconds() / 3600.0, 1)
    except Exception:
        return None


def _lens_info(ticker: str, now: datetime) -> Dict:
    d = _load_json(RC / f"stock_lens_{ticker}_latest.json")
    if d is None:
        return {"exists": False, "age_hours": None, "label": None,
                "stale": True, "constructive": False, "options_quality": None}
    age = _age_hours(d.get("built_at") or d.get("generated_at"), now)
    label = d.get("label")
    opt = (d.get("layers") or {}).get("options") or {}
    oq = None
    if opt.get("available"):
        oq = opt.get("spread_quality") or "available"
    constructive = bool(label) and str(label).strip().lower().startswith(
        _CONSTRUCTIVE_LENS_PREFIX) and "extended" not in str(label).lower()
    stale = age is None or age > LENS_STALE_HOURS
    return {"exists": True, "age_hours": age, "label": label, "stale": stale,
            "constructive": constructive, "options_quality": oq}


def _gatekeeper_info(ticker: str, now: datetime) -> Dict:
    d = _load_json(RC / f"executive_gatekeeper_{ticker}_latest.json")
    if d is None:
        return {"exists": False, "age_hours": None, "status": None, "stale": True,
                "blocked": False}
    age = _age_hours(d.get("generated_at"), now)
    status = d.get("final_status")
    blocked = (status == "BLOCK") or bool(d.get("blocking_reasons"))
    stale = age is None or age > GATEKEEPER_STALE_HOURS
    return {"exists": True, "age_hours": age, "status": status, "stale": stale,
            "blocked": blocked}


def _alpha_board() -> Tuple[set, Dict[str, float]]:
    d = _load_json(RC / "alpha_discovery_board_latest.json") or {}
    tickers = {str(i.get("ticker", "")).upper() for i in (d.get("items") or [])}
    return tickers, {}


def _theme_leaders(theme_doc: Dict, watched_states=("LEADING", "EMERGING")) -> Dict[str, List[str]]:
    """{theme: [leader tickers]} for themes in a watched state. top_leaders is a
    list of ticker strings in the radar artifact."""
    out: Dict[str, List[str]] = {}
    for name, d in (theme_doc.get("themes") or {}).items():
        if d.get("theme_state") in watched_states:
            leaders = [str(t).upper() for t in (d.get("top_leaders") or [])][:THEME_LEADERS_PER_THEME]
            if leaders:
                out[name] = leaders
    return out


# ── candidate assembly ────────────────────────────────────────────────────────

def _assemble_candidates(rs_doc: Dict, theme_doc: Dict,
                         proposed_rows: List[Dict]) -> Dict[str, Dict]:
    """ticker → {sources: set, theme_hint, stage_hint, score_hint, rs20_hint}."""
    cand: Dict[str, Dict] = {}

    def _add(t: str, source: str, **hints):
        t = t.upper()
        if t in dataio.BENCHMARKS:
            return
        e = cand.setdefault(t, {"sources": set(), "hints": {}})
        e["sources"].add(source)
        for k, v in hints.items():
            if v is not None:
                e["hints"].setdefault(k, v)

    # RS recall lane top leaders
    for r in (rs_doc.get("live", {}).get("top_rs_leaders") or [])[:RS_LANE_TOP]:
        _add(r["ticker"], "RS", rs20_hint=r.get("rs20"), theme_hint=r.get("theme"))

    # Leading/Emerging theme leaders
    for theme, leaders in _theme_leaders(theme_doc).items():
        for t in leaders:
            _add(t, "theme", theme_hint=theme)

    # Proposed-dynamic early leaders (richest per-ticker detail)
    ranked = sorted(proposed_rows, key=lambda r: -(r.get("early_leader_score") or 0))
    for r in ranked[:PROPOSED_EARLY_TOP]:
        _add(r["ticker"], "proposed_dynamic",
             stage_hint=r.get("stage_label"), score_hint=r.get("early_leader_score"),
             theme_hint=r.get("theme"))
    return cand


def _source_label(sources: set) -> str:
    return "overlap" if len(sources) > 1 else next(iter(sources))


# ── per-ticker evaluation ─────────────────────────────────────────────────────

def _extension_state(f: Dict, sc: Dict) -> str:
    codes = set(sc.get("reason_codes") or [])
    if "PARABOLIC_5D" in codes or (f.get("r5") or 0) >= 0.25:
        return "parabolic"
    if "EXTENDED_EMA20" in codes or (sc.get("late_extension_score") or 0) >= LATE_EXTENSION_TOO_EXTENDED:
        return "extended"
    ext20 = f.get("ext_ema20")
    if ext20 is not None and abs(ext20) <= 0.05:
        return "near_ema20"
    return "constructive"


def _pullback_potential(f: Dict, stage: str) -> str:
    if stage == "PULLBACK_RECLAIM":
        return "reclaim_setup"
    ext20 = f.get("ext_ema20")
    if ext20 is not None and -0.10 <= ext20 <= 0.05:
        return "near_support"
    if (f.get("pullback_from_high") or 0) >= 0.10:
        return "pulled_back_from_high"
    return "none"


def _gate_eval(ticker: str, asof: pd.Timestamp) -> Dict:
    """Run the live-mirrored Voyager/Sniper gates read-only and classify the kill."""
    df = dataio.load_prices(ticker)
    if df is None:
        return {"evaluable": False}
    v = voyager_structural(df, asof)
    s = sniper_breakout(df, asof)
    passed_either = bool(v.passed or s.passed)
    reasons = sorted(set(v.reasons) | set(s.reasons))
    killed = not passed_either
    non_cache = [r for r in reasons if r not in CACHE_DEPTH_REASONS]
    if not killed:
        root = "passes_a_gate"
    elif not non_cache:
        root = "cache_artifact"
    elif all(r in GATE_DESIGN_REASONS for r in non_cache):
        root = "gate_design_mismatch"
    elif any(r in REAL_QUALITY_REASONS for r in non_cache):
        root = "real_quality"
    else:
        root = "unknown"
    return {"evaluable": True, "passed_voyager": v.passed, "passed_sniper": s.passed,
            "passed_either": passed_either, "killed": killed, "reasons": reasons,
            "root_cause": root}


def _alpha_reason(ticker: str, on_board: set, profiles: Dict) -> str:
    if ticker in on_board:
        return "on_alpha_board"
    mcap = (profiles.get(ticker) or {}).get("market_cap")
    res = alpha_market_cap_eligible(mcap)
    if not res.passed:
        return res.reasons[0] if res.reasons else "market_cap_ineligible"
    return "alpha_board_cap"          # cap-band eligible but did not rank top-25


def _triage_label(f: Optional[Dict], stage: Optional[str], sc: Optional[Dict],
                  lens: Dict, gk: Dict) -> str:
    if f is None or (f.get("bars") or 0) < 60:
        return "NOT_ENOUGH_DATA"
    if gk["exists"] and not gk["stale"] and gk["blocked"]:
        return "BLOCKED"
    ext_state = _extension_state(f, sc or {})
    if stage in LATE_STAGES or ext_state in ("extended", "parabolic"):
        return "TOO_EXTENDED"
    if stage in NOISE_STAGES:
        return "LOW_QUALITY_NOISE"
    if lens["exists"] and not lens["stale"] and not lens["constructive"]:
        # Lens already evaluated it and found no constructive edge — triage worked.
        return "LOW_QUALITY_NOISE"
    if not lens["exists"] or lens["stale"]:
        return "NEEDS_LENS"
    # Lens present, fresh, constructive from here.
    if not gk["exists"] or gk["stale"]:
        return "NEEDS_GATEKEEPER"
    if not gk["blocked"]:
        return "LENS_READY"
    return "RESEARCH_WATCH"


def _evaluate(ticker: str, meta: Dict, cal, i: int, asof, spy, qqq, profiles,
              theme_states: Dict[str, str], on_board: set, now: datetime) -> Dict:
    f = features(ticker, cal, i, spy, qqq, profiles)
    stage = score = None
    sc: Dict = {}
    if f is not None:
        stage = classify_stage(f)
        f["_stage"] = stage
        sc = early_leader_score(f, theme_states.get(f.get("theme")))
        score = sc.get("early_leader_score")
    lens = _lens_info(ticker, now)
    gk = _gatekeeper_info(ticker, now)
    gate = _gate_eval(ticker, asof)
    label = _triage_label(f, stage, sc, lens, gk)
    theme = (f.get("theme") if f else None) or meta["hints"].get("theme_hint")
    return {
        "ticker": ticker,
        "source": _source_label(meta["sources"]),
        "sources": sorted(meta["sources"]),
        "stage_label": stage or meta["hints"].get("stage_hint"),
        "early_leader_score": score if score is not None else meta["hints"].get("score_hint"),
        "rs_score": (sc.get("relative_strength_score") if sc else None),
        "rs20_vs_spy": (round(f["rs20_vs_spy"], 4) if f and f.get("rs20_vs_spy") is not None else
                        meta["hints"].get("rs20_hint")),
        "theme_score": sc.get("theme_score") if sc else None,
        "theme": theme,
        "theme_state": theme_states.get(theme) if theme else None,
        "extension_state": _extension_state(f, sc) if f else None,
        "late_extension_score": sc.get("late_extension_score") if sc else None,
        "pullback_potential": _pullback_potential(f, stage) if f else None,
        "price": (f.get("price") if f else None),
        "lens_exists": lens["exists"],
        "lens_age_hours": lens["age_hours"],
        "lens_state": lens["label"],
        "lens_stale": lens["stale"],
        "gatekeeper_exists": gk["exists"],
        "gatekeeper_age_hours": gk["age_hours"],
        "gatekeeper_status": gk["status"],
        "gatekeeper_stale": gk["stale"],
        "options_quality": lens["options_quality"],
        "reason_not_on_alpha_board": _alpha_reason(ticker, on_board, profiles),
        "killed_by_gates": gate.get("killed"),
        "gate_passed_either": gate.get("passed_either"),
        "gate_rejection_reasons": gate.get("reasons", []),
        "gate_root_cause": gate.get("root_cause"),
        "triage_label": label,
    }


# ── Task 2: targeted refresh plan ─────────────────────────────────────────────

def _refresh_plan(rows: List[Dict]) -> Dict:
    missing_lens = [r["ticker"] for r in rows if not r["lens_exists"]]
    stale_lens = [r["ticker"] for r in rows if r["lens_exists"] and r["lens_stale"]]
    missing_gk = [r["ticker"] for r in rows
                  if (r["lens_exists"] and not r["lens_stale"]) and not r["gatekeeper_exists"]]
    stale_gk = [r["ticker"] for r in rows
                if r["gatekeeper_exists"] and r["gatekeeper_stale"]]
    lens_targets = missing_lens + stale_lens
    gk_targets = missing_gk + stale_gk
    cmds = []
    if lens_targets:
        cmds.append({
            "purpose": "build/refresh Stock Lens (PROVIDER calls — operator approval required)",
            "cost": f"~{len(lens_targets)} stock-lens builds (Alpaca bars + FMP profile/options per ticker)",
            "command": "./scripts/run_research_cycle.sh lens " + " ".join(lens_targets),
        })
    if gk_targets:
        cmds.append({
            "purpose": "refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)",
            "cost": f"~{len(gk_targets)} gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)",
            "command": "./scripts/run_research_cycle.sh gatekeeper-refresh --watch "
                       + " ".join(gk_targets),
        })
    return {
        "auto_refresh": False,
        "note": "DESIGN ONLY. No refresh is executed by this report. Run the commands "
                "below only with explicit operator approval.",
        "recommended_refresh_tickers": {
            "missing_lens": missing_lens, "stale_lens": stale_lens,
            "missing_gatekeeper": missing_gk, "stale_gatekeeper": stale_gk,
        },
        "expected_cost": {
            "lens_builds": len(lens_targets), "gatekeeper_rebuilds": len(gk_targets),
        },
        "commands": cmds,
    }


# ── Task 3: gate rejection decomposition ──────────────────────────────────────

def _gate_decomposition(rows: List[Dict]) -> Dict:
    killed = [r for r in rows if r.get("killed_by_gates")]
    raw_counts: Counter = Counter()
    bucket_counts: Counter = Counter()
    for r in killed:
        for code in r.get("gate_rejection_reasons", []):
            raw_counts[code] += 1
            bucket_counts[REASON_BUCKET.get(code, "unknown")] += 1
    root = Counter(r.get("gate_root_cause") for r in killed)
    cache_artifact = root.get("cache_artifact", 0)
    gate_design = root.get("gate_design_mismatch", 0)
    real_quality = root.get("real_quality", 0)
    unknown = root.get("unknown", 0)
    return {
        "n_evaluated": sum(1 for r in rows if r.get("killed_by_gates") is not None),
        "n_killed_by_both_gates": len(killed),
        "raw_rejection_counts": dict(raw_counts.most_common()),
        "bucketed_rejection_counts": dict(bucket_counts.most_common()),
        "root_cause_counts": {
            "cache_or_data_depth_artifact": cache_artifact,
            "gate_design_mismatch": gate_design,
            "real_quality_rejection": real_quality,
            "unknown": unknown,
        },
        "possibly_valid_early_candidates": cache_artifact + gate_design,
        "interpretation": (
            "cache_or_data_depth_artifact = killed only by the 260/75-bar history gate "
            "(shallow cache, not a structure failure); gate_design_mismatch = killed only "
            "by breakout/contraction/volume gates an EARLY leader is not meant to satisfy "
            "yet; real_quality_rejection = killed by a genuine structural reason "
            "(too extended, below MA200 floor). possibly_valid_early_candidates sums the "
            "first two — names a Lens/Gatekeeper second surface could legitimately surface."),
    }


# ── Task 4: triage quality summary + verdict ──────────────────────────────────

def _summary(rows: List[Dict], decomp: Dict) -> Dict:
    lab = Counter(r["triage_label"] for r in rows)
    needs_lens = lab.get("NEEDS_LENS", 0)
    needs_gk = lab.get("NEEDS_GATEKEEPER", 0)
    lens_ready = lab.get("LENS_READY", 0)
    too_ext = lab.get("TOO_EXTENDED", 0)
    blocked = lab.get("BLOCKED", 0)
    watch = lab.get("RESEARCH_WATCH", 0)
    noise = lab.get("LOW_QUALITY_NOISE", 0)
    not_enough = lab.get("NOT_ENOUGH_DATA", 0)
    with_options = sum(1 for r in rows if r.get("options_quality"))
    leading_theme = sum(1 for r in rows if r.get("theme_state") == "LEADING")
    killed_only_alpha_cap = sum(
        1 for r in rows
        if r.get("reason_not_on_alpha_board") == "alpha_board_cap"
        and not r.get("killed_by_gates"))
    cache_gate_artifact = decomp["possibly_valid_early_candidates"]

    n = len(rows)
    actionable = needs_lens + needs_gk + lens_ready + watch
    quality_share = (actionable / n) if n else 0.0
    # Verdict ladder (no forward maturity yet ⇒ never above PROMISING here).
    if n == 0:
        verdict = "NEED_MORE_DATA"
    elif (lens_ready or needs_gk) and quality_share >= 0.4 and noise <= actionable:
        verdict = "PROMISING_RESEARCH_SURFACE"
    elif actionable >= 3 and decomp["possibly_valid_early_candidates"] >= actionable:
        verdict = "PROMISING_RESEARCH_SURFACE"
    elif actionable == 0 and (noise + not_enough) >= n * 0.6:
        verdict = "NO_VALUE"
    else:
        verdict = "NEED_MORE_DATA"

    return {
        "candidates_evaluated": n,
        "needs_lens": needs_lens,
        "needs_gatekeeper": needs_gk,
        "lens_ready": lens_ready,
        "too_extended": too_ext,
        "blocked": blocked,
        "research_watch": watch,
        "low_quality_noise": noise,
        "not_enough_data": not_enough,
        "with_options_confirmation": with_options,
        "in_leading_themes": leading_theme,
        "killed_only_alpha_board_cap": killed_only_alpha_cap,
        "killed_by_cache_or_gate_artifact": cache_gate_artifact,
        "label_distribution": dict(lab.most_common()),
        "key_question": ("Would routing RS/theme leaders to Lens/Gatekeeper reveal useful "
                         "candidates, or just create noise?"),
        "verdict": verdict,
    }


# ── Task 5: forward historizer (idempotent per date/ticker) ───────────────────

def _historize(rows: List[Dict], asof_date: str, now_iso: str) -> Dict:
    existing = dataio.read_jsonl(TRIAGE_HISTORY)
    seen = {(r.get("asof_date"), r.get("ticker")) for r in existing}
    new_rows: List[Dict] = []
    for r in rows:
        key = (asof_date, r["ticker"])
        if key in seen:
            continue
        seen.add(key)
        new_rows.append({
            "generated_at": now_iso,
            "asof_date": asof_date,
            "ticker": r["ticker"],
            "triage_label": r["triage_label"],
            "source": r["source"],
            "stage_label": r["stage_label"],
            "early_leader_score": r["early_leader_score"],
            "rs_score": r["rs_score"],
            "theme": r["theme"],
            "lens_state": r["lens_state"],
            "gatekeeper_status": r["gatekeeper_status"],
            "options_quality": r["options_quality"],
            "rejection_reasons": r["gate_rejection_reasons"],
            "price": r["price"],
        })
    written = dataio.append_jsonl(TRIAGE_HISTORY, new_rows) if new_rows else 0
    return {"asof_date": asof_date, "rows_written": written,
            "already_present": written == 0,
            "history_total_rows": len(existing) + written,
            "history_path": dataio.rel_to_repo(TRIAGE_HISTORY)}


# ── build ──────────────────────────────────────────────────────────────────────

def _load_proposed_rows() -> Tuple[List[Dict], Optional[str]]:
    """Latest-date proposed_dynamic early-leader rows from the shadow ledger."""
    ledger = dataio.read_jsonl(HISTORY_PATH)
    by_date: Dict[str, List[Dict]] = {}
    for r in ledger:
        if r.get("universe_version") == PROPOSED_VERSION and r.get("included"):
            by_date.setdefault(r["asof_date"], []).append(r)
    if not by_date:
        return [], None
    latest = sorted(by_date)[-1]
    early = [r for r in by_date[latest] if r.get("stage_label") in TRIAGE_EARLY_STAGES]
    return early, latest


def _options_regime_annotation(options_regime: Dict) -> Dict:
    """Phase 1G.12 — compact annotation from the Options Regime Lens sidecar.
    Annotation only: it never changes candidate labels, never blocks, never
    approves. None-safe; absent sidecar degrades to all-None."""
    mk = (options_regime or {}).get("market") or {}
    return {
        "market_options_regime": mk.get("market_options_regime"),
        "skew_warning": mk.get("skew_state") == "PUT_HEDGE_DEMAND",
        "gamma_warning": mk.get("gamma_regime") == "negative_gamma_regime",
        "iv_rank_context": mk.get("iv_state"),
        "risk_warning_level": mk.get("risk_warning_level"),
        "note": "annotation only — does not change candidate labels or block candidates",
    }


def build(cap: int = DEFAULT_CAP) -> Dict:
    now = datetime.now(timezone.utc)
    rs_doc = _load_json(RC / "rs_recall_lane_latest.json") or {}
    theme_doc = _load_json(RC / "theme_leadership_latest.json") or {}
    forecast = _load_json(RC / "regime_forecast_latest.json") or {}
    # Phase 1G.12 — annotation-only options-regime context (cache-only read of
    # the Options Regime Lens sidecar). Surfaces market options conditions
    # alongside the candidates; it never changes a candidate label, never
    # blocks, and never approves. Missing sidecar degrades to all-None.
    options_regime = _load_json(RC / "options_regime_lens_latest.json") or {}
    proposed_rows, proposed_date = _load_proposed_rows()
    theme_states = _theme_states()
    on_board, _ = _alpha_board()
    profiles = dataio.load_profiles()

    cal = dataio.benchmark_calendar()
    i = len(cal) - 1
    asof = cal[-1]
    spy = _aligned(dataio.load_prices("SPY"), cal)
    qqq_df = dataio.load_prices("QQQ")
    qqq = _aligned(qqq_df, cal) if qqq_df is not None else spy

    cand = _assemble_candidates(rs_doc, theme_doc, proposed_rows)
    rows: List[Dict] = []
    for t, meta in cand.items():
        rows.append(_evaluate(t, meta, cal, i, asof, spy, qqq, profiles,
                              theme_states, on_board, now))

    # Rank: overlap first, then highest early_leader_score, then RS edge. Cap.
    def _rank_key(r: Dict):
        return (0 if r["source"] == "overlap" else 1,
                -(r["early_leader_score"] or 0),
                -((r["rs20_vs_spy"] or 0)))
    rows.sort(key=_rank_key)
    rows = rows[:cap]

    decomp = _gate_decomposition(rows)
    summary = _summary(rows, decomp)
    refresh = _refresh_plan(rows)
    asof_date = str(asof.date())
    hist = _historize(rows, asof_date, now.isoformat())

    fc = forecast.get("headline") or {}
    return {
        "generated_at": now.isoformat(),
        "asof_date": asof_date,
        "disclaimer": ("RESEARCH-ONLY triage surface. Routing labels only — NOT buy/sell "
                       "signals, NOT paper signals, NOT trade proposals. Does NOT modify "
                       "core/universe.py, strategy gates, execution, or governance."),
        "phase": "1G.9",
        "cap": cap,
        "market_regime": {"regime": fc.get("current_regime"), "bias_5d": fc.get("bias_5d"),
                          "confidence": fc.get("confidence")},
        # Phase 1G.12 — annotation only (NOT a label/gate). Diagnostic context.
        "options_regime_context": _options_regime_annotation(options_regime),
        "inputs": {
            "rs_lane_asof": rs_doc.get("live", {}).get("asof_date"),
            "theme_asof": theme_doc.get("asof_date"),
            "leading_themes": theme_doc.get("leading_themes"),
            "proposed_universe_asof": proposed_date,
            "n_proposed_early_leaders": len(proposed_rows),
        },
        "candidates": rows,
        "refresh_plan": refresh,
        "gate_decomposition": decomp,
        "summary": summary,
        "verdict": summary["verdict"],
        "historize": hist,
    }


# ── rendering ──────────────────────────────────────────────────────────────────

def _render_txt(res: Dict) -> List[str]:
    s = res["summary"]
    L = [
        f"== RS/THEME → LENS/GATEKEEPER TRIAGE ({res['generated_at']}) ==",
        res["disclaimer"],
        f"asof={res['asof_date']}  leading_themes={res['inputs']['leading_themes']}  "
        f"regime={res['market_regime'].get('regime')}",
        f"VERDICT: {res['verdict']}",
        "",
        f"evaluated={s['candidates_evaluated']}  needs_lens={s['needs_lens']}  "
        f"needs_gatekeeper={s['needs_gatekeeper']}  lens_ready={s['lens_ready']}  "
        f"too_extended={s['too_extended']}  blocked={s['blocked']}  "
        f"research_watch={s['research_watch']}  noise={s['low_quality_noise']}  "
        f"not_enough_data={s['not_enough_data']}",
        f"with_options={s['with_options_confirmation']}  leading_theme={s['in_leading_themes']}  "
        f"killed_only_alpha_cap={s['killed_only_alpha_board_cap']}  "
        f"cache/gate_artifact={s['killed_by_cache_or_gate_artifact']}",
        "",
        f"{'ticker':<8}{'src':<10}{'stage':<18}{'els':>5}{'triage':>18}  lens/gk",
    ]
    for r in res["candidates"]:
        lens = "L" if (r["lens_exists"] and not r["lens_stale"]) else ("l?" if r["lens_exists"] else "—")
        gk = "G" if (r["gatekeeper_exists"] and not r["gatekeeper_stale"]) else ("g?" if r["gatekeeper_exists"] else "—")
        els = r["early_leader_score"]
        src = ("ovlp" if r["source"] == "overlap" else
               {"proposed_dynamic": "prop", "theme": "theme", "RS": "RS"}.get(r["source"], r["source"]))
        L.append(f"{r['ticker']:<8}{src:<6}{str(r['stage_label'] or '—'):<19}"
                 f"{(els if els is not None else 0):>5.0f}{r['triage_label']:>18}  {lens}/{gk}")
    d = res["gate_decomposition"]
    L += ["",
          f"GATE DECOMPOSITION (killed by both gates n={d['n_killed_by_both_gates']}):",
          f"  root causes: {d['root_cause_counts']}",
          f"  possibly-valid early candidates (cache+gate-design): {d['possibly_valid_early_candidates']}",
          f"  bucketed reasons: {d['bucketed_rejection_counts']}"]
    rp = res["refresh_plan"]
    L += ["", "REFRESH PLAN (design only, no auto-refresh):"]
    for c in rp["commands"]:
        L.append(f"  [{c['cost']}] {c['command']}")
    if not rp["commands"]:
        L.append("  (nothing missing/stale)")
    return L


def _render_doc(res: Dict) -> str:
    s = res["summary"]
    d = res["gate_decomposition"]
    L = [
        "# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9",
        "",
        f"*Generated {res['generated_at']} · research-only · cache-only. Routing labels "
        "only — not buy/sell signals, not paper signals, not trade proposals. Does NOT "
        "modify the production universe, strategy gates, execution, or governance.*",
        "",
        f"**Verdict:** `{res['verdict']}`",
        "",
        "## Why this surface exists",
        "Phase 1G.8 found 333/356 proposed-dynamic early leaders are killed by the "
        "Voyager/Sniper structural gates, but most rejections are cache-depth artifacts. "
        "Its own recommendation was to route RS/theme early leaders to the Stock "
        "Lens/Gatekeeper as a research-only second surface that BYPASSES those score "
        "gates. This report is that surface — diagnostic only, no gate change.",
        "",
        "## Triage quality summary (Task 4)",
        "",
        "| metric | value |",
        "|---|--:|",
        f"| candidates evaluated | {s['candidates_evaluated']} |",
        f"| needs Lens | {s['needs_lens']} |",
        f"| needs Gatekeeper | {s['needs_gatekeeper']} |",
        f"| Lens-ready (both artifacts fresh) | {s['lens_ready']} |",
        f"| too extended | {s['too_extended']} |",
        f"| blocked | {s['blocked']} |",
        f"| research-watch | {s['research_watch']} |",
        f"| low-quality noise | {s['low_quality_noise']} |",
        f"| not enough data | {s['not_enough_data']} |",
        f"| with options confirmation | {s['with_options_confirmation']} |",
        f"| in leading themes | {s['in_leading_themes']} |",
        f"| killed only by Alpha-board cap | {s['killed_only_alpha_board_cap']} |",
        f"| killed by cache/gate artifact | {s['killed_by_cache_or_gate_artifact']} |",
        "",
        f"**Key question:** {s['key_question']}",
        "",
        "## Gate rejection decomposition (Task 3)",
        "",
        f"- Killed by both Voyager+Sniper gates: **{d['n_killed_by_both_gates']}** / "
        f"{d['n_evaluated']} evaluable.",
        f"- Root causes: `{d['root_cause_counts']}`",
        f"- Possibly-valid early candidates (cache-depth + gate-design only): "
        f"**{d['possibly_valid_early_candidates']}**",
        f"- Bucketed reasons: `{d['bucketed_rejection_counts']}`",
        "",
        f"*{d['interpretation']}*",
        "",
        "## Candidates",
        "",
        "| ticker | source | stage | ELS | theme | ext | lens | gk | options | "
        "alpha-board | gate root | triage |",
        "|---|---|---|--:|---|---|---|---|---|---|---|---|",
    ]
    for r in res["candidates"]:
        els = r["early_leader_score"]
        L.append(
            f"| {r['ticker']} | {r['source']} | {r['stage_label'] or '—'} "
            f"| {els if els is not None else '—'} | {r['theme'] or '—'} "
            f"| {r['extension_state'] or '—'} "
            f"| {(r['lens_state'] or '—')} | {r['gatekeeper_status'] or '—'} "
            f"| {r['options_quality'] or '—'} | {r['reason_not_on_alpha_board']} "
            f"| {r['gate_root_cause'] or '—'} | **{r['triage_label']}** |")
    rp = res["refresh_plan"]
    L += ["", "## Targeted refresh plan (Task 2 — design only, not executed)", "",
          rp["note"], ""]
    for c in rp["commands"]:
        L += [f"- **{c['purpose']}** — {c['cost']}", f"  ```", f"  {c['command']}", "  ```"]
    if not rp["commands"]:
        L.append("- Nothing missing or stale among the current candidates.")
    L += ["", "## Forward maturation", "",
          "Each run appends today's triage to "
          f"`{res['historize']['history_path']}` (idempotent per date/ticker). "
          "Forward outcomes will later answer whether research-watch names outperform, "
          "too-extended names pull back, the Lens/Gatekeeper rejected correctly, and "
          "whether RS/theme triage beats the Alpha board. No future data is stored today.",
          ""]
    return "\n".join(L) + "\n"


# ── MCP summary block (Task 6) ─────────────────────────────────────────────────

def mcp_summary(res: Optional[Dict] = None) -> Dict:
    """Compact, cache-only summary for the MCP audit orchestrator / dashboard.
    None-safe: reads the latest sidecar when no payload is supplied."""
    if res is None:
        res = _load_json(RC / "rs_theme_lens_triage_latest.json")
    if not res:
        return {"present": False}
    s = res.get("summary") or {}
    return {
        "present": True,
        "evaluated": s.get("candidates_evaluated"),
        "research_watch": s.get("research_watch"),
        "needs_lens": s.get("needs_lens"),
        "needs_gatekeeper": s.get("needs_gatekeeper"),
        "too_extended": s.get("too_extended"),
        "blocked": s.get("blocked"),
        "verdict": res.get("verdict"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1G.9 RS/Theme → Lens/Gatekeeper triage")
    ap.add_argument("--cap", type=int, default=DEFAULT_CAP,
                    help=f"max candidates (default {DEFAULT_CAP})")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the text report")
    args = ap.parse_args(argv)

    res = build(cap=args.cap)
    dataio.write_json(RC / "rs_theme_lens_triage_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "rs_theme_lens_triage_latest.txt", lines)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "RS_THEME_LENS_TRIAGE.md").write_text(_render_doc(res))
    print(json.dumps(res, indent=2, default=str) if args.json else "\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
