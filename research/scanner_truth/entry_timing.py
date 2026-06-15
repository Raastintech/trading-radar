"""
research/scanner_truth/entry_timing.py — TASK 8 (entry-state timing audit).

The live entry-validator states (too_extended / watch_reclaim / research_ready /
actionable / blocked) are NOT historized, so we reconstruct the PRICE-DERIVED
entry window honestly: for each top winner, when did a "buyable" Voyager-style
zone exist (near MA50, not yet >12% extended, above the MA200 floor where
computable), and when did it first become too_extended? That answers the core
question — did a clean early entry exist before the run, and how late was the
system relative to it?

MA50-based extension is reliable for ~110-bar names; the MA200 floor needs 200
bars and is flagged ``ma200_reliable=false`` where the cache is too shallow
(then the buyable test uses extension only, disclosed).

Output:
  cache/research/scanner_entry_timing_latest.json
  logs/scanner_entry_timing_latest.txt
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import dataio
from .filters import VOY_MA200_FLOOR, VOY_MAX_EXTENSION_MA50, sma

BUYABLE_LOW = -0.08      # not more than 8% below MA50
BUYABLE_HIGH = VOY_MAX_EXTENSION_MA50   # not more than 12% above MA50


def _timeline(df: pd.DataFrame, calendar) -> Dict:
    c = df["close"].reindex(calendar)
    fv = c.first_valid_index()
    if fv is not None:
        c.loc[c.index >= fv] = c.loc[c.index >= fv].ffill()
    ma50 = sma(c, 50)
    ma200 = sma(c, 200)
    ext = (c - ma50) / ma50
    bars = int(len(df))
    ma200_reliable = bars >= 200
    above_floor = (c / ma200 >= VOY_MA200_FLOOR) if ma200_reliable else pd.Series(True, index=c.index)
    buyable = (ext >= BUYABLE_LOW) & (ext <= BUYABLE_HIGH) & above_floor
    too_ext = ext > VOY_MAX_EXTENSION_MA50

    def _first(mask) -> Optional[str]:
        m = mask[mask.fillna(False)]
        return str(m.index[0])[:10] if len(m) else None

    first_buyable = _first(buyable)
    first_too_ext = _first(too_ext)
    # entry window width (buyable → too_extended), in calendar days
    window_days = None
    if first_buyable and first_too_ext and first_too_ext > first_buyable:
        window_days = (datetime.fromisoformat(first_too_ext)
                       - datetime.fromisoformat(first_buyable)).days
    return {
        "first_buyable_date": first_buyable,
        "first_too_extended_date": first_too_ext,
        "buyable_before_extended": bool(first_buyable and (not first_too_ext or first_buyable <= first_too_ext)),
        "entry_window_days": window_days,
        "ma200_reliable": ma200_reliable,
    }


def build(min_return: float = 0.80) -> Dict:
    uni = json.loads((dataio.RESEARCH_CACHE / "missed_winner_universe_latest.json").read_text())
    winners = [w for w in uni["winners"] if w["best_max_return"] >= min_return]
    calendar = dataio.benchmark_calendar()
    # seen dates from the funnel trace
    seen_dates = {}
    try:
        tr = json.loads((dataio.RESEARCH_CACHE / "scanner_funnel_trace_latest.json").read_text())
        seen_dates = {t["ticker"]: t["first_date_actually_saw"] for t in tr["traces"]}
    except Exception:
        pass

    timelines: List[Dict] = []
    for w in winners:
        df = dataio.load_prices(w["ticker"])
        if df is None:
            continue
        tl = _timeline(df, calendar)
        c = df["close"].reindex(calendar).ffill()
        seen = seen_dates.get(w["ticker"])
        # price move from first_buyable → first_seen (how much it ran before detection)
        move_before = None
        days_late = None
        if tl["first_buyable_date"] and seen:
            try:
                pb = float(c.loc[c.index >= tl["first_buyable_date"]].iloc[0])
                ps = float(c.loc[c.index >= seen[:10]].iloc[0])
                if pb > 0:
                    move_before = round(ps / pb - 1.0, 4)
                days_late = (datetime.fromisoformat(seen[:10])
                             - datetime.fromisoformat(tl["first_buyable_date"])).days
            except Exception:
                pass
        timelines.append({
            "ticker": w["ticker"], "theme": w["theme"],
            "best_max_return": w["best_max_return"],
            **tl,
            "first_detected_date": seen[:10] if seen else None,
            "first_detected_state": "detected" if seen else "never_detected",
            "best_possible_state": "buyable" if tl["buyable_before_extended"] else "extended_only",
            "days_late_vs_buyable": days_late,
            "price_move_before_detection": move_before,
        })

    n = len(timelines)
    had_buyable = sum(1 for t in timelines if t["buyable_before_extended"])
    windows = [t["entry_window_days"] for t in timelines if t["entry_window_days"] is not None]
    detected = [t for t in timelines if t["first_detected_state"] == "detected"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_winners": n,
        "summary": {
            "had_clean_buyable_window_before_extension": had_buyable,
            "pct_with_buyable_window": round(100.0 * had_buyable / n, 1) if n else None,
            "median_entry_window_days": median(windows) if windows else None,
            "n_detected": len(detected),
            "note": "buyable = near MA50 (−8%..+12%), above MA200 floor where computable. "
                    "A buyable window existing but no detection ⇒ the system had a clean "
                    "early entry it did not take (ENTRY_VALIDATOR/UNIVERSE gap).",
        },
        "timelines": timelines,
    }


def _render_txt(res: Dict) -> List[str]:
    s = res["summary"]
    L = [
        f"== ENTRY-STATE TIMING AUDIT ({res['generated_at']}) ==",
        f"winners: {res['n_winners']}",
        f"had clean buyable window BEFORE becoming extended: "
        f"{s['had_clean_buyable_window_before_extension']} ({s['pct_with_buyable_window']}%)",
        f"median entry-window width: {s['median_entry_window_days']} days   "
        f"detected by funnel: {s['n_detected']}",
        "",
        f"{'ticker':<7}{'maxret':>7} {'buyable_from':<13}{'too_ext_from':<13}{'detected':<11}win(d)",
    ]
    for t in res["timelines"][:25]:
        L.append(f"{t['ticker']:<7}{t['best_max_return']*100:>6.0f}% "
                 f"{str(t['first_buyable_date'] or '—'):<13}"
                 f"{str(t['first_too_extended_date'] or '—'):<13}"
                 f"{str(t['first_detected_date'] or '—'):<11}"
                 f"{t['entry_window_days'] if t['entry_window_days'] is not None else '—'}")
    return L


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "scanner_entry_timing_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "scanner_entry_timing_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
