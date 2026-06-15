from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

# yfinance made optional — FMP Ultimate will provide options chains
try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    yf = None  # type: ignore
    _HAS_YFINANCE = False

from options_intelligence import OptionsIntelligence
from options_underlying_router import OptionUnderlying

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


def _dte(expiry: str) -> int:
    try:
        return max(0, (date.fromisoformat(str(expiry)) - date.today()).days)
    except Exception:
        return 0


@dataclass
class SpreadLeg:
    leg_role: str
    contract_symbol: str
    option_type: str
    strike: float
    expiry: str
    delta: float
    bid: float
    ask: float
    mid: float
    volume: int
    open_interest: int
    side_open: str
    side_close: str


@dataclass
class SpreadCandidate:
    ticker: str
    underlying_strategy: str
    underlying_direction: str
    structure_type: str
    source_type: str
    expiry: str
    dte: int
    contracts: int
    entry_net_price: float
    entry_is_credit: bool
    width: float
    max_risk_usd: float
    max_profit_usd: float
    profit_target_mark: float
    stop_mark: float
    score: float
    underlying_score: float
    underlying_entry_price: float
    underlying_stop_loss: float
    underlying_target_price: float
    equity_rr: float
    options_pcr: Optional[float]
    options_gamma: Optional[str]
    iv_regime: str
    iv_rank_30d: Optional[float]
    iv_rank_252d: Optional[float]
    short_leg: SpreadLeg
    long_leg: SpreadLeg
    notes: Dict = field(default_factory=dict)


class OptionsMarketDataAdapter:
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


class OptionsSpreadScanner:
    CREDIT_DTE_MIN = 21
    CREDIT_DTE_MAX = 60
    DEBIT_DTE_MIN = 30
    DEBIT_DTE_MAX = 60
    SHORT_MIN_VOLUME = 50
    SHORT_MIN_OI = 500
    SHORT_MAX_SPREAD_PCT = 0.05
    LONG_MIN_VOLUME = 0
    LONG_MIN_OI = 100
    LONG_MAX_SPREAD_PCT = 0.12
    CREDIT_WIDTH_MIN_DEFAULT = 0.20

    def __init__(self, provider=None):
        self.provider = provider or OptionsMarketDataAdapter()
        self.last_diagnostics: Dict[str, object] = {}

    def scan_underlying(self, underlying: OptionUnderlying, iv_context: Dict) -> Tuple[Optional[SpreadCandidate], Optional[str]]:
        strategy = str(underlying.strategy or "").upper()
        iv_regime = str(iv_context.get("iv_regime", "CHEAP")).upper()
        vix_level = float(iv_context.get("vix_level") or 0.0)
        self.last_diagnostics = {
            "ticker": underlying.ticker,
            "strategy": strategy,
            "direction": str(underlying.direction or "").upper(),
            "source_type": underlying.source_type,
            "iv_regime": iv_regime,
            "vix_level": round(vix_level, 2),
            "provider": getattr(self.provider, "source_name", None),
        }

        if vix_level >= 28.0 and (
            strategy in {"VOYAGER", "CONTRARIAN"} or (strategy == "SNIPER" and iv_regime == "RICH")
        ):
            self.last_diagnostics.update(
                {
                    "scan_type": "credit",
                    "option_type": "put",
                    "structure_type": "BULL_PUT_CREDIT",
                    "failure_reason": "vix_too_high_for_bull_put",
                }
            )
            return None, "vix_too_high_for_bull_put"

        if strategy in {"VOYAGER", "CONTRARIAN"}:
            # VIX > 25 opens the gate regardless of individual stock IV rank.
            # Elevated VIX means fat put premiums across the board — ideal for
            # credit spread selling on Voyager-approved long-term quality names.
            if iv_regime == "CHEAP" and vix_level < 25.0:
                self.last_diagnostics.update({"failure_reason": "iv_too_cheap_for_credit"})
                return None, "iv_too_cheap_for_credit"
            return self._scan_credit_spread(underlying, iv_context, option_type="put", structure_type="BULL_PUT_CREDIT")

        if strategy == "SHORT":
            if iv_regime == "CHEAP" and vix_level < 25.0:
                self.last_diagnostics.update({"failure_reason": "iv_too_cheap_for_credit"})
                return None, "iv_too_cheap_for_credit"
            return self._scan_credit_spread(underlying, iv_context, option_type="call", structure_type="BEAR_CALL_CREDIT")

        if strategy == "SNIPER":
            # When IV is RICH, sell premium (bull put credit) instead of buying it.
            # When IV is CHEAP or FAIR, buy direction via call debit spread.
            if iv_regime == "RICH":
                return self._scan_credit_spread(underlying, iv_context, option_type="put", structure_type="BULL_PUT_CREDIT")
            return self._scan_call_debit_spread(underlying, iv_context)

        self.last_diagnostics.update({"failure_reason": "strategy_not_supported"})
        return None, "strategy_not_supported"

    def mark_position(self, ticker: str, expiry: str, entry_is_credit: bool, legs: List[Dict]) -> Optional[Dict]:
        chain = self.provider.get_chain(ticker, expiry)
        spot = _safe_float(self.provider.get_current_price(ticker), None)
        if not chain or spot is None:
            return None
        calls = self._prepare_frame(chain.get("calls"), "call", spot)
        puts = self._prepare_frame(chain.get("puts"), "put", spot)
        frames = {"call": calls, "put": puts}
        leg_marks: Dict[str, float] = {}
        short_delta = None
        for leg in legs:
            option_type = str(leg["option_type"] or "").lower()
            symbol = str(leg["contract_symbol"])
            frame = frames.get(option_type)
            if frame is None or getattr(frame, "empty", True):
                return None
            match = frame[frame["contractSymbol"] == symbol]
            if match.empty:
                return None
            row = match.iloc[0]
            leg_marks[symbol] = float(row["mid"])
            if str(leg.get("leg_role") or "").upper() == "SHORT":
                short_delta = abs(float(row["delta_eff"]))
        if len(leg_marks) < 2:
            return None
        ordered = {str(leg["leg_role"]).upper(): leg_marks[str(leg["contract_symbol"])] for leg in legs}
        if entry_is_credit:
            current_mark = max(0.0, float(ordered.get("SHORT", 0.0)) - float(ordered.get("LONG", 0.0)))
        else:
            current_mark = max(0.0, float(ordered.get("LONG", 0.0)) - float(ordered.get("SHORT", 0.0)))
        return {
            "current_mark": round(current_mark, 4),
            "leg_marks": leg_marks,
            "underlying_price": float(spot),
            "current_dte": _dte(expiry),
            "short_delta": short_delta,
        }

    def _scan_credit_spread(
        self,
        underlying: OptionUnderlying,
        iv_context: Dict,
        *,
        option_type: str,
        structure_type: str,
    ) -> Tuple[Optional[SpreadCandidate], Optional[str]]:
        ticker = underlying.ticker
        spot = _safe_float(self.provider.get_current_price(ticker), None)
        if spot is None or spot <= 0:
            self.last_diagnostics.update(
                {
                    "scan_type": "credit",
                    "option_type": option_type,
                    "structure_type": structure_type,
                    "failure_reason": "no_underlying_price",
                }
            )
            return None, "no_underlying_price"

        best: Optional[SpreadCandidate] = None
        min_credit_width = self._min_credit_width_threshold(
            underlying=underlying,
            iv_context=iv_context,
            structure_type=structure_type,
        )
        diag = {
            "scan_type": "credit",
            "option_type": option_type,
            "structure_type": structure_type,
            "spot": round(float(spot), 4),
            "min_credit_width_required": round(float(min_credit_width), 4),
            "best_credit_width_observed": 0.0,
            "expirations_total": 0,
            "expirations_in_window": 0,
            "chains_with_data": 0,
            "short_candidates_pre_spot": 0,
            "short_candidates_post_spot": 0,
            "long_pool_hits": 0,
            "width_candidates": 0,
            "credit_positive": 0,
            "credit_width_pass": 0,
        }
        for expiry in self.provider.get_expirations(ticker):
            diag["expirations_total"] += 1
            dte = _dte(expiry)
            if dte < self.CREDIT_DTE_MIN or dte > self.CREDIT_DTE_MAX:
                continue
            diag["expirations_in_window"] += 1
            chain = self.provider.get_chain(ticker, expiry)
            if not chain:
                continue
            frame = self._prepare_frame(chain.get("puts" if option_type == "put" else "calls"), option_type, spot)
            if frame is None or getattr(frame, "empty", True):
                continue
            diag["chains_with_data"] += 1
            total_rows = int(len(frame))
            diag["rows_total"] = int(diag.get("rows_total", 0) or 0) + total_rows
            mid_ok = frame[frame["mid"] > 0]
            diag["mid_positive"] = int(diag.get("mid_positive", 0) or 0) + int(len(mid_ok))
            delta_ok = mid_ok[(mid_ok["delta_abs"] >= 0.18) & (mid_ok["delta_abs"] <= 0.28)]
            diag["delta_band_pass"] = int(diag.get("delta_band_pass", 0) or 0) + int(len(delta_ok))
            volume_ok = delta_ok[delta_ok["volume"] >= self.SHORT_MIN_VOLUME]
            diag["short_volume_pass"] = int(diag.get("short_volume_pass", 0) or 0) + int(len(volume_ok))
            oi_ok = volume_ok[volume_ok["openInterest"] >= self.SHORT_MIN_OI]
            diag["short_oi_pass"] = int(diag.get("short_oi_pass", 0) or 0) + int(len(oi_ok))
            spread_ok = oi_ok[oi_ok["spread_pct"] <= self.SHORT_MAX_SPREAD_PCT]
            diag["short_spread_pass"] = int(diag.get("short_spread_pass", 0) or 0) + int(len(spread_ok))
            short_candidates = spread_ok.copy()
            diag["short_candidates_pre_spot"] += int(len(short_candidates))
            if option_type == "put":
                short_candidates = short_candidates[short_candidates["strike"] < float(spot)]
            else:
                short_candidates = short_candidates[short_candidates["strike"] > float(spot)]
            diag["short_candidates_post_spot"] += int(len(short_candidates))
            if short_candidates.empty:
                continue
            width_min, width_max = self._width_bounds(spot)
            for _, short_row in short_candidates.iterrows():
                if option_type == "put":
                    long_pool = frame[(frame["strike"] < short_row["strike"]) & (frame["delta_abs"] >= 0.05) & (frame["delta_abs"] <= 0.15)]
                else:
                    long_pool = frame[(frame["strike"] > short_row["strike"]) & (frame["delta_abs"] >= 0.05) & (frame["delta_abs"] <= 0.15)]
                # The protective long wing does not need the same tape quality as
                # the premium-selling short leg; the combo still enters as one spread.
                long_pool = long_pool[long_pool["mid"] > 0]
                diag["long_mid_positive"] = int(diag.get("long_mid_positive", 0) or 0) + int(len(long_pool))
                long_pool = long_pool[long_pool["volume"] >= self.LONG_MIN_VOLUME]
                diag["long_volume_pass"] = int(diag.get("long_volume_pass", 0) or 0) + int(len(long_pool))
                long_pool = long_pool[long_pool["openInterest"] >= self.LONG_MIN_OI]
                diag["long_oi_pass"] = int(diag.get("long_oi_pass", 0) or 0) + int(len(long_pool))
                long_pool = long_pool[long_pool["spread_pct"] <= self.LONG_MAX_SPREAD_PCT]
                diag["long_spread_pass"] = int(diag.get("long_spread_pass", 0) or 0) + int(len(long_pool))
                if long_pool.empty:
                    continue
                diag["long_pool_hits"] += 1
                for _, long_row in long_pool.iterrows():
                    width = abs(float(short_row["strike"]) - float(long_row["strike"]))
                    if width < width_min or width > width_max:
                        continue
                    diag["width_candidates"] += 1
                    credit = float(short_row["mid"]) - float(long_row["mid"])
                    if credit <= 0:
                        continue
                    diag["credit_positive"] += 1
                    credit_width = credit / width if width > 0 else 0.0
                    if credit_width > float(diag.get("best_credit_width_observed", 0.0) or 0.0):
                        diag["best_credit_width_observed"] = round(float(credit_width), 4)
                    if credit_width < min_credit_width:
                        continue
                    diag["credit_width_pass"] += 1
                    candidate = self._build_credit_candidate(
                        underlying,
                        iv_context,
                        structure_type=structure_type,
                        option_type=option_type,
                        expiry=expiry,
                        dte=dte,
                        spot=spot,
                        short_row=short_row,
                        long_row=long_row,
                        width=width,
                        credit=credit,
                    )
                    if candidate and (best is None or candidate.score > best.score):
                        best = candidate
        if best is not None:
            diag.update(
                {
                    "selected_expiry": best.expiry,
                    "selected_dte": best.dte,
                    "selected_score": best.score,
                    "selected_net_price": best.entry_net_price,
                    "selected_max_risk": best.max_risk_usd,
                }
            )
            self.last_diagnostics.update(diag)
            return best, None

        reason = self._credit_failure_reason(diag)
        diag["failure_reason"] = reason
        self.last_diagnostics.update(diag)
        return None, reason

    def _scan_call_debit_spread(self, underlying: OptionUnderlying, iv_context: Dict) -> Tuple[Optional[SpreadCandidate], Optional[str]]:
        ticker = underlying.ticker
        spot = _safe_float(self.provider.get_current_price(ticker), None)
        if spot is None or spot <= 0:
            self.last_diagnostics.update(
                {
                    "scan_type": "debit",
                    "option_type": "call",
                    "structure_type": "CALL_DEBIT",
                    "failure_reason": "no_underlying_price",
                }
            )
            return None, "no_underlying_price"

        best: Optional[SpreadCandidate] = None
        diag = {
            "scan_type": "debit",
            "option_type": "call",
            "structure_type": "CALL_DEBIT",
            "spot": round(float(spot), 4),
            "expirations_total": 0,
            "expirations_in_window": 0,
            "chains_with_data": 0,
            "long_pool_candidates": 0,
            "short_pool_candidates": 0,
            "width_candidates": 0,
            "debit_positive": 0,
            "debit_width_pass": 0,
        }
        for expiry in self.provider.get_expirations(ticker):
            diag["expirations_total"] += 1
            dte = _dte(expiry)
            if dte < self.DEBIT_DTE_MIN or dte > self.DEBIT_DTE_MAX:
                continue
            diag["expirations_in_window"] += 1
            chain = self.provider.get_chain(ticker, expiry)
            if not chain:
                continue
            calls = self._prepare_frame(chain.get("calls"), "call", spot)
            if calls is None or getattr(calls, "empty", True):
                continue
            diag["chains_with_data"] += 1
            long_pool = calls[
                (calls["delta_abs"] >= 0.55)
                & (calls["delta_abs"] <= 0.70)
                & (calls["volume"] >= 50)
                & (calls["openInterest"] >= 500)
                & (calls["spread_pct"] <= 0.05)
                & (calls["mid"] > 0)
            ].copy()
            short_pool = calls[
                (calls["delta_abs"] >= 0.25)
                & (calls["delta_abs"] <= 0.45)
                & (calls["volume"] >= 50)
                & (calls["openInterest"] >= 500)
                & (calls["spread_pct"] <= 0.05)
                & (calls["mid"] > 0)
            ].copy()
            diag["long_pool_candidates"] += int(len(long_pool))
            diag["short_pool_candidates"] += int(len(short_pool))
            if long_pool.empty or short_pool.empty:
                continue
            width_min, width_max = self._width_bounds(spot)
            for _, long_row in long_pool.iterrows():
                candidates = short_pool[short_pool["strike"] > long_row["strike"]]
                for _, short_row in candidates.iterrows():
                    width = abs(float(short_row["strike"]) - float(long_row["strike"]))
                    if width < width_min or width > width_max:
                        continue
                    diag["width_candidates"] += 1
                    debit = float(long_row["mid"]) - float(short_row["mid"])
                    if debit <= 0:
                        continue
                    diag["debit_positive"] += 1
                    debit_width = debit / width if width > 0 else 1.0
                    if debit_width > 0.65:
                        continue
                    diag["debit_width_pass"] += 1
                    candidate = self._build_debit_candidate(
                        underlying,
                        iv_context,
                        expiry=expiry,
                        dte=dte,
                        spot=spot,
                        long_row=long_row,
                        short_row=short_row,
                        width=width,
                        debit=debit,
                    )
                    if candidate and (best is None or candidate.score > best.score):
                        best = candidate
        if best is not None:
            diag.update(
                {
                    "selected_expiry": best.expiry,
                    "selected_dte": best.dte,
                    "selected_score": best.score,
                    "selected_net_price": best.entry_net_price,
                    "selected_max_risk": best.max_risk_usd,
                }
            )
            self.last_diagnostics.update(diag)
            return best, None

        reason = self._debit_failure_reason(diag)
        diag["failure_reason"] = reason
        self.last_diagnostics.update(diag)
        return None, reason

    def _build_credit_candidate(self, underlying: OptionUnderlying, iv_context: Dict, *, structure_type: str, option_type: str, expiry: str, dte: int, spot: float, short_row, long_row, width: float, credit: float) -> SpreadCandidate:
        short_leg = SpreadLeg(
            leg_role="SHORT",
            contract_symbol=str(short_row["contractSymbol"]),
            option_type=option_type,
            strike=float(short_row["strike"]),
            expiry=expiry,
            delta=float(short_row["delta_eff"]),
            bid=float(short_row["bid"]),
            ask=float(short_row["ask"]),
            mid=float(short_row["mid"]),
            volume=int(short_row["volume"]),
            open_interest=int(short_row["openInterest"]),
            side_open="SELL_TO_OPEN",
            side_close="BUY_TO_CLOSE",
        )
        long_leg = SpreadLeg(
            leg_role="LONG",
            contract_symbol=str(long_row["contractSymbol"]),
            option_type=option_type,
            strike=float(long_row["strike"]),
            expiry=expiry,
            delta=float(long_row["delta_eff"]),
            bid=float(long_row["bid"]),
            ask=float(long_row["ask"]),
            mid=float(long_row["mid"]),
            volume=int(long_row["volume"]),
            open_interest=int(long_row["openInterest"]),
            side_open="BUY_TO_OPEN",
            side_close="SELL_TO_CLOSE",
        )
        max_risk = max(0.01, (width - credit) * 100.0)
        max_profit = max(0.0, credit * 100.0)
        score = self._score_credit_spread(
            underlying_score=float(underlying.score or 0.0),
            credit_width=(credit / width) if width > 0 else 0.0,
            short_delta=abs(float(short_leg.delta)),
            dte=dte,
            short_liquidity=min(short_leg.open_interest, long_leg.open_interest),
            iv_rank=float(iv_context.get("iv_rank_252d") or iv_context.get("iv_rank_30d") or 50.0),
        )
        return SpreadCandidate(
            ticker=underlying.ticker,
            underlying_strategy=underlying.strategy,
            underlying_direction=underlying.direction,
            structure_type=structure_type,
            source_type=underlying.source_type,
            expiry=expiry,
            dte=dte,
            contracts=1,
            entry_net_price=round(float(credit), 4),
            entry_is_credit=True,
            width=round(float(width), 4),
            max_risk_usd=round(max_risk, 2),
            max_profit_usd=round(max_profit, 2),
            profit_target_mark=round(max(0.01, float(credit) * 0.50), 4),
            stop_mark=round(float(credit) * 2.0, 4),
            score=score,
            underlying_score=float(underlying.score or 0.0),
            underlying_entry_price=float(underlying.entry_price),
            underlying_stop_loss=float(underlying.stop_loss),
            underlying_target_price=float(underlying.target_price),
            equity_rr=float(underlying.equity_rr or 0.0),
            options_pcr=underlying.options_pcr,
            options_gamma=underlying.options_gamma,
            iv_regime=str(iv_context.get("iv_regime") or "UNKNOWN"),
            iv_rank_30d=_safe_float(iv_context.get("iv_rank_30d"), None),
            iv_rank_252d=_safe_float(iv_context.get("iv_rank_252d"), None),
            short_leg=short_leg,
            long_leg=long_leg,
            notes={
                "spot": float(spot),
                "credit_width_ratio": round((credit / width) if width > 0 else 0.0, 4),
            },
        )

    def _build_debit_candidate(self, underlying: OptionUnderlying, iv_context: Dict, *, expiry: str, dte: int, spot: float, long_row, short_row, width: float, debit: float) -> SpreadCandidate:
        long_leg = SpreadLeg(
            leg_role="LONG",
            contract_symbol=str(long_row["contractSymbol"]),
            option_type="call",
            strike=float(long_row["strike"]),
            expiry=expiry,
            delta=float(long_row["delta_eff"]),
            bid=float(long_row["bid"]),
            ask=float(long_row["ask"]),
            mid=float(long_row["mid"]),
            volume=int(long_row["volume"]),
            open_interest=int(long_row["openInterest"]),
            side_open="BUY_TO_OPEN",
            side_close="SELL_TO_CLOSE",
        )
        short_leg = SpreadLeg(
            leg_role="SHORT",
            contract_symbol=str(short_row["contractSymbol"]),
            option_type="call",
            strike=float(short_row["strike"]),
            expiry=expiry,
            delta=float(short_row["delta_eff"]),
            bid=float(short_row["bid"]),
            ask=float(short_row["ask"]),
            mid=float(short_row["mid"]),
            volume=int(short_row["volume"]),
            open_interest=int(short_row["openInterest"]),
            side_open="SELL_TO_OPEN",
            side_close="BUY_TO_CLOSE",
        )
        max_risk = max(0.01, debit * 100.0)
        max_profit = max(0.0, (width - debit) * 100.0)
        score = self._score_debit_spread(
            underlying_score=float(underlying.score or 0.0),
            payout_ratio=((width - debit) / debit) if debit > 0 else 0.0,
            long_delta=abs(float(long_leg.delta)),
            short_delta=abs(float(short_leg.delta)),
            dte=dte,
            liquidity=min(long_leg.open_interest, short_leg.open_interest),
            iv_rank=float(iv_context.get("iv_rank_252d") or iv_context.get("iv_rank_30d") or 50.0),
        )
        return SpreadCandidate(
            ticker=underlying.ticker,
            underlying_strategy=underlying.strategy,
            underlying_direction=underlying.direction,
            structure_type="CALL_DEBIT",
            source_type=underlying.source_type,
            expiry=expiry,
            dte=dte,
            contracts=1,
            entry_net_price=round(float(debit), 4),
            entry_is_credit=False,
            width=round(float(width), 4),
            max_risk_usd=round(max_risk, 2),
            max_profit_usd=round(max_profit, 2),
            profit_target_mark=round(min(width * 0.85, debit * 1.5), 4),
            stop_mark=round(max(0.01, debit * 0.5), 4),
            score=score,
            underlying_score=float(underlying.score or 0.0),
            underlying_entry_price=float(underlying.entry_price),
            underlying_stop_loss=float(underlying.stop_loss),
            underlying_target_price=float(underlying.target_price),
            equity_rr=float(underlying.equity_rr or 0.0),
            options_pcr=underlying.options_pcr,
            options_gamma=underlying.options_gamma,
            iv_regime=str(iv_context.get("iv_regime") or "UNKNOWN"),
            iv_rank_30d=_safe_float(iv_context.get("iv_rank_30d"), None),
            iv_rank_252d=_safe_float(iv_context.get("iv_rank_252d"), None),
            short_leg=short_leg,
            long_leg=long_leg,
            notes={
                "spot": float(spot),
                "debit_width_ratio": round((debit / width) if width > 0 else 0.0, 4),
            },
        )

    @staticmethod
    def _width_bounds(spot: float) -> Tuple[float, float]:
        if spot < 50:
            return 1.0, 3.0
        if spot < 150:
            return 2.0, 5.0
        return 5.0, 10.0

    def _prepare_frame(self, frame, option_type: str, spot: float):
        if frame is None or getattr(frame, "empty", True):
            return frame
        rows = frame.copy()
        rows["strike"] = rows["strike"].astype(float)
        for col in ("bid", "ask", "lastPrice", "openInterest", "volume"):
            if col not in rows.columns:
                rows[col] = 0.0
        if "contractSymbol" not in rows.columns:
            rows["contractSymbol"] = ""
        rows["bid"] = rows["bid"].fillna(0).astype(float)
        rows["ask"] = rows["ask"].fillna(0).astype(float)
        rows["lastPrice"] = rows["lastPrice"].fillna(0).astype(float)
        rows["openInterest"] = rows["openInterest"].fillna(0).astype(int)
        rows["volume"] = rows["volume"].fillna(0).astype(int)
        rows["mid"] = rows.apply(lambda r: self._mid_price(r["bid"], r["ask"], r["lastPrice"]), axis=1)
        rows["spread_pct"] = rows.apply(lambda r: self._spread_pct(r["bid"], r["ask"], r["mid"]), axis=1)
        rows["delta_eff"] = rows.apply(lambda r: self._effective_delta(r, option_type, spot), axis=1)
        rows["delta_abs"] = rows["delta_eff"].abs()
        return rows

    @staticmethod
    def _mid_price(bid: float, ask: float, last_price: float) -> float:
        bid = float(_safe_float(bid, 0.0) or 0.0)
        ask = float(_safe_float(ask, 0.0) or 0.0)
        last_price = float(_safe_float(last_price, 0.0) or 0.0)
        if bid > 0 and ask > 0:
            return round((bid + ask) / 2.0, 4)
        if ask > 0:
            return round(ask, 4)
        if bid > 0:
            return round(bid, 4)
        return round(last_price, 4)

    def _min_credit_width_threshold(
        self,
        *,
        underlying: OptionUnderlying,
        iv_context: Dict,
        structure_type: str,
    ) -> float:
        strategy = str(underlying.strategy or "").upper()
        vix_level = float(_safe_float(iv_context.get("vix_level"), 0.0) or 0.0)
        if structure_type == "BULL_PUT_CREDIT" and vix_level >= 28.0:
            return 1.0
        if structure_type == "BULL_PUT_CREDIT" and strategy in {"VOYAGER", "CONTRARIAN"}:
            return 0.20
        if structure_type == "BULL_PUT_CREDIT" and strategy == "SNIPER":
            return 0.18
        return self.CREDIT_WIDTH_MIN_DEFAULT

    @staticmethod
    def _spread_pct(bid: float, ask: float, mid: float) -> float:
        bid = float(_safe_float(bid, 0.0) or 0.0)
        ask = float(_safe_float(ask, 0.0) or 0.0)
        mid = float(_safe_float(mid, 0.0) or 0.0)
        if mid <= 0 or ask <= 0:
            return 1.0
        return abs(ask - bid) / mid

    @staticmethod
    def _effective_delta(row, option_type: str, spot: float) -> float:
        raw_delta = _safe_float(row.get("delta"), None)
        if raw_delta is not None and abs(raw_delta) > 0:
            return float(raw_delta)
        strike = float(_safe_float(row.get("strike"), spot) or spot)
        if spot <= 0:
            return 0.0
        if option_type == "call":
            otm_pct = max(0.0, (strike - spot) / spot)
            itm_pct = max(0.0, (spot - strike) / spot)
            if itm_pct > 0:
                return min(0.95, 0.55 + itm_pct * 4.0)
            return max(0.05, 0.5 - otm_pct * 6.0)
        otm_pct = max(0.0, (spot - strike) / spot)
        itm_pct = max(0.0, (strike - spot) / spot)
        if itm_pct > 0:
            return -min(0.95, 0.55 + itm_pct * 4.0)
        return -max(0.05, 0.5 - otm_pct * 6.0)

    @staticmethod
    def _score_credit_spread(*, underlying_score: float, credit_width: float, short_delta: float, dte: int, short_liquidity: int, iv_rank: float) -> float:
        thesis = min(100.0, max(0.0, underlying_score))
        efficiency = min(100.0, max(0.0, credit_width / 0.35 * 100.0))
        delta_quality = max(0.0, 100.0 - abs(short_delta - 0.23) / 0.10 * 100.0)
        dte_quality = max(0.0, 100.0 - abs(dte - 37) / 15.0 * 100.0)
        liquidity = min(100.0, short_liquidity / 1500.0 * 100.0)
        iv_fit = min(100.0, max(0.0, iv_rank))
        score = (
            thesis * 0.30
            + efficiency * 0.25
            + delta_quality * 0.20
            + dte_quality * 0.10
            + liquidity * 0.10
            + iv_fit * 0.05
        )
        return round(score, 1)

    @staticmethod
    def _score_debit_spread(*, underlying_score: float, payout_ratio: float, long_delta: float, short_delta: float, dte: int, liquidity: int, iv_rank: float) -> float:
        thesis = min(100.0, max(0.0, underlying_score))
        payout = min(100.0, max(0.0, payout_ratio / 1.5 * 100.0))
        delta_quality = max(0.0, 100.0 - (abs(long_delta - 0.62) + abs(short_delta - 0.32)) / 0.25 * 100.0)
        dte_quality = max(0.0, 100.0 - abs(dte - 45) / 20.0 * 100.0)
        liquidity_score = min(100.0, liquidity / 1500.0 * 100.0)
        iv_fit = max(0.0, 100.0 - max(0.0, iv_rank - 40.0) * 2.0)
        score = (
            thesis * 0.35
            + payout * 0.25
            + delta_quality * 0.20
            + dte_quality * 0.10
            + liquidity_score * 0.05
            + iv_fit * 0.05
        )
        return round(score, 1)

    @staticmethod
    def _credit_failure_reason(diag: Dict[str, object]) -> str:
        if int(diag.get("expirations_in_window", 0) or 0) == 0:
            return "no_expiry_in_dte_window"
        if int(diag.get("chains_with_data", 0) or 0) == 0:
            return "no_option_chain_data"
        if int(diag.get("short_candidates_post_spot", 0) or 0) == 0:
            return "no_short_leg_candidate"
        if int(diag.get("long_pool_hits", 0) or 0) == 0:
            return "no_long_leg_candidate"
        if int(diag.get("width_candidates", 0) or 0) == 0:
            return "no_width_aligned_pairs"
        if int(diag.get("credit_positive", 0) or 0) == 0:
            return "non_positive_credit"
        if int(diag.get("credit_width_pass", 0) or 0) == 0:
            return "credit_width_too_low"
        return "no_credit_spread_qualified"

    @staticmethod
    def _debit_failure_reason(diag: Dict[str, object]) -> str:
        if int(diag.get("expirations_in_window", 0) or 0) == 0:
            return "no_expiry_in_dte_window"
        if int(diag.get("chains_with_data", 0) or 0) == 0:
            return "no_option_chain_data"
        if int(diag.get("long_pool_candidates", 0) or 0) == 0:
            return "no_debit_long_leg_candidate"
        if int(diag.get("short_pool_candidates", 0) or 0) == 0:
            return "no_debit_short_leg_candidate"
        if int(diag.get("width_candidates", 0) or 0) == 0:
            return "no_width_aligned_pairs"
        if int(diag.get("debit_positive", 0) or 0) == 0:
            return "non_positive_debit"
        if int(diag.get("debit_width_pass", 0) or 0) == 0:
            return "debit_too_expensive"
        return "no_call_debit_spread_qualified"
