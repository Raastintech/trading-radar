"""
research/theme_leadership_radar.py — Phase 1G.6 Task 2.

A LIVE, forward-looking theme/sector leadership radar. Where
``research/scanner_truth/theme_audit.py`` looks BACKWARD (which themes held past
winners and were they on the board), this radar looks at TODAY: it clusters the
liquid universe by theme and measures each cluster's current strength so an
operator can see which clusters are leading, emerging, extended, or fading —
independent of whether any scanner emitted them.

Per theme it reports breadth, returns, volume expansion, relative strength vs
SPY, and coverage by the three discovery surfaces (Alpha board / RS recall lane /
Stock Lens), then assigns a state:
  EMERGING       — RS turning positive, breadth building, not yet at highs
  LEADING        — broad positive RS + members at/near highs, trend intact
  EXTENDED       — strong but median member blown off far above MA20/50
  FADING         — prior strength (60d) now rolling over (20d negative / < MA20)
  NOT_CONFIRMED  — too few liquid members or no coherent strength

Outputs:
  cache/research/theme_leadership_latest.json
  logs/theme_leadership_latest.txt

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes, NO trade signals.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from statistics import mean, median
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd

from research.scanner_truth import dataio
from research.scanner_truth.filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL,
                                            UNIV_MIN_AVG_VOL, UNIV_MIN_PRICE, sma)

ARTIFACT_VERSION = "1g7.1"
HISTORY_PATH = dataio.HISTORY_DIR / "theme_leadership_history.jsonl"
FWD_HORIZON = 10           # trading days for theme continuation check
MIN_MEMBERS = 4            # a theme needs at least this many liquid members to confirm
LEADER_RS = 0.10          # 20d excess return vs SPY for a "leader"
NEAR_HIGH = 0.97
EXTENDED_MA20 = 0.20


def _aligned(df: pd.DataFrame, cal) -> pd.Series:
    c = df["close"].reindex(cal)
    fv = c.first_valid_index()
    if fv is not None:
        c.loc[c.index >= fv] = c.loc[c.index >= fv].ffill()
    return c


def _ticker_features(df: pd.DataFrame, cal, i: int, spy: pd.Series) -> Optional[Dict]:
    c = _aligned(df, cal)
    if i < 60 or pd.isna(c.iloc[i]):
        return None
    vol = df["volume"].reindex(cal).ffill()
    price = float(c.iloc[i])
    avgvol = float(vol.iloc[i - 19:i + 1].mean())
    avgdvol = float((c * vol).iloc[i - 19:i + 1].mean())
    if not (UNIV_MIN_PRICE <= price <= UNIV_MAX_PRICE
            and avgvol >= UNIV_MIN_AVG_VOL and avgdvol >= UNIV_MIN_AVG_DVOL):
        return None

    def ret(n):
        a = c.iloc[i - n]
        return float(c.iloc[i] / a - 1.0) if (not pd.isna(a) and a > 0) else None
    r20, r40, r60 = ret(20), ret(40), ret(60)
    spy20 = float(spy.iloc[i] / spy.iloc[i - 20] - 1.0) if not pd.isna(spy.iloc[i - 20]) else None
    rs20 = (r20 - spy20) if (r20 is not None and spy20 is not None) else None
    high20 = float(c.iloc[i - 19:i + 1].max())
    ma20 = float(sma(c.iloc[:i + 1], 20).iloc[-1])
    ma50 = float(sma(c.iloc[:i + 1], 50).iloc[-1])
    ext20 = (price - ma20) / ma20 if ma20 else None
    vol_exp = (avgvol / float(vol.iloc[i - 39:i - 19].mean())
               if i >= 39 and vol.iloc[i - 39:i - 19].mean() else None)
    return {
        "r20": r20, "r40": r40, "r60": r60, "rs20": rs20,
        "near_high": price >= high20 * NEAR_HIGH,
        "above_ma20": price >= ma20 if ma20 else False,
        "ext_ma20": ext20, "vol_expansion": vol_exp,
    }


def _state(members: List[Dict]) -> str:
    if len(members) < MIN_MEMBERS:
        return "NOT_CONFIRMED"
    rs = [m["rs20"] for m in members if m["rs20"] is not None]
    r20 = [m["r20"] for m in members if m["r20"] is not None]
    r60 = [m["r60"] for m in members if m["r60"] is not None]
    ext = [m["ext_ma20"] for m in members if m["ext_ma20"] is not None]
    if not rs or not r20:
        return "NOT_CONFIRMED"
    med_rs, med_r20 = median(rs), median(r20)
    med_r60 = median(r60) if r60 else 0.0
    med_ext = median(ext) if ext else 0.0
    near = sum(1 for m in members if m["near_high"]) / len(members)
    # FADING: was strong over 60d, now rolling over.
    if med_r60 >= 0.15 and med_r20 < 0:
        return "FADING"
    # EXTENDED: strong but median member blown off above MA20.
    if med_rs >= LEADER_RS and med_ext > EXTENDED_MA20:
        return "EXTENDED"
    # LEADING: broad positive RS and a real share at/near highs.
    if med_rs >= LEADER_RS and near >= 0.40:
        return "LEADING"
    # EMERGING: RS turning positive, not yet broadly at highs.
    if med_rs > 0 and med_r20 > 0:
        return "EMERGING"
    return "NOT_CONFIRMED"


def build() -> Dict:
    cal = dataio.benchmark_calendar()
    i = len(cal) - 1
    profiles = dataio.load_profiles()
    spy = _aligned(dataio.load_prices("SPY"), cal)

    # discovery-surface coverage sets
    board: Set[str] = set()
    try:
        b = json.loads((dataio.RESEARCH_CACHE / "alpha_discovery_board_latest.json").read_text())
        board = {(it.get("ticker") or it.get("symbol") or "").upper()
                 for it in b.get("items", []) if (it.get("ticker") or it.get("symbol"))}
    except Exception:
        pass
    rs_flagged: Set[str] = set()
    try:
        rl = json.loads((dataio.RESEARCH_CACHE / "rs_recall_lane_latest.json").read_text())
        rs_flagged = {x["ticker"].upper() for x in rl.get("live", {}).get("top_rs_leaders", [])}
    except Exception:
        pass
    lens = {p.name[len("stock_lens_"):-len("_latest.json")].upper()
            for p in dataio.RESEARCH_CACHE.glob("stock_lens_*_latest.json")}

    by_theme: Dict[str, List[Dict]] = {}
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = dataio.load_prices(t)
        if df is None:
            continue
        f = _ticker_features(df, cal, i, spy)
        if f is None:
            continue
        theme = dataio.classify_theme(profiles.get(t))
        f["ticker"] = t
        by_theme.setdefault(theme, []).append(f)

    themes: Dict[str, Dict] = {}
    for theme, members in by_theme.items():
        if theme in ("unknown",):
            continue
        members_sorted = sorted(members, key=lambda m: -(m["rs20"] or -9))
        r20s = [m["r20"] for m in members if m["r20"] is not None]
        rs20s = [m["rs20"] for m in members if m["rs20"] is not None]
        volx = [m["vol_expansion"] for m in members if m["vol_expansion"] is not None]
        leaders = [m["ticker"] for m in members_sorted[:8]]
        breadth_strong = sum(1 for m in members if (m["rs20"] or -9) >= LEADER_RS)
        themes[theme] = {
            "member_tickers": [m["ticker"] for m in members_sorted],
            "n_members": len(members),
            "top_leaders": leaders,
            "median_r20": round(median(r20s), 4) if r20s else None,
            "top_r20": round(max(r20s), 4) if r20s else None,
            "median_rs20_vs_spy": round(median(rs20s), 4) if rs20s else None,
            "breadth_count_rs_leaders": breadth_strong,
            "median_vol_expansion": round(median(volx), 3) if volx else None,
            "covered_by_alpha_board": sorted(set(leaders) & board),
            "covered_by_rs_lane": sorted(set(leaders) & rs_flagged),
            "lens_for_leaders": sorted(set(leaders) & lens),
            "theme_state": _state(members),
        }
    # sort by state priority then breadth
    order = {"LEADING": 0, "EMERGING": 1, "EXTENDED": 2, "FADING": 3, "NOT_CONFIRMED": 4}
    themes = dict(sorted(themes.items(),
                         key=lambda kv: (order.get(kv[1]["theme_state"], 9),
                                         -kv[1]["breadth_count_rs_leaders"])))
    leading = [t for t, d in themes.items() if d["theme_state"] == "LEADING"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asof_date": str(cal[i])[:10],
        "disclaimer": "DESCRIPTIVE RADAR — cluster strength only, not a buy signal.",
        "classifier_limitation": (
            "Themes are derived from the coarse FMP industry/name taxonomy; memory & "
            "AI-hardware names mostly fold into semiconductors/hardware. Member counts "
            "for those clusters are lower bounds (see scanner_truth/dataio.classify_theme)."),
        "leading_themes": leading,
        "themes": themes,
    }


def historize(res: Dict) -> Dict:
    """Append one row per theme to the forward-evidence JSONL. Idempotent per
    as-of date (append-only; never rewrites). No signals, no execution."""
    asof = res["asof_date"]
    existing = dataio.read_jsonl(HISTORY_PATH)
    if any(r.get("asof_date") == asof for r in existing):
        return {"asof_date": asof, "rows_written": 0, "already_present": True,
                "history_total_rows": len(existing),
                "history_path": dataio.rel_to_repo(HISTORY_PATH)}
    rows = []
    for theme, d in res["themes"].items():
        rows.append({
            "generated_at": res["generated_at"], "asof_date": asof, "theme": theme,
            "theme_state": d["theme_state"], "n_members": d["n_members"],
            "breadth_count_rs_leaders": d["breadth_count_rs_leaders"],
            "median_r20": d["median_r20"], "median_rs20_vs_spy": d["median_rs20_vs_spy"],
            "median_vol_expansion": d["median_vol_expansion"],
            "top_leaders": d["top_leaders"],
            "n_covered_by_alpha_board": len(d["covered_by_alpha_board"]),
            "n_covered_by_rs_lane": len(d["covered_by_rs_lane"]),
            "artifact_version": ARTIFACT_VERSION,
        })
    written = dataio.append_jsonl(HISTORY_PATH, rows)
    return {"asof_date": asof, "rows_written": written, "already_present": False,
            "history_total_rows": len(existing) + written,
            "history_path": dataio.rel_to_repo(HISTORY_PATH)}


def forward_check() -> Dict:
    """Read prior theme snapshots and, for matured ones, ask: did LEADING/EMERGING
    themes continue, and did their top leaders outperform SPY over FWD_HORIZON?
    Returns NEED_MORE_DATA until enough snapshots have matured."""
    hist = dataio.read_jsonl(HISTORY_PATH)
    cal = dataio.benchmark_calendar()
    spy = _aligned(dataio.load_prices("SPY"), cal)
    by_date: Dict[str, List[Dict]] = {}
    for r in hist:
        by_date.setdefault(r.get("asof_date"), []).append(r)
    dates = sorted(by_date)
    matured = []
    for asof in dates:
        ts = pd.Timestamp(asof)
        locs = np.where(cal <= ts)[0]
        if not len(locs):
            continue
        i = int(locs[-1])
        if i + FWD_HORIZON >= len(cal):
            continue                            # immature
        spy_ret = float(spy.iloc[i + FWD_HORIZON] / spy.iloc[i] - 1.0) if spy.iloc[i] else None
        for r in by_date[asof]:
            if r.get("theme_state") not in ("LEADING", "EMERGING"):
                continue
            rels = []
            for t in r.get("top_leaders", []):
                df = dataio.load_prices(t)
                if df is None:
                    continue
                c = _aligned(df, cal)
                if pd.isna(c.iloc[i]) or c.iloc[i] <= 0:
                    continue
                fwd = float(c.iloc[i + FWD_HORIZON] / c.iloc[i] - 1.0)
                if spy_ret is not None:
                    rels.append(fwd - spy_ret)
            # continuation: state still leading in a later snapshot
            later = [s for d2 in dates if d2 > asof for s in by_date[d2]
                     if s["theme"] == r["theme"]]
            continued = any(s.get("theme_state") in ("LEADING", "EMERGING") for s in later) \
                if later else None
            matured.append({
                "asof_date": asof, "theme": r["theme"], "state": r["theme_state"],
                "leaders_mean_excess_spy": round(mean(rels), 4) if rels else None,
                "continued_leading": continued,
            })
    verdict = "NEED_MORE_DATA" if len(matured) < 10 else "HAS_DATA"
    return {"matured_observations": len(matured), "horizon_td": FWD_HORIZON,
            "verdict": verdict, "observations": matured[:50]}


def _render_txt(res: Dict) -> List[str]:
    L = [
        f"== THEME LEADERSHIP RADAR ({res['generated_at']}) ==",
        res["disclaimer"],
        f"as-of {res['asof_date']}  ·  leading: {', '.join(res['leading_themes']) or '(none)'}",
        "",
        f"{'theme':<20}{'state':<14}{'n':>4}{'medR20':>8}{'medRS':>8}{'breadth':>8}  coverage(board/rs/lens)",
    ]
    for th, d in res["themes"].items():
        mr = f"{d['median_r20']*100:.0f}%" if d["median_r20"] is not None else "—"
        rs = f"{d['median_rs20_vs_spy']*100:.0f}%" if d["median_rs20_vs_spy"] is not None else "—"
        cov = f"{len(d['covered_by_alpha_board'])}/{len(d['covered_by_rs_lane'])}/{len(d['lens_for_leaders'])}"
        L.append(f"{th:<20}{d['theme_state']:<14}{d['n_members']:>4}{mr:>8}{rs:>8}"
                 f"{d['breadth_count_rs_leaders']:>8}  {cov}  [{', '.join(d['top_leaders'][:5])}]")
    L += ["", "classifier limitation: " + res["classifier_limitation"]]
    return L


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Theme leadership radar (research-only).")
    ap.add_argument("--historize", action="store_true",
                    help="append today's theme snapshots + run the forward-continuation check")
    args = ap.parse_args(argv)

    res = build()
    if args.historize:
        res["historize"] = historize(res)
        res["forward_check"] = forward_check()
    dataio.write_json(dataio.RESEARCH_CACHE / "theme_leadership_latest.json", res)
    lines = _render_txt(res)
    if "historize" in res:
        h, fc = res["historize"], res["forward_check"]
        lines += ["", f"HISTORIZED: {h['rows_written']} theme rows (asof {h['asof_date']}, "
                  f"already_present={h['already_present']}, total {h['history_total_rows']}) "
                  f"→ {h['history_path']}",
                  f"FORWARD CHECK: {fc['matured_observations']} matured obs "
                  f"({fc['horizon_td']}td) · verdict {fc['verdict']}"]
    dataio.write_text(dataio.LOGS_DIR / "theme_leadership_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
