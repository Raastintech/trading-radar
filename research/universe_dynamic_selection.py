"""
research/universe_dynamic_selection.py — Phase 1G.7B Tasks 2-7.

A research-only, point-in-time analysis of the top-1000 universe that:
  T2. classifies every selected name by MOVE STAGE
      {EARLY_ACCUMULATION, EMERGING_MOMENTUM, BREAKOUT_CONFIRMED, PULLBACK_RECLAIM,
       LATE_EXTENDED, PARABOLIC, BROKEN, LOW_QUALITY_NOISE}
  T3. computes an EARLY_LEADER_SCORE (0-100) that rewards future-leader setups and
      penalises already-extended/parabolic names (with component sub-scores +
      reason codes)
  T4. builds a PROPOSED dynamic top-1000 (slot-allocated, diversity-capped) and
      compares it to the current production top-1000
  T6. replays the missed-winner universe point-in-time where data permits (else
      marks NOT_RETAINED and recommends historization)
  T7. appends a daily universe-selection ledger to
      data/research/universe_selection_history.jsonl

It NEVER changes the production universe, emits no signals, registers no strategy.
Features are computed from the (now partly deepened) price cache via
research.scanner_truth.dataio — bars ≤ as-of only, no look-ahead.

Outputs:
  cache/research/universe_dynamic_selection_latest.json
  logs/universe_dynamic_selection_latest.txt
  docs/research/DYNAMIC_UNIVERSE_SELECTION_POLICY.md   (Task 5)

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes, no signals.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.scanner_truth import dataio
from research.scanner_truth.filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL,
                                            UNIV_MIN_AVG_VOL, UNIV_MIN_PRICE, sma)

ARTIFACT_VERSION = "1g7b.1"
HISTORY_PATH = dataio.HISTORY_DIR / "universe_selection_history.jsonl"
SNAPSHOT = dataio.REPO / "cache" / "universe" / "universe_snapshot_latest.json"
THEME = dataio.RESEARCH_CACHE / "theme_leadership_latest.json"
WINNER_UNI = dataio.RESEARCH_CACHE / "missed_winner_universe_latest.json"
BROKER_SNAP = dataio.REPO / "cache" / "state" / "broker_positions_snapshot.json"
DOCS_DIR = dataio.REPO / "docs" / "research"

BASE_LIMIT = 1000
DEEP_BARS = 200            # "full" data quality bar floor

STAGES = ("EARLY_ACCUMULATION", "EMERGING_MOMENTUM", "BREAKOUT_CONFIRMED",
          "PULLBACK_RECLAIM", "LATE_EXTENDED", "PARABOLIC", "BROKEN",
          "LOW_QUALITY_NOISE")
EARLY_STAGES = {"EARLY_ACCUMULATION", "EMERGING_MOMENTUM"}
LATE_STAGES = {"LATE_EXTENDED", "PARABOLIC"}


# ── feature computation (point-in-time, bars ≤ i) ────────────────────────────

def _aligned(df: pd.DataFrame, cal) -> pd.Series:
    c = df["close"].reindex(cal)
    fv = c.first_valid_index()
    if fv is not None:
        c.loc[c.index >= fv] = c.loc[c.index >= fv].ffill()
    return c


def _ema(s: pd.Series, n: int) -> float:
    return float(s.ewm(span=n, min_periods=min(n, len(s))).mean().iloc[-1])


def _rsi(s: pd.Series, n: int = 14) -> Optional[float]:
    if len(s) < n + 1:
        return None
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean().iloc[-1]
    dn = (-d.clip(upper=0)).rolling(n).mean().iloc[-1]
    if dn == 0 or pd.isna(dn):
        return 100.0 if up else 50.0
    rs = up / dn
    return float(100 - 100 / (1 + rs))


def features(t: str, cal, i: int, spy: pd.Series, qqq: pd.Series,
             profiles: Dict) -> Optional[Dict]:
    df = dataio.load_prices(t)
    if df is None:
        return None
    c = _aligned(df, cal)
    if i < 60 or i >= len(c) or pd.isna(c.iloc[i]):
        return None
    bars = int(c.iloc[:i + 1].notna().sum())
    vol = df["volume"].reindex(cal).ffill()
    price = float(c.iloc[i])
    avgvol = float(vol.iloc[i - 19:i + 1].mean())
    avgdvol = float((c * vol).iloc[i - 19:i + 1].mean())
    if not (UNIV_MIN_PRICE <= price <= UNIV_MAX_PRICE
            and avgvol >= UNIV_MIN_AVG_VOL and avgdvol >= UNIV_MIN_AVG_DVOL):
        return None
    hist = c.iloc[:i + 1].dropna()

    def ret(n):
        if i - n < 0 or pd.isna(c.iloc[i - n]) or c.iloc[i - n] <= 0:
            return None
        return float(c.iloc[i] / c.iloc[i - n] - 1.0)
    r5, r10, r20, r40, r60 = ret(5), ret(10), ret(20), ret(40), ret(60)

    def bench(s, n):
        return float(s.iloc[i] / s.iloc[i - n] - 1.0) if (i - n >= 0 and not pd.isna(s.iloc[i - n]) and s.iloc[i - n] > 0) else None
    spy20, spy40 = bench(spy, 20), bench(spy, 40)
    rs20 = (r20 - spy20) if (r20 is not None and spy20 is not None) else None
    rs40 = (r40 - spy40) if (r40 is not None and spy40 is not None) else None

    ema20 = _ema(hist.tail(120), 20)
    ema50 = _ema(hist.tail(200), 50)
    sma50 = float(sma(hist, 50).iloc[-1]) if bars >= 50 else None
    ext_ema20 = (price - ema20) / ema20 if ema20 else None
    ext_sma50 = (price - sma50) / sma50 if sma50 else None
    # ATR(14)
    h = df["high"].reindex(cal).ffill(); l = df["low"].reindex(cal).ffill()
    tr = pd.concat([(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = float(tr.iloc[i - 13:i + 1].mean())
    atr_pct = (atr / price * 100.0) if price else None
    atr_ext = ((price - ema20) / atr) if (atr and ema20) else None   # extension in ATRs
    high20 = float(c.iloc[i - 19:i + 1].max())
    high60 = float(c.iloc[i - 59:i + 1].max()) if i >= 59 else high20
    low20 = float(c.iloc[i - 19:i + 1].min())
    d_hi20 = (high20 - price) / high20 if high20 else None
    d_hi60 = (high60 - price) / high60 if high60 else None
    pullback_from_high = d_hi60
    # range tightness / volatility compression: 10d range / price
    rng10 = (float(c.iloc[i - 9:i + 1].max()) - float(c.iloc[i - 9:i + 1].min())) / price if price else None
    vol_exp = (avgvol / float(vol.iloc[i - 39:i - 19].mean())
               if i >= 39 and vol.iloc[i - 39:i - 19].mean() else None)
    # higher-lows proxy: low of last 10 > low of prior 10
    hl = None
    if i >= 20:
        lo_recent = float(c.iloc[i - 9:i + 1].min())
        lo_prior = float(c.iloc[i - 19:i - 9].min())
        hl = lo_recent >= lo_prior
    rsi = _rsi(hist.tail(60))
    ema20_rising = False
    if bars >= 26:
        ema20_prev = _ema(hist.iloc[:-5].tail(120), 20)
        ema20_rising = ema20 > ema20_prev
    prof = profiles.get(t) or {}
    return {
        "ticker": t, "price": round(price, 4), "bars": bars,
        "avg_dvol_20": round(avgdvol, 0),
        "r5": r5, "r10": r10, "r20": r20, "r40": r40, "r60": r60,
        "rs20_vs_spy": rs20, "rs40_vs_spy": rs40,
        "ema20": round(ema20, 4), "ema50": round(ema50, 4),
        "sma50": round(sma50, 4) if sma50 else None,
        "ext_ema20": ext_ema20, "ext_sma50": ext_sma50,
        "atr_pct": atr_pct, "atr_ext": atr_ext,
        "d_hi20": d_hi20, "d_hi60": d_hi60, "pullback_from_high": pullback_from_high,
        "range10_pct": rng10, "vol_expansion": vol_exp, "higher_lows": hl,
        "rsi14": rsi, "ema20_rising": ema20_rising,
        "above_ema20": price >= ema20 if ema20 else False,
        "above_sma50": (price >= sma50) if sma50 else None,
        "near_20d_high": (d_hi20 is not None and d_hi20 <= 0.03),
        "sector": prof.get("sector"), "theme": dataio.classify_theme(prof),
    }


# ── Task 2: stage classifier ─────────────────────────────────────────────────

def classify_stage(f: Dict) -> str:
    r5, r10 = (f["r5"] or 0), (f["r10"] or 0)
    r20, r60 = (f["r20"] or 0), (f["r60"] or 0)
    ext20, ext50 = f["ext_ema20"], f["ext_sma50"]
    rs20 = f["rs20_vs_spy"]
    rsi = f["rsi14"]
    # BROKEN: below 50d trend with negative momentum
    if (f["above_sma50"] is False and r60 < -0.10) or (not f["above_ema20"] and r20 < -0.12):
        return "BROKEN"
    # PARABOLIC: vertical blow-off
    if r5 >= 0.25 or (r10 >= 0.40 and ext20 is not None and ext20 > 0.30) \
            or (rsi is not None and rsi >= 85 and ext20 is not None and ext20 > 0.25):
        return "PARABOLIC"
    # LATE_EXTENDED: strong but stretched far above trend
    if r20 > 0 and ((ext20 is not None and ext20 > 0.20) or (ext50 is not None and ext50 > 0.35)):
        return "LATE_EXTENDED"
    # BREAKOUT_CONFIRMED: at/near highs, positive RS, moderate extension
    if f["near_20d_high"] and r20 > 0.05 and (rs20 is None or rs20 > 0) \
            and (ext20 is None or ext20 <= 0.20):
        return "BREAKOUT_CONFIRMED"
    # PULLBACK_RECLAIM: prior strength, now pulled back to/near rising EMA20
    if r60 >= 0.15 and ext20 is not None and -0.12 <= ext20 <= 0.05 and f["ema20_rising"]:
        return "PULLBACK_RECLAIM"
    # EMERGING_MOMENTUM: RS turning up, above rising EMA20, not yet at highs, not stretched
    if (rs20 is not None and rs20 >= 0.03) and r20 > 0 and f["above_ema20"] \
            and f["ema20_rising"] and (ext20 is None or ext20 < 0.15) and not f["near_20d_high"]:
        return "EMERGING_MOMENTUM"
    # EARLY_ACCUMULATION: volume expansion + tight range + constructive, modest move
    if (f["vol_expansion"] or 0) >= 1.2 and (f["range10_pct"] or 1) <= 0.15 \
            and ext20 is not None and -0.05 <= ext20 <= 0.12 \
            and 0 <= r20 <= 0.20 and (f["higher_lows"] is True) and f["above_ema20"]:
        return "EARLY_ACCUMULATION"
    return "LOW_QUALITY_NOISE"


# ── Task 3: early-leader scoring ─────────────────────────────────────────────

def _clip(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def early_leader_score(f: Dict, theme_state: Optional[str]) -> Dict:
    codes: List[str] = []
    rs20, rs40 = f["rs20_vs_spy"], f["rs40_vs_spy"]
    ext20, ext50 = f["ext_ema20"], f["ext_sma50"]

    # 1. RS acceleration (0-25)
    rs_accel = 0.0
    if rs20 is not None and rs20 > 0:
        rs_accel += 10 * _clip(rs20 / 0.10); codes.append("RS20_POS")
    if rs40 is not None and rs40 > 0:
        rs_accel += 7 * _clip(rs40 / 0.15)
    if rs20 is not None and rs40 is not None and rs20 > rs40:
        rs_accel += 8; codes.append("RS_ACCELERATING")
    rs_accel = min(rs_accel, 25.0)

    # 2. Controlled accumulation (0-25)
    acc = 0.0
    if (f["vol_expansion"] or 0) >= 1.2 and (ext20 is None or ext20 < 0.15):
        acc += 9; codes.append("VOL_EXP_NO_EXTENSION")
    if f["above_ema20"] and f["ema20_rising"]:
        acc += 8; codes.append("ABOVE_RISING_EMA20")
    if f["higher_lows"] is True:
        acc += 4; codes.append("HIGHER_LOWS")
    if (f["range10_pct"] or 1) <= 0.12:
        acc += 4; codes.append("TIGHT_RANGE")
    acc = min(acc, 25.0)

    # 3. Theme confirmation (0-20)
    theme = 0.0
    if theme_state == "LEADING":
        theme = 20.0; codes.append("LEADING_THEME")
    elif theme_state == "EMERGING":
        theme = 12.0; codes.append("EMERGING_THEME")
    elif theme_state == "EXTENDED":
        theme = 5.0; codes.append("EXTENDED_THEME")

    # 4. Entry potential (0-15)
    entry = 0.0
    if ext20 is not None and abs(ext20) <= 0.05:
        entry += 9; codes.append("NEAR_EMA20")
    if f.get("_stage") == "PULLBACK_RECLAIM":
        entry += 6; codes.append("PULLBACK_RECLAIM_SETUP")
    elif ext20 is not None and -0.10 <= ext20 <= 0.08:
        entry += 3; codes.append("MEASURABLE_STOP")
    entry = min(entry, 15.0)

    # 5. Data quality (0-15)
    dq = 0.0
    if f["bars"] >= DEEP_BARS:
        dq += 8; codes.append("DEEP_HISTORY")
    elif f["bars"] >= 120:
        dq += 4
    if (f["avg_dvol_20"] or 0) >= 20_000_000:
        dq += 5; codes.append("LIQUID")
    elif (f["avg_dvol_20"] or 0) >= UNIV_MIN_AVG_DVOL:
        dq += 3
    if dataio.deep_bar_count(f["ticker"]) >= DEEP_BARS:
        dq += 2; codes.append("DEEP_CACHE")
    dq = min(dq, 15.0)

    # Late-extension penalty score (0-100) — separate, also used as its own field
    late = 0.0
    if ext20 is not None:
        late = max(late, _clip(ext20 / 0.20) * 100)
    if ext50 is not None:
        late = max(late, _clip(ext50 / 0.40) * 100)
    if (f["r5"] or 0) >= 0.25:
        late = max(late, 90); codes.append("PARABOLIC_5D")
    if f["rsi14"] is not None and f["rsi14"] >= 80:
        late = max(late, max(late, 70)); codes.append("RSI_EXTREME")
    if ext20 is not None and ext20 > 0.20:
        codes.append("EXTENDED_EMA20")

    raw = rs_accel + acc + theme + entry + dq
    score = _clip(raw - 0.4 * late, 0, 100)
    return {
        "early_leader_score": round(score, 1),
        "late_extension_score": round(late, 1),
        "rs_acceleration_score": round(rs_accel, 1),
        "relative_strength_score": round(rs_accel, 1),   # alias for the ledger schema
        "accumulation_score": round(acc, 1),
        "theme_score": round(theme, 1),
        "entry_quality_score": round(entry, 1),
        "data_quality_score": round(dq, 1),
        "reason_codes": codes,
    }


# ── scan: features + stage + score for a set of tickers ──────────────────────

def _theme_states() -> Dict[str, str]:
    try:
        th = json.loads(THEME.read_text())
        return {name: d.get("theme_state") for name, d in (th.get("themes") or {}).items()}
    except Exception:
        return {}


def _scan(tickers: List[str], cal, i: int, spy, qqq, profiles,
          theme_states: Dict[str, str]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for t in tickers:
        if t in dataio.BENCHMARKS:
            continue
        f = features(t, cal, i, spy, qqq, profiles)
        if f is None:
            continue
        stage = classify_stage(f)
        f["_stage"] = stage
        sc = early_leader_score(f, theme_states.get(f["theme"]))
        f.update(sc)
        f["stage_label"] = stage
        out[t] = f
    return out


# ── Tasks 4/5: proposed dynamic universe (slot-allocated, diversity-capped) ──

# Slot policy (Task 5). Buckets filled in priority order, deduped; total ≤ BASE_LIMIT.
SLOT_POLICY = [
    ("core_liquid", 300),
    ("rs_leaders", 250),
    ("emerging_theme", 150),
    ("pullback_reclaim", 150),
    ("accumulation_unusual_vol", 75),
    ("earnings_drift", 50),          # NOT_RETAINED — see note; filled from best remaining
    ("watchlist_positions", 25),
]
SECTOR_CAP_FRAC = 0.22               # max share per sector in discretionary buckets
LEADING_THEMES_EXEMPT = True         # confirmed-leading themes are exempt from the cap


def _watchlist_positions() -> List[str]:
    import os
    out = [t.strip().upper() for t in os.getenv("WATCHLIST_TICKERS", "").split(",") if t.strip()]
    try:
        snap = json.loads(BROKER_SNAP.read_text())
        rows = snap.get("positions", snap if isinstance(snap, list) else [])
        for r in rows or []:
            if isinstance(r, dict):
                s = (r.get("symbol") or r.get("ticker") or "").upper()
                if s:
                    out.append(s)
    except Exception:
        pass
    return list(dict.fromkeys(out))


def _propose_universe(scan: Dict[str, Dict], theme_states: Dict[str, str]) -> Dict:
    selected: Dict[str, str] = {}        # ticker -> bucket
    sector_count: Counter = Counter()
    leading_themes = {th for th, st in theme_states.items() if st == "LEADING"}

    def _cap_ok(f: Dict, enforce: bool) -> bool:
        if not enforce:
            return True
        if LEADING_THEMES_EXEMPT and f.get("theme") in leading_themes:
            return True
        sec = f.get("sector") or "UNKNOWN"
        return sector_count[sec] < int(SECTOR_CAP_FRAC * BASE_LIMIT)

    def _take(cands: List[str], bucket: str, limit: int, enforce_cap: bool):
        n = 0
        for t in cands:
            if n >= limit or len(selected) >= BASE_LIMIT:
                break
            if t in selected or t not in scan:
                continue
            f = scan[t]
            if not _cap_ok(f, enforce_cap):
                continue
            selected[t] = bucket
            sector_count[(f.get("sector") or "UNKNOWN")] += 1
            n += 1
        return n

    by_dvol = sorted(scan, key=lambda t: -(scan[t]["avg_dvol_20"] or 0))
    by_rs = sorted(scan, key=lambda t: -(scan[t]["rs20_vs_spy"] or -9))
    by_score = sorted(scan, key=lambda t: -scan[t]["early_leader_score"])
    emerging_theme = [t for t in by_score
                      if theme_states.get(scan[t]["theme"]) in ("LEADING", "EMERGING")
                      and scan[t]["stage_label"] in EARLY_STAGES | {"BREAKOUT_CONFIRMED", "PULLBACK_RECLAIM"}]
    pullback = [t for t in by_score if scan[t]["stage_label"] == "PULLBACK_RECLAIM"]
    accumulation = [t for t in by_score
                    if scan[t]["stage_label"] in ("EARLY_ACCUMULATION", "EMERGING_MOMENTUM")]
    watch = [t for t in _watchlist_positions() if t in scan]

    bucket_counts = {}
    bucket_counts["core_liquid"] = _take(by_dvol, "core_liquid", 300, enforce_cap=False)
    bucket_counts["rs_leaders"] = _take(by_rs, "rs_leaders", 250, enforce_cap=True)
    bucket_counts["emerging_theme"] = _take(emerging_theme, "emerging_theme", 150, enforce_cap=True)
    bucket_counts["pullback_reclaim"] = _take(pullback, "pullback_reclaim", 150, enforce_cap=True)
    bucket_counts["accumulation_unusual_vol"] = _take(accumulation, "accumulation_unusual_vol", 75, enforce_cap=True)
    # earnings_drift: point-in-time earnings not retained cheaply ⇒ fill from best remaining,
    # flagged so the slot is visible but honestly labelled.
    bucket_counts["earnings_drift"] = _take(by_score, "earnings_drift", 50, enforce_cap=True)
    bucket_counts["watchlist_positions"] = _take(watch, "watchlist_positions", 25, enforce_cap=False)
    # backfill any remaining slots from best early_leader_score (keeps size ~1000)
    bucket_counts["backfill_score"] = _take(by_score, "backfill_score", BASE_LIMIT, enforce_cap=True)
    return {"selected": selected, "bucket_counts": bucket_counts,
            "sector_distribution": dict(sorted(sector_count.items(), key=lambda x: -x[1])[:10])}


# ── Task 6: missed-winner point-in-time replay (honest about retention) ──────

def _replay(scan: Dict[str, Dict], current_top1000: set, proposed: set) -> Dict:
    """Compare current vs proposed COVERAGE of missed winners. The full historical
    RANK replay is NOT_RETAINED — no per-day universe snapshots existed before
    Task 7's historizer. We report what IS computable: today's coverage of the
    winner set by each universe, with an explicit caveat."""
    try:
        uni = json.loads(WINNER_UNI.read_text())
        winners = {w["ticker"].upper(): w for w in uni.get("winners", [])}
    except Exception:
        winners = {}
    wset = set(winners)
    cur_cov = wset & current_top1000
    prop_cov = wset & proposed
    # of winners in proposed, how many are flagged EARLY/EMERGING/BREAKOUT today
    early_now = [t for t in prop_cov if t in scan
                 and scan[t]["stage_label"] in EARLY_STAGES | {"BREAKOUT_CONFIRMED", "PULLBACK_RECLAIM"}]
    return {
        "rank_replay_status": "NOT_RETAINED",
        "rank_replay_reason": (
            "Point-in-time top-1000 RANK replay requires historical universe snapshots, "
            "which did not exist before this phase. Task 7's universe_selection_history "
            "ledger begins capturing them now, enabling a real forward replay later."),
        "n_winners": len(wset),
        "current_top1000_covers": len(cur_cov),
        "proposed_covers": len(prop_cov),
        "coverage_delta": len(prop_cov) - len(cur_cov),
        "winners_added_by_proposed": sorted(prop_cov - cur_cov)[:40],
        "winners_dropped_by_proposed": sorted(cur_cov - prop_cov)[:40],
        "proposed_winner_coverage_flagged_early_now": len(early_now),
        "caveat": "Coverage is measured TODAY; many winners are now late/extended, so "
                  "today's coverage is not proof of earlier detection. Use the forward "
                  "ledger for that.",
    }


# ── Task 7 / Phase 1G.8 Task 1: dual-version shadow universe historizer ──────

PRODUCTION_VERSION = "production"
PROPOSED_VERSION = "proposed_dynamic"


def _ledger_row(version: str, t: str, included: bool, rank: Optional[int],
                bucket: Optional[str], f: Optional[Dict], now: str, asof: str) -> Dict:
    f = f or {}
    return {
        "generated_at": now, "asof_date": asof, "universe_version": version,
        "ticker": t, "rank": rank, "selection_bucket": bucket, "included": included,
        "early_leader_score": f.get("early_leader_score"),
        "late_extension_score": f.get("late_extension_score"),
        "accumulation_score": f.get("accumulation_score"),
        "relative_strength_score": f.get("relative_strength_score"),
        "data_quality_score": f.get("data_quality_score"),
        "stage_label": f.get("stage_label"), "theme": f.get("theme"),
        "sector": f.get("sector"), "reason_codes": f.get("reason_codes"),
        "bars": f.get("bars"), "source_version": ARTIFACT_VERSION,
    }


def historize(scan: Dict[str, Dict], proposed: Dict, production_ordered: List[str],
              asof: str) -> Dict:
    """Append BOTH universes (production + proposed_dynamic) for `asof`. Idempotent
    per (asof_date, universe_version): a date already carrying versioned rows is
    skipped. Append-only — never rewrites prior history. No signals, no DB writes."""
    existing = dataio.read_jsonl(HISTORY_PATH)
    have_versioned = any(r.get("asof_date") == asof and r.get("universe_version")
                         for r in existing)
    if have_versioned:
        return {"asof_date": asof, "rows_written": 0, "already_present": True,
                "history_total_rows": len(existing),
                "history_path": dataio.rel_to_repo(HISTORY_PATH)}

    prod_set = set(production_ordered)
    prod_rank = {t: i + 1 for i, t in enumerate(production_ordered)}
    sel = proposed["selected"]                       # ticker -> bucket
    prop_ranked = sorted(sel, key=lambda t: -scan[t]["early_leader_score"])
    prop_rank = {t: i + 1 for i, t in enumerate(prop_ranked)}

    union = list(dict.fromkeys(production_ordered + prop_ranked))
    now = datetime.now(timezone.utc).isoformat()
    rows: List[Dict] = []
    for t in union:
        f = scan.get(t)
        rows.append(_ledger_row(PRODUCTION_VERSION, t, t in prod_set,
                                 prod_rank.get(t),
                                 "production_base" if t in prod_set else None, f, now, asof))
        rows.append(_ledger_row(PROPOSED_VERSION, t, t in sel,
                                 prop_rank.get(t), sel.get(t), f, now, asof))
    written = dataio.append_jsonl(HISTORY_PATH, rows)
    return {"asof_date": asof, "rows_written": written, "already_present": False,
            "n_union_tickers": len(union),
            "n_production_included": len(prod_set),
            "n_proposed_included": len(sel),
            "history_total_rows": len(existing) + written,
            "history_path": dataio.rel_to_repo(HISTORY_PATH)}


# ── build / compare ──────────────────────────────────────────────────────────

def build(full_universe: bool = True, do_historize: bool = False) -> Dict:
    cal = dataio.benchmark_calendar()
    i = len(cal) - 1
    asof = str(cal[i])[:10]
    profiles = dataio.load_profiles()
    spy = _aligned(dataio.load_prices("SPY"), cal)
    qqq = _aligned(dataio.load_prices("QQQ"), cal)
    theme_states = _theme_states()

    snap = json.loads(SNAPSHOT.read_text())
    current = [s.upper() for s in snap.get("base_universe", [])]
    current_set = set(current)

    # scan the candidate pool: current top-1000 ∪ all liquid (to find missed names)
    pool = list(current_set | {t for t in dataio.all_price_tickers() if t not in dataio.BENCHMARKS}) \
        if full_universe else current
    scan = _scan(pool, cal, i, spy, qqq, profiles, theme_states)

    # current top-1000 stage distribution (only those we could score)
    cur_scored = {t: scan[t] for t in current_set if t in scan}
    cur_stage = Counter(f["stage_label"] for f in cur_scored.values())
    cur_late = sum(cur_stage[s] for s in LATE_STAGES)
    cur_early = sum(cur_stage[s] for s in EARLY_STAGES)
    cur_leading_theme = sum(1 for f in cur_scored.values()
                            if theme_states.get(f["theme"]) == "LEADING")

    proposed = _propose_universe(scan, theme_states)
    prop_set = set(proposed["selected"])
    prop_scored = {t: scan[t] for t in prop_set}
    prop_stage = Counter(f["stage_label"] for f in prop_scored.values())
    prop_late = sum(prop_stage[s] for s in LATE_STAGES)
    prop_early = sum(prop_stage[s] for s in EARLY_STAGES)

    overlap = current_set & prop_set
    added = prop_set - current_set
    dropped = current_set - prop_set
    added_early = sum(1 for t in added if t in scan and scan[t]["stage_label"] in EARLY_STAGES)
    dropped_late = sum(1 for t in dropped if t in scan and scan[t]["stage_label"] in LATE_STAGES)

    replay = _replay(scan, current_set, prop_set)

    # theme coverage (semis/memory/space/AI hardware) — top leaders in each universe
    watch_themes = ("semiconductors", "memory_storage", "space_aerospace", "hardware")
    theme_cov = {}
    for th in watch_themes:
        cur_n = sum(1 for t in current_set if t in scan and scan[t]["theme"] == th)
        prop_n = sum(1 for t in prop_set if scan[t]["theme"] == th)
        theme_cov[th] = {"current": cur_n, "proposed": prop_n, "state": theme_states.get(th)}

    hist = historize(scan, proposed, current, asof) if do_historize else None

    res = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asof_date": asof,
        "disclaimer": "RESEARCH-ONLY. Proposed universe is a COMPARISON, not a "
                      "production change. No signals, no trades.",
        "pool_scored": len(scan), "current_top1000_scored": len(cur_scored),
        "current_top1000": {
            "stage_distribution": dict(cur_stage),
            "late_extended_parabolic": cur_late,
            "late_pct": round(100.0 * cur_late / max(len(cur_scored), 1), 1),
            "early_accumulation_emerging": cur_early,
            "early_pct": round(100.0 * cur_early / max(len(cur_scored), 1), 1),
            "leading_theme_members": cur_leading_theme,
        },
        "proposed_universe": {
            "size": len(prop_set),
            "bucket_counts": proposed["bucket_counts"],
            "stage_distribution": dict(prop_stage),
            "late_extended_parabolic": prop_late,
            "late_pct": round(100.0 * prop_late / max(len(prop_set), 1), 1),
            "early_accumulation_emerging": prop_early,
            "early_pct": round(100.0 * prop_early / max(len(prop_set), 1), 1),
            "sector_distribution": proposed["sector_distribution"],
            "earnings_drift_bucket_note": "NOT_RETAINED — point-in-time earnings not "
                                          "cheaply available; slot filled from best "
                                          "early_leader_score and flagged.",
        },
        "comparison": {
            "overlap": len(overlap), "added": len(added), "dropped": len(dropped),
            "added_that_are_early": added_early,
            "dropped_that_are_late": dropped_late,
            "current_distinct_sectors": len({scan[t]["sector"] for t in cur_scored}),
            "proposed_distinct_sectors": len({scan[t]["sector"] for t in prop_set}),
        },
        "theme_coverage": theme_cov,
        "missed_winner_replay": replay,
        "historize": hist,
    }
    res["summary_line"] = (
        f"current late={res['current_top1000']['late_pct']}% "
        f"early={res['current_top1000']['early_pct']}% → "
        f"proposed late={res['proposed_universe']['late_pct']}% "
        f"early={res['proposed_universe']['early_pct']}%; "
        f"added {len(added)} ({added_early} early), dropped {len(dropped)} "
        f"({dropped_late} late)")
    return res


def _render_txt(res: Dict) -> List[str]:
    c, p, cmp = res["current_top1000"], res["proposed_universe"], res["comparison"]
    L = [
        f"== UNIVERSE DYNAMIC SELECTION ({res['generated_at']}) ==",
        res["disclaimer"],
        f"as-of {res['asof_date']}  ·  pool scored {res['pool_scored']}  ·  "
        f"current top-1000 scored {res['current_top1000_scored']}",
        "",
        "CURRENT TOP-1000 stages: " + ", ".join(f"{k}={v}" for k, v in
                                                 sorted(c["stage_distribution"].items(), key=lambda x: -x[1])),
        f"  late/extended/parabolic: {c['late_extended_parabolic']} ({c['late_pct']}%)   "
        f"early/emerging: {c['early_accumulation_emerging']} ({c['early_pct']}%)   "
        f"leading-theme members: {c['leading_theme_members']}",
        "",
        "PROPOSED stages: " + ", ".join(f"{k}={v}" for k, v in
                                        sorted(p["stage_distribution"].items(), key=lambda x: -x[1])),
        f"  late: {p['late_extended_parabolic']} ({p['late_pct']}%)   "
        f"early: {p['early_accumulation_emerging']} ({p['early_pct']}%)   size: {p['size']}",
        f"  buckets: {p['bucket_counts']}",
        "",
        f"COMPARISON: overlap={cmp['overlap']}  added={cmp['added']} "
        f"(early {cmp['added_that_are_early']})  dropped={cmp['dropped']} "
        f"(late {cmp['dropped_that_are_late']})",
        f"  distinct sectors: current {cmp['current_distinct_sectors']} → "
        f"proposed {cmp['proposed_distinct_sectors']}",
        "",
        "THEME COVERAGE (current → proposed):",
    ]
    for th, d in res["theme_coverage"].items():
        L.append(f"  {th:<16} {d['current']:>3} → {d['proposed']:>3}  (state={d['state']})")
    r = res["missed_winner_replay"]
    L += ["",
          f"MISSED-WINNER REPLAY: rank_replay={r['rank_replay_status']} — {r['rank_replay_reason']}",
          f"  winners {r['n_winners']}: current covers {r['current_top1000_covers']}, "
          f"proposed {r['proposed_covers']} (Δ{r['coverage_delta']})",
          "", "SUMMARY: " + res["summary_line"]]
    if res.get("historize"):
        h = res["historize"]
        L.append(f"HISTORIZED: {h['rows_written']} rows (asof {h['asof_date']}, "
                 f"already={h['already_present']}) → {h['history_path']}")
    return L


def _write_policy_doc(res: Dict) -> None:
    p = DOCS_DIR / "DYNAMIC_UNIVERSE_SELECTION_POLICY.md"
    c, pr = res["current_top1000"], res["proposed_universe"]
    L = [
        "# Dynamic Universe Selection Policy — Phase 1G.7B (Task 5)",
        "",
        f"*Generated {res['generated_at']} · research-only DESIGN. Not implemented in "
        "production; no scanner/strategy/universe change is made.*",
        "",
        "## Problem (from Task 1 audit)",
        "The production top-1000 ranks by `base_score` = 0.45·liquidity + 0.25·ATR + "
        "0.15·volume-ratio + 0.15·**absolute** 20d return. It is dynamic (30-min refresh) "
        "but has **no relative-strength, theme, earliness, or diversity** term, so it is "
        "liquidity-dominated and rewards already-moved names.",
        "",
        "## Proposed slot-allocation policy",
        "",
        "| bucket | slots | selection key |",
        "|---|--:|---|",
        "| core_liquid | 300 | top liquid names (keeps a broad liquid core) |",
        "| rs_leaders | 250 | top 20d relative strength vs SPY |",
        "| emerging_theme | 150 | leading/emerging-theme members, early/breakout stage |",
        "| pullback_reclaim | 150 | prior strength now reclaiming rising EMA20 |",
        "| accumulation_unusual_vol | 75 | volume expansion + tight range, constructive |",
        "| earnings_drift | 50 | post-earnings drift (**NOT_RETAINED** — needs PIT earnings) |",
        "| watchlist_positions | 25 | operator watchlist + open positions |",
        "",
        "### Constraints",
        f"- **Sector cap:** ≤ {int(SECTOR_CAP_FRAC*100)}% of slots per sector in discretionary "
        "buckets, **exempt** for confirmed-LEADING themes (so a real leadership cluster can "
        "be over-weighted, but a random hot sector cannot crowd everything).",
        "- **Reserve for new entrants:** the emerging/accumulation/pullback buckets (375 "
        "slots) are explicitly reserved for non-extended setups.",
        "- **Late names kept but downgraded:** LATE_EXTENDED/PARABOLIC names are NOT deleted "
        "— they stay visible (core_liquid / monitor) but are excluded from the early-entry "
        "buckets and flagged `stage_label`.",
        "- **Refresh:** at least daily (the production builder already refreshes every 30 "
        "min; this policy changes the ranking objective, not the cadence).",
        "- **Provenance:** every selected ticker records its `selection_bucket`, "
        "`stage_label`, `early_leader_score`, and `reason_codes` in the Task 7 ledger.",
        "",
        "## Early-Leader Score (Task 3) components (0–100)",
        "- RS acceleration (0–25): rs20>0, rs40>0, rs20>rs40 (accelerating).",
        "- Controlled accumulation (0–25): volume expansion without extension, above rising "
        "EMA20, higher lows, tight range.",
        "- Theme confirmation (0–20): LEADING=20 / EMERGING=12 / EXTENDED=5.",
        "- Entry potential (0–15): near EMA20, pullback/reclaim, measurable stop.",
        "- Data quality (0–15): ≥200 bars, deep cache, liquidity.",
        "- **Late-extension penalty:** up to −40 from the sum (scaled by ext-EMA20 / ext-"
        "SMA50 / parabolic / RSI-extreme).",
        "",
        "## Observed effect (today's research comparison)",
        f"- Current top-1000: late {c['late_pct']}%, early {c['early_pct']}%.",
        f"- Proposed: late {pr['late_pct']}%, early {pr['early_pct']}%, size {pr['size']}.",
        f"- {res['comparison']['added']} names added ({res['comparison']['added_that_are_early']} "
        f"early-stage), {res['comparison']['dropped']} dropped "
        f"({res['comparison']['dropped_that_are_late']} late-stage).",
        "",
        "## What is NOT done",
        "- No production universe/ranking change. No new strategy, signals, or governance "
        "change. The earnings_drift bucket is `NOT_RETAINED` pending point-in-time earnings.",
        "- Promotion requires the Task 7 ledger to accrue and a forward replay to show the "
        "proposed universe surfaces winners EARLIER point-in-time — not just covers them today.",
        "",
    ]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(L) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Dynamic universe selection (research-only).")
    ap.add_argument("--historize", action="store_true",
                    help="append today's universe-selection ledger rows")
    ap.add_argument("--current-only", action="store_true",
                    help="score only the current top-1000 (faster; skips missed-name discovery)")
    args = ap.parse_args(argv)

    res = build(full_universe=not args.current_only, do_historize=args.historize)
    dataio.write_json(dataio.RESEARCH_CACHE / "universe_dynamic_selection_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "universe_dynamic_selection_latest.txt", lines)
    _write_policy_doc(res)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

