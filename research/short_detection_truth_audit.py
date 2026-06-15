#!/usr/bin/env python3
"""
research/short_detection_truth_audit.py — Phase 1G.16

Short Detection Truth Audit — QQQ Breakdown / Tactical Short Regime Review.

RESEARCH-ONLY. This module audits whether the system's downside-detection layer
(the Short Opportunity Radar) is too blunt / too SPY-biased to notice a real
QQQ/tech/semis breakdown while SPY still looks superficially healthy.

It does NOT:
  - unfreeze SHORT_A (it stays FROZEN / research-only),
  - emit paper signals or trade proposals,
  - touch execution / governance / live-capital / strategy gates / Veto Council,
  - tune any production threshold.

It is cache-only: reads price parquets (cache/prices) and existing research
sidecars; never calls a provider; writes only research artifacts:
  - cache/research/short_detection_truth_audit_latest.json
  - logs/short_detection_truth_audit_latest.txt
  - docs/research/SHORT_DETECTION_TRUTH_AUDIT.md
  - data/research/short_detection_history.jsonl   (append-only forward spine)

Distinction this audit preserves (per operator):
  1. Old SHORT_A sleeve  — stays FROZEN (failed structurally).
  2. Short *detection*   — should still flag tactical short/hedge research
                            conditions when QQQ/tech/momentum breaks.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.scanner_truth import dataio  # noqa: E402

CACHE = dataio.RESEARCH_CACHE
JSON_OUT = CACHE / "short_detection_truth_audit_latest.json"
TXT_OUT = dataio.LOGS_DIR / "short_detection_truth_audit_latest.txt"
DOC_OUT = ROOT / "docs" / "research" / "SHORT_DETECTION_TRUTH_AUDIT.md"
HISTORY = dataio.HISTORY_DIR / "short_detection_history.jsonl"

VERSION = "SHORT_DETECTION_TRUTH_AUDIT_V1"

HORIZONS = (1, 3, 5, 10)

# Index / sector ETFs the audit reconstructs the tape from. SMH (semis) is
# included opportunistically — it is frequently absent from the shallow cache,
# so the loader degrades to XLK as the tech proxy.
INDEX_ETFS = ("SPY", "QQQ", "IWM", "SMH", "XLK", "VXX")
SECTOR_ETFS = ("XLK", "XLF", "XLE", "XLP", "XLU", "XLV", "XLI", "XLY", "XLB", "XLRE", "XLC")

# Reference "major tech / AI" universe for breadth + missed-short autopsy. Only
# the names with a cached parquet are actually used; the rest degrade out.
MAJOR_TECH = (
    "NVDA", "MSFT", "AAPL", "GOOGL", "GOOG", "AMZN", "META", "AVGO", "AMD",
    "TSLA", "NFLX", "CRM", "ORCL", "ADBE", "QCOM", "MU", "MRVL", "SMCI",
    "ARM", "PLTR", "NOW", "INTC", "TXN", "AMAT", "LRCX", "KLAC", "ASML",
    "ANET", "DELL", "TSM",
)

# ── Proposed short-side research states (Task 6). Research-only labels. ───────
STATE_SHORTS_OFF = "SHORTS_OFF"
STATE_HEDGE_WATCH = "HEDGE_WATCH"
STATE_FAILED_LEADER_WATCH = "FAILED_LEADER_WATCH"
STATE_TACTICAL_SHORT_RESEARCH = "TACTICAL_SHORT_RESEARCH"
STATE_SHORT_REGIME_ACTIVE = "SHORT_REGIME_ACTIVE"  # never auto-set; validation-gated

# Missed-short autopsy archetypes (Task 4).
ARCH_FAILED_LEADER = "FAILED_LEADER"
ARCH_REL_WEAKNESS = "RELATIVE_WEAKNESS_BREAKDOWN"
ARCH_POWER_TREND_UNWIND = "POWER_TREND_UNWIND"
ARCH_POST_EARNINGS = "POST_EARNINGS_FAILED_REACTION"
ARCH_CALL_CHASE_UNWIND = "OVERCROWDED_CALL_CHASE_UNWIND"
ARCH_INDEX_HEDGE = "INDEX_HEDGE_ONLY"
ARCH_NO_EDGE = "NO_SHORT_EDGE"

# Detection thresholds. Conservative and documented — NOT tuned to flatter the
# result. They define what "a tactical tech breakdown" means, not a trade rule.
TH = {
    "qqq_vel_warn_pct": -2.0,     # QQQ 5d return warning
    "qqq_vel_strong_pct": -4.0,   # QQQ 5d return strong
    "qqq_vel_severe_pct": -6.0,   # QQQ 5d return severe
    "qqq_rel_spy_pct": -2.0,      # QQQ underperforms SPY by this over 5d
    "tech_rel_spy_pct": -3.0,     # XLK/SMH underperforms SPY by this over 5d
    "vxx_stress_3d_pct": 5.0,     # VXX 3d pop = stress confirmation
    "tech_breadth_frac": 0.5,     # fraction of tech leaders above EMA20
    "failed_leaders_min": 3,      # # prior leaders breaking to escalate
    "sharp_drop_5d_pct": -5.0,    # candidate-level "sharp drop" over 5d
    "vix_benign": 20.0,           # VIX below this = "benign" in the SPY rule
}


# ── price helpers (cache-only) ───────────────────────────────────────────────
def _aligned_close(ticker: str, cal: pd.DatetimeIndex) -> Optional[pd.Series]:
    df = dataio.load_prices(ticker)
    if df is None or "close" not in df.columns:
        return None
    return df["close"].reindex(cal).ffill()


def _ret_pct(close: Optional[pd.Series], n: int) -> Optional[float]:
    if close is None:
        return None
    s = close.dropna()
    if len(s) <= n:
        return None
    prev = s.iloc[-1 - n]
    if prev == 0 or pd.isna(prev):
        return None
    return float((s.iloc[-1] / prev - 1.0) * 100.0)


def _returns_block(close: Optional[pd.Series]) -> Dict[str, Optional[float]]:
    return {f"ret_{n}d_pct": _ret_pct(close, n) for n in HORIZONS}


def _ema(close: Optional[pd.Series], span: int) -> Optional[float]:
    if close is None:
        return None
    s = close.dropna()
    if len(s) < span:
        return None
    return float(s.ewm(span=span, adjust=False).mean().iloc[-1])


def _below_ema(close: Optional[pd.Series], span: int) -> Optional[bool]:
    e = _ema(close, span)
    if e is None or close is None:
        return None
    s = close.dropna()
    if s.empty:
        return None
    return bool(s.iloc[-1] < e)


def _drawdown_pct(close: Optional[pd.Series], lookback: int = 10) -> Optional[float]:
    """Peak-to-last drawdown over the last `lookback` trading days (negative)."""
    if close is None:
        return None
    s = close.dropna()
    if len(s) < 2:
        return None
    window = s.iloc[-(lookback + 1):]
    peak = window.max()
    if peak == 0 or pd.isna(peak):
        return None
    return float((s.iloc[-1] / peak - 1.0) * 100.0)


# ── artifact readers (cache-only, degrade-safe) ──────────────────────────────
def _alpha_items() -> List[Dict[str, Any]]:
    d = dataio_read("alpha_discovery_board_latest.json")
    return (d or {}).get("items") or []


def dataio_read(name: str) -> Optional[Dict[str, Any]]:
    import json
    p = CACHE / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _glob_tickers_from(prefix: str, key_paths: Tuple[str, ...]) -> List[str]:
    """Best-effort: pull a list of tickers out of a research sidecar."""
    out: List[str] = []
    d = dataio_read(prefix)
    if not d:
        return out
    for kp in key_paths:
        node: Any = d
        for part in kp.split("."):
            if isinstance(node, dict):
                node = node.get(part)
            else:
                node = None
                break
        if isinstance(node, list):
            for row in node:
                if isinstance(row, dict):
                    t = row.get("ticker") or row.get("symbol")
                    if t:
                        out.append(str(t).upper())
                elif isinstance(row, str):
                    out.append(row.upper())
    return out


def gather_universe() -> Dict[str, List[str]]:
    """Ticker sets the audit examines for downside, by provenance."""
    alpha = [str(i.get("ticker")).upper() for i in _alpha_items() if i.get("ticker")]
    recall = _glob_tickers_from(
        "recall_repair_shadow_lane_latest.json",
        ("candidates", "leaders", "rows"),
    )
    power = _glob_tickers_from(
        "power_trend_extension_latest.json",
        ("cohort.rows", "cohort.names", "power_trend_names", "rows"),
    )
    avail = set(dataio.all_price_tickers())
    tech = [t for t in MAJOR_TECH if t in avail]
    return {
        "alpha_board": alpha,
        "recall_shadow": sorted(set(recall)),
        "power_trend": sorted(set(power)),
        "major_tech": tech,
    }


# ── Task 1: reconstruct the recent downside tape ─────────────────────────────
def pick_tech_proxy(available: Dict[str, bool]) -> str:
    """Tech/semis proxy symbol. Prefer SMH (semiconductors) when its parquet is
    cached; fall back to XLK (broad tech) only when SMH is missing. Phase 1G.16
    ran on XLK because SMH was never pre-warmed into cache/prices."""
    return "SMH" if available.get("SMH") else "XLK"


def reconstruct_tape(cal: pd.DatetimeIndex) -> Dict[str, Any]:
    closes = {t: _aligned_close(t, cal) for t in INDEX_ETFS}
    have = {t: (closes[t] is not None) for t in INDEX_ETFS}

    spy = closes.get("SPY")
    qqq = closes.get("QQQ")
    iwm = closes.get("IWM")
    # tech proxy: SMH (semis) when cached, else fall back to XLK (broad tech).
    tech_sym = pick_tech_proxy(have)
    tech = closes.get(tech_sym)
    xlk = closes.get("XLK")
    vxx = closes.get("VXX")

    etf_returns = {t: _returns_block(closes.get(t)) for t in INDEX_ETFS if have.get(t)}

    def rel(a: Optional[pd.Series], b: Optional[pd.Series], n: int) -> Optional[float]:
        ra, rb = _ret_pct(a, n), _ret_pct(b, n)
        if ra is None or rb is None:
            return None
        return round(ra - rb, 3)

    qqq_vs_spy = {f"rel_{n}d_pct": rel(qqq, spy, n) for n in HORIZONS}
    tech_vs_spy = {f"rel_{n}d_pct": rel(tech, spy, n) for n in HORIZONS}
    iwm_vs_spy = {f"rel_{n}d_pct": rel(iwm, spy, n) for n in HORIZONS}

    # breadth: how many sector ETFs are below EMA20 / negative 5d
    sector_states = []
    for s in SECTOR_ETFS:
        c = _aligned_close(s, cal)
        if c is None:
            continue
        sector_states.append({
            "etf": s,
            "ret_5d_pct": _ret_pct(c, 5),
            "below_ema20": _below_ema(c, 20),
            "below_ema50": _below_ema(c, 50),
        })
    n_sec = len(sector_states)
    n_below_ema20 = sum(1 for s in sector_states if s["below_ema20"])
    n_neg_5d = sum(1 for s in sector_states if (s["ret_5d_pct"] or 0) < 0)

    # classify the move
    spy5 = _ret_pct(spy, 5)
    qqq5 = _ret_pct(qqq, 5)
    iwm5 = _ret_pct(iwm, 5)
    tech5 = _ret_pct(tech, 5)
    classification, class_reason = _classify_move(spy5, qqq5, iwm5, tech5)

    return {
        "tech_proxy_symbol": tech_sym,
        "available": have,
        "etf_returns": etf_returns,
        "qqq_drawdown_10d_pct": _drawdown_pct(qqq, 10),
        "spy_drawdown_10d_pct": _drawdown_pct(spy, 10),
        "qqq_vs_spy": qqq_vs_spy,
        "tech_vs_spy": tech_vs_spy,
        "iwm_vs_spy": iwm_vs_spy,
        "vxx_3d_pct": _ret_pct(vxx, 3),
        "vxx_5d_pct": _ret_pct(vxx, 5),
        "breadth": {
            "n_sector_etfs": n_sec,
            "n_below_ema20": n_below_ema20,
            "frac_below_ema20": round(n_below_ema20 / n_sec, 3) if n_sec else None,
            "n_negative_5d": n_neg_5d,
            "frac_negative_5d": round(n_neg_5d / n_sec, 3) if n_sec else None,
            "rows": sector_states,
        },
        "move_classification": classification,
        "move_reason": class_reason,
        "last_bar": str(cal[-1].date()) if len(cal) else None,
    }


def _classify_move(spy5, qqq5, iwm5, tech5) -> Tuple[str, str]:
    """broad-market vs tech-specific vs isolated, from 5d ETF returns."""
    if spy5 is None or qqq5 is None:
        return ("UNKNOWN", "insufficient ETF history")
    broad = (spy5 <= -2.0) and (iwm5 is not None and iwm5 <= -2.0)
    tech_lag = (qqq5 - spy5) <= -1.5 or (tech5 is not None and (tech5 - spy5) <= -3.0)
    if broad and not tech_lag:
        return ("BROAD_MARKET", f"SPY {spy5:+.1f}% & IWM {iwm5:+.1f}% both weak, tech not leading down")
    if tech_lag and spy5 > -2.5:
        return ("TECH_SPECIFIC",
                f"QQQ/tech underperform SPY (QQQ−SPY 5d={qqq5 - spy5:+.1f}%) while SPY only {spy5:+.1f}%")
    if broad and tech_lag:
        return ("BROAD_MARKET_TECH_LED",
                f"broad weakness led by tech (QQQ−SPY 5d={qqq5 - spy5:+.1f}%)")
    if spy5 > -1.0 and qqq5 > -1.0:
        return ("ISOLATED", "indices roughly flat; any weakness is name-specific")
    return ("MIXED", f"SPY {spy5:+.1f}% / QQQ {qqq5:+.1f}% — no clean regime label")


# ── Task 2: what the Short Opportunity Radar said ────────────────────────────
def audit_radar() -> Dict[str, Any]:
    radar = dataio_read("short_opportunity_radar_latest.json")
    if not radar:
        return {
            "available": False,
            "note": "short_opportunity_radar_latest.json missing — cannot audit radar output",
        }
    inp = radar.get("inputs") or {}
    suppressed = bool(radar.get("suppressed_bull_tape"))
    vix = inp.get("vix")
    cands = (radar.get("candidates") or {}).get("total")
    # diagnostic answers
    suppressed_due_spy = bool(
        suppressed and inp.get("spy_above_ma50") and inp.get("spy_above_ma200")
    )
    vix_near_threshold = (vix is not None and TH["vix_benign"] - 2.0 <= vix < TH["vix_benign"])
    uses_qqq = any("qqq" in str(c.get("reason", "")).lower()
                   for c in (radar.get("score_components") or []))
    return {
        "available": True,
        "generated_at": radar.get("generated_at"),
        "state": radar.get("state"),
        "short_regime_score": radar.get("short_regime_score"),
        "suppressed_bull_tape": suppressed,
        "candidates_total": cands,
        "inputs": inp,
        "diagnosis": {
            "suppressed_because_spy_above_mas": suppressed_due_spy,
            "vix_just_under_threshold": vix_near_threshold,
            "score_uses_qqq_or_sector_rel": uses_qqq,
            "history_archive_available": False,
        },
        "history_note": (
            "The radar persists only a single _latest.json snapshot; there is no "
            "per-day archive, so 'what it said on each day of the drawdown' cannot "
            "be reconstructed historically. Phase 1G.16 begins a forward history "
            "spine (data/research/short_detection_history.jsonl) to close this gap."
        ),
    }


# ── Task 3: simple research-only baseline detectors ──────────────────────────
def baseline_detectors(tape: Dict[str, Any], leaders_breadth: Dict[str, Any]) -> Dict[str, Any]:
    er = tape["etf_returns"]
    qqq5 = (er.get("QQQ") or {}).get("ret_5d_pct")
    qqq3 = (er.get("QQQ") or {}).get("ret_3d_pct")
    qqq_rel5 = tape["qqq_vs_spy"].get("rel_5d_pct")
    tech_rel5 = tape["tech_vs_spy"].get("rel_5d_pct")
    vxx3 = tape.get("vxx_3d_pct")
    frac_above = leaders_breadth.get("frac_above_ema20")
    n_failed = leaders_breadth.get("n_failed_leaders", 0)

    def tier(v: Optional[float]) -> Optional[str]:
        if v is None:
            return None
        if v <= TH["qqq_vel_severe_pct"]:
            return "SEVERE"
        if v <= TH["qqq_vel_strong_pct"]:
            return "STRONG"
        if v <= TH["qqq_vel_warn_pct"]:
            return "WARN"
        return None

    A = {"name": "QQQ downside velocity", "qqq_5d_pct": qqq5, "qqq_3d_pct": qqq3,
         "tier": tier(qqq5), "triggered": tier(qqq5) is not None}
    B = {"name": "QQQ relative weakness vs SPY", "qqq_rel_spy_5d_pct": qqq_rel5,
         "triggered": qqq_rel5 is not None and qqq_rel5 <= TH["qqq_rel_spy_pct"]}
    C = {"name": "Tech/semis breakdown", "tech_rel_spy_5d_pct": tech_rel5,
         "frac_tech_above_ema20": frac_above,
         "triggered": (tech_rel5 is not None and tech_rel5 <= TH["tech_rel_spy_pct"])
                      and (frac_above is not None and frac_above < TH["tech_breadth_frac"])}
    D = {"name": "Momentum leader unwind", "n_failed_leaders": n_failed,
         "triggered": n_failed >= TH["failed_leaders_min"]}
    E = {"name": "VXX stress confirmation", "vxx_3d_pct": vxx3,
         "qqq_5d_pct": qqq5,
         "triggered": (vxx3 is not None and vxx3 >= TH["vxx_stress_3d_pct"])
                      and (qqq5 is not None and qqq5 < 0)}
    F = {"name": "Tech breadth deterioration", "frac_tech_above_ema20": frac_above,
         "triggered": frac_above is not None and frac_above < TH["tech_breadth_frac"]}

    detectors = {"A": A, "B": B, "C": C, "D": D, "E": E, "F": F}
    n_triggered = sum(1 for d in detectors.values() if d["triggered"])
    return {
        "detectors": detectors,
        "n_triggered": n_triggered,
        "any_triggered": n_triggered > 0,
        "summary": (
            f"{n_triggered}/6 simple baselines fired. These are research signals "
            f"only — no trades. Compare against radar state."
        ),
    }


# ── Task 4: candidate-level missed short autopsy ─────────────────────────────
def _alpha_lookup() -> Dict[str, Dict[str, Any]]:
    return {str(i.get("ticker")).upper(): i for i in _alpha_items() if i.get("ticker")}


def missed_short_autopsy(cal: pd.DatetimeIndex, universe: Dict[str, List[str]],
                         radar: Dict[str, Any]) -> Dict[str, Any]:
    alpha = _alpha_lookup()
    profiles = dataio.load_profiles()
    power_names = set(universe.get("power_trend") or [])
    radar_seen = set()
    rd = dataio_read("short_opportunity_radar_latest.json") or {}
    for rows in ((rd.get("candidates") or {}).get("by_archetype") or {}).values():
        for c in rows or []:
            if c.get("ticker"):
                radar_seen.add(str(c["ticker"]).upper())

    # universe of names to inspect = alpha + recall + power + major tech
    names: List[str] = []
    for k in ("alpha_board", "recall_shadow", "power_trend", "major_tech"):
        names.extend(universe.get(k) or [])
    names = sorted(set(names))

    rows: List[Dict[str, Any]] = []
    for t in names:
        c = _aligned_close(t, cal)
        r5 = _ret_pct(c, 5)
        if r5 is None or r5 > TH["sharp_drop_5d_pct"]:
            continue  # only names that actually dropped sharply
        r3, r10 = _ret_pct(c, 3), _ret_pct(c, 10)
        prof = profiles.get(t) or {}
        a = alpha.get(t) or {}
        theme = dataio.classify_theme(prof)
        below20 = _below_ema(c, 20)
        below50 = _below_ema(c, 50)
        qqq5 = (_ret_pct(_aligned_close("QQQ", cal), 5))
        # index-hedge test: move tracks the index (not idiosyncratic)
        index_like = qqq5 is not None and abs(r5 - qqq5) < 1.5
        archetype = _classify_candidate(
            ticker=t, r5=r5, below20=below20, below50=below50,
            alpha_item=a, in_power=t in power_names, index_like=index_like,
        )
        rows.append({
            "ticker": t,
            "ret_3d_pct": r3, "ret_5d_pct": r5, "ret_10d_pct": r10,
            "sector": prof.get("sector") or a.get("sector"),
            "theme": theme,
            "prior_bucket": a.get("bucket"),
            "prior_return_20d_pct": a.get("return_20d_pct"),
            "extension_state": a.get("entry_state") or a.get("validator_state"),
            "stock_lens_label": a.get("lens_label"),
            "gatekeeper_status": a.get("gatekeeper_status"),
            "below_ema20": below20, "below_ema50": below50,
            "radar_saw_it": t in radar_seen,
            "alpha_action_label": a.get("action_label"),
            "options_quality": a.get("options_quality"),
            "simple_baseline_would_catch": below20 is True and r5 <= TH["qqq_vel_strong_pct"],
            "archetype": archetype,
        })

    rows.sort(key=lambda x: (x["ret_5d_pct"] if x["ret_5d_pct"] is not None else 0))
    by_arch: Dict[str, int] = {}
    for r in rows:
        by_arch[r["archetype"]] = by_arch.get(r["archetype"], 0) + 1
    missed = [r for r in rows if not r["radar_saw_it"]
              and r["archetype"] not in (ARCH_NO_EDGE, ARCH_INDEX_HEDGE)]
    return {
        "n_examined": len(names),
        "n_sharp_drops": len(rows),
        "n_missed_by_radar": len(missed),
        "by_archetype": by_arch,
        "rows": rows[:40],
        "post_earnings_note": (
            "POST_EARNINGS_FAILED_REACTION needs an earnings-reaction/gap dataset "
            "not present in cached artifacts; not populated to avoid fabrication."
        ),
    }


def _classify_candidate(*, ticker: str, r5: float, below20: Optional[bool],
                        below50: Optional[bool], alpha_item: Dict[str, Any],
                        in_power: bool, index_like: bool) -> str:
    bucket = str(alpha_item.get("bucket") or "")
    oq = str(alpha_item.get("options_quality") or "")
    r20 = alpha_item.get("return_20d_pct")
    crowded = bucket == "Too Late / Crowded"
    prior_leader = (r20 is not None and r20 > 10) or crowded

    if crowded and oq == "BEARISH_HEDGE":
        return ARCH_CALL_CHASE_UNWIND
    if in_power and (below20 or below50) and r5 < 0:
        return ARCH_POWER_TREND_UNWIND
    if prior_leader and (below20 is True) and r5 <= TH["qqq_vel_warn_pct"]:
        return ARCH_FAILED_LEADER
    if index_like:
        return ARCH_INDEX_HEDGE
    if (below50 is True) and r5 <= TH["qqq_vel_strong_pct"]:
        return ARCH_REL_WEAKNESS
    if r5 > TH["sharp_drop_5d_pct"]:
        return ARCH_NO_EDGE
    return ARCH_NO_EDGE if (below20 is not True) else ARCH_REL_WEAKNESS


# ── Task 6 core: the proposed short-state classifier (pure + testable) ───────
def classify_short_state(
    *,
    spy_above_ma50: Optional[bool],
    spy_above_ma200: Optional[bool],
    vix: Optional[float],
    qqq_ret_5d: Optional[float],
    qqq_rel_spy_5d: Optional[float],
    qqq_below_ema20: Optional[bool],
    tech_rel_spy_5d: Optional[float],
    tech_breadth_frac_above_ema20: Optional[float],
    vxx_ret_3d: Optional[float],
    n_failed_leaders: int,
) -> Dict[str, Any]:
    """Proposed QQQ-aware short-side state. RESEARCH-ONLY. Never returns
    SHORT_REGIME_ACTIVE (that is validation-gated and out of scope here)."""
    reasons: List[str] = []

    bull = bool(spy_above_ma50) and bool(spy_above_ma200) and (vix is None or vix < TH["vix_benign"])

    qqq_breakdown = any([
        qqq_rel_spy_5d is not None and qqq_rel_spy_5d <= TH["qqq_rel_spy_pct"],
        qqq_below_ema20 is True,
        qqq_ret_5d is not None and qqq_ret_5d <= TH["qqq_vel_strong_pct"],
    ])
    tech_breakdown = (
        tech_rel_spy_5d is not None and tech_rel_spy_5d <= TH["tech_rel_spy_pct"]
        and tech_breadth_frac_above_ema20 is not None
        and tech_breadth_frac_above_ema20 < TH["tech_breadth_frac"]
    )
    vxx_stress = vxx_ret_3d is not None and vxx_ret_3d >= TH["vxx_stress_3d_pct"]
    failed_leaders = n_failed_leaders >= TH["failed_leaders_min"]

    if qqq_breakdown:
        reasons.append("QQQ tactical breakdown (rel-SPY / below EMA20 / 5d velocity)")
    if tech_breakdown:
        reasons.append("tech/semis breakdown (XLK/SMH rel-SPY + weak breadth)")
    if vxx_stress:
        reasons.append(f"VXX 3d {vxx_ret_3d:+.1f}% — volatility stress")
    if failed_leaders:
        reasons.append(f"{n_failed_leaders} prior leaders breaking")

    if qqq_breakdown and (tech_breakdown or (vxx_stress and failed_leaders)):
        state = STATE_TACTICAL_SHORT_RESEARCH
    elif failed_leaders and (qqq_breakdown or tech_breakdown):
        state = STATE_FAILED_LEADER_WATCH
    elif qqq_breakdown or vxx_stress or not bull:
        state = STATE_HEDGE_WATCH
        if bull and not reasons:
            reasons.append("early QQQ/vol softening under a still-bull SPY tape")
        if not bull:
            reasons.append("SPY itself below its trend — broad hedge research")
    else:
        state = STATE_SHORTS_OFF
        reasons.append("broad tape constructive; QQQ/tech healthy")

    return {
        "state": state,
        "reasons": reasons,
        "flags": {
            "bull_tape": bull,
            "qqq_breakdown": qqq_breakdown,
            "tech_breakdown": tech_breakdown,
            "vxx_stress": vxx_stress,
            "failed_leaders": failed_leaders,
        },
    }


# ── Task 5: diagnose the SPY-only suppression rule ───────────────────────────
def diagnose_suppression(tape: Dict[str, Any], radar: Dict[str, Any],
                         proposed: Dict[str, Any], autopsy: Dict[str, Any],
                         *, spy_above_ma50: Optional[bool] = None,
                         spy_above_ma200: Optional[bool] = None) -> Dict[str, Any]:
    current_suppressed = bool(radar.get("suppressed_bull_tape")) if radar.get("available") else None
    current_state = radar.get("state") if radar.get("available") else None
    proposed_state = proposed["state"]

    # The SPY bull-tape rule keys on SPY structure + VIX<20. Its only escape
    # hatch is VIX crossing 20 — a LAGGING signal that fires after price has
    # already broken. So "too broad" must be judged on SPY's *structural* bull
    # state (above 50d & 200d), not on whether suppression happens to be active
    # this instant (which flips with intraday VIX noise around the 20 line).
    spy_bull_structure = bool(spy_above_ma50) and bool(spy_above_ma200)
    vix = (radar.get("inputs") or {}).get("vix") if radar.get("available") else None
    vix_escape_hatch = (
        f"radar un-suppresses only once VIX>{TH['vix_benign']} (now {vix}); during the "
        f"breakdown VIX sat just under the line, so price weakness was hidden until "
        f"volatility caught up — a lagging trigger."
    )

    missed = autopsy.get("n_missed_by_radar", 0)
    # false-positive risk: the alternative is multi-condition (QQQ AND tech/vol),
    # so a single noisy day should not flip it. Qualitative estimate.
    flags = proposed["flags"]
    if proposed_state == STATE_SHORTS_OFF:
        fp_risk = "n/a (proposed also says SHORTS_OFF)"
    elif proposed_state in (STATE_TACTICAL_SHORT_RESEARCH, STATE_FAILED_LEADER_WATCH):
        fp_risk = "LOW–MEDIUM: requires QQQ breakdown AND (tech breakdown or vol+leaders)"
    else:
        fp_risk = "MEDIUM: single-leg (QQQ softening or VXX) can set HEDGE_WATCH; research-only so cost is low"

    # Too broad if SPY is structurally bull (so the SPY rule's suppression branch
    # governs) while the QQQ-aware proposal sees a real breakdown.
    too_broad = bool(spy_bull_structure and proposed_state != STATE_SHORTS_OFF)
    rec: List[str] = []
    if too_broad:
        rec = [
            "keep the SPY bull-tape rule as the BROAD-regime gate (do not delete it)",
            "add QQQ tactical warning (rel-SPY + EMA20 + 5d velocity)",
            "add sector-breakdown short watch (XLK/SMH rel-SPY + tech breadth)",
            "add hedge-only mode (HEDGE_WATCH) so QQQ weakness is visible without single-name shorts",
            "add failed-leader research mode (FAILED_LEADER_WATCH)",
        ]
    else:
        rec = ["keep rule — proposed QQQ-aware logic agrees with current state today"]

    return {
        "current_rule": "bull tape: SPY>50d & >200d, VIX<20 → short regime suppressed",
        "current_rule_result": {"suppressed": current_suppressed, "state": current_state},
        "alternative_rule": (
            "QQQ-aware: SPY bull is the broad gate, but QQQ/tech tactical breakdown "
            "(rel-SPY, EMA20, velocity, tech breadth) or VXX stress escalates to "
            "HEDGE_WATCH / FAILED_LEADER_WATCH / TACTICAL_SHORT_RESEARCH"
        ),
        "alternative_rule_result": {"state": proposed_state, "flags": flags},
        "spy_bull_structure": spy_bull_structure,
        "currently_suppressed": current_suppressed,
        "vix_escape_hatch_note": vix_escape_hatch,
        "rule_too_broad": too_broad,
        "missed_detection_count": missed,
        "false_positive_risk": fp_risk,
        "recommended_change": rec,
    }


# ── leaders breadth (for baselines + classifier) ─────────────────────────────
def leaders_breadth(cal: pd.DatetimeIndex, universe: Dict[str, List[str]]) -> Dict[str, Any]:
    tech = universe.get("major_tech") or []
    above20 = 0
    counted = 0
    failed = 0
    rows = []
    alpha = _alpha_lookup()
    for t in tech:
        c = _aligned_close(t, cal)
        b20 = _below_ema(c, 20)
        if b20 is None:
            continue
        counted += 1
        above20 += 0 if b20 else 1
        r5 = _ret_pct(c, 5)
        r20 = (alpha.get(t) or {}).get("return_20d_pct")
        was_leader = r20 is not None and r20 > 10
        if (was_leader or t in (universe.get("power_trend") or [])) and b20 and (r5 or 0) < 0:
            failed += 1
        rows.append({"ticker": t, "below_ema20": b20, "ret_5d_pct": r5})
    return {
        "n_tech_leaders": counted,
        "n_above_ema20": above20,
        "frac_above_ema20": round(above20 / counted, 3) if counted else None,
        "n_failed_leaders": failed,
        "rows": rows,
    }


# ── Task 7 spine: append today's snapshot to the forward history ─────────────
def history_row(asof: str, tape: Dict[str, Any], proposed: Dict[str, Any],
                autopsy: Dict[str, Any]) -> Dict[str, Any]:
    """One immutable daily snapshot for the forward validator. Stores entry
    closes for the missed-short candidates so forward returns can be scored."""
    cands = []
    for r in autopsy.get("rows", []):
        if r["archetype"] in (ARCH_NO_EDGE,):
            continue
        cands.append({
            "ticker": r["ticker"],
            "archetype": r["archetype"],
            "ret_5d_pct_at_detection": r["ret_5d_pct"],
        })
    return {
        "asof_date": asof,
        "version": VERSION,
        "proposed_state": proposed["state"],
        "qqq_drawdown_10d_pct": tape.get("qqq_drawdown_10d_pct"),
        "move_classification": tape.get("move_classification"),
        "n_candidates": len(cands),
        "candidates": cands,
    }


# ── orchestration ────────────────────────────────────────────────────────────
def build_audit(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cal = dataio.benchmark_calendar()

    universe = gather_universe()
    tape = reconstruct_tape(cal)
    radar = audit_radar()
    breadth = leaders_breadth(cal, universe)
    baselines = baseline_detectors(tape, breadth)
    autopsy = missed_short_autopsy(cal, universe, radar)

    er = tape["etf_returns"]
    proposed = classify_short_state(
        spy_above_ma50=_spy_flag(cal, 50),
        spy_above_ma200=_spy_flag(cal, 200),
        vix=(radar.get("inputs") or {}).get("vix") if radar.get("available") else None,
        qqq_ret_5d=(er.get("QQQ") or {}).get("ret_5d_pct"),
        qqq_rel_spy_5d=tape["qqq_vs_spy"].get("rel_5d_pct"),
        qqq_below_ema20=_below_ema(_aligned_close("QQQ", cal), 20),
        tech_rel_spy_5d=tape["tech_vs_spy"].get("rel_5d_pct"),
        tech_breadth_frac_above_ema20=breadth.get("frac_above_ema20"),
        vxx_ret_3d=tape.get("vxx_3d_pct"),
        n_failed_leaders=breadth.get("n_failed_leaders", 0),
    )
    suppression = diagnose_suppression(
        tape, radar, proposed, autopsy,
        spy_above_ma50=_spy_flag(cal, 50), spy_above_ma200=_spy_flag(cal, 200),
    )

    redesign_justified = _redesign_verdict(suppression, autopsy, proposed)

    return {
        "kind": "short_detection_truth_audit",
        "version": VERSION,
        "generated_at": now.isoformat(),
        "research_only": True,
        "short_a_status": "FROZEN / RESEARCH ONLY (2026-05-24) — unchanged by this audit",
        "asof_date": tape.get("last_bar"),
        "universe_counts": {k: len(v) for k, v in universe.items()},
        "tape": tape,
        "radar_audit": radar,
        "leaders_breadth": breadth,
        "baselines": baselines,
        "missed_short_autopsy": autopsy,
        "proposed_state": proposed,
        "proposed_states_catalogue": _states_catalogue(),
        "suppression_diagnosis": suppression,
        "short_redesign_justified": redesign_justified,
        "disclaimer": (
            "Research-only. No paper signals, no trade proposals, no execution / "
            "governance / live-capital / gate / Veto Council changes. SHORT_A "
            "stays frozen. Proposed states are research labels, not trade triggers."
        ),
    }


def _spy_flag(cal: pd.DatetimeIndex, span: int) -> Optional[bool]:
    c = _aligned_close("SPY", cal)
    bel = _below_ema(c, span)
    return None if bel is None else (not bel)


def _states_catalogue() -> List[Dict[str, str]]:
    return [
        {"state": STATE_SHORTS_OFF, "meaning": "Broad tape constructive; no short research except alerts."},
        {"state": STATE_HEDGE_WATCH, "meaning": "SPY still okay but QQQ/tech/semis weakening; index-hedge research only, no stock shorts."},
        {"state": STATE_FAILED_LEADER_WATCH, "meaning": "Prior winners breaking; research-only short candidates allowed."},
        {"state": STATE_TACTICAL_SHORT_RESEARCH, "meaning": "QQQ/sector breakdown confirmed; generate research candidates only."},
        {"state": STATE_SHORT_REGIME_ACTIVE, "meaning": "Only after backtested validation; NOT active now and never auto-set."},
    ]


def _redesign_verdict(suppression: Dict[str, Any], autopsy: Dict[str, Any],
                      proposed: Dict[str, Any]) -> Dict[str, Any]:
    """Is a short *detection* redesign justified? (NOT a SHORT_A unfreeze.)"""
    too_broad = suppression.get("rule_too_broad")
    missed = autopsy.get("n_missed_by_radar", 0)
    if too_broad and missed >= 3:
        verdict = "DETECTION_GAP_CONFIRMED"
        detail = (
            f"Radar suppressed shorts on SPY bull tape while {missed} sharply-falling "
            f"names went unseen and proposed state is {proposed['state']}. A QQQ-aware "
            f"detection layer is justified for RESEARCH — forward validation required "
            f"before any sleeve work."
        )
    elif too_broad:
        verdict = "DETECTION_GAP_LIKELY"
        detail = ("Suppression looks too broad vs the proposed QQQ-aware state, but few "
                  "missed candidates yet — accumulate forward history before concluding.")
    else:
        verdict = "NO_GAP_TODAY"
        detail = "Current radar state agrees with the QQQ-aware proposal today."
    return {
        "verdict": verdict,
        "detail": detail,
        "note": "Concerns DETECTION only. SHORT_A remains frozen; no active short sleeve proposed.",
    }


# ── rendering ────────────────────────────────────────────────────────────────
def render_text(a: Dict[str, Any]) -> str:
    L: List[str] = []
    er = a["tape"]["etf_returns"]
    L.append("=" * 70)
    L.append(f"SHORT DETECTION TRUTH AUDIT — {a['generated_at'][:19]}  (research-only)")
    L.append("=" * 70)
    L.append(f"SHORT_A: {a['short_a_status']}")
    L.append(f"as-of bar: {a['asof_date']}   tech proxy: {a['tape']['tech_proxy_symbol']}")
    L.append("")
    L.append("TASK 1 — recent tape (close-to-close % returns):")
    L.append(f"  {'ETF':5} {'1d':>7} {'3d':>7} {'5d':>7} {'10d':>7}")
    for t in INDEX_ETFS:
        b = er.get(t)
        if not b:
            continue
        def f(n):
            v = b.get(f"ret_{n}d_pct")
            return f"{v:+6.2f}" if v is not None else "   n/a"
        L.append(f"  {t:5} {f(1):>7} {f(3):>7} {f(5):>7} {f(10):>7}")
    L.append(f"  QQQ drawdown(10d)={a['tape']['qqq_drawdown_10d_pct']}  "
             f"SPY drawdown(10d)={a['tape']['spy_drawdown_10d_pct']}")
    L.append(f"  QQQ−SPY 5d rel={a['tape']['qqq_vs_spy'].get('rel_5d_pct')}  "
             f"TECH−SPY 5d rel={a['tape']['tech_vs_spy'].get('rel_5d_pct')}  "
             f"VXX 3d={a['tape'].get('vxx_3d_pct')}")
    br = a["tape"]["breadth"]
    L.append(f"  sector breadth: {br['n_below_ema20']}/{br['n_sector_etfs']} below EMA20, "
             f"{br['n_negative_5d']}/{br['n_sector_etfs']} negative 5d")
    L.append(f"  MOVE: {a['tape']['move_classification']} — {a['tape']['move_reason']}")
    L.append("")
    L.append("TASK 2 — what the Short Opportunity Radar said:")
    r = a["radar_audit"]
    if r.get("available"):
        L.append(f"  state={r['state']} score={r['short_regime_score']}/100 "
                 f"suppressed_bull_tape={r['suppressed_bull_tape']} candidates={r['candidates_total']}")
        dg = r["diagnosis"]
        L.append(f"  suppressed because SPY>MAs: {dg['suppressed_because_spy_above_mas']}; "
                 f"VIX just under {TH['vix_benign']}: {dg['vix_just_under_threshold']}; "
                 f"score uses QQQ/sector rel: {dg['score_uses_qqq_or_sector_rel']}")
    else:
        L.append(f"  {r.get('note')}")
    L.append("")
    L.append("TASK 3 — simple baseline detectors:")
    for k, d in a["baselines"]["detectors"].items():
        L.append(f"  [{k}] {d['name']:30} triggered={d['triggered']}")
    L.append(f"  → {a['baselines']['summary']}")
    L.append("")
    L.append("TASK 4 — missed-short autopsy:")
    ms = a["missed_short_autopsy"]
    L.append(f"  examined={ms['n_examined']} sharp_drops={ms['n_sharp_drops']} "
             f"missed_by_radar={ms['n_missed_by_radar']}  by_archetype={ms['by_archetype']}")
    for row in ms["rows"][:12]:
        L.append(f"    - {row['ticker']:6} 5d={row['ret_5d_pct']:+6.2f}% "
                 f"[{row['archetype']}] theme={row['theme']} radar_saw={row['radar_saw_it']}")
    L.append("")
    L.append("TASK 5 — suppression diagnosis:")
    sd = a["suppression_diagnosis"]
    L.append(f"  current: {sd['current_rule_result']}")
    L.append(f"  proposed: {sd['alternative_rule_result']}")
    L.append(f"  rule_too_broad={sd['rule_too_broad']} missed={sd['missed_detection_count']} "
             f"fp_risk={sd['false_positive_risk']}")
    for rec in sd["recommended_change"]:
        L.append(f"    · {rec}")
    L.append("")
    L.append("TASK 6 — proposed state TODAY:")
    L.append(f"  STATE = {a['proposed_state']['state']}")
    for why in a["proposed_state"]["reasons"]:
        L.append(f"    - {why}")
    L.append("")
    L.append(f"REDESIGN VERDICT: {a['short_redesign_justified']['verdict']}")
    L.append(f"  {a['short_redesign_justified']['detail']}")
    L.append(f"  {a['short_redesign_justified']['note']}")
    L.append("=" * 70)
    return "\n".join(L)


def render_doc(a: Dict[str, Any]) -> str:
    sd = a["suppression_diagnosis"]
    ms = a["missed_short_autopsy"]
    L: List[str] = []
    L.append("# Short Detection Truth Audit — QQQ Breakdown / Tactical Short Regime Review")
    L.append("")
    L.append(f"_Phase 1G.16 · research-only · generated {a['generated_at'][:19]}Z · "
             f"as-of bar {a['asof_date']}_")
    L.append("")
    L.append("> **Scope guard.** This audits the *short-detection layer* only. "
             "`SHORT_A` stays **FROZEN** (it failed structurally). No paper signals, "
             "no trade proposals, no execution/governance/live-capital/gate/Veto-Council "
             "changes, no new active short strategy. Proposed states are research labels.")
    L.append("")
    L.append("## 1. Recent downside tape")
    L.append("")
    L.append("| ETF | 1d | 3d | 5d | 10d |")
    L.append("|-----|----|----|----|-----|")
    for t in INDEX_ETFS:
        b = a["tape"]["etf_returns"].get(t)
        if not b:
            continue
        def f(n):
            v = b.get(f"ret_{n}d_pct")
            return f"{v:+.2f}%" if v is not None else "n/a"
        L.append(f"| {t} | {f(1)} | {f(3)} | {f(5)} | {f(10)} |")
    L.append("")
    L.append(f"- **Move classification:** `{a['tape']['move_classification']}` — "
             f"{a['tape']['move_reason']}")
    L.append(f"- QQQ−SPY 5d relative: **{a['tape']['qqq_vs_spy'].get('rel_5d_pct')}%**; "
             f"tech−SPY 5d: **{a['tape']['tech_vs_spy'].get('rel_5d_pct')}%**; "
             f"VXX 3d: **{a['tape'].get('vxx_3d_pct')}%**")
    L.append("")
    L.append("## 2. What the Short Opportunity Radar said")
    r = a["radar_audit"]
    if r.get("available"):
        L.append(f"- state **{r['state']}**, score {r['short_regime_score']}/100, "
                 f"suppressed_bull_tape={r['suppressed_bull_tape']}, candidates={r['candidates_total']}")
        L.append(f"- suppressed because SPY>50d&200d: **{r['diagnosis']['suppressed_because_spy_above_mas']}**; "
                 f"VIX just under {TH['vix_benign']}: {r['diagnosis']['vix_just_under_threshold']}; "
                 f"score uses QQQ/sector relative weakness: **{r['diagnosis']['score_uses_qqq_or_sector_rel']}**")
        L.append(f"- {r['history_note']}")
    else:
        L.append(f"- {r.get('note')}")
    L.append("")
    L.append("## 3. Simple baseline detectors")
    L.append("")
    L.append("| # | detector | triggered |")
    L.append("|---|----------|-----------|")
    for k, d in a["baselines"]["detectors"].items():
        L.append(f"| {k} | {d['name']} | {d['triggered']} |")
    L.append("")
    L.append(f"_{a['baselines']['summary']}_")
    L.append("")
    L.append("## 4. Missed-short autopsy")
    L.append(f"- examined **{ms['n_examined']}**, sharp 5d drops **{ms['n_sharp_drops']}**, "
             f"missed by radar **{ms['n_missed_by_radar']}**; by archetype: `{ms['by_archetype']}`")
    L.append("")
    if ms["rows"]:
        L.append("| ticker | 5d | archetype | theme | radar saw |")
        L.append("|--------|----|-----------|-------|-----------|")
        for row in ms["rows"][:20]:
            L.append(f"| {row['ticker']} | {row['ret_5d_pct']:+.1f}% | {row['archetype']} | "
                     f"{row['theme']} | {row['radar_saw_it']} |")
    L.append("")
    L.append("## 5. Suppression-rule diagnosis")
    L.append(f"- **Current rule:** `{sd['current_rule']}` → {sd['current_rule_result']}")
    L.append(f"- **Alternative (QQQ-aware):** {sd['alternative_rule']} → {sd['alternative_rule_result']}")
    L.append(f"- **Rule too broad:** {sd['rule_too_broad']} · missed={sd['missed_detection_count']} · "
             f"false-positive risk: {sd['false_positive_risk']}")
    L.append("- **Recommended (not implemented):**")
    for rec in sd["recommended_change"]:
        L.append(f"  - {rec}")
    L.append("")
    L.append("## 6. Proposed short-side research states")
    for s in a["proposed_states_catalogue"]:
        L.append(f"- **{s['state']}** — {s['meaning']}")
    L.append("")
    L.append(f"**State today:** `{a['proposed_state']['state']}` — "
             + "; ".join(a["proposed_state"]["reasons"]))
    L.append("")
    L.append("## 7–8. Redesign verdict / surfacing")
    L.append(f"- **Verdict:** `{a['short_redesign_justified']['verdict']}` — "
             f"{a['short_redesign_justified']['detail']}")
    L.append(f"- {a['short_redesign_justified']['note']}")
    L.append("- Forward validation: `./scripts/run_research_cycle.sh short-detection-forward` "
             "(verdict ladder NEED_MORE_DATA → NO_VALUE → SHORT_DETECTION_EDGE → "
             "READY_FOR_SHORT_REDESIGN_RESEARCH).")
    L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Short Detection Truth Audit (research-only)")
    p.add_argument("--print", dest="do_print", action="store_true")
    p.add_argument("--no-historize", action="store_true",
                   help="skip appending today's snapshot to the forward history spine")
    args = p.parse_args(argv)

    audit = build_audit()

    dataio.write_json(JSON_OUT, audit)
    text = render_text(audit)
    dataio.write_text(TXT_OUT, text.split("\n"))
    DOC_OUT.parent.mkdir(parents=True, exist_ok=True)
    DOC_OUT.write_text(render_doc(audit) + "\n")

    historized = 0
    if not args.no_historize and audit.get("asof_date"):
        existing = {row.get("asof_date") for row in dataio.read_jsonl(HISTORY)}
        if audit["asof_date"] not in existing:  # idempotent per trading day
            row = history_row(audit["asof_date"], audit["tape"],
                              audit["proposed_state"], audit["missed_short_autopsy"])
            historized = dataio.append_jsonl(HISTORY, [row])

    if args.do_print:
        print(text)
    else:
        print(f"short_detection_truth_audit: move={audit['tape']['move_classification']} "
              f"proposed_state={audit['proposed_state']['state']} "
              f"verdict={audit['short_redesign_justified']['verdict']} "
              f"(historized {historized})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
