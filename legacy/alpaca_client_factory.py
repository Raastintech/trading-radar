"""Shared Alpaca client helpers for runtime scanners."""

from __future__ import annotations

import logging
import os
from typing import Optional


def build_trading_client(
    logger: Optional[logging.Logger] = None,
    paper: bool = True,
):
    """Create an Alpaca TradingClient when credentials are available."""
    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

    if not api_key or not secret_key:
        if logger:
            logger.warning("No Alpaca trading credentials - asset discovery unavailable")
        return None

    try:
        from alpaca.trading.client import TradingClient

        return TradingClient(api_key, secret_key, paper=paper)
    except Exception as exc:
        if logger:
            logger.warning("Could not initialize Alpaca TradingClient: %s", exc)
        return None
