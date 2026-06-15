"""
research/scanner_truth/winner_universe.py — TASK 1.

Builds an OBJECTIVE point-in-time "market winner" universe from local OHLCV
parquet, not memory. Windows are measured in SPY trading days. For each
window we compute end-return and intra-window max-return, relative strength
vs SPY/QQQ, volume expansion, liquidity, sector/theme, and market cap.

This is an AUDIT set: the winner list must NOT be used to retroactively tune
thresholds. It defines "what actually ran" so we can ask whether the funnel
saw it.

Outputs:
  cache/research/missed_winner_universe_latest.json
  logs/missed_winner_universe_latest.txt
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import dataio
from .filters import UNIV_MIN_AVG_DVOL, UNIV_MIN_PRICE

WINDOWS = [20, 40, 60, 90]            # trading days
WINNER_THRESHOLDS = [0.50, 0.80, 1.00]  # +50%, +80%, +100% (2x)
# Liquidity floor for the primary studied set (mirrors the universe gate so the
# winner set is comparable to what the system *could* trade). Penny/illiquid
# names are excluded from the primary set but counted + studied separately.
PRIMARY_MIN_PRICE = UNIV_MIN_PRICE          # $5
PRIMARY_MIN_AVG_DVOL = UNIV_MIN_AVG_DVOL    # $5M/day


def _aligned_close(df: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.Series:
    """Reindex a ticker's close to the SPY calendar (forward-filled within its
    listed range). Bars before the ticker's first listing stay NaN so a recent
    IPO does not get a fabricated early price."""
    c = df["close"].reindex(calendar)
    first_valid = c.first_valid_index()
    if first_valid is not None:
        c.loc[c.index >= first_valid] = c.loc[c.index >= first_valid].ffill()
    return c


def _window_stats(close: pd.Series, dvol: pd.Series, win: int) -> Optional[Dict]:
    if len(close) <= win:
        return None
    seg = close.iloc[-(win + 1):]
    start = seg.iloc[0]
    end = seg.iloc[-1]
    if pd.isna(start) or pd.isna(end) or start <= 0:
        return None
    peak = float(np.nanmax(seg.values))
    return {
        "start_price": round(float(start), 4),
        "end_price": round(float(end), 4),
        "window_return": round(float(end / start - 1.0), 4),
        "max_return": round(float(peak / start - 1.0), 4),
        "bars_in_window": int(seg.notna().sum()),
    }


def build(*, min_window_return: float = 0.50) -> Dict:
    """Construct the winner universe. ``min_window_return`` is the floor for a
    name to be listed (default +50% in at least one window)."""
    calendar = dataio.benchmark_calendar()
    profiles = dataio.load_profiles()
    spy_close = _aligned_close(dataio.load_prices("SPY"), calendar)
    qqq_close = _aligned_close(dataio.load_prices("QQQ"), calendar)

    spy_ret = {w: float(spy_close.iloc[-1] / spy_close.iloc[-(w + 1)] - 1.0)
               for w in WINDOWS if len(spy_close) > w}
    qqq_ret = {w: float(qqq_close.iloc[-1] / qqq_close.iloc[-(w + 1)] - 1.0)
               for w in WINDOWS if len(qqq_close) > w}

    winners: List[Dict] = []
    illiquid_winners: List[Dict] = []
    scanned = 0
    for ticker in dataio.all_price_tickers():
        if ticker in dataio.BENCHMARKS:
            continue
        df = dataio.load_prices(ticker)
        if df is None or len(df) < 21:
            continue
        scanned += 1
        close = _aligned_close(df, calendar)
        dvol = (df["close"] * df["volume"]).reindex(calendar).ffill()

        per_window = {}
        for w in WINDOWS:
            st = _window_stats(close, dvol, w)
            if st:
                per_window[str(w)] = st
        if not per_window:
            continue
        best_max = max((v["max_return"] for v in per_window.values()), default=0.0)
        best_end = max((v["window_return"] for v in per_window.values()), default=0.0)
        if best_max < min_window_return:
            continue

        # Liquidity (as-of latest bar).
        end_price = float(close.iloc[-1]) if not pd.isna(close.iloc[-1]) else 0.0
        avg_dvol20 = float(dvol.tail(20).mean())
        avg_vol20 = float(df["volume"].tail(20).mean())
        # Volume expansion: recent 20d avg vol vs prior 20d avg vol.
        v = df["volume"]
        vol_exp = (float(v.tail(20).mean()) / float(v.iloc[-40:-20].mean())
                   if len(v) >= 40 and v.iloc[-40:-20].mean() else None)

        prof = profiles.get(ticker)
        # Relative strength vs benchmarks over the longest available window.
        rs = {}
        for w in WINDOWS:
            if str(w) in per_window and w in spy_ret:
                rs[f"rs_spy_{w}d"] = round(per_window[str(w)]["window_return"] - spy_ret[w], 4)
            if str(w) in per_window and w in qqq_ret:
                rs[f"rs_qqq_{w}d"] = round(per_window[str(w)]["window_return"] - qqq_ret[w], 4)

        rec = {
            "ticker": ticker,
            "sector": (prof or {}).get("sector") or "UNKNOWN",
            "industry": (prof or {}).get("industry"),
            "theme": dataio.classify_theme(prof),
            "market_cap": (prof or {}).get("market_cap"),
            "end_price": round(end_price, 4),
            "best_max_return": best_max,
            "best_window_return": best_end,
            "returns": {w: per_window[w]["window_return"] for w in per_window},
            "max_returns": {w: per_window[w]["max_return"] for w in per_window},
            "start_prices": {w: per_window[w]["start_price"] for w in per_window},
            "avg_dvol_20": round(avg_dvol20, 0),
            "avg_vol_20": round(avg_vol20, 0),
            "volume_expansion": round(vol_exp, 3) if vol_exp else None,
            "relative_strength": rs,
            "hit_thresholds": {f"+{int(t*100)}%": best_max >= t for t in WINNER_THRESHOLDS},
            "liquid": end_price >= PRIMARY_MIN_PRICE and avg_dvol20 >= PRIMARY_MIN_AVG_DVOL,
        }
        (winners if rec["liquid"] else illiquid_winners).append(rec)

    winners.sort(key=lambda r: r["best_max_return"], reverse=True)
    illiquid_winners.sort(key=lambda r: r["best_max_return"], reverse=True)

    # Top-percentile buckets among LIQUID winners by best_max_return.
    n_liq = len(winners)
    pct_buckets = {}
    for pct in (1, 3, 5):
        k = max(1, int(round(n_liq * pct / 100.0)))
        pct_buckets[f"top_{pct}pct"] = [w["ticker"] for w in winners[:k]]

    by_theme: Dict[str, int] = {}
    by_sector: Dict[str, int] = {}
    for w in winners:
        by_theme[w["theme"]] = by_theme.get(w["theme"], 0) + 1
        by_sector[w["sector"]] = by_sector.get(w["sector"], 0) + 1

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "calendar_end": str(calendar[-1])[:10],
        "windows": WINDOWS,
        "winner_floor": min_window_return,
        "liquidity_floor": {"min_price": PRIMARY_MIN_PRICE, "min_avg_dvol_20": PRIMARY_MIN_AVG_DVOL},
        "benchmark_returns": {"spy": spy_ret, "qqq": qqq_ret},
        "coverage": {
            "tickers_scanned": scanned,
            "profile_coverage": sum(1 for w in winners if w["sector"] != "UNKNOWN"),
        },
        "counts": {
            "liquid_winners": n_liq,
            "illiquid_winners_excluded": len(illiquid_winners),
            "ge_50pct": sum(1 for w in winners if w["best_max_return"] >= 0.50),
            "ge_80pct": sum(1 for w in winners if w["best_max_return"] >= 0.80),
            "ge_100pct": sum(1 for w in winners if w["best_max_return"] >= 1.00),
        },
        "percentile_buckets": pct_buckets,
        "by_theme": dict(sorted(by_theme.items(), key=lambda x: -x[1])),
        "by_sector": dict(sorted(by_sector.items(), key=lambda x: -x[1])),
        "winners": winners,
        "illiquid_winners": illiquid_winners,
    }
    return result


def _render_txt(res: Dict) -> List[str]:
    L = [
        f"== MISSED-WINNER UNIVERSE ({res['generated_at']}) ==",
        f"calendar end: {res['calendar_end']}  windows(td): {res['windows']}",
        f"scanned: {res['coverage']['tickers_scanned']} tickers  "
        f"liquidity floor: price≥${res['liquidity_floor']['min_price']:.0f} "
        f"avg$vol≥${res['liquidity_floor']['min_avg_dvol_20']/1e6:.0f}M",
        "",
        f"liquid winners (≥+50% any window): {res['counts']['liquid_winners']}  "
        f"(≥+80%: {res['counts']['ge_80pct']}, ≥+100%/2x: {res['counts']['ge_100pct']})",
        f"illiquid winners excluded (studied separately): {res['counts']['illiquid_winners_excluded']}",
        "",
        "by theme: " + ", ".join(f"{k}={v}" for k, v in res["by_theme"].items()),
        "",
        f"TOP 20 LIQUID WINNERS by max return:",
        f"{'ticker':<8}{'theme':<20}{'maxret':>8}{'endret':>8}{'$vol(M)':>9}  sector",
    ]
    for w in res["winners"][:20]:
        L.append(
            f"{w['ticker']:<8}{w['theme']:<20}{w['best_max_return']*100:>7.0f}%"
            f"{w['best_window_return']*100:>7.0f}%{(w['avg_dvol_20'] or 0)/1e6:>8.1f}  {w['sector']}"
        )
    return L


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "missed_winner_universe_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "missed_winner_universe_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
