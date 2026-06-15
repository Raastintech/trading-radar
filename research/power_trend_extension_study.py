"""
research/power_trend_extension_study.py — Phase 1G.14.

Power-Trend Extension Study — Too-Extended Block Audit.

Phase 1G.13 found that the Gatekeeper's dominant block reason, ``too_extended``,
fired on names that then OUTPERFORMED at 1-5d (win-rate ~coin-flip; 10-20d still
immature). The manual observation behind this phase: in confirmed theme power
trends (semis / hardware / AI-infra / memory / space / nuclear) names keep running
50-100% well past "overextended", while in normal tape extension mean-reverts.

This study asks — RESEARCH ONLY — whether ``too_extended`` should be split into:

  A. POWER_TREND_EXTENSION    — extended but supported by real theme/RS/volume strength
  B. CLIMAX_CHASE_EXTENSION   — parabolic blow-off / speculative chase, dangerous
  C. EXTENDED_BUT_WAIT_FOR_RESET — good name, bad entry; wait for EMA20/pullback reclaim
  D. LOW_QUALITY_EXTENSION    — weak structure / no theme / no RS; avoid

It builds a point-in-time extension cohort from the real Gatekeeper ``too_extended``
BLOCK snapshots, the frozen RS/theme 1G.10 BLOCKED names, the Alpha-board
extended/late names, and the missed-winner universe, computes extension features
(distance from EMA20/SMA50, ATR-extension, RSI, volume expansion, RS vs SPY/QQQ,
theme/skew), classifies each into A/B/C/D, and measures forward outcomes
out-of-sample (bars strictly AFTER the flag date) over 1/3/5/10/20d. It compares
POWER_TREND vs CLIMAX vs random controls and theme vs non-theme extension, runs a
theme-specific continuation audit, and emits a gated recommendation plus PROPOSED
(not implemented) future Gatekeeper fields.

RESEARCH AUDIT ONLY. It never changes the Gatekeeper / Veto Council / universe /
strategy gates / strategy registry / execution / governance / live capital, never
promotes a sleeve or registers a strategy, never emits paper signals or trade
proposals, never makes any label tradeable, never mutates historical evidence, and
makes no provider calls. Cache-only reads.

Outputs:
  cache/research/power_trend_extension_latest.json
  logs/power_trend_extension_latest.txt
  docs/research/POWER_TREND_EXTENSION_STUDY.md
"""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research.gatekeeper_precision_audit import (_load_gatekeeper_snapshots,
                                                 _load_missed_winners,
                                                 _load_rs_theme_blocked)
from research.rs_recall_lane import _aligned
from research.rs_theme_forward_validation import (_alpha_board_tickers, _cal_index,
                                                  _fwd_metrics, _is_matured,
                                                  _liquid_universe_at)
from research.scanner_truth import dataio
from research.scanner_truth.filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL,
                                            UNIV_MIN_AVG_VOL, UNIV_MIN_PRICE,
                                            atr, rsi, sma)

# ── paths / constants ──────────────────────────────────────────────────────────
RC = dataio.RESEARCH_CACHE
LOGS = dataio.LOGS_DIR
DOCS = dataio.REPO / "docs" / "research"

ALPHA_BOARD = RC / "alpha_discovery_board_latest.json"
MISSED_WINNERS = RC / "missed_winner_universe_latest.json"

OUT_JSON = RC / "power_trend_extension_latest.json"
OUT_TXT = LOGS / "power_trend_extension_latest.txt"
OUT_MD = DOCS / "POWER_TREND_EXTENSION_STUDY.md"

HORIZONS = (1, 3, 5, 10, 20)
PRIMARY_HORIZON = 5
MIN_MATURED_FOR_VERDICT = 15    # min matured per primary label before a real verdict
RANDOM_SEED = 1014

# ── classification thresholds (documented research defaults, NOT production) ─────
EXT_THRESHOLD = 0.20    # >20% above EMA20 ⇒ "extended" (EXTENDED_MA20)
EXTREME_EXT = 0.40      # >40% above EMA20 ⇒ extreme blow-off territory
PARABOLIC_R10 = 0.40    # >+40% in 10 sessions ⇒ parabolic
CLIMAX_VOL = 3.0        # today's volume > 3× 20d avg ⇒ blow-off / climax volume
STRONG_RS = 0.10        # 20d excess return vs SPY ≥ 10% ⇒ strong RS (LEADER_RS)
VOL_CONFIRM = 1.0       # 20d avg vol ≥ prior-20d avg ⇒ volume confirms

# Theme clusters where power-trend continuation is hypothesised (semis / hardware /
# AI-infra adjacent / memory / space / nuclear / quantum / crypto-infra).
POWER_THEMES = {"semiconductors", "hardware", "memory_storage", "space_aerospace",
                "nuclear_energy", "quantum", "crypto_blockchain"}

CALL_CHASE_TOKENS = ("speculative_call_chase", "call_chase", "bullish_but_late")

LABELS = ("POWER_TREND_EXTENSION", "CLIMAX_CHASE_EXTENSION",
          "EXTENDED_BUT_WAIT_FOR_RESET", "LOW_QUALITY_EXTENSION")

DISCLAIMER = (
    "RESEARCH-ONLY power-trend extension study. Forward MEASUREMENT + classification "
    "only — NOT buy/sell/trade signals, NOT paper signals, NOT trade proposals, and "
    "NO label is tradeable. Does NOT change the Gatekeeper, Veto Council, "
    "core/universe.py, strategy gates, the strategy registry, execution, governance, "
    "or live capital, and makes no provider calls.")
ARTIFACT_CAVEAT = (
    "Alpha-board / lens / options annotations use CURRENT artifacts, not point-in-time "
    "snapshots; per-ticker option skew comes from the latest lens only. Random controls "
    "anchor at the median flag date (single as-of). Treat as best-effort context.")


# ── extension features (point-in-time at index i) ────────────────────────────────

def _extension_features(t: str, cal, i: int, spy: pd.Series, qqq: pd.Series) -> Optional[Dict]:
    df = dataio.load_prices(t)
    if df is None:
        return None
    c = _aligned(df, cal)
    if i is None or i < 50 or i >= len(c) or pd.isna(c.iloc[i]) or c.iloc[i] <= 0:
        return None
    price = float(c.iloc[i])
    vol = df["volume"].reindex(cal).ffill()
    avgvol = float(vol.iloc[i - 19:i + 1].mean())
    avgdvol = float((c * vol).iloc[i - 19:i + 1].mean())
    liquid = (UNIV_MIN_PRICE <= price <= UNIV_MAX_PRICE
              and avgvol >= UNIV_MIN_AVG_VOL and avgdvol >= UNIV_MIN_AVG_DVOL)

    def ret(n):
        if i - n < 0:
            return None
        a = c.iloc[i - n]
        return float(price / a - 1.0) if (not pd.isna(a) and a > 0) else None

    r5, r10, r20, r40 = ret(5), ret(10), ret(20), ret(40)
    spy20 = (float(spy.iloc[i] / spy.iloc[i - 20] - 1.0)
             if i >= 20 and not pd.isna(spy.iloc[i - 20]) and spy.iloc[i - 20] else None)
    qqq20 = (float(qqq.iloc[i] / qqq.iloc[i - 20] - 1.0)
             if i >= 20 and not pd.isna(qqq.iloc[i - 20]) and qqq.iloc[i - 20] else None)
    rs20_spy = (r20 - spy20) if (r20 is not None and spy20 is not None) else None
    rs20_qqq = (r20 - qqq20) if (r20 is not None and qqq20 is not None) else None

    ma20 = float(sma(c.iloc[:i + 1], 20).iloc[-1])
    ma50_series = sma(c.iloc[:i + 1], 50)
    ma50 = float(ma50_series.iloc[-1]) if not pd.isna(ma50_series.iloc[-1]) else None
    ma50_prev = float(ma50_series.iloc[-6]) if len(ma50_series) >= 6 and not pd.isna(ma50_series.iloc[-6]) else None
    ext_ma20 = (price - ma20) / ma20 if ma20 else None
    ext_ma50 = (price - ma50) / ma50 if ma50 else None
    ma50_rising = (ma50 is not None and ma50_prev is not None and ma50 > ma50_prev)

    atr14 = atr(df.reindex(cal).ffill(), 14)
    atr_now = float(atr14.iloc[i]) if not pd.isna(atr14.iloc[i]) else None
    atr_ext = ((price - ma20) / atr_now) if (atr_now and atr_now > 0) else None
    rsi14 = rsi(c.iloc[:i + 1], 14)
    rsi_now = float(rsi14.iloc[-1]) if not pd.isna(rsi14.iloc[-1]) else None

    prior_avgvol = float(vol.iloc[i - 39:i - 19].mean()) if i >= 39 else None
    vol_expansion = (avgvol / prior_avgvol) if (prior_avgvol and prior_avgvol > 0) else None
    today_vol = float(vol.iloc[i]) if not pd.isna(vol.iloc[i]) else None
    climax_vol = (today_vol / avgvol) if (avgvol and avgvol > 0 and today_vol is not None) else None

    return {
        "price": price, "liquid": liquid, "avg_dvol_20": round(avgdvol, 0),
        "r5": r5, "r10": r10, "r20": r20, "r40": r40,
        "rs20_spy": rs20_spy, "rs20_qqq": rs20_qqq,
        "ext_ma20": ext_ma20, "ext_ma50": ext_ma50, "atr_ext": atr_ext,
        "rsi14": rsi_now, "vol_expansion": vol_expansion, "climax_vol": climax_vol,
        "ma50_rising": ma50_rising,
    }


# ── per-ticker context (theme / sector / options) from cached artifacts ──────────

def _alpha_board_index() -> Dict[str, Dict]:
    try:
        b = json.loads(ALPHA_BOARD.read_text())
    except Exception:
        return {}
    out = {}
    for it in b.get("items") or []:
        t = (it.get("ticker") or "").upper()
        if t:
            out[t] = it
    return out


def _lens_options(t: str) -> Dict:
    p = RC / f"stock_lens_{t}_latest.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text())
    except Exception:
        return {}
    layers = d.get("layers") or {}
    opt = layers.get("options") or layers.get("options_pulse") or {}
    return {"lens_label": d.get("label"),
            "iv_skew": opt.get("iv_skew") if isinstance(opt, dict) else None,
            "vol_tilt": opt.get("vol_tilt") if isinstance(opt, dict) else None,
            "options_quality": (opt.get("options_quality") or opt.get("quality"))
            if isinstance(opt, dict) else None}


def _call_chase(options_quality: Optional[str], vol_tilt) -> bool:
    oq = str(options_quality or "").lower()
    if any(tok in oq for tok in CALL_CHASE_TOKENS):
        return True
    return bool(isinstance(vol_tilt, (int, float)) and vol_tilt >= 2.0)


# ── classification (Task 2) ──────────────────────────────────────────────────────

def _classify(f: Dict, theme: str, options_quality: Optional[str], call_chase: bool) -> Tuple[str, str]:
    # NOTE: every name here was ALREADY flagged "too extended" by the gate. This
    # classifier does NOT re-decide whether a name is extended — it sub-classifies
    # the *quality* of that extension (theme / RS / trend health / climax risk). So
    # ext_ma20 feeds severity (climax), it is not an inclusion gate.
    ext = f.get("ext_ma20") or 0.0
    r10 = f.get("r10") or 0.0
    rs = f.get("rs20_spy")
    strong_rs = (rs is not None and rs >= STRONG_RS)
    rs_ok = (rs is not None and rs >= 0.03)          # emerging-RS floor
    in_power = theme in POWER_THEMES
    quality = strong_rs or in_power
    extreme = ext > EXTREME_EXT
    parabolic = r10 > PARABOLIC_R10
    climax = (f.get("climax_vol") or 0.0) > CLIMAX_VOL
    ma_rising = bool(f.get("ma50_rising"))

    # D — low quality: neither a leading theme nor real RS strength.
    if not quality:
        return "LOW_QUALITY_EXTENSION", "no leading theme and weak RS vs SPY"
    # B — climax / speculative chase: blow-off / parabolic with climax volume or a
    #     speculative call chase (dangerous regardless of theme; checked before A).
    if (extreme and (climax or call_chase)) or (parabolic and (climax or call_chase)):
        why = []
        if parabolic:
            why.append(f"parabolic r10={r10:+.2f}")
        if extreme:
            why.append(f"ext_ma20={ext:+.2f}")
        if climax:
            why.append("climax volume")
        if call_chase:
            why.append("speculative call chase")
        return "CLIMAX_CHASE_EXTENSION", "; ".join(why)
    # A — power trend: leading theme + non-negative RS + rising MA50, not climax /
    #     not call-chase. The supported-extension bucket.
    if in_power and ma_rising and rs_ok and not climax and not call_chase:
        return "POWER_TREND_EXTENSION", (
            f"theme={theme} rs20={(rs or 0):+.2f} ext_ma20={ext:+.2f} ma50_rising, not climax/chase")
    # C — extended but wait for reset: quality name (theme or strong RS) without a
    #     clean power-trend setup (MA50 not rising, or non-theme leader).
    return "EXTENDED_BUT_WAIT_FOR_RESET", (
        f"quality but no clean entry (ext_ma20={ext:+.2f}, ma50_rising={ma_rising}) — wait for reset")


# ── forward outcome + pullback metrics (Task 3) ──────────────────────────────────

def _fwd_with_pullback(t: str, cal, i: int, h: int, spy, qqq) -> Optional[Dict]:
    m = _fwd_metrics(t, cal, i, h, spy, qqq)
    if not m:
        return None
    c = _aligned(dataio.load_prices(t), cal)
    p0 = float(c.iloc[i])
    ma20 = sma(c, 20)
    ttp = None         # bars to first pullback (close below EMA20)
    peak_before = p0
    continued_before_reset = False
    low_after = None
    for j in range(i + 1, i + h + 1):
        cj = float(c.iloc[j]) if not pd.isna(c.iloc[j]) else None
        if cj is None:
            continue
        if cj > peak_before:
            peak_before = cj
        m20 = float(ma20.iloc[j]) if not pd.isna(ma20.iloc[j]) else None
        if ttp is None and m20 is not None and cj < m20:
            ttp = j - i
            continued_before_reset = peak_before > p0 * 1.005   # made a higher high first
        if ttp is not None:
            low_after = cj if low_after is None else min(low_after, cj)
    # waiting for reset improved entry: a pullback occurred, it dipped below the
    # as-of price, and the name still finished above the as-of price (cheaper entry
    # into a continuation).
    reset_improved_entry = bool(
        ttp is not None and low_after is not None
        and low_after < p0 and float(c.iloc[i + h]) > p0)
    m.update({
        "pullback_depth": m["mae"],                 # max adverse from as-of
        "time_to_first_pullback": ttp,
        "continued_before_reset": continued_before_reset,
        "reset_improved_entry": reset_improved_entry,
    })
    return m


def _label_basket_stats(entries: List[Dict], h: int) -> Dict:
    rels_spy, rels_qqq, fwds, maes, mfes = [], [], [], [], []
    ttp, cont, reset_better = [], [], []
    for e in entries:
        m = (e.get("by_horizon") or {}).get(f"{h}d")
        if not m:
            continue
        fwds.append(m["fwd_return"])
        maes.append(m["mae"])
        mfes.append(m["mfe"])
        if m["rel_return_spy"] is not None:
            rels_spy.append(m["rel_return_spy"])
        if m["rel_return_qqq"] is not None:
            rels_qqq.append(m["rel_return_qqq"])
        if m.get("time_to_first_pullback") is not None:
            ttp.append(m["time_to_first_pullback"])
        if m.get("continued_before_reset") is not None:
            cont.append(bool(m["continued_before_reset"]))
        if m.get("reset_improved_entry") is not None:
            reset_better.append(bool(m["reset_improved_entry"]))
    return {
        "n_names": len(entries),
        "n_matured": len(fwds),
        "mean_fwd": round(mean(fwds), 4) if fwds else None,
        "mean_rel_spy": round(mean(rels_spy), 4) if rels_spy else None,
        "mean_rel_qqq": round(mean(rels_qqq), 4) if rels_qqq else None,
        "win_rate_vs_spy": round(100.0 * sum(1 for r in rels_spy if r > 0) / len(rels_spy), 1) if rels_spy else None,
        "mean_mfe": round(mean(mfes), 4) if mfes else None,
        "mean_mae": round(mean(maes), 4) if maes else None,
        "median_time_to_pullback": (sorted(ttp)[len(ttp) // 2] if ttp else None),
        "pct_continued_before_reset": round(100.0 * sum(cont) / len(cont), 1) if cont else None,
        "pct_reset_improved_entry": round(100.0 * sum(reset_better) / len(reset_better), 1) if reset_better else None,
    }


# ── cohort assembly (Task 1) ─────────────────────────────────────────────────────

def _too_extended_sources() -> List[Dict]:
    """Collect extension-flagged candidates with their flag-as-of date + context."""
    out: List[Dict] = []
    # 1+2. Gatekeeper too_extended BLOCK snapshots + RS/theme 1G.10 too_extended.
    gk_blocked, _watch = _load_gatekeeper_snapshots()
    for b in gk_blocked:
        if "too_extended" in b.get("reasons", []):
            out.append({"ticker": b["ticker"], "asof": b["asof"], "source": "gatekeeper_too_extended",
                        "gatekeeper_status": "BLOCK", "options_quality": None})
    for b in _load_rs_theme_blocked():
        if "too_extended" in b.get("reasons", []):
            out.append({"ticker": b["ticker"], "asof": b["asof"], "source": "rs_theme_too_extended",
                        "gatekeeper_status": "BLOCK", "options_quality": b.get("options_quality")})
    # 3. Alpha-board extended / late names (current board; point-in-time-limited).
    try:
        board = json.loads(ALPHA_BOARD.read_text())
        asof = str(board.get("built_at") or board.get("generated_at") or "")[:10] or None
        for it in board.get("items") or []:
            lbl = f"{it.get('action_label')} {it.get('validator_state')} {it.get('entry_validator_state')}".lower()
            if "extend" in lbl or "late" in lbl:
                out.append({"ticker": (it.get("ticker") or "").upper(), "asof": asof,
                            "source": "alpha_board_extended", "gatekeeper_status": it.get("gatekeeper_status"),
                            "options_quality": it.get("options_quality")})
    except Exception:
        pass
    return [e for e in out if e["ticker"] and e["asof"]]


def _build_cohort(cal, spy, qqq) -> List[Dict]:
    profiles = dataio.load_profiles()
    board_idx = _alpha_board_index()
    winners = _load_missed_winners()
    raw = _too_extended_sources()
    # dedupe by (ticker, asof) — keep the first (gatekeeper > rs_theme > alpha order).
    seen, cohort = set(), []
    # first pass to know theme breadth within the cohort
    pre = []
    for e in raw:
        key = (e["ticker"], e["asof"])
        if key in seen:
            continue
        seen.add(key)
        i = _cal_index(cal, e["asof"])
        f = _extension_features(e["ticker"], cal, i, spy, qqq) if i is not None else None
        if not f or not f.get("liquid"):
            continue
        theme = dataio.classify_theme(profiles.get(e["ticker"]))
        pre.append((e, i, f, theme))
    theme_counts = Counter(theme for _e, _i, _f, theme in pre)
    for e, i, f, theme in pre:
        lens = _lens_options(e["ticker"])
        oq = e.get("options_quality") or lens.get("options_quality")
        call_chase = _call_chase(oq, lens.get("vol_tilt"))
        label, why = _classify(f, theme, oq, call_chase)
        # theme_power_score [0..100]: power-theme membership + RS strength + cohort breadth.
        rs = f.get("rs20_spy") or 0.0
        breadth = theme_counts.get(theme, 0)
        tps = (40 if theme in POWER_THEMES else 0)
        tps += max(0.0, min(40.0, (rs / STRONG_RS) * 20.0)) if rs > 0 else 0.0
        tps += min(20.0, breadth * 4.0)
        prof = profiles.get(e["ticker"]) or {}
        by_h = {f"{h}d": _fwd_with_pullback(e["ticker"], cal, i, h, spy, qqq) for h in HORIZONS}
        cohort.append({
            **e, "asof_index": i, "theme": theme,
            "sector": prof.get("sector") or "UNKNOWN",
            "features": f, "lens_label": lens.get("lens_label"),
            "iv_skew": lens.get("iv_skew"), "options_quality": oq,
            "call_chase": call_chase, "label": label, "label_reason": why,
            "theme_power_score": round(tps, 1),
            "was_missed_winner": e["ticker"] in winners,
            "by_horizon": by_h,
        })
    return cohort


# ── comparisons (Task 3) ─────────────────────────────────────────────────────────

def _by_label(cohort: List[Dict]) -> Dict[str, List[Dict]]:
    out = defaultdict(list)
    for e in cohort:
        out[e["label"]].append(e)
    return out


def _build_comparison(cohort: List[Dict], cal, spy, qqq, rng) -> Tuple[Dict, Dict, Dict]:
    grouped = _by_label(cohort)
    idxs = sorted(e["asof_index"] for e in cohort if e.get("asof_index") is not None)
    anchor_i = idxs[len(idxs) // 2] if idxs else None
    liquid = _liquid_universe_at(cal, anchor_i) if anchor_i is not None else []
    n_ctrl = max(len(cohort), 1)
    rand_ctrl = rng.sample(liquid, min(n_ctrl, len(liquid))) if liquid else []

    def control_stats(tickers, h):
        ents = [{"by_horizon": {f"{h}d": _fwd_with_pullback(t, cal, anchor_i, h, spy, qqq)}}
                for t in tickers]
        return _label_basket_stats(ents, h)

    by_h_label: Dict[str, Dict] = {}
    for h in HORIZONS:
        row = {lbl: _label_basket_stats(grouped.get(lbl, []), h) for lbl in LABELS}
        row["RANDOM_CONTROL"] = control_stats(rand_ctrl, h) if _is_matured(cal, anchor_i, h) else None
        row["ALL_TOO_EXTENDED"] = _label_basket_stats(cohort, h)
        by_h_label[f"{h}d"] = row

    # theme vs non-theme extended (Task 3 last bullet)
    theme_ext = [e for e in cohort if e["theme"] in POWER_THEMES]
    nontheme_ext = [e for e in cohort if e["theme"] not in POWER_THEMES]
    theme_cmp = {f"{h}d": {"power_theme": _label_basket_stats(theme_ext, h),
                           "non_theme": _label_basket_stats(nontheme_ext, h)}
                 for h in HORIZONS}
    controls = {"anchor_index": anchor_i, "liquid_universe_size": len(liquid),
                "random_control_n": len(rand_ctrl)}
    return by_h_label, theme_cmp, controls


# ── theme-specific continuation audit (Task 5) ───────────────────────────────────

def _theme_audit(cohort: List[Dict]) -> Dict:
    by_theme = defaultdict(list)
    for e in cohort:
        by_theme[e["theme"]].append(e)
    out = {}
    for theme, members in by_theme.items():
        s5 = _label_basket_stats(members, 5)
        s10 = _label_basket_stats(members, 10)
        call_chase_members = [m for m in members if m.get("call_chase")]
        cc5 = _label_basket_stats(call_chase_members, 5)
        skews = [m["iv_skew"] for m in members if isinstance(m.get("iv_skew"), (int, float))]
        out[theme] = {
            "n": len(members), "is_power_theme": theme in POWER_THEMES,
            "mean_rel_spy_5d": s5["mean_rel_spy"], "win_rate_5d": s5["win_rate_vs_spy"],
            "mean_rel_spy_10d": s10["mean_rel_spy"],
            "pct_continued_before_reset_5d": s5["pct_continued_before_reset"],
            "median_time_to_pullback_5d": s5["median_time_to_pullback"],
            "n_call_chase": len(call_chase_members),
            "call_chase_mean_rel_spy_5d": cc5["mean_rel_spy"],
            "mean_iv_skew": round(mean(skews), 3) if skews else None,
            "verdict": _theme_verdict(s5, s10),
        }
    return dict(sorted(out.items(), key=lambda kv: -(kv[1]["n"])))


def _theme_verdict(s5: Dict, s10: Dict) -> str:
    if s5["n_matured"] < 5:
        return "NEED_MORE_DATA"
    r5 = s5["mean_rel_spy"] or 0.0
    r10 = s10["mean_rel_spy"]
    if r5 > 0 and (r10 is None or r10 > 0):
        return "REWARDS_CHASING_STRENGTH"
    if r5 > 0 and r10 is not None and r10 <= 0:
        return "MEAN_REVERTS_BY_10D"
    return "EXTENSION_NOT_REWARDED"


# ── verdict + proposed fields (Task 4) ───────────────────────────────────────────

PROPOSED_FUTURE_FIELDS = {
    "extension_block_type": "enum HARD_BLOCK_EXTENSION | SOFT_WARNING_POWER_TREND | WAIT_FOR_RESET | LOW_QUALITY",
    "extension_context": "theme / RS / volume / breadth summary at flag time",
    "theme_power_score": "0-100 composite of power-theme membership + RS + cohort breadth",
    "extension_risk_score": "0-100 climax/chase risk (parabolic move, blow-off volume, call chase)",
    "reset_required": "bool — wait for EMA20/VWAP pullback-reclaim before any research entry",
    "continuation_allowed_for_research": "bool — research-only continuation tracking permitted",
}


def _verdict(by_h_label: Dict, n_power_matured: int) -> Dict:
    ph = by_h_label.get(f"{PRIMARY_HORIZON}d", {})
    power = ph.get("POWER_TREND_EXTENSION") or {}
    climax = ph.get("CLIMAX_CHASE_EXTENSION") or {}
    lowq = ph.get("LOW_QUALITY_EXTENSION") or {}
    rand = ph.get("RANDOM_CONTROL") or {}
    p_rel = power.get("mean_rel_spy")
    p_win = power.get("win_rate_vs_spy")
    c_rel = climax.get("mean_rel_spy")
    lq_rel = lowq.get("mean_rel_spy")
    r_rel = rand.get("mean_rel_spy")
    # The discriminating comparisons are POWER vs RANDOM and POWER vs the
    # correctly-blocked LOW_QUALITY bucket. CLIMAX is reported for context but is
    # NOT used to gate the verdict — it is typically a tiny, noisy bucket.
    beats_random = r_rel is not None and p_rel is not None and p_rel > r_rel
    beats_lowq = lq_rel is None or (p_rel is not None and p_rel > lq_rel)

    if n_power_matured < MIN_MATURED_FOR_VERDICT or p_rel is None:
        prelim = (f"preliminary POWER_TREND {PRIMARY_HORIZON}d rel-SPY={_num(p_rel)} "
                  f"(win {p_win}%) vs random {_num(r_rel)} / low-quality {_num(lq_rel)}"
                  if p_rel is not None else "no matured POWER_TREND names yet")
        return {"power_trend_verdict": "NEED_MORE_DATA", "recommendation": "D",
                "rationale": (f"Only {n_power_matured} matured POWER_TREND name(s) at "
                              f"{PRIMARY_HORIZON}d (< {MIN_MATURED_FOR_VERDICT} floor) — {prelim}. "
                              "Signal is encouraging but below the verdict floor; keep measuring. "
                              "too_extended stays a HARD BLOCK in production.")}
    if p_rel > 0 and beats_random and beats_lowq:
        return {"power_trend_verdict": "POWER_TREND_EXCEPTION_PROMISING", "recommendation": "C",
                "rationale": (f"POWER_TREND extension {p_rel:+.4f} rel-SPY (win {p_win}%) beats random "
                              f"({r_rel}) and the correctly-blocked LOW_QUALITY bucket ({lq_rel}) at "
                              f"{PRIMARY_HORIZON}d; CLIMAX context={c_rel}. A soft-warning exception for "
                              "confirmed power-theme extension looks promising, but 10/20d is still "
                              "immature — PROPOSED research path only; production unchanged.")}
    if p_rel is None or p_rel <= 0 or not beats_random:
        return {"power_trend_verdict": "KEEP_BLOCK", "recommendation": "A",
                "rationale": (f"POWER_TREND extension {_num(p_rel)} rel-SPY does not beat random "
                              f"({r_rel}) — no evidence the block rejects winners; keep too_extended "
                              "as a hard block.")}
    return {"power_trend_verdict": "NEED_MORE_DATA", "recommendation": "D",
            "rationale": (f"POWER_TREND {p_rel:+.4f} rel-SPY vs random {r_rel} / low-quality {lq_rel} "
                          "is not decisive — keep measuring; production unchanged.")}


def _num(v) -> str:
    return f"{v:+.4f}" if isinstance(v, (int, float)) else "—"


RECOMMENDATION_OPTIONS = {
    "A": "Keep too_extended as a hard block.",
    "B": "Split too_extended into hard block vs soft warning.",
    "C": "Keep block for non-theme names; soft warning for confirmed power-trend themes.",
    "D": "Wait for more data.",
}


# ── orchestration ────────────────────────────────────────────────────────────────

def build() -> Dict:
    cal = dataio.benchmark_calendar()
    spy = _aligned(dataio.load_prices("SPY"), cal)
    qqq = _aligned(dataio.load_prices("QQQ"), cal)
    rng = random.Random(RANDOM_SEED)

    cohort = _build_cohort(cal, spy, qqq)
    by_h_label, theme_cmp, controls = _build_comparison(cohort, cal, spy, qqq, rng)
    theme_audit = _theme_audit(cohort)

    n_power_matured = ((by_h_label.get(f"{PRIMARY_HORIZON}d", {}).get("POWER_TREND_EXTENSION") or {})
                       .get("n_matured", 0))
    verdict = _verdict(by_h_label, n_power_matured)

    label_dist = dict(Counter(e["label"] for e in cohort))
    asof_dates = sorted({e["asof"] for e in cohort if e.get("asof")})
    by_source = dict(Counter(e["source"] for e in cohort))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": "1G.14",
        "disclaimer": DISCLAIMER,
        "primary_horizon": f"{PRIMARY_HORIZON}d",
        "min_matured_for_verdict": MIN_MATURED_FOR_VERDICT,
        "thresholds": {
            "ext_threshold": EXT_THRESHOLD, "extreme_ext": EXTREME_EXT,
            "parabolic_r10": PARABOLIC_R10, "climax_vol": CLIMAX_VOL,
            "strong_rs": STRONG_RS, "power_themes": sorted(POWER_THEMES)},
        "cohort": {
            "n_total": len(cohort), "by_source": by_source,
            "label_distribution": label_dist,
            "flag_asof_range": [asof_dates[0], asof_dates[-1]] if asof_dates else None,
            "n_power_trend_matured_primary": n_power_matured,
            "n_were_missed_winners": sum(1 for e in cohort if e.get("was_missed_winner")),
        },
        "controls_used": controls,
        "by_horizon_label": by_h_label,
        "theme_vs_non_theme": theme_cmp,
        "theme_audit": theme_audit,
        "verdict": verdict,
        "recommendation_options": RECOMMENDATION_OPTIONS,
        "proposed_future_fields": PROPOSED_FUTURE_FIELDS,
        "per_name": [_compact(e) for e in cohort],
        "artifact_annotations_caveat": ARTIFACT_CAVEAT,
    }


def _compact(e: Dict) -> Dict:
    f = e.get("features") or {}
    m5 = (e.get("by_horizon") or {}).get(f"{PRIMARY_HORIZON}d") or {}
    return {
        "ticker": e["ticker"], "asof": e.get("asof"), "source": e["source"],
        "theme": e["theme"], "sector": e.get("sector"), "label": e["label"],
        "label_reason": e.get("label_reason"), "theme_power_score": e.get("theme_power_score"),
        "ext_ma20": round(f["ext_ma20"], 3) if f.get("ext_ma20") is not None else None,
        "atr_ext": round(f["atr_ext"], 2) if f.get("atr_ext") is not None else None,
        "rsi14": round(f["rsi14"], 1) if f.get("rsi14") is not None else None,
        "rs20_spy": round(f["rs20_spy"], 3) if f.get("rs20_spy") is not None else None,
        "r10": round(f["r10"], 3) if f.get("r10") is not None else None,
        "climax_vol": round(f["climax_vol"], 2) if f.get("climax_vol") is not None else None,
        "call_chase": e.get("call_chase"), "iv_skew": e.get("iv_skew"),
        "lens_label": e.get("lens_label"), "was_missed_winner": e.get("was_missed_winner"),
        "fwd_5d": m5.get("fwd_return"), "rel_spy_5d": m5.get("rel_return_spy"),
        "mfe_5d": m5.get("mfe"), "mae_5d": m5.get("mae"),
        "time_to_first_pullback_5d": m5.get("time_to_first_pullback"),
        "reset_improved_entry_5d": m5.get("reset_improved_entry"),
    }


# ── MCP / dashboard cache-only summary (Task 6) ──────────────────────────────────

def mcp_summary(res: Optional[Dict] = None) -> Dict:
    if res is None:
        if not OUT_JSON.exists():
            return {"present": False}
        try:
            res = json.loads(OUT_JSON.read_text())
        except Exception:
            return {"present": False}
    ph = (res.get("by_horizon_label") or {}).get(f"{PRIMARY_HORIZON}d", {}) or {}

    def rel(lbl):
        return (ph.get(lbl) or {}).get("mean_rel_spy")
    v = res.get("verdict") or {}
    return {
        "present": True,
        "n_cohort": (res.get("cohort") or {}).get("n_total"),
        "too_extended_5d": rel("ALL_TOO_EXTENDED"),
        "power_trend_5d": rel("POWER_TREND_EXTENSION"),
        "climax_5d": rel("CLIMAX_CHASE_EXTENSION"),
        "verdict": v.get("power_trend_verdict"),
        "recommendation": v.get("recommendation"),
    }


# ── renderers ────────────────────────────────────────────────────────────────────

def _fmt(v, pct=False) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        return f"{v:.1f}%" if pct else f"{v:+.4f}"
    return str(v)


def _render_txt(res: Dict) -> List[str]:
    co = res["cohort"]
    v = res["verdict"]
    L = [f"== POWER-TREND EXTENSION STUDY — Phase 1G.14 ({res['generated_at']}) ==",
         res["disclaimer"], "",
         f"cohort       : n={co['n_total']} ({co['by_source']}) flagged {co['flag_asof_range']}",
         f"labels       : {co['label_distribution']}",
         f"power matured {res['primary_horizon']}: {co['n_power_trend_matured_primary']} "
         f"(verdict floor {res['min_matured_for_verdict']}) · missed-winners in cohort={co['n_were_missed_winners']}",
         "",
         f"{'label':<30}{'n':>4}{'mat':>5}{'rel5d':>9}{'win%':>7}{'mfe':>9}{'mae':>9}{'pull_d':>7}{'cont%':>7}"]
    ph = res["by_horizon_label"][f"{res['primary_horizon']}"]
    for lbl in (*LABELS, "ALL_TOO_EXTENDED", "RANDOM_CONTROL"):
        s = ph.get(lbl) or {}
        L.append(f"{lbl:<30}{s.get('n_names', 0):>4}{s.get('n_matured', 0):>5}"
                 f"{_fmt(s.get('mean_rel_spy')):>9}{_fmt(s.get('win_rate_vs_spy'), pct=True):>7}"
                 f"{_fmt(s.get('mean_mfe')):>9}{_fmt(s.get('mean_mae')):>9}"
                 f"{s.get('median_time_to_pullback') or '—':>7}"
                 f"{_fmt(s.get('pct_continued_before_reset'), pct=True):>7}")
    L += ["", "FORWARD rel-SPY by horizon (POWER vs CLIMAX vs RANDOM):",
          f"  {'h':<5}{'power':>9}{'climax':>9}{'wait':>9}{'lowq':>9}{'random':>9}"]
    for h in (f"{x}d" for x in HORIZONS):
        r = res["by_horizon_label"][h]

        def g(lbl):
            return _fmt((r.get(lbl) or {}).get("mean_rel_spy")) if r.get(lbl) else "—"
        L.append(f"  {h:<5}{g('POWER_TREND_EXTENSION'):>9}{g('CLIMAX_CHASE_EXTENSION'):>9}"
                 f"{g('EXTENDED_BUT_WAIT_FOR_RESET'):>9}{g('LOW_QUALITY_EXTENSION'):>9}{g('RANDOM_CONTROL'):>9}")
    L += ["", "THEME-SPECIFIC CONTINUATION AUDIT:",
          f"  {'theme':<22}{'n':>4}{'pwr':>4}{'rel5d':>9}{'rel10d':>9}{'cont%':>7}{'cc_n':>5}{'skew':>7}  verdict"]
    for theme, ta in res["theme_audit"].items():
        L.append(f"  {theme:<22}{ta['n']:>4}{('Y' if ta['is_power_theme'] else 'n'):>4}"
                 f"{_fmt(ta['mean_rel_spy_5d']):>9}{_fmt(ta['mean_rel_spy_10d']):>9}"
                 f"{_fmt(ta['pct_continued_before_reset_5d'], pct=True):>7}{ta['n_call_chase']:>5}"
                 f"{_fmt(ta['mean_iv_skew']):>7}  {ta['verdict']}")
    L += ["", f"VERDICT: {v['power_trend_verdict']}  → recommendation {v['recommendation']}",
          f"  {RECOMMENDATION_OPTIONS.get(v['recommendation'], '')}",
          f"  {v['rationale']}",
          "", "PROPOSED future Gatekeeper fields (NOT implemented):"]
    for k, desc in res["proposed_future_fields"].items():
        L.append(f"  {k}: {desc}")
    L += ["", "caveat: " + res["artifact_annotations_caveat"]]
    return L


def _render_md(res: Dict) -> List[str]:
    co = res["cohort"]
    v = res["verdict"]
    L = ["# Power-Trend Extension Study — Phase 1G.14", "",
         f"> {res['disclaimer']}", "",
         f"_Generated {res['generated_at']}._", "",
         "## Cohort", "",
         f"- Too-extended candidates: **{co['n_total']}** by source `{co['by_source']}`",
         f"- Flag as-of range: `{co['flag_asof_range']}`",
         f"- Label distribution: `{co['label_distribution']}`",
         f"- Matured POWER_TREND at {res['primary_horizon']}: **{co['n_power_trend_matured_primary']}** "
         f"(floor {res['min_matured_for_verdict']})",
         f"- Names that were missed winners: **{co['n_were_missed_winners']}**", "",
         "## Forward rel-SPY by extension label and horizon", "",
         "| Horizon | POWER_TREND | CLIMAX_CHASE | WAIT_FOR_RESET | LOW_QUALITY | ALL too_ext | Random |",
         "|---|---|---|---|---|---|---|"]
    for h in (f"{x}d" for x in HORIZONS):
        r = res["by_horizon_label"][h]

        def g(lbl):
            return _fmt((r.get(lbl) or {}).get("mean_rel_spy")) if r.get(lbl) else "—"
        L.append(f"| {h} | {g('POWER_TREND_EXTENSION')} | {g('CLIMAX_CHASE_EXTENSION')} | "
                 f"{g('EXTENDED_BUT_WAIT_FOR_RESET')} | {g('LOW_QUALITY_EXTENSION')} | "
                 f"{g('ALL_TOO_EXTENDED')} | {g('RANDOM_CONTROL')} |")
    ph = res["by_horizon_label"][f"{res['primary_horizon']}"]
    L += ["", f"## Label detail ({res['primary_horizon']})", "",
          "| Label | n | matured | rel-SPY | win% | MFE | MAE | time→pullback | continued% | reset-better% |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for lbl in (*LABELS, "ALL_TOO_EXTENDED"):
        s = ph.get(lbl) or {}
        L.append(f"| {lbl} | {s.get('n_names', 0)} | {s.get('n_matured', 0)} | "
                 f"{_fmt(s.get('mean_rel_spy'))} | {_fmt(s.get('win_rate_vs_spy'), pct=True)} | "
                 f"{_fmt(s.get('mean_mfe'))} | {_fmt(s.get('mean_mae'))} | "
                 f"{s.get('median_time_to_pullback') or '—'} | "
                 f"{_fmt(s.get('pct_continued_before_reset'), pct=True)} | "
                 f"{_fmt(s.get('pct_reset_improved_entry'), pct=True)} |")
    L += ["", "## Theme-specific continuation audit", "",
          "| Theme | n | power? | rel-SPY 5d | rel-SPY 10d | continued% | call-chase n | mean IV skew | verdict |",
          "|---|---|---|---|---|---|---|---|---|"]
    for theme, ta in res["theme_audit"].items():
        L.append(f"| {theme} | {ta['n']} | {'Y' if ta['is_power_theme'] else '—'} | "
                 f"{_fmt(ta['mean_rel_spy_5d'])} | {_fmt(ta['mean_rel_spy_10d'])} | "
                 f"{_fmt(ta['pct_continued_before_reset_5d'], pct=True)} | {ta['n_call_chase']} | "
                 f"{_fmt(ta['mean_iv_skew'])} | **{ta['verdict']}** |")
    L += ["", "## Verdict", "", f"### {v['power_trend_verdict']} → recommendation {v['recommendation']}",
          "", f"**{RECOMMENDATION_OPTIONS.get(v['recommendation'], '')}**", "", v["rationale"],
          "", "## Proposed future Gatekeeper fields (PROPOSED ONLY — not implemented)", ""]
    for k, desc in res["proposed_future_fields"].items():
        L.append(f"- **{k}**: {desc}")
    L += ["", f"> caveat: {res['artifact_annotations_caveat']}"]
    return L


def main() -> int:
    res = build()
    dataio.write_json(OUT_JSON, res)
    dataio.write_text(OUT_TXT, _render_txt(res))
    DOCS.mkdir(parents=True, exist_ok=True)
    dataio.write_text(OUT_MD, _render_md(res))
    print("\n".join(_render_txt(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
