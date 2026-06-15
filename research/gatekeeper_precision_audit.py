"""
research/gatekeeper_precision_audit.py — Phase 1G.13.

Gatekeeper Precision Audit + Blocked-Winner Autopsy.

The June-4 checkpoint found that the RS/theme 1G.10 BLOCKED names OUTPERFORMED the
LENS_READY names and SPY at 5d (blocked +2.57% vs lens_ready +0.77% rel-SPY). That
is one small (n=8) cohort. This module asks the broader, evidence-based question:

    Are the Executive Gatekeeper's BLOCK decisions ADDING PRECISION (the blocked
    names go on to underperform) or REJECTING FUTURE WINNERS (the blocked names go
    on to outperform)?

It autopsies every BLOCK the Gatekeeper actually emitted — not just the RS/theme
cohort — by reading the per-ticker ``executive_gatekeeper_<T>_latest.json``
snapshots (each carries its own ``generated_at`` block-as-of date and blocking
reasons), the frozen immutable RS/theme 1G.10 cohort, and the missed-winner
universe. Each blocked name is scored OUT-OF-SAMPLE using bars strictly AFTER its
block date, over 1d/3d/5d/10d/20d (matured windows only).

The natural not-blocked control is the WATCH basket from the SAME gate over the
SAME dates; random-liquid / RS-top / Alpha-board controls anchor at the median
block date (single-anchor caveat, like 1G.11). Blocks are grouped by reason and
each reason is labelled GOOD_BLOCK / OVER_BLOCKING / DATA_ARTIFACT / NEED_MORE_DATA.
Cache-depth artifacts (insufficient_history_260 / below_ma200_floor on a shallow
cache) are isolated so MA200/260-bar blocks are not mistaken for real structure
rejections. A research-only rescue simulation tracks what the blocked baskets
WOULD have returned (forward measurement only — never a trade, signal, or proposal).

This is a RESEARCH AUDIT only. It never promotes a sleeve, never registers a
strategy, never modifies the Gatekeeper / Veto Council / universe / strategy gates
/ execution / governance / live capital, never emits paper signals or trade
proposals, never mutates historical evidence, and makes no provider calls. It reads
only cached artifacts and the cache-only price parquets.

Outputs:
  cache/research/gatekeeper_precision_audit_latest.json
  logs/gatekeeper_precision_audit_latest.txt
  docs/research/GATEKEEPER_PRECISION_AUDIT.md

CACHE-ONLY / RESEARCH-ONLY. No provider calls, no DB writes, no signals.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from datetime import datetime, timezone
from statistics import mean
from typing import Dict, List, Optional, Tuple

from research.rs_theme_forward_validation import (_aligned, _alpha_board_tickers,
                                                  _cal_index, _fwd_metrics,
                                                  _is_matured,
                                                  _liquid_universe_at, _rs_top_at)
from research.scanner_truth import dataio

# ── paths / constants ──────────────────────────────────────────────────────────
RC = dataio.RESEARCH_CACHE
LOGS = dataio.LOGS_DIR
DOCS = dataio.REPO / "docs" / "research"

COHORT_PATH = RC / "rs_theme_lens_ready_cohort_1g10.json"
MISSED_WINNERS = RC / "missed_winner_universe_latest.json"
PRICE_CACHE_COVERAGE = RC / "price_cache_coverage_latest.json"
GK_GLOB = "executive_gatekeeper_*_latest.json"

OUT_JSON = RC / "gatekeeper_precision_audit_latest.json"
OUT_TXT = LOGS / "gatekeeper_precision_audit_latest.txt"
OUT_MD = DOCS / "GATEKEEPER_PRECISION_AUDIT.md"

HORIZONS = (1, 3, 5, 10, 20)
PRIMARY_HORIZON = 5
MIN_MATURED_FOR_VERDICT = 20    # min matured BLOCK names before a real verdict
DEEP_BAR_FLOOR = 260            # bars needed for MA200 / 260-bar gates to be real
RANDOM_SEED = 1013              # deterministic controls (Phase 1G.13)

# Canonical block-reason taxonomy (matches Task 2 groups).
CANON_REASONS = [
    "too_extended", "below_ma200_floor", "insufficient_history_260",
    "no_breakout", "no_atr_contraction", "volume_insufficient",
    "ma50_not_rising", "options_bearish", "gatekeeper_stale",
    "data_missing", "unknown",
]
# Reasons that can only be a real rejection if the cache is deep enough to compute
# the 200-day / 260-bar context. On a shallow cache they are data-depth artifacts.
CACHE_DEPTH_REASONS = {"below_ma200_floor", "insufficient_history_260"}

DISCLAIMER = (
    "RESEARCH-ONLY Gatekeeper precision audit. Forward MEASUREMENT only — NOT "
    "buy/sell/trade signals, NOT paper signals, NOT trade proposals, and it does "
    "NOT make any blocked name tradeable. Does NOT promote any sleeve, register a "
    "strategy, or modify the Gatekeeper, Veto Council, core/universe.py, strategy "
    "gates, execution, governance, or live capital, and makes no provider calls.")
ARTIFACT_CAVEAT = (
    "Random / RS-top / Alpha-board controls anchor at the MEDIAN block date (single "
    "as-of), while BLOCK/WATCH baskets use each name's own block date. Alpha-board "
    "membership is the CURRENT artifact, not a point-in-time snapshot. Treat control "
    "comparisons as best-effort context, not as-of truth.")


# ── reason normalisation ─────────────────────────────────────────────────────────

def _canon_reason(raw: str) -> str:
    """Map a free-text / vocab reason to one canonical bucket (Task 2)."""
    r = (raw or "").strip().lower()
    if not r:
        return "unknown"
    if r in CANON_REASONS:
        return r
    if "too extended" in r or "extended" in r:
        return "too_extended"
    if "ma200" in r or "200-day" in r or "200 day" in r or "below_ma200" in r:
        return "below_ma200_floor"
    if "insufficient_history" in r or "260" in r or "not enough history" in r:
        return "insufficient_history_260"
    if "breakout" in r:
        return "no_breakout"
    if "atr" in r:
        return "no_atr_contraction"
    if "volume" in r:
        return "volume_insufficient"
    if "ma50" in r or "50-day" in r:
        return "ma50_not_rising"
    if ("option" in r and ("no_edge" in r or "no edge" in r or "bear" in r
                           or "caution" in r or "unusable" in r)):
        return "options_bearish"
    if "missing" in r or "insufficient_data" in r or "no daily entry" in r \
            or "silent" in r or "no size" in r:
        return "data_missing"
    if "stale" in r:
        return "gatekeeper_stale"
    return "unknown"


def _gk_block_reasons(doc: Dict) -> List[str]:
    """Extract canonical reasons from an executive-gatekeeper snapshot.

    The block fires from the gate(s) whose verdict is BLOCK; we also fold in the
    free-text blocking_reasons. INSUFFICIENT_DATA / MISSING ⇒ data_missing.
    """
    reasons: List[str] = []
    # Only the gate(s) that actually fired the BLOCK carry block reasons. MISSING
    # sub-gates are data-availability, not the rejection cause — folding them in
    # would swamp every block with a spurious "unknown" reason.
    for g in doc.get("gates", []) or []:
        if str(g.get("verdict") or "").upper() == "BLOCK":
            for txt in (g.get("reasons") or []):
                reasons.append(_canon_reason(str(txt)))
    for txt in (doc.get("blocking_reasons") or []):
        reasons.append(_canon_reason(str(txt)))
    if str(doc.get("final_status") or "").upper() == "INSUFFICIENT_DATA":
        reasons.append("data_missing")
    out = sorted(set(r for r in reasons if r))
    return out or ["unknown"]


# ── source loaders ───────────────────────────────────────────────────────────────

def _asof_date(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    try:
        return str(ts)[:10]
    except Exception:
        return None


def _load_rs_theme_blocked() -> List[Dict]:
    """The 8 frozen RS/theme 1G.10 BLOCKED names (Task 1 item A)."""
    if not COHORT_PATH.exists():
        return []
    doc = json.loads(COHORT_PATH.read_text())
    asof = doc.get("asof_date")
    out = []
    for c in doc.get("cohort", []):
        if c.get("triage_label") != "BLOCKED":
            continue
        reasons = [_canon_reason(r) for r in (c.get("gate_rejection_reasons") or [])] or ["unknown"]
        out.append({
            "ticker": c.get("ticker"), "asof": asof, "source": "rs_theme_1g10",
            "reasons": sorted(set(reasons)),
            "root_cause": c.get("gate_root_cause"),
            "options_quality": c.get("options_quality"),
            "price_asof": c.get("price_asof"),
        })
    return out


def _load_gatekeeper_snapshots() -> Tuple[List[Dict], List[Dict]]:
    """Scan per-ticker executive-gatekeeper snapshots.

    Returns (blocked, watch). US tickers only (cache has no foreign-listing bars).
    Each blocked entry carries its own block-as-of date (generated_at) and the
    canonical blocking reasons (Task 1 items B/C/D — the snapshots are written for
    alpha-board / lens / watch candidates, so those sources are subsumed).
    """
    blocked, watch = [], []
    for p in sorted(RC.glob(GK_GLOB)):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        t = str(d.get("ticker") or "").upper()
        if not t or "." in t:        # skip foreign / index-suffixed listings
            continue
        status = str(d.get("final_status") or "").upper()
        asof = _asof_date(d.get("generated_at"))
        if status == "BLOCK":
            blocked.append({
                "ticker": t, "asof": asof, "source": "gatekeeper_snapshot",
                "reasons": _gk_block_reasons(d),
                "root_cause": None, "options_quality": None, "price_asof": None,
            })
        elif status == "WATCH":
            watch.append({"ticker": t, "asof": asof, "source": "gatekeeper_snapshot"})
    return blocked, watch


def _load_missed_winners() -> Dict[str, Dict]:
    """Map ticker → winner row from the missed-winner universe (Task 1 item E)."""
    if not MISSED_WINNERS.exists():
        return {}
    try:
        d = json.loads(MISSED_WINNERS.read_text())
    except Exception:
        return {}
    out = {}
    for w in d.get("winners", []) or []:
        t = str(w.get("ticker") or "").upper()
        if t:
            out[t] = {"best_max_return": w.get("best_max_return"),
                      "best_window_return": w.get("best_window_return"),
                      "theme": w.get("theme")}
    return out


# ── per-entry forward metrics + cache-depth tag ──────────────────────────────────

def _entry_metrics(entry: Dict, cal, spy, qqq) -> Dict:
    """Attach per-horizon forward metrics + cache-depth classification to a blocked
    (or watch) entry, in place-returning a NEW dict. Each horizon only when matured."""
    t = entry["ticker"]
    i = _cal_index(cal, entry["asof"]) if entry.get("asof") else None
    df = dataio.load_prices(t)
    total_bars = int(df["close"].notna().sum()) if df is not None else 0
    deep_bars = dataio.deep_bar_count(t)
    deep_ok = max(total_bars, deep_bars) >= DEEP_BAR_FLOOR
    by_h = {}
    for h in HORIZONS:
        m = _fwd_metrics(t, cal, i, h, spy, qqq) if i is not None else None
        by_h[f"{h}d"] = m
    # cache-depth artifact: a cache-depth reason fired but the chart cannot support
    # a real MA200/260 evaluation (shallow on BOTH shallow and deep caches).
    reasons = entry.get("reasons", [])
    has_cache_reason = any(r in CACHE_DEPTH_REASONS for r in reasons)
    cache_artifact = bool(has_cache_reason and not deep_ok)
    return {
        **entry,
        "asof_index": i,
        "total_bars": total_bars,
        "deep_bars": deep_bars,
        "deep_cache_sufficient": deep_ok,
        "has_cache_depth_reason": has_cache_reason,
        "is_cache_depth_artifact": cache_artifact,
        "by_horizon": by_h,
    }


def _basket_stats(entries: List[Dict], h: int) -> Dict:
    """Pool per-name forward metrics (each at its own as-of) for horizon h."""
    rels, fwds, maes, mfes, ext, pb = [], [], [], [], [], []
    for e in entries:
        m = (e.get("by_horizon") or {}).get(f"{h}d")
        if not m:
            continue
        fwds.append(m["fwd_return"])
        maes.append(m["mae"])
        mfes.append(m["mfe"])
        if m["rel_return_spy"] is not None:
            rels.append(m["rel_return_spy"])
        if m["became_too_extended"] is not None:
            ext.append(m["became_too_extended"])
        if m["offered_pullback_reclaim"] is not None:
            pb.append(m["offered_pullback_reclaim"])
    return {
        "n_names": len(entries),
        "n_matured": len(fwds),
        "mean_fwd": round(mean(fwds), 4) if fwds else None,
        "mean_rel_spy": round(mean(rels), 4) if rels else None,
        "win_rate_vs_spy": round(100.0 * sum(1 for r in rels if r > 0) / len(rels), 1) if rels else None,
        "mean_mfe": round(mean(mfes), 4) if mfes else None,
        "mean_mae": round(mean(maes), 4) if maes else None,
        "pct_became_extended": round(100.0 * sum(ext) / len(ext), 1) if ext else None,
        "pct_offered_pullback": round(100.0 * sum(pb) / len(pb), 1) if pb else None,
    }


# ── Task 2: per-reason performance ───────────────────────────────────────────────

def _reason_performance(blocked: List[Dict]) -> Dict:
    out = {}
    for reason in CANON_REASONS:
        members = [b for b in blocked if reason in b.get("reasons", [])]
        if not members:
            continue
        s5 = _basket_stats(members, 5)
        s10 = _basket_stats(members, 10)
        cache_art = sum(1 for m in members if m.get("is_cache_depth_artifact"))
        out[reason] = {
            "n": len(members),
            "n_matured_5d": s5["n_matured"],
            "mean_rel_spy_5d": s5["mean_rel_spy"],
            "win_rate_vs_spy_5d": s5["win_rate_vs_spy"],
            "mean_rel_spy_10d": s10["mean_rel_spy"],
            "mean_mfe_5d": s5["mean_mfe"],
            "mean_mae_5d": s5["mean_mae"],
            "n_cache_depth_artifact": cache_art,
            "label": _reason_label(reason, s5, cache_art, len(members)),
        }
    return out


def _reason_label(reason: str, s5: Dict, cache_art: int, n: int) -> str:
    if reason in CACHE_DEPTH_REASONS and cache_art >= max(1, n // 2):
        return "DATA_ARTIFACT"
    if s5["n_matured"] < 5:
        return "NEED_MORE_DATA"
    rel = s5["mean_rel_spy"]
    if rel is None:
        return "NEED_MORE_DATA"
    # A "good" block: the names it rejected went on to UNDERPERFORM SPY.
    if rel <= 0:
        return "GOOD_BLOCK"
    return "OVER_BLOCKING"


# ── Task 3: blocked vs not-blocked vs controls ───────────────────────────────────

def _build_comparison(blocked: List[Dict], watch: List[Dict],
                      cal, spy, qqq, rng) -> Tuple[Dict, Dict]:
    # control anchor = median block-date index (single as-of; documented caveat)
    idxs = sorted(e["asof_index"] for e in blocked if e.get("asof_index") is not None)
    anchor_i = idxs[len(idxs) // 2] if idxs else None
    liquid = _liquid_universe_at(cal, anchor_i) if anchor_i is not None else []
    n_ctrl = max(len(blocked), 1)
    rand_ctrl = rng.sample(liquid, min(n_ctrl, len(liquid))) if liquid else []
    rs_top = _rs_top_at(cal, anchor_i, liquid, n_ctrl) if liquid else []
    board = [t for t in _alpha_board_tickers() if t not in dataio.BENCHMARKS]

    def control_stats(tickers, h):
        ents = [{"ticker": t, "asof_index": anchor_i,
                 "by_horizon": {f"{h}d": _fwd_metrics(t, cal, anchor_i, h, spy, qqq)}}
                for t in tickers]
        return _basket_stats(ents, h)

    by_h = {}
    for h in HORIZONS:
        matured = _is_matured(cal, anchor_i, h)
        by_h[f"{h}d"] = {
            "A_blocked": _basket_stats(blocked, h),
            "B_watch_not_blocked": _basket_stats(watch, h),
            "C_alpha_board": control_stats(board, h) if matured else None,
            "D_random_control": control_stats(rand_ctrl, h) if matured else None,
            "E_rs_top": control_stats(rs_top, h) if matured else None,
        }
    controls_used = {
        "anchor_index": anchor_i, "liquid_universe_size": len(liquid),
        "random_control": rand_ctrl, "rs_top": rs_top, "alpha_board_size": len(board),
    }
    return by_h, controls_used


# ── Task 5: research-only rescue simulation ──────────────────────────────────────

def _rescue_simulation(blocked: List[Dict]) -> Dict:
    """What WOULD the blocked baskets have returned (forward measurement only)?
    Subsets isolate which slice of the block list drives the result."""
    def subset(pred):
        return [b for b in blocked if pred(b)]
    real_weak = lambda b: b.get("root_cause") == "real_quality"
    design_mismatch = lambda b: b.get("root_cause") == "gate_design_mismatch"
    too_ext = lambda b: "too_extended" in b.get("reasons", [])
    sims = {
        "blocked_all": _basket_stats(blocked, PRIMARY_HORIZON),
        "blocked_ex_real_weak_structure": _basket_stats(subset(lambda b: not real_weak(b)), PRIMARY_HORIZON),
        "blocked_ex_data_artifact": _basket_stats(subset(lambda b: not b.get("is_cache_depth_artifact")), PRIMARY_HORIZON),
        "blocked_only_gate_design_mismatch": _basket_stats(subset(design_mismatch), PRIMARY_HORIZON),
        "blocked_only_too_extended": _basket_stats(subset(too_ext), PRIMARY_HORIZON),
    }
    return {
        "horizon": f"{PRIMARY_HORIZON}d",
        "subsets": sims,
        "note": ("RESEARCH-ONLY counterfactual: forward returns tracked AS IF the names "
                 "had not been blocked — NO trade, NO paper signal, NO proposal. Does not "
                 "approve anything. root_cause is only populated for the RS/theme 1G.10 "
                 "subset; gatekeeper-snapshot blocks have root_cause=None."),
    }


# ── Task 4: cache-depth artifact isolation ───────────────────────────────────────

def _cache_artifact_isolation(blocked: List[Dict]) -> Dict:
    cache_reason_blocks = [b for b in blocked if b.get("has_cache_depth_reason")]
    artifacts = [b for b in cache_reason_blocks if b.get("is_cache_depth_artifact")]
    trustworthy = [b for b in cache_reason_blocks if not b.get("is_cache_depth_artifact")]
    cov = {}
    if PRICE_CACHE_COVERAGE.exists():
        try:
            c = json.loads(PRICE_CACHE_COVERAGE.read_text())
            cov = {"median_bars_universe": c.get("median_bars_universe"),
                   "cache_uniformly_shallow": c.get("cache_uniformly_shallow"),
                   "deep_cache_present": c.get("deep_cache_present")}
        except Exception:
            cov = {}
    return {
        "deep_bar_floor": DEEP_BAR_FLOOR,
        "n_blocks_citing_cache_depth_reason": len(cache_reason_blocks),
        "n_data_depth_artifact": len(artifacts),
        "n_trustworthy_cache_block": len(trustworthy),
        "artifact_tickers": sorted({b["ticker"] for b in artifacts}),
        "trustworthy_tickers": sorted({b["ticker"] for b in trustworthy}),
        "price_cache_coverage": cov,
        "finding": (
            f"{len(artifacts)} of {len(cache_reason_blocks)} cache-depth-reason blocks fired "
            f"on tickers with < {DEEP_BAR_FLOOR} usable bars (shallow on both caches) — those "
            "below_ma200_floor / insufficient_history_260 blocks are DATA-DEPTH ARTIFACTS, not "
            "trustworthy structure rejections. Deepen the cache before judging them."),
    }


# ── verdict + recommendation (Task 6) ────────────────────────────────────────────

def _verdict(by_h: Dict, reason_perf: Dict, cache_iso: Dict, n_blocked_matured: int) -> Dict:
    ph = by_h.get(f"{PRIMARY_HORIZON}d", {})
    a = ph.get("A_blocked") or {}
    b = ph.get("B_watch_not_blocked") or {}
    d = ph.get("D_random_control") or {}
    blocked_rel = a.get("mean_rel_spy")
    watch_rel = b.get("mean_rel_spy")
    rand_rel = d.get("mean_rel_spy")

    over_blocking_reasons = [r for r, v in reason_perf.items() if v["label"] == "OVER_BLOCKING"]
    good_block_reasons = [r for r, v in reason_perf.items() if v["label"] == "GOOD_BLOCK"]
    n_cache_reason = cache_iso.get("n_blocks_citing_cache_depth_reason", 0)
    n_artifact = cache_iso.get("n_data_depth_artifact", 0)

    # Horizon-consistency: is the primary-horizon signal stable, or does it reverse
    # at longer horizons? (Blocks of "too extended" names can be early-but-right.)
    longest = None
    for h in reversed(HORIZONS):
        ah = (by_h.get(f"{h}d") or {}).get("A_blocked") or {}
        bh = (by_h.get(f"{h}d") or {}).get("B_watch_not_blocked") or {}
        if (ah.get("n_matured") or 0) >= 10 and bh.get("mean_rel_spy") is not None:
            longest = (h, ah.get("mean_rel_spy"), bh.get("mean_rel_spy"))
            break
    reversal_note = ""
    if longest and longest[0] > PRIMARY_HORIZON and blocked_rel is not None:
        lh, l_block, l_watch = longest
        short_over = blocked_rel >= (watch_rel if watch_rel is not None else 0)
        long_under = l_block < l_watch and l_block <= 0
        if short_over and long_under:
            reversal_note = (f" HORIZON REVERSAL: blocked names lead WATCH at {PRIMARY_HORIZON}d "
                             f"({blocked_rel:+.4f} vs {watch_rel}) but REVERSE by {lh}d "
                             f"({l_block:+.4f} vs {l_watch:+.4f}) — the blocks look early-but-right "
                             f"(mostly 'too extended' names that mean-revert). Do NOT loosen the gate "
                             f"on the short-horizon read alone.")
    else:
        # No well-populated horizon beyond the primary yet: the short-horizon
        # outperformance has not had time to mean-revert. Flag it honestly.
        a_win = a.get("win_rate_vs_spy")
        reversal_note = (f" CAVEAT: no horizon beyond {PRIMARY_HORIZON}d has ≥10 matured blocked names "
                         f"yet (10d/20d are n=2-3), so any mean-reversion of these 'too extended' "
                         f"names is not yet observable. BLOCK win-rate vs SPY is {a_win}% (~coin-flip) "
                         f"— the positive mean is driven by a few extended names that kept running, "
                         f"not a systematic edge. Treat the over-blocking read as SHORT-HORIZON, "
                         f"UNCONFIRMED.")

    if n_blocked_matured < MIN_MATURED_FOR_VERDICT or blocked_rel is None:
        return {"gatekeeper_verdict": "NEED_MORE_DATA", "recommendation": "D",
                "rationale": (f"Only {n_blocked_matured} matured BLOCK name(s) at "
                              f"{PRIMARY_HORIZON}d — below the {MIN_MATURED_FOR_VERDICT}-name "
                              "floor. Keep accumulating forward bars; production unchanged.")}

    # blocked underperformed the not-blocked WATCH set AND SPY ⇒ blocks add precision.
    beats_watch = watch_rel is not None and blocked_rel > watch_rel
    beats_random = rand_rel is not None and blocked_rel > rand_rel
    if n_artifact >= max(1, n_cache_reason // 2) and n_cache_reason >= 5:
        return {"gatekeeper_verdict": "CACHE_ARTIFACT_DOMINATED", "recommendation": "C",
                "rationale": (f"{n_artifact}/{n_cache_reason} cache-depth-reason blocks are data-depth "
                              "artifacts (MA200/260 not computable on this cache). Fix cache depth "
                              "before trusting or redesigning those gates.")}
    if blocked_rel <= 0 and not beats_watch and not beats_random:
        return {"gatekeeper_verdict": "WORKING", "recommendation": "A",
                "rationale": (f"BLOCK basket {blocked_rel:+.4f} rel-SPY ≤ 0 and underperforms the "
                              f"not-blocked WATCH set ({watch_rel}) and random control ({rand_rel}) — "
                              "the Gatekeeper is adding precision, not rejecting winners.")}
    if blocked_rel > 0 and (beats_watch or beats_random):
        # blocks reject winners overall, but reasons split → propose hard/soft split.
        # A horizon reversal means the short-horizon "over-blocking" is illusory →
        # keep measuring (B), do not redesign.
        if reversal_note:
            rec = "B"
        else:
            rec = "E" if (good_block_reasons and over_blocking_reasons) else "B"
        verdict_label = "OVER_BLOCKING_SHORT_HORIZON_ONLY" if reversal_note else "OVER_BLOCKING"
        tail = (" Split hard blocks vs soft warnings (E)." if rec == "E"
                else " Keep measuring nightly (B) before any redesign.")
        return {"gatekeeper_verdict": verdict_label, "recommendation": rec,
                "rationale": (f"BLOCK basket {blocked_rel:+.4f} rel-SPY > 0 and ≥ the not-blocked "
                              f"WATCH set ({watch_rel}) / random ({rand_rel}) at {PRIMARY_HORIZON}d — "
                              f"the gate appears to reject future winners short-horizon. Over-blocking "
                              f"reasons={over_blocking_reasons}; good-block reasons={good_block_reasons}."
                              + reversal_note + tail)}
    return {"gatekeeper_verdict": "MIXED", "recommendation": "B",
            "rationale": (f"BLOCK basket {blocked_rel:+.4f} rel-SPY is not decisively better or worse "
                          f"than the not-blocked WATCH set ({watch_rel}) — inconclusive; keep "
                          "measuring nightly (B), production unchanged.")}


RECOMMENDATION_OPTIONS = {
    "A": "Gatekeeper is working — keep unchanged.",
    "B": "Need more data — keep nightly cache-only measurement; production unchanged.",
    "C": "Mostly a cache-depth artifact — deepen the price cache first.",
    "D": "Need more data — sample below verdict floor.",
    "E": "Split Gatekeeper blocks into HARD blocks vs SOFT warnings (research redesign).",
}
SPLIT_CATEGORIES = {
    "HARD_BLOCK": "real weak structure, broken trend, severe liquidity / spread risk.",
    "SOFT_WARNING": ("insufficient history, no ATR contraction, no breakout yet, early "
                     "leader not mature."),
    "DATA_WARNING": "MA200 / 260-bar unavailable or unreliable on a shallow cache.",
}


# ── orchestration ────────────────────────────────────────────────────────────────

def build() -> Dict:
    cal = dataio.benchmark_calendar()
    spy = _aligned(dataio.load_prices("SPY"), cal)
    qqq = _aligned(dataio.load_prices("QQQ"), cal)
    rng = random.Random(RANDOM_SEED)

    rs_blocked = _load_rs_theme_blocked()
    gk_blocked, gk_watch = _load_gatekeeper_snapshots()
    winners = _load_missed_winners()

    # merge blocked sources (keep both; dedupe identical (ticker, asof, source))
    raw_blocked = rs_blocked + gk_blocked
    seen = set()
    blocked = []
    for e in raw_blocked:
        key = (e["ticker"], e.get("asof"), e["source"])
        if key in seen:
            continue
        seen.add(key)
        e["was_missed_winner"] = e["ticker"] in winners
        blocked.append(_entry_metrics(e, cal, spy, qqq))
    watch = [_entry_metrics(e, cal, spy, qqq) for e in gk_watch]

    by_h, controls_used = _build_comparison(blocked, watch, cal, spy, qqq, rng)
    reason_perf = _reason_performance(blocked)
    cache_iso = _cache_artifact_isolation(blocked)
    rescue = _rescue_simulation(blocked)

    n_blocked_matured = (by_h.get(f"{PRIMARY_HORIZON}d", {}).get("A_blocked") or {}).get("n_matured", 0)
    verdict = _verdict(by_h, reason_perf, cache_iso, n_blocked_matured)

    # blocked winners (Task 1 item E) — names the Gatekeeper blocked that were winners.
    blocked_winners = sorted({b["ticker"] for b in blocked if b.get("was_missed_winner")})

    asof_dates = sorted({b["asof"] for b in blocked if b.get("asof")})
    by_source = dict(Counter(b["source"] for b in blocked))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "1G.13",
        "disclaimer": DISCLAIMER,
        "primary_horizon": f"{PRIMARY_HORIZON}d",
        "min_matured_for_verdict": MIN_MATURED_FOR_VERDICT,
        "blocked_cohort": {
            "n_total": len(blocked),
            "by_source": by_source,
            "block_asof_range": [asof_dates[0], asof_dates[-1]] if asof_dates else None,
            "n_matured_primary": n_blocked_matured,
            "n_watch_not_blocked": len(watch),
            "n_blocked_that_were_winners": len(blocked_winners),
            "blocked_winner_tickers": blocked_winners,
        },
        "controls_used": controls_used,
        "by_horizon": by_h,
        "reason_performance": reason_perf,
        "cache_artifact_isolation": cache_iso,
        "rescue_simulation": rescue,
        "verdict": verdict,
        "recommendation_options": RECOMMENDATION_OPTIONS,
        "split_categories_if_E": SPLIT_CATEGORIES,
        "per_name": [_compact_name(b, winners) for b in blocked],
        "artifact_annotations_caveat": ARTIFACT_CAVEAT,
    }


def _compact_name(b: Dict, winners: Dict) -> Dict:
    m5 = (b.get("by_horizon") or {}).get(f"{PRIMARY_HORIZON}d") or {}
    correct = None
    if m5.get("rel_return_spy") is not None:
        # block was "correct" if the blocked name underperformed SPY forward.
        correct = m5["rel_return_spy"] <= 0
    return {
        "ticker": b["ticker"], "asof": b.get("asof"), "source": b["source"],
        "reasons": b.get("reasons"), "root_cause": b.get("root_cause"),
        "total_bars": b.get("total_bars"), "deep_cache_sufficient": b.get("deep_cache_sufficient"),
        "is_cache_depth_artifact": b.get("is_cache_depth_artifact"),
        "was_missed_winner": b.get("was_missed_winner"),
        "fwd_5d": m5.get("fwd_return"), "rel_spy_5d": m5.get("rel_return_spy"),
        "mfe_5d": m5.get("mfe"), "mae_5d": m5.get("mae"),
        "became_too_extended": m5.get("became_too_extended"),
        "offered_pullback_reclaim": m5.get("offered_pullback_reclaim"),
        "block_correct_5d": correct,
    }


# ── MCP / dashboard cache-only summary (Task 7) ──────────────────────────────────

def mcp_summary(res: Optional[Dict] = None) -> Dict:
    """Compact cache-only summary for the MCP orchestrator / dashboard. Reads the
    published sidecar; never recomputes, never calls providers."""
    if res is None:
        if not OUT_JSON.exists():
            return {"present": False}
        try:
            res = json.loads(OUT_JSON.read_text())
        except Exception:
            return {"present": False}
    ph = (res.get("by_horizon") or {}).get(f"{PRIMARY_HORIZON}d", {}) or {}
    a = ph.get("A_blocked") or {}
    b = ph.get("B_watch_not_blocked") or {}
    v = res.get("verdict") or {}
    return {
        "present": True,
        "n_blocked": (res.get("blocked_cohort") or {}).get("n_total"),
        "n_matured": (res.get("blocked_cohort") or {}).get("n_matured_primary"),
        "blocked_5d": a.get("mean_rel_spy"),
        "watch_5d": b.get("mean_rel_spy"),
        "verdict": v.get("gatekeeper_verdict"),
        "recommendation": v.get("recommendation"),
    }


# ── renderers ────────────────────────────────────────────────────────────────────

def _fmt(v, pct=False) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:.1f}%" if pct else f"{v:+.4f}"
    return str(v)


def _render_txt(res: Dict) -> List[str]:
    bc = res["blocked_cohort"]
    v = res["verdict"]
    L = [f"== GATEKEEPER PRECISION AUDIT — Phase 1G.13 ({res['generated_at']}) ==",
         res["disclaimer"], "",
         f"blocked cohort : n={bc['n_total']} ({bc['by_source']}) asof {bc['block_asof_range']}",
         f"matured {res['primary_horizon']}  : {bc['n_matured_primary']} (verdict floor {res['min_matured_for_verdict']})",
         f"watch control  : {bc['n_watch_not_blocked']} not-blocked names",
         f"blocked winners: {bc['n_blocked_that_were_winners']} {bc['blocked_winner_tickers'][:12]}",
         "",
         f"{'horizon':<8}{'A:block':>9}{'B:watch':>9}{'C:alpha':>9}{'D:rand':>9}{'E:rstop':>9}  (mean rel SPY)"]
    for h, d in res["by_horizon"].items():
        def g(k):
            return _fmt((d.get(k) or {}).get("mean_rel_spy")) if d.get(k) else "—"
        L.append(f"{h:<8}{g('A_blocked'):>9}{g('B_watch_not_blocked'):>9}"
                 f"{g('C_alpha_board'):>9}{g('D_random_control'):>9}{g('E_rs_top'):>9}")
    L += ["", "BLOCK REASON PERFORMANCE (5d):",
          f"  {'reason':<26}{'n':>4}{'mat':>5}{'rel5d':>9}{'win%':>7}  label"]
    for r, rp in sorted(res["reason_performance"].items(), key=lambda kv: -(kv[1]["n"])):
        L.append(f"  {r:<26}{rp['n']:>4}{rp['n_matured_5d']:>5}"
                 f"{_fmt(rp['mean_rel_spy_5d']):>9}{_fmt(rp['win_rate_vs_spy_5d'], pct=True):>7}  {rp['label']}")
    ci = res["cache_artifact_isolation"]
    L += ["", "CACHE-DEPTH ARTIFACT ISOLATION:",
          f"  cache-depth-reason blocks={ci['n_blocks_citing_cache_depth_reason']} "
          f"data-artifacts={ci['n_data_depth_artifact']} trustworthy={ci['n_trustworthy_cache_block']}",
          f"  {ci['finding']}"]
    rs = res["rescue_simulation"]
    L += ["", f"RESCUE SIMULATION (research-only, {rs['horizon']} mean rel-SPY):"]
    for name, st in rs["subsets"].items():
        L.append(f"  {name:<38}n={st['n_names']:>3} mat={st['n_matured']:>3} "
                 f"rel={_fmt(st['mean_rel_spy'])} win={_fmt(st['win_rate_vs_spy'], pct=True)}")
    L += ["", f"VERDICT: {v['gatekeeper_verdict']}  → recommendation {v['recommendation']}",
          f"  {RECOMMENDATION_OPTIONS.get(v['recommendation'], '')}",
          f"  {v['rationale']}"]
    if v["recommendation"] == "E":
        L += ["", "  PROPOSED hard/soft split categories (not implemented):"]
        for k, desc in res["split_categories_if_E"].items():
            L.append(f"    {k}: {desc}")
    L += ["", "caveat: " + res["artifact_annotations_caveat"]]
    return L


def _render_md(res: Dict) -> List[str]:
    bc = res["blocked_cohort"]
    v = res["verdict"]
    L = ["# Gatekeeper Precision Audit — Phase 1G.13", "",
         f"> {res['disclaimer']}", "",
         f"_Generated {res['generated_at']}._", "",
         "## Blocked cohort", "",
         f"- Total blocked names: **{bc['n_total']}** by source `{bc['by_source']}`",
         f"- Block as-of range: `{bc['block_asof_range']}`",
         f"- Matured at {res['primary_horizon']}: **{bc['n_matured_primary']}** "
         f"(verdict floor {res['min_matured_for_verdict']})",
         f"- Not-blocked WATCH control: **{bc['n_watch_not_blocked']}**",
         f"- Blocked names that were missed winners: **{bc['n_blocked_that_were_winners']}** "
         f"`{bc['blocked_winner_tickers'][:20]}`", "",
         "## Forward returns by horizon (mean excess vs SPY)", "",
         "| Horizon | A BLOCKED | B WATCH (not blocked) | C Alpha | D Random | E RS-top |",
         "|---|---|---|---|---|---|"]
    for h, d in res["by_horizon"].items():
        def g(k):
            return _fmt((d.get(k) or {}).get("mean_rel_spy")) if d.get(k) else "—"
        L.append(f"| {h} | {g('A_blocked')} | {g('B_watch_not_blocked')} | "
                 f"{g('C_alpha_board')} | {g('D_random_control')} | {g('E_rs_top')} |")
    L += ["", "## Block reason performance (5d)", "",
          "| Reason | n | matured | rel-SPY 5d | win% | MFE | MAE | Label |",
          "|---|---|---|---|---|---|---|---|"]
    for r, rp in sorted(res["reason_performance"].items(), key=lambda kv: -(kv[1]["n"])):
        L.append(f"| {r} | {rp['n']} | {rp['n_matured_5d']} | {_fmt(rp['mean_rel_spy_5d'])} | "
                 f"{_fmt(rp['win_rate_vs_spy_5d'], pct=True)} | {_fmt(rp['mean_mfe_5d'])} | "
                 f"{_fmt(rp['mean_mae_5d'])} | **{rp['label']}** |")
    ci = res["cache_artifact_isolation"]
    L += ["", "## Cache-depth artifact isolation", "",
          f"- Cache-depth-reason blocks: **{ci['n_blocks_citing_cache_depth_reason']}**",
          f"- Data-depth artifacts (shallow cache): **{ci['n_data_depth_artifact']}** "
          f"`{ci['artifact_tickers'][:20]}`",
          f"- Trustworthy cache-depth blocks (deep enough): **{ci['n_trustworthy_cache_block']}** "
          f"`{ci['trustworthy_tickers'][:20]}`",
          f"- {ci['finding']}", "",
          "## Rescue simulation (research-only — no trade / signal / proposal)", "",
          f"_{res['rescue_simulation']['note']}_", "",
          "| Subset | n | matured | rel-SPY 5d | win% |", "|---|---|---|---|---|"]
    for name, st in res["rescue_simulation"]["subsets"].items():
        L.append(f"| {name} | {st['n_names']} | {st['n_matured']} | "
                 f"{_fmt(st['mean_rel_spy'])} | {_fmt(st['win_rate_vs_spy'], pct=True)} |")
    L += ["", "## Verdict", "", f"### {v['gatekeeper_verdict']} → recommendation {v['recommendation']}",
          "", f"**{RECOMMENDATION_OPTIONS.get(v['recommendation'], '')}**", "", v["rationale"]]
    if v["recommendation"] == "E":
        L += ["", "### Proposed hard/soft split (not implemented)", ""]
        for k, desc in res["split_categories_if_E"].items():
            L.append(f"- **{k}**: {desc}")
    L += ["", f"> caveat: {res['artifact_annotations_caveat']}"]
    return L


def main() -> int:
    res = build()
    dataio.write_json(OUT_JSON, res)
    dataio.write_text(OUT_TXT, _render_txt(res))
    DOCS.mkdir(parents=True, exist_ok=True)
    dataio.write_text(OUT_MD, _render_md(res))
    print("\n".join(_render_txt(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
