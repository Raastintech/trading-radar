"""
research/scanner_truth/baselines.py — TASK 5 (compare scanner vs simple baselines).

Point-in-time backtest: pick an as-of date H trading days before the calendar
end. Each baseline produces a signal using ONLY bars at-or-before the as-of
date (no look-ahead); outcomes are the FORWARD return from as-of → end. We then
ask, of the names that actually ran ≥+50% forward, how many each baseline
flagged (recall), and of each baseline's flags how many became winners
(precision) — and compare against the live funnel's near-zero recall.

Baselines (all pure price/liquidity, deliberately dumb):
  1. rs_20d            — 20d return beats SPY by ≥10%
  2. high_50d_breakout — close at/above its trailing 50d high
  3. vol_strength      — 20d vol ≥1.5× prior 20d AND 20d return ≥+5%
  4. sector_rs         — 20d return beats its sector median by ≥10%
  5. mom_20_60         — 20d return ≥+10% AND 60d return ≥+20%

Output:
  cache/research/scanner_baseline_comparison_latest.json
  logs/scanner_baseline_comparison_latest.txt
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import dataio
from .filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL, UNIV_MIN_AVG_VOL,
                      UNIV_MIN_PRICE)

HORIZON = 60                 # trading days from as-of → end (the forward window)
WINNER_FWD = 0.50            # forward max-return threshold to count as a winner


def _aligned(df: pd.DataFrame, calendar) -> pd.Series:
    c = df["close"].reindex(calendar)
    fv = c.first_valid_index()
    if fv is not None:
        c.loc[c.index >= fv] = c.loc[c.index >= fv].ffill()
    return c


def _ret(s: pd.Series, i_from: int, i_to: int) -> Optional[float]:
    a, b = s.iloc[i_from], s.iloc[i_to]
    if pd.isna(a) or pd.isna(b) or a <= 0:
        return None
    return float(b / a - 1.0)


def build(horizon: int = HORIZON) -> Dict:
    calendar = dataio.benchmark_calendar()
    if len(calendar) <= horizon + 60:
        return {"error": "calendar too short for backtest"}
    asof_i = len(calendar) - horizon - 1
    asof = calendar[asof_i]
    profiles = dataio.load_profiles()
    spy = _aligned(dataio.load_prices("SPY"), calendar)
    spy_20d = _ret(spy, asof_i - 20, asof_i)

    # Gather as-of features for every liquid ticker.
    rows: List[Dict] = []
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = dataio.load_prices(t)
        if df is None:
            continue
        c = _aligned(df, calendar)
        if pd.isna(c.iloc[asof_i]):
            continue
        # liquidity as-of asof
        vol = df["volume"].reindex(calendar).ffill()
        dvol = (c * vol)
        price = float(c.iloc[asof_i])
        avgvol = float(vol.iloc[max(0, asof_i - 19):asof_i + 1].mean())
        avgdvol = float(dvol.iloc[max(0, asof_i - 19):asof_i + 1].mean())
        if not (UNIV_MIN_PRICE <= price <= UNIV_MAX_PRICE
                and avgvol >= UNIV_MIN_AVG_VOL and avgdvol >= UNIV_MIN_AVG_DVOL):
            continue
        r20 = _ret(c, asof_i - 20, asof_i)
        r60 = _ret(c, asof_i - 60, asof_i) if asof_i >= 60 else None
        high50 = float(c.iloc[max(0, asof_i - 49):asof_i + 1].max())
        vol20 = float(vol.iloc[max(0, asof_i - 19):asof_i + 1].mean())
        vol_prior20 = float(vol.iloc[max(0, asof_i - 39):asof_i - 19].mean()) if asof_i >= 39 else np.nan
        vol_ratio = vol20 / vol_prior20 if vol_prior20 else np.nan
        # forward outcome
        fwd_seg = c.iloc[asof_i:]
        fwd_max = float(np.nanmax(fwd_seg.values) / price - 1.0) if price else None
        fwd_end = _ret(c, asof_i, len(calendar) - 1)
        rows.append({
            "ticker": t, "sector": (profiles.get(t) or {}).get("sector") or "UNKNOWN",
            "price": price, "r20": r20, "r60": r60,
            "at_50d_high": price >= high50 * 0.999,
            "vol_ratio": vol_ratio, "fwd_max": fwd_max, "fwd_end": fwd_end,
            "beat_spy_20": (r20 - spy_20d) if (r20 is not None and spy_20d is not None) else None,
        })

    n_liq = len(rows)
    # sector medians (as-of) for sector_rs
    sec_r20: Dict[str, List[float]] = {}
    for r in rows:
        if r["r20"] is not None:
            sec_r20.setdefault(r["sector"], []).append(r["r20"])
    sec_med = {s: median(v) for s, v in sec_r20.items() if v}

    winners = [r for r in rows if (r["fwd_max"] or 0) >= WINNER_FWD]
    win_set = {r["ticker"] for r in winners}
    n_win = len(winners)

    def _eval(name: str, flag_fn) -> Dict:
        flagged = [r for r in rows if flag_fn(r)]
        fset = {r["ticker"] for r in flagged}
        tp = len(fset & win_set)
        fwd = [r["fwd_end"] for r in flagged if r["fwd_end"] is not None]
        return {
            "name": name,
            "n_flagged": len(flagged),
            "recall_pct": round(100.0 * tp / n_win, 1) if n_win else None,
            "precision_pct": round(100.0 * tp / len(flagged), 1) if flagged else None,
            "winners_caught": tp,
            "avg_fwd_return_of_flagged": round(float(np.mean(fwd)), 4) if fwd else None,
            "false_positive_pct": round(100.0 * (len(flagged) - tp) / len(flagged), 1) if flagged else None,
        }

    baselines = {
        "rs_20d": _eval("rs_20d", lambda r: r["beat_spy_20"] is not None and r["beat_spy_20"] >= 0.10),
        "high_50d_breakout": _eval("high_50d_breakout", lambda r: r["at_50d_high"]),
        "vol_strength": _eval("vol_strength", lambda r: (r["vol_ratio"] or 0) >= 1.5 and (r["r20"] or 0) >= 0.05),
        "sector_rs": _eval("sector_rs", lambda r: r["r20"] is not None
                           and (r["r20"] - sec_med.get(r["sector"], 0)) >= 0.10),
        "mom_20_60": _eval("mom_20_60", lambda r: (r["r20"] or 0) >= 0.10 and (r["r60"] or 0) >= 0.20),
    }

    # Funnel recall over the SAME as-of winner set (names ever in historized funnel).
    # Reuse the trace artifact's seen-set if available.
    funnel_recall = None
    try:
        import json
        tr = json.loads((dataio.RESEARCH_CACHE / "scanner_funnel_trace_latest.json").read_text())
        seen = {t["ticker"] for t in tr["traces"]
                if t["first_date_actually_saw"] or t["today_snapshot"]["in_alpha_board"]}
        tp = len(seen & win_set)
        funnel_recall = round(100.0 * tp / n_win, 1) if n_win else None
    except Exception:
        pass

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asof_date": str(asof)[:10],
        "horizon_trading_days": horizon,
        "winner_fwd_threshold": WINNER_FWD,
        "n_liquid_universe": n_liq,
        "n_forward_winners": n_win,
        "spy_20d_return_at_asof": round(spy_20d, 4) if spy_20d else None,
        "baselines": baselines,
        "funnel_recall_pct_same_set": funnel_recall,
        "verdict": _verdict(baselines, funnel_recall),
    }


def _verdict(baselines: Dict, funnel_recall: Optional[float]) -> str:
    best = max(baselines.values(), key=lambda b: (b["recall_pct"] or 0))
    if funnel_recall is None:
        return f"best baseline '{best['name']}' recall={best['recall_pct']}%; funnel recall n/a"
    if (best["recall_pct"] or 0) > (funnel_recall or 0):
        return (f"a SIMPLE baseline ('{best['name']}', recall {best['recall_pct']}%) "
                f"caught more forward winners than the live funnel ({funnel_recall}%). "
                "Sophistication did not buy recall here.")
    return f"funnel recall ({funnel_recall}%) ≥ best baseline ({best['recall_pct']}%)"


def _render_txt(res: Dict) -> List[str]:
    if "error" in res:
        return [f"baseline comparison error: {res['error']}"]
    L = [
        f"== SIMPLE-BASELINE COMPARISON ({res['generated_at']}) ==",
        f"as-of {res['asof_date']}  forward horizon {res['horizon_trading_days']}td  "
        f"winner=fwd_max≥+{int(res['winner_fwd_threshold']*100)}%",
        f"liquid universe: {res['n_liquid_universe']}   forward winners: {res['n_forward_winners']}",
        "",
        f"{'baseline':<20}{'flagged':>8}{'recall':>8}{'prec':>7}{'caught':>8}{'avgfwd':>8}",
    ]
    for b in res["baselines"].values():
        L.append(f"{b['name']:<20}{b['n_flagged']:>8}{(b['recall_pct'] or 0):>7.1f}%"
                 f"{(b['precision_pct'] or 0):>6.1f}%{b['winners_caught']:>8}"
                 f"{(b['avg_fwd_return_of_flagged'] or 0)*100:>7.0f}%")
    L += ["",
          f"funnel recall (same winner set): {res['funnel_recall_pct_same_set']}%",
          "", f"VERDICT: {res['verdict']}"]
    return L


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "scanner_baseline_comparison_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "scanner_baseline_comparison_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
