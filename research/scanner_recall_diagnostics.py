"""
research/scanner_recall_diagnostics.py — scanner recall diagnostics.

Point-in-time comparison of three candidate-selection cohorts on the SAME
liquid universe at the SAME as-of date (default: ~21 trading days back so
5/10/20d forward returns are fully resolvable, no look-ahead in selection):

  strict_scanner  — production-mirror funnel gates from scanner_truth.filters:
                    liquidity gate AND (Voyager structural OR Sniper breakout).
  rs_baseline     — deliberately simple control: top-N liquid names ranked by
                    20d return relative to SPY (positive 40d momentum required).
  loose_scanner   — the strict gates with research-loosened thresholds (wider
                    MA50 extension band, lower MA200 floor, shallower history
                    requirement, softer volume spike, 10d breakout lookback,
                    ATR-contraction and MA50-slope requirements dropped).
                    By construction a superset of strict_scanner.

Reported per run:
  • reject counts by filter — every liquid name rejected by the strict scanner,
    tallied per failing-gate code (lane-prefixed), with forward-winner losses.
  • top rejected names — strict-scanner rejects ranked by 20d forward return,
    annotated with the filters that killed them and whether the loose variant
    or the RS baseline caught them.
  • 5d/10d/20d forward results per cohort — mean/median return, win rate,
    excess vs SPY, winner recall/precision, and a seeded size-matched random
    liquid control.

Outputs:
  cache/research/scanner_recall_diagnostics_latest.json
  logs/scanner_recall_diagnostics_latest.txt

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes, no signals, no
gate changes — this is a diagnostic surface; production thresholds are NOT
modified by anything here.
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from research.scanner_truth import dataio
from research.scanner_truth.filters import (
    GateResult, liquidity_gate, sma, sniper_breakout, voyager_structural,
)

ARTIFACT_VERSION = "srd.1"
HORIZONS = (5, 10, 20)
DEFAULT_LOOKBACK_TD = 21          # as-of = last bar − 21 ⇒ 20d fwd resolvable
DEFAULT_WINNER_FWD20 = 0.10       # liquid name with fwd20 ≥ +10% = forward winner
DEFAULT_RS_CAP = 25               # RS baseline picks (mirrors ALPHA_BOARD_CAP)
DEFAULT_TOP_REJECTED = 15
RANDOM_SEED = 42
MAX_SANE_R20 = 3.0                # >+300% in 20d ⇒ data-suspect parquet, excluded

# ── Loosened thresholds (research knobs — NOT production values) ─────────────
LOOSE_VOY_BARS_NEEDED = 120       # vs 260 (known non-functional on shallow cache)
LOOSE_VOY_MA200_FLOOR = 0.88      # vs 0.92
LOOSE_VOY_MAX_EXTENSION_MA50 = 0.25   # vs 0.12
LOOSE_VOY_DVOL_TREND_RATIO = 0.70     # vs 0.85
LOOSE_SNI_BREAKOUT_LOOKBACK = 10  # vs 20
LOOSE_SNI_VOL_SPIKE_THRESH = 1.15     # vs 1.4
# Loose sniper drops the ATR-contraction and MA50-rising requirements entirely.

LOOSE_THRESHOLDS = {
    "voyager_bars_needed": LOOSE_VOY_BARS_NEEDED,
    "voyager_ma200_floor": LOOSE_VOY_MA200_FLOOR,
    "voyager_max_extension_ma50": LOOSE_VOY_MAX_EXTENSION_MA50,
    "voyager_dvol_trend_ratio": LOOSE_VOY_DVOL_TREND_RATIO,
    "sniper_breakout_lookback": LOOSE_SNI_BREAKOUT_LOOKBACK,
    "sniper_vol_spike_thresh": LOOSE_SNI_VOL_SPIKE_THRESH,
    "sniper_atr_contraction_required": False,
    "sniper_ma50_rising_required": False,
}


# ── Loose gate variants (local — shared filters.py stays production-mirror) ──

def loose_voyager(df: pd.DataFrame, asof: pd.Timestamp) -> GateResult:
    """Voyager structural gates with loosened research thresholds. With fewer
    than 200 bars the MA200 floor uses the longest available SMA instead."""
    d = df[df.index <= asof]
    reasons: List[str] = []
    metrics: Dict[str, float] = {"bars": float(len(d))}
    if len(d) < LOOSE_VOY_BARS_NEEDED:
        return GateResult(False, ["insufficient_history_120"], metrics)
    close = d["close"]
    price = float(close.iloc[-1])
    ma50 = float(sma(close, 50).iloc[-1])
    ma_long = float(sma(close, min(200, len(d))).iloc[-1])
    ext_ma50 = (price - ma50) / ma50 if ma50 else np.nan
    dist_long = price / ma_long if ma_long else np.nan
    dvol = d["close"] * d["volume"]
    dvol60 = float(dvol.tail(60).mean())
    dvol_ratio = float(dvol.tail(20).mean()) / dvol60 if dvol60 else np.nan
    metrics.update({"price": price, "ext_above_ma50": float(ext_ma50),
                    "dist_ma200_ratio": float(dist_long),
                    "dvol_trend_ratio": float(dvol_ratio)})
    if price < 5.0:
        reasons.append("price_below_min")
    if dist_long < LOOSE_VOY_MA200_FLOOR:
        reasons.append("below_ma200_floor")
    if ext_ma50 > LOOSE_VOY_MAX_EXTENSION_MA50:
        reasons.append("too_extended")
    if dvol_ratio < LOOSE_VOY_DVOL_TREND_RATIO:
        reasons.append("dvol_fading")
    return GateResult(not reasons, reasons, metrics)


def loose_sniper(df: pd.DataFrame, asof: pd.Timestamp) -> GateResult:
    """Sniper breakout with loosened research thresholds: 10d breakout, softer
    volume spike, no ATR-contraction / MA50-slope requirements."""
    d = df[df.index <= asof]
    reasons: List[str] = []
    metrics: Dict[str, float] = {"bars": float(len(d))}
    if len(d) < 75:
        return GateResult(False, ["insufficient_history_75"], metrics)
    close, high, vol = d["close"], d["high"], d["volume"]
    prior_high = float(high.iloc[-(LOOSE_SNI_BREAKOUT_LOOKBACK + 1):-1].max())
    today_close = float(close.iloc[-1])
    vol_mean = vol.tail(20).mean()
    vol_ratio = float(vol.iloc[-1] / vol_mean) if vol_mean else np.nan
    metrics.update({"today_close": today_close, "prior_10d_high": prior_high,
                    "vol_ratio": vol_ratio})
    if today_close <= prior_high:
        reasons.append("no_breakout")
    if not (vol_ratio >= LOOSE_SNI_VOL_SPIKE_THRESH):
        reasons.append("volume_insufficient")
    return GateResult(not reasons, reasons, metrics)


# ── Point-in-time world at the as-of index ───────────────────────────────────

def _load_merged(ticker: str) -> Optional[pd.DataFrame]:
    """Deep history + shallow freshness. `load_prices(prefer_deep=True)` returns
    a longer-but-stale deep parquet even when the shallow one has newer bars
    (the daily refresh only touches cache/prices); merge the two so a name is
    not counted stale when fresh bars exist."""
    pref = dataio.load_prices(ticker, prefer_deep=True)
    shallow = dataio.load_prices(ticker, prefer_deep=False)
    if pref is None or shallow is None:
        return pref if pref is not None else shallow
    if shallow.index.max() <= pref.index.max():
        return pref
    return pd.concat([pref[pref.index < shallow.index.min()], shallow])


def _aligned(df: pd.DataFrame, cal) -> pd.Series:
    c = df["close"].reindex(cal)
    fv = c.first_valid_index()
    if fv is not None:
        c.loc[c.index >= fv] = c.loc[c.index >= fv].ffill()
    return c


def _fwd_return(c: pd.Series, i: int, h: int) -> Optional[float]:
    if i + h >= len(c):
        return None
    p0, p1 = c.iloc[i], c.iloc[i + h]
    if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
        return None
    return float(p1 / p0 - 1.0)


def _mae20(c: pd.Series, i: int) -> Optional[float]:
    """Max adverse excursion: worst return vs entry over the next 20 bars."""
    p0 = c.iloc[i]
    if pd.isna(p0) or p0 <= 0:
        return None
    seg = c.iloc[i + 1:min(i + 20, len(c) - 1) + 1].dropna()
    return float(seg.min() / p0 - 1.0) if len(seg) else None


def _winsor_mean(vals: List[float], p: float = 0.05) -> Optional[float]:
    """Mean after clipping at the p / 1-p quantiles — outlier-robust."""
    if not vals:
        return None
    a = np.asarray(vals, dtype=float)
    lo, hi = np.quantile(a, [p, 1.0 - p])
    return float(np.clip(a, lo, hi).mean())


def _eval_ticker(df: pd.DataFrame, c: pd.Series, cal, i: int,
                 spy20: Optional[float],
                 last_needed: pd.Timestamp) -> Tuple[str, Optional[Dict]]:
    """Evaluate one ticker at as-of index ``i`` (selection uses bars ≤ as-of
    only; forward bars are outcome measurement). ``c`` is the calendar-aligned
    close (caller-provided so multi-date callers align once per ticker).
    Returns (status, record): status ∈ {ok, stale, suspect, skip}."""
    asof = cal[i]
    if not liquidity_gate(df, asof).passed:
        return "skip", None
    if df.index.max() < last_needed:
        return "stale", None
    if i < 60 or pd.isna(c.iloc[i]):
        return "skip", None
    r20 = (float(c.iloc[i] / c.iloc[i - 20] - 1.0)
           if not pd.isna(c.iloc[i - 20]) and c.iloc[i - 20] > 0 else None)
    if r20 is not None and r20 > MAX_SANE_R20:
        return "suspect", None
    voy, sni = voyager_structural(df, asof), sniper_breakout(df, asof)
    lvoy, lsni = loose_voyager(df, asof), loose_sniper(df, asof)
    r40 = (float(c.iloc[i] / c.iloc[i - 40] - 1.0)
           if i >= 40 and not pd.isna(c.iloc[i - 40]) and c.iloc[i - 40] > 0 else None)
    return "ok", {
        "strict_pass": voy.passed or sni.passed,
        "loose_pass": lvoy.passed or lsni.passed,
        "voyager_reasons": voy.reasons, "sniper_reasons": sni.reasons,
        "rs20": (r20 - spy20) if (r20 is not None and spy20 is not None) else None,
        "r40": r40,
        "fwd": {h: _fwd_return(c, i, h) for h in HORIZONS},
        "mae20": _mae20(c, i),
    }


def _scan_world(cal, i: int) -> Dict[str, Dict]:
    """Per liquid ticker: strict/loose gate results (bars ≤ as-of only), 20d RS
    vs SPY, and 5/10/20d forward returns (post-as-of, outcome measurement only).

    Data-quality guards (stale parquets rank as fake RS leaders and report
    fake 0% forward returns via ffill — the 2026-07-02 universe-freshness
    failure class): a ticker is excluded as `stale` when its last real bar
    does not cover the longest forward horizon, and as `data_suspect` when
    its 20d as-of return exceeds MAX_SANE_R20. Counts land in __meta__."""
    spy = _aligned(_load_merged("SPY"), cal)
    spy20 = (float(spy.iloc[i] / spy.iloc[i - 20] - 1.0)
             if i >= 20 and not pd.isna(spy.iloc[i - 20]) else None)
    spy_fwd = {h: _fwd_return(spy, i, h) for h in HORIZONS}
    world: Dict[str, Dict] = {"__spy__": {"fwd": spy_fwd, "r20": spy20},
                              "__meta__": {"excluded_stale": 0,
                                           "excluded_data_suspect": 0}}
    last_needed = cal[min(i + max(HORIZONS), len(cal) - 1)]
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = _load_merged(t)
        if df is None or df.empty:
            continue
        status, rec = _eval_ticker(df, _aligned(df, cal), cal, i, spy20, last_needed)
        if status == "ok":
            world[t] = rec
        elif status == "stale":
            world["__meta__"]["excluded_stale"] += 1
        elif status == "suspect":
            world["__meta__"]["excluded_data_suspect"] += 1
    return world


# ── Cohorts + stats ──────────────────────────────────────────────────────────

def _rs_baseline(world: Dict[str, Dict], cap: int) -> Set[str]:
    rows = [(w["rs20"], t) for t, w in world.items()
            if not t.startswith("__") and w["rs20"] is not None
            and w["r40"] is not None and w["r40"] > 0]
    rows.sort(key=lambda x: -x[0])
    return {t for _, t in rows[:cap]}


def _pct(x: Optional[float]) -> Optional[float]:
    return round(100.0 * x, 2) if x is not None else None


def _cohort_stats(name: str, members: Set[str], world: Dict[str, Dict],
                  winners: Set[str], liquid: Set[str]) -> Dict:
    spy_fwd = world["__spy__"]["fwd"]
    fwd: Dict[str, Dict] = {}
    for h in HORIZONS:
        rets = [world[t]["fwd"][h] for t in members if world[t]["fwd"][h] is not None]
        if rets:
            mean = float(np.mean(rets))
            fwd[f"{h}d"] = {
                "n": len(rets), "mean_pct": _pct(mean),
                "median_pct": _pct(float(np.median(rets))),
                "winsor_mean_pct": _pct(_winsor_mean(rets)),
                "win_rate_pct": round(100.0 * sum(1 for r in rets if r > 0) / len(rets), 1),
                "excess_vs_spy_pct": (_pct(mean - spy_fwd[h])
                                      if spy_fwd[h] is not None else None),
            }
        else:
            fwd[f"{h}d"] = {"n": 0, "mean_pct": None, "median_pct": None,
                            "winsor_mean_pct": None,
                            "win_rate_pct": None, "excess_vs_spy_pct": None}
    maes = [world[t]["mae20"] for t in members
            if world[t].get("mae20") is not None]
    hit = members & winners
    rng = random.Random(RANDOM_SEED)
    ctrl = (set(rng.sample(sorted(liquid), min(len(members), len(liquid))))
            if members and liquid else set())
    ctrl20 = [world[t]["fwd"][20] for t in ctrl if world[t]["fwd"][20] is not None]
    return {
        "cohort": name, "n": len(members),
        "mae20_mean_pct": _pct(float(np.mean(maes))) if maes else None,
        "mae20_median_pct": _pct(float(np.median(maes))) if maes else None,
        "members_by_fwd20": sorted(
            members, key=lambda t: -(world[t]["fwd"][20] if world[t]["fwd"][20]
                                     is not None else -9))[:10],
        "forward": fwd,
        "winner_recall_pct": (round(100.0 * len(hit) / len(winners), 1)
                              if winners else None),
        "winner_precision_pct": (round(100.0 * len(hit) / len(members), 1)
                                 if members else None),
        "random_control": {
            "n": len(ctrl),
            "winner_recall_pct": (round(100.0 * len(ctrl & winners) / len(winners), 1)
                                  if winners else None),
            "fwd20_mean_pct": _pct(float(np.mean(ctrl20))) if ctrl20 else None,
        },
    }


def _reject_counts(world: Dict[str, Dict], rejected: Set[str],
                   winners: Set[str]) -> List[Dict]:
    agg: Dict[str, Dict] = {}
    for t in rejected:
        w = world[t]
        codes = ([f"voyager:{r}" for r in w["voyager_reasons"]]
                 + [f"sniper:{r}" for r in w["sniper_reasons"]])
        for code in codes:
            a = agg.setdefault(code, {"rejected_n": 0, "winners_missed": 0,
                                      "fwd20": []})
            a["rejected_n"] += 1
            if t in winners:
                a["winners_missed"] += 1
            if w["fwd"][20] is not None:
                a["fwd20"].append(w["fwd"][20])
    out = []
    for code, a in agg.items():
        lane, filt = code.split(":", 1)
        out.append({
            "filter": filt, "lane": lane, "rejected_n": a["rejected_n"],
            "winners_missed": a["winners_missed"],
            "fwd20_mean_pct": (_pct(float(np.mean(a["fwd20"])))
                               if a["fwd20"] else None),
        })
    out.sort(key=lambda r: (-r["rejected_n"], r["lane"], r["filter"]))
    return out


def _top_rejected(world: Dict[str, Dict], rejected: Set[str], loose: Set[str],
                  rs_base: Set[str], profiles: Dict, top_n: int) -> List[Dict]:
    ranked = sorted(
        (t for t in rejected if world[t]["fwd"][20] is not None),
        key=lambda t: -world[t]["fwd"][20])
    out = []
    for t in ranked[:top_n]:
        w = world[t]
        out.append({
            "ticker": t,
            "fwd5_pct": _pct(w["fwd"][5]), "fwd10_pct": _pct(w["fwd"][10]),
            "fwd20_pct": _pct(w["fwd"][20]),
            "reject_reasons": ([f"voyager:{r}" for r in w["voyager_reasons"]]
                               + [f"sniper:{r}" for r in w["sniper_reasons"]]),
            "caught_by_loose": t in loose,
            "caught_by_rs_baseline": t in rs_base,
            "sector": (profiles.get(t) or {}).get("sector"),
            "theme": dataio.classify_theme(profiles.get(t)),
        })
    return out


def _verdict(cohorts: Dict[str, Dict]) -> Tuple[str, str]:
    best_name, best = None, None
    for name, c in cohorts.items():
        m = c["forward"]["20d"]["mean_pct"]
        if m is not None and (best is None or m > best):
            best_name, best = name, m
    strict_n = cohorts["strict_scanner"]["n"]
    if strict_n == 0:
        return ("STRICT_SCANNER_EMPTY",
                "Strict scanner emitted 0 names at as-of — recall is 0 by "
                "construction; compare rs_baseline vs loose_scanner instead.")
    return ("SINGLE_DATE_DIAGNOSTIC",
            f"Best 20d forward mean: {best_name} ({best}%). One as-of date "
            "only — direction, not proof; rerun across dates before drawing "
            "conclusions.")


# ── Build + render ───────────────────────────────────────────────────────────

def build(*, lookback_td: int = DEFAULT_LOOKBACK_TD,
          winner_fwd20: float = DEFAULT_WINNER_FWD20,
          rs_cap: int = DEFAULT_RS_CAP,
          top_rejected_n: int = DEFAULT_TOP_REJECTED) -> Dict:
    cal = dataio.benchmark_calendar()
    if len(cal) < lookback_td + 61:
        return {"error": f"calendar too short ({len(cal)} bars) for "
                         f"lookback {lookback_td} + 60 bars of history"}
    i = len(cal) - 1 - lookback_td
    profiles = dataio.load_profiles()
    world = _scan_world(cal, i)
    liquid = {t for t in world if not t.startswith("__")}
    winners = {t for t in liquid
               if world[t]["fwd"][20] is not None
               and world[t]["fwd"][20] >= winner_fwd20}
    strict = {t for t in liquid if world[t]["strict_pass"]}
    loose = {t for t in liquid if world[t]["loose_pass"]}
    rs_base = _rs_baseline(world, rs_cap)
    rejected = liquid - strict
    cohorts = {
        "strict_scanner": _cohort_stats("strict_scanner", strict, world, winners, liquid),
        "rs_baseline": _cohort_stats("rs_baseline", rs_base, world, winners, liquid),
        "loose_scanner": _cohort_stats("loose_scanner", loose, world, winners, liquid),
    }
    verdict, note = _verdict(cohorts)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact_version": ARTIFACT_VERSION,
        "disclaimer": ("RESEARCH DIAGNOSTIC — not a buy list, not a signal, "
                       "not a gate change. Loose thresholds are research "
                       "knobs only; production gates are unchanged."),
        "params": {"lookback_td": lookback_td, "horizons_td": list(HORIZONS),
                   "winner_threshold_fwd20": winner_fwd20, "rs_cap": rs_cap,
                   "loose_thresholds": LOOSE_THRESHOLDS,
                   "random_seed": RANDOM_SEED},
        "asof_date": str(cal[i])[:10],
        "n_liquid": len(liquid),
        "n_excluded_stale": world["__meta__"]["excluded_stale"],
        "n_excluded_data_suspect": world["__meta__"]["excluded_data_suspect"],
        "n_forward_winners": len(winners),
        "spy_fwd_pct": {f"{h}d": _pct(world["__spy__"]["fwd"][h]) for h in HORIZONS},
        "cohorts": cohorts,
        "reject_counts_by_filter": _reject_counts(world, rejected, winners),
        "top_rejected": _top_rejected(world, rejected, loose, rs_base,
                                      profiles, top_rejected_n),
        "n_rejected": len(rejected),
        "verdict": verdict,
        "verdict_note": note,
    }


def _render_txt(res: Dict) -> List[str]:
    if "error" in res:
        return [f"== SCANNER RECALL DIAGNOSTICS — ERROR: {res['error']} =="]
    L = [
        f"== SCANNER RECALL DIAGNOSTICS ({res['generated_at']}) ==",
        res["disclaimer"], "",
        f"as-of {res['asof_date']}  liquid={res['n_liquid']}  "
        f"fwd20-winners(≥{res['params']['winner_threshold_fwd20']:+.0%})="
        f"{res['n_forward_winners']}  rejected-by-strict={res['n_rejected']}",
        f"excluded: stale-parquet={res['n_excluded_stale']}  "
        f"data-suspect={res['n_excluded_data_suspect']}",
        "SPY fwd: " + "  ".join(f"{k}={v}%" for k, v in res["spy_fwd_pct"].items()),
        "",
        "COHORT FORWARD RESULTS (mean% / median% / win% / excess-vs-SPY%):",
    ]
    for name, c in res["cohorts"].items():
        L.append(f"  {name:<15} n={c['n']:<4} recall={c['winner_recall_pct']}%  "
                 f"precision={c['winner_precision_pct']}%  "
                 f"(random-ctrl recall={c['random_control']['winner_recall_pct']}%)")
        for h, f in c["forward"].items():
            L.append(f"    {h:>3}: n={f['n']:<4} mean={f['mean_pct']}%  "
                     f"med={f['median_pct']}%  win={f['win_rate_pct']}%  "
                     f"exSPY={f['excess_vs_spy_pct']}%")
    L += ["", "REJECT COUNTS BY FILTER (strict scanner):"]
    for r in res["reject_counts_by_filter"]:
        L.append(f"  {r['lane']:<8} {r['filter']:<26} n={r['rejected_n']:<5} "
                 f"winners_missed={r['winners_missed']:<4} "
                 f"fwd20_mean={r['fwd20_mean_pct']}%")
    L += ["", f"TOP {len(res['top_rejected'])} REJECTED NAMES (by 20d forward return):"]
    for x in res["top_rejected"]:
        catch = ("loose" if x["caught_by_loose"] else "") + \
                ("+rs" if x["caught_by_rs_baseline"] else "")
        L.append(f"  {x['ticker']:<7} fwd 5/10/20d: {x['fwd5_pct']}% / "
                 f"{x['fwd10_pct']}% / {x['fwd20_pct']}%  "
                 f"caught_by={catch or 'none'}  {x['theme']}/{x['sector']}")
        L.append(f"          rejected by: {', '.join(x['reject_reasons'])}")
    L += ["", f"VERDICT: {res['verdict']} — {res['verdict_note']}"]
    return L


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Scanner recall diagnostics (research-only, cache-only).")
    ap.add_argument("--lookback-td", type=int, default=DEFAULT_LOOKBACK_TD,
                    help="trading days back for the as-of date (≥21 keeps 20d fwd resolvable)")
    ap.add_argument("--winner-thresh", type=float, default=DEFAULT_WINNER_FWD20,
                    help="fwd20 return defining a forward winner (default 0.10)")
    ap.add_argument("--rs-cap", type=int, default=DEFAULT_RS_CAP,
                    help="RS baseline pick count (default 25)")
    ap.add_argument("--top-rejected", type=int, default=DEFAULT_TOP_REJECTED,
                    help="rejected names listed (default 15)")
    args = ap.parse_args(argv)
    res = build(lookback_td=max(1, args.lookback_td),
                winner_fwd20=args.winner_thresh,
                rs_cap=max(1, args.rs_cap),
                top_rejected_n=max(1, args.top_rejected))
    dataio.write_json(dataio.RESEARCH_CACHE / "scanner_recall_diagnostics_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "scanner_recall_diagnostics_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
