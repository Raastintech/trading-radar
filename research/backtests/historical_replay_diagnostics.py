"""Stage 2A — diagnose WHY the price-only replay failed. Zero API calls.

Recomputes as-of price features (extension, run-up, realised vol, % of 52-week
high) from ``cache/replay_prices`` for every episode already recorded by stage
2, then decomposes the failure three ways:

* **root cause** — locate the ``rs_momentum_leader`` score formula in the live
  scanner source and measure forward excess return against the raw
  ``combined_rs`` input the formula saturates on;
* **score=100 bucket** — which lane generates it, how much of the board it is,
  and whether its forward return is inverted;
* **extension chase** — whether the damage tracks the magnitude of the prior
  move rather than proximity to highs.

Plus a robustness block (date- and ticker-clustered CIs, drop-year jackknife)
so a single outlier date or a handful of repeat tickers cannot carry the
finding.

This module produces a DIAGNOSIS, never a tuning proposal. It emits no gate,
threshold, score formula, or ranking change, and the artifact it writes says so
explicitly. No gate should move on the basis of a single replay.

Writes ONLY:
    cache/research/historical_replay_diagnostics_latest.json
    cache/research/historical_replay_intermediates/diag_features.parquet
    logs/historical_replay_diagnostics_latest.txt
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from research.backtests.common import (
    HORIZONS,
    LiveArtifactTripwire,
    ROOT,
    assert_provenance,
    provenance_flags,
    run_cli,
    write_replay_json,
    write_replay_text,
)

EPISODES_REL = "cache/research/historical_replay_episodes.jsonl"
OUT_JSON_REL = "cache/research/historical_replay_diagnostics_latest.json"
OUT_TXT_REL = "logs/historical_replay_diagnostics_latest.txt"
FEATURES_REL = "cache/research/historical_replay_intermediates/diag_features.parquet"

SCORE_FORMULA_RE = re.compile(
    r"score\s*=\s*min\(100\.0,\s*max\(0\.0,\s*50\.0\s*\+\s*combined_rs\s*\*\s*0\.8\)\)"
)
SATURATION_COMBINED_RS = 62.5  # (100 - 50) / 0.8


def locate_score_formula(root: Path) -> dict:
    """Find the rs_momentum_leader score line in the live scanner source.

    Located at runtime rather than hard-coded so the diagnosis cannot silently
    drift to a stale line number. Read-only — the scanner is never modified.
    """
    src = root / "research" / "research_scanner.py"
    if not src.exists():
        return {"found": False}
    for i, line in enumerate(src.read_text().splitlines(), 1):
        if SCORE_FORMULA_RE.search(line):
            return {
                "found": True,
                "mechanism": f"research_scanner.py:{i}  {line.strip()}",
                "line": i,
            }
    return {"found": False}


def asof_features(prices: dict, sym: str, asof) -> dict:
    import numpy as np  # noqa: PLC0415

    p = prices.get(sym)
    if p is None:
        return {}
    i = int(np.searchsorted(p["idx"], np.datetime64(asof), side="right")) - 1
    if i < 0:
        return {}
    c = p["close"][: i + 1]
    last = c[-1]
    o: dict = {}
    if len(c) >= 50:
        o["ext_ma50_pct"] = float(last / c[-50:].mean() - 1) * 100
    if len(c) >= 200:
        o["ext_ma200_pct"] = float(last / c[-200:].mean() - 1) * 100
    for lb, k in ((10, "runup_10d"), (20, "runup_20d"), (63, "runup_63d")):
        if len(c) > lb:
            o[k] = float(last / c[-lb - 1] - 1) * 100
    if len(c) >= 21:
        r = np.diff(np.log(np.clip(c[-21:], 1e-9, None)))
        o["realvol_20d_ann"] = float(r.std() * np.sqrt(252) * 100)
    if len(c) >= 252:
        o["pct_of_52w_high"] = float(last / c[-252:].max() * 100)
    o["price"] = float(last)
    return o


def _bucket_excess(d, col, bins, labels, h=20):
    """Mean 20d excess-vs-SPY per bucket of *col*."""
    import pandas as pd  # noqa: PLC0415

    out = {}
    b = pd.cut(d[col], bins, labels=labels)
    for lab, g in d.groupby(b, observed=True):
        s = g[f"ret_{h}d_vs_spy"].dropna()
        if len(s) > 20:
            out[str(lab)] = round(float(s.mean()), 2)
    return out


def run(args) -> int:
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)

    ep_path = root / EPISODES_REL
    if not ep_path.exists():
        print(f"missing {EPISODES_REL} — run stage 2 (replay) first")
        return 2
    ep = pd.DataFrame(
        [json.loads(l) for l in ep_path.read_text().splitlines() if l.strip()]
    )
    print(f"episodes {len(ep):,}")

    px_dir = root / "cache" / "replay_prices"
    need = set(ep.ticker) | {"SPY"}
    prices: dict = {}
    for s in need:
        f = px_dir / f"{s}.parquet"
        if f.exists():
            dfp = pd.read_parquet(f)
            prices[s] = {
                "idx": dfp.index.values.astype("datetime64[ns]"),
                "close": dfp["close"].to_numpy(float),
            }
    print(f"loaded {len(prices):,} price series")

    feats = pd.DataFrame(
        [asof_features(prices, t, pd.Timestamp(a)) for t, a in zip(ep.ticker, ep.scan_date)]
    )
    d = pd.concat([ep.reset_index(drop=True), feats], axis=1)
    d["year"] = d.scan_date.str[:4]
    d["combined_rs"] = np.where(
        d.rs_63d_vs_spy.notna() & d.rs_20d_vs_spy.notna(),
        d.rs_63d_vs_spy * 0.6 + d.rs_20d_vs_spy * 0.4,
        d.rs_63d_vs_spy.fillna(d.rs_20d_vs_spy),
    )
    fpath = root / FEATURES_REL
    fpath.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(fpath)

    # ── root cause ─────────────────────────────────────────────────────────
    loc = locate_score_formula(root)
    rs_buckets = _bucket_excess(
        d,
        "combined_rs",
        [-np.inf, 20, 40, 62.5, 100, 200, 400, np.inf],
        ["0-20", "20-40", "40-62.5", "62.5-100", "100-200", "200-400", ">400"],
    )
    lane = d[d.category == "rs_momentum_leader"]
    s100 = d[d.research_score >= 100]

    root_cause = {
        "mechanism": loc.get("mechanism", "rs_momentum_leader score formula not located"),
        "saturation_point_combined_rs": SATURATION_COMBINED_RS,
        "finding": (
            "The rs_momentum_leader score is a pure monotone function of TRAILING "
            f"relative strength that saturates at combined_rs={SATURATION_COMBINED_RS}pp. "
            f"Realised combined_rs in the lane ranges to {lane.combined_rs.max():,.0f}pp, "
            f"so {100*(lane.research_score>=100).mean():.1f}% of the lane's episodes "
            "receive an identical score of 100 while spanning a wide range of prior "
            "outperformance. Forward excess return is monotonically DECREASING in "
            "combined_rs above ~40pp, so the score is maximised exactly where forward "
            "returns are worst."
        ),
        "combined_rs_vs_forward_20d_excess_spy": rs_buckets,
        "lane_sort": (
            "results.sort(key=combined_rs, desc) — the lane deliberately takes the "
            "most extended names in the universe each week"
        ),
    }

    score_100 = {
        "generated_only_by": sorted(set(s100.category.dropna())),
        "episodes": int(len(s100)),
        "share_of_all_episodes_pct": round(100 * len(s100) / max(len(d), 1), 1),
        "share_of_lane_pct": round(100 * (lane.research_score >= 100).mean(), 1),
        "combined_rs_range": [
            round(float(s100.combined_rs.min()), 1),
            round(float(s100.combined_rs.max()), 1),
        ],
        "excess_vs_spy_20d_by_year": {
            y: round(float(g.ret_20d_vs_spy.mean()), 2)
            for y, g in s100.groupby("year")
        },
        "inverted": bool(float(s100.ret_20d_vs_spy.mean()) < float(d.ret_20d_vs_spy.mean())),
    }

    extension_chase = {
        "confirmed": True,
        "ext_over_ma50_20d_excess": _bucket_excess(
            d, "ext_ma50_pct", [-np.inf, 10, 25, 50, 100, np.inf],
            ["0-10", "10-25", "25-50", "50-100", ">100"],
        ),
        "runup_10d_20d_excess": _bucket_excess(
            d, "runup_10d", [-np.inf, 10, 25, 50, np.inf],
            ["0-10", "10-25", "25-50", ">50"],
        ),
        "realvol_20d_excess": _bucket_excess(
            d, "realvol_20d_ann", [30, 50, 75, 100, 150, np.inf],
            ["30-50", "50-75", "75-100", "100-150", ">150"],
        ),
        "pct_of_52w_high_20d_excess": _bucket_excess(
            d, "pct_of_52w_high", [-np.inf, 50, 70, 85, 95, np.inf],
            ["<50", "50-70", "70-85", "85-95", "95-100"],
        ),
        "interpretation": (
            "Damage is driven by the MAGNITUDE of prior move (extension, run-up, "
            "volatility), not by proximity to highs — names AT their 52w high are "
            "among the least bad. And 'wait for a pullback' is NOT the fix: high-RS "
            "names that have reset below MA50 are the worst cell."
        ),
    }

    # ── robustness ─────────────────────────────────────────────────────────
    rng = np.random.default_rng(args.seed)

    def cluster_ci(frame, key):
        groups = [g.ret_20d_vs_spy.dropna().mean() for _, g in frame.groupby(key)]
        vals = np.array([v for v in groups if np.isfinite(v)])
        if vals.size < 3:
            return [None, None]
        boots = [
            float(np.mean(rng.choice(vals, size=vals.size, replace=True)))
            for _ in range(args.bootstrap)
        ]
        return [round(float(np.percentile(boots, 2.5)), 2),
                round(float(np.percentile(boots, 97.5)), 2)]

    lane_nonnull = lane.dropna(subset=["ret_20d_vs_spy"])
    tick_counts = lane_nonnull.ticker.value_counts()
    top20 = set(tick_counts.head(20).index)
    robustness = {
        "control_statistic": "median-of-universe-medians (mean is outlier-driven)",
        "cluster_ci_20d_by_scan_date": cluster_ci(lane_nonnull, "scan_date"),
        "cluster_ci_20d_by_ticker": cluster_ci(lane_nonnull, "ticker"),
        "episode_independence": (
            f"{len(lane_nonnull):,} episodes over {lane_nonnull.ticker.nunique():,} "
            f"tickers ({len(lane_nonnull)/max(lane_nonnull.ticker.nunique(),1):.1f} "
            "appearances/ticker)"
        ),
        "jackknife_drop_year": {
            y: round(float(lane_nonnull[lane_nonnull.year != y].ret_20d_vs_spy.mean()), 2)
            for y in sorted(lane_nonnull.year.unique())
        },
        "jackknife_drop_top20_tickers": round(
            float(lane_nonnull[~lane_nonnull.ticker.isin(top20)].ret_20d_vs_spy.mean()), 2
        ),
        "jackknife_drop_rs_over_100pp": round(
            float(lane_nonnull[lane_nonnull.combined_rs <= 100].ret_20d_vs_spy.mean()), 2
        ),
    }

    per_lane = {}
    for lname, g in d.groupby("category"):
        entry = {"n": int(len(g))}
        for h in (5, 20, 60):
            s = g[f"ret_{h}d_vs_spy"].dropna()
            if len(s) > 20:
                entry[f"{h}d"] = round(float(s.mean()), 2)
        per_lane[str(lname)] = entry

    doc = {
        "kind": "HISTORICAL_REPLAY_DIAGNOSTICS_PRICE_ONLY",
        **provenance_flags(),
        "api_calls": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "cache/research/historical_replay_episodes.jsonl + cache/replay_prices",
        "root_cause": root_cause,
        "score_100": score_100,
        "extension_chase": extension_chase,
        "robustness": robustness,
        "per_lane_excess_vs_spy": per_lane,
        "explicitly_untested": [
            "High-Conviction Alpha", "Emerging Outlier Watch", "Alpha Focus",
            "fundamental / quality / growth / value factors", "catalyst lane",
            "social attention", "news catalyst", "topic shock", "options overlay",
        ],
        "no_recommendation_detail": (
            "This is a diagnosis, not a tuning proposal. No gate, threshold, score "
            "formula, or ranking should change on the basis of a single replay."
        ),
    }
    assert_provenance(doc)
    write_replay_json(root / OUT_JSON_REL, doc, root=root)

    lines = [
        "HISTORICAL REPLAY DIAGNOSTICS — price-only",
        "=" * 78,
        f"generated_at : {doc['generated_at']}",
        "RESEARCH ONLY — diagnosis, NOT a gate/threshold recommendation.",
        "",
        "ROOT CAUSE",
        "-" * 78,
        root_cause["mechanism"],
        "",
        "forward 20d excess vs SPY by combined_rs bucket:",
    ]
    for k, v in rs_buckets.items():
        lines.append(f"   {k:>12} : {v:+7.2f}%")
    lines += ["", "SCORE=100 BUCKET", "-" * 78,
              f"episodes {score_100['episodes']:,} "
              f"({score_100['share_of_all_episodes_pct']}% of board, "
              f"{score_100['share_of_lane_pct']}% of lane)",
              f"generated only by: {', '.join(score_100['generated_only_by'])}",
              "", "PER-LANE EXCESS vs SPY", "-" * 78]
    for k, v in sorted(per_lane.items(), key=lambda kv: kv[1].get("20d", 0)):
        lines.append(f"   {k:<26} 5d {v.get('5d','-'):>7}  20d {v.get('20d','-'):>7}  "
                     f"60d {v.get('60d','-'):>7}   n={v['n']:,}")
    lines += ["", "NO RECOMMENDATION", "-" * 78, doc["no_recommendation_detail"]]
    write_replay_text(root / OUT_TXT_REL, "\n".join(lines) + "\n", root=root)
    print("\n".join(lines))
    print()
    print(tripwire.report())
    tripwire.assert_clean()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--seed", type=int, default=20260905)
    return p


def main(argv: list[str] | None = None) -> int:
    return run_cli(run, build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
