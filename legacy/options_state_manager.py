from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from options_logger import OptionsLogger
from options_spread_scanner import SpreadCandidate


@dataclass
class OptionLegState:
    id: int
    leg_role: str
    contract_symbol: str
    option_type: str
    strike: float
    expiry: str
    side_open: str
    side_close: str
    quantity: int
    delta_at_entry: float
    entry_price: float
    exit_price: Optional[float]


@dataclass
class OptionPositionState:
    id: int
    ticker: str
    underlying_strategy: str
    structure_type: str
    underlying_direction: str
    state: str
    broker: str
    paper_mode: bool
    run_id: Optional[str]
    source_type: Optional[str]
    opened_at: str
    closed_at: Optional[str]
    entry_expiry: str
    entry_dte: int
    contracts: int
    entry_net_price: float
    entry_is_credit: bool
    profit_target_mark: float
    stop_mark: float
    max_profit_usd: float
    max_risk_usd: float
    current_mark: float
    total_pnl_usd: float
    total_pnl_pct: float
    underlying_entry_price: float
    underlying_exit_price: Optional[float]
    iv_regime: Optional[str]
    iv_rank_30d: Optional[float]
    iv_rank_252d: Optional[float]
    options_pcr: Optional[float]
    options_gamma: Optional[str]
    notes: Optional[str]
    candidate_id: Optional[int]
    legs: List[OptionLegState]


class OptionsStateManager:
    def __init__(self, db_path: str = "trading_performance.db", logger_store: Optional[OptionsLogger] = None):
        self.logger_store = logger_store or OptionsLogger(db_path=db_path)

    def load_open_positions(self) -> List[OptionPositionState]:
        positions: List[OptionPositionState] = []
        for row in self.logger_store.load_open_positions():
            leg_rows = self.logger_store.load_legs(int(row["id"]))
            positions.append(
                OptionPositionState(
                    id=int(row["id"]),
                    ticker=str(row["ticker"]),
                    underlying_strategy=str(row["underlying_strategy"]),
                    structure_type=str(row["structure_type"]),
                    underlying_direction=str(row["underlying_direction"]),
                    state=str(row["state"]),
                    broker=str(row["broker"]),
                    paper_mode=bool(int(row["paper_mode"] or 0)),
                    run_id=row["run_id"],
                    source_type=row["source_type"],
                    opened_at=str(row["opened_at"]),
                    closed_at=row["closed_at"],
                    entry_expiry=str(row["entry_expiry"]),
                    entry_dte=int(row["entry_dte"] or 0),
                    contracts=int(row["contracts"] or 1),
                    entry_net_price=float(row["entry_net_price"] or 0.0),
                    entry_is_credit=bool(int(row["entry_is_credit"] or 0)),
                    profit_target_mark=float(row["profit_target_mark"] or 0.0),
                    stop_mark=float(row["stop_mark"] or 0.0),
                    max_profit_usd=float(row["max_profit_usd"] or 0.0),
                    max_risk_usd=float(row["max_risk_usd"] or 0.0),
                    current_mark=float(row["current_mark"] or row["entry_net_price"] or 0.0),
                    total_pnl_usd=float(row["total_pnl_usd"] or 0.0),
                    total_pnl_pct=float(row["total_pnl_pct"] or 0.0),
                    underlying_entry_price=float(row["underlying_entry_price"] or 0.0),
                    underlying_exit_price=row["underlying_exit_price"],
                    iv_regime=row["iv_regime"],
                    iv_rank_30d=row["iv_rank_30d"],
                    iv_rank_252d=row["iv_rank_252d"],
                    options_pcr=row["options_pcr"],
                    options_gamma=row["options_gamma"],
                    notes=row["notes"],
                    candidate_id=row["candidate_id"],
                    legs=[
                        OptionLegState(
                            id=int(leg["id"]),
                            leg_role=str(leg["leg_role"]),
                            contract_symbol=str(leg["contract_symbol"]),
                            option_type=str(leg["option_type"]),
                            strike=float(leg["strike"] or 0.0),
                            expiry=str(leg["expiry"]),
                            side_open=str(leg["side_open"]),
                            side_close=str(leg["side_close"] or ""),
                            quantity=int(leg["quantity"] or 1),
                            delta_at_entry=float(leg["delta_at_entry"] or 0.0),
                            entry_price=float(leg["entry_price"] or 0.0),
                            exit_price=float(leg["exit_price"]) if leg["exit_price"] is not None else None,
                        )
                        for leg in leg_rows
                    ],
                )
            )
        return positions

    def get_open_tickers(self) -> set[str]:
        return {pos.ticker for pos in self.load_open_positions()}

    def open_from_candidate(
        self,
        candidate: SpreadCandidate,
        *,
        run_id: Optional[str],
        broker: str,
        paper_mode: bool,
        candidate_id: Optional[int],
    ) -> int:
        return self.logger_store.open_position(
            position_payload={
                "ticker": candidate.ticker,
                "underlying_strategy": candidate.underlying_strategy,
                "structure_type": candidate.structure_type,
                "underlying_direction": candidate.underlying_direction,
                "state": "OPEN",
                "broker": broker,
                "paper_mode": paper_mode,
                "run_id": run_id,
                "source_type": candidate.source_type,
                "entry_expiry": candidate.expiry,
                "entry_dte": candidate.dte,
                "contracts": candidate.contracts,
                "entry_net_price": candidate.entry_net_price,
                "entry_is_credit": candidate.entry_is_credit,
                "profit_target_mark": candidate.profit_target_mark,
                "stop_mark": candidate.stop_mark,
                "max_profit_usd": candidate.max_profit_usd,
                "max_risk_usd": candidate.max_risk_usd,
                "current_mark": candidate.entry_net_price,
                "underlying_entry_price": candidate.underlying_entry_price,
                "iv_regime": candidate.iv_regime,
                "iv_rank_30d": candidate.iv_rank_30d,
                "iv_rank_252d": candidate.iv_rank_252d,
                "options_pcr": candidate.options_pcr,
                "options_gamma": candidate.options_gamma,
                "notes": {
                    **(candidate.notes or {}),
                    "underlying_entry_price": candidate.underlying_entry_price,
                    "underlying_stop_loss": candidate.underlying_stop_loss,
                    "underlying_target_price": candidate.underlying_target_price,
                    "equity_rr": candidate.equity_rr,
                },
                "candidate_id": candidate_id,
            },
            legs=[
                {
                    "leg_role": candidate.short_leg.leg_role,
                    "contract_symbol": candidate.short_leg.contract_symbol,
                    "option_type": candidate.short_leg.option_type,
                    "strike": candidate.short_leg.strike,
                    "expiry": candidate.short_leg.expiry,
                    "side_open": candidate.short_leg.side_open,
                    "side_close": candidate.short_leg.side_close,
                    "quantity": candidate.contracts,
                    "delta_at_entry": candidate.short_leg.delta,
                    "entry_price": candidate.short_leg.mid,
                },
                {
                    "leg_role": candidate.long_leg.leg_role,
                    "contract_symbol": candidate.long_leg.contract_symbol,
                    "option_type": candidate.long_leg.option_type,
                    "strike": candidate.long_leg.strike,
                    "expiry": candidate.long_leg.expiry,
                    "side_open": candidate.long_leg.side_open,
                    "side_close": candidate.long_leg.side_close,
                    "quantity": candidate.contracts,
                    "delta_at_entry": candidate.long_leg.delta,
                    "entry_price": candidate.long_leg.mid,
                },
            ],
        )

    def evaluate_actions(
        self,
        position: OptionPositionState,
        *,
        current_mark: float,
        current_dte: int,
        short_delta: Optional[float],
        earnings_blackout: bool,
        thesis_broken: bool,
    ) -> List[str]:
        actions: List[str] = []
        if position.entry_is_credit:
            if current_mark <= position.profit_target_mark:
                actions.append("TAKE_PROFIT")
            elif current_mark >= position.stop_mark:
                actions.append("STOP_LOSS")
        else:
            if current_mark >= position.profit_target_mark:
                actions.append("TAKE_PROFIT")
            elif current_mark <= position.stop_mark:
                actions.append("STOP_LOSS")
        if earnings_blackout:
            actions.append("EARNINGS_EXIT")
        if thesis_broken:
            actions.append("THESIS_EXIT")
        if current_dte <= 21:
            actions.append("DTE_EXIT")
        if short_delta is not None and short_delta >= 0.40:
            actions.append("DELTA_PRESSURE")
        return actions

    def pnl_snapshot(self, position: OptionPositionState, current_mark: float) -> Dict[str, float]:
        multiplier = 100.0 * float(position.contracts or 1)
        if position.entry_is_credit:
            pnl_usd = (float(position.entry_net_price) - float(current_mark)) * multiplier
        else:
            pnl_usd = (float(current_mark) - float(position.entry_net_price)) * multiplier
        basis = float(position.max_risk_usd or 0.0) if position.max_risk_usd else (float(position.entry_net_price) * multiplier)
        pnl_pct = (pnl_usd / basis * 100.0) if basis > 0 else 0.0
        return {
            "total_pnl_usd": round(pnl_usd, 2),
            "total_pnl_pct": round(pnl_pct, 2),
        }

    def update_mark(self, position_id: int, *, current_mark: float, total_pnl_usd: float, total_pnl_pct: float, notes: Optional[Dict] = None) -> None:
        self.logger_store.update_position_mark(
            position_id,
            current_mark=current_mark,
            total_pnl_usd=total_pnl_usd,
            total_pnl_pct=total_pnl_pct,
            notes=notes,
        )

    def close_position(
        self,
        position_id: int,
        *,
        close_reason: str,
        current_mark: float,
        total_pnl_usd: float,
        total_pnl_pct: float,
        underlying_exit_price: Optional[float],
        leg_exit_prices: Optional[Dict[str, float]] = None,
        notes: Optional[Dict] = None,
    ) -> None:
        self.logger_store.close_position(
            position_id,
            close_reason=close_reason,
            current_mark=current_mark,
            total_pnl_usd=total_pnl_usd,
            total_pnl_pct=total_pnl_pct,
            underlying_exit_price=underlying_exit_price,
            notes=notes,
            leg_exit_prices=leg_exit_prices,
        )
