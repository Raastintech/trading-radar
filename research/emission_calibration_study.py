"""
research/emission_calibration_study.py — Phase 1G.17 Task 8.

Counterfactual calibration study: which SNIPER / VOYAGER gate-variant sets
would produce a 1–3 candidate/week research flow per sleeve, and at what
forward quality? PRODUCTION THRESHOLDS ARE NOT MODIFIED — every variant is a
replay on cached bars; promotion of any variant is an operator decision that
belongs to the (proposed) holdout V2 restatement, not to this script.

For each variant: historical emission rate, forward 5/10/20d return,
rel-SPY, win rate, MFE/MAE (10d), false-positive share (10d rel-SPY < 0),
within-pool winner recall, overlap with the recall-shadow board, theme/sector
exposure, and a NEED_MORE_DATA flag at the house n>=15 floor.

Fidelity caveats (stated, not hidden):
  - Replays run on cached bar depth (median ~113 bars for the SNIPER
    whitelist, ~300 for the VOYAGER universe at audit time): emission rates
    from short histories carry wide error bars and are flagged.
  - VOYAGER's earnings / fundamental-quality / 13F gates are NOT replayable
    cache-only (NOT_RETAINED ⇒ treated as pass), so VOYAGER variant emission
    rates are UPPER bounds on live emission.
  - SNIPER score replay assumes the neutral VIX band (no VIX history cached).
  - Forward windows that extend past available bars are excluded (immature),
    never imputed.

RESEARCH-ONLY / CACHE-ONLY / READ-ONLY. No provider calls, no DB writes, no
signals, no proposals, no gate/execution/governance change.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from research.scanner_truth import dataio
from research.sniper_starvation_audit import (
    gate_frame as sniper_gate_frame, load_live_constants, GATES as SNIPER_GATES)

OUT_JSON = dataio.RESEARCH_CACHE / "emission_calibration_study_latest.json"
OUT_TXT = dataio.LOGS_DIR / "emission_calibration_study_latest.txt"
OUT_DOC = dataio.REPO / "docs" / "research" / "EMISSION_CALIBRATION_STUDY.md"
UNI_SNAPSHOT = dataio.REPO / "cache" / "universe" / "universe_snapshot_latest.json"
SHADOW_LANE = dataio.RESEARCH_CACHE / "recall_repair_shadow_lane_latest.json"

TARGET_WEEKLY = (1.0, 3.0)     # research-flow target band per sleeve
N_FLOOR = 15                   # house maturity floor for any verdict
HORIZONS = (5, 10, 20)
WINNER_FWD20 = 0.10            # within-pool 'future winner' = +10% in 20d

# ── VOYAGER variant grid (mirrored thresholds; NOT applied to production) ────
VOY_DEFAULTS = {
    "MIN_PRICE": 5.0, "MIN_AVG_DOLLAR_VOL": 5_000_000.0,
    "MA200_FLOOR": 0.92, "MAX_EXTENSION_MA50": 0.12,
    "RS_MIN": 0.0, "DVOL_TREND_RATIO": 0.85, "REQUIRE_ARCHETYPE": True,
}
VOY_VARIANTS: Dict[str, Dict] = {
    "Y0_baseline": {},
    "Y1_ext_20pct": {"MAX_EXTENSION_MA50": 0.20},
    "Y2_no_extension_gate": {"MAX_EXTENSION_MA50": 10.0},
    "Y3_rs_minus2pct": {"RS_MIN": -0.02},
    "Y4_floor_088": {"MA200_FLOOR": 0.88},
    "Y5_no_archetype": {"REQUIRE_ARCHETYPE": False},
    "Y6_ext20_rs0_no_arch": {"MAX_EXTENSION_MA50": 0.20,
                             "REQUIRE_ARCHETYPE": False},
}

# ── SNIPER variant grid (consts overrides for the shared gate_frame) ─────────
SNI_VARIANTS: Dict[str, Dict] = {
    "S0_baseline": {},
    "S1_no_atr_contraction": {"drop": ["atr_contraction"]},
    "S2_vol_1_2": {"VOL_SPIKE_THRESH": 1.2},
    "S3_score_60": {"MIN_SCORE": 60},
    "S4_no_first_bar": {"breakout_any": True},
    "S5_no_atr_vol_1_2": {"drop": ["atr_contraction"], "VOL_SPIKE_THRESH": 1.2},
    "S6_no_slope": {"drop": ["ma50_rising"]},
}


def _spy_series() -> Optional[pd.Series]:
    reg = dataio.PRICES_DIR / "SPY_regime.parquet"
    if reg.exists():
        try:
            df = pd.read_parquet(reg).sort_index()
            df.index = pd.to_datetime(df.index)
            return df["close"]
        except Exception:
            pass
    df = dataio.load_prices("SPY")
    return df["close"] if df is not None else None


def voyager_gate_frame(df: pd.DataFrame, spy_close: pd.Series,
                       p: Dict) -> Optional[pd.DataFrame]:
    """Vectorized VOYAGER structural gates per day (cache-only mirror;
    earnings/fundamentals/13F NOT_RETAINED). No look-ahead: every value is a
    rolling/shifted function of bars at-or-before the row."""
    if df is None or len(df) < 210:
        return None
    close, vol = df["close"], df["volume"]
    dvol = close * vol
    ma50 = close.rolling(50, min_periods=50).mean()
    ma200 = close.rolling(200, min_periods=200).mean()
    dvol20 = dvol.rolling(20, min_periods=20).mean()
    dvol_base40 = dvol.shift(20).rolling(40, min_periods=40).mean()
    dvol_ratio = dvol20 / dvol_base40
    spy = spy_close.reindex(df.index).ffill()
    rs50 = (close / close.shift(50) - 1) - (spy / spy.shift(50) - 1)
    ext = (close - ma50) / ma50
    dist200 = close / ma200

    gates = pd.DataFrame(index=df.index)
    gates["price_floor"] = close >= p["MIN_PRICE"]
    gates["dvol_floor"] = dvol20 >= p["MIN_AVG_DOLLAR_VOL"]
    gates["ma200_floor"] = dist200 >= p["MA200_FLOOR"]
    gates["not_extended"] = ext <= p["MAX_EXTENSION_MA50"]
    gates["rs_50d"] = rs50 > p["RS_MIN"]
    gates["dvol_trend"] = dvol_ratio >= p["DVOL_TREND_RATIO"]

    if p["REQUIRE_ARCHETYPE"]:
        golden = ma50 > ma200
        tightness = (close.rolling(20).std() / close.rolling(20).mean())
        base_acc = golden & (ext.abs() <= 0.05) & (tightness <= 0.03)
        ma50_30ago = close.shift(30).rolling(50, min_periods=50).mean()
        pullback = golden & (ext < -0.02) & (ext >= -0.10) & (ma50 > ma50_30ago)
        gap = (ma200 - ma50) / ma200
        ma50_20ago = close.shift(20).rolling(50, min_periods=50).mean()
        early = (~golden) & (gap <= 0.03) & (ma50 > ma50_20ago) & (dvol_ratio >= 1.15)
        gates["archetype"] = (base_acc | pullback | early)
    else:
        gates["archetype"] = True

    return gates.fillna(False).iloc[200:]


def forward_stats(df: pd.DataFrame, spy_close: pd.Series,
                  hits: pd.DatetimeIndex) -> List[Dict]:
    """Point-in-time forward outcomes for each emission day; immature
    windows excluded."""
    close = df["close"]
    spy = spy_close.reindex(df.index).ffill()
    pos = {ts: i for i, ts in enumerate(df.index)}
    out = []
    for ts in hits:
        i = pos.get(ts)
        if i is None:
            continue
        row: Dict = {"date": str(ts.date())}
        entry = float(close.iloc[i])
        for h in HORIZONS:
            j = i + h
            if j >= len(close):
                row[f"fwd{h}"] = None
                row[f"rel{h}"] = None
                continue
            r = float(close.iloc[j]) / entry - 1
            s = float(spy.iloc[j]) / float(spy.iloc[i]) - 1 if spy.iloc[i] else 0.0
            row[f"fwd{h}"] = r
            row[f"rel{h}"] = r - s
        j10 = min(i + 10, len(close) - 1)
        if j10 > i:
            win = close.iloc[i + 1: j10 + 1]
            row["mfe10"] = float(win.max()) / entry - 1
            row["mae10"] = float(win.min()) / entry - 1
        out.append(row)
    return out


def _agg(rows: List[Dict], pool_weeks: float, shadow_tickers: set,
         emitted_tickers: List[str], themes: Dict[str, str]) -> Dict:
    def col(k):
        return [r[k] for r in rows if r.get(k) is not None]
    n5, n10, n20 = (len(col(f"rel{h}")) for h in HORIZONS)
    rel10 = col("rel10")
    theme_mix: Dict[str, int] = {}
    for t in emitted_tickers:
        theme_mix[themes.get(t, "unknown")] = theme_mix.get(themes.get(t, "unknown"), 0) + 1
    return {
        "emissions": len(rows),
        "per_week": round(len(rows) / pool_weeks, 2) if pool_weeks else None,
        "n_mature_5d": n5, "n_mature_10d": n10, "n_mature_20d": n20,
        "fwd_avg": {str(h): (round(float(np.mean(col(f'fwd{h}'))), 4)
                             if col(f"fwd{h}") else None) for h in HORIZONS},
        "rel_spy_avg": {str(h): (round(float(np.mean(col(f'rel{h}'))), 4)
                                 if col(f"rel{h}") else None) for h in HORIZONS},
        "win_rate_10d": (round(100 * sum(1 for x in col("fwd10") if x > 0)
                               / n10, 1) if n10 else None),
        "false_positive_share_10d": (round(sum(1 for x in rel10 if x < 0)
                                           / len(rel10), 3) if rel10 else None),
        "mfe10_avg": (round(float(np.mean(col("mfe10"))), 4)
                      if col("mfe10") else None),
        "mae10_avg": (round(float(np.mean(col("mae10"))), 4)
                      if col("mae10") else None),
        "shadow_overlap_tickers": sorted(set(emitted_tickers) & shadow_tickers),
        "theme_exposure": dict(sorted(theme_mix.items(), key=lambda kv: -kv[1])),
        "need_more_data": n10 < N_FLOOR,
    }


def run_sleeve(sleeve: str, tickers: List[str], variants: Dict[str, Dict],
               spy_close: pd.Series, shadow_tickers: set,
               themes: Dict[str, str], consts: Dict) -> Dict:
    frames: Dict[str, pd.DataFrame] = {}
    for t in tickers:
        df = dataio.load_prices(t)
        if df is not None and not df.empty:
            frames[t] = df

    ticker_days = 0
    results: Dict[str, Dict] = {}
    per_variant_rows: Dict[str, List[Dict]] = {v: [] for v in variants}
    per_variant_tickers: Dict[str, List[str]] = {v: [] for v in variants}
    winner_tickers: set = set()
    winners_caught: Dict[str, set] = {v: set() for v in variants}

    for t, df in frames.items():
        close = df["close"]
        fwd20 = close.shift(-20) / close - 1
        is_winner_day = fwd20 >= WINNER_FWD20

        if sleeve == "SNIPER":
            base_gf = sniper_gate_frame(df, spy_close, consts)
            if base_gf is None:
                continue
            ticker_days += len(base_gf)
            breakout_any = df["close"] > df["high"].shift(1).rolling(20, min_periods=20).max()
            for vname, spec in variants.items():
                c2 = dict(consts)
                for k, v in spec.items():
                    if k in c2:
                        c2[k] = v
                gf = (sniper_gate_frame(df, spy_close, c2)
                      if any(k in consts for k in spec) else base_gf)
                if gf is None:
                    continue
                req = [g for g in SNIPER_GATES if g not in spec.get("drop", [])]
                mask = pd.Series(True, index=gf.index)
                for g in req:
                    colv = gf[g]
                    if g == "breakout_cross" and spec.get("breakout_any"):
                        colv = breakout_any.reindex(gf.index).fillna(False)
                    mask &= colv
                hits = gf.index[mask]
                per_variant_rows[vname] += forward_stats(df, spy_close, hits)
                per_variant_tickers[vname] += [t] * len(hits)
                hit_winner = is_winner_day.reindex(hits).fillna(False)
                if bool(hit_winner.any()):
                    winners_caught[vname].add(t)
        else:
            for vname, spec in variants.items():
                p = dict(VOY_DEFAULTS)
                p.update(spec)
                gf = voyager_gate_frame(df, spy_close, p)
                if gf is None:
                    continue
                if vname == "Y0_baseline":
                    ticker_days += len(gf)
                mask = gf.all(axis=1)
                hits = gf.index[mask]
                per_variant_rows[vname] += forward_stats(df, spy_close, hits)
                per_variant_tickers[vname] += [t] * len(hits)
                hit_winner = is_winner_day.reindex(hits).fillna(False)
                if bool(hit_winner.any()):
                    winners_caught[vname].add(t)

        if bool(is_winner_day.fillna(False).any()):
            winner_tickers.add(t)

    n_tickers = max(1, len(frames))
    pool_weeks = max(1.0, ticker_days / n_tickers / 5.0)
    for vname in variants:
        agg = _agg(per_variant_rows[vname], pool_weeks, shadow_tickers,
                   per_variant_tickers[vname], themes)
        agg["winner_recall_within_pool"] = (
            round(len(winners_caught[vname]) / len(winner_tickers), 3)
            if winner_tickers else None)
        agg["meets_target_band"] = (
            agg["per_week"] is not None
            and TARGET_WEEKLY[0] <= agg["per_week"] <= TARGET_WEEKLY[1])
        agg["spec"] = variants[vname]
        results[vname] = agg

    return {
        "tickers_replayed": len(frames),
        "ticker_days": ticker_days,
        "pool_weeks": round(pool_weeks, 1),
        "winner_tickers_in_pool": len(winner_tickers),
        "variants": results,
    }


def build() -> Dict:
    snap = json.loads(UNI_SNAPSHOT.read_text())
    voy_universe = [str(t).upper() for t in snap.get("voyager_universe") or []]
    whitelist, consts, _ = load_live_constants()
    spy_close = _spy_series()
    if spy_close is None:
        raise RuntimeError("SPY series unavailable — cannot compute rel-SPY")

    shadow_tickers: set = set()
    try:
        lane = json.loads(SHADOW_LANE.read_text())
        shadow_tickers = {str(c.get("ticker") or "").upper()
                          for c in lane.get("candidates") or []}
    except Exception:
        pass
    profiles = dataio.load_profiles()
    themes = {t: dataio.classify_theme(profiles.get(t)) for t in
              set(voy_universe) | whitelist}

    sniper = run_sleeve("SNIPER", sorted(whitelist), SNI_VARIANTS,
                        spy_close, shadow_tickers, themes, consts)
    voyager = run_sleeve("VOYAGER", voy_universe, VOY_VARIANTS,
                         spy_close, shadow_tickers, themes, consts)

    def qualify(sleeve_res: Dict) -> Dict[str, List[str]]:
        strict, near = [], []
        lo, hi = TARGET_WEEKLY
        for name, v in sleeve_res["variants"].items():
            rel10 = v["rel_spy_avg"].get("10")
            ok_quality = (not v["need_more_data"]
                          and rel10 is not None and rel10 >= 0)
            if not ok_quality or v["per_week"] is None:
                continue
            if lo <= v["per_week"] <= hi:
                strict.append(name)
            elif lo * 0.8 <= v["per_week"] <= hi * 1.25:
                near.append(name)   # banding artifact, not a different regime
        return {"strict": strict, "near_band": near}

    sniper_q, voyager_q = qualify(sniper), qualify(voyager)
    return {
        "kind": "emission_calibration_study",
        "version": "v1",
        "phase": "1G.17",
        "research_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": ("counterfactual replay only · PRODUCTION THRESHOLDS "
                       "NOT MODIFIED · no signals, no proposals, no side "
                       "effects · variant promotion = operator decision via "
                       "holdout V2 restatement"),
        "target_weekly_band": list(TARGET_WEEKLY),
        "n_floor": N_FLOOR,
        "sleeves": {"SNIPER": sniper, "VOYAGER": voyager},
        "verdicts": {
            "sniper_qualifying_variants": sniper_q["strict"],
            "sniper_near_band_variants": sniper_q["near_band"],
            "voyager_qualifying_variants": voyager_q["strict"],
            "voyager_near_band_variants": voyager_q["near_band"],
            "any_qualifying": bool(sniper_q["strict"] or voyager_q["strict"]
                                   or sniper_q["near_band"]
                                   or voyager_q["near_band"]),
            "note": ("a variant qualifies only if it lands in the 1-3/week "
                     "band AND clears the n>=15 maturity floor AND has "
                     "non-negative 10d rel-SPY; near_band = same quality bar "
                     "within [0.8x, 1.25x] of the band (banding artifact "
                     "tolerance); if none qualify the answer is 'collect "
                     "more depth/history', NOT 'force the target'"),
        },
    }


def _render_txt(res: Dict) -> List[str]:
    lines = [
        f"EMISSION CALIBRATION STUDY — {res['generated_at'][:10]} "
        f"(counterfactual; production thresholds NOT modified)",
        "=" * 90,
    ]
    for sleeve, sr in res["sleeves"].items():
        lines += [
            "",
            f"{sleeve}: {sr['tickers_replayed']} tickers · "
            f"{sr['ticker_days']} ticker-days · pool_weeks={sr['pool_weeks']}"
            f" · winner tickers in pool: {sr['winner_tickers_in_pool']}",
            f"{'variant':26s} {'emit':>5s} {'/wk':>6s} {'rel5':>8s} "
            f"{'rel10':>8s} {'rel20':>8s} {'win10':>6s} {'FP10':>6s} "
            f"{'recall':>7s} {'n10':>4s} {'flag'}",
        ]
        for name, v in sr["variants"].items():
            rel = v["rel_spy_avg"]
            flag = ("TARGET+QUALITY" if v["meets_target_band"]
                    and not v["need_more_data"]
                    and (rel.get("10") or -1) >= 0 else
                    ("NEED_MORE_DATA" if v["need_more_data"] else
                     ("target_band" if v["meets_target_band"] else "")))
            lines.append(
                f"{name:26s} {v['emissions']:5d} "
                f"{v['per_week'] if v['per_week'] is not None else '—':>6} "
                f"{_f(rel.get('5')):>8s} {_f(rel.get('10')):>8s} "
                f"{_f(rel.get('20')):>8s} "
                f"{v['win_rate_10d'] if v['win_rate_10d'] is not None else '—':>6} "
                f"{_f(v['false_positive_share_10d'], pct=False):>6s} "
                f"{_f(v['winner_recall_within_pool'], pct=False):>7s} "
                f"{v['n_mature_10d']:4d} {flag}")
    v = res["verdicts"]
    lines += [
        "",
        f"qualifying — SNIPER: {v['sniper_qualifying_variants'] or 'NONE'} "
        f"(near-band: {v['sniper_near_band_variants'] or 'none'})  "
        f"VOYAGER: {v['voyager_qualifying_variants'] or 'NONE'} "
        f"(near-band: {v['voyager_near_band_variants'] or 'none'})",
        f"note: {v['note']}",
    ]
    return lines


def _f(x, pct=True) -> str:
    if x is None:
        return "—"
    return f"{x:+.2%}" if pct else f"{x:.3f}"


def _write_doc(res: Dict) -> None:
    v = res["verdicts"]
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    body = [f"""# Emission Calibration Study (Phase 1G.17)

Generated: {res['generated_at'][:19]}Z · research-only counterfactual replay.
**No production threshold was modified by this study.** Promotion of a
variant is an operator decision tied to the holdout V2 restatement proposal.

Target research flow: **{res['target_weekly_band'][0]}–{res['target_weekly_band'][1]}
candidates/week/sleeve**; a variant qualifies only with n≥{res['n_floor']}
mature 10d outcomes AND non-negative 10d rel-SPY. If nothing qualifies, the
honest answer is to extend bar depth / collect history — not to force flow.
"""]
    for sleeve, sr in res["sleeves"].items():
        body.append(f"\n## {sleeve} ({sr['tickers_replayed']} tickers, "
                    f"{sr['ticker_days']} ticker-days)\n")
        body.append("| Variant | Emissions | /week | rel-SPY 5d | 10d | 20d "
                    "| win10 | FP10 | recall | n10 | Status |")
        body.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for name, vv in sr["variants"].items():
            rel = vv["rel_spy_avg"]
            status = ("**QUALIFIES**" if name in
                      v[f"{sleeve.lower()}_qualifying_variants"] else
                      ("NEED_MORE_DATA" if vv["need_more_data"] else "—"))
            body.append(
                f"| `{name}` | {vv['emissions']} | {vv['per_week']} | "
                f"{_f(rel.get('5'))} | {_f(rel.get('10'))} | "
                f"{_f(rel.get('20'))} | {vv['win_rate_10d']} | "
                f"{_f(vv['false_positive_share_10d'], pct=False)} | "
                f"{_f(vv['winner_recall_within_pool'], pct=False)} | "
                f"{vv['n_mature_10d']} | {status} |")
    body.append(f"""
## Verdict

SNIPER qualifying: {v['sniper_qualifying_variants'] or '**NONE**'} ·
VOYAGER qualifying: {v['voyager_qualifying_variants'] or '**NONE**'}

{v['note']}

## Fidelity caveats

- Replay depth is the cached bar depth; short histories ⇒ wide error bars.
- VOYAGER earnings/fundamental/13F gates are NOT_RETAINED (treated as pass)
  ⇒ VOYAGER emission rates are upper bounds.
- SNIPER score assumes neutral VIX band (no VIX history cached).
- Forward windows past available bars are excluded, never imputed.

*Sidecar:* `cache/research/emission_calibration_study_latest.json` ·
*Runner:* `./scripts/run_research_cycle.sh emission-calibration`
""")
    OUT_DOC.write_text("\n".join(body))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Emission calibration study (1G.17)")
    ap.parse_args(argv)
    res = build()
    dataio.write_json(OUT_JSON, res)
    lines = _render_txt(res)
    dataio.write_text(OUT_TXT, lines)
    _write_doc(res)
    print("\n".join(lines))
    print(f"\nwrote {dataio.rel_to_repo(OUT_JSON)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
