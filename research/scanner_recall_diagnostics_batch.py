"""
research/scanner_recall_diagnostics_batch.py — multi-as-of-date aggregate of
the scanner recall diagnostics (research/scanner_recall_diagnostics.py).

Runs the three-cohort comparison (strict production-mirror scanner vs
simple-RS baseline vs loosened scanner) across a GRID of historical as-of
dates (via --grid lookback list, default 12 dates: 20..75 trading days back)
in ONE pass over the price cache (each parquet loaded/aligned once), then
aggregates across dates:

  • cohort scoreboard — per date + cross-date medians; strict-vs-random,
    strict-vs-RS, loose-recall-vs-edge answered explicitly.
  • filter-level stability — per reject reason, per-date median/winsorized
    fwd20 of rejects vs SPY; a filter finding is only "trusted" with
    ≥ MIN_REJECTS_PER_DATE rejects on ≥ MIN_EVIDENCE_DATES dates.
  • filter-combination stats — named combos (too_extended+no_breakout, etc.)
    via reason-superset matching.
  • outlier control — winsorized means + medians everywhere; per-date
    top-rejected winners tracked so single names cannot drive conclusions.
  • sector split — healthcare/biotech vs non-healthcare rejects per filter,
    to detect sector-specific distortion.
  • decision section — filters_to_keep_strict / filters_to_investigate /
    filters_to_test_in_loose_experiment / filters_not_safe_to_loosen +
    evidence_strength ∈ {INSUFFICIENT, WEAK, MIXED, STRONG}.

GUARDRAILS: diagnostic study only. No scanner threshold is changed, no
filter is loosened, no ticker is promoted to a signal. The decision lists
are proposals for a SEPARATE gated experiment, not gate changes.

Outputs:
  cache/research/scanner_recall_diagnostics_batch_latest.json
  logs/scanner_recall_diagnostics_batch_latest.txt

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from research import scanner_recall_diagnostics as srd
from research.scanner_truth import dataio

ARTIFACT_VERSION = "srd-batch.1"
GRID_DEFAULT = (20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75)
MIN_REJECTS_PER_DATE = 5      # a date counts as filter evidence only at ≥ this n
MIN_EVIDENCE_DATES = 4        # a filter finding is trusted only at ≥ this many dates
TOP_REJECTED_PER_DATE = 10

# Named reject-reason combinations (lane-stripped; superset match).
NAMED_COMBOS: Tuple[Tuple[str, ...], ...] = (
    ("too_extended", "no_breakout"),
    ("below_ma200_floor",),
    ("ma50_not_rising",),
    ("dvol_fading",),
    ("below_ma200_floor", "ma50_not_rising"),
    ("dvol_fading", "no_breakout"),
)


def _is_healthcare(profiles: Dict, t: str) -> bool:
    p = profiles.get(t) or {}
    return (p.get("sector") == "Healthcare"
            or dataio.classify_theme(p) == "biotech_healthcare")


def _median(vals: List[float]) -> Optional[float]:
    return round(float(np.median(vals)), 4) if vals else None


# ── one-pass multi-date scan ─────────────────────────────────────────────────

def _scan_grid(cal, idxs: Dict[int, int]) -> Dict[int, Dict[str, Dict]]:
    """Per lookback: the same `world` dict `_scan_world` builds, but every
    ticker's parquet is loaded and calendar-aligned exactly once."""
    spy = srd._aligned(srd._load_merged("SPY"), cal)
    worlds: Dict[int, Dict[str, Dict]] = {}
    spy20, last_needed = {}, {}
    for lb, i in idxs.items():
        spy20[lb] = (float(spy.iloc[i] / spy.iloc[i - 20] - 1.0)
                     if i >= 20 and not pd.isna(spy.iloc[i - 20]) else None)
        last_needed[lb] = cal[min(i + max(srd.HORIZONS), len(cal) - 1)]
        worlds[lb] = {
            "__spy__": {"fwd": {h: srd._fwd_return(spy, i, h) for h in srd.HORIZONS},
                        "r20": spy20[lb]},
            "__meta__": {"excluded_stale": 0, "excluded_data_suspect": 0},
        }
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = srd._load_merged(t)
        if df is None or df.empty:
            continue
        c = srd._aligned(df, cal)
        for lb, i in idxs.items():
            status, rec = srd._eval_ticker(df, c, cal, i, spy20[lb], last_needed[lb])
            if status == "ok":
                worlds[lb][t] = rec
            elif status == "stale":
                worlds[lb]["__meta__"]["excluded_stale"] += 1
            elif status == "suspect":
                worlds[lb]["__meta__"]["excluded_data_suspect"] += 1
    return worlds


# ── per-date record (serializable) + in-memory extras ────────────────────────

def _date_record(lb: int, i: int, cal, world: Dict, profiles: Dict,
                 winner_thresh: float, rs_cap: int) -> Tuple[Dict, Dict]:
    liquid = {t for t in world if not t.startswith("__")}
    winners = {t for t in liquid
               if world[t]["fwd"][20] is not None
               and world[t]["fwd"][20] >= winner_thresh}
    strict = {t for t in liquid if world[t]["strict_pass"]}
    loose = {t for t in liquid if world[t]["loose_pass"]}
    rs_base = srd._rs_baseline(world, rs_cap)
    rejected = liquid - strict
    rec = {
        "lookback_td": lb,
        "asof_date": str(cal[i])[:10],
        "n_liquid": len(liquid),
        "n_excluded_stale": world["__meta__"]["excluded_stale"],
        "n_excluded_data_suspect": world["__meta__"]["excluded_data_suspect"],
        "n_forward_winners": len(winners),
        "n_rejected": len(rejected),
        "spy_fwd_pct": {f"{h}d": srd._pct(world["__spy__"]["fwd"][h])
                        for h in srd.HORIZONS},
        "cohorts": {
            "strict_scanner": srd._cohort_stats("strict_scanner", strict, world,
                                                winners, liquid),
            "rs_baseline": srd._cohort_stats("rs_baseline", rs_base, world,
                                             winners, liquid),
            "loose_scanner": srd._cohort_stats("loose_scanner", loose, world,
                                               winners, liquid),
        },
        "reject_counts_by_filter": srd._reject_counts(world, rejected, winners),
        "top_rejected": srd._top_rejected(world, rejected, loose, rs_base,
                                          profiles, TOP_REJECTED_PER_DATE),
    }
    extras = {"world": world, "rejected": rejected, "winners": winners}
    return rec, extras


# ── cross-date cohort scoreboard (questions 1-3, 6) ──────────────────────────

def _cohort_scoreboard(dates: List[Dict]) -> Dict:
    def series(cohort: str, path: Tuple[str, ...]) -> List[Optional[float]]:
        out = []
        for d in dates:
            v = d["cohorts"][cohort]
            for k in path:
                v = v[k] if v is not None else None
            out.append(v)
        return out

    def _count(pairs) -> Tuple[int, int]:
        both = [(a, b) for a, b in pairs if a is not None and b is not None]
        return sum(1 for a, b in both if a > b), len(both)

    sb: Dict[str, Dict] = {}
    for c in ("strict_scanner", "rs_baseline", "loose_scanner"):
        sb[c] = {
            "n_median": _median([v for v in series(c, ("n",)) if v is not None]),
            "fwd20_mean_median_pct": _median(
                [v for v in series(c, ("forward", "20d", "mean_pct")) if v is not None]),
            "fwd20_winsor_median_pct": _median(
                [v for v in series(c, ("forward", "20d", "winsor_mean_pct")) if v is not None]),
            "excess20_median_pct": _median(
                [v for v in series(c, ("forward", "20d", "excess_vs_spy_pct")) if v is not None]),
            "recall_median_pct": _median(
                [v for v in series(c, ("winner_recall_pct",)) if v is not None]),
            "ctrl_recall_median_pct": _median(
                [v for v in series(c, ("random_control", "winner_recall_pct")) if v is not None]),
            "mae20_median_pct": _median(
                [v for v in series(c, ("mae20_median_pct",)) if v is not None]),
        }
    n_dates = len(dates)
    s_recall = series("strict_scanner", ("winner_recall_pct",))
    s_ctrl = series("strict_scanner", ("random_control", "winner_recall_pct"))
    s_f20 = series("strict_scanner", ("forward", "20d", "winsor_mean_pct"))
    s_ctrl_f20 = series("strict_scanner", ("random_control", "fwd20_mean_pct"))
    rs_f20 = series("rs_baseline", ("forward", "20d", "winsor_mean_pct"))
    l_recall = series("loose_scanner", ("winner_recall_pct",))
    l_ctrl = series("loose_scanner", ("random_control", "winner_recall_pct"))
    l_f20 = series("loose_scanner", ("forward", "20d", "winsor_mean_pct"))
    q1_rec, q1n = _count(zip(s_recall, s_ctrl))
    q1_fwd, _ = _count(zip(s_f20, s_ctrl_f20))
    q2_fwd, q2n = _count(zip(s_f20, rs_f20))
    q3_rec, q3n = _count(zip(l_recall, s_recall))
    q3_ctl, _ = _count(zip(l_recall, l_ctrl))
    q3_fwd, _ = _count(zip(s_f20, l_f20))
    sb["answers"] = {
        "q1_strict_beats_random": {
            "recall_dates": f"{q1_rec}/{q1n}", "fwd20_winsor_dates": f"{q1_fwd}/{q1n}",
            "answer": bool(q1n and q1_rec >= 0.7 * q1n and q1_fwd >= 0.7 * q1n),
        },
        "q2_strict_beats_simple_rs": {
            "fwd20_winsor_dates": f"{q2_fwd}/{q2n}",
            "answer": bool(q2n and q2_fwd >= 0.7 * q2n),
        },
        "q3_loose_improves_recall_without_destroying_edge": {
            "recall_gain_dates": f"{q3_rec}/{q3n}",
            "loose_recall_beats_own_control_dates": f"{q3_ctl}/{q3n}",
            "strict_fwd20_beats_loose_dates": f"{q3_fwd}/{q3n}",
            "answer": bool(q3n and q3_rec >= 0.7 * q3n and q3_ctl >= 0.7 * q3n),
            "note": ("Loose adds recall but must beat ITS OWN size-matched random "
                     "control to count as selection edge, not just width."),
        },
        "n_dates": n_dates,
    }
    return sb


# ── filter-level stability + sector split (questions 4, 5, 7) ────────────────

def _filter_stability(dates: List[Dict], extras: List[Dict],
                      profiles: Dict) -> List[Dict]:
    codes: Set[str] = set()
    for d in dates:
        codes.update(f"{r['lane']}:{r['filter']}" for r in d["reject_counts_by_filter"])
    out = []
    for code in sorted(codes):
        lane, filt = code.split(":", 1)
        per_date, per_date_exhc = [], []
        pooled, pooled_exhc, missed = [], [], 0
        for d, ex in zip(dates, extras):
            world, rejected = ex["world"], ex["rejected"]
            spy20 = d["spy_fwd_pct"]["20d"]
            rets, rets_exhc = [], []
            for t in rejected:
                w = world[t]
                reasons = w["voyager_reasons"] if lane == "voyager" else w["sniper_reasons"]
                if filt in reasons and w["fwd"][20] is not None:
                    rets.append(w["fwd"][20])
                    if t in ex["winners"]:
                        missed += 1
                    if not _is_healthcare(profiles, t):
                        rets_exhc.append(w["fwd"][20])
            if len(rets) >= MIN_REJECTS_PER_DATE:
                med = srd._pct(_median(rets))
                per_date.append({"asof": d["asof_date"], "n": len(rets),
                                 "median_pct": med,
                                 "winsor_mean_pct": srd._pct(srd._winsor_mean(rets)),
                                 "beats_spy": (med is not None and spy20 is not None
                                               and med > spy20)})
                pooled.extend(rets)
            if len(rets_exhc) >= MIN_REJECTS_PER_DATE:
                med = srd._pct(_median(rets_exhc))
                per_date_exhc.append({"asof": d["asof_date"], "n": len(rets_exhc),
                                      "median_pct": med,
                                      "beats_spy": (med is not None and spy20 is not None
                                                    and med > spy20)})
                pooled_exhc.extend(rets_exhc)
        ev, ev_x = len(per_date), len(per_date_exhc)
        beats = sum(1 for p in per_date if p["beats_spy"])
        beats_x = sum(1 for p in per_date_exhc if p["beats_spy"])
        out.append({
            "filter": filt, "lane": lane,
            "evidence_dates": ev,
            "trusted": ev >= MIN_EVIDENCE_DATES,
            "pooled_n": len(pooled),
            "winners_missed_total": missed,
            "pooled_median_fwd20_pct": srd._pct(_median(pooled)),
            "pooled_winsor_fwd20_pct": srd._pct(srd._winsor_mean(pooled)),
            "beats_spy_dates": f"{beats}/{ev}",
            "beats_spy_frac": round(beats / ev, 2) if ev else None,
            "ex_healthcare": {
                "evidence_dates": ev_x,
                "pooled_n": len(pooled_exhc),
                "pooled_median_fwd20_pct": srd._pct(_median(pooled_exhc)),
                "pooled_winsor_fwd20_pct": srd._pct(srd._winsor_mean(pooled_exhc)),
                "beats_spy_dates": f"{beats_x}/{ev_x}",
                "beats_spy_frac": round(beats_x / ev_x, 2) if ev_x else None,
            },
            "per_date": per_date,
        })
    out.sort(key=lambda r: -(r["pooled_n"]))
    return out


def _combo_stats(dates: List[Dict], extras: List[Dict], profiles: Dict) -> List[Dict]:
    out = []
    for combo in NAMED_COMBOS:
        pooled, pooled_exhc, missed, ev = [], [], 0, 0
        for d, ex in zip(dates, extras):
            world = ex["world"]
            rets = []
            for t in ex["rejected"]:
                w = world[t]
                reasons = set(w["voyager_reasons"]) | set(w["sniper_reasons"])
                if set(combo) <= reasons and w["fwd"][20] is not None:
                    rets.append(w["fwd"][20])
                    if t in ex["winners"]:
                        missed += 1
                    if not _is_healthcare(profiles, t):
                        pooled_exhc.append(w["fwd"][20])
            if len(rets) >= MIN_REJECTS_PER_DATE:
                ev += 1
            pooled.extend(rets)
        out.append({
            "combo": "+".join(combo),
            "evidence_dates": ev,
            "pooled_n": len(pooled),
            "winners_missed_total": missed,
            "pooled_median_fwd20_pct": srd._pct(_median(pooled)),
            "pooled_winsor_fwd20_pct": srd._pct(srd._winsor_mean(pooled)),
            "ex_healthcare_median_fwd20_pct": srd._pct(_median(pooled_exhc)),
            "ex_healthcare_n": len(pooled_exhc),
        })
    return out


# ── top rejected winners aggregate (questions 7, 8) ──────────────────────────

def _rejected_winner_analysis(dates: List[Dict], winner_thresh: float) -> Dict:
    thresh_pct = 100.0 * winner_thresh
    seen: Dict[str, List[Dict]] = {}
    single, combo, hc, non_hc = 0, 0, 0, 0
    for d in dates:
        for x in d["top_rejected"]:
            if x["fwd20_pct"] is None or x["fwd20_pct"] < thresh_pct:
                continue
            seen.setdefault(x["ticker"], []).append(
                {"asof": d["asof_date"], "fwd20_pct": x["fwd20_pct"],
                 "reasons": x["reject_reasons"]})
            voy = sum(1 for r in x["reject_reasons"] if r.startswith("voyager:"))
            sni = sum(1 for r in x["reject_reasons"] if r.startswith("sniper:"))
            # one lane needs one flipped filter to admit ⇒ single-filter block
            if min(voy, sni) <= 1:
                single += 1
            else:
                combo += 1
            if x.get("theme") == "biotech_healthcare" or x.get("sector") == "Healthcare":
                hc += 1
            else:
                non_hc += 1
    repeats = {t: v for t, v in seen.items() if len(v) >= 2}
    total = single + combo
    return {
        "distinct_top_rejected_winners": len(seen),
        "repeat_winners": {
            t: {"appearances": len(v),
                "fwd20s_pct": [round(e["fwd20_pct"], 1) for e in v],
                "filters": sorted({r.split(":", 1)[1] for e in v for r in e["reasons"]})}
            for t, v in sorted(repeats.items(), key=lambda x: -len(x[1]))},
        "block_structure": {
            "single_filter_blocks": single, "combination_blocks": combo,
            "single_filter_frac": round(single / total, 2) if total else None,
        },
        "sector_split": {
            "healthcare_biotech": hc, "non_healthcare": non_hc,
            "healthcare_frac": round(hc / (hc + non_hc), 2) if (hc + non_hc) else None,
        },
    }


# ── decision section (question set + guardrails) ─────────────────────────────

def _decide(stability: List[Dict], n_dates: int) -> Dict:
    keep, investigate, test_loose, not_safe, insufficient = [], [], [], [], []
    sector_flip = []
    for f in stability:
        name = f"{f['lane']}:{f['filter']}"
        if not f["trusted"]:
            insufficient.append(name)
            continue
        frac = f["beats_spy_frac"] or 0.0
        med = f["pooled_median_fwd20_pct"]
        x = f["ex_healthcare"]
        x_ok = (x["evidence_dates"] >= MIN_EVIDENCE_DATES
                and (x["beats_spy_frac"] or 0.0) >= 0.5
                and (x["pooled_median_fwd20_pct"] or 0.0) > 0)
        x_flip = (frac >= 0.7 and x["evidence_dates"] >= MIN_EVIDENCE_DATES
                  and (x["beats_spy_frac"] or 0.0) < 0.5)
        if x_flip:
            sector_flip.append(name)
        if frac >= 0.7 and med is not None and med > 0:
            if x_ok:
                test_loose.append(name)
            else:
                investigate.append(name + " (sector-specific — healthcare-driven; "
                                          "needs sector-aware experiment)")
        elif frac >= 0.5 and med is not None and med > 0:
            investigate.append(name)
        else:
            keep.append(name)
            if med is not None and med <= 0 and frac <= 0.4:
                not_safe.append(name)
    if n_dates < MIN_EVIDENCE_DATES:
        strength = "INSUFFICIENT"
    elif n_dates < 8:
        strength = "WEAK"
    elif sector_flip or (test_loose and investigate):
        strength = "MIXED"
    else:
        strength = "STRONG"
    return {
        "filters_to_keep_strict": keep,
        "filters_to_investigate": investigate,
        "filters_to_test_in_loose_experiment": test_loose,
        "filters_not_safe_to_loosen": not_safe,
        "insufficient_evidence": insufficient,
        "sector_flip_filters": sector_flip,
        "evidence_strength": strength,
        "rules": ("trusted = >=%d rejects/date on >=%d dates; "
                  "test_loose = beats SPY on >=70%% of evidence dates with positive "
                  "pooled median AND holds ex-healthcare; investigate = 50-70%% or "
                  "sector-specific; not_safe = median <=0 and beats-SPY <=40%%."
                  % (MIN_REJECTS_PER_DATE, MIN_EVIDENCE_DATES)),
        "guardrails": ("DIAGNOSTIC ONLY — no scanner threshold changed, no filter "
                       "loosened, no ticker promoted to a signal. Any loosening "
                       "requires a separate gated forward experiment."),
    }


# ── build + render ───────────────────────────────────────────────────────────

def build(*, grid=GRID_DEFAULT, winner_thresh: float = srd.DEFAULT_WINNER_FWD20,
          rs_cap: int = srd.DEFAULT_RS_CAP) -> Dict:
    cal = dataio.benchmark_calendar()
    profiles = dataio.load_profiles()
    grid = sorted({int(g) for g in grid})
    idxs = {lb: len(cal) - 1 - lb for lb in grid if len(cal) >= lb + 61}
    skipped = [lb for lb in grid if lb not in idxs]
    if not idxs:
        return {"error": f"calendar too short ({len(cal)} bars) for grid {grid}"}
    worlds = _scan_grid(cal, idxs)
    dates, extras = [], []
    for lb, i in sorted(idxs.items()):
        rec, ex = _date_record(lb, i, cal, worlds[lb], profiles,
                               winner_thresh, rs_cap)
        dates.append(rec)
        extras.append(ex)
    scoreboard = _cohort_scoreboard(dates)
    stability = _filter_stability(dates, extras, profiles)
    decision = _decide(stability, len(dates))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_version": ARTIFACT_VERSION,
        "disclaimer": ("MULTI-DATE RESEARCH DIAGNOSTIC — not a buy list, not a "
                       "signal, not a gate change. Production gates unchanged."),
        "params": {"grid_lookback_td": list(idxs), "grid_skipped": skipped,
                   "winner_threshold_fwd20": winner_thresh, "rs_cap": rs_cap,
                   "min_rejects_per_date": MIN_REJECTS_PER_DATE,
                   "min_evidence_dates": MIN_EVIDENCE_DATES,
                   "loose_thresholds": srd.LOOSE_THRESHOLDS},
        "n_dates": len(dates),
        "dates": dates,
        "cohort_scoreboard": scoreboard,
        "filter_stability": stability,
        "filter_combos": _combo_stats(dates, extras, profiles),
        "rejected_winner_analysis": _rejected_winner_analysis(dates, winner_thresh),
        "decision": decision,
        "verdict": "DIAGNOSTIC_ONLY",
    }


def _render_txt(res: Dict) -> List[str]:
    if "error" in res:
        return [f"== SCANNER RECALL DIAGNOSTICS BATCH — ERROR: {res['error']} =="]
    sb, ans = res["cohort_scoreboard"], res["cohort_scoreboard"]["answers"]
    L = [
        f"== SCANNER RECALL DIAGNOSTICS — MULTI-DATE BATCH ({res['generated_at']}) ==",
        res["disclaimer"], "",
        f"dates={res['n_dates']} (lookbacks {res['params']['grid_lookback_td']})"
        + (f"  skipped={res['params']['grid_skipped']}" if res["params"]["grid_skipped"] else ""),
        "",
        "PER-DATE SNAPSHOT (winsorized 20d fwd mean % | recall % vs random-ctrl %):",
    ]
    for d in res["dates"]:
        row = f"  {d['asof_date']} lb={d['lookback_td']:<3} liq={d['n_liquid']:<5}"
        for c in ("strict_scanner", "rs_baseline", "loose_scanner"):
            co = d["cohorts"][c]
            f20 = co["forward"]["20d"]
            row += (f"  {c.split('_')[0]}: {f20['winsor_mean_pct']}% "
                    f"{co['winner_recall_pct']}/{co['random_control']['winner_recall_pct']}")
        L.append(row + f"  stale={d['n_excluded_stale']}")
    L += ["", "CROSS-DATE MEDIANS:"]
    for c in ("strict_scanner", "rs_baseline", "loose_scanner"):
        s = sb[c]
        L.append(f"  {c:<15} n~{s['n_median']}  fwd20 winsor={s['fwd20_winsor_median_pct']}%  "
                 f"exSPY={s['excess20_median_pct']}%  recall={s['recall_median_pct']}%  "
                 f"ctrl={s['ctrl_recall_median_pct']}%  mae20={s['mae20_median_pct']}%")
    L += ["", "ANSWERS:",
          f"  Q1 strict beats random:      {ans['q1_strict_beats_random']['answer']}  "
          f"(recall {ans['q1_strict_beats_random']['recall_dates']}, "
          f"fwd {ans['q1_strict_beats_random']['fwd20_winsor_dates']} dates)",
          f"  Q2 strict beats simple-RS:   {ans['q2_strict_beats_simple_rs']['answer']}  "
          f"(fwd {ans['q2_strict_beats_simple_rs']['fwd20_winsor_dates']} dates)",
          f"  Q3 loose adds real recall:   "
          f"{ans['q3_loose_improves_recall_without_destroying_edge']['answer']}  "
          f"(recall gain {ans['q3_loose_improves_recall_without_destroying_edge']['recall_gain_dates']}, "
          f"beats own ctrl {ans['q3_loose_improves_recall_without_destroying_edge']['loose_recall_beats_own_control_dates']})",
          "", "FILTER STABILITY (trusted filters, pooled across dates; median/winsor fwd20 of rejects):"]
    for f in res["filter_stability"]:
        if not f["trusted"]:
            continue
        x = f["ex_healthcare"]
        L.append(f"  {f['lane']}:{f['filter']:<26} n={f['pooled_n']:<6} "
                 f"missed={f['winners_missed_total']:<5} med={f['pooled_median_fwd20_pct']}% "
                 f"winsor={f['pooled_winsor_fwd20_pct']}%  beatsSPY={f['beats_spy_dates']}  "
                 f"exHC: med={x['pooled_median_fwd20_pct']}% beatsSPY={x['beats_spy_dates']}")
    L += ["", "FILTER COMBOS (superset match, pooled):"]
    for cst in res["filter_combos"]:
        L.append(f"  {cst['combo']:<40} n={cst['pooled_n']:<6} "
                 f"missed={cst['winners_missed_total']:<5} med={cst['pooled_median_fwd20_pct']}%  "
                 f"winsor={cst['pooled_winsor_fwd20_pct']}%  "
                 f"exHC med={cst['ex_healthcare_median_fwd20_pct']}% (n={cst['ex_healthcare_n']})")
    rw = res["rejected_winner_analysis"]
    L += ["", f"REJECTED WINNERS: distinct={rw['distinct_top_rejected_winners']}  "
          f"repeats={len(rw['repeat_winners'])}  "
          f"single-filter blocks={rw['block_structure']['single_filter_blocks']} vs "
          f"combination={rw['block_structure']['combination_blocks']}  "
          f"healthcare share={rw['sector_split']['healthcare_frac']}"]
    for t, v in list(rw["repeat_winners"].items())[:10]:
        L.append(f"  {t:<7} x{v['appearances']}  fwd20s={v['fwd20s_pct']}  filters={v['filters']}")
    dec = res["decision"]
    L += ["", "DECISION (diagnostic proposals — NOT gate changes):",
          f"  evidence_strength: {dec['evidence_strength']}",
          f"  keep_strict:       {dec['filters_to_keep_strict']}",
          f"  investigate:       {dec['filters_to_investigate']}",
          f"  test_in_loose_exp: {dec['filters_to_test_in_loose_experiment']}",
          f"  not_safe_to_loosen:{dec['filters_not_safe_to_loosen']}",
          f"  insufficient:      {dec['insufficient_evidence']}",
          "", dec["guardrails"],
          "", f"VERDICT: {res['verdict']}"]
    return L


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Multi-date scanner recall diagnostics batch (research-only).")
    ap.add_argument("--grid", type=str,
                    default=",".join(str(g) for g in GRID_DEFAULT),
                    help="comma-separated lookback trading days (default 20..75)")
    ap.add_argument("--winner-thresh", type=float, default=srd.DEFAULT_WINNER_FWD20)
    ap.add_argument("--rs-cap", type=int, default=srd.DEFAULT_RS_CAP)
    args = ap.parse_args(argv)
    grid = [int(x) for x in args.grid.split(",") if x.strip()]
    res = build(grid=grid, winner_thresh=args.winner_thresh,
                rs_cap=max(1, args.rs_cap))
    dataio.write_json(dataio.RESEARCH_CACHE
                      / "scanner_recall_diagnostics_batch_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR
                      / "scanner_recall_diagnostics_batch_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
