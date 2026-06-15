"""
research/recall_shadow_gk_forward.py — Phase 1G.17A forward validation.

Measures the FROZEN 1G.17A cohort (research/recall_shadow_gk_cohort_freeze.py
→ data/research/recall_shadow_gk_cohort_1g17a.json) forward at 1/3/5/10/20
trading days from each name's anchor bar, answering three pre-registered
questions:

  Q1  Did 'Too Extended' BLOCK names continue higher (the 1G.13/1G.14
      over-blocking pattern) or did the block save capital?
  Q2  Did WATCH names outperform BLOCK names (does the Gatekeeper's split
      point the right way on this surface)?
  Q3  Does recall-shadow + Gatekeeper add precision over the raw shadow
      board (WATCH vs cohort-average spread)?

Per group (WATCH / BLOCK / TOO_EXTENDED_BLOCK subset / ALL) and horizon:
absolute forward return, rel-SPY, rel-QQQ, win rate, MFE/MAE inside the
horizon window. Immature horizons (insufficient forward bars) are excluded,
never imputed; verdicts carry NEED_MORE_DATA below the house n>=15 floor —
with n=7 BLOCK names the BLOCK-side answers stay directional, not proven,
and the report says so.

The frozen cohort file is read-only here — outcomes never rewrite entries.
Each run appends one dated summary row to the history JSONL (idempotent per
as-of date) so verdict drift over maturation stays auditable.

RESEARCH-ONLY / CACHE-ONLY / READ-ONLY. No paper signals, no trade
proposals, no execution/governance/live-capital change, no DB writes.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from research.scanner_truth import dataio

FROZEN = dataio.HISTORY_DIR / "recall_shadow_gk_cohort_1g17a.json"
OUT_JSON = dataio.RESEARCH_CACHE / "recall_shadow_gk_forward_latest.json"
OUT_TXT = dataio.LOGS_DIR / "recall_shadow_gk_forward_latest.txt"
HISTORY = dataio.HISTORY_DIR / "recall_shadow_gk_forward_history.jsonl"

HORIZONS = (1, 3, 5, 10, 20)
N_FLOOR = 15
GROUPS = ("WATCH", "BLOCK", "TOO_EXTENDED_BLOCK", "ALL")


def _bench(sym: str) -> Optional[pd.Series]:
    if sym == "SPY":
        reg = dataio.PRICES_DIR / "SPY_regime.parquet"
        if reg.exists():
            try:
                df = pd.read_parquet(reg).sort_index()
                df.index = pd.to_datetime(df.index)
                return df["close"]
            except Exception:
                pass
    df = dataio.load_prices(sym)
    return df["close"] if df is not None else None


def ticker_outcomes(row: Dict, spy: pd.Series, qqq: Optional[pd.Series]) -> Dict:
    """Forward outcomes for one frozen row, anchored at its recorded price
    bar. Trading-day offsets use the ticker's own bar index; benchmarks are
    aligned by date. Missing/immature → None (excluded upstream)."""
    out: Dict = {"ticker": row["ticker"], "gk_status": row["gk_status"],
                 "too_extended_block": row.get("too_extended_block", False)}
    t = row["ticker"]
    anchor_date = row.get("price_bar_date")
    entry = row.get("price_at_refresh")
    df = dataio.load_prices(t)
    if df is None or df.empty or not anchor_date or entry in (None, 0):
        out["error"] = "no_anchor_or_bars"
        return out
    df = df.sort_index()
    idx = df.index[df.index <= anchor_date]
    if len(idx) == 0:
        out["error"] = "anchor_before_history"
        return out
    i = df.index.get_loc(idx[-1])
    close = df["close"]
    spy_a = spy.reindex(df.index).ffill()
    qqq_a = qqq.reindex(df.index).ffill() if qqq is not None else None
    for h in HORIZONS:
        j = i + h
        if j >= len(close):
            out[f"h{h}"] = None        # immature — excluded, not imputed
            continue
        fwd = float(close.iloc[j]) / float(entry) - 1
        rel_spy = rel_qqq = None
        if spy_a is not None and pd.notna(spy_a.iloc[i]) and spy_a.iloc[i]:
            rel_spy = fwd - (float(spy_a.iloc[j]) / float(spy_a.iloc[i]) - 1)
        if qqq_a is not None and pd.notna(qqq_a.iloc[i]) and qqq_a.iloc[i]:
            rel_qqq = fwd - (float(qqq_a.iloc[j]) / float(qqq_a.iloc[i]) - 1)
        win = close.iloc[i + 1: j + 1]
        out[f"h{h}"] = {
            "fwd": round(fwd, 4),
            "rel_spy": None if rel_spy is None else round(rel_spy, 4),
            "rel_qqq": None if rel_qqq is None else round(rel_qqq, 4),
            "mfe": round(float(win.max()) / float(entry) - 1, 4),
            "mae": round(float(win.min()) / float(entry) - 1, 4),
        }
    return out


def _group_rows(outcomes: List[Dict], group: str) -> List[Dict]:
    if group == "ALL":
        return outcomes
    if group == "TOO_EXTENDED_BLOCK":
        return [o for o in outcomes if o.get("too_extended_block")]
    return [o for o in outcomes if o.get("gk_status") == group]


def aggregate(outcomes: List[Dict]) -> Dict:
    res: Dict = {}
    for group in GROUPS:
        rows = _group_rows(outcomes, group)
        g: Dict = {"n_names": len(rows), "by_horizon": {}}
        for h in HORIZONS:
            pts = [r[f"h{h}"] for r in rows
                   if isinstance(r.get(f"h{h}"), dict)]
            if not pts:
                g["by_horizon"][str(h)] = {"n": 0}
                continue
            def m(k):
                vals = [p[k] for p in pts if p.get(k) is not None]
                return round(float(np.mean(vals)), 4) if vals else None
            g["by_horizon"][str(h)] = {
                "n": len(pts),
                "fwd_avg": m("fwd"),
                "rel_spy_avg": m("rel_spy"),
                "rel_qqq_avg": m("rel_qqq"),
                "win_rate": round(100 * sum(1 for p in pts if p["fwd"] > 0)
                                  / len(pts), 1),
                "mfe_avg": m("mfe"),
                "mae_avg": m("mae"),
                "need_more_data": len(pts) < N_FLOOR,
            }
        res[group] = g
    return res


def verdicts(agg: Dict) -> Dict:
    """Pre-registered Q1-Q3, answered per matured horizon. A horizon answers
    only when BOTH sides have data; every answer carries its n."""
    q1, q2, q3 = {}, {}, {}
    for h in HORIZONS:
        hs = str(h)
        teb = agg["TOO_EXTENDED_BLOCK"]["by_horizon"].get(hs) or {}
        w = agg["WATCH"]["by_horizon"].get(hs) or {}
        b = agg["BLOCK"]["by_horizon"].get(hs) or {}
        a = agg["ALL"]["by_horizon"].get(hs) or {}
        if teb.get("n"):
            q1[hs] = {
                "n": teb["n"],
                "continued_higher_abs": (teb.get("fwd_avg") or 0) > 0,
                "continued_higher_rel_spy": (teb.get("rel_spy_avg") or 0) > 0,
                "fwd_avg": teb.get("fwd_avg"),
                "rel_spy_avg": teb.get("rel_spy_avg"),
            }
        if w.get("n") and b.get("n"):
            spread = ((w.get("rel_spy_avg") or 0)
                      - (b.get("rel_spy_avg") or 0))
            q2[hs] = {
                "n_watch": w["n"], "n_block": b["n"],
                "watch_rel_spy": w.get("rel_spy_avg"),
                "block_rel_spy": b.get("rel_spy_avg"),
                "watch_outperformed_block": spread > 0,
                "spread_rel_spy": round(spread, 4),
            }
        if w.get("n") and a.get("n"):
            edge = ((w.get("rel_spy_avg") or 0) - (a.get("rel_spy_avg") or 0))
            q3[hs] = {
                "n_watch": w["n"], "n_all": a["n"],
                "watch_minus_cohort_rel_spy": round(edge, 4),
                "gk_adds_precision": edge > 0,
            }
    mature = any((agg["WATCH"]["by_horizon"].get(str(h)) or {}).get("n", 0)
                 >= N_FLOOR for h in HORIZONS)
    return {
        "q1_too_extended_blocks_continued_higher": q1,
        "q2_watch_vs_block": q2,
        "q3_gk_precision_over_shadow_board": q3,
        "maturity": ("MATURING" if q2 else "NO_DATA_YET"),
        "need_more_data": not mature,
        "caveat": (f"BLOCK side is n=7 and WATCH n=13 — below the n>="
                   f"{N_FLOOR} floor; every answer here is directional "
                   "evidence for the gatekeeper-precision ledger, not proof"),
    }


def build() -> Dict:
    frozen = json.loads(FROZEN.read_text())
    spy = _bench("SPY")
    qqq = _bench("QQQ")
    if spy is None:
        raise RuntimeError("SPY bench unavailable")
    outcomes = [ticker_outcomes(r, spy, qqq) for r in frozen["rows"]]
    skipped = [o["ticker"] for o in outcomes if o.get("error")]
    usable = [o for o in outcomes if not o.get("error")]
    agg = aggregate(usable)
    v = verdicts(agg)
    return {
        "kind": "recall_shadow_gk_forward",
        "version": "1G.17A",
        "research_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asof_date": datetime.now(timezone.utc).date().isoformat(),
        "disclaimer": ("forward measurement of the immutable 1G.17A cohort · "
                       "cohort file is never rewritten · no signals, no "
                       "proposals, no execution/governance/DB side effects"),
        "cohort": {
            "frozen_at": frozen.get("frozen_at"),
            "anchor_date": frozen.get("anchor_date"),
            "n_total": frozen.get("n_total"),
            "n_watch": frozen.get("n_watch"),
            "n_block": frozen.get("n_block"),
            "n_too_extended_block": frozen.get("n_too_extended_block"),
        },
        "skipped_no_data": skipped,
        "groups": agg,
        "verdicts": v,
        "per_ticker": usable,
    }


def historize(res: Dict) -> int:
    seen = {r.get("asof_date") for r in dataio.read_jsonl(HISTORY)}
    if res["asof_date"] in seen:
        return 0
    row = {"asof_date": res["asof_date"],
           "generated_at": res["generated_at"],
           "verdicts": res["verdicts"],
           "groups_summary": {
               g: {h: (res["groups"][g]["by_horizon"].get(h) or {}).get("rel_spy_avg")
                   for h in map(str, HORIZONS)}
               for g in GROUPS}}
    return dataio.append_jsonl(HISTORY, [row])


def _f(x) -> str:
    return "—" if x is None else f"{x:+.2%}"


def _render_txt(res: Dict) -> List[str]:
    c, v = res["cohort"], res["verdicts"]
    lines = [
        f"RECALL-SHADOW × GATEKEEPER FORWARD (1G.17A) — {res['asof_date']} "
        f"(research-only; immutable cohort)",
        "=" * 92,
        f"cohort frozen {str(c['frozen_at'])[:16]}Z anchor={c['anchor_date']}"
        f"  n={c['n_total']} (WATCH {c['n_watch']} / BLOCK {c['n_block']} / "
        f"too-extended blocks {c['n_too_extended_block']})",
        "",
        f"{'group':20s} {'h':>3s} {'n':>3s} {'fwd':>8s} {'relSPY':>8s} "
        f"{'relQQQ':>8s} {'win%':>6s} {'MFE':>8s} {'MAE':>8s}",
    ]
    for g in GROUPS:
        for h in HORIZONS:
            row = res["groups"][g]["by_horizon"].get(str(h)) or {}
            if not row.get("n"):
                continue
            lines.append(
                f"{g:20s} {h:3d} {row['n']:3d} {_f(row['fwd_avg']):>8s} "
                f"{_f(row['rel_spy_avg']):>8s} {_f(row['rel_qqq_avg']):>8s} "
                f"{row['win_rate']:>6.1f} {_f(row['mfe_avg']):>8s} "
                f"{_f(row['mae_avg']):>8s}")
        lines.append("")
    q2 = v["q2_watch_vs_block"]
    if q2:
        for h, r in q2.items():
            lines.append(
                f"Q2 {h}d: WATCH {_f(r['watch_rel_spy'])} vs BLOCK "
                f"{_f(r['block_rel_spy'])} → watch_outperformed="
                f"{r['watch_outperformed_block']} (spread {_f(r['spread_rel_spy'])})")
    q1 = v["q1_too_extended_blocks_continued_higher"]
    for h, r in q1.items():
        lines.append(
            f"Q1 {h}d: too-extended blocks fwd {_f(r['fwd_avg'])} relSPY "
            f"{_f(r['rel_spy_avg'])} → continued_higher(rel)="
            f"{r['continued_higher_rel_spy']} (n={r['n']})")
    q3 = v["q3_gk_precision_over_shadow_board"]
    for h, r in q3.items():
        lines.append(
            f"Q3 {h}d: WATCH − cohort = {_f(r['watch_minus_cohort_rel_spy'])}"
            f" → gk_adds_precision={r['gk_adds_precision']}")
    lines += ["", f"maturity={v['maturity']}  need_more_data="
              f"{v['need_more_data']}", f"caveat: {v['caveat']}"]
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="1G.17A cohort forward validation")
    ap.parse_args(argv)
    if not FROZEN.exists():
        print("no frozen cohort — run research/recall_shadow_gk_cohort_freeze.py first")
        return 0
    res = build()
    dataio.write_json(OUT_JSON, res)
    appended = historize(res)
    lines = _render_txt(res)
    dataio.write_text(OUT_TXT, lines)
    print("\n".join(lines))
    print(f"\nwrote {dataio.rel_to_repo(OUT_JSON)} · history rows appended: {appended}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
