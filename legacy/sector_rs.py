from typing import Any, Dict, Optional

from alpaca_data import AlpacaDataFeed
from sector_resolver import resolve_sector_etf


def _safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a / b - 1.0) * 100.0


def _linear_slope(y):
    n = len(y)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(y) / n
    num = sum((i - x_mean) * (y[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return (num / den) if den else 0.0


def _sma_series(arr, length):
    if len(arr) < length:
        return []
    out = []
    for i in range(length - 1, len(arr)):
        out.append(sum(arr[i - length + 1 : i + 1]) / length)
    return out


def compute_rs_confidence(gap_pct: float, rs_slope: float, resolver_conf: float) -> float:
    if gap_pct is None or rs_slope is None:
        return 0.0
    gap_term = _clamp01(abs(gap_pct) / 10.0)
    slope_term = _clamp01(abs(rs_slope) / 0.01)
    map_term = _clamp01(resolver_conf if resolver_conf is not None else 0.0)
    conf = (0.55 * gap_term) + (0.30 * slope_term) + (0.15 * map_term)
    conf = conf * map_term
    return float(round(_clamp01(conf), 3))


def calc_rs(
    ticker: str,
    sector_etf: str,
    bars_ticker,
    bars_sector,
    resolver_confidence: float = 1.0,
    source: str = "manual",
    notes: str = "",
    ma_len: int = 10,
    slope_len: int = 10,
) -> Dict[str, Any]:
    t_close = [_safe_float(b.get("close")) for b in bars_ticker]
    e_close = [_safe_float(b.get("close")) for b in bars_sector]
    t_close = [x for x in t_close if x is not None]
    e_close = [x for x in e_close if x is not None]

    n = min(len(t_close), len(e_close))
    if n < (ma_len + 2):
        return {
            "ok": False,
            "ticker": ticker,
            "sector_etf": sector_etf,
            "resolver_confidence": resolver_confidence,
            "source": source,
            "notes": notes,
            "verdict": "NEUTRAL",
            "confidence": 0.0,
            "reason": "insufficient_aligned_closes",
        }

    t_close = t_close[-n:]
    e_close = e_close[-n:]
    rs_line = [(t_close[i] / e_close[i]) if e_close[i] else None for i in range(n)]
    rs_line = [x for x in rs_line if x is not None]

    if len(rs_line) < (ma_len + 2):
        return {
            "ok": False,
            "ticker": ticker,
            "sector_etf": sector_etf,
            "resolver_confidence": resolver_confidence,
            "source": source,
            "notes": notes,
            "verdict": "NEUTRAL",
            "confidence": 0.0,
            "reason": "rs_line_too_short",
        }

    rs_ma = _sma_series(rs_line, ma_len)
    rs_ratio = _safe_float(rs_line[-1])
    rs_ma10 = _safe_float(rs_ma[-1] if rs_ma else rs_ratio)
    slope_series = rs_ma[-slope_len:] if len(rs_ma) >= slope_len else rs_ma
    rs_slope = _safe_float(_linear_slope(slope_series) if slope_series else 0.0)

    gap_pct = None
    if rs_ratio is not None and rs_ma10 not in (None, 0):
        gap_pct = 100.0 * (rs_ratio / rs_ma10 - 1.0)

    slope_strength = (rs_slope / rs_ma10) if (rs_slope is not None and rs_ma10 not in (None, 0)) else 0.0
    conf = compute_rs_confidence(gap_pct, rs_slope, resolver_confidence)

    verdict = "NEUTRAL"
    reason = "mixed_rs"
    if rs_ratio is not None and rs_ma10 is not None:
        if rs_ratio > rs_ma10 and rs_slope is not None and rs_slope > 0:
            verdict, reason = "TAILWIND", "rs>ma10_and_slope_up"
        elif rs_ratio < rs_ma10 and rs_slope is not None and rs_slope < 0:
            verdict, reason = "HEADWIND", "rs<ma10_and_slope_down"

    rs = {
        "ok": True,
        "ticker": ticker,
        "sector_etf": sector_etf,
        "resolver_confidence": resolver_confidence,
        "source": source,
        "notes": notes,
        "rs_ratio": rs_ratio,
        "rs_ma10": rs_ma10,
        "rs_slope": rs_slope,
        "gap_pct": gap_pct,
        "slope_strength": slope_strength,
        "verdict": verdict,
        "confidence": conf,
        "reason": reason,
    }

    required = ["rs_ratio", "rs_ma10", "rs_slope", "verdict", "confidence"]
    missing = [k for k in required if rs.get(k) is None]

    def _is_num(x):
        try:
            return x is not None and float(x) == float(x)
        except Exception:
            return False

    if (not _is_num(rs.get("rs_ratio"))) or (not _is_num(rs.get("rs_ma10"))):
        return {
            "ok": False,
            "ticker": rs.get("ticker", ticker),
            "sector_etf": rs.get("sector_etf", sector_etf),
            "resolver_confidence": rs.get("resolver_confidence", 0.0),
            "source": rs.get("source", "unknown"),
            "verdict": "NEUTRAL",
            "confidence": 0.0,
            "reason": "rs_schema_invalid_missing_ratio_or_ma10",
            "schema_missing": missing,
            "raw": rs,
        }
    return rs


class SectorRS:
    def __init__(self, data_feed: Optional[AlpacaDataFeed] = None):
        self.feed = data_feed or AlpacaDataFeed()

    def _compute_with_etf(
        self,
        ticker: str,
        sector_etf: str,
        resolver_confidence: float,
        source: str,
        notes: str = "",
        lookback_days: int = 30,
        ma_len: int = 10,
        slope_len: int = 10,
    ) -> Dict[str, Any]:
        days = max(lookback_days, ma_len + slope_len + 5)
        t_bars = self.feed.get_daily_bars(ticker, days_back=days)
        e_bars = self.feed.get_daily_bars(sector_etf, days_back=days)

        if not t_bars or not e_bars or len(t_bars) < (ma_len + 2) or len(e_bars) < (ma_len + 2):
            return {
                "ok": False,
                "ticker": ticker,
                "sector_etf": sector_etf,
                "resolver_confidence": resolver_confidence,
                "source": source,
                "notes": notes,
                "verdict": "NEUTRAL",
                "confidence": 0.0,
                "reason": "insufficient_bars",
            }
        return calc_rs(
            ticker=ticker,
            sector_etf=sector_etf,
            bars_ticker=t_bars,
            bars_sector=e_bars,
            resolver_confidence=resolver_confidence,
            source=source,
            notes=notes,
            ma_len=ma_len,
            slope_len=slope_len,
        )

    def compute(
        self,
        ticker: str,
        lookback_days: int = 30,
        ma_len: int = 10,
        slope_len: int = 10,
    ) -> Dict[str, Any]:
        res = resolve_sector_etf(ticker)
        if not res.get("ok"):
            return {
                "ok": False,
                "ticker": res.get("ticker", (ticker or "").upper()),
                "sector_etf": res.get("sector_etf"),
                "resolver_confidence": float(res.get("resolver_confidence", 0.0) or 0.0),
                "source": res.get("source", "unknown"),
                "verdict": "NEUTRAL",
                "confidence": 0.0,
                "reason": res.get("reason", "missing_mapping"),
            }
        return self._compute_with_etf(
            ticker=res.get("ticker", (ticker or "").upper()),
            sector_etf=res.get("sector_etf"),
            resolver_confidence=float(res.get("resolver_confidence", 0.0) or 0.0),
            source=res.get("source", "unknown"),
            notes=res.get("notes", ""),
            lookback_days=lookback_days,
            ma_len=ma_len,
            slope_len=slope_len,
        )


# Backward-compatible adapter for existing imports.
class SectorRelativeStrength:
    def __init__(self, data_feed: Optional[AlpacaDataFeed] = None):
        self.engine = SectorRS(data_feed=data_feed)

    def compute(self, ticker: str, sector_etf: str, lookback=60, rs_ma=10, slope_n=10) -> Dict[str, Any]:
        return self.engine._compute_with_etf(
            ticker=ticker,
            sector_etf=sector_etf,
            resolver_confidence=1.0,
            source="manual",
            notes="explicit_etf",
            lookback_days=lookback,
            ma_len=rs_ma,
            slope_len=slope_n,
        )
