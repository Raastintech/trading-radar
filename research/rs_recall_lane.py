"""
research/rs_recall_lane.py — Phase 1G.6 Task 2.

A simple, high-RECALL discovery lane that runs alongside Alpha Discovery but
creates NO trades, NO paper signals, and is NOT a registered strategy. Its job
is to surface relative-strength / momentum leaders early; precision is left to
the downstream Stock Lens / Gatekeeper / risk layers.

Per liquid ticker (as-of a date, using ONLY bars ≤ as-of — no look-ahead) it
computes 20d RS vs SPY/QQQ, 40/60d momentum, proximity to 20/50d highs, volume
expansion, sector-relative strength, and MA20/50 extension, then assigns one
label:
  RS_LEADER          — strong RS, at/near highs, trend intact, not blown off
  RS_EMERGING        — positive RS building, not yet at highs
  RS_EXTENDED        — strong RS but far above MA20/50 (MARKED, not discarded)
  RS_PULLBACK_WATCH  — prior strength now pulled back toward MA20/50
  RS_NO_EDGE         — fails RS/momentum criteria

Outputs:
  cache/research/rs_recall_lane_latest.json   (live candidates + backtest)
  logs/rs_recall_lane_latest.txt

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes, no signals.
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.scanner_truth import dataio
from research.scanner_truth.filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL,
                                            UNIV_MIN_AVG_VOL, UNIV_MIN_PRICE, sma)

ARTIFACT_VERSION = "1g7.1"
HISTORY_PATH = dataio.HISTORY_DIR / "rs_recall_lane_history.jsonl"
HISTORIZE_CAP_DEFAULT = 20            # top-N ranked picks appended per run

BACKTEST_HORIZON = 60
WINNER_FWD = 0.50
LEADER_RS = 0.10           # 20d excess return vs SPY
EMERGING_RS = 0.03
NEAR_HIGH = 0.97           # within 3% of trailing high
EXTENDED_MA20 = 0.20       # >20% above MA20 ⇒ extended
PULLBACK_BAND = (-0.10, 0.03)  # near MA20 from below = pullback watch


def _aligned(df: pd.DataFrame, cal) -> pd.Series:
    c = df["close"].reindex(cal)
    fv = c.first_valid_index()
    if fv is not None:
        c.loc[c.index >= fv] = c.loc[c.index >= fv].ffill()
    return c


def _features(df: pd.DataFrame, cal, i: int, spy: pd.Series, qqq: pd.Series) -> Optional[Dict]:
    c = _aligned(df, cal)
    if pd.isna(c.iloc[i]) or i < 60:
        return None
    vol = df["volume"].reindex(cal).ffill()
    price = float(c.iloc[i])
    avgvol = float(vol.iloc[i - 19:i + 1].mean())
    avgdvol = float((c * vol).iloc[i - 19:i + 1].mean())
    if not (UNIV_MIN_PRICE <= price <= UNIV_MAX_PRICE
            and avgvol >= UNIV_MIN_AVG_VOL and avgdvol >= UNIV_MIN_AVG_DVOL):
        return None

    def ret(s, n):
        a = s.iloc[i - n]
        return float(s.iloc[i] / a - 1.0) if (not pd.isna(a) and a > 0) else None
    r20, r40, r60 = ret(c, 20), ret(c, 40), ret(c, 60)
    spy20 = float(spy.iloc[i] / spy.iloc[i - 20] - 1.0) if not pd.isna(spy.iloc[i - 20]) else None
    rs20 = (r20 - spy20) if (r20 is not None and spy20 is not None) else None
    high20 = float(c.iloc[i - 19:i + 1].max())
    high50 = float(c.iloc[i - 49:i + 1].max())
    ma20 = float(sma(c.iloc[:i + 1], 20).iloc[-1])
    ma50 = float(sma(c.iloc[:i + 1], 50).iloc[-1])
    ext20 = (price - ma20) / ma20 if ma20 else None
    ext50 = (price - ma50) / ma50 if ma50 else None
    vol_exp = (avgvol / float(vol.iloc[i - 39:i - 19].mean())
               if i >= 39 and vol.iloc[i - 39:i - 19].mean() else None)
    return {
        "price": price, "avg_dvol_20": round(avgdvol, 0),
        "r20": r20, "r40": r40, "r60": r60, "rs20": rs20,
        "near_20d_high": price >= high20 * NEAR_HIGH,
        "at_20d_high": price >= high20 * 0.999,
        "near_50d_high": price >= high50 * NEAR_HIGH,
        "ext_ma20": ext20, "ext_ma50": ext50, "vol_expansion": vol_exp,
    }


def _label(f: Dict, sector_rs: Optional[float]) -> str:
    rs20 = f["rs20"] or -1
    r40, r60 = (f["r40"] or 0), (f["r60"] or 0)
    ext20 = f["ext_ma20"]
    strong = rs20 >= LEADER_RS or (sector_rs is not None and sector_rs >= LEADER_RS)
    # Extended: strong but blown off far above MA20.
    if strong and ext20 is not None and ext20 > EXTENDED_MA20 and not f["near_20d_high"]:
        return "RS_EXTENDED"
    if strong and f["near_20d_high"] and r40 > 0 and r60 > 0:
        if ext20 is not None and ext20 > EXTENDED_MA20:
            return "RS_EXTENDED"
        return "RS_LEADER"
    # Pullback watch: had momentum (r60 strong) but now near/below MA20.
    if r60 >= 0.15 and ext20 is not None and PULLBACK_BAND[0] <= ext20 <= PULLBACK_BAND[1]:
        return "RS_PULLBACK_WATCH"
    if rs20 >= EMERGING_RS and r40 > 0:
        return "RS_EMERGING"
    return "RS_NO_EDGE"


BUYABLE_LABELS = {"RS_LEADER", "RS_EMERGING", "RS_PULLBACK_WATCH"}
ALL_FLAGGED = BUYABLE_LABELS | {"RS_EXTENDED"}


def _scan(i: int, cal, profiles) -> Dict[str, Tuple[str, Dict]]:
    spy = _aligned(dataio.load_prices("SPY"), cal)
    qqq = _aligned(dataio.load_prices("QQQ"), cal)
    raw: Dict[str, Dict] = {}
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = dataio.load_prices(t)
        if df is None:
            continue
        f = _features(df, cal, i, spy, qqq)
        if f:
            raw[t] = f
    # sector medians for sector-RS
    sec_r20: Dict[str, List[float]] = {}
    for t, f in raw.items():
        sec = (profiles.get(t) or {}).get("sector") or "UNKNOWN"
        if f["r20"] is not None:
            sec_r20.setdefault(sec, []).append(f["r20"])
    sec_med = {s: median(v) for s, v in sec_r20.items() if v}
    out: Dict[str, Tuple[str, Dict]] = {}
    for t, f in raw.items():
        sec = (profiles.get(t) or {}).get("sector") or "UNKNOWN"
        srs = (f["r20"] - sec_med[sec]) if (f["r20"] is not None and sec in sec_med) else None
        f["sector_rs"] = round(srs, 4) if srs is not None else None
        out[t] = (_label(f, srs), f)
    return out


def _backtest(cal, profiles) -> Dict:
    if len(cal) <= BACKTEST_HORIZON + 60:
        return {"error": "calendar too short"}
    i = len(cal) - BACKTEST_HORIZON - 1
    scan = _scan(i, cal, profiles)
    # forward outcomes
    winners, all_liq = set(), set(scan)
    reach = {0.20: set(), 0.30: set(), 0.50: set()}
    for t in scan:
        df = dataio.load_prices(t)
        c = _aligned(df, cal)
        p0 = c.iloc[i]
        fwd_max = float(np.nanmax(c.iloc[i:].values) / p0 - 1.0) if p0 else 0
        if fwd_max >= WINNER_FWD:
            winners.add(t)
        for lvl in reach:
            if fwd_max >= lvl:
                reach[lvl].add(t)
    flagged = {t for t, (lab, _) in scan.items() if lab in ALL_FLAGGED}
    buyable = {t for t, (lab, _) in scan.items() if lab in BUYABLE_LABELS}
    tp = flagged & winners

    def _recall(num, den):
        return round(100.0 * len(num) / len(den), 1) if den else None

    # random liquid control: same number of picks as `flagged`, recall expectation
    rng = random.Random(42)
    ctrl = set(rng.sample(sorted(all_liq), min(len(flagged), len(all_liq)))) if all_liq else set()
    # alpha board overlap
    try:
        board = {(it.get("ticker") or it.get("symbol") or "").upper()
                 for it in json.loads((dataio.RESEARCH_CACHE / "alpha_discovery_board_latest.json").read_text()).get("items", [])}
    except Exception:
        board = set()
    return {
        "asof_date": str(cal[i])[:10], "horizon_td": BACKTEST_HORIZON,
        "n_liquid": len(all_liq), "n_forward_winners": len(winners),
        "n_flagged": len(flagged), "n_buyable": len(buyable),
        "recall_pct": _recall(tp, winners),
        "buyable_recall_pct": _recall(buyable & winners, winners),
        "precision_pct": _recall(tp, flagged),
        "false_positive_pct": round(100.0 * (len(flagged) - len(tp)) / len(flagged), 1) if flagged else None,
        "recall_before_20pct": _recall(flagged & reach[0.20], reach[0.20]),
        "recall_before_30pct": _recall(flagged & reach[0.30], reach[0.30]),
        "recall_before_50pct": _recall(flagged & reach[0.50], reach[0.50]),
        "random_control_recall_pct": _recall(ctrl & winners, winners),
        "alpha_board_overlap": sorted(flagged & board)[:30],
        "alpha_board_overlap_n": len(flagged & board),
    }


def _live(cal, profiles) -> Dict:
    i = len(cal) - 1
    scan = _scan(i, cal, profiles)
    by_label: Dict[str, int] = {}
    for _, (lab, _f) in scan.items():
        by_label[lab] = by_label.get(lab, 0) + 1
    leaders = sorted(
        [(t, f) for t, (lab, f) in scan.items() if lab == "RS_LEADER"],
        key=lambda x: -(x[1]["rs20"] or 0))[:25]
    return {
        "asof_date": str(cal[i])[:10],
        "label_counts": dict(sorted(by_label.items(), key=lambda x: -x[1])),
        "candidates_per_day": sum(v for k, v in by_label.items() if k != "RS_NO_EDGE"),
        "top_rs_leaders": [{
            "ticker": t, "rs20": round(f["rs20"], 3) if f["rs20"] is not None else None,
            "r40": round(f["r40"], 3) if f["r40"] is not None else None,
            "sector": (profiles.get(t) or {}).get("sector"),
            "theme": dataio.classify_theme(profiles.get(t)),
        } for t, f in leaders],
    }


def _extension_state(ext20: Optional[float]) -> str:
    if ext20 is None:
        return "unknown"
    if ext20 > EXTENDED_MA20:
        return "extended"
    if ext20 < PULLBACK_BAND[1]:
        return "near_or_below_ma20"
    return "normal"


def _ranked_live(cal, profiles, cap: int) -> List[Dict]:
    """Top-`cap` buyable RS candidates as-of the last bar, ranked by 20d RS.
    Uses ONLY bars ≤ as-of (no look-ahead) — same _scan path as the live view."""
    i = len(cal) - 1
    asof = str(cal[i])[:10]
    scan = _scan(i, cal, profiles)
    rows: List[Tuple[float, str, str, Dict]] = []
    for t, (label, f) in scan.items():
        if label in BUYABLE_LABELS:
            rows.append((f["rs20"] if f["rs20"] is not None else -9, t, label, f))
    rows.sort(key=lambda x: -x[0])
    out: List[Dict] = []
    for rank, (rs, t, label, f) in enumerate(rows[:cap], start=1):
        out.append({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "asof_date": asof,
            "ticker": t,
            "rank": rank,
            "rs_score": round(rs, 4),
            "label": label,
            "rs20_vs_spy": round(f["rs20"], 4) if f["rs20"] is not None else None,
            "momentum_40d": round(f["r40"], 4) if f["r40"] is not None else None,
            "momentum_60d": round(f["r60"], 4) if f["r60"] is not None else None,
            "volume_expansion": round(f["vol_expansion"], 3) if f["vol_expansion"] is not None else None,
            "sector": (profiles.get(t) or {}).get("sector"),
            "theme": dataio.classify_theme(profiles.get(t)),
            "extension_state": _extension_state(f["ext_ma20"]),
            "price": round(f["price"], 4),
            "artifact_version": ARTIFACT_VERSION,
        })
    return out


def historize(cap: int = HISTORIZE_CAP_DEFAULT) -> Dict:
    """Append today's top-`cap` ranked RS picks to the forward-evidence JSONL.
    Idempotent per as-of date: if rows for today's as-of already exist, skips
    (append-only — never rewrites prior history). NO paper signals, NO strategy
    registration, NO execution. Pure forward-evidence capture."""
    cap = max(1, min(int(cap), 25))                  # doctrine: top 15–25 only
    cal = dataio.benchmark_calendar()
    profiles = dataio.load_profiles()
    rows = _ranked_live(cal, profiles, cap)
    asof = rows[0]["asof_date"] if rows else str(cal[-1])[:10]
    existing = dataio.read_jsonl(HISTORY_PATH)
    already = any(r.get("asof_date") == asof for r in existing)
    written = 0
    if not already and rows:
        written = dataio.append_jsonl(HISTORY_PATH, rows)
    return {"asof_date": asof, "cap": cap, "rows_written": written,
            "already_present": already, "history_total_rows": len(existing) + written,
            "history_path": dataio.rel_to_repo(HISTORY_PATH)}


def build() -> Dict:
    cal = dataio.benchmark_calendar()
    profiles = dataio.load_profiles()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "DISCOVERY LANE — not a buy signal, not a paper signal, not a strategy.",
        "live": _live(cal, profiles),
        "backtest": _backtest(cal, profiles),
    }


def _render_txt(res: Dict) -> List[str]:
    lv, bt = res["live"], res["backtest"]
    L = [
        f"== RS RECALL LANE ({res['generated_at']}) ==",
        res["disclaimer"], "",
        f"LIVE ({lv['asof_date']}): " + ", ".join(f"{k}={v}" for k, v in lv["label_counts"].items()),
        f"candidates/day (non-NO_EDGE): {lv['candidates_per_day']}",
        "",
        "BACKTEST (as-of " + bt.get("asof_date", "?") + f", {bt.get('horizon_td')}td fwd):",
        f"  liquid={bt.get('n_liquid')}  fwd_winners={bt.get('n_forward_winners')}  flagged={bt.get('n_flagged')}",
        f"  recall={bt.get('recall_pct')}%  buyable_recall={bt.get('buyable_recall_pct')}%  "
        f"precision={bt.get('precision_pct')}%  FP={bt.get('false_positive_pct')}%",
        f"  recall before +20/+30/+50: {bt.get('recall_before_20pct')}% / "
        f"{bt.get('recall_before_30pct')}% / {bt.get('recall_before_50pct')}%",
        f"  random-control recall={bt.get('random_control_recall_pct')}%  "
        f"alpha-board overlap={bt.get('alpha_board_overlap_n')}",
        "",
        "TOP CURRENT RS LEADERS:",
    ]
    for x in lv["top_rs_leaders"][:15]:
        L.append(f"  {x['ticker']:<7} rs20={x['rs20']}  r40={x['r40']}  {x['theme']}/{x['sector']}")
    return L


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RS recall lane (research-only).")
    ap.add_argument("--historize", action="store_true",
                    help="append today's top-N ranked picks to the forward-evidence JSONL")
    ap.add_argument("--cap", type=int, default=HISTORIZE_CAP_DEFAULT,
                    help="number of top ranked picks to historize (15–25)")
    args = ap.parse_args(argv)

    res = build()
    if args.historize:
        res["historize"] = historize(cap=args.cap)
    dataio.write_json(dataio.RESEARCH_CACHE / "rs_recall_lane_latest.json", res)
    lines = _render_txt(res)
    if "historize" in res:
        h = res["historize"]
        lines += ["", f"HISTORIZED: {h['rows_written']} rows (asof {h['asof_date']}, "
                  f"cap {h['cap']}, already_present={h['already_present']}, "
                  f"total {h['history_total_rows']}) → {h['history_path']}"]
    dataio.write_text(dataio.LOGS_DIR / "rs_recall_lane_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
