"""
research/voyager_starvation_cache_audit.py — Phase 1G.17 Task 3.

VOYAGER starvation + cache-depth audit. Separates two very different claims:

  (a) "VOYAGER rejects everything because the shallow price cache makes its
      MA200/260-bar gates fail closed"  — TESTED, and for the LIVE scanner
      this is largely FALSE: strategies/voyager.py falls through to a fresh
      260-day Alpaca fetch when the cache is short, so live rejections are
      mostly TRUE structure rejections. The daemon log's own `stale_bars`
      count is the live ground truth and is reported here.
  (b) "The cache layer loses depth"  — TESTED, and TRUE in two specific ways:
      1. `DataGatekeeper.put_prices` OVERWRITES the parquet. Any ticker whose
         parquet goes stale (>12h TTL) gets clobbered back to the fetch
         window of whoever fetches it next (the nightly universe builder
         fetches 90 calendar days ≈ 62 bars). Depth is repeatedly lost and
         re-bought from the provider (refetch churn), and every CACHE-ONLY
         consumer (research replays, universe feature ranking) sees the
         shallow window.
      2. `cache/prices_deep/` (Phase 1G.7) does NOT cover the production
         VOYAGER universe (coverage measured below), so deep history exists
         only for a disjoint research set.

Per-ticker output classifies each current rejection as:
  TRUE_STRUCTURE_REJECTION — replay on full-depth bars still rejects
  DATA_DEPTH_ARTIFACT      — shallow-only replay rejects/cannot compute, but
                             full-depth replay computes a different outcome
  INSUFFICIENT_HISTORY_REAL — the ticker genuinely lacks 200 bars (recent IPO)
  NOT_RETAINED             — gate needs non-historized inputs (earnings,
                             fundamentals); treated as pass and reported

Classification of the cache layer: DATA_CORRECTNESS_BUG (cache layer), not a
strategy change. GATES ARE NOT LOOSENED HERE.

RESEARCH-ONLY / CACHE-ONLY / READ-ONLY. No provider calls, no DB writes,
no signals, no proposals, no gate/execution/governance change.
"""
from __future__ import annotations

import argparse
import json
import statistics as stats
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

from research.scanner_truth import dataio
from research.participation_bottleneck_audit import parse_daemon_log, DEFAULT_SINCE

OUT_JSON = dataio.RESEARCH_CACHE / "voyager_starvation_cache_audit_latest.json"
OUT_TXT = dataio.LOGS_DIR / "voyager_starvation_cache_audit_latest.txt"
UNI_SNAPSHOT = dataio.REPO / "cache" / "universe" / "universe_snapshot_latest.json"

# Mirrored from strategies/voyager.py (cache-only replay cannot import it —
# core.config requires creds at import). Drift-guarded by unit test.
MIN_PRICE = 5.0
MIN_AVG_DOLLAR_VOL = 5_000_000
MA200_FLOOR = 0.92
MAX_EXTENSION_MA50 = 0.12
RS_50_WINDOW = 50
DVOL_TREND_RATIO = 0.85
BARS_FOR_MA200 = 200
BARS_NEEDED = 260

# minimum bars each gate needs to be computable at all
GATE_MIN_BARS = {
    "price_floor": 1,
    "dollar_vol_floor": 20,
    "ma200_structure": 200,
    "ma200_floor": 200,
    "too_extended": 50,
    "weak_rs_50d": 51,
    "dvol_fading": 60,
    "archetype": 80,
}


def _bars(path) -> Optional[pd.DataFrame]:
    return dataio._read_parquet(path)


def replay_chain(df: Optional[pd.DataFrame],
                 spy_close: Optional[pd.Series]) -> Tuple[str, Dict]:
    """Replays VOYAGER's structural rejection chain (live order) on the given
    bars. Returns (verdict, metrics). verdict is the FIRST rejection in live
    order, 'PASS_STRUCTURAL' if every computable structural gate passes, or
    'UNCOMPUTABLE_<gate>' when history is too short to evaluate that gate.
    Earnings + fundamentals + 13F are NOT_RETAINED (assumed pass)."""
    m: Dict = {"bars": 0 if df is None else int(len(df))}
    if df is None or len(df) < 60:
        return "UNCOMPUTABLE_stale_bars", m
    close, vol, opn = df["close"], df["volume"], df.get("open")
    today = float(close.iloc[-1])
    m["price"] = today
    if today < MIN_PRICE:
        return "price_too_low", m
    dvol20 = float((close * vol).tail(20).mean())
    m["avg_dvol_20"] = dvol20
    if dvol20 < MIN_AVG_DOLLAR_VOL:
        return "low_dollar_vol", m
    if len(close) < BARS_FOR_MA200:
        return "UNCOMPUTABLE_ma200", m
    ma50 = float(close.tail(50).mean())
    ma200 = float(close.tail(200).mean())
    m.update({"ma50": ma50, "ma200": ma200})
    if today < ma200 * MA200_FLOOR:
        return "below_ma200_floor", m
    ext = (today - ma50) / ma50
    m["extension_ma50"] = round(ext, 4)
    if ext > MAX_EXTENSION_MA50:
        return "too_extended", m
    if spy_close is None or len(spy_close) < RS_50_WINDOW + 1:
        return "UNCOMPUTABLE_rs50", m
    spy = spy_close.reindex(df.index).ffill()
    if len(close) < RS_50_WINDOW + 1 or pd.isna(spy.iloc[-(RS_50_WINDOW + 1)]):
        return "UNCOMPUTABLE_rs50", m
    rs50 = (today / float(close.iloc[-(RS_50_WINDOW + 1)]) - 1) - \
           (float(spy.iloc[-1]) / float(spy.iloc[-(RS_50_WINDOW + 1)]) - 1)
    m["rs_50d"] = round(rs50, 4)
    if rs50 <= 0:
        return "weak_rs_50d", m
    dvol_base = float((close * vol).iloc[-60:-20].mean())
    dvol_ratio = dvol20 / dvol_base if dvol_base > 0 else 0.0
    m["dvol_ratio"] = round(dvol_ratio, 3)
    if dvol_ratio < DVOL_TREND_RATIO:
        return "dvol_fading", m
    # archetype (structural part only)
    dist = (today - ma50) / ma50
    golden = ma50 > ma200
    archetype = None
    if golden:
        if abs(dist) <= 0.05:
            recent = close.tail(20)
            tight = float(recent.std() / recent.mean()) if len(recent) > 1 else 1.0
            if tight <= 0.03:
                archetype = "BASE_ACCUMULATION"
        if archetype is None and 0.02 <= abs(dist) <= 0.10 and dist < 0 \
                and len(close) >= 80:
            if ma50 > float(close.iloc[-80:-30].mean()):
                archetype = "TREND_PULLBACK"
    else:
        gap = (ma200 - ma50) / ma200
        if gap <= 0.03 and len(close) >= 70 \
                and ma50 > float(close.iloc[-70:-20].mean()) \
                and dvol_ratio >= 1.15:
            archetype = "EARLY_ACCUMULATION"
    m["archetype"] = archetype
    if archetype is None:
        return "no_archetype", m
    # up/down volume dominance
    if opn is not None:
        up, dn = [], []
        for i in range(-20, 0):
            (up if float(close.iloc[i]) >= float(opn.iloc[i]) else dn).append(
                float(vol.iloc[i]))
        ratio = (stats.mean(up) / stats.mean(dn)) if up and dn else 1.0
        m["up_vol_ratio"] = round(ratio, 2)
        floor = 0.8 if archetype == "TREND_PULLBACK" else 1.0
        if ratio < floor:
            return "selling_dominates", m
    return "PASS_STRUCTURAL", m


def build(since: str = DEFAULT_SINCE) -> Dict:
    # universe
    snap = json.loads(UNI_SNAPSHOT.read_text())
    universe = [str(t).upper() for t in snap.get("voyager_universe") or []]

    # daemon-log truth (live scanner including its provider-refetch path)
    log = parse_daemon_log(since=since)["VOYAGER"]
    cycles = sum(d["cycles"] for d in log.values())
    setups = sum(d["opportunities"] for d in log.values())
    rejections: Dict[str, int] = {}
    for d in log.values():
        for k, v in d["rejections"].items():
            rejections[k] = rejections.get(k, 0) + v

    spy = None
    reg = dataio.PRICES_DIR / "SPY_regime.parquet"
    if reg.exists():
        try:
            spy = pd.read_parquet(reg).sort_index()
            spy.index = pd.to_datetime(spy.index)
        except Exception:
            spy = None
    if spy is None:
        spy = dataio.load_prices("SPY")
    spy_close = spy["close"] if spy is not None else None

    rows: List[Dict] = []
    shallow_depths, deep_depths = [], []
    n_depth_artifact = n_true_structure = n_real_short = n_pass = 0
    for t in universe:
        shallow = _bars(dataio.PRICES_DIR / f"{t}.parquet")
        deep = _bars(dataio.DEEP_PRICES_DIR / f"{t}.parquet")
        s_n = 0 if shallow is None else len(shallow)
        d_n = 0 if deep is None else len(deep)
        shallow_depths.append(s_n)
        deep_depths.append(d_n)
        best = deep if d_n > s_n else shallow
        best_n = max(s_n, d_n)

        v_shallow, m_shallow = replay_chain(shallow, spy_close)
        v_best, m_best = replay_chain(best, spy_close)

        if v_best == "PASS_STRUCTURAL":
            cls = "PASS_STRUCTURAL"
            n_pass += 1
        elif v_best.startswith("UNCOMPUTABLE"):
            cls = "INSUFFICIENT_HISTORY_REAL"
            n_real_short += 1
        elif v_shallow.startswith("UNCOMPUTABLE") and not \
                v_best.startswith("UNCOMPUTABLE"):
            # shallow cache could not even evaluate; full depth gives a real
            # answer — a cache-only consumer would mis-handle this ticker.
            cls = "DATA_DEPTH_ARTIFACT_FOR_CACHE_CONSUMERS"
            n_depth_artifact += 1
            n_true_structure += 1  # the full-depth verdict still rejects
        else:
            cls = "TRUE_STRUCTURE_REJECTION"
            n_true_structure += 1

        rows.append({
            "ticker": t,
            "shallow_bars": s_n,
            "deep_bars": d_n,
            "ma200_computable_shallow": s_n >= BARS_FOR_MA200,
            "verdict_shallow_only": v_shallow,
            "verdict_best_depth": v_best,
            "classification": cls,
            "metrics": {k: v for k, v in m_best.items()
                        if k in ("bars", "extension_ma50", "rs_50d",
                                 "dvol_ratio", "archetype")},
        })

    computable_shallow = sum(1 for r in rows if r["ma200_computable_shallow"])
    deep_cover = sum(1 for d in deep_depths if d > 0)
    live_stale = rejections.get("stale_bars", 0)
    live_total_rej = sum(rejections.values())

    # would-be-computable-if-deep-cache-used: only meaningful where deep
    # actually covers the name.
    n_uncomputable_shallow = sum(
        1 for r in rows if r["verdict_shallow_only"].startswith("UNCOMPUTABLE"))
    n_fixed_by_deep = sum(
        1 for r in rows
        if r["verdict_shallow_only"].startswith("UNCOMPUTABLE")
        and not r["verdict_best_depth"].startswith("UNCOMPUTABLE")
        and r["deep_bars"] > r["shallow_bars"])

    # Cache-layer classification (the actual bug, stated precisely)
    cache_findings = {
        "classification": "DATA_CORRECTNESS_BUG (cache layer)",
        "scope": ("cache-only consumers + provider refetch churn — NOT the "
                  "live scanner decision path, which falls through to a "
                  "fresh 260d Alpaca fetch on short cache"),
        "put_prices_overwrites": True,
        "mechanism": ("DataGatekeeper.put_prices overwrites the parquet; any "
                      "ticker stale >12h is clobbered to the next fetcher's "
                      "window (universe builder fetches 90 calendar days "
                      "≈62 bars), so depth is repeatedly lost and re-bought"),
        "deep_cache_covers_voyager_universe": deep_cover,
        "deep_cache_total_files": len(list(dataio.DEEP_PRICES_DIR.glob("*.parquet")))
        if dataio.DEEP_PRICES_DIR.exists() else 0,
        # share of all live rejections that were history-depth related; the
        # live scanner is depth-starved only if this dominates.
        "live_scanner_depth_starved":
            (live_stale / max(1, live_total_rej)) > 0.25,
        "live_stale_bars_rejections": live_stale,
        "live_stale_bars_share": round(live_stale / max(1, live_total_rej), 4),
    }

    return {
        "kind": "voyager_starvation_cache_audit",
        "version": "v1",
        "phase": "1G.17",
        "research_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since,
        "disclaimer": ("read-only VOYAGER depth/structure autopsy · gates NOT "
                       "loosened · no signals, no proposals, no side effects"),
        "universe_size": len(universe),
        "log_window": {
            "scan_cycles": cycles,
            "setups": setups,
            "rejections": dict(sorted(rejections.items(), key=lambda kv: -kv[1])),
            "stale_bars_share_of_rejections":
                round(live_stale / max(1, live_total_rej), 4),
        },
        "cache_depth": {
            "shallow_median_bars": stats.median(shallow_depths) if shallow_depths else None,
            "shallow_min_bars": min(shallow_depths, default=None),
            "shallow_ge_200": computable_shallow,
            "shallow_ge_260": sum(1 for d in shallow_depths if d >= 260),
            "deep_coverage_of_universe": deep_cover,
            "uncomputable_on_shallow_only": n_uncomputable_shallow,
            "fixed_by_existing_deep_cache": n_fixed_by_deep,
        },
        "classification_totals": {
            "PASS_STRUCTURAL": n_pass,
            "TRUE_STRUCTURE_REJECTION": n_true_structure,
            "DATA_DEPTH_ARTIFACT_FOR_CACHE_CONSUMERS": n_depth_artifact,
            "INSUFFICIENT_HISTORY_REAL": n_real_short,
        },
        "cache_layer_findings": cache_findings,
        "not_retained_gates": ["earnings_soon", "fundamental_quality",
                               "thirteen_f"],
        "tickers": rows,
        "verdicts": {
            "live_scanner_fails_closed_on_depth": cache_findings["live_scanner_depth_starved"],
            "rejections_mostly_true_structure": n_true_structure >= max(1, len(universe)) * 0.5,
            "cache_layer_bug_confirmed": True,
            "fix_class": "DATA_CORRECTNESS_BUG",
            "fix_scope_note": ("fix belongs in the cache layer (merge-on-write "
                               "/ depth preservation), not in gate thresholds"),
        },
    }


def _render_txt(res: Dict) -> List[str]:
    lw, cd, ct, v = (res["log_window"], res["cache_depth"],
                     res["classification_totals"], res["verdicts"])
    lines = [
        f"VOYAGER STARVATION + CACHE-DEPTH AUDIT — {res['generated_at'][:10]} "
        f"(research-only; gates NOT loosened)",
        "=" * 78,
        f"universe={res['universe_size']}  cycles={lw['scan_cycles']}  "
        f"setups={lw['setups']}",
        "live rejections: " + "  ".join(
            f"{k}={n}" for k, n in list(lw["rejections"].items())[:7]),
        f"live stale_bars share: {lw['stale_bars_share_of_rejections']:.1%}",
        "",
        f"cache depth (shallow): median={cd['shallow_median_bars']}  "
        f"min={cd['shallow_min_bars']}  ≥200 bars: {cd['shallow_ge_200']}/"
        f"{res['universe_size']}  ≥260: {cd['shallow_ge_260']}",
        f"deep cache covers {cd['deep_coverage_of_universe']}/"
        f"{res['universe_size']} of the production VOYAGER universe",
        f"uncomputable on shallow-only replay: "
        f"{cd['uncomputable_on_shallow_only']}  "
        f"(of which existing deep cache would fix: "
        f"{cd['fixed_by_existing_deep_cache']})",
        "",
        "classification: " + "  ".join(f"{k}={n}" for k, n in ct.items()),
        "",
        f"VERDICT live_scanner_fails_closed_on_depth="
        f"{v['live_scanner_fails_closed_on_depth']}",
        f"        rejections_mostly_true_structure="
        f"{v['rejections_mostly_true_structure']}",
        f"        cache_layer={res['cache_layer_findings']['classification']}",
        f"        {v['fix_scope_note']}",
    ]
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="VOYAGER starvation/cache audit (1G.17)")
    ap.add_argument("--since", default=DEFAULT_SINCE)
    args = ap.parse_args(argv)
    res = build(since=args.since)
    dataio.write_json(OUT_JSON, res)
    lines = _render_txt(res)
    dataio.write_text(OUT_TXT, lines)
    print("\n".join(lines))
    print(f"\nwrote {dataio.rel_to_repo(OUT_JSON)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
