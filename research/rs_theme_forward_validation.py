"""
research/rs_theme_forward_validation.py — Phase 1G.11.

Forward-validates the Phase 1G.10 RS/Theme Lens-Gatekeeper cohort OUT-OF-SAMPLE:
each cohort name is scored only once enough forward bars have matured, using bars
strictly AFTER the cohort as-of date. It asks whether the RS/theme LENS_READY
names actually perform better than the BLOCKED names, the Alpha board, random
liquid controls, a simple RS-top baseline, and the SPY/QQQ benchmarks — and
whether the Gatekeeper's blocks and the options-quality labels added precision.

This is a RESEARCH GATE only. It never promotes the surface to a paper strategy,
never registers a strategy, never emits paper signals or trade proposals, never
mutates historical evidence, and makes no provider calls. It reads the frozen
immutable cohort (cache/research/rs_theme_lens_ready_cohort_1g10.json) plus the
cache-only price parquets and the current Alpha board artifact.

Forward windows: 1d / 3d / 5d / 10d / 20d (each only when matured). Immature
windows are excluded from aggregates and counted explicitly.

Per matured name: forward return, return relative to SPY/QQQ, max favorable /
adverse excursion, drawdown from as-of price, became-too-extended, offered
pullback/reclaim, plus best-effort (point-in-time-limited) Gatekeeper / options
annotations.

Verdicts: NEED_MORE_DATA / NO_VALUE / PROMISING_BUT_UNPROVEN /
FORWARD_EDGE_DETECTED / READY_TO_ROUTE_TO_LENS_DAILY.

Outputs:
  cache/research/rs_theme_lens_ready_cohort_1g10.json   (Task 1, immutable freeze)
  logs/rs_theme_lens_ready_cohort_1g10.txt
  cache/research/rs_theme_forward_validation_latest.json
  logs/rs_theme_forward_validation_latest.txt
  docs/research/RS_THEME_FORWARD_VALIDATION.md

CACHE-ONLY / RESEARCH-ONLY. No provider calls, no DB writes, no signals.
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.rs_recall_lane import EXTENDED_MA20, _aligned
from research.scanner_truth import dataio
from research.scanner_truth.filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL,
                                            UNIV_MIN_AVG_VOL, UNIV_MIN_PRICE, sma)

# ── paths / constants ──────────────────────────────────────────────────────────
RC = dataio.RESEARCH_CACHE
LOGS = dataio.LOGS_DIR
DOCS = dataio.REPO / "docs" / "research"

TRIAGE_LATEST = RC / "rs_theme_lens_triage_latest.json"
COHORT_PATH = RC / "rs_theme_lens_ready_cohort_1g10.json"
COHORT_TXT = LOGS / "rs_theme_lens_ready_cohort_1g10.txt"
ALPHA_BOARD = RC / "alpha_discovery_board_latest.json"

OUT_JSON = RC / "rs_theme_forward_validation_latest.json"
OUT_TXT = LOGS / "rs_theme_forward_validation_latest.txt"
OUT_MD = DOCS / "RS_THEME_FORWARD_VALIDATION.md"

HORIZONS = (1, 3, 5, 10, 20)
PRIMARY_HORIZON = 5
MIN_MATURED_FOR_VERDICT = 20    # min matured LENS_READY names before a real verdict
RANDOM_SEED = 1011              # deterministic controls (Phase 1G.11)

COHORT_TAG = "1G10"

DISCLAIMER = (
    "RESEARCH-ONLY forward-validation gate. Routing/measurement only — NOT "
    "buy/sell/trade signals, NOT paper signals, NOT trade proposals. Does NOT "
    "promote any sleeve, register a strategy, modify core/universe.py, strategy "
    "gates, execution, governance, or live capital, and makes no provider calls."
)
ARTIFACT_CAVEAT = (
    "Alpha-board / options-quality annotations use CURRENT artifacts, not "
    "point-in-time snapshots (no board history exists at cohort as-of) — treat as "
    "best-effort context, not as-of truth.")


# ── Task 1: immutable cohort freeze ─────────────────────────────────────────────

def freeze_cohort(force: bool = False) -> Dict:
    """Freeze the current triage candidates into an immutable cohort checkpoint.

    Write-once: refuses to overwrite an existing cohort artifact unless ``force``
    is passed (operator escape hatch only). Future triage reruns therefore cannot
    silently clobber the checkpoint the forward validation is measured against.
    """
    if COHORT_PATH.exists() and not force:
        existing = json.loads(COHORT_PATH.read_text())
        return {"status": "already_frozen", "path": dataio.rel_to_repo(COHORT_PATH),
                "frozen_at": existing.get("frozen_at"),
                "asof_date": existing.get("asof_date"),
                "n": len(existing.get("cohort", []))}
    if not TRIAGE_LATEST.exists():
        return {"status": "error", "reason": f"missing {dataio.rel_to_repo(TRIAGE_LATEST)}"}

    tri = json.loads(TRIAGE_LATEST.read_text())
    cands = tri.get("candidates", [])
    cohort = [{
        "ticker": c.get("ticker"),
        "triage_label": c.get("triage_label"),
        "source": c.get("source"),
        "sources": c.get("sources"),
        "stage_label": c.get("stage_label"),
        "theme": c.get("theme"),
        "theme_state": c.get("theme_state"),
        "early_leader_score": c.get("early_leader_score"),
        "rs_score": c.get("rs_score"),
        "lens_state": c.get("lens_state"),
        "lens_age_hours": c.get("lens_age_hours"),
        "gatekeeper_status": c.get("gatekeeper_status"),
        "options_quality": c.get("options_quality"),
        "extension_state": c.get("extension_state"),
        "pullback_potential": c.get("pullback_potential"),
        "gate_root_cause": c.get("gate_root_cause"),
        "gate_rejection_reasons": c.get("gate_rejection_reasons"),
        "price_asof": c.get("price"),
    } for c in cands]

    from collections import Counter
    label_dist = dict(Counter(c["triage_label"] for c in cohort))
    payload = {
        "cohort_tag": COHORT_TAG,
        "phase": "1G.11 (freeze of 1G.10 surface)",
        "immutable": True,
        "immutability_note": (
            "Write-once research checkpoint. freeze_cohort() refuses to overwrite "
            "this file; future rs-theme-triage reruns do not touch it. Re-freeze "
            "only via explicit --force (operator escape hatch)."),
        "disclaimer": DISCLAIMER,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "asof_date": tri.get("asof_date"),
        "triage_generated_at": tri.get("generated_at"),
        "market_regime": tri.get("market_regime"),
        "label_distribution": label_dist,
        "cohort": cohort,
    }
    dataio.write_json(COHORT_PATH, payload)
    dataio.write_text(COHORT_TXT, _render_cohort_txt(payload))
    return {"status": "frozen", "path": dataio.rel_to_repo(COHORT_PATH),
            "frozen_at": payload["frozen_at"], "asof_date": payload["asof_date"],
            "n": len(cohort), "label_distribution": label_dist}


def _render_cohort_txt(p: Dict) -> List[str]:
    L = [f"== RS/THEME LENS-READY COHORT {p['cohort_tag']} (IMMUTABLE FREEZE) ==",
         p["disclaimer"], "",
         f"frozen_at : {p['frozen_at']}",
         f"asof_date : {p['asof_date']}   (triage gen {p['triage_generated_at']})",
         f"labels    : {p['label_distribution']}", ""]
    L.append(f"{'ticker':7}{'label':18}{'src':12}{'stage':20}{'lens':28}{'gk':8}{'opts':10}{'price':>8}")
    for c in sorted(p["cohort"], key=lambda x: (x["triage_label"], -(x.get("early_leader_score") or 0))):
        L.append(f"{c['ticker'] or '?':7}{(c['triage_label'] or '')[:17]:18}"
                 f"{(c['source'] or '')[:11]:12}{(c['stage_label'] or '')[:19]:20}"
                 f"{(c['lens_state'] or '—')[:27]:28}{(c['gatekeeper_status'] or '—'):8}"
                 f"{str(c['options_quality'] or '—'):10}{(c['price_asof'] or 0):>8.2f}")
    return L


def _load_cohort() -> Optional[Dict]:
    if not COHORT_PATH.exists():
        return None
    return json.loads(COHORT_PATH.read_text())


# ── forward metric primitives ───────────────────────────────────────────────────

def _cal_index(cal: pd.DatetimeIndex, asof: str) -> Optional[int]:
    ts = pd.Timestamp(asof)
    locs = np.where(cal <= ts)[0]
    return int(locs[-1]) if len(locs) else None


def _is_matured(cal: pd.DatetimeIndex, i: Optional[int], h: int) -> bool:
    return i is not None and (i + h) < len(cal)


def _fwd_metrics(t: str, cal, i: int, h: int, spy: pd.Series, qqq: pd.Series) -> Optional[Dict]:
    """Forward metrics for ticker `t` over `h` bars after as-of index `i`.
    Returns None if not matured (cal lacks i+h) or price data missing."""
    if not _is_matured(cal, i, h):
        return None
    df = dataio.load_prices(t)
    if df is None:
        return None
    c = _aligned(df, cal)
    p0 = c.iloc[i]
    pf = c.iloc[i + h]
    if pd.isna(p0) or pd.isna(pf) or p0 <= 0:
        return None
    fwd = float(pf / p0 - 1.0)
    window = c.iloc[i:i + h + 1]
    mfe = float(np.nanmax(window.values) / p0 - 1.0)
    mae = float(np.nanmin(window.values) / p0 - 1.0)
    spy_ret = float(spy.iloc[i + h] / spy.iloc[i] - 1.0) if spy.iloc[i] else None
    qqq_ret = float(qqq.iloc[i + h] / qqq.iloc[i] - 1.0) if qqq.iloc[i] else None
    rel_spy = (fwd - spy_ret) if spy_ret is not None else None
    rel_qqq = (fwd - qqq_ret) if qqq_ret is not None else None
    ext_series = []
    for j in range(i, i + h + 1):
        ma20 = float(sma(c.iloc[:j + 1], 20).iloc[-1]) if j >= 20 else np.nan
        if ma20 and not np.isnan(ma20):
            ext_series.append((float(c.iloc[j]) - ma20) / ma20)
    became_extended = any(e > EXTENDED_MA20 for e in ext_series) if ext_series else None
    offered_pullback = any(e <= 0.0 for e in ext_series) if ext_series else None
    return {
        "fwd_return": round(fwd, 4),
        "rel_return_spy": round(rel_spy, 4) if rel_spy is not None else None,
        "rel_return_qqq": round(rel_qqq, 4) if rel_qqq is not None else None,
        "mfe": round(mfe, 4), "mae": round(mae, 4),
        "drawdown_from_asof": round(mae, 4),
        "became_too_extended": became_extended,
        "offered_pullback_reclaim": offered_pullback,
    }


def _cohort_stats(tickers: List[str], cal, i: int, h: int,
                  spy: pd.Series, qqq: pd.Series) -> Dict:
    rels, fwds, maes, ext, pb = [], [], [], [], []
    matured = 0
    for t in tickers:
        m = _fwd_metrics(t, cal, i, h, spy, qqq)
        if not m:
            continue
        matured += 1
        fwds.append(m["fwd_return"])
        maes.append(m["mae"])
        if m["rel_return_spy"] is not None:
            rels.append(m["rel_return_spy"])
        if m["became_too_extended"] is not None:
            ext.append(m["became_too_extended"])
        if m["offered_pullback_reclaim"] is not None:
            pb.append(m["offered_pullback_reclaim"])
    return {
        "n_names": len(tickers),
        "n_matured": matured,
        "mean_fwd": round(mean(fwds), 4) if fwds else None,
        "mean_rel_spy": round(mean(rels), 4) if rels else None,
        "median_rel_spy": round(median(rels), 4) if rels else None,
        "mean_mae": round(mean(maes), 4) if maes else None,
        "win_rate_vs_spy": round(100.0 * sum(1 for r in rels if r > 0) / len(rels), 1) if rels else None,
        "pct_became_extended": round(100.0 * sum(ext) / len(ext), 1) if ext else None,
        "pct_offered_pullback": round(100.0 * sum(pb) / len(pb), 1) if pb else None,
    }


def _liquid_universe_at(cal, i: int) -> List[str]:
    """Liquid universe as-of index i (cache-only, point-in-time)."""
    out = []
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = dataio.load_prices(t)
        if df is None:
            continue
        c = _aligned(df, cal)
        if i >= len(c) or i < 20 or pd.isna(c.iloc[i]):
            continue
        price = float(c.iloc[i])
        vol = df["volume"].reindex(cal).ffill()
        avgvol = float(vol.iloc[i - 19:i + 1].mean())
        avgdvol = float((c * vol).iloc[i - 19:i + 1].mean())
        if (UNIV_MIN_PRICE <= price <= UNIV_MAX_PRICE
                and avgvol >= UNIV_MIN_AVG_VOL and avgdvol >= UNIV_MIN_AVG_DVOL):
            out.append(t)
    return out


def _rs_top_at(cal, i: int, liquid: List[str], n: int) -> List[str]:
    """Simple RS-top baseline: top-`n` liquid names by 20d return relative to SPY
    as-of i (point-in-time, no look-ahead)."""
    if i - 20 < 0:
        return []
    spy = _aligned(dataio.load_prices("SPY"), cal)
    spy_ret = float(spy.iloc[i] / spy.iloc[i - 20] - 1.0) if spy.iloc[i - 20] else 0.0
    scored = []
    for t in liquid:
        c = _aligned(dataio.load_prices(t), cal)
        if pd.isna(c.iloc[i - 20]) or c.iloc[i - 20] <= 0:
            continue
        rs = float(c.iloc[i] / c.iloc[i - 20] - 1.0) - spy_ret
        scored.append((rs, t))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:n]]


def _alpha_board_tickers() -> List[str]:
    try:
        b = json.loads(ALPHA_BOARD.read_text())
        return [(i.get("ticker") or i.get("symbol") or "").upper()
                for i in b.get("items", []) if (i.get("ticker") or i.get("symbol"))]
    except Exception:
        return []


# ── per-horizon comparison across cohorts ───────────────────────────────────────

def _build_by_horizon(cohort: Dict, cal, spy, qqq, rng) -> Tuple[Dict, Dict]:
    asof = cohort["asof_date"]
    i = _cal_index(cal, asof)
    rows = cohort["cohort"]
    lens_ready = [r["ticker"] for r in rows if r["triage_label"] == "LENS_READY"]
    blocked = [r["ticker"] for r in rows if r["triage_label"] == "BLOCKED"]
    board = [t for t in _alpha_board_tickers() if t not in dataio.BENCHMARKS]
    liquid = _liquid_universe_at(cal, i) if i is not None else []
    n_ctrl = max(len(lens_ready), 1)
    rand_ctrl = rng.sample(liquid, min(n_ctrl, len(liquid))) if liquid else []
    rs_top = _rs_top_at(cal, i, liquid, n_ctrl) if liquid else []

    by_h: Dict[str, Dict] = {}
    maturity: Dict[str, bool] = {}
    for h in HORIZONS:
        matured = _is_matured(cal, i, h)
        maturity[f"{h}d"] = matured
        spy_ret = (float(spy.iloc[i + h] / spy.iloc[i] - 1.0)
                   if matured and i is not None and spy.iloc[i] else None)
        qqq_ret = (float(qqq.iloc[i + h] / qqq.iloc[i] - 1.0)
                   if matured and i is not None and qqq.iloc[i] else None)
        by_h[f"{h}d"] = {
            "matured": matured,
            "A_lens_ready": _cohort_stats(lens_ready, cal, i, h, spy, qqq) if matured else None,
            "B_blocked": _cohort_stats(blocked, cal, i, h, spy, qqq) if matured else None,
            "C_alpha_board": _cohort_stats(board, cal, i, h, spy, qqq) if matured else None,
            "D_random_control": _cohort_stats(rand_ctrl, cal, i, h, spy, qqq) if matured else None,
            "E_rs_top": _cohort_stats(rs_top, cal, i, h, spy, qqq) if matured else None,
            "spy_return": round(spy_ret, 4) if spy_ret is not None else None,
            "qqq_return": round(qqq_ret, 4) if qqq_ret is not None else None,
        }
    cohorts_used = {
        "lens_ready": lens_ready, "blocked": blocked,
        "alpha_board": board, "random_control": rand_ctrl, "rs_top": rs_top,
        "asof_index": i, "liquid_universe_size": len(liquid),
    }
    return by_h, cohorts_used


# ── Task 4: Gatekeeper precision audit ──────────────────────────────────────────

def _gatekeeper_precision(cohort: Dict, by_h: Dict) -> Dict:
    rows = cohort["cohort"]
    blocked = [r for r in rows if r["triage_label"] == "BLOCKED"]
    from collections import Counter
    cause = Counter(r.get("gate_root_cause") or "unknown" for r in blocked)
    # map cohort gate_root_cause vocabulary → audit buckets
    bucket = {"real_quality": "real_quality_rejection",
              "gate_design_mismatch": "gate_design_mismatch",
              "cache_artifact": "data_cache_artifact",
              "passes_a_gate": "passes_a_gate"}
    cause_buckets = Counter()
    for r in blocked:
        cause_buckets[bucket.get(r.get("gate_root_cause"), "unknown")] += 1
    ph = by_h.get(f"{PRIMARY_HORIZON}d", {})
    a = ph.get("A_lens_ready") or {}
    b = ph.get("B_blocked") or {}
    matured = ph.get("matured", False)
    blocked_rel = b.get("mean_rel_spy")
    lens_rel = a.get("mean_rel_spy")
    blocked_underperforms = (
        blocked_rel is not None and lens_rel is not None and blocked_rel < lens_rel)
    higher_adverse = (
        b.get("mean_mae") is not None and a.get("mean_mae") is not None
        and b.get("mean_mae") < a.get("mean_mae"))  # more negative MAE = worse
    if not matured or b.get("n_matured", 0) == 0:
        precision_verdict = "NEED_MORE_DATA"
        precision_note = ("No matured forward window for the BLOCKED cohort yet — "
                          "cannot yet judge whether blocks add precision or reject winners.")
    elif blocked_underperforms and (blocked_rel is None or blocked_rel <= 0):
        precision_verdict = "BLOCKS_LOOK_CORRECT"
        precision_note = ("Blocked names underperform LENS_READY and do not beat SPY — "
                          "the Gatekeeper appears to be adding precision, not rejecting winners.")
    elif blocked_rel is not None and lens_rel is not None and blocked_rel > lens_rel and blocked_rel > 0:
        precision_verdict = "BLOCKS_MAY_REJECT_WINNERS"
        precision_note = ("Blocked names are outrunning LENS_READY and beating SPY — early "
                          "sign the gates may be rejecting future winners; keep watching.")
    else:
        precision_verdict = "INCONCLUSIVE"
        precision_note = "Blocked vs LENS_READY forward spread is not yet decisive."
    return {
        "n_blocked": len(blocked),
        "block_reason_root_cause": dict(cause),
        "block_reason_buckets": dict(cause_buckets),
        "blocked_names": [r["ticker"] for r in blocked],
        "blocked_underperforms_lens_ready": blocked_underperforms if matured else None,
        "blocked_higher_adverse_excursion": higher_adverse if matured else None,
        "blocked_mean_rel_spy": blocked_rel,
        "lens_ready_mean_rel_spy": lens_rel,
        "precision_verdict": precision_verdict,
        "precision_note": precision_note,
    }


# ── Task 5: options quality audit ───────────────────────────────────────────────

def _options_quality_audit(cohort: Dict, cal, spy, qqq) -> Dict:
    asof = cohort["asof_date"]
    i = _cal_index(cal, asof)
    h = PRIMARY_HORIZON
    by_label: Dict[str, List[float]] = {}
    for r in cohort["cohort"]:
        oq = r.get("options_quality")
        key = str(oq) if oq is not None else "none"
        m = _fwd_metrics(r["ticker"], cal, i, h, spy, qqq) if i is not None else None
        if m and m["fwd_return"] is not None:
            by_label.setdefault(key, []).append(m["fwd_return"])
    summary = {k: {"n_matured": len(v),
                   "mean_fwd": round(mean(v), 4) if v else None}
               for k, v in by_label.items()}
    n_with_opts = sum(1 for r in cohort["cohort"]
                      if r.get("options_quality") not in (None, "none"))
    matured_any = any(v["n_matured"] > 0 for v in summary.values())
    if not matured_any:
        finding = ("NEED_MORE_DATA — no matured forward window yet, so options-quality "
                   "labels cannot be tied to forward outcomes. No options P/L is modeled "
                   "(no point-in-time chain history exists).")
    else:
        finding = ("Comparing mean forward return by options-quality label (see by_label). "
                   "No options P/L is modeled — labels are evaluated only as a quality "
                   "filter on the underlying's forward move.")
    return {
        "n_candidates_with_options_label": n_with_opts,
        "by_label": summary,
        "p_l_modeled": False,
        "finding": finding,
        "caveat": ARTIFACT_CAVEAT,
    }


# ── Task 3: comparison questions + verdict ───────────────────────────────────────

def _comparison_questions(by_h: Dict, gk: Dict) -> Dict:
    ph = by_h.get(f"{PRIMARY_HORIZON}d", {})
    if not ph.get("matured"):
        na = "NEED_MORE_DATA — primary horizon immature"
        return {
            "primary_horizon_matured": False,
            "q1_lens_ready_vs_blocked": na,
            "q2_lens_ready_vs_alpha_board": na,
            "q3_lens_ready_vs_random": na,
            "q4_blocked_correctly_filtered": na,
            "q5_too_extended_pct": None,
            "q6_offered_pullback_pct": None,
        }

    def rel(key):
        return (ph.get(key) or {}).get("mean_rel_spy")
    a, b, c = rel("A_lens_ready"), rel("B_blocked"), rel("C_alpha_board")
    d, e = rel("D_random_control"), rel("E_rs_top")
    A = ph.get("A_lens_ready") or {}

    def cmp(x, y, better="outperform", worse="underperform"):
        if x is None or y is None:
            return "NEED_MORE_DATA"
        return better if x > y else (worse if x < y else "tie")
    return {
        "primary_horizon_matured": True,
        "q1_lens_ready_vs_blocked": cmp(a, b),
        "q2_lens_ready_vs_alpha_board": cmp(a, c),
        "q3_lens_ready_vs_random": cmp(a, d),
        "q4_blocked_correctly_filtered": gk.get("precision_verdict"),
        "q5_too_extended_pct": A.get("pct_became_extended"),
        "q6_offered_pullback_pct": A.get("pct_offered_pullback"),
        "values": {"lens_ready_rel_spy": a, "blocked_rel_spy": b,
                   "alpha_board_rel_spy": c, "random_rel_spy": d, "rs_top_rel_spy": e},
    }


def _verdict(by_h: Dict, cohort: Dict) -> Tuple[str, str]:
    ph = by_h.get(f"{PRIMARY_HORIZON}d", {})
    if not ph.get("matured"):
        return "NEED_MORE_DATA", (
            f"Cohort as-of {cohort['asof_date']} has not matured to the "
            f"{PRIMARY_HORIZON}d horizon yet — no forward bars to score. Re-run "
            "after sufficient forward sessions complete.")
    a = ph.get("A_lens_ready") or {}
    n_mat = a.get("n_matured", 0)
    rs_rel = a.get("mean_rel_spy")
    rand_rel = (ph.get("D_random_control") or {}).get("mean_rel_spy")
    board_rel = (ph.get("C_alpha_board") or {}).get("mean_rel_spy")
    if n_mat < MIN_MATURED_FOR_VERDICT or rs_rel is None:
        return "NEED_MORE_DATA", (
            f"Only {n_mat} matured LENS_READY name(s) at {PRIMARY_HORIZON}d — below the "
            f"{MIN_MATURED_FOR_VERDICT}-name floor for a verdict.")
    if rs_rel <= 0:
        return "NO_VALUE", (
            f"LENS_READY {PRIMARY_HORIZON}d mean excess vs SPY {rs_rel:+.4f} ≤ 0 — no "
            "forward edge over the benchmark.")
    beats_rand = rand_rel is None or rs_rel > rand_rel
    beats_board = board_rel is None or rs_rel >= board_rel
    if not beats_rand:
        return "NO_VALUE", (
            f"LENS_READY ({rs_rel:+.4f}) does not beat random control ({rand_rel:+.4f}) "
            f"at {PRIMARY_HORIZON}d — positive but no selection edge.")
    if beats_rand and beats_board:
        return "FORWARD_EDGE_DETECTED", (
            f"LENS_READY {PRIMARY_HORIZON}d excess {rs_rel:+.4f} beats random "
            f"({rand_rel}) and is at least on par with the Alpha board ({board_rel}) "
            "over an adequate matured sample. Eligible to consider routing to Lens "
            "daily (NOT execution) pending sustained confirmation.")
    return "PROMISING_BUT_UNPROVEN", (
        f"LENS_READY excess {rs_rel:+.4f} beats random but not the Alpha board — a "
        "positive but not yet decisive forward edge; keep accumulating sample.")


# ── Task 6: daily maintenance recommendation ─────────────────────────────────────

def _maintenance_recommendation(verdict: str, by_h: Dict, cohort: Dict) -> Dict:
    options = [
        {"id": "A", "label": "Keep manual/adhoc RS-theme triage only",
         "evidence": "Default until a forward edge is proven on a matured sample.",
         "provider_cost": "none (operator-invoked, cache-first triage)",
         "false_positive_risk": "low — nothing is automated",
         "overfitting_risk": "low — no recurring fit to noise",
         "expected_benefit": "low — surface stays available but unmonitored between runs"},
        {"id": "B", "label": "Run RS/theme triage nightly (cache-only)",
         "evidence": "Triage itself is cache-first; nightly cadence builds the forward "
                     "ledger needed to ever reach a verdict.",
         "provider_cost": "negligible — triage is cache-only; only the existing nightly "
                          "lens/gatekeeper cadence touches providers",
         "false_positive_risk": "low — labels only, no routing into any board",
         "overfitting_risk": "low — measurement only",
         "expected_benefit": "medium — accumulates the matured sample that every verdict needs"},
        {"id": "C", "label": "Run RS/theme triage + Lens refresh for top 20 nightly",
         "evidence": "1G.10 showed lens/gatekeeper gaps close quickly; nightly refresh keeps "
                     "the cohort fresh for forward scoring.",
         "provider_cost": "~20 stock-lens builds/night (provider calls per ticker) — non-trivial",
         "false_positive_risk": "medium — fresh constructive lenses may over-suggest names",
         "overfitting_risk": "medium — daily re-selection can chase recent movers",
         "expected_benefit": "medium-high IF a forward edge is later confirmed"},
        {"id": "D", "label": "Route RS/theme LENS_READY into the Alpha board as a separate research strip",
         "evidence": "Only justified once LENS_READY demonstrably ≥ Alpha board forward.",
         "provider_cost": "low incremental (reuses board enrichment)",
         "false_positive_risk": "high — a visible board strip invites action on unproven names",
         "overfitting_risk": "medium-high — couples a research surface to the operator board",
         "expected_benefit": "high only after FORWARD_EDGE_DETECTED is sustained"},
        {"id": "E", "label": "Keep production unchanged",
         "evidence": "Always valid — production universe/gates/execution stay untouched regardless.",
         "provider_cost": "none",
         "false_positive_risk": "none",
         "overfitting_risk": "none",
         "expected_benefit": "baseline safety"},
    ]
    if verdict in ("NEED_MORE_DATA",):
        primary = "B"
        rationale = ("Sample is immature — the only justified step is the cache-only nightly "
                     "triage (B) to grow the forward ledger, while production stays unchanged (E). "
                     "Do NOT adopt C/D until a matured sample yields FORWARD_EDGE_DETECTED.")
    elif verdict in ("NO_VALUE",):
        primary = "A"
        rationale = ("No forward edge detected — keep it adhoc (A) / production unchanged (E); "
                     "do not invest provider budget in nightly lens refresh (C) or board routing (D).")
    elif verdict == "PROMISING_BUT_UNPROVEN":
        primary = "B"
        rationale = ("Positive but not decisive — continue nightly cache-only triage (B) to confirm; "
                     "defer C/D until the edge holds over more matured windows.")
    elif verdict == "FORWARD_EDGE_DETECTED":
        primary = "C"
        rationale = ("Forward edge detected — nightly triage + top-20 lens refresh (C) is justified "
                     "to keep the cohort fresh; consider D only after the edge is sustained.")
    else:  # READY_TO_ROUTE_TO_LENS_DAILY (not reachable from _verdict yet)
        primary = "D"
        rationale = ("Edge sustained — routing LENS_READY into a separate research strip (D) is "
                     "defensible, still with NO execution/governance coupling.")
    return {"options": options, "primary_recommendation": primary,
            "rationale": rationale,
            "note": "PROPOSED ONLY — not implemented. No cadence/wiring changed by this report."}


# ── orchestration ────────────────────────────────────────────────────────────────

def build() -> Dict:
    cohort = _load_cohort()
    if cohort is None:
        return {"generated_at": datetime.now(timezone.utc).isoformat(),
                "disclaimer": DISCLAIMER, "error": "cohort_not_frozen",
                "reason": (f"missing {dataio.rel_to_repo(COHORT_PATH)} — run "
                           "`rs_theme_forward_validation.py --freeze-cohort` first."),
                "verdict": "NEED_MORE_DATA"}
    cal = dataio.benchmark_calendar()
    spy = _aligned(dataio.load_prices("SPY"), cal)
    qqq = _aligned(dataio.load_prices("QQQ"), cal)
    rng = random.Random(RANDOM_SEED)

    by_h, cohorts_used = _build_by_horizon(cohort, cal, spy, qqq, rng)
    gk = _gatekeeper_precision(cohort, by_h)
    opts = _options_quality_audit(cohort, cal, spy, qqq)
    questions = _comparison_questions(by_h, gk)
    verdict, reason = _verdict(by_h, cohort)
    maint = _maintenance_recommendation(verdict, by_h, cohort)

    asof_i = cohorts_used["asof_index"]
    today_i = len(cal) - 1
    forward_sessions = (today_i - asof_i) if asof_i is not None else None
    matured_horizons = [h for h in (f"{x}d" for x in HORIZONS) if by_h[h]["matured"]]
    primary = by_h.get(f"{PRIMARY_HORIZON}d", {}).get("A_lens_ready") or {}
    n_matured_primary = primary.get("n_matured", 0) if by_h.get(f"{PRIMARY_HORIZON}d", {}).get("matured") else 0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "1G.11",
        "disclaimer": DISCLAIMER,
        "cohort_tag": cohort.get("cohort_tag"),
        "cohort_path": dataio.rel_to_repo(COHORT_PATH),
        "cohort_asof": cohort.get("asof_date"),
        "cohort_frozen_at": cohort.get("frozen_at"),
        "label_distribution": cohort.get("label_distribution"),
        "primary_horizon": f"{PRIMARY_HORIZON}d",
        "min_matured_for_verdict": MIN_MATURED_FOR_VERDICT,
        "forward_sessions_elapsed": forward_sessions,
        "matured_horizons": matured_horizons,
        "n_matured_lens_ready_primary": n_matured_primary,
        "cohorts_used": {k: (v if not isinstance(v, list) else v)
                         for k, v in cohorts_used.items()},
        "by_horizon": by_h,
        "comparison": questions,
        "gatekeeper_precision_audit": gk,
        "options_quality_audit": opts,
        "maintenance_recommendation": maint,
        "verdict": verdict,
        "verdict_reason": reason,
        "artifact_annotations_caveat": ARTIFACT_CAVEAT,
    }


# ── MCP / dashboard cache-only summary ───────────────────────────────────────────

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
    a = ph.get("A_lens_ready") or {}
    vals = (res.get("comparison") or {}).get("values") or {}
    return {
        "present": True,
        "cohort": res.get("cohort_tag"),
        "matured": res.get("n_matured_lens_ready_primary", 0),
        "lens_ready_5d_rel_spy": a.get("mean_rel_spy"),
        "vs_alpha": vals.get("alpha_board_rel_spy"),
        "vs_random": vals.get("random_rel_spy"),
        "verdict": res.get("verdict"),
    }


# ── renderers ────────────────────────────────────────────────────────────────────

def _fmt(v, pct=False, plus=True) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        if pct:
            return f"{v:.1f}%"
        return f"{v:+.4f}" if plus else f"{v:.4f}"
    return str(v)


def _render_txt(res: Dict) -> List[str]:
    if res.get("error"):
        return [f"== RS/THEME FORWARD VALIDATION ({res['generated_at']}) ==",
                res["disclaimer"], "", f"ERROR: {res['error']} — {res.get('reason')}"]
    L = [f"== RS/THEME FORWARD VALIDATION — cohort {res['cohort_tag']} ({res['generated_at']}) ==",
         res["disclaimer"], "",
         f"cohort asof  : {res['cohort_asof']}  (frozen {res['cohort_frozen_at']})",
         f"labels       : {res['label_distribution']}",
         f"fwd sessions : {res['forward_sessions_elapsed']}  matured horizons: {res['matured_horizons'] or 'none'}",
         f"primary {res['primary_horizon']} matured LENS_READY: {res['n_matured_lens_ready_primary']} "
         f"(verdict floor {res['min_matured_for_verdict']})",
         "",
         f"{'horizon':<8}{'mat':>4}{'A:lens':>9}{'B:blkd':>9}{'C:alpha':>9}{'D:rand':>9}{'E:rstop':>9}  (mean rel SPY)"]
    for h, d in res["by_horizon"].items():
        def g(k):
            return _fmt((d.get(k) or {}).get("mean_rel_spy")) if d.get(k) else "—"
        L.append(f"{h:<8}{('Y' if d['matured'] else 'n'):>4}{g('A_lens_ready'):>9}"
                 f"{g('B_blocked'):>9}{g('C_alpha_board'):>9}{g('D_random_control'):>9}{g('E_rs_top'):>9}")
    q = res["comparison"]
    L += ["", "COMPARISON (primary horizon):",
          f"  1. LENS_READY vs BLOCKED  : {q.get('q1_lens_ready_vs_blocked')}",
          f"  2. LENS_READY vs ALPHA    : {q.get('q2_lens_ready_vs_alpha_board')}",
          f"  3. LENS_READY vs RANDOM   : {q.get('q3_lens_ready_vs_random')}",
          f"  4. BLOCKED filtered well? : {q.get('q4_blocked_correctly_filtered')}",
          f"  5. became too extended    : {_fmt(q.get('q5_too_extended_pct'), pct=True)}",
          f"  6. offered pullback/reclaim: {_fmt(q.get('q6_offered_pullback_pct'), pct=True)}"]
    gk = res["gatekeeper_precision_audit"]
    L += ["", f"GATEKEEPER PRECISION: {gk['precision_verdict']}",
          f"  blocked={gk['n_blocked']} reasons={gk['block_reason_buckets']}",
          f"  {gk['precision_note']}"]
    opt = res["options_quality_audit"]
    L += ["", f"OPTIONS QUALITY: {opt['n_candidates_with_options_label']} labelled · P/L modeled={opt['p_l_modeled']}",
          f"  by_label={opt['by_label']}",
          f"  {opt['finding']}"]
    mr = res["maintenance_recommendation"]
    L += ["", f"MAINTENANCE RECOMMENDATION (proposed only): primary={mr['primary_recommendation']}",
          f"  {mr['rationale']}"]
    L += ["", f"VERDICT: {res['verdict']}", "  " + res["verdict_reason"],
          "", "caveat: " + res["artifact_annotations_caveat"]]
    return L


def _render_md(res: Dict) -> List[str]:
    if res.get("error"):
        return [f"# RS/Theme Forward Validation — cohort {COHORT_TAG}", "",
                f"> {res['disclaimer']}", "",
                f"**ERROR:** {res['error']} — {res.get('reason')}"]
    q = res["comparison"]
    gk = res["gatekeeper_precision_audit"]
    opt = res["options_quality_audit"]
    mr = res["maintenance_recommendation"]
    L = [f"# RS/Theme Forward Validation — cohort {res['cohort_tag']} (Phase 1G.11)", "",
         f"> {res['disclaimer']}", "",
         f"_Generated {res['generated_at']}; cohort as-of {res['cohort_asof']} "
         f"(frozen {res['cohort_frozen_at']})._", "",
         "## Status", "",
         f"- Forward sessions elapsed: **{res['forward_sessions_elapsed']}**",
         f"- Matured horizons: **{res['matured_horizons'] or 'none yet'}**",
         f"- Matured LENS_READY at {res['primary_horizon']}: "
         f"**{res['n_matured_lens_ready_primary']}** (verdict floor {res['min_matured_for_verdict']})",
         f"- Label distribution: `{res['label_distribution']}`", "",
         "## Forward returns by horizon (mean excess vs SPY)", "",
         "| Horizon | Matured | A LENS_READY | B BLOCKED | C Alpha board | D Random | E RS-top |",
         "|---|---|---|---|---|---|---|"]
    for h, d in res["by_horizon"].items():
        def g(k):
            return _fmt((d.get(k) or {}).get("mean_rel_spy")) if d.get(k) else "—"
        L.append(f"| {h} | {'Y' if d['matured'] else '—'} | {g('A_lens_ready')} | "
                 f"{g('B_blocked')} | {g('C_alpha_board')} | {g('D_random_control')} | {g('E_rs_top')} |")
    L += ["", "## Candidate-quality comparison (primary horizon)", "",
          f"1. **LENS_READY vs BLOCKED:** {q.get('q1_lens_ready_vs_blocked')}",
          f"2. **LENS_READY vs Alpha board:** {q.get('q2_lens_ready_vs_alpha_board')}",
          f"3. **LENS_READY vs random control:** {q.get('q3_lens_ready_vs_random')}",
          f"4. **Blocked correctly filtered?** {q.get('q4_blocked_correctly_filtered')}",
          f"5. **Became too extended:** {_fmt(q.get('q5_too_extended_pct'), pct=True)}",
          f"6. **Offered pullback/reclaim:** {_fmt(q.get('q6_offered_pullback_pct'), pct=True)}",
          "", "## Gatekeeper precision audit", "",
          f"- Verdict: **{gk['precision_verdict']}**",
          f"- Blocked names ({gk['n_blocked']}): `{gk['blocked_names']}`",
          f"- Root-cause buckets: `{gk['block_reason_buckets']}`",
          f"- {gk['precision_note']}",
          "", "## Options-quality audit", "",
          f"- Candidates with an options label: {opt['n_candidates_with_options_label']}",
          f"- Options P/L modeled: {opt['p_l_modeled']} (no point-in-time chain history)",
          f"- By label: `{opt['by_label']}`",
          f"- {opt['finding']}",
          "", "## Daily maintenance recommendation (proposed only — not implemented)", "",
          f"**Primary: option {mr['primary_recommendation']}.** {mr['rationale']}", "",
          "| Opt | Action | Provider cost | False-positive risk | Overfitting risk | Expected benefit |",
          "|---|---|---|---|---|---|"]
    for o in mr["options"]:
        L.append(f"| {o['id']} | {o['label']} | {o['provider_cost']} | "
                 f"{o['false_positive_risk']} | {o['overfitting_risk']} | {o['expected_benefit']} |")
    L += ["", "## Verdict", "", f"### {res['verdict']}", "", res["verdict_reason"],
          "", f"> caveat: {res['artifact_annotations_caveat']}"]
    return L


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1G.11 RS/theme forward validation (cache-only).")
    ap.add_argument("--freeze-cohort", action="store_true",
                    help="freeze the immutable 1G.10 cohort from the latest triage (write-once).")
    ap.add_argument("--force", action="store_true",
                    help="with --freeze-cohort: overwrite an existing cohort (operator escape hatch).")
    args = ap.parse_args()

    if args.freeze_cohort:
        r = freeze_cohort(force=args.force)
        print(json.dumps(r, indent=2, default=str))
        return 0

    res = build()
    dataio.write_json(OUT_JSON, res)
    dataio.write_text(OUT_TXT, _render_txt(res))
    DOCS.mkdir(parents=True, exist_ok=True)
    dataio.write_text(OUT_MD, _render_md(res))
    print("\n".join(_render_txt(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
