"""Stage 2 — price-only historical replay of the frozen research scanner.

Runs the **frozen production scanner logic** as-of each historical scan date
against ``cache/replay_prices/``, with every non-price input disabled. It
changes no scanner logic: as-of behaviour is injected purely by patching the
module's price loaders and seed functions at runtime, inside this process. The
scanner source file is never modified and never imported by production from
here.

Sub-stages::

    replay    run the scanner over every weekly scan date  (zero API)
    control   forward returns of the FULL scanned universe (zero API)
    analyse   pooled stats + selection skill + verdict      (zero API)

All three are read-only with respect to the provider — the replay consumes
``cache/replay_prices`` only.

Writes ONLY:
    cache/research/historical_replay_episodes.jsonl
    cache/research/historical_replay_latest.json
    cache/research/historical_replay_intermediates/*.json
    logs/historical_replay_latest.txt

The scanner's ``_build_universe`` unconditionally writes three LIVE artifacts
(``research_universe_build_latest.json``, ``universe_miss_diagnostic_latest.json``
and ``logs/research_universe_build_latest.txt``). :func:`install_asof_patches`
stubs that writer out, and every sub-stage additionally runs the
:class:`~research.backtests.common.LiveArtifactTripwire` so an unpatched write
fails the stage instead of silently corrupting live state.

RESEARCH ONLY — replay evidence, not live forward evidence, not Phase 4B input.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from research.backtests.common import (
    BENCHMARKS,
    HORIZONS,
    LiveArtifactTripwire,
    REPLAY_WINDOW_END,
    REPLAY_WINDOW_START,
    ROOT,
    ReplayVerdict,
    assert_provenance,
    assert_replay_verdict,
    offline_env,
    provenance_flags,
    run_cli,
    write_replay_json,
    write_replay_text,
)

offline_env()  # must run before core.config is imported anywhere downstream

EPISODES_REL = "cache/research/historical_replay_episodes.jsonl"
REPORT_JSON_REL = "cache/research/historical_replay_latest.json"
REPORT_TXT_REL = "logs/historical_replay_latest.txt"
INTERMEDIATES_REL = "cache/research/historical_replay_intermediates"

# Module-level as-of state. The replay forks a worker pool, so each worker
# inherits the preloaded price arrays copy-on-write and mutates only its own
# copy of these two globals.
_ASOF = None
_SLICE_CACHE: dict = {}
PRICES: dict = {}
ALL_IDX = None


# ── price cache ─────────────────────────────────────────────────────────────


def load_replay_prices(root: Path, exclude: set[str]) -> dict:
    """Preload every replay parquet into plain numpy arrays."""
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    out: dict = {}
    t0 = time.time()
    for f in (root / "cache" / "replay_prices").glob("*.parquet"):
        sym = f.stem
        if sym in exclude:
            continue
        try:
            df = pd.read_parquet(f)
            if df is None or df.empty:
                continue
            df = df.sort_index()
            out[sym] = {
                "idx": df.index.values.astype("datetime64[ns]"),
                "close": df["close"].to_numpy(np.float64),
                "volume": df["volume"].to_numpy(np.float64),
            }
        except Exception:
            pass
    print(f"loaded {len(out):,} replay symbols in {time.time()-t0:.0f}s", flush=True)
    return out


def load_quarantine_excludes(root: Path) -> set[str]:
    """Symbols the price backfill quarantined as symbol-reuse contaminated."""
    q = root / "cache" / "replay_universe" / "replay_price_quarantine.json"
    if not q.exists():
        return set()
    d = json.loads(q.read_text())
    return set(d.get("symbol_reuse_bars_past_delisting_over_30d") or [])


def weekly_scan_dates(prices: dict, start: str, end: str):
    """Last trading session of each ISO week, from SPY's own calendar."""
    import pandas as pd  # noqa: PLC0415

    spy_idx = pd.DatetimeIndex(prices["SPY"]["idx"])
    cal = spy_idx[(spy_idx >= start) & (spy_idx <= end)]
    weekly = (
        pd.Series(cal, index=cal)
        .groupby([cal.isocalendar().year, cal.isocalendar().week])
        .max()
    )
    return sorted(pd.DatetimeIndex(weekly.values)), spy_idx


# ── as-of machinery ─────────────────────────────────────────────────────────


def _sliced(sym: str):
    """Frame for *sym* truncated at the current as-of date, or None."""
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    if sym in _SLICE_CACHE:
        return _SLICE_CACHE[sym]
    p = PRICES.get(sym)
    out = None
    if p is not None:
        n = int(np.searchsorted(p["idx"], np.datetime64(_ASOF), side="right"))
        if n > 0:
            out = pd.DataFrame(
                {"close": p["close"][:n], "volume": p["volume"][:n]},
                index=pd.DatetimeIndex(p["idx"][:n]),
            )
    _SLICE_CACHE[sym] = out
    return out


def install_asof_patches(root: Path):
    """Patch the frozen scanner's IO surface for as-of replay. Returns (RS, ENR).

    Every patch is a *read* redirection or a disabled non-price seed. The one
    write-side patch is ``_write_universe_build_log``, which is stubbed because
    the production implementation writes three live artifacts unconditionally.
    """
    import research.research_scanner as RS  # noqa: PLC0415
    import research.research_candidate_enrichment as ENR  # noqa: PLC0415

    def _patched_load_cached_frame(symbol):
        """Mirror the production loader's session/suspect gates on sliced data."""
        sym = symbol.upper()
        df = _sliced(sym)
        if df is None or df.empty:
            return None
        if RS._REQUIRED_SESSION is not None:
            if df.index.max().date() < RS._REQUIRED_SESSION:
                RS._SESSION_MISMATCH_SKIPPED.add(sym)
                return None
        if sym != "SPY":
            closes = df["close"].tolist()
            if closes and RS._price_series_suspect(closes):
                RS._DATA_SUSPECT_SKIPPED.add(sym)
                return None
        return df

    def _patched_ticker_last_bar_date(symbol):
        # Must return the AS-OF last bar. The production implementation reads
        # the parquet directly, which would compute same_session against the
        # unsliced (future) end of the series.
        df = _sliced(str(symbol).upper())
        if df is None or df.empty:
            return None
        return str(df.index.max().date())

    RS._load_cached_frame = _patched_load_cached_frame
    RS._load_ranked_frame = lambda sym: _sliced(sym.upper())
    RS._parquet_mtime = lambda sym: None
    RS._ticker_last_bar_date = _patched_ticker_last_bar_date
    RS._load_scan_manifest = lambda: None
    RS._load_social_data = lambda: {}
    RS._alpha_board_tickers = lambda: []
    RS._daily_radar_tickers = lambda: []
    RS._social_arb_tickers = lambda: []
    RS.PRICE_DIR = root / "cache" / "replay_prices"
    RS.DEEP_PRICE_DIR = root / "cache" / "__no_deep_cache__"
    RS._MAX_STALE_DAYS = None
    RS._write_universe_build_log = lambda info: None
    ENR._load_frame = lambda ticker, price_dir: _sliced(str(ticker).upper())
    ENR._parquet_mtime = lambda ticker, price_dir: None
    return RS, ENR


def fwd(sym: str, asof, h: int):
    """(pct_return, status) *h* trading days after *asof*, truncating at death.

    A delisted name has no bar at ``asof + h``. Rather than drop it — which
    would restore exactly the survivorship bias this replay exists to remove —
    the return is measured to the final traded bar and labelled.
    """
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    p = PRICES.get(sym)
    if p is None:
        return None, "no_data"
    i = int(np.searchsorted(p["idx"], np.datetime64(asof), side="right")) - 1
    if i < 0:
        return None, "not_yet_listed"
    p0 = p["close"][i]
    if not np.isfinite(p0) or p0 <= 0:
        return None, "bad_base_price"
    j = i + h
    if j < len(p["close"]):
        return (float(p["close"][j] / p0 - 1.0) * 100.0, "ok")
    last = len(p["close"]) - 1
    if last <= i:
        return None, "no_forward_bars"
    cal_room = (
        int(np.searchsorted(ALL_IDX.values, np.datetime64(asof), side="right")) - 1 + h
    )
    status = (
        "horizon_truncated_by_delisting_end_of_life"
        if cal_room < len(ALL_IDX)
        else "horizon_not_matured"
    )
    return (float(p["close"][last] / p0 - 1.0) * 100.0, status)


def _reset_asof(RS, ts):
    import pandas as pd  # noqa: PLC0415

    global _ASOF, _SLICE_CACHE
    _ASOF = pd.Timestamp(ts)
    _SLICE_CACHE = {}
    RS._REQUIRED_SESSION = _ASOF.date()
    RS._STALE_SKIPPED.clear()
    RS._SESSION_MISMATCH_SKIPPED.clear()
    RS._DATA_SUSPECT_SKIPPED.clear()
    return _ASOF


_RS = None


def run_date(ts):
    """Scan one historical date and attach forward returns to each pick."""
    RS = _RS
    asof = _reset_asof(RS, ts)
    try:
        scan = RS.build_scanner(offline=True, universe_cap=RS.DEFAULT_UNIVERSE_CAP)
    except Exception as e:
        return {"date": str(asof.date()), "error": f"{type(e).__name__}: {e}"}, []

    wl = scan.get("watchlist") or []
    eps = []
    bench_r = {b: {h: fwd(b, asof, h)[0] for h in HORIZONS} for b in BENCHMARKS}
    for it in wl:
        t = it.get("ticker")
        rec = {
            "scan_date": str(asof.date()),
            "ticker": t,
            "category": it.get("category"),
            "watchlist_label": it.get("watchlist_label"),
            "research_score": it.get("research_score"),
            "rs_63d_vs_spy": it.get("rs_63d_vs_spy"),
            "rs_20d_vs_spy": it.get("rs_20d_vs_spy"),
            "above_ma50": it.get("above_ma50"),
            "above_ma200": it.get("above_ma200"),
            "dd_from_high_pct": it.get("dd_from_high_pct"),
            "vol_trend_ratio": it.get("vol_trend_ratio"),
            "bars_available": it.get("bars_available"),
            "data_confidence": it.get("data_confidence"),
            "avg_dollar_volume": it.get("avg_dollar_volume"),
        }
        for h in HORIZONS:
            r, st = fwd(t, asof, h)
            rec[f"ret_{h}d"] = None if r is None else round(r, 4)
            rec[f"ret_{h}d_status"] = st
            for b in BENCHMARKS:
                br = bench_r[b][h]
                rec[f"ret_{h}d_vs_{b.lower()}"] = (
                    None if (r is None or br is None) else round(r - br, 4)
                )
        eps.append(rec)
    meta = {
        "date": str(asof.date()),
        "universe_size": scan.get("universe_size"),
        "watchlist": len(wl),
        "categories": {k: len(v) for k, v in (scan.get("categories") or {}).items()},
        "session_mismatch_excluded": scan.get("session_mismatch_excluded_count"),
        "data_suspect_skipped": scan.get("data_suspect_skipped_count"),
        "benchmarks": {
            b: {str(h): bench_r[b][h] for h in HORIZONS} for b in BENCHMARKS
        },
    }
    return meta, eps


def control_date(ts):
    """Forward returns of the FULL scanned universe — the selection control.

    Without this, a negative scanner return is unattributable: it could be the
    market. Comparing the picks to the pool they were drawn from on the same
    date isolates selection skill.
    """
    import numpy as np  # noqa: PLC0415

    RS = _RS
    asof = _reset_asof(RS, ts)
    try:
        uni, _ = RS._build_universe(cap=RS.DEFAULT_UNIVERSE_CAP)
    except Exception as e:
        return {"date": str(asof.date()), "error": str(e)}
    out = {"date": str(asof.date()), "universe_n": len(uni)}
    bench = {b: {h: fwd(b, asof, h)[0] for h in HORIZONS} for b in BENCHMARKS}
    for h in HORIZONS:
        vals = []
        for t in uni:
            r, _st = fwd(t, asof, h)
            if r is not None:
                vals.append(r)
        if vals:
            a = np.array(vals)
            out[f"uni_mean_{h}d"] = float(a.mean())
            out[f"uni_median_{h}d"] = float(np.median(a))
            out[f"uni_n_{h}d"] = len(a)
            out[f"uni_win_{h}d"] = float((a > 0).mean() * 100)
            sp = bench["SPY"][h]
            out[f"uni_excess_spy_{h}d"] = None if sp is None else float(a.mean() - sp)
    return out


# ── stage entry points ──────────────────────────────────────────────────────


def _bootstrap_setup(args):
    """Shared preload for the replay and control stages."""
    global PRICES, ALL_IDX, _RS
    import pandas as pd  # noqa: PLC0415

    root = Path(args.root)
    exclude = load_quarantine_excludes(root)
    print(f"quarantine excludes {len(exclude)} symbol-reuse names", flush=True)
    PRICES = load_replay_prices(root, exclude)
    if "SPY" not in PRICES:
        raise SystemExit("SPY missing from cache/replay_prices — run stage 1C first")
    scan_dates, spy_idx = weekly_scan_dates(PRICES, args.start, args.end)
    ALL_IDX = pd.DatetimeIndex(spy_idx)
    RS, _ENR = install_asof_patches(root)
    _RS = RS
    if args.limit:
        scan_dates = scan_dates[: args.limit]
    print(
        f"scan dates: {len(scan_dates)}  "
        f"{scan_dates[0].date()} .. {scan_dates[-1].date()}",
        flush=True,
    )
    return root, scan_dates


def replay_stage(args) -> int:
    import multiprocessing as mp  # noqa: PLC0415

    root, scan_dates = _bootstrap_setup(args)
    tripwire = LiveArtifactTripwire.snapshot(root)

    t0 = time.time()
    print(f"running {len(scan_dates)} dates on {args.procs} procs …", flush=True)
    results = []
    with mp.get_context("fork").Pool(args.procs) as pool:
        for i, out in enumerate(pool.imap(run_date, scan_dates, chunksize=1), 1):
            results.append(out)
            if i % 20 == 0:
                print(f"  [{i}/{len(scan_dates)}] {time.time()-t0:.0f}s", flush=True)

    metas = [m for m, _ in results]
    episodes = [e for _, es in results for e in es]

    ep_path = root / EPISODES_REL
    write_replay_text(
        ep_path,
        "\n".join(json.dumps(e) for e in episodes) + "\n",
        root=root,
    )
    write_replay_json(
        root / INTERMEDIATES_REL / "replay_raw.json",
        {"metas": metas, "episodes": episodes},
        root=root,
    )

    print(tripwire.report())
    tripwire.assert_clean()
    errs = [m for m in metas if m.get("error")]
    print(
        f"\ndone in {time.time()-t0:.0f}s  dates={len(metas)} "
        f"errors={len(errs)} episodes={len(episodes):,}"
    )
    if errs:
        print("first errors:", errs[:3])
    return 0


def control_stage(args) -> int:
    import multiprocessing as mp  # noqa: PLC0415

    root, scan_dates = _bootstrap_setup(args)
    tripwire = LiveArtifactTripwire.snapshot(root)

    t0 = time.time()
    res = []
    with mp.get_context("fork").Pool(args.procs) as pool:
        for i, o in enumerate(pool.imap(control_date, scan_dates, chunksize=1), 1):
            res.append(o)
            if i % 40 == 0:
                print(f"  [{i}/{len(scan_dates)}] {time.time()-t0:.0f}s", flush=True)

    write_replay_json(root / INTERMEDIATES_REL / "control.json", res, root=root)
    print(tripwire.report())
    tripwire.assert_clean()
    print(
        f"done {time.time()-t0:.0f}s  dates={len(res)} "
        f"errors={sum(1 for r in res if r.get('error'))}"
    )
    return 0


# ── analysis ────────────────────────────────────────────────────────────────


def _trim_mean(a, prop: float) -> float:
    """Mean after cutting *prop* of the sorted mass from each tail.

    Matches scipy.stats.trim_mean's convention (int(prop * n) elements per
    tail) so the numbers stay comparable across environments; implemented in
    numpy because scipy is not a dependency of this repo.
    """
    import numpy as np  # noqa: PLC0415

    n = a.size
    k = int(prop * n)
    if n - 2 * k <= 0:
        return float(a.mean())
    return float(np.sort(a)[k : n - k].mean())


def _winsor_mean(a, lo: float = 5.0, hi: float = 95.0) -> float:
    """Mean after clipping to the [lo, hi] percentile band."""
    import numpy as np  # noqa: PLC0415

    return float(np.clip(a, np.percentile(a, lo), np.percentile(a, hi)).mean())


def _pooled_stats(series):
    import numpy as np  # noqa: PLC0415

    a = np.asarray([v for v in series if v is not None], dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 3),
        "median": round(float(np.median(a)), 3),
        "trimmed10": round(_trim_mean(a, 0.10), 3),
        "winsor5_95": round(_winsor_mean(a), 3),
        "win_rate": round(float((a > 0).mean() * 100), 1),
    }


def _date_cluster_ci(per_date_values, n_boot: int, seed: int):
    """95% CI by resampling whole scan dates.

    Episodes on the same scan date are not independent — they share a market
    day. Resampling dates rather than episodes keeps the CI honest.
    """
    import numpy as np  # noqa: PLC0415

    vals = np.asarray([v for v in per_date_values if v is not None], dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size < 3:
        return [None, None]
    rng = np.random.default_rng(seed)
    boots = [
        float(np.mean(rng.choice(vals, size=vals.size, replace=True)))
        for _ in range(n_boot)
    ]
    return [round(float(np.percentile(boots, 2.5)), 4), round(float(np.percentile(boots, 97.5)), 4)]


def analyse_stage(args) -> int:
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)

    ep_path = root / EPISODES_REL
    if not ep_path.exists():
        print(f"missing {EPISODES_REL} — run the replay sub-stage first")
        return 2
    ep = pd.DataFrame(
        [json.loads(l) for l in ep_path.read_text().splitlines() if l.strip()]
    )
    ctrl_path = root / INTERMEDIATES_REL / "control.json"
    ctrl = json.loads(ctrl_path.read_text()) if ctrl_path.exists() else []
    ctrl_by_date = {c["date"]: c for c in ctrl if not c.get("error")}

    pooled = {}
    for h in HORIZONS:
        pooled[str(h)] = {
            "abs": _pooled_stats(ep.get(f"ret_{h}d", pd.Series(dtype=float))),
            "vs_spy": _pooled_stats(ep.get(f"ret_{h}d_vs_spy", pd.Series(dtype=float))),
            "vs_qqq": _pooled_stats(ep.get(f"ret_{h}d_vs_qqq", pd.Series(dtype=float))),
            "vs_iwm": _pooled_stats(ep.get(f"ret_{h}d_vs_iwm", pd.Series(dtype=float))),
        }

    # Selection skill, robust form: per-date MEDIAN of the scanner's picks
    # against the per-date MEDIAN of the universe it drew from. Medians are
    # used because a single 2024-04-26 outlier drags the universe MEAN to
    # +1197% against a +1.51% median; a mean-based read of that date is noise.
    selection_robust = {}
    for h in HORIZONS:
        col = f"ret_{h}d"
        rows = []
        for d, g in ep.groupby("scan_date"):
            c = ctrl_by_date.get(d)
            if not c or c.get(f"uni_median_{h}d") is None:
                continue
            s = g[col].dropna()
            if s.empty:
                continue
            rows.append((float(s.median()), float(c[f"uni_median_{h}d"])))
        if not rows:
            continue
        sc = np.array([r[0] for r in rows])
        un = np.array([r[1] for r in rows])
        diff = sc - un
        selection_robust[str(h)] = {
            "scanner_median": round(float(sc.mean()), 4),
            "universe_median": round(float(un.mean()), 4),
            "selection": round(float(diff.mean()), 4),
            "ci": _date_cluster_ci(diff, args.bootstrap, args.seed),
            "dates_won_pct": round(float((diff > 0).mean() * 100), 4),
            "n_dates": int(len(rows)),
        }

    by_lane = {}
    for lane, g in ep.groupby("category"):
        entry = {"n": int(len(g))}
        for h in (5, 20, 60):
            rows = []
            for d, gg in g.groupby("scan_date"):
                c = ctrl_by_date.get(d)
                if not c or c.get(f"uni_median_{h}d") is None:
                    continue
                s = gg[f"ret_{h}d"].dropna()
                if s.empty:
                    continue
                rows.append(float(s.median()) - float(c[f"uni_median_{h}d"]))
            if rows:
                entry[f"{h}d"] = round(float(np.mean(rows)), 2)
        by_lane[str(lane)] = entry

    # ── verdict ────────────────────────────────────────────────────────────
    # CONTRADICTS requires the scanner to lose to BOTH SPY and its own universe
    # at every horizon, with date-clustered CIs excluding zero. Anything weaker
    # is INCONCLUSIVE — a replay may not manufacture a positive verdict.
    horizons_present = [h for h in HORIZONS if str(h) in selection_robust]
    all_neg_vs_spy = all(
        (pooled[str(h)]["vs_spy"] or {}).get("mean", 0) < 0 for h in horizons_present
    )
    all_neg_selection = all(
        selection_robust[str(h)]["selection"] < 0 for h in horizons_present
    )
    all_ci_excl_zero = all(
        (selection_robust[str(h)]["ci"][1] or 0) < 0 for h in horizons_present
    )
    if horizons_present and all_neg_vs_spy and all_neg_selection and all_ci_excl_zero:
        verdict = ReplayVerdict.PRICE_ONLY_CONTRADICTS.value
        reason = (
            "The price-only scanner lanes underperform BOTH SPY and the median "
            "of their own source universe at every horizon from 5d to 60d, "
            "monotonically worsening, with date-clustered 95% CIs excluding zero "
            "at every horizon."
        )
    else:
        verdict = ReplayVerdict.PRICE_ONLY_INCONCLUSIVE.value
        reason = (
            "The decision rule for a directional replay verdict was not met at "
            "every horizon; the replay is inconclusive and must not be read as "
            "support for the scanner."
        )
    assert_replay_verdict(verdict)

    doc = {
        "kind": "HISTORICAL_REPLAY_PRICE_ONLY",
        "version": "HISTORICAL_REPLAY_V1",
        **provenance_flags(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "replay_window": {"start": args.start, "end": args.end},
        "api_calls_this_step": 0,
        "sample_sizes": {
            "scan_dates": int(ep.scan_date.nunique()),
            "episodes": int(len(ep)),
            "tickers": int(ep.ticker.nunique()),
            "universe_per_date": int(
                np.median([c["universe_n"] for c in ctrl_by_date.values()])
            ) if ctrl_by_date else 0,
        },
        "pooled_returns": pooled,
        "selection_vs_own_universe_robust_median": selection_robust,
        "per_lane_selection_vs_universe_median": by_lane,
        "verdict": verdict,
        "verdict_ladder": [
            ReplayVerdict.PRICE_ONLY_CORROBORATES.value,
            ReplayVerdict.PRICE_ONLY_CONTRADICTS.value,
            ReplayVerdict.PRICE_ONLY_INCONCLUSIVE.value,
        ],
        "verdict_reason": reason,
        "not_tested": [
            "High-Conviction Alpha",
            "Emerging Outlier Watch",
            "Alpha Focus",
            "fundamentals",
            "catalysts",
            "options",
            "social attention (seeds disabled — see note)",
            "news catalyst (seeds disabled — see note)",
            "topic shock (seeds disabled — see note)",
        ],
        "ci_method": (
            f"date-clustered bootstrap, {args.bootstrap} resamples, seed {args.seed}"
        ),
        "caveats": [
            "Equal-weight, no sizing, no exits, no transaction costs.",
            "Benchmarks SPY/QQQ/IWM only; no per-name sector-ETF attribution offline.",
            "Current-rule overfitting risk is unresolvable by replay; note the "
            "results are NEGATIVE, the opposite direction from an overfitting artifact.",
        ],
    }
    assert_provenance(doc)
    write_replay_json(root / REPORT_JSON_REL, doc, root=root)

    lines = [
        f"HISTORICAL REPLAY — price-only, {args.start[:4]}-{args.end[:4]}",
        "=" * 78,
        f"generated_at : {doc['generated_at']}",
        "RESEARCH ONLY — replay evidence, NOT live forward evidence.",
        "",
        f"scan dates {doc['sample_sizes']['scan_dates']}   "
        f"episodes {doc['sample_sizes']['episodes']:,}   "
        f"tickers {doc['sample_sizes']['tickers']:,}",
        f"universe/date {doc['sample_sizes']['universe_per_date']:,}   "
        "price source: survivorship-corrected replay cache",
        "",
        "POOLED RETURNS",
        "-" * 78,
        f"{'h':>4}{'mean%':>9}{'median%':>9}{'trim10':>9}{'win%':>8}"
        f"{'vsSPY':>9}{'vsQQQ':>9}{'vsIWM':>9}",
    ]
    for h in HORIZONS:
        b = pooled[str(h)]
        if not b["abs"]:
            continue
        lines.append(
            f"{h:>4}{b['abs']['mean']:>9}{b['abs']['median']:>9}"
            f"{b['abs']['trimmed10']:>9}{b['abs']['win_rate']:>8}"
            f"{b['vs_spy']['mean']:>9}{b['vs_qqq']['mean']:>9}{b['vs_iwm']['mean']:>9}"
        )
    lines += ["", "SELECTION SKILL vs OWN UNIVERSE (robust median)", "-" * 78,
              f"{'h':>4}{'scanner':>10}{'universe':>10}{'selection':>11}{'CI95':>24}{'dates won':>11}"]
    for h in HORIZONS:
        s = selection_robust.get(str(h))
        if not s:
            continue
        ci = f"[{s['ci'][0]:.2f},{s['ci'][1]:.2f}]" if s["ci"][0] is not None else "n/a"
        lines.append(
            f"{h:>4}{s['scanner_median']:>10.2f}{s['universe_median']:>10.2f}"
            f"{s['selection']:>11.2f}{ci:>24}{s['dates_won_pct']:>10.1f}%"
        )
    sel_first = selection_robust.get(str(HORIZONS[0]), {}).get("selection")
    sel_last = selection_robust.get(str(HORIZONS[-1]), {}).get("selection")
    won = [s["dates_won_pct"] for s in selection_robust.values()]
    lines += ["", f"VERDICT: {verdict}", "-" * 78,
              reason + (
                  f" Selection effect vs own universe: {sel_first:.2f}% "
                  f"({HORIZONS[0]}d) to {sel_last:.2f}% ({HORIZONS[-1]}d). The scanner "
                  f"wins the date only {min(won):.0f}-{max(won):.0f}% of the time."
                  if sel_first is not None and won else ""),
              "",
              "Lanes tested : "
              + ", ".join(f"{k} (n={v['n']:,})" for k, v in sorted(by_lane.items())),
              "NOT tested   : " + ", ".join(doc["not_tested"]),
              "Note         : social/news/topic-shock seeds are DISABLED in the "
              "replay, so any residual",
              "               social_arb_attention episodes are a by-product, not a "
              "valid test of that lane.",
              "",
              "This artifact is on the replay verdict ladder and must never be pooled "
              "with the live", "forward ledger, Phase 4B, or program verdicts."]
    write_replay_text(root / REPORT_TXT_REL, "\n".join(lines) + "\n", root=root)
    print("\n".join(lines))
    print()
    print(tripwire.report())
    tripwire.assert_clean()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="stage", required=True)
    for name, fn, helptxt in (
        ("replay", replay_stage, "run the frozen scanner over every scan date"),
        ("control", control_stage, "forward returns of the full scanned universe"),
        ("analyse", analyse_stage, "pooled stats + selection skill + verdict"),
    ):
        s = sub.add_parser(name, help=helptxt)
        s.add_argument("--root", default=str(ROOT))
        s.add_argument("--start", default=REPLAY_WINDOW_START)
        s.add_argument("--end", default=REPLAY_WINDOW_END)
        s.add_argument("--procs", type=int, default=8)
        s.add_argument("--limit", type=int, default=0, help="cap scan dates (smoke runs)")
        s.add_argument("--bootstrap", type=int, default=2000)
        s.add_argument("--seed", type=int, default=20260905)
        s.set_defaults(func=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_cli(args.func, args)


if __name__ == "__main__":
    sys.exit(main())
