"""
research/sniper_starvation_audit.py — Phase 1G.17 Task 2.

SNIPER-specific starvation autopsy. Answers, from real artifacts only:

  * how big is the sealed whitelist, and what does it intersect to in the
    live universe snapshot (effective inputs)?
  * how many scan cycles ran and how many opportunities emerged (daemon log)?
  * full rejection-reason distribution (daemon log)?
  * gate-by-gate replay on cached bars: how often does each gate pass alone,
    how often do ALL gates pass, how often does exactly ONE gate block an
    otherwise-valid candidate ("near-miss")?
  * which relaxed gate combinations would have emitted candidates
    historically, and at what weekly rate?
  * is the current gate confluence near-unsatisfiable on this universe?

THRESHOLDS ARE NOT CHANGED. This is measurement only; promotion of any
relaxed variant is the emission-calibration study's job (Task 8) and an
operator decision after that.

Fidelity caveats (documented, not hidden):
  - VIX history is not cached, so the score replay assumes the neutral VIX
    band (no +5/−10 adjustment). Reported as `score_vix_assumption`.
  - The replay's ATR windows are TR-series approximations of the live
    slice-local computation (boundary bar differs by one TR term).
  - Regime gates (VIX ceiling, SPY>MA200) are reported separately and were
    NOT suppressing scans in the audit window (the log shows per-ticker
    rejections, which only happen when regime gates pass).

RESEARCH-ONLY / CACHE-ONLY / READ-ONLY. No provider calls, no DB writes,
no signals, no trade proposals, no gate/execution/governance change.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.scanner_truth import dataio
from research.participation_bottleneck_audit import parse_daemon_log, DEFAULT_SINCE

OUT_JSON = dataio.RESEARCH_CACHE / "sniper_starvation_audit_latest.json"
OUT_TXT = dataio.LOGS_DIR / "sniper_starvation_audit_latest.txt"

SNIPER_SRC = dataio.REPO / "strategies" / "sniper.py"
UNI_SNAPSHOT = dataio.REPO / "cache" / "universe" / "universe_snapshot_latest.json"

# Mirrored live thresholds (provenance strategies/sniper.py; the AST loader
# below overrides these from source so they cannot silently drift).
MIRROR = {
    "VOL_SPIKE_THRESH": 1.4,
    "MIN_SCORE": 70,
    "MIN_RRR": 2.5,
    "BARS_NEEDED": 75,
    "MA50_SLOPE_BARS": 20,
    "ATR_CONTRACTION_THRESH": 0.85,
}

GATES = ("breakout_cross", "volume", "atr_contraction",
         "above_ma50", "ma50_rising", "score")


def load_live_constants() -> Tuple[set, Dict[str, float], bool]:
    """Parse LARGE_CAP_UNIVERSE + gate constants from strategies/sniper.py
    source via AST (no import — importing pulls core.config which needs
    creds). Returns (whitelist, constants, sealed). Falls back to MIRROR."""
    consts = dict(MIRROR)
    whitelist: set = set()
    try:
        tree = ast.parse(SNIPER_SRC.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            tgt = node.targets[0]
            if not isinstance(tgt, ast.Name):
                continue
            if tgt.id == "LARGE_CAP_UNIVERSE":
                whitelist = set(ast.literal_eval(node.value))
            elif tgt.id in consts:
                consts[tgt.id] = ast.literal_eval(node.value)
    except Exception:
        pass
    # "sealed": the whitelist is a hand-written literal in source, not built
    # from data — true by construction when the AST literal parse succeeded.
    return whitelist, consts, bool(whitelist)


# ── vectorized gate replay (pure functions of bars; no look-ahead) ────────────

def gate_frame(df: pd.DataFrame, spy_close: pd.Series,
               consts: Dict[str, float]) -> Optional[pd.DataFrame]:
    """Per-day boolean gate vector + score for one ticker. Each row uses only
    bars at-or-before that row (shift/rolling — no look-ahead)."""
    if df is None or len(df) < consts["BARS_NEEDED"]:
        return None
    close, high, vol = df["close"], df["high"], df["volume"]

    prior_high = high.shift(1).rolling(20, min_periods=20).max()
    breakout_cross = (close > prior_high) & (close.shift(1) <= prior_high)

    vol20 = vol.rolling(20, min_periods=20).mean()
    vol_ratio = vol / vol20
    volume_ok = vol_ratio >= consts["VOL_SPIKE_THRESH"]

    prev_c = close.shift(1)
    tr = pd.concat([(high - df["low"]),
                    (high - prev_c).abs(),
                    (df["low"] - prev_c).abs()], axis=1).max(axis=1)
    recent5 = tr.shift(1).rolling(4, min_periods=4).mean()
    prior15 = tr.shift(6).rolling(14, min_periods=14).mean()
    contraction = recent5 / prior15
    atr_ok = contraction < consts["ATR_CONTRACTION_THRESH"]

    ma50 = close.rolling(50, min_periods=50).mean()
    above_ma50 = close >= ma50
    ma50_rising = ma50 > ma50.shift(int(consts["MA50_SLOPE_BARS"]))

    spy = spy_close.reindex(df.index).ffill()
    rs_pos = (close / close.shift(10) - 1) > (spy / spy.shift(10) - 1)

    vol_pts = np.minimum(25, ((vol_ratio - consts["VOL_SPIKE_THRESH"]) * 40)
                         .fillna(-100).astype(int)).clip(lower=0)
    contr_pts = np.where(contraction < 0.65, 10,
                         np.where(contraction < 0.75, 5, 0))
    score = 50 + vol_pts + np.where(rs_pos, 15, 0) + contr_pts  # VIX adj = 0
    score_ok = score >= consts["MIN_SCORE"]

    out = pd.DataFrame({
        "breakout_cross": breakout_cross.fillna(False),
        "volume": volume_ok.fillna(False),
        "atr_contraction": atr_ok.fillna(False),
        "above_ma50": above_ma50.fillna(False),
        "ma50_rising": ma50_rising.fillna(False),
        "score": pd.Series(score_ok, index=df.index).fillna(False),
        "score_value": pd.Series(score, index=df.index),
    }, index=df.index)
    return out.iloc[int(consts["BARS_NEEDED"]):]


# Relaxation variants: which gates are required. THRESHOLDS UNCHANGED in
# production — these are counterfactual replays only.
VARIANTS: Dict[str, Dict] = {
    "V0_baseline": {"require": list(GATES)},
    "V1_no_first_bar": {"require": list(GATES), "breakout_any": True},
    "V2_no_atr_contraction": {"require": [g for g in GATES if g != "atr_contraction"]},
    "V3_no_ma50_slope": {"require": [g for g in GATES if g != "ma50_rising"]},
    "V4_no_volume": {"require": [g for g in GATES if g != "volume"]},
    "V5_no_score": {"require": [g for g in GATES if g != "score"]},
    "V6_breakout_only": {"require": ["breakout_cross", "above_ma50"]},
    "V7_no_atr_no_first_bar": {
        "require": [g for g in GATES if g != "atr_contraction"],
        "breakout_any": True},
}


def variant_mask(gf: pd.DataFrame, spec: Dict,
                 breakout_any: pd.Series) -> pd.Series:
    req = spec["require"]
    m = pd.Series(True, index=gf.index)
    for g in req:
        col = gf[g]
        if g == "breakout_cross" and spec.get("breakout_any"):
            col = breakout_any
        m &= col
    return m


def build(since: str = DEFAULT_SINCE) -> Dict:
    whitelist, consts, sealed = load_live_constants()

    # effective inputs = whitelist ∩ live sniper universe snapshot
    eff: List[str] = []
    snap_n = None
    try:
        import json
        snap = json.loads(UNI_SNAPSHOT.read_text())
        uni = [str(t).upper() for t in snap.get("sniper_universe") or []]
        snap_n = len(uni)
        eff = sorted(set(uni) & whitelist)
    except Exception:
        pass

    # daemon-log truth for the window
    log = parse_daemon_log(since=since)["SNIPER"]
    cycles = sum(d["cycles"] for d in log.values())
    opportunities = sum(d["opportunities"] for d in log.values())
    rejections: Dict[str, int] = {}
    for d in log.values():
        for k, v in d["rejections"].items():
            rejections[k] = rejections.get(k, 0) + v
    no_breakout = rejections.get("no_breakout", 0)

    # SPY series (regime parquet has multi-year depth; shallow fallback)
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
    spy_close = spy["close"] if spy is not None else pd.Series(dtype=float)

    # ── replay ────────────────────────────────────────────────────────────────
    pool = sorted(whitelist)
    per_gate_pass: Dict[str, int] = {g: 0 for g in GATES}
    solo_blocker: Dict[str, int] = {g: 0 for g in GATES}
    ticker_days = 0
    all_pass_days: List[Tuple[str, str]] = []
    variant_hits: Dict[str, List[Tuple[str, str]]] = {v: [] for v in VARIANTS}
    depth_used: Dict[str, int] = {}
    skipped: List[str] = []

    for t in pool:
        df = dataio.load_prices(t)
        gf = gate_frame(df, spy_close, consts) if df is not None else None
        if gf is None or gf.empty:
            skipped.append(t)
            continue
        depth_used[t] = int(len(df))
        ticker_days += len(gf)
        breakout_any = df["close"] > df["high"].shift(1).rolling(20, min_periods=20).max()
        breakout_any = breakout_any.reindex(gf.index).fillna(False)

        gates_bool = gf[list(GATES)]
        for g in GATES:
            per_gate_pass[g] += int(gates_bool[g].sum())
        n_fail = (~gates_bool).sum(axis=1)
        full = gf.index[n_fail == 0]
        for ts in full:
            all_pass_days.append((t, str(ts.date())))
        for g in GATES:
            solo = gf.index[(n_fail == 1) & (~gates_bool[g])]
            solo_blocker[g] += len(solo)
        for name, spec in VARIANTS.items():
            hits = gf.index[variant_mask(gf, spec, breakout_any)]
            for ts in hits:
                variant_hits[name].append((t, str(ts.date())))

    # Weekly-rate normalization. Coverage is skewed (a few tickers have deep
    # parquet history, most only ~113 shallow bars), so the honest pool rate
    # is: emissions / pool-weeks, where pool-weeks treats the whole pool
    # scanned for the AVERAGE per-ticker coverage. Reported alongside the raw
    # ticker-day rate so the skew is visible, not hidden.
    n_tickers = max(1, len(depth_used))
    pool_weeks = max(1.0, ticker_days / n_tickers / 5.0)

    variants_out = {}
    for name, hits in variant_hits.items():
        dates = sorted({d for _, d in hits})
        variants_out[name] = {
            "emissions": len(hits),
            "distinct_days": len(dates),
            "per_week": round(len(hits) / pool_weeks, 2),
            "spec": {k: v for k, v in VARIANTS[name].items()},
            "recent_examples": [f"{t}@{d}" for t, d in hits[-8:]],
        }

    joint = len(all_pass_days)
    relax_any = max(v["emissions"] for n, v in variants_out.items()
                    if n != "V0_baseline") if variants_out else 0
    # Expected pool emission rate and the probability of the observed
    # 20-trading-day zero-candidate drought under a Poisson model at that
    # rate. This separates "gates are unsatisfiable" from "gates are rare
    # and a multi-week drought is within expectation".
    joint_rate = joint / ticker_days if ticker_days else 0.0
    expected_per_week = joint_rate * n_tickers * 5
    lam_20d = joint_rate * n_tickers * 20
    drought_20d_prob = float(np.exp(-lam_20d)) if lam_20d else 1.0
    near_unsatisfiable = joint == 0 and relax_any >= 5
    if near_unsatisfiable:
        confluence_verdict = "NEAR_UNSATISFIABLE"
    elif expected_per_week < 1.0:
        confluence_verdict = "RARE_BUT_SATISFIABLE"
    else:
        confluence_verdict = "SATISFIABLE"

    pass_rates = {g: round(per_gate_pass[g] / ticker_days, 4) if ticker_days else None
                  for g in GATES}
    independent_joint = float(np.prod([v for v in pass_rates.values() if v is not None])) \
        if ticker_days else None

    return {
        "kind": "sniper_starvation_audit",
        "version": "v1",
        "phase": "1G.17",
        "research_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since,
        "disclaimer": ("read-only SNIPER gate autopsy · thresholds NOT changed "
                       "· no signals, no proposals, no execution/governance "
                       "side effects"),
        "whitelist": {
            "size": len(whitelist),
            "sealed_static_literal": sealed,
            "snapshot_sniper_universe": snap_n,
            # production routes the doctrinal whitelist itself to the scanner
            # (main.SNIPER_DOCTRINAL_POOL), so effective inputs in production
            # = whitelist size. The snapshot intersection shows how little of
            # the whitelist the dynamic universe would surface on its own.
            "effective_inputs_production": len(whitelist),
            "snapshot_intersection": len(eff),
            "snapshot_intersection_tickers": eff,
            "note_frozen_params_doc": ("holdout frozen-params note says "
                                       "'82-name whitelist sealed 2026-05-07'; "
                                       f"the live source literal has "
                                       f"{len(whitelist)} names — doc/code "
                                       "drift, flagged not fixed"),
        },
        "log_window": {
            "scan_cycles": cycles,
            "opportunities": opportunities,
            "rejections": dict(sorted(rejections.items(), key=lambda kv: -kv[1])),
            "no_breakout": no_breakout,
            "no_breakout_share": round(no_breakout / max(1, sum(rejections.values())), 4),
        },
        "replay": {
            "tickers_replayed": len(depth_used),
            "tickers_skipped_no_bars": skipped,
            "ticker_days": ticker_days,
            "bar_depth_median": (int(np.median(list(depth_used.values())))
                                 if depth_used else None),
            "gate_pass_rates": pass_rates,
            "independent_joint_pass_rate": independent_joint,
            "all_gates_pass_days": joint,
            "all_pass_examples": [f"{t}@{d}" for t, d in all_pass_days[-10:]],
            "solo_blocker_counts": solo_blocker,
            "score_vix_assumption": "neutral band (no VIX adjustment) — VIX history not cached",
        },
        "relaxation_variants": variants_out,
        "verdicts": {
            "near_unsatisfiable": near_unsatisfiable,
            "confluence": confluence_verdict,
            "expected_emissions_per_week_pool": round(expected_per_week, 3),
            "prob_20day_zero_drought_at_observed_rate": round(drought_20d_prob, 4),
            "baseline_emissions_per_week": variants_out.get("V0_baseline", {}).get("per_week"),
            "binding_gates_ranked": sorted(
                solo_blocker, key=lambda g: -solo_blocker[g]),
        },
    }


def _render_txt(res: Dict) -> List[str]:
    w, lw, rp, vd = (res["whitelist"], res["log_window"],
                     res["replay"], res["verdicts"])
    lines = [
        f"SNIPER STARVATION AUDIT — {res['generated_at'][:10]} "
        f"(research-only; thresholds NOT changed)",
        "=" * 78,
        f"whitelist={w['size']} (sealed literal: {w['sealed_static_literal']})"
        f"  effective inputs (production)={w['effective_inputs_production']}"
        f"  snapshot∩whitelist={w['snapshot_intersection']}",
        f"log window since {res['since']}: cycles={lw['scan_cycles']}  "
        f"opportunities={lw['opportunities']}  "
        f"no_breakout share={lw['no_breakout_share']:.1%}",
        "",
        f"replay: {rp['tickers_replayed']} tickers · {rp['ticker_days']} "
        f"ticker-days · median depth {rp['bar_depth_median']} bars",
        "gate pass rates: " + "  ".join(
            f"{g}={v:.3f}" for g, v in rp["gate_pass_rates"].items() if v is not None),
        f"ALL gates pass: {rp['all_gates_pass_days']} ticker-days "
        f"(independent-product expectation {rp['independent_joint_pass_rate']:.2e})",
        "solo blockers (all-but-one pass): " + "  ".join(
            f"{g}={n}" for g, n in rp["solo_blocker_counts"].items()),
        "",
        "relaxation variants (counterfactual replay, NOT applied):",
    ]
    for name, v in res["relaxation_variants"].items():
        lines.append(f"  {name:24s} emissions={v['emissions']:4d}  "
                     f"per_week={v['per_week']:6.2f}  "
                     f"e.g. {', '.join(v['recent_examples'][-3:]) or '—'}")
    lines += [
        "",
        f"VERDICT confluence={vd['confluence']}  "
        f"expected/week={vd['expected_emissions_per_week_pool']}  "
        f"P(20d zero drought)={vd['prob_20day_zero_drought_at_observed_rate']}",
        f"binding gates ranked: {', '.join(vd['binding_gates_ranked'])}",
    ]
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="SNIPER starvation audit (1G.17)")
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
