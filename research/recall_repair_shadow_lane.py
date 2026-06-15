"""
research/recall_repair_shadow_lane.py — Path B Recall-Repair Shadow Lane.

RESEARCH-ONLY / CACHE-ONLY.  Tests the June Dashboard Truth Audit hypothesis:
that simple sector-relative strength + 20/60 momentum on a deep, theme-aware
universe catches forward winners *earlier* and with *better precision* than the
current scanner funnel (measured recall ~1.1% vs an 18% sector_rs baseline).

This module ONLY proposes a dated, historized research watch board.  It emits
NO paper signals, NO trade proposals, touches NO governance / execution / gate /
live-capital / production-universe logic, mutates NO DB rows, and makes NO
provider calls.  The forward edge is measured separately by
``research/recall_repair_shadow_forward.py``; nothing here is an entry signal.

Candidate logic (all pure price/liquidity/theme, deliberately simple):
  sector_rs   — 20d return minus the ticker's sector median 20d return
  mom_20_60   — 20d return AND 60d return strength
  theme score — leadership of the ticker's profile-derived theme
  liquidity   — avg 20d dollar volume (a floor + a 0-1 score)
  volume exp  — 20d avg volume vs the prior 20d
  extension   — distance above MA50 / 20d run, used to MARK (not discard)
                late/parabolic names

Labels (research routing only, never an order):
  SHADOW_RS_LEADER · SHADOW_MOMENTUM_LEADER · SHADOW_THEME_LEADER ·
  SHADOW_PULLBACK_WATCH · SHADOW_LATE_EXTENDED · SHADOW_NO_EDGE

Outputs:
  cache/research/recall_repair_shadow_lane_latest.json
  logs/recall_repair_shadow_lane_latest.txt
  data/research/recall_repair_shadow_lane_history.jsonl   (append-only, idempotent)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.scanner_truth import dataio
from research.scanner_truth.filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL,
                                            UNIV_MIN_AVG_VOL, UNIV_MIN_PRICE)

# Bump when the candidate logic changes so historized rows stay comparable and
# the forward validator can cohort by version.
VERSION = "v1"

LANE_JSON = dataio.RESEARCH_CACHE / "recall_repair_shadow_lane_latest.json"
LANE_TXT = dataio.LOGS_DIR / "recall_repair_shadow_lane_latest.txt"
LANE_HISTORY = dataio.HISTORY_DIR / "recall_repair_shadow_lane_history.jsonl"

# ── thresholds (documented; not tuned to flatter results) ────────────────────
SECTOR_RS_FLAG = 0.10          # 20d return ≥ sector median + 10pp
MOM_R20_FLAG = 0.10            # 20d return ≥ +10%
MOM_R60_FLAG = 0.20            # 60d return ≥ +20%
EXT_EXTENDED = 0.20            # > 20% above MA50  → EXTENDED
EXT_PARABOLIC = 0.40           # > 40% above MA50  → PARABOLIC
EXT_R20_PARABOLIC = 0.80       # OR 20d run ≥ +80% → PARABOLIC
VOL_EXPANSION = 1.30           # 20d avg vol ≥ 1.3× prior 20d
PULLBACK_R60 = 0.20            # was a leader (60d ≥ +20%) …
PULLBACK_R5_MAX = -0.03        # … but pulled back ≥3% over the last 5d
DEEP_MIN_BARS = 200            # bar-depth needed for MA200-class gates
MIN_BARS_FOR_FEATURES = 65     # need ≥60d history for r60
DEFAULT_CAP = 150              # ranked labeled candidates kept + historized
DISPLAY_TOP = 40               # rows shown in the text report
TOP_THEMES = 3                 # # of leading themes that count as "leadership"


def _aligned(df: pd.DataFrame, calendar) -> pd.Series:
    c = df["close"].reindex(calendar)
    fv = c.first_valid_index()
    if fv is not None:
        c.loc[c.index >= fv] = c.loc[c.index >= fv].ffill()
    return c


def _ret(s: pd.Series, i_from: int, i_to: int) -> Optional[float]:
    if i_from < 0 or i_to < 0 or i_from >= len(s) or i_to >= len(s):
        return None
    a, b = s.iloc[i_from], s.iloc[i_to]
    if pd.isna(a) or pd.isna(b) or a <= 0:
        return None
    return float(b / a - 1.0)


# ── cross-reference sets (cache-only existence checks) ───────────────────────

def _alpha_board_symbols() -> set:
    out: set = set()
    for fn in ("alpha_discovery_board_latest.json",
               "alpha_discovery_overlay_latest.json"):
        p = dataio.RESEARCH_CACHE / fn
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        for key in ("items", "symbols"):
            for row in (d.get(key) or []):
                if isinstance(row, dict):
                    t = str(row.get("ticker") or row.get("symbol") or "").upper()
                elif isinstance(row, str):
                    t = row.upper()
                else:
                    t = ""
                if t:
                    out.add(t)
    return out


def _scanner_seen_symbols() -> Optional[set]:
    """Tickers the live funnel actually surfaced (from the funnel trace).

    Returns None when the trace is unavailable so callers can render 'unknown'
    rather than a misleading 'no'.
    """
    p = dataio.RESEARCH_CACHE / "scanner_funnel_trace_latest.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None
    traces = d.get("traces") or []
    seen = set()
    for t in traces:
        try:
            if t.get("first_date_actually_saw") or \
               (t.get("today_snapshot") or {}).get("in_alpha_board"):
                seen.add(str(t.get("ticker") or "").upper())
        except Exception:
            continue
    return seen


def _has_artifact(prefix: str, ticker: str) -> bool:
    return (dataio.RESEARCH_CACHE / f"{prefix}_{ticker.upper()}_latest.json").exists()


# ── core build ───────────────────────────────────────────────────────────────

def _extension_label(ext_pct: Optional[float], r20: Optional[float]) -> str:
    e = ext_pct if ext_pct is not None else 0.0
    r = r20 if r20 is not None else 0.0
    if e >= EXT_PARABOLIC or r >= EXT_R20_PARABOLIC:
        return "PARABOLIC"
    if e >= EXT_EXTENDED:
        return "EXTENDED"
    return "NORMAL"


def _classify(feat: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return (label, reason_codes).  Late/parabolic names are MARKED, never
    silently dropped — they are surfaced as SHADOW_LATE_EXTENDED so the forward
    study can measure whether chasing them pays (it must not be auto-traded)."""
    reasons: List[str] = []
    sector_rs = feat["sector_rs"]
    r20, r60, r5 = feat["r20"], feat["r60"], feat["r5"]
    is_rs = sector_rs is not None and sector_rs >= SECTOR_RS_FLAG
    is_mom = (r20 or 0) >= MOM_R20_FLAG and (r60 or 0) >= MOM_R60_FLAG
    is_theme = feat["theme_leader"]
    is_vol = (feat["vol_ratio"] or 0) >= VOL_EXPANSION
    is_pullback = (r60 or 0) >= PULLBACK_R60 and (r5 is not None and r5 <= PULLBACK_R5_MAX)
    ext = feat["extension_label"]

    if is_rs:
        reasons.append(f"sector_rs>={SECTOR_RS_FLAG:.2f}")
    if is_mom:
        reasons.append("mom_20_60")
    if is_theme:
        reasons.append(f"theme_leader:{feat['theme']}")
    if is_vol:
        reasons.append("vol_expansion")
    if is_pullback:
        reasons.append("pullback_from_leader")
    if ext != "NORMAL":
        reasons.append(ext.lower())

    has_leadership = is_rs or is_mom or is_theme
    # Parabolic + any leadership → mark as late/extended (do NOT chase blindly).
    if ext == "PARABOLIC" and has_leadership:
        return "SHADOW_LATE_EXTENDED", reasons
    if is_theme and (is_rs or is_mom):
        return "SHADOW_THEME_LEADER", reasons
    if is_mom:
        return "SHADOW_MOMENTUM_LEADER", reasons
    if is_rs:
        return "SHADOW_RS_LEADER", reasons
    if is_pullback:
        return "SHADOW_PULLBACK_WATCH", reasons
    return "SHADOW_NO_EDGE", reasons


def _clamp(x: Optional[float], lo: float, hi: float) -> float:
    return max(lo, min(hi, x if x is not None else 0.0))


def _rank_score(feat: Dict[str, Any]) -> float:
    """Rank EARLY quality leaders to the top.  Contributions are clamped so a
    parabolic micro-cap (e.g. +1300% in 20d) cannot dominate the board on raw
    magnitude — the whole point is to catch leaders *before* the blow-off.
    Late/parabolic names are still kept + historized (just ranked low) so the
    forward study can measure whether chasing them ever pays."""
    sr = _clamp(feat["sector_rs"], -0.30, 0.50)
    mom = 0.5 * _clamp(feat["r20"], -0.30, 0.50) + 0.5 * _clamp(feat["r60"], -0.30, 0.80)
    theme = _clamp(feat["theme_score"], 0.0, 1.0)
    vol = _clamp((feat["vol_ratio"] or 1.0) - 1.0, 0.0, 1.0)
    deep_bonus = 1.0 if feat.get("bar_depth_status") == "DEEP" else 0.0
    score = 60.0 * sr + 50.0 * mom + 30.0 * theme + 8.0 * vol + 10.0 * deep_bonus
    # Multiplicative dampening so extended/parabolic names reliably sink below
    # not-yet-extended leaders regardless of their raw run size.
    if feat["extension_label"] == "PARABOLIC":
        score *= 0.15
    elif feat["extension_label"] == "EXTENDED":
        score *= 0.60
    return float(score)


def build(asof_offset: int = 0, cap: int = DEFAULT_CAP) -> Dict[str, Any]:
    """Build today's (or asof_offset-back) shadow board.  Pure read; no writes."""
    calendar = dataio.benchmark_calendar()
    if len(calendar) < MIN_BARS_FOR_FEATURES + 1:
        return {"error": "calendar too short", "version": VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat()}
    asof_i = len(calendar) - 1 - max(0, int(asof_offset))
    asof = calendar[asof_i]
    profiles = dataio.load_profiles()
    spy = _aligned(dataio.load_prices("SPY"), calendar)
    spy_20d = _ret(spy, asof_i - 20, asof_i)

    alpha_syms = _alpha_board_symbols()
    scanner_seen = _scanner_seen_symbols()

    rows: List[Dict[str, Any]] = []
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = dataio.load_prices(t)
        if df is None:
            continue
        c = _aligned(df, calendar)
        if asof_i >= len(c) or pd.isna(c.iloc[asof_i]):
            continue
        vol = df["volume"].reindex(calendar).ffill() if "volume" in df.columns \
            else pd.Series(index=calendar, dtype=float)
        price = float(c.iloc[asof_i])
        avgvol = float(vol.iloc[max(0, asof_i - 19):asof_i + 1].mean()) if len(vol) else 0.0
        avgdvol = float((c * vol).iloc[max(0, asof_i - 19):asof_i + 1].mean()) if len(vol) else 0.0
        if not (UNIV_MIN_PRICE <= price <= UNIV_MAX_PRICE
                and avgvol >= UNIV_MIN_AVG_VOL and avgdvol >= UNIV_MIN_AVG_DVOL):
            continue
        # bar-depth: how much *deep* history backs the longer gates.
        deep_bars = dataio.deep_bar_count(t)
        bars_available = int(c.iloc[:asof_i + 1].notna().sum())
        if bars_available < MIN_BARS_FOR_FEATURES:
            # not enough history for r60 — record as INSUFFICIENT, skip board.
            continue
        r5 = _ret(c, asof_i - 5, asof_i)
        r20 = _ret(c, asof_i - 20, asof_i)
        r60 = _ret(c, asof_i - 60, asof_i)
        ma50 = float(c.iloc[max(0, asof_i - 49):asof_i + 1].mean())
        ext_pct = (price / ma50 - 1.0) if ma50 > 0 else None
        vol20 = avgvol
        vol_prior20 = float(vol.iloc[max(0, asof_i - 39):asof_i - 19].mean()) \
            if (len(vol) and asof_i >= 39) else np.nan
        vol_ratio = (vol20 / vol_prior20) if vol_prior20 else None
        bar_depth_status = ("DEEP" if deep_bars >= DEEP_MIN_BARS
                            else ("SHALLOW" if bars_available >= MIN_BARS_FOR_FEATURES
                                  else "INSUFFICIENT"))
        prof = profiles.get(t) or {}
        rows.append({
            "ticker": t,
            "price": price,
            "sector": prof.get("sector") or "UNKNOWN",
            "theme": dataio.classify_theme(prof),
            "avg_dvol": avgdvol,
            "r5": r5, "r20": r20, "r60": r60,
            "beat_spy_20": (r20 - spy_20d) if (r20 is not None and spy_20d is not None) else None,
            "ext_pct": ext_pct,
            "extension_label": _extension_label(ext_pct, r20),
            "vol_ratio": vol_ratio,
            "deep_bars": deep_bars,
            "bars_available": bars_available,
            "bar_depth_status": bar_depth_status,
        })

    n_universe = len(rows)
    # sector medians (as-of) for sector_rs
    sec_r20: Dict[str, List[float]] = {}
    for r in rows:
        if r["r20"] is not None:
            sec_r20.setdefault(r["sector"], []).append(r["r20"])
    sec_med = {s: median(v) for s, v in sec_r20.items() if v}

    # theme leadership: median 20d return per theme; top themes lead.
    theme_r20: Dict[str, List[float]] = {}
    for r in rows:
        if r["r20"] is not None:
            theme_r20.setdefault(r["theme"], []).append(r["r20"])
    theme_med = {th: median(v) for th, v in theme_r20.items() if v}
    leading_themes = {th for th, _ in sorted(theme_med.items(),
                      key=lambda kv: kv[1], reverse=True)[:TOP_THEMES]}
    if theme_med:
        lo, hi = min(theme_med.values()), max(theme_med.values())
        span = (hi - lo) or 1.0
    else:
        lo, span = 0.0, 1.0

    for r in rows:
        r["sector_rs"] = (r["r20"] - sec_med.get(r["sector"], 0.0)) \
            if r["r20"] is not None else None
        tm = theme_med.get(r["theme"])
        r["theme_score"] = ((tm - lo) / span) if tm is not None else 0.0
        r["theme_leader"] = (r["theme"] in leading_themes
                             and r["r20"] is not None
                             and tm is not None and r["r20"] >= tm
                             and r["theme"] not in ("other", "unknown"))

    # classify + rank
    candidates: List[Dict[str, Any]] = []
    label_counts: Dict[str, int] = {}
    for r in rows:
        label, reasons = _classify(r)
        label_counts[label] = label_counts.get(label, 0) + 1
        r["label"] = label
        r["reason_codes"] = reasons
        r["rank_score"] = _rank_score(r)
        if label != "SHADOW_NO_EDGE":
            candidates.append(r)

    candidates.sort(key=lambda x: x["rank_score"], reverse=True)
    candidates = candidates[:cap]

    asof_date = str(asof)[:10]
    out_rows: List[Dict[str, Any]] = []
    for i, r in enumerate(candidates, start=1):
        t = r["ticker"]
        out_rows.append({
            "ticker": t,
            "asof_date": asof_date,
            "rank": i,
            "label": r["label"],
            "sector": r["sector"],
            "theme": r["theme"],
            "sector_rs": round(r["sector_rs"], 4) if r["sector_rs"] is not None else None,
            "r20": round(r["r20"], 4) if r["r20"] is not None else None,
            "r60": round(r["r60"], 4) if r["r60"] is not None else None,
            "mom_20_60_score": round(0.5 * (r["r20"] or 0) + 0.5 * (r["r60"] or 0), 4),
            "theme_score": round(r["theme_score"], 4),
            "theme_leader": bool(r["theme_leader"]),
            "liquidity_score": round(min(1.0, (r["avg_dvol"] or 0) / 5e8), 4),
            "avg_dvol": round(r["avg_dvol"], 1),
            "extension_label": r["extension_label"],
            "ext_pct": round(r["ext_pct"], 4) if r["ext_pct"] is not None else None,
            "vol_ratio": round(r["vol_ratio"], 3) if r["vol_ratio"] is not None else None,
            "bar_depth_status": r["bar_depth_status"],
            "deep_bars": r["deep_bars"],
            "rank_score": round(r["rank_score"], 3),
            "reason_codes": r["reason_codes"],
            "price_at_asof": round(r["price"], 4),
            "on_alpha_board": t in alpha_syms,
            "has_lens": _has_artifact("stock_lens", t),
            "has_gatekeeper": _has_artifact("executive_gatekeeper", t),
            "scanner_saw": (None if scanner_seen is None else (t in scanner_seen)),
            "note": "research watch only · not a signal · not paper evidence",
        })

    n_alpha_overlap = sum(1 for r in out_rows if r["on_alpha_board"])
    n_scanner_overlap = sum(1 for r in out_rows if r["scanner_saw"] is True)
    return {
        "kind": "recall_repair_shadow_lane",
        "version": VERSION,
        "research_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asof_date": asof_date,
        "disclaimer": ("research-only shadow board · simple sector_rs + mom_20_60 + "
                       "theme leadership · NOT a signal, NOT paper evidence, NOT a "
                       "trade proposal · no provider/DB/gate/execution side effects"),
        "thresholds": {
            "sector_rs_flag": SECTOR_RS_FLAG, "mom_r20": MOM_R20_FLAG,
            "mom_r60": MOM_R60_FLAG, "ext_extended": EXT_EXTENDED,
            "ext_parabolic": EXT_PARABOLIC, "vol_expansion": VOL_EXPANSION,
            "deep_min_bars": DEEP_MIN_BARS,
        },
        "n_universe": n_universe,
        "n_candidates": len(out_rows),
        "cap": cap,
        "label_counts": label_counts,
        "leading_themes": sorted(leading_themes),
        "theme_medians": {k: round(v, 4) for k, v in sorted(
            theme_med.items(), key=lambda kv: kv[1], reverse=True)},
        "alpha_board_overlap": n_alpha_overlap,
        "scanner_overlap": n_scanner_overlap,
        "scanner_seen_available": scanner_seen is not None,
        "spy_20d_return_at_asof": round(spy_20d, 4) if spy_20d is not None else None,
        "candidates": out_rows,
    }


# ── historizer (append-only, idempotent per asof_date/ticker/version) ────────

def historize(result: Dict[str, Any], *, path=LANE_HISTORY) -> int:
    """Append today's candidates to the dated history.  Idempotent: a row for an
    (asof_date, ticker, version) already present is never duplicated.  Append-only
    — prior lines are never rewritten (forward evidence is immutable)."""
    if result.get("error"):
        return 0
    existing = dataio.read_jsonl(path)
    seen = {(r.get("asof_date"), r.get("ticker"), r.get("version"))
            for r in existing}
    gen = result["generated_at"]
    asof = result["asof_date"]
    ver = result["version"]
    new_rows: List[Dict[str, Any]] = []
    for c in result["candidates"]:
        key = (asof, c["ticker"], ver)
        if key in seen:
            continue
        seen.add(key)
        row = dict(c)
        row["asof_date"] = asof
        row["ticker"] = c["ticker"]
        row["version"] = ver
        row["generated_at"] = gen
        row["source_versions"] = {
            "lane_version": ver,
            "alpha_board_overlap": result["alpha_board_overlap"],
            "scanner_seen_available": result["scanner_seen_available"],
        }
        new_rows.append(row)
    return dataio.append_jsonl(path, new_rows) if new_rows else 0


# ── render ───────────────────────────────────────────────────────────────────

def _render_txt(res: Dict[str, Any]) -> List[str]:
    if res.get("error"):
        return [f"recall-repair shadow lane error: {res['error']}"]
    L = [
        f"== RECALL-REPAIR SHADOW LANE ({res['version']}) — {res['generated_at']} ==",
        res["disclaimer"],
        f"as-of {res['asof_date']}  universe {res['n_universe']}  "
        f"candidates {res['n_candidates']} (cap {res['cap']})",
        f"labels: " + "  ".join(f"{k}={v}" for k, v in sorted(res['label_counts'].items())),
        f"leading themes: {', '.join(res['leading_themes']) or '—'}",
        f"alpha-board overlap: {res['alpha_board_overlap']}  ·  "
        f"scanner overlap: {res['scanner_overlap']}"
        f"{'' if res['scanner_seen_available'] else ' (scanner-seen unavailable)'}",
        "",
        f"{'#':>3} {'ticker':<7}{'label':<24}{'sRS':>7}{'r20':>7}{'r60':>7}"
        f"{'theme':<18}{'ext':<10}{'depth':<8}{'A':>2}{'L':>2}{'G':>2}",
    ]
    for c in res["candidates"][:40]:
        L.append(
            f"{c['rank']:>3} {c['ticker']:<7}{c['label']:<24}"
            f"{(c['sector_rs'] or 0)*100:>6.1f}%{(c['r20'] or 0)*100:>6.1f}%"
            f"{(c['r60'] or 0)*100:>6.1f}%{c['theme'][:17]:<18}"
            f"{c['extension_label']:<10}{c['bar_depth_status']:<8}"
            f"{'Y' if c['on_alpha_board'] else '·':>2}"
            f"{'Y' if c['has_lens'] else '·':>2}"
            f"{'Y' if c['has_gatekeeper'] else '·':>2}"
        )
    L += ["", "research watch only — route candidates to Lens/Gatekeeper research, "
          "never to execution.  Forward edge measured by recall_repair_shadow_forward.py."]
    return L


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Recall-Repair Shadow Lane (research-only)")
    ap.add_argument("--print", action="store_true", help="print the report to stdout")
    ap.add_argument("--max", type=int, default=DEFAULT_CAP, help="board cap")
    ap.add_argument("--asof-offset", type=int, default=0,
                    help="build N trading days back (default 0 = latest bar)")
    ap.add_argument("--no-historize", action="store_true",
                    help="do not append to the dated history JSONL")
    ap.add_argument("--dry-run", action="store_true",
                    help="compute + print but write no files at all")
    args = ap.parse_args(argv)

    res = build(asof_offset=args.asof_offset, cap=args.max)
    lines = _render_txt(res)
    if args.dry_run:
        print("\n".join(lines))
        print("\n[dry-run] no files written")
        return 0
    dataio.write_json(LANE_JSON, res)
    dataio.write_text(LANE_TXT, lines)
    n_hist = 0
    if not args.no_historize:
        n_hist = historize(res)
    if args.print:
        print("\n".join(lines))
    print(f"\nwrote {dataio.rel_to_repo(LANE_JSON)} · "
          f"{dataio.rel_to_repo(LANE_TXT)} · historized {n_hist} new row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
