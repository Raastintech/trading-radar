"""
research/scanner_truth/filter_audit.py — TASK 7 (filter audit).

For each major funnel filter, count — at a representative point-in-time entry
date (the same as-of as the baseline backtest) — how many FORWARD winners it
rejects vs how many FORWARD non-winners (losers) it rejects. A filter that
rejects a large share of winners while sparing losers is killing recall; one
that rejects mostly losers is buying precision.

Reliably-computable filters only (pure price/liquidity/market-cap). Filters
that need point-in-time fundamentals/earnings/options or breakout context are
listed with status NOT_RELIABLY_COMPUTABLE rather than guessed.

  recall_cost   = winners_rejected / total_winners      (higher = worse for recall)
  loser_rejection = losers_rejected / total_losers       (higher = better for precision)

Output:
  cache/research/scanner_filter_audit_latest.json
  logs/scanner_filter_audit_latest.txt
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from . import dataio
from .baselines import HORIZON, WINNER_FWD, _aligned
from .filters import (ALPHA_MCAP_CEILING, ALPHA_MCAP_FLOOR, SNI_BARS_NEEDED,
                      UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL, UNIV_MIN_AVG_VOL,
                      UNIV_MIN_PRICE, VOY_BARS_NEEDED, VOY_MA200_FLOOR,
                      VOY_MAX_EXTENSION_MA50, sma)


def _features(horizon: int = HORIZON) -> Dict:
    calendar = dataio.benchmark_calendar()
    asof_i = len(calendar) - horizon - 1
    asof = calendar[asof_i]
    profiles = dataio.load_profiles()
    rows: List[Dict] = []
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = dataio.load_prices(t)
        if df is None:
            continue
        c = _aligned(df, calendar)
        if pd.isna(c.iloc[asof_i]):
            continue
        bars = int((df.index <= asof).sum())     # bars our cache holds at as-of
        price = float(c.iloc[asof_i])
        vol = df["volume"].reindex(calendar).ffill()
        dvol = (c * vol)
        avgvol = float(vol.iloc[max(0, asof_i - 19):asof_i + 1].mean())
        avgdvol = float(dvol.iloc[max(0, asof_i - 19):asof_i + 1].mean())
        ma50 = float(sma(c.iloc[:asof_i + 1], 50).iloc[-1]) if asof_i >= 50 else np.nan
        ma200 = float(sma(c.iloc[:asof_i + 1], 200).iloc[-1]) if asof_i >= 200 else np.nan
        ext = (price - ma50) / ma50 if ma50 and not np.isnan(ma50) else np.nan
        dist200 = price / ma200 if ma200 and not np.isnan(ma200) else np.nan
        fwd_seg = c.iloc[asof_i:]
        fwd_max = float(np.nanmax(fwd_seg.values) / price - 1.0) if price else None
        rows.append({
            "ticker": t, "price": price, "bars": bars,
            "avgvol": avgvol, "avgdvol": avgdvol, "ext_ma50": ext, "dist200": dist200,
            "market_cap": (profiles.get(t) or {}).get("market_cap"),
            "is_winner": (fwd_max or 0) >= WINNER_FWD,
        })
    return {"asof": str(asof)[:10], "rows": rows}


def _audit_filter(rows: List[Dict], name: str, purpose: str, threshold: str,
                  rejects: Callable[[Dict], Optional[bool]], *,
                  reliable: bool = True, design_exclusion: bool = False,
                  note: str = "") -> Dict:
    winners = [r for r in rows if r["is_winner"]]
    losers = [r for r in rows if not r["is_winner"]]
    # A filter "rejects" a row when rejects(r) is True; None ⇒ indeterminate (skip).
    wr = [r for r in winners if rejects(r) is True]
    lr = [r for r in losers if rejects(r) is True]
    indet = sum(1 for r in rows if rejects(r) is None)
    nW, nL = len(winners), len(losers)
    recall_cost = round(100.0 * len(wr) / nW, 1) if nW else None
    loser_rej = round(100.0 * len(lr) / nL, 1) if nL else None
    # verdict heuristic
    if not reliable:
        verdict = ("INDETERMINATE — cache depth at as-of (<260/75 bars available) "
                   "confounds this; live scanner pulls full history from Alpaca. "
                   "Not a scanner conclusion.")
    elif design_exclusion and recall_cost is not None and recall_cost >= 25:
        verdict = ("BY-DESIGN exclusion — high recall cost reflects penny/illiquid/"
                   "off-band winners the system intentionally avoids; trading them "
                   "carries real risk. Keep, but quantify the opportunity given up.")
    elif recall_cost is None:
        verdict = "indeterminate"
    elif recall_cost >= 40 and (loser_rej or 0) < recall_cost:
        verdict = "SOFTEN / regime-adaptive — rejects winners faster than losers"
    elif recall_cost >= 25:
        verdict = "REVIEW — meaningful recall cost"
    else:
        verdict = "KEEP — low recall cost"
    return {
        "filter": name, "purpose": purpose, "threshold": threshold,
        "reliable": reliable, "note": note,
        "winners_rejected": len(wr), "losers_rejected": len(lr),
        "winners_total": nW, "losers_total": nL, "indeterminate": indet,
        "recall_cost_pct": recall_cost, "loser_rejection_pct": loser_rej,
        "verdict": verdict,
        "sample_winners_rejected": [r["ticker"] for r in wr[:15]],
    }


def build(horizon: int = HORIZON) -> Dict:
    feat = _features(horizon)
    rows = feat["rows"]

    def ext_reject(r):
        return None if (r["ext_ma50"] is None or (isinstance(r["ext_ma50"], float) and np.isnan(r["ext_ma50"]))) \
            else r["ext_ma50"] > VOY_MAX_EXTENSION_MA50

    def ma200_reject(r):
        return None if (r["dist200"] is None or (isinstance(r["dist200"], float) and np.isnan(r["dist200"]))) \
            else r["dist200"] < VOY_MA200_FLOOR

    def mcap_reject(r):
        mc = r["market_cap"]
        return None if not mc else (mc < ALPHA_MCAP_FLOOR or mc > ALPHA_MCAP_CEILING)

    audits = [
        _audit_filter(rows, "liquidity_price", "exclude penny stocks",
                      f"price∈[${UNIV_MIN_PRICE:.0f},${UNIV_MAX_PRICE:.0f}]",
                      lambda r: not (UNIV_MIN_PRICE <= r["price"] <= UNIV_MAX_PRICE),
                      design_exclusion=True,
                      note="many explosive winners are sub-$5 — excluded by design"),
        _audit_filter(rows, "liquidity_dvol", "institutional liquidity floor",
                      f"avg$vol≥${UNIV_MIN_AVG_DVOL/1e6:.0f}M & vol≥{UNIV_MIN_AVG_VOL/1e3:.0f}k",
                      lambda r: r["avgdvol"] < UNIV_MIN_AVG_DVOL or r["avgvol"] < UNIV_MIN_AVG_VOL,
                      design_exclusion=True,
                      note="illiquid names excluded by design; tradability vs recall tradeoff"),
        _audit_filter(rows, "voyager_max_extension_ma50",
                      "reject names too far above MA50 (buyable-pullback mandate)",
                      f">{VOY_MAX_EXTENSION_MA50*100:.0f}% above MA50 → reject", ext_reject,
                      note="STATIC as-of recall cost understates the DYNAMIC effect: "
                           "names become extended AS they run and are rejected then — "
                           "see entry-timing audit (Task 8)"),
        _audit_filter(rows, "voyager_ma200_floor", "reject broken downtrends",
                      f"price < MA200×{VOY_MA200_FLOOR} → reject", ma200_reject),
        _audit_filter(rows, "voyager_bars_needed_260", "require 52wk history (MA200/RS130)",
                      f"<{VOY_BARS_NEEDED} bars → reject",
                      lambda r: r["bars"] < VOY_BARS_NEEDED, reliable=False,
                      note="cache depth confounds at as-of; not attributable to scanner"),
        _audit_filter(rows, "sniper_bars_needed_75", "require 75 bars (MA50 + slope)",
                      f"<{SNI_BARS_NEEDED} bars → reject",
                      lambda r: r["bars"] < SNI_BARS_NEEDED, reliable=False,
                      note="cache depth confounds at as-of; not attributable to scanner"),
        _audit_filter(rows, "alpha_market_cap_band", "exclude micro/mega caps; favour mid",
                      f"mcap∉[${ALPHA_MCAP_FLOOR/1e6:.0f}M,${ALPHA_MCAP_CEILING/1e9:.0f}B]", mcap_reject),
    ]
    not_computable = [
        {"filter": f, "status": "NOT_RELIABLY_COMPUTABLE", "reason": reason}
        for f, reason in [
            ("voyager_rs_130 / fundamental_score", "needs 130-bar RS (cache-limited) + point-in-time fundamentals"),
            ("voyager_dvol_trend_ratio", "computable but entry-context dependent; evaluated in funnel_trace"),
            ("sniper_vol_spike_1.4x / atr_contraction_0.85", "only meaningful on a breakout bar, not a static as-of"),
            ("earnings_safe_days", "needs point-in-time earnings calendar (not historized)"),
            ("options_liquidity / 13F_sponsorship", "needs point-in-time options/13F (not historized)"),
            ("top_25_board_cap", "needs historized ranked boards (only ~6 days exist)"),
        ]
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asof_date": feat["asof"],
        "n_universe": len(rows),
        "n_forward_winners": sum(1 for r in rows if r["is_winner"]),
        "audits": audits,
        "not_reliably_computable": not_computable,
    }


def _render_txt(res: Dict) -> List[str]:
    L = [
        f"== FILTER AUDIT ({res['generated_at']}) ==",
        f"as-of {res['asof_date']}  universe={res['n_universe']}  "
        f"forward winners={res['n_forward_winners']}",
        "",
        f"{'filter':<30}{'wRej':>6}{'lRej':>6}{'recallcost':>11}{'loserRej':>9}  verdict",
    ]
    for a in res["audits"]:
        L.append(f"{a['filter']:<30}{a['winners_rejected']:>6}{a['losers_rejected']:>6}"
                 f"{(a['recall_cost_pct'] or 0):>10.1f}%{(a['loser_rejection_pct'] or 0):>8.1f}%  "
                 f"{a['verdict']}")
    L.append("")
    L.append("NOT_RELIABLY_COMPUTABLE (disclosed, not guessed):")
    for n in res["not_reliably_computable"]:
        L.append(f"  - {n['filter']}: {n['reason']}")
    return L


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "scanner_filter_audit_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "scanner_filter_audit_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
