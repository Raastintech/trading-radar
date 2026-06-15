#!/usr/bin/env python3
"""
research/strategy_lab_profile.py - Phase 1H.1 Strategy Lab runtime profile.

Research-only, cache-only. Measures the current Strategy Research Lab hot
paths (price loading, feature computation, universe building, forward-window
simulation, JSON rendering), compares them against the recorded
pre-optimization baseline, and estimates full-unsampled-window runtimes.

It never imports execution, governance, broker, or live-capital modules and
never calls providers.
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import resource
import time
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_lab_data as d
from research import strategy_research_lab as lab

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "cache" / "research" / "strategy_lab_performance_profile_latest.json"
OUT_TXT = ROOT / "logs" / "strategy_lab_performance_profile_latest.txt"
OUT_DOC = ROOT / "docs" / "research" / "STRATEGY_LAB_PERFORMANCE_PROFILE.md"

VERSION = "STRATEGY_LAB_PERFORMANCE_PROFILE_V1"

# Measured on 2026-06-12 against the pre-Phase-1H.1 lab (per-(ticker,date)
# feature recomputation, no vectorized tables, no shared trade-path cache).
# Kept as a constant so post-optimization runs always show the comparison.
BASELINE_PRE_1H1 = {
    "measured_at": "2026-06-12",
    "pool_size": 599,
    "cold_first_date_seconds": 7.83,
    "warm_new_date_seconds": 7.77,
    "same_date_memoized_seconds": 0.004,
    "simulate_trade_ms_per_call": 0.44,
    "unique_default_window_dates": 613,
    "estimated_full_lab_minutes": 79.4,
    "estimated_exact_walk_forward_minutes": 85.0,
    "estimated_exact_sweep_minutes": 95.0,
    "ru_maxrss_mb": 153.3,
    "bottlenecks": [
        "compute_features_asof recomputed every rolling window per (ticker, date): ~9 ms x 599 tickers per date",
        "atr()/rsi() recomputed from scratch per (ticker, date) (~2.1 s + 1.3 s per date)",
        "_bench_ret re-sliced SPY/QQQ frames per ticker per date (~2.7 s per date)",
        "pandas attrs deepcopy on every slice (~1.7 s per date in copy.deepcopy)",
        "get_forward_window copied the full merged price frame per simulated trade",
        "simulate_trade re-simulated the identical price path once per cost model (3x)",
        "feature lru_cache (250k entries) would thrash on full windows: 599 tickers x 613 dates = 367k entries",
    ],
}


def _measure_dates(dates: Sequence[str]) -> Dict[str, Any]:
    cfg = lab.BacktestConfig()
    params = lab.StrategyParams()
    timings: List[float] = []
    signal_counts: List[int] = []
    last_signals: Dict[str, List[Any]] = {}
    for asof in dates:
        t0 = time.time()
        last_signals = lab.generate_signals_for_date(
            asof, variants=lab.DEFAULT_VARIANTS, params=params, config=cfg
        )
        timings.append(time.time() - t0)
        signal_counts.append(sum(len(v) for v in last_signals.values()))
    # Memoized repeat of the final date.
    t0 = time.time()
    lab.generate_signals_for_date(
        dates[-1], variants=lab.DEFAULT_VARIANTS, params=params, config=cfg
    )
    memo_repeat = time.time() - t0

    flat = [s for v in last_signals.values() for s in v]
    sim_ms = None
    if flat:
        t0 = time.time()
        calls = 0
        for sig in flat:
            for cost in lab.COST_MODELS:
                lab.simulate_trade(sig, params=params, cost_model=cost)
                calls += 1
        sim_ms = (time.time() - t0) / max(1, calls) * 1000.0
    return {
        "dates_measured": list(dates),
        "per_date_seconds": [round(x, 4) for x in timings],
        "cold_first_date_seconds": round(timings[0], 4),
        "warm_new_date_seconds": round(
            sum(timings[1:]) / max(1, len(timings) - 1), 4
        ) if len(timings) > 1 else None,
        "same_date_memoized_seconds": round(memo_repeat, 4),
        "signals_on_last_date": signal_counts[-1] if signal_counts else 0,
        "simulate_trade_ms_per_call": round(sim_ms, 4) if sim_ms is not None else None,
    }


def _profile_one_date(asof: str, top_n: int = 20) -> List[Dict[str, Any]]:
    cfg = lab.BacktestConfig()
    params = lab.StrategyParams()
    pr = cProfile.Profile()
    pr.enable()
    lab.generate_signals_for_date(asof, variants=lab.DEFAULT_VARIANTS, params=params, config=cfg)
    pr.disable()
    stats = pstats.Stats(pr)
    rows: List[Dict[str, Any]] = []
    for func, (cc, nc, tt, ct, callers) in stats.stats.items():  # type: ignore[attr-defined]
        filename, lineno, name = func
        rows.append({
            "function": f"{Path(filename).name}:{lineno}:{name}",
            "ncalls": nc,
            "tottime_s": round(tt, 4),
            "cumtime_s": round(ct, 4),
        })
    rows.sort(key=lambda r: r["cumtime_s"], reverse=True)
    return rows[:top_n]


def _estimate(measure: Dict[str, Any]) -> Dict[str, Any]:
    warm = measure.get("warm_new_date_seconds") or measure.get("cold_first_date_seconds") or 0.0
    windows = lab.default_windows()
    uniq: set = set()
    per_window: Dict[str, int] = {}
    for w in windows:
        dates = d.trading_dates_between(w["start"], w["end"])
        per_window[w["name"]] = len(dates)
        uniq.update(str(x.date()) for x in dates)
    n_unique = len(uniq)
    lab_minutes = n_unique * warm / 60.0
    # Walk-forward/sweep share the per-date feature cost in-process; extra
    # passes only re-run signal generation and simulation on warm caches.
    memo = measure.get("same_date_memoized_seconds") or 0.0
    wf_minutes = lab_minutes + 5 * 3 * n_unique * memo / 60.0
    sweep_minutes = lab_minutes + 13 * 3 * n_unique * memo / 60.0
    return {
        "window_date_counts": per_window,
        "unique_default_window_dates": n_unique,
        "estimated_full_lab_minutes": round(lab_minutes, 2),
        "estimated_exact_walk_forward_minutes": round(wf_minutes, 2),
        "estimated_exact_sweep_minutes": round(sweep_minutes, 2),
    }


def build_profile(*, dates: Optional[Sequence[str]] = None, with_cprofile: bool = True) -> Dict[str, Any]:
    cal = d.benchmark_calendar()
    recent = [str(x.date()) for x in cal[-4:-1]]
    dates = list(dates or recent)
    t0 = time.time()
    pool = d._candidate_pool("research_core")
    pool_seconds = time.time() - t0
    measure = _measure_dates(dates)
    top = _profile_one_date(str(cal[-1].date())) if with_cprofile else []
    estimates = _estimate(measure)
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    fast = getattr(d, "FAST_FEATURES", False)
    return {
        "kind": "strategy_lab_performance_profile",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "fast_features_enabled": bool(fast),
        "pool_size": len(pool),
        "pool_build_seconds": round(pool_seconds, 4),
        "measured": measure,
        "estimates": estimates,
        "ru_maxrss_mb": round(rss_mb, 1),
        "slowest_functions_cumtime": top,
        "baseline_pre_1h1": BASELINE_PRE_1H1,
        "speedup_vs_baseline_per_date": (
            round(BASELINE_PRE_1H1["warm_new_date_seconds"] / measure["warm_new_date_seconds"], 1)
            if measure.get("warm_new_date_seconds") else None
        ),
        "cache_coverage": d.cache_coverage(),
    }


def render_text(res: Dict[str, Any]) -> List[str]:
    m = res["measured"]
    e = res["estimates"]
    b = res["baseline_pre_1h1"]
    lines = [
        f"STRATEGY LAB PERFORMANCE PROFILE - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"fast_features_enabled={res['fast_features_enabled']} pool={res['pool_size']}",
        f"cold_first_date={m['cold_first_date_seconds']}s warm_new_date={m.get('warm_new_date_seconds')}s memoized_repeat={m['same_date_memoized_seconds']}s",
        f"simulate_trade={m.get('simulate_trade_ms_per_call')}ms/call ru_maxrss={res['ru_maxrss_mb']}MB",
        "",
        f"BASELINE (pre-1H.1, {b['measured_at']}): warm_new_date={b['warm_new_date_seconds']}s "
        f"-> speedup x{res.get('speedup_vs_baseline_per_date')}",
        "",
        "ESTIMATED FULL-WINDOW RUNTIMES (unsampled):",
        f"  lab default windows ({e['unique_default_window_dates']} unique dates): {e['estimated_full_lab_minutes']} min "
        f"(baseline {b['estimated_full_lab_minutes']} min)",
        f"  exact walk-forward: {e['estimated_exact_walk_forward_minutes']} min (baseline {b['estimated_exact_walk_forward_minutes']} min)",
        f"  exact threshold sweep: {e['estimated_exact_sweep_minutes']} min (baseline {b['estimated_exact_sweep_minutes']} min)",
        "",
        "SLOWEST FUNCTIONS (cumtime, one date):",
    ]
    for row in res.get("slowest_functions_cumtime", [])[:15]:
        lines.append(f"  {row['cumtime_s']:>9.3f}s {row['ncalls']:>9} {row['function']}")
    lines += ["", "BASELINE BOTTLENECKS (pre-1H.1):"]
    lines.extend(f"  - {x}" for x in b["bottlenecks"])
    return lines


def render_doc(res: Dict[str, Any]) -> str:
    m = res["measured"]
    e = res["estimates"]
    b = res["baseline_pre_1h1"]
    lines = [
        "# Strategy Lab Performance Profile (Phase 1H.1)",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        "## Baseline bottlenecks (measured 2026-06-12, pre-optimization)",
        "",
    ]
    lines.extend(f"- {x}" for x in b["bottlenecks"])
    lines += [
        "",
        "## Measurements",
        "",
        "| Metric | Baseline (pre-1H.1) | Current |",
        "|---|---:|---:|",
        f"| Per-date cost, warm new date | {b['warm_new_date_seconds']} s | {m.get('warm_new_date_seconds')} s |",
        f"| Same-date memoized repeat | {b['same_date_memoized_seconds']} s | {m['same_date_memoized_seconds']} s |",
        f"| simulate_trade per call | {b['simulate_trade_ms_per_call']} ms | {m.get('simulate_trade_ms_per_call')} ms |",
        f"| Peak RSS | {b['ru_maxrss_mb']} MB | {res['ru_maxrss_mb']} MB |",
        f"| Estimated full lab (unsampled, {e['unique_default_window_dates']} dates) | {b['estimated_full_lab_minutes']} min | {e['estimated_full_lab_minutes']} min |",
        f"| Estimated exact walk-forward | {b['estimated_exact_walk_forward_minutes']} min | {e['estimated_exact_walk_forward_minutes']} min |",
        f"| Estimated exact threshold sweep | {b['estimated_exact_sweep_minutes']} min | {e['estimated_exact_sweep_minutes']} min |",
        "",
        f"Per-date speedup vs baseline: **x{res.get('speedup_vs_baseline_per_date')}**",
        "",
        "## Optimization targets identified",
        "",
        "1. Vectorize feature computation once per ticker (rolling/ewm columns) instead of per (ticker, date).",
        "2. Share SPY/QQQ benchmark return tables instead of re-slicing frames per ticker per date.",
        "3. Stop copying the full merged price frame per forward-window lookup.",
        "4. Simulate each trade path once and apply the three cost models to the same path.",
        "5. Scan the universe from precomputed column arrays without building feature dicts for non-survivors.",
        "",
        "## Slowest functions (cumtime over one profiled date)",
        "",
        "| cumtime (s) | ncalls | function |",
        "|---:|---:|---|",
    ]
    for row in res.get("slowest_functions_cumtime", [])[:20]:
        lines.append(f"| {row['cumtime_s']} | {row['ncalls']} | `{row['function']}` |")
    lines += [
        "",
        "All measurements are cache-only and research-only; no providers, no DB writes, no execution paths.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(res: Dict[str, Any]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text("\n".join(render_text(res)) + "\n", encoding="utf-8")
    OUT_DOC.write_text(render_doc(res), encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Strategy Lab performance profile (research-only)")
    ap.add_argument("--no-cprofile", action="store_true")
    args = ap.parse_args(argv)
    res = build_profile(with_cprofile=not args.no_cprofile)
    write_outputs(res)
    print("\n".join(render_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
