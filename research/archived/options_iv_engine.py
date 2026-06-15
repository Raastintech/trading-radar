from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

# yfinance made optional — FMP Ultimate will provide options chains
try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    yf = None  # type: ignore
    _HAS_YFINANCE = False

from options_intelligence import OptionsIntelligence
from options_logger import OptionsLogger

logger = logging.getLogger(__name__)

try:
    from tradier_options_feed import tradier_feed
except Exception:  # pragma: no cover - import guard
    tradier_feed = None  # type: ignore


def _safe_float(value, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


class _DefaultIVProvider:
    def __init__(self):
        self.options_intelligence = OptionsIntelligence()

    @property
    def source_name(self) -> str:
        try:
            if tradier_feed and tradier_feed.is_configured():
                return "TRADIER"
        except Exception:
            pass
        return "YFINANCE"

    def get_current_price(self, ticker: str) -> Optional[float]:
        return self.options_intelligence.get_current_price(ticker)

    def get_expirations(self, ticker: str) -> List[str]:
        ticker = str(ticker or "").upper().strip()
        if not ticker:
            return []
        try:
            if tradier_feed and tradier_feed.is_configured():
                return list(tradier_feed.get_expirations(ticker))
        except Exception:
            pass
        try:
            return list(yf.Ticker(ticker).options or [])
        except Exception:
            return []

    def get_chain(self, ticker: str, expiry: str):
        return self.options_intelligence._get_chain(ticker, expiry)


class OptionsIVEngine:
    """Daily IV snapshot persistence and rank/percentile context."""

    def __init__(self, db_path: str = "trading_performance.db", provider=None, logger_store: Optional[OptionsLogger] = None):
        self.provider = provider or _DefaultIVProvider()
        self.logger_store = logger_store or OptionsLogger(db_path=db_path)

    def get_iv_context(self, ticker: str) -> Optional[Dict]:
        ticker = str(ticker or "").upper().strip()
        if not ticker:
            return None

        iv_pair, used_expiry = self._get_current_atm_iv(ticker)
        if iv_pair is None:
            return None
        put_iv, call_iv = iv_pair
        # Canonical ATM IV used for historical rank/percentile: average of both sides
        atm_iv = (put_iv + call_iv) / 2.0

        today = date.today().isoformat()
        history = self.logger_store.load_iv_series(ticker, limit=252)
        values = [float(row["atm_iv"]) for row in reversed(history) if _safe_float(row["atm_iv"], None) is not None]
        if not values or today not in {str(row["date"]) for row in history}:
            values.append(float(atm_iv))
        iv_rank_30d = self._calc_iv_rank(values[-30:], atm_iv)
        iv_rank_252d = self._calc_iv_rank(values[-252:], atm_iv)
        iv_pct_252d = self._calc_iv_percentile(values[-252:], atm_iv)
        # Regime based on put IV for selling accuracy (puts always carry higher IV)
        iv_regime = self._classify_regime(iv_rank_252d, iv_rank_30d, put_iv)

        self.logger_store.upsert_iv_snapshot(
            ticker=ticker,
            date_str=today,
            atm_iv=float(atm_iv),
            iv_rank_30d=iv_rank_30d,
            iv_rank_252d=iv_rank_252d,
            iv_pct_252d=iv_pct_252d,
            source=getattr(self.provider, "source_name", "UNKNOWN"),
        )

        return {
            "ticker": ticker,
            "atm_iv": float(atm_iv),
            "put_iv": float(put_iv),   # for put spreads / CSP — higher due to skew
            "call_iv": float(call_iv), # for call spreads / CC
            "expiry": used_expiry,
            "iv_rank_30d": iv_rank_30d,
            "iv_rank_252d": iv_rank_252d,
            "iv_pct_252d": iv_pct_252d,
            "iv_regime": iv_regime,
            "source": getattr(self.provider, "source_name", "UNKNOWN"),
        }

    def _get_current_atm_iv(self, ticker: str) -> Tuple[Optional[float], Optional[str]]:
        expirations = self.provider.get_expirations(ticker)
        price = _safe_float(self.provider.get_current_price(ticker), None)
        if not expirations or price is None or price <= 0:
            return None, None

        ranked = []
        for expiry in expirations:
            try:
                exp_date = date.fromisoformat(str(expiry))
            except Exception:
                continue
            dte = (exp_date - date.today()).days
            if dte < 14 or dte > 75:
                continue
            ranked.append((abs(dte - 35), dte, str(expiry)))
        if not ranked:
            return None, None
        _, _, target_expiry = sorted(ranked)[0]
        chain = self.provider.get_chain(ticker, target_expiry)
        if not chain:
            return None, target_expiry

        put_iv: Optional[float] = None
        call_iv: Optional[float] = None
        for option_type in ("calls", "puts"):
            frame = chain.get(option_type)
            if frame is None or getattr(frame, "empty", True):
                continue
            rows = frame.copy()
            try:
                rows["strike"] = rows["strike"].astype(float)
                rows["dist"] = (rows["strike"] - float(price)).abs()
                rows = rows.sort_values("dist")
                row = rows.iloc[0]
                iv = _safe_float(row.get("impliedVolatility"), None)
                if iv is not None and iv > 0:
                    if option_type == "puts":
                        put_iv = float(iv)
                    else:
                        call_iv = float(iv)
            except Exception:
                continue
        # Use put IV for put spreads, call IV for call spreads.
        # Fall back to the other leg or average only when one side is missing.
        if put_iv is None and call_iv is None:
            return None, target_expiry
        if put_iv is None:
            put_iv = call_iv
        if call_iv is None:
            call_iv = put_iv
        # Store as tuple: (put_iv, call_iv, expiry) — caller unpacks
        return (put_iv, call_iv), target_expiry

    @staticmethod
    def _calc_iv_rank(values: List[float], current: float) -> Optional[float]:
        clean = [float(v) for v in values if _safe_float(v, None) is not None]
        if not clean:
            return None
        lo = min(clean)
        hi = max(clean)
        if hi <= lo:
            return 50.0
        return round(((float(current) - lo) / (hi - lo)) * 100.0, 1)

    @staticmethod
    def _calc_iv_percentile(values: List[float], current: float) -> Optional[float]:
        clean = [float(v) for v in values if _safe_float(v, None) is not None]
        if not clean:
            return None
        count = sum(1 for v in clean if v <= float(current))
        return round((count / max(len(clean), 1)) * 100.0, 1)

    @staticmethod
    def _classify_regime(iv_rank_252d: Optional[float], iv_rank_30d: Optional[float], atm_iv: float) -> str:
        ref = iv_rank_252d if iv_rank_252d is not None else iv_rank_30d
        if ref is None:
            if atm_iv >= 0.45:
                return "RICH"
            if atm_iv >= 0.25:
                return "FAIR"
            return "CHEAP"
        if ref >= 50.0:
            return "RICH"
        if ref >= 30.0:
            return "FAIR"
        return "CHEAP"
