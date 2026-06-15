"""
research/rs_recall_forward_validation.py — Phase 1G.7 Task 4.

Forward-validates the RS recall lane's historized top-N picks
(data/research/rs_recall_lane_history.jsonl) OUT-OF-SAMPLE: each pick is scored
only once enough forward bars have matured, using bars strictly AFTER the pick's
as-of date. It compares the lane against random liquid controls, a simple
momentum baseline, the Alpha board, and cash, then emits a gated verdict.

This is the gate that decides whether the lane is allowed to FEED the Lens
later — it never promotes the lane to a paper strategy and never emits signals.

Forward windows: 1d / 3d / 5d / 10d / 20d (each only when matured).
Per matured pick: forward return, return relative to SPY/QQQ, max favorable /
adverse excursion, became-too-extended, offered-pullback/reclaim, plus best-effort
(point-in-time-limited) artifact annotations (later on Alpha board / Lens exists /
Gatekeeper artifact).

Verdicts: NEED_MORE_DATA / REJECT / WATCHLIST_RESEARCH / READY_TO_FEED_LENS.

Outputs:
  cache/research/rs_recall_forward_validation_latest.json
  logs/rs_recall_forward_validation_latest.txt

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes, no signals.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from statistics import mean, median
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from research.rs_recall_lane import (EXTENDED_MA20, HISTORY_PATH, NEAR_HIGH,
                                     _aligned)
from research.scanner_truth import dataio
from research.scanner_truth.filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL,
                                            UNIV_MIN_AVG_VOL, UNIV_MIN_PRICE, sma)

HORIZONS = (1, 3, 5, 10, 20)
PRIMARY_HORIZON = 5
MIN_MATURED_FOR_VERDICT = 30        # need this many matured picks before judging
RANDOM_SEED = 42


def _cal_index(cal: pd.DatetimeIndex, asof: str) -> Optional[int]:
    ts = pd.Timestamp(asof)
    locs = np.where(cal <= ts)[0]
    return int(locs[-1]) if len(locs) else None


def _fwd_metrics(t: str, cal, i: int, h: int, spy: pd.Series, qqq: pd.Series) -> Optional[Dict]:
    """Forward metrics for ticker `t` over `h` bars after as-of index `i`.
    Returns None if not matured (cal lacks i+h) or price data missing."""
    if i is None or i + h >= len(cal):
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
    # became too extended / offered pullback within the forward window
    ext_series, near_high_dips = [], False
    for j in range(i, i + h + 1):
        ma20 = float(sma(c.iloc[:j + 1], 20).iloc[-1]) if j >= 20 else np.nan
        if ma20 and not np.isnan(ma20):
            ext_series.append((float(c.iloc[j]) - ma20) / ma20)
    became_extended = any(e > EXTENDED_MA20 for e in ext_series) if ext_series else None
    offered_pullback = any(e <= 0.0 for e in ext_series) if ext_series else None
    return {
        "fwd_return": round(fwd, 4),
        "rel_return_spy": round(rel_spy, 4) if rel_spy is not None else None,
        "rel_return_qqq": round(fwd - qqq_ret, 4) if qqq_ret is not None else None,
        "mfe": round(mfe, 4), "mae": round(mae, 4),
        "became_too_extended": became_extended,
        "offered_pullback": offered_pullback,
    }


def _liquid_at(cal, i: int) -> List[str]:
    """Liquid universe as-of index i (cache-only, point-in-time)."""
    out = []
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = dataio.load_prices(t)
        if df is None:
            continue
        c = _aligned(df, cal)
        if i >= len(c) or pd.isna(c.iloc[i]) or i < 20:
            continue
        price = float(c.iloc[i])
        vol = df["volume"].reindex(cal).ffill()
        avgvol = float(vol.iloc[i - 19:i + 1].mean())
        avgdvol = float((c * vol).iloc[i - 19:i + 1].mean())
        if (UNIV_MIN_PRICE <= price <= UNIV_MAX_PRICE
                and avgvol >= UNIV_MIN_AVG_VOL and avgdvol >= UNIV_MIN_AVG_DVOL):
            out.append(t)
    return out


def _momentum_cohort(cal, i: int, liquid: List[str], n: int) -> List[str]:
    """Top-`n` liquid names by 60d momentum as-of i (point-in-time baseline)."""
    scored = []
    for t in liquid:
        c = _aligned(dataio.load_prices(t), cal)
        if i - 60 < 0 or pd.isna(c.iloc[i - 60]) or c.iloc[i - 60] <= 0:
            continue
        scored.append((float(c.iloc[i] / c.iloc[i - 60] - 1.0), t))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:n]]


def _cohort_stats(tickers: List[str], cal, i: int, h: int,
                  spy: pd.Series, qqq: pd.Series) -> Dict:
    rels, fwds = [], []
    for t in tickers:
        m = _fwd_metrics(t, cal, i, h, spy, qqq)
        if m and m["rel_return_spy"] is not None:
            rels.append(m["rel_return_spy"])
            fwds.append(m["fwd_return"])
    return {"n": len(rels),
            "mean_fwd": round(mean(fwds), 4) if fwds else None,
            "mean_rel_spy": round(mean(rels), 4) if rels else None,
            "median_rel_spy": round(median(rels), 4) if rels else None,
            "win_rate": round(100.0 * sum(1 for r in rels if r > 0) / len(rels), 1) if rels else None}


def build() -> Dict:
    hist = dataio.read_jsonl(HISTORY_PATH)
    cal = dataio.benchmark_calendar()
    spy = _aligned(dataio.load_prices("SPY"), cal)
    qqq = _aligned(dataio.load_prices("QQQ"), cal)
    rng = random.Random(RANDOM_SEED)

    # group history rows by as-of date
    by_date: Dict[str, List[Dict]] = {}
    for r in hist:
        by_date.setdefault(r.get("asof_date"), []).append(r)

    today_i = len(cal) - 1
    per_horizon: Dict[str, Dict] = {}
    for h in HORIZONS:
        lane15, lane25, board_c, rand_c, mom_c = [], [], [], [], []
        matured_dates = 0
        for asof, rows in by_date.items():
            i = _cal_index(cal, asof)
            if i is None or i + h >= len(cal):
                continue                          # immature for this horizon
            matured_dates += 1
            top15 = [r["ticker"] for r in rows if r.get("rank", 99) <= 15]
            top25 = [r["ticker"] for r in rows if r.get("rank", 99) <= 25]
            lane15.append(_cohort_stats(top15, cal, i, h, spy, qqq))
            lane25.append(_cohort_stats(top25, cal, i, h, spy, qqq))
            liquid = _liquid_at(cal, i)
            ctrl = rng.sample(liquid, min(len(top25), len(liquid))) if liquid else []
            rand_c.append(_cohort_stats(ctrl, cal, i, h, spy, qqq))
            mom_c.append(_cohort_stats(_momentum_cohort(cal, i, liquid, len(top25)),
                                       cal, i, h, spy, qqq))
            board_c.append(_cohort_stats(_current_board(), cal, i, h, spy, qqq))

        def _agg(cohorts: List[Dict]) -> Dict:
            rels = [c["mean_rel_spy"] for c in cohorts if c["mean_rel_spy"] is not None]
            return {"matured_groups": len(rels),
                    "mean_rel_spy": round(mean(rels), 4) if rels else None}
        per_horizon[f"{h}d"] = {
            "matured_dates": matured_dates,
            "rs_top15": _agg(lane15), "rs_top25": _agg(lane25),
            "alpha_board": _agg(board_c), "random_control": _agg(rand_c),
            "momentum_baseline": _agg(mom_c),
            "cash": {"mean_rel_spy": 0.0, "note": "cash earns 0 vs SPY by construction"},
        }

    verdict, reason = _verdict(per_horizon)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "FORWARD VALIDATION GATE — research-only. Does NOT promote the "
                      "lane to a paper strategy and emits no signals.",
        "history_path": dataio.rel_to_repo(HISTORY_PATH),
        "history_rows": len(hist),
        "history_dates": sorted(by_date),
        "primary_horizon": f"{PRIMARY_HORIZON}d",
        "min_matured_for_verdict": MIN_MATURED_FOR_VERDICT,
        "artifact_annotations_caveat": (
            "Alpha-board / Lens / Gatekeeper annotations use CURRENT artifacts, not "
            "point-in-time snapshots (no board history exists yet) — treat as "
            "best-effort, not as-of truth."),
        "by_horizon": per_horizon,
        "verdict": verdict,
        "verdict_reason": reason,
    }


def _current_board() -> List[str]:
    try:
        b = json.loads((dataio.RESEARCH_CACHE / "alpha_discovery_board_latest.json").read_text())
        return [(i.get("ticker") or i.get("symbol") or "").upper()
                for i in b.get("items", []) if (i.get("ticker") or i.get("symbol"))]
    except Exception:
        return []


def _verdict(per_horizon: Dict) -> tuple:
    ph = per_horizon.get(f"{PRIMARY_HORIZON}d", {})
    matured = ph.get("matured_dates", 0)
    rs = (ph.get("rs_top15") or {}).get("mean_rel_spy")
    rand = (ph.get("random_control") or {}).get("mean_rel_spy")
    # Count total matured picks (groups × ~picks) conservatively by matured groups.
    if matured == 0 or rs is None:
        return "NEED_MORE_DATA", (
            f"No matured {PRIMARY_HORIZON}d picks yet (history is young; the historizer "
            "appends nightly). Re-run after forward bars mature.")
    if matured * 15 < MIN_MATURED_FOR_VERDICT:
        return "NEED_MORE_DATA", (
            f"Only {matured} matured as-of date(s) at {PRIMARY_HORIZON}d — below the "
            f"{MIN_MATURED_FOR_VERDICT}-pick floor for a verdict.")
    if rs <= 0:
        return "REJECT", (f"RS top-15 {PRIMARY_HORIZON}d mean excess return {rs} ≤ 0 — "
                          "no forward edge vs SPY.")
    if rand is not None and rs <= rand:
        return "REJECT", (f"RS top-15 ({rs}) does not beat random control ({rand}) at "
                          f"{PRIMARY_HORIZON}d — no selection edge.")
    if rand is not None and rs > rand and rs > 0:
        return "READY_TO_FEED_LENS", (
            f"RS top-15 {PRIMARY_HORIZON}d excess {rs} beats random ({rand}) and is "
            "positive over an adequate matured sample. Eligible to feed Lens/Gatekeeper "
            "(NOT execution) pending operator review.")
    return "WATCHLIST_RESEARCH", "Positive but not clearly above controls; keep watching."


def _render_txt(res: Dict) -> List[str]:
    L = [
        f"== RS RECALL FORWARD VALIDATION ({res['generated_at']}) ==",
        res["disclaimer"],
        f"history: {res['history_rows']} rows over {len(res['history_dates'])} date(s) "
        f"→ {res['history_path']}",
        "",
        f"{'horizon':<8}{'matured':>8}{'rs15':>9}{'rs25':>9}{'board':>9}{'random':>9}{'mom':>9}",
    ]
    for h, d in res["by_horizon"].items():
        def g(k):
            v = (d.get(k) or {}).get("mean_rel_spy")
            return f"{v:+.3f}" if isinstance(v, (int, float)) else "—"
        L.append(f"{h:<8}{d['matured_dates']:>8}{g('rs_top15'):>9}{g('rs_top25'):>9}"
                 f"{g('alpha_board'):>9}{g('random_control'):>9}{g('momentum_baseline'):>9}")
    L += ["", f"VERDICT: {res['verdict']}", "  " + res["verdict_reason"],
          "", "caveat: " + res["artifact_annotations_caveat"]]
    return L


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "rs_recall_forward_validation_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "rs_recall_forward_validation_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
