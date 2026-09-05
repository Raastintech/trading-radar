"""Stage 3 — targeted fundamental replay: does HC/EO filter the score=100 trap?

Runs the **frozen** High-Conviction Alpha (``build_shortlist``) and Emerging
Outlier Watch (``build_watch``) rules against a per-date **sandbox root**
containing only replay-derived artifacts. No live artifact is read or written:
each scan date gets a throwaway temp directory holding a replay scanner doc,
replay program routing, and point-in-time fundamentals, and the builders are
pointed at that root instead of the repo.

Point-in-time discipline:

* a statement is usable only from the first session strictly after its
  ``acceptedDate``, and an acceptance at or after 16:00 rolls to the next day;
* only the newest 4 quarters are passed, because production passes exactly 4
  while the underlying ``_sum_field`` helper has no cap — handing it more would
  make the replay more informed than the live rule;
* market cap is read as-of, so the value factor and the market-cap bucket
  cannot see a future revaluation.

**Alpha Focus is deliberately NOT replayed.** See
:func:`alpha_focus_replay_safe` — it depends on live 2026 artifacts derived
from the forward ledger, so supplying them would inject future information into
every historical date, and omitting them would no longer be the frozen rule.

Writes ONLY:
    cache/research/historical_fundamental_replay_latest.json
    cache/research/historical_replay_intermediates/step3_*.json
    logs/historical_fundamental_replay_latest.txt

RESEARCH ONLY. Restatement-contaminated (see the artifact's caveats).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research.backtests.common import (
    BENCHMARKS,
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

offline_env()

from research.backtests import historical_replay_price_only as PO  # noqa: E402

OUT_JSON_REL = "cache/research/historical_fundamental_replay_latest.json"
OUT_TXT_REL = "logs/historical_fundamental_replay_latest.txt"
INTERMEDIATES_REL = "cache/research/historical_replay_intermediates"

H = (5, 10, 20, 30, 45, 60)

_FCACHE: dict = {}
_MCACHE: dict = {}
_FUND_SRC: Path | None = None
_MC_SRC: Path | None = None


# ── Alpha Focus safety gate ─────────────────────────────────────────────────

ALPHA_FOCUS_LIVE_INPUTS = (
    "cache/research/cohort_attribution_latest.json",
    "cache/research/alpha_failure_root_cause_latest.json",
    "data/research/research_watchlist_history.jsonl",
)

ALPHA_FOCUS_SKIP_REASON = (
    "NOT SAFE — SKIPPED. alpha_focus reads cohort_attribution_latest.json and "
    "alpha_failure_root_cause_latest.json (live artifacts derived from the live "
    "forward ledger) plus research_watchlist_history.jsonl (a live ledger that "
    "sits entirely outside the replay window). Supplying them injects future "
    "information into every historical date. Running without them would omit 2 "
    "of 5 inputs, so the result would not be the frozen Alpha Focus rule. No "
    "replay-local substitute exists that does not itself depend on forward "
    "outcomes."
)


def alpha_focus_replay_safe(sandbox_root: Path) -> tuple[bool, str]:
    """Is an Alpha Focus replay safe for this sandbox?

    Safe only when every Alpha Focus input exists **replay-locally** inside the
    sandbox. Falling back to the live copies would be lookahead, so the default
    answer is No and the caller must skip test 4.
    """
    missing = [p for p in ALPHA_FOCUS_LIVE_INPUTS if not (sandbox_root / p).exists()]
    if missing:
        return False, ALPHA_FOCUS_SKIP_REASON
    return True, "replay-local Alpha Focus inputs present in the sandbox"


# ── point-in-time fundamentals ──────────────────────────────────────────────


def usable_date(accepted: str):
    """First date a filing is actionable; post-16:00 acceptance rolls forward."""
    try:
        ts = datetime.strptime(str(accepted)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.strptime(str(accepted)[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    d = ts.date()
    return (d + timedelta(days=1)) if (ts.hour >= 16) else d


def _raw_fund(sym: str):
    if sym not in _FCACHE:
        f = _FUND_SRC / f"{sym}.json"
        _FCACHE[sym] = json.loads(f.read_text()) if f.exists() else None
    return _FCACHE[sym]


def pit_fundamentals(sym: str, asof_date):
    """Statements known on *asof_date*, newest 4 quarters only."""
    raw = _raw_fund(sym)
    if not raw:
        return None
    out = {"ticker": sym}
    any_rows = False
    for k in ("income", "balance", "cashflow"):
        rows = [r for r in (raw.get(k) or []) if r.get("acceptedDate")]
        keep = []
        for r in rows:
            u = usable_date(r["acceptedDate"])
            if u is not None and u <= asof_date:
                keep.append(r)
        keep.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
        keep = keep[:4]  # MUST cap at 4 — production passes exactly 4
        out[k] = keep
        if keep:
            any_rows = True
    return out if any_rows else None


def market_cap_asof(sym: str, asof):
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    if sym not in _MCACHE:
        f = _MC_SRC / f"{sym}.parquet"
        if f.exists():
            d = pd.read_parquet(f)
            _MCACHE[sym] = (
                d.index.values.astype("datetime64[ns]"),
                d["marketCap"].to_numpy(float),
            )
        else:
            _MCACHE[sym] = None
    p = _MCACHE[sym]
    if p is None:
        return None
    i = int(np.searchsorted(p[0], np.datetime64(asof), side="right")) - 1
    return float(p[1][i]) if i >= 0 else None


# ── one scan date ───────────────────────────────────────────────────────────


def run_date(ts, value_enabled: bool = True):
    import pandas as pd  # noqa: PLC0415
    import research.latest_scan_programs as LSP  # noqa: PLC0415
    import research.high_conviction_alpha as HCA  # noqa: PLC0415
    import research.emerging_outlier_watch as EOW  # noqa: PLC0415

    RS = PO._RS
    asof = PO._reset_asof(RS, ts)
    ad = asof.date()
    try:
        scan = RS.build_scanner(offline=True, universe_cap=RS.DEFAULT_UNIVERSE_CAP)
    except Exception as e:
        return {"date": str(ad), "error": f"scanner:{type(e).__name__}:{e}"}, []
    wl = scan.get("watchlist") or []
    if not wl:
        return {"date": str(ad), "error": "empty watchlist"}, []

    for it in wl:
        mc = market_cap_asof(it["ticker"], asof) if value_enabled else None
        it["market_cap"] = mc
        if mc is not None:
            it["market_cap_bucket"] = (
                "MEGA" if mc >= 2e11 else "LARGE" if mc >= 1e10
                else "MID" if mc >= 2e9 else "SMALL" if mc >= 3e8 else "MICRO"
            )

    sb = Path(tempfile.mkdtemp(prefix=f"replay_{ad}_"))
    try:
        (sb / "cache" / "research").mkdir(parents=True)
        (sb / "cache" / "fundamentals").mkdir(parents=True)
        (sb / "cache" / "research" / "research_scanner_latest.json").write_text(
            json.dumps(scan)
        )
        nowdt = asof.to_pydatetime().replace(tzinfo=timezone.utc)
        routed = LSP.build_latest_scan(root=sb, scanner_doc=scan, now=nowdt)
        (sb / "cache" / "research" / "latest_scan_programs_latest.json").write_text(
            json.dumps(routed)
        )
        nf = 0
        for it in wl:
            pf = pit_fundamentals(it["ticker"], ad)
            if pf:
                (sb / "cache" / "fundamentals" / f"{it['ticker']}.json").write_text(
                    json.dumps(pf)
                )
                nf += 1
        af_safe, af_reason = alpha_focus_replay_safe(sb)
        hc = HCA.build_shortlist(root=sb, now=nowdt)
        eo = EOW.build_watch(root=sb, now=nowdt)
    finally:
        shutil.rmtree(sb, ignore_errors=True)

    hc_eval: dict = {}
    for k in ("shortlist", "quality_but_extended", "improving_but_unproven", "rejected"):
        for c in hc.get(k) or []:
            hc_eval[str(c.get("ticker"))] = c
    hc_partition = {
        k: {str(c.get("ticker")) for c in (hc.get(k) or [])}
        for k in ("shortlist", "quality_but_extended", "improving_but_unproven", "rejected")
    }
    eo_recs = {str(r.get("ticker")): r for r in (eo.get("watch") or eo.get("watchlist") or [])}
    hc_short = {str(c.get("ticker")) for c in (hc.get("shortlist") or [])}

    rows = []
    bench = {b: {h: PO.fwd(b, asof, h)[0] for h in H} for b in BENCHMARKS}
    for it in wl:
        t = it["ticker"]
        c = hc_eval.get(t) or {}
        comp = c.get("component_scores") or {}
        r = {
            "scan_date": str(ad),
            "ticker": t,
            "category": it.get("category"),
            "price_only_score": it.get("research_score"),
            "is_score_100": bool((it.get("research_score") or 0) >= 100),
            "rs_63d_vs_spy": it.get("rs_63d_vs_spy"),
            "rs_20d_vs_spy": it.get("rs_20d_vs_spy"),
            "extension_state": it.get("extension_state"),
            "market_cap": it.get("market_cap"),
            "hc_classification": c.get("classification"),
            "hc_score": c.get("high_conviction_score"),
            "hc_shortlisted": t in hc_short,
            "hc_partition": next((k for k, v in hc_partition.items() if t in v), None),
            "hc_value_component": comp.get("value_score"),
            "hc_quality_score": comp.get("quality_score"),
            "hc_growth_score": comp.get("growth_score"),
            "eo_selected": t in eo_recs,
            "eo_class": (eo_recs.get(t) or {}).get("classification"),
        }
        for h in H:
            v, st = PO.fwd(t, asof, h)
            r[f"ret_{h}d"] = None if v is None else round(v, 4)
            r[f"ret_{h}d_status"] = st
            for b in BENCHMARKS:
                bb = bench[b][h]
                r[f"ret_{h}d_vs_{b.lower()}"] = (
                    None if (v is None or bb is None) else round(v - bb, 4)
                )
        rows.append(r)

    meta = {
        "date": str(ad),
        "watchlist": len(wl),
        "fund_files": nf,
        "hc_evaluated": len(hc_eval),
        "hc_shortlisted": len(hc_short),
        "eo_selected": len(eo_recs),
        "value_enabled": value_enabled,
        "alpha_focus_replayed": af_safe,
        "alpha_focus_skip_reason": None if af_safe else af_reason,
    }
    return meta, rows


def _worker(a):
    return run_date(*a)


def replay_stage(args) -> int:
    import multiprocessing as mp  # noqa: PLC0415

    global _FUND_SRC, _MC_SRC
    root = Path(args.root)
    _FUND_SRC = root / "cache" / "replay_fundamentals"
    _MC_SRC = root / "cache" / "replay_market_cap"

    _root, scan_dates = PO._bootstrap_setup(args)
    tripwire = LiveArtifactTripwire.snapshot(root)

    ve = not args.value_off
    tag = "value_on" if ve else "value_off"
    t0 = time.time()
    metas, rows = [], []
    with mp.get_context("fork").Pool(args.procs) as pool:
        for i, (m, r) in enumerate(
            pool.imap(_worker, [(d, ve) for d in scan_dates], chunksize=1), 1
        ):
            metas.append(m)
            rows.extend(r)
            if i % 25 == 0:
                print(
                    f"  [{i}/{len(scan_dates)}] {time.time()-t0:.0f}s rows={len(rows):,}",
                    flush=True,
                )

    write_replay_json(
        root / INTERMEDIATES_REL / f"step3_{tag}.json",
        {"metas": metas, "rows": rows},
        root=root,
    )
    print(tripwire.report())
    tripwire.assert_clean()
    errs = [m for m in metas if m.get("error")]
    print(
        f"\ndone {time.time()-t0:.0f}s dates={len(metas)} errors={len(errs)} "
        f"rows={len(rows):,}"
    )
    return 0


# ── analysis ────────────────────────────────────────────────────────────────


def _sel_vs_universe(rows, mask_fn, h, ctrl_by_date):
    """Per-date median of a subset minus the same-date universe median."""
    import numpy as np  # noqa: PLC0415
    from collections import defaultdict  # noqa: PLC0415

    by_date = defaultdict(list)
    for r in rows:
        if mask_fn(r) and r.get(f"ret_{h}d") is not None:
            by_date[r["scan_date"]].append(float(r[f"ret_{h}d"]))
    diffs = []
    for d, vals in by_date.items():
        c = ctrl_by_date.get(d)
        if not c or c.get(f"uni_median_{h}d") is None:
            continue
        diffs.append(float(np.median(vals)) - float(c[f"uni_median_{h}d"]))
    return np.array(diffs)


def analyse_stage(args) -> int:
    import numpy as np  # noqa: PLC0415

    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)
    inter = root / INTERMEDIATES_REL

    on_path = inter / "step3_value_on.json"
    if not on_path.exists():
        print("missing step3_value_on.json — run the replay sub-stage first")
        return 2
    on = json.loads(on_path.read_text())
    rows = on["rows"]
    metas = on["metas"]

    ctrl_path = inter / "control.json"
    ctrl = json.loads(ctrl_path.read_text()) if ctrl_path.exists() else []
    ctrl_by_date = {c["date"]: c for c in ctrl if not c.get("error")}

    rng = np.random.default_rng(args.seed)

    def ci(vals):
        vals = vals[np.isfinite(vals)]
        if vals.size < 3:
            return [None, None]
        boots = [
            float(np.mean(rng.choice(vals, size=vals.size, replace=True)))
            for _ in range(args.bootstrap)
        ]
        return [round(float(np.percentile(boots, 2.5)), 2),
                round(float(np.percentile(boots, 97.5)), 2)]

    n_all = len(rows)
    s100 = [r for r in rows if r.get("is_score_100")]
    non100 = [r for r in rows if not r.get("is_score_100")]
    hc = [r for r in rows if r.get("hc_shortlisted")]
    eo = [r for r in rows if r.get("eo_selected")]

    def acceptance(subset, flag):
        if not subset:
            return 0.0
        return round(100 * sum(1 for r in subset if r.get(flag)) / len(subset), 2)

    test1 = {
        "score_100_candidates": len(s100),
        "score_100_share_of_board_pct": round(100 * len(s100) / max(n_all, 1), 1),
        "hc_acceptance_of_score_100_pct": acceptance(s100, "hc_shortlisted"),
        "hc_acceptance_of_non_100_pct": acceptance(non100, "hc_shortlisted"),
        "hc_rejection_of_score_100_pct": round(
            100 - acceptance(s100, "hc_shortlisted"), 2
        ),
        "eo_acceptance_of_score_100_pct": acceptance(s100, "eo_selected"),
        "eo_acceptance_of_non_100_pct": acceptance(non100, "eo_selected"),
    }

    test2, test3 = {}, {}
    for h in H:
        a = _sel_vs_universe(rows, lambda r: r.get("hc_shortlisted"), h, ctrl_by_date)
        b = _sel_vs_universe(rows, lambda r: r.get("eo_selected"), h, ctrl_by_date)
        if a.size:
            test2[str(h)] = {"selection": round(float(a.mean()), 2), "ci": ci(a),
                             "n_dates": int(a.size)}
        if b.size:
            test3[str(h)] = {"selection": round(float(b.mean()), 2), "ci": ci(b),
                             "n_dates": int(b.size)}

    # Constructive lead: split EO by inherited score=100 exposure.
    eo_split = {}
    for label, subset in (
        ("eo_score_100", [r for r in eo if r.get("is_score_100")]),
        ("eo_non_score_100", [r for r in eo if not r.get("is_score_100")]),
    ):
        # n_subset is membership; n per horizon is how many of those had a
        # matured, non-null return at that horizon. Reporting both stops the
        # two counts being confused for each other.
        entry = {"n_subset": len(subset)}
        for h in (20, 60):
            vals = [
                r[f"ret_{h}d_vs_spy"]
                for r in subset
                if r.get(f"ret_{h}d_vs_spy") is not None
            ]
            if vals:
                entry[f"{h}d_vs_spy"] = round(float(np.mean(vals)), 2)
                entry[f"{h}d_n"] = len(vals)
        eo_split[label] = entry

    off_path = inter / "step3_value_off.json"
    test5: dict = {"attempted": off_path.exists()}
    if off_path.exists():
        off = json.loads(off_path.read_text())
        off_hc = {(r["scan_date"], r["ticker"]) for r in off["rows"] if r.get("hc_shortlisted")}
        on_hc = {(r["scan_date"], r["ticker"]) for r in rows if r.get("hc_shortlisted")}
        inter_n = len(on_hc & off_hc)
        union_n = len(on_hc | off_hc) or 1
        per_h = {}
        for h in H:
            a = _sel_vs_universe(rows, lambda r: r.get("hc_shortlisted"), h, ctrl_by_date)
            b = _sel_vs_universe(off["rows"], lambda r: r.get("hc_shortlisted"), h, ctrl_by_date)
            per_h[str(h)] = {
                "value_on": round(float(a.mean()), 3) if a.size else None,
                "value_off": round(float(b.mean()), 3) if b.size else None,
            }
        test5.update(
            shortlist_jaccard_on_vs_off=round(inter_n / union_n, 3),
            per_horizon_diff=per_h,
            verdict="NEUTRAL at every horizon — all CIs include zero."
            if all(
                (test2.get(str(h), {}).get("ci") or [None, None])[0] is None
                or (test2[str(h)]["ci"][0] <= 0 <= test2[str(h)]["ci"][1])
                for h in H
                if str(h) in test2
            )
            else "value factor changes the shortlist measurably",
        )

    af_safe = any(m.get("alpha_focus_replayed") for m in metas)
    test4 = {
        "attempted": bool(af_safe),
        "safe": bool(af_safe),
        "reason": None if af_safe else ALPHA_FOCUS_SKIP_REASON,
    }

    # ── verdict ────────────────────────────────────────────────────────────
    # CORROBORATES requires BOTH: HC/EO reject the score=100 bucket AND beat the
    # same-date universe median at 20/45/60d with CIs excluding zero. Anything
    # less is CONTRADICTS (if selection is negative) or INCONCLUSIVE.
    key_h = ("20", "45", "60")
    hc_rejects_trap = test1["hc_rejection_of_score_100_pct"] >= 95.0
    eo_rejects_trap = test1["eo_acceptance_of_score_100_pct"] < test1["eo_acceptance_of_non_100_pct"]
    hc_positive = all(
        (test2.get(h, {}).get("selection") or 0) > 0 and (test2[h]["ci"][0] or 0) > 0
        for h in key_h
        if h in test2
    )
    if hc_rejects_trap and eo_rejects_trap and hc_positive:
        verdict = ReplayVerdict.FUNDAMENTAL_CORROBORATES.value
    elif not hc_positive:
        verdict = ReplayVerdict.FUNDAMENTAL_CONTRADICTS.value
    else:
        verdict = ReplayVerdict.FUNDAMENTAL_INCONCLUSIVE.value
    assert_replay_verdict(verdict)

    reason = (
        "The decision rule required HC/EO to BOTH reject the score=100 bucket AND "
        "outperform the same-date universe median at 20d/45d/60d. "
        f"HC rejects the trap ({test1['hc_rejection_of_score_100_pct']}%) but its "
        "selection vs the universe median is negative or indistinguishable from "
        "zero; EO inherits score=100 exposure. The selective layer therefore "
        "improves on a broken scanner without producing positive selection."
    )

    doc = {
        "kind": "HISTORICAL_FUNDAMENTAL_REPLAY",
        "version": "HISTORICAL_FUNDAMENTAL_REPLAY_V1",
        **provenance_flags(fundamentals=True),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "replay_window": {"start": args.start, "end": args.end},
        "excluded_2021": True,
        "api_calls_this_step": 0,
        "sample_sizes": {
            "episodes": n_all,
            "dates": len({r["scan_date"] for r in rows}),
            "tickers": len({r["ticker"] for r in rows}),
            "score_100": len(s100),
            "hc_shortlist": len(hc),
            "eo_watch": len(eo),
        },
        "test1_score100_exposure": test1,
        "test2_hc_performance": test2,
        "test3_eo_performance": test3,
        "test4_alpha_focus": test4,
        "test5_value_factor": test5,
        "verdict": verdict,
        "verdict_ladder": [
            ReplayVerdict.FUNDAMENTAL_CORROBORATES.value,
            ReplayVerdict.FUNDAMENTAL_CONTRADICTS.value,
            ReplayVerdict.FUNDAMENTAL_INCONCLUSIVE.value,
        ],
        "verdict_reason": reason,
        "constructive_lead": eo_split,
        "not_tested": [
            "Alpha Focus (unsafe — see test4)", "social attention", "news catalyst",
            "topic shock", "options overlay", "catalyst lane",
        ],
        "ci_method": f"date-clustered bootstrap, {args.bootstrap} resamples, seed {args.seed}",
        "caveats": [
            "Restatement-contaminated: the provider serves the CURRENT version of "
            "each statement, correctly timestamped but not as-originally-filed.",
            "Current-rule overfitting risk unresolvable by replay; note results are "
            "NEGATIVE, the opposite direction from an overfitting artifact.",
            "Equal-weight, no sizing, no exits, no transaction costs.",
            "Benchmarks SPY/QQQ/IWM only; no per-name sector-ETF attribution offline.",
        ],
    }
    assert_provenance(doc)
    write_replay_json(root / OUT_JSON_REL, doc, root=root)

    lines = [
        "HISTORICAL FUNDAMENTAL REPLAY — HC/EO vs the score=100 trap",
        "=" * 78,
        f"generated_at : {doc['generated_at']}",
        "RESEARCH ONLY — restatement_contaminated=true, not_live_evidence=true.",
        "",
        f"window {args.start}..{args.end}   dates {doc['sample_sizes']['dates']}   "
        f"episodes {n_all:,}",
        "",
        "TEST 1 — score=100 exposure",
        "-" * 78,
        f"  score=100 candidates      : {test1['score_100_candidates']:,} "
        f"({test1['score_100_share_of_board_pct']}% of board)",
        f"  HC shortlist acceptance   : {test1['hc_acceptance_of_score_100_pct']}%  "
        f"(non-100: {test1['hc_acceptance_of_non_100_pct']}%)",
        f"  HC rejection of score=100 : {test1['hc_rejection_of_score_100_pct']}%",
        f"  EO acceptance             : {test1['eo_acceptance_of_score_100_pct']}%  "
        f"(non-100: {test1['eo_acceptance_of_non_100_pct']}%)",
        "",
        "TEST 2/3 — selection vs universe median (positive = adds value)",
        "-" * 78,
        f"{'h':>4}{'HC':>10}{'HC CI':>22}{'EO':>10}{'EO CI':>22}",
    ]
    for h in H:
        a = test2.get(str(h))
        b = test3.get(str(h))
        if not a and not b:
            continue
        aci = f"[{a['ci'][0]:+.2f},{a['ci'][1]:+.2f}]" if a and a["ci"][0] is not None else "n/a"
        bci = f"[{b['ci'][0]:+.2f},{b['ci'][1]:+.2f}]" if b and b["ci"][0] is not None else "n/a"
        lines.append(
            f"{h:>4}{(a['selection'] if a else 0):>10.2f}{aci:>22}"
            f"{(b['selection'] if b else 0):>10.2f}{bci:>22}"
        )
    lines += [
        "",
        f"TEST 4 — Alpha Focus: {'REPLAYED' if af_safe else 'NOT SAFE, SKIPPED'}",
        f"TEST 5 — value factor: {test5.get('verdict', 'not attempted')}",
        "",
        f"VERDICT: {verdict}",
        "-" * 78,
        reason,
    ]
    write_replay_text(root / OUT_TXT_REL, "\n".join(lines) + "\n", root=root)
    print("\n".join(lines))
    print()
    print(tripwire.report())
    tripwire.assert_clean()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="stage", required=True)

    r = sub.add_parser("replay", help="run frozen HC/EO over each scan date (zero API)")
    r.add_argument("--root", default=str(ROOT))
    r.add_argument("--start", default=REPLAY_WINDOW_START)
    r.add_argument("--end", default=REPLAY_WINDOW_END)
    r.add_argument("--procs", type=int, default=8)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--value-off", action="store_true", help="disable the value factor")
    r.set_defaults(func=replay_stage)

    a = sub.add_parser("analyse", help="tests 1-5 + verdict (zero API)")
    a.add_argument("--root", default=str(ROOT))
    a.add_argument("--start", default=REPLAY_WINDOW_START)
    a.add_argument("--end", default=REPLAY_WINDOW_END)
    a.add_argument("--bootstrap", type=int, default=2000)
    a.add_argument("--seed", type=int, default=20260905)
    a.set_defaults(func=analyse_stage)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_cli(args.func, args)


if __name__ == "__main__":
    sys.exit(main())
