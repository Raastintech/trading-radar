from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from options_spread_scanner import SpreadCandidate
from options_state_manager import OptionPositionState

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    submitted: bool
    broker: str
    order_id: Optional[str]
    paper_mode: bool
    fill_price: Optional[float]
    message: str
    raw: Optional[object] = None


class OptionsBrokerRouter:
    def __init__(self, trading_client=None, execution_mode: str = "PAPER"):
        self.trading_client = trading_client
        self.execution_mode = str(execution_mode or "PAPER").upper()
        self.options_enabled = str(os.getenv("OPTIONS_ENABLE", "0")).strip().lower() in {"1", "true", "yes", "on"}
        self.options_live = str(os.getenv("OPTIONS_LIVE", "0")).strip().lower() in {"1", "true", "yes", "on"}
        self.alpaca_live = str(os.getenv("ALPACA_OPTIONS_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}

    @property
    def paper_mode(self) -> bool:
        return not (self.options_live and self.execution_mode == "LIVE")

    def is_enabled(self) -> bool:
        return self.options_enabled

    def submit_open(self, candidate: SpreadCandidate) -> OrderResult:
        if self.paper_mode:
            return OrderResult(
                submitted=True,
                broker="PAPER",
                order_id=f"PAPER-OPT-{uuid.uuid4()}",
                paper_mode=True,
                fill_price=float(candidate.entry_net_price),
                message="paper fill assumed at entry net price",
            )
        if not self.trading_client or not self.alpaca_live:
            return OrderResult(
                submitted=False,
                broker="ALPACA",
                order_id=None,
                paper_mode=False,
                fill_price=None,
                message="live options requested but Alpaca options routing not enabled",
            )
        return self._submit_alpaca_open(candidate)

    def submit_close(self, position: OptionPositionState, close_mark: float) -> OrderResult:
        if self.paper_mode:
            return OrderResult(
                submitted=True,
                broker="PAPER",
                order_id=f"PAPER-OPT-CLOSE-{uuid.uuid4()}",
                paper_mode=True,
                fill_price=float(close_mark),
                message="paper close assumed at mark",
            )
        if not self.trading_client or not self.alpaca_live:
            return OrderResult(
                submitted=False,
                broker="ALPACA",
                order_id=None,
                paper_mode=False,
                fill_price=None,
                message="live options close requested but Alpaca options routing not enabled",
            )
        return self._submit_alpaca_close(position, close_mark)

    def _submit_alpaca_open(self, candidate: SpreadCandidate) -> OrderResult:
        try:
            from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
            from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

            side = OrderSide.SELL if candidate.entry_is_credit else OrderSide.BUY
            limit_price = self._open_limit(candidate)
            request = LimitOrderRequest(
                symbol=candidate.ticker,
                qty=float(candidate.contracts),
                side=side,
                type="limit",
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.MLEG,
                limit_price=float(limit_price),
                legs=[
                    OptionLegRequest(
                        symbol=candidate.short_leg.contract_symbol,
                        ratio_qty=float(candidate.contracts),
                        side=OrderSide.SELL,
                        position_intent=PositionIntent.SELL_TO_OPEN,
                    ),
                    OptionLegRequest(
                        symbol=candidate.long_leg.contract_symbol,
                        ratio_qty=float(candidate.contracts),
                        side=OrderSide.BUY,
                        position_intent=PositionIntent.BUY_TO_OPEN,
                    ),
                ],
            )
            order = self.trading_client.submit_order(request)
            return OrderResult(
                submitted=True,
                broker="ALPACA",
                order_id=str(getattr(order, "id", None) or getattr(order, "client_order_id", None) or uuid.uuid4()),
                paper_mode=False,
                fill_price=float(limit_price),
                message="alpaca multileg order submitted",
                raw=order,
            )
        except Exception as exc:
            logger.warning("[OPTIONS] Alpaca open submit failed: %s", exc)
            return OrderResult(
                submitted=False,
                broker="ALPACA",
                order_id=None,
                paper_mode=False,
                fill_price=None,
                message=str(exc),
            )

    def _submit_alpaca_close(self, position: OptionPositionState, close_mark: float) -> OrderResult:
        try:
            from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
            from alpaca.trading.requests import LimitOrderRequest, OptionLegRequest

            side = OrderSide.BUY if position.entry_is_credit else OrderSide.SELL
            limit_price = self._close_limit(position, close_mark)
            legs = []
            for leg in position.legs:
                if str(leg.leg_role).upper() == "SHORT":
                    leg_side = OrderSide.BUY if position.entry_is_credit else OrderSide.SELL
                    intent = PositionIntent.BUY_TO_CLOSE if position.entry_is_credit else PositionIntent.SELL_TO_CLOSE
                else:
                    leg_side = OrderSide.SELL if position.entry_is_credit else OrderSide.BUY
                    intent = PositionIntent.SELL_TO_CLOSE if position.entry_is_credit else PositionIntent.BUY_TO_CLOSE
                legs.append(
                    OptionLegRequest(
                        symbol=leg.contract_symbol,
                        ratio_qty=float(position.contracts),
                        side=leg_side,
                        position_intent=intent,
                    )
                )
            request = LimitOrderRequest(
                symbol=position.ticker,
                qty=float(position.contracts),
                side=side,
                type="limit",
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.MLEG,
                limit_price=float(limit_price),
                legs=legs,
            )
            order = self.trading_client.submit_order(request)
            return OrderResult(
                submitted=True,
                broker="ALPACA",
                order_id=str(getattr(order, "id", None) or getattr(order, "client_order_id", None) or uuid.uuid4()),
                paper_mode=False,
                fill_price=float(limit_price),
                message="alpaca multileg close submitted",
                raw=order,
            )
        except Exception as exc:
            logger.warning("[OPTIONS] Alpaca close submit failed: %s", exc)
            return OrderResult(
                submitted=False,
                broker="ALPACA",
                order_id=None,
                paper_mode=False,
                fill_price=None,
                message=str(exc),
            )

    @staticmethod
    def _open_limit(candidate: SpreadCandidate) -> float:
        if candidate.entry_is_credit:
            return round(max(0.01, float(candidate.entry_net_price) - 0.05), 2)
        return round(float(candidate.entry_net_price) + 0.05, 2)

    @staticmethod
    def _close_limit(position: OptionPositionState, close_mark: float) -> float:
        if position.entry_is_credit:
            return round(float(close_mark) + 0.05, 2)
        return round(max(0.01, float(close_mark) - 0.05), 2)
