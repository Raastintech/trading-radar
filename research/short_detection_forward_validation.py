#!/usr/bin/env python3
"""
research/short_detection_forward_validation.py — Phase 1G.16 (Task 7)

Forward validator for the Short Detection Truth Audit candidates. RESEARCH-ONLY.

Reads the append-only history spine written by short_detection_truth_audit.py
(data/research/short_detection_history.jsonl), and for each historized short
candidate scores the FORWARD outcome from cached prices:

  - forward return at 1d / 3d / 5d / 10d / 20d
  - short-side return = negative of the stock's forward return
  - rel-SPY / rel-QQQ (did the short beat just shorting the index?)
  - MFE / MAE for the short (max favorable / adverse excursion)
  - gap risk (largest adverse overnight gap during the window)
  - whether shorting QQQ (index hedge) would have been better than the single name

Verdict ladder (no trades, no paper signals, no sleeve activation):
  NEED_MORE_DATA → NO_VALUE → SHORT_DETECTION_EDGE → READY_FOR_SHORT_REDESIGN_RESEARCH

This NEVER unfreezes SHORT_A and never proposes an active short sleeve. A passing
verdict only authorizes further RESEARCH (a backtested redesign), not promotion.

Cache-only: prices from cache/prices, history from data/research; no providers,
no DB writes, no governance/execution/live-capital touch.
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
from research.short_detection_truth_audit import HISTORY, VERSION as AUDIT_VERSION  # noqa: E402

CACHE = dataio.RESEARCH_CACHE
JSON_OUT = CACHE / "short_detection_forward_latest.json"
TXT_OUT = dataio.LOGS_DIR / "short_detection_forward_latest.txt"

VERSION = "SHORT_DETECTION_FORWARD_V1"

HORIZONS = (1, 3, 5, 10, 20)
PRIMARY_HORIZON = 5

# Promotion gates (conservative; research authorization only).
MIN_HISTORY_DAYS = 10        # distinct trading days in the spine
MIN_MATURED_PRIMARY = 20     # candidate-horizon observations matured at PRIMARY
EDGE_MIN_MEAN_SHORT = 1.0    # mean short-side return % to call an edge
EDGE_MIN_WIN_RATE = 0.55     # short win rate
EDGE_MIN_VS_INDEX = 0.5      # short must beat index-short by this margin (pp)


def _aligned_close(ticker: str, cal: pd.DatetimeIndex) -> Optional[pd.Series]:
    df = dataio.load_prices(ticker)
    if df is None or "close" not in df.columns:
        return None
    return df["close"].reindex(cal).ffill()


def _pos_of(cal: pd.DatetimeIndex, asof: str) -> Optional[int]:
    try:
        ts = pd.Timestamp(asof)
    except Exception:
        return None
    locs = cal[cal <= ts]
    if len(locs) == 0:
        return None
    return cal.get_loc(locs[-1])


def _fwd_short_return(close: Optional[pd.Series], i0: int, h: int) -> Optional[float]:
    """Short-side forward return %: negative of the long return entry→entry+h."""
    if close is None or i0 is None:
        return None
    if i0 + h >= len(close):
        return None
    a, b = close.iloc[i0], close.iloc[i0 + h]
    if pd.isna(a) or pd.isna(b) or a == 0:
        return None
    return float(-(b / a - 1.0) * 100.0)


def _mfe_mae_short(close: Optional[pd.Series], i0: int, h: int) -> Tuple[Optional[float], Optional[float]]:
    """For a short held i0→i0+h: MFE = max favorable (price falls), MAE = max
    adverse (price rises). Returned as positive %s."""
    if close is None or i0 is None or i0 + h >= len(close):
        return (None, None)
    window = close.iloc[i0: i0 + h + 1].dropna()
    if len(window) < 2:
        return (None, None)
    entry = window.iloc[0]
    if entry == 0:
        return (None, None)
    lo, hi = window.min(), window.max()
    mfe = float((entry - lo) / entry * 100.0)   # favorable for short
    mae = float((hi - entry) / entry * 100.0)   # adverse for short
    return (mfe, mae)


def _max_adverse_gap(close: Optional[pd.Series], i0: int, h: int) -> Optional[float]:
    """Largest adverse overnight gap (close-to-close up move) during the window."""
    if close is None or i0 is None or i0 + h >= len(close):
        return None
    window = close.iloc[i0: i0 + h + 1].dropna()
    if len(window) < 2:
        return None
    rets = window.pct_change().dropna() * 100.0
    if rets.empty:
        return None
    return float(rets.max())  # most positive day = worst for a short


def build(history_path: Path = HISTORY) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = dataio.read_jsonl(history_path)
    if not rows:
        return _empty_result(now, "no history spine yet — run short_detection_truth_audit first")

    cal = dataio.benchmark_calendar()
    spy = _aligned_close("SPY", cal)
    qqq = _aligned_close("QQQ", cal)

    history_days = len({r.get("asof_date") for r in rows if r.get("asof_date")})

    per_horizon: Dict[int, List[Dict[str, Any]]] = {h: [] for h in HORIZONS}
    n_candidate_rows = 0

    for snap in rows:
        asof = snap.get("asof_date")
        i0 = _pos_of(cal, asof) if asof else None
        if i0 is None:
            continue
        for cand in snap.get("candidates") or []:
            t = str(cand.get("ticker") or "").upper()
            if not t:
                continue
            n_candidate_rows += 1
            c = _aligned_close(t, cal)
            for h in HORIZONS:
                sr = _fwd_short_return(c, i0, h)
                if sr is None:
                    continue
                spy_sr = _fwd_short_return(spy, i0, h)
                qqq_sr = _fwd_short_return(qqq, i0, h)
                mfe, mae = _mfe_mae_short(c, i0, h)
                per_horizon[h].append({
                    "asof": asof, "ticker": t, "archetype": cand.get("archetype"),
                    "short_return_pct": sr,
                    "rel_spy_pct": (sr - spy_sr) if spy_sr is not None else None,
                    "rel_qqq_pct": (sr - qqq_sr) if qqq_sr is not None else None,
                    "index_hedge_better": (qqq_sr is not None and qqq_sr > sr),
                    "mfe_pct": mfe, "mae_pct": mae,
                    "max_adverse_gap_pct": _max_adverse_gap(c, i0, h),
                })

    by_horizon = {h: _summarize(per_horizon[h]) for h in HORIZONS}
    matured_primary = by_horizon[PRIMARY_HORIZON]["n"]
    verdict, verdict_reason = _verdict(by_horizon, history_days, matured_primary)

    return {
        "kind": "short_detection_forward_validation",
        "version": VERSION,
        "audit_version": AUDIT_VERSION,
        "generated_at": now.isoformat(),
        "research_only": True,
        "short_a_status": "FROZEN / RESEARCH ONLY — unchanged",
        "history_days": history_days,
        "n_history_rows": len(rows),
        "n_candidate_rows": n_candidate_rows,
        "primary_horizon": PRIMARY_HORIZON,
        "decision_gates": {
            "min_history_days": MIN_HISTORY_DAYS,
            "history_days_met": history_days >= MIN_HISTORY_DAYS,
            "min_matured_primary": MIN_MATURED_PRIMARY,
            "matured_primary_met": matured_primary >= MIN_MATURED_PRIMARY,
        },
        "by_horizon": by_horizon,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "ladder": "NEED_MORE_DATA → NO_VALUE → SHORT_DETECTION_EDGE → READY_FOR_SHORT_REDESIGN_RESEARCH",
        "disclaimer": (
            "Research-only forward scoring. No paper trades, no trade proposals, no "
            "sleeve activation. A passing verdict authorizes a backtested redesign "
            "study only — SHORT_A stays frozen."
        ),
    }


def _summarize(obs: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(obs)
    if n == 0:
        return {"n": 0}
    sr = [o["short_return_pct"] for o in obs if o["short_return_pct"] is not None]
    relq = [o["rel_qqq_pct"] for o in obs if o["rel_qqq_pct"] is not None]
    wins = sum(1 for v in sr if v > 0)
    idx_better = sum(1 for o in obs if o.get("index_hedge_better"))
    mae = [o["mae_pct"] for o in obs if o["mae_pct"] is not None]
    return {
        "n": n,
        "mean_short_return_pct": round(sum(sr) / len(sr), 3) if sr else None,
        "win_rate": round(wins / len(sr), 3) if sr else None,
        "mean_rel_qqq_pct": round(sum(relq) / len(relq), 3) if relq else None,
        "index_hedge_better_frac": round(idx_better / n, 3),
        "mean_mae_pct": round(sum(mae) / len(mae), 3) if mae else None,
    }


def _verdict(by_horizon: Dict[int, Dict[str, Any]], history_days: int,
            matured_primary: int) -> Tuple[str, str]:
    if history_days < MIN_HISTORY_DAYS or matured_primary < MIN_MATURED_PRIMARY:
        return ("NEED_MORE_DATA",
                f"history_days={history_days} (<{MIN_HISTORY_DAYS}) or matured "
                f"primary obs={matured_primary} (<{MIN_MATURED_PRIMARY})")
    p = by_horizon[PRIMARY_HORIZON]
    mean_sr = p.get("mean_short_return_pct")
    wr = p.get("win_rate")
    rel_q = p.get("mean_rel_qqq_pct")
    if mean_sr is None:
        return ("NEED_MORE_DATA", "no matured short returns at primary horizon")
    beats_index = rel_q is not None and rel_q >= EDGE_MIN_VS_INDEX
    if mean_sr >= EDGE_MIN_MEAN_SHORT and (wr or 0) >= EDGE_MIN_WIN_RATE and beats_index:
        return ("READY_FOR_SHORT_REDESIGN_RESEARCH",
                f"mean short {mean_sr:+.2f}% / win {wr:.0%} / beats QQQ-short by "
                f"{rel_q:+.2f}pp at {PRIMARY_HORIZON}d — authorize backtested redesign research")
    if mean_sr >= EDGE_MIN_MEAN_SHORT and (wr or 0) >= EDGE_MIN_WIN_RATE:
        return ("SHORT_DETECTION_EDGE",
                f"mean short {mean_sr:+.2f}% / win {wr:.0%} at {PRIMARY_HORIZON}d, but does "
                f"not clearly beat a QQQ index short (rel {rel_q})")
    return ("NO_VALUE",
            f"mean short {mean_sr:+.2f}% / win {wr} at {PRIMARY_HORIZON}d — no detectable short edge")


def _empty_result(now: datetime, reason: str) -> Dict[str, Any]:
    return {
        "kind": "short_detection_forward_validation",
        "version": VERSION,
        "generated_at": now.isoformat(),
        "research_only": True,
        "history_days": 0,
        "n_candidate_rows": 0,
        "by_horizon": {h: {"n": 0} for h in HORIZONS},
        "verdict": "NEED_MORE_DATA",
        "verdict_reason": reason,
        "short_a_status": "FROZEN / RESEARCH ONLY — unchanged",
    }


def render_text(r: Dict[str, Any]) -> List[str]:
    L = ["=" * 64,
         f"SHORT DETECTION FORWARD VALIDATION — {r['generated_at'][:19]} (research-only)",
         "=" * 64,
         f"history_days={r['history_days']} candidate_rows={r.get('n_candidate_rows')} "
         f"primary={r.get('primary_horizon')}d"]
    g = r.get("decision_gates") or {}
    if g:
        L.append(f"GATES: history≥{g['min_history_days']}={g['history_days_met']} "
                 f"matured≥{g['min_matured_primary']}={g['matured_primary_met']}")
    L.append("")
    for h in HORIZONS:
        s = r["by_horizon"].get(h, {})
        if s.get("n"):
            L.append(f"  {h:>2}d: n={s['n']:<4} mean_short={s.get('mean_short_return_pct')}% "
                     f"win={s.get('win_rate')} rel_qqq={s.get('mean_rel_qqq_pct')}pp "
                     f"idx_better={s.get('index_hedge_better_frac')} mae={s.get('mean_mae_pct')}%")
        else:
            L.append(f"  {h:>2}d: n=0 (not matured)")
    L.append("")
    L.append(f"VERDICT: {r['verdict']}")
    L.append(f"  {r['verdict_reason']}")
    L.append(f"  ladder: {r.get('ladder', '')}")
    L.append("=" * 64)
    return L


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Short Detection Forward Validation (research-only)")
    p.add_argument("--print", dest="do_print", action="store_true")
    args = p.parse_args(argv)

    r = build()
    dataio.write_json(JSON_OUT, r)
    lines = render_text(r)
    dataio.write_text(TXT_OUT, lines)
    if args.do_print:
        print("\n".join(lines))
    else:
        print(f"short_detection_forward: verdict {r['verdict']} "
              f"(history_days={r['history_days']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
