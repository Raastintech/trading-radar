"""
research/recall_repair_shadow_forward.py — Recall-Repair Shadow Lane forward study.

RESEARCH-ONLY / CACHE-ONLY.  Measures whether the dated, historized shadow board
(``research/recall_repair_shadow_lane.py``) actually catches forward winners
earlier and with better precision than the live funnel / Alpha board, against
random and dumb-baseline controls.  Emits a GATED verdict.

NO provider calls, NO DB writes, NO signals, NO trade proposals, NO gate/
execution/governance/universe changes.  Forward returns are point-in-time
correct (features used only bars ≤ as-of; outcomes use only bars > as-of).

CAVEAT (documented, not hidden): the historized cross-reference annotations
(on_alpha_board / has_lens / has_gatekeeper / scanner_saw) use CURRENT artifacts,
not point-in-time snapshots — so the "vs Alpha board" comparison is best-effort
context, while the forward RETURN comparisons are as-of correct.

Outputs:
  cache/research/recall_repair_shadow_forward_latest.json
  logs/recall_repair_shadow_forward_latest.txt
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.scanner_truth import dataio
from research.scanner_truth.filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL,
                                            UNIV_MIN_AVG_VOL, UNIV_MIN_PRICE)
from research.recall_repair_shadow_lane import (LANE_HISTORY, VERSION,
                                                MIN_BARS_FOR_FEATURES)

HORIZONS = [1, 3, 5, 10, 20]
PRIMARY_HORIZON = 5            # the random-control gate anchors here
RECALL_HORIZON = 20           # winner thresholds measured over this window
WINNER_THRESHOLDS = [0.20, 0.30, 0.50]
RANDOM_CONTROL_SEED = 1729    # fixed so the verdict is reproducible run-to-run
RANDOM_CONTROL_N = 200
RS_TOP_N = 200                # the dumb sector_rs baseline control size

# Funnel recall benchmark from the Scanner Truth review (the number to beat).
FUNNEL_RECALL_PCT = 1.1
# Decision-gate floors (Task C).
MIN_HISTORY_DAYS = 10
MIN_MATURE_TICKER_DAYS = 300
RECALL_IMPROVE_FACTOR = 2.0   # shadow recall must be ≥ 2× the funnel's 1.1%

FWD_JSON = dataio.RESEARCH_CACHE / "recall_repair_shadow_forward_latest.json"
FWD_TXT = dataio.LOGS_DIR / "recall_repair_shadow_forward_latest.txt"


def _aligned(df: pd.DataFrame, calendar) -> pd.Series:
    c = df["close"].reindex(calendar)
    fv = c.first_valid_index()
    if fv is not None:
        c.loc[c.index >= fv] = c.loc[c.index >= fv].ffill()
    return c


def _ret(s: pd.Series, i_from: int, i_to: int) -> Optional[float]:
    if i_from < 0 or i_to < 0 or i_from >= len(s) or i_to >= len(s):
        return None
    a, b = s.iloc[i_from], s.iloc[i_to]
    if pd.isna(a) or pd.isna(b) or a <= 0:
        return None
    return float(b / a - 1.0)


def _fwd_metrics(c: pd.Series, asof_i: int, h: int) -> Optional[Dict[str, float]]:
    """Forward end/MFE/MAE over [asof_i, asof_i+h].  None if not matured."""
    end_i = asof_i + h
    if end_i >= len(c):
        return None
    base = c.iloc[asof_i]
    if pd.isna(base) or base <= 0:
        return None
    seg = c.iloc[asof_i:end_i + 1].values.astype(float)
    seg = seg[~np.isnan(seg)]
    if len(seg) < 2:
        return None
    fwd_end = float(c.iloc[end_i] / base - 1.0) if not pd.isna(c.iloc[end_i]) else None
    if fwd_end is None:
        return None
    return {
        "fwd_end": fwd_end,
        "mfe": float(np.nanmax(seg) / base - 1.0),
        "mae": float(np.nanmin(seg) / base - 1.0),
    }


def _mean(xs: List[float]) -> Optional[float]:
    return float(np.mean(xs)) if xs else None


def _winrate(xs: List[float]) -> Optional[float]:
    return round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 1) if xs else None


def build(history_path=LANE_HISTORY) -> Dict[str, Any]:
    history = dataio.read_jsonl(history_path)
    history = [r for r in history if r.get("version") == VERSION]
    gen = datetime.now(timezone.utc).isoformat()
    if not history:
        return {"kind": "recall_repair_shadow_forward", "version": VERSION,
                "generated_at": gen, "error": "no history yet",
                "verdict": "NEED_MORE_DATA",
                "verdict_reason": "no historized shadow-lane rows yet"}

    calendar = dataio.benchmark_calendar()
    date_to_i = {str(d)[:10]: i for i, d in enumerate(calendar)}

    # Pre-load every price series we will touch (universe + board), aligned once.
    universe_tickers = [t for t in dataio.all_price_tickers() if t not in dataio.BENCHMARKS]
    series: Dict[str, pd.Series] = {}
    vols: Dict[str, pd.Series] = {}
    for t in universe_tickers:
        df = dataio.load_prices(t)
        if df is None:
            continue
        series[t] = _aligned(df, calendar)
        if "volume" in df.columns:
            vols[t] = df["volume"].reindex(calendar).ffill()
    spy = _aligned(dataio.load_prices("SPY"), calendar)
    qqq_df = dataio.load_prices("QQQ")
    qqq = _aligned(qqq_df, calendar) if qqq_df is not None else None
    profiles = dataio.load_profiles()

    # distinct as-of dates present in history (only those on the calendar).
    asof_dates = sorted({r["asof_date"] for r in history if r.get("asof_date") in date_to_i})
    rng = random.Random(RANDOM_CONTROL_SEED)

    # Per-horizon pooled accumulators.
    pooled: Dict[int, Dict[str, List[float]]] = {
        h: {"shadow": [], "shadow_rel": [], "random": [], "random_rel": [],
            "rs_top": [], "rs_top_rel": [], "alpha": [], "alpha_rel": [],
            "spy": [], "qqq": [], "mfe": [], "mae": []}
        for h in HORIZONS
    }
    # Recall accumulators at RECALL_HORIZON, pooled across as-of dates.
    recall_acc = {thr: {"universe_winners": 0, "shadow_caught": 0,
                        "rs_top_caught": 0, "alpha_caught": 0,
                        "shadow_flagged": 0, "shadow_winners_in_board": 0}
                  for thr in WINNER_THRESHOLDS}
    n_board_matured_primary = 0
    per_asof: List[Dict[str, Any]] = []
    theme_recall = {"shadow_leading_theme_winners": 0, "alpha_leading_theme_winners": 0,
                    "leading_theme_winners_total": 0}

    for asof in asof_dates:
        asof_i = date_to_i[asof]
        board_rows = [r for r in history if r.get("asof_date") == asof]
        board_set = {r["ticker"] for r in board_rows}
        board_rank = {r["ticker"]: r.get("rank", 9999) for r in board_rows}
        board_leading_theme = {r["ticker"] for r in board_rows if r.get("theme_leader")}
        alpha_set = {r["ticker"] for r in board_rows if r.get("on_alpha_board")}

        # Recompute the liquid universe forward outcomes at this as-of.
        uni: Dict[str, Dict[str, Any]] = {}
        for t, c in series.items():
            if asof_i >= len(c) or pd.isna(c.iloc[asof_i]):
                continue
            v = vols.get(t)
            price = float(c.iloc[asof_i])
            avgvol = float(v.iloc[max(0, asof_i - 19):asof_i + 1].mean()) if v is not None else 0.0
            avgdvol = float((c * v).iloc[max(0, asof_i - 19):asof_i + 1].mean()) if v is not None else 0.0
            if not (UNIV_MIN_PRICE <= price <= UNIV_MAX_PRICE
                    and avgvol >= UNIV_MIN_AVG_VOL and avgdvol >= UNIV_MIN_AVG_DVOL):
                continue
            bars_available = int(c.iloc[:asof_i + 1].notna().sum())
            if bars_available < MIN_BARS_FOR_FEATURES:
                continue
            r20 = _ret(c, asof_i - 20, asof_i)
            sector = (profiles.get(t) or {}).get("sector") or "UNKNOWN"
            fwd = {h: _fwd_metrics(c, asof_i, h) for h in HORIZONS}
            uni[t] = {"price": price, "r20": r20, "sector": sector, "fwd": fwd}

        if not uni:
            continue
        # sector medians for sector_rs (the dumb baseline control)
        sec_r20: Dict[str, List[float]] = {}
        for t, d in uni.items():
            if d["r20"] is not None:
                sec_r20.setdefault(d["sector"], []).append(d["r20"])
        sec_med = {s: median(vv) for s, vv in sec_r20.items() if vv}
        for t, d in uni.items():
            d["sector_rs"] = (d["r20"] - sec_med.get(d["sector"], 0.0)) \
                if d["r20"] is not None else None

        spy_fwd = {h: _fwd_metrics(spy, asof_i, h) for h in HORIZONS}
        qqq_fwd = {h: _fwd_metrics(qqq, asof_i, h) for h in HORIZONS} if qqq is not None else {}

        # rs_top control = top RS_TOP_N liquid names by sector_rs at as-of.
        rs_sorted = sorted((t for t in uni if uni[t]["sector_rs"] is not None),
                           key=lambda t: uni[t]["sector_rs"], reverse=True)
        rs_top_set = set(rs_sorted[:RS_TOP_N])
        # random control = seeded sample of liquid names.
        pool = list(uni.keys())
        rand_set = set(rng.sample(pool, min(RANDOM_CONTROL_N, len(pool))))

        # ── per-horizon forward returns ──
        for h in HORIZONS:
            spy_h = (spy_fwd[h] or {}).get("fwd_end")
            qqq_h = (qqq_fwd.get(h) or {}).get("fwd_end")
            if spy_h is not None:
                pooled[h]["spy"].append(spy_h)
            if qqq_h is not None:
                pooled[h]["qqq"].append(qqq_h)

            def _collect(names, key):
                for t in names:
                    d = uni.get(t)
                    if not d:
                        continue
                    fm = d["fwd"].get(h)
                    if not fm:
                        continue
                    pooled[h][key].append(fm["fwd_end"])
                    if spy_h is not None:
                        pooled[h][key + "_rel"].append(fm["fwd_end"] - spy_h)
                    if key == "shadow":
                        pooled[h]["mfe"].append(fm["mfe"])
                        pooled[h]["mae"].append(fm["mae"])

            _collect(board_set, "shadow")
            _collect(rand_set, "random")
            _collect(rs_top_set, "rs_top")
            _collect(alpha_set, "alpha")

        # ── recall @ RECALL_HORIZON (winner = fwd MFE ≥ thr) ──
        rh = RECALL_HORIZON
        for thr in WINNER_THRESHOLDS:
            uni_winners = {t for t, d in uni.items()
                           if (d["fwd"].get(rh) or {}).get("mfe", -1) >= thr}
            recall_acc[thr]["universe_winners"] += len(uni_winners)
            recall_acc[thr]["shadow_caught"] += len(board_set & uni_winners)
            recall_acc[thr]["rs_top_caught"] += len(rs_top_set & uni_winners)
            recall_acc[thr]["alpha_caught"] += len(alpha_set & uni_winners)
            board_matured = {t for t in board_set
                             if (uni.get(t, {}).get("fwd", {}).get(rh))}
            recall_acc[thr]["shadow_flagged"] += len(board_matured)
            recall_acc[thr]["shadow_winners_in_board"] += len(board_matured & uni_winners)

        # theme-earliness: leading-theme winners caught by shadow vs alpha.
        uni_winners_20 = {t for t, d in uni.items()
                          if (d["fwd"].get(rh) or {}).get("mfe", -1) >= 0.20}
        lead_winners = uni_winners_20  # winners in the universe at +20%
        theme_recall["leading_theme_winners_total"] += len(lead_winners)
        theme_recall["shadow_leading_theme_winners"] += len(board_leading_theme & lead_winners)
        theme_recall["alpha_leading_theme_winners"] += len(alpha_set & lead_winners)

        # count primary-horizon matured board names
        n_board_matured_primary += sum(
            1 for t in board_set if (uni.get(t, {}).get("fwd", {}).get(PRIMARY_HORIZON)))

        per_asof.append({
            "asof_date": asof,
            "board_size": len(board_set),
            "universe_size": len(uni),
            "matured_primary": sum(1 for t in board_set
                                   if (uni.get(t, {}).get("fwd", {}).get(PRIMARY_HORIZON))),
            "matured_20d": sum(1 for t in board_set
                               if (uni.get(t, {}).get("fwd", {}).get(20))),
        })

    # ── aggregate per-horizon ──
    by_horizon: Dict[str, Any] = {}
    for h in HORIZONS:
        p = pooled[h]
        by_horizon[str(h)] = {
            "n_shadow": len(p["shadow"]),
            "shadow_fwd_end_avg": round(_mean(p["shadow"]), 4) if p["shadow"] else None,
            "shadow_rel_spy_avg": round(_mean(p["shadow_rel"]), 4) if p["shadow_rel"] else None,
            "shadow_win_rate": _winrate(p["shadow"]),
            "random_rel_spy_avg": round(_mean(p["random_rel"]), 4) if p["random_rel"] else None,
            "random_win_rate": _winrate(p["random"]),
            "rs_top_rel_spy_avg": round(_mean(p["rs_top_rel"]), 4) if p["rs_top_rel"] else None,
            "alpha_rel_spy_avg": round(_mean(p["alpha_rel"]), 4) if p["alpha_rel"] else None,
            "n_alpha": len(p["alpha"]),
            "spy_avg": round(_mean(p["spy"]), 4) if p["spy"] else None,
            "qqq_avg": round(_mean(p["qqq"]), 4) if p["qqq"] else None,
            "mfe_avg": round(_mean(p["mfe"]), 4) if p["mfe"] else None,
            "mae_avg": round(_mean(p["mae"]), 4) if p["mae"] else None,
            "cash": 0.0,
        }

    # ── recall / precision / FP @ RECALL_HORIZON ──
    recall_summary: Dict[str, Any] = {}
    for thr in WINNER_THRESHOLDS:
        a = recall_acc[thr]
        uw = a["universe_winners"]
        flagged = a["shadow_flagged"]
        tp = a["shadow_winners_in_board"]
        recall_summary[f"+{int(thr*100)}pct"] = {
            "universe_winners": uw,
            "shadow_recall_pct": round(100.0 * a["shadow_caught"] / uw, 2) if uw else None,
            "rs_top_recall_pct": round(100.0 * a["rs_top_caught"] / uw, 2) if uw else None,
            "alpha_recall_pct": round(100.0 * a["alpha_caught"] / uw, 2) if uw else None,
            "shadow_precision_pct": round(100.0 * tp / flagged, 2) if flagged else None,
            "shadow_false_positive_pct": round(100.0 * (flagged - tp) / flagged, 2) if flagged else None,
            "funnel_recall_benchmark_pct": FUNNEL_RECALL_PCT,
        }

    # top-decile hit rate at primary horizon (by board rank).
    # (rank is per as-of; pool top-10% ranked board names' win rate.)
    top_decile_winrate = None
    td_returns: List[float] = []
    for asof in asof_dates:
        asof_i = date_to_i[asof]
        board_rows = sorted([r for r in history if r.get("asof_date") == asof],
                            key=lambda r: r.get("rank", 9999))
        cut = max(1, len(board_rows) // 10)
        for r in board_rows[:cut]:
            c = series.get(r["ticker"])
            if c is None:
                continue
            fm = _fwd_metrics(c, asof_i, PRIMARY_HORIZON)
            if fm:
                td_returns.append(fm["fwd_end"])
    if td_returns:
        top_decile_winrate = _winrate(td_returns)

    history_days = len(asof_dates)
    mature_ticker_days = n_board_matured_primary
    verdict, reason = _verdict(by_horizon, recall_summary, theme_recall,
                               history_days, mature_ticker_days)

    return {
        "kind": "recall_repair_shadow_forward",
        "version": VERSION,
        "research_only": True,
        "generated_at": gen,
        "disclaimer": ("forward edge of the research-only recall-repair shadow board · "
                       "point-in-time returns; CURRENT-artifact cross-ref annotations "
                       "(alpha/lens/gatekeeper) are best-effort context · NO signals, NO "
                       "trade proposals, NO gate/execution/provider/DB side effects"),
        "history_path": dataio.rel_to_repo(history_path),
        "controls": {"random_seed": RANDOM_CONTROL_SEED, "random_n": RANDOM_CONTROL_N,
                     "rs_top_n": RS_TOP_N, "primary_horizon": PRIMARY_HORIZON,
                     "recall_horizon": RECALL_HORIZON},
        "history_days": history_days,
        "asof_dates": asof_dates,
        "mature_ticker_days_primary": mature_ticker_days,
        "per_asof": per_asof,
        "by_horizon": by_horizon,
        "recall_at_recall_horizon": recall_summary,
        "top_decile_win_rate_primary": top_decile_winrate,
        "theme_earliness": {
            **theme_recall,
            "shadow_leading_theme_recall_pct": round(
                100.0 * theme_recall["shadow_leading_theme_winners"]
                / theme_recall["leading_theme_winners_total"], 2)
            if theme_recall["leading_theme_winners_total"] else None,
            "alpha_leading_theme_recall_pct": round(
                100.0 * theme_recall["alpha_leading_theme_winners"]
                / theme_recall["leading_theme_winners_total"], 2)
            if theme_recall["leading_theme_winners_total"] else None,
        },
        "decision_gates": {
            "min_history_days": MIN_HISTORY_DAYS,
            "min_mature_ticker_days": MIN_MATURE_TICKER_DAYS,
            "recall_improve_factor": RECALL_IMPROVE_FACTOR,
            "history_days_met": history_days >= MIN_HISTORY_DAYS,
            "mature_ticker_days_met": mature_ticker_days >= MIN_MATURE_TICKER_DAYS,
        },
        "verdict": verdict,
        "verdict_reason": reason,
    }


def _beats(shadow: Optional[float], control: Optional[float]) -> bool:
    return shadow is not None and control is not None and shadow > control


def _verdict(by_horizon, recall_summary, theme_recall,
             history_days: int, mature_ticker_days: int) -> Tuple[str, str]:
    """Gated verdict (Task C).  Never overclaims from a small sample."""
    h5 = by_horizon.get(str(PRIMARY_HORIZON), {})
    h10 = by_horizon.get("10", {})
    beats_5 = _beats(h5.get("shadow_rel_spy_avg"), h5.get("random_rel_spy_avg"))
    beats_10 = _beats(h10.get("shadow_rel_spy_avg"), h10.get("random_rel_spy_avg"))

    rec20 = recall_summary.get("+20pct", {})
    shadow_recall = rec20.get("shadow_recall_pct")
    recall_improved = (shadow_recall is not None
                       and shadow_recall >= FUNNEL_RECALL_PCT * RECALL_IMPROVE_FACTOR)
    fp = rec20.get("shadow_false_positive_pct")
    fp_controlled = fp is not None and fp <= 92.0  # ≤ the dumb baselines' ~90%
    themes_earlier = _beats(
        theme_recall.get("shadow_leading_theme_winners"),
        theme_recall.get("alpha_leading_theme_winners"))

    # Maturity gates first — never emit a strong verdict on thin data.
    if history_days < MIN_HISTORY_DAYS or mature_ticker_days < MIN_MATURE_TICKER_DAYS:
        return ("NEED_MORE_DATA",
                f"history_days={history_days} (<{MIN_HISTORY_DAYS}) or "
                f"mature_ticker_days={mature_ticker_days} (<{MIN_MATURE_TICKER_DAYS}); "
                "metrics shown are preliminary and must not drive routing.")

    if not (beats_5 or beats_10) and not recall_improved:
        return ("NO_VALUE",
                "shadow does not beat the random control at 5d/10d and recall is "
                "not materially above the funnel benchmark.")

    strong = (beats_5 and beats_10 and recall_improved and fp_controlled)
    if strong and themes_earlier:
        return ("READY_TO_FEED_LENS_RESEARCH_ONLY",
                "beats random at 5d AND 10d, recall ≥2× funnel, FP controlled, and "
                "catches leading-theme winners earlier than the Alpha board — route "
                "candidates to Lens/Gatekeeper RESEARCH ONLY (no production routing).")
    if strong:
        return ("RECALL_EDGE_DETECTED",
                "beats random at 5d AND 10d, recall ≥2× funnel, FP controlled — a "
                "recall edge is present; confirm theme-earliness before routing.")
    return ("PROMISING_BUT_UNPROVEN",
            "partial edge (beats random at one horizon and/or recall improved) but "
            "not decisive across all gates — keep accumulating.")


# ── render ───────────────────────────────────────────────────────────────────

def _render_txt(res: Dict[str, Any]) -> List[str]:
    if res.get("error"):
        return [f"recall-repair shadow forward: {res['error']} → verdict {res.get('verdict')}"]
    L = [
        f"== RECALL-REPAIR SHADOW FORWARD ({res['version']}) — {res['generated_at']} ==",
        res["disclaimer"],
        f"history days: {res['history_days']}  ·  mature ticker-days (primary "
        f"{res['controls']['primary_horizon']}d): {res['mature_ticker_days_primary']}",
        f"as-of dates: {', '.join(res['asof_dates'])}",
        "",
        f"{'h':>4}{'n':>6}{'shadow_relSPY':>15}{'random_relSPY':>15}"
        f"{'rs_top_relSPY':>15}{'alpha_relSPY':>14}{'shadow_win%':>13}{'spy':>8}",
    ]
    for h in HORIZONS:
        d = res["by_horizon"].get(str(h), {})
        L.append(
            f"{h:>4}{d.get('n_shadow', 0):>6}"
            f"{(d.get('shadow_rel_spy_avg') or 0)*100:>14.2f}%"
            f"{(d.get('random_rel_spy_avg') or 0)*100:>14.2f}%"
            f"{(d.get('rs_top_rel_spy_avg') or 0)*100:>14.2f}%"
            f"{(d.get('alpha_rel_spy_avg') or 0)*100:>13.2f}%"
            f"{(d.get('shadow_win_rate') or 0):>12.1f}%"
            f"{(d.get('spy_avg') or 0)*100:>7.1f}%")
    L += ["", f"RECALL @ {res['controls']['recall_horizon']}d (winner = MFE ≥ thr):"]
    for thr, d in res["recall_at_recall_horizon"].items():
        L.append(
            f"  {thr:<7} universe_winners={d['universe_winners']:>4}  "
            f"shadow_recall={d['shadow_recall_pct']}%  rs_top={d['rs_top_recall_pct']}%  "
            f"alpha={d['alpha_recall_pct']}%  (funnel≈{d['funnel_recall_benchmark_pct']}%)  "
            f"prec={d['shadow_precision_pct']}%  FP={d['shadow_false_positive_pct']}%")
    te = res["theme_earliness"]
    L += ["",
          f"theme earliness: shadow leading-theme recall {te['shadow_leading_theme_recall_pct']}% "
          f"vs alpha {te['alpha_leading_theme_recall_pct']}% "
          f"(of {te['leading_theme_winners_total']} leading-theme winners)",
          f"top-decile (by rank) {res['controls']['primary_horizon']}d win rate: "
          f"{res['top_decile_win_rate_primary']}%",
          "",
          f"GATES: history_days≥{MIN_HISTORY_DAYS}={res['decision_gates']['history_days_met']}  "
          f"mature_ticker_days≥{MIN_MATURE_TICKER_DAYS}="
          f"{res['decision_gates']['mature_ticker_days_met']}",
          "",
          f"VERDICT: {res['verdict']}",
          f"  {res['verdict_reason']}"]
    return L


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Recall-Repair Shadow forward study (research-only)")
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    res = build()
    lines = _render_txt(res)
    if args.dry_run:
        print("\n".join(lines))
        print("\n[dry-run] no files written")
        return 0
    dataio.write_json(FWD_JSON, res)
    dataio.write_text(FWD_TXT, lines)
    if args.print:
        print("\n".join(lines))
    print(f"\nwrote {dataio.rel_to_repo(FWD_JSON)} · {dataio.rel_to_repo(FWD_TXT)} · "
          f"verdict {res['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
