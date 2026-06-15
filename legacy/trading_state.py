"""
Trading State - canonical position normalization and daemon heartbeat.
"""

import json
import os
from datetime import datetime
from typing import Dict, Optional


HEARTBEAT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trader_heartbeat.json")


def _safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def _fetch_orders_for_ticker(trading_client, ticker: str) -> Dict:
    """
    Fetch active stop/limit orders for a ticker.
    Alpaca bracket children often show up as HELD rather than OPEN.
    """
    if not trading_client or not ticker:
        return {}

    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        req = GetOrdersRequest(status=QueryOrderStatus.ALL, symbols=[ticker], limit=20)
        orders = trading_client.get_orders(filter=req)
    except Exception:
        try:
            orders = trading_client.get_orders()
        except Exception:
            return {}

    result = {}
    active_statuses = {
        "new",
        "accepted",
        "pending_new",
        "held",
        "partially_filled",
        "open",
    }
    for order in orders:
        sym = getattr(order, "symbol", None)
        if sym != ticker:
            continue

        status = str(getattr(order, "status", "") or "").lower()
        if status.startswith("orderstatus."):
            status = status.split(".", 1)[1]
        if status not in active_statuses:
            continue

        otype = str(getattr(order, "type", "") or "").lower()
        if otype.startswith("ordertype."):
            otype = otype.split(".", 1)[1]
        stop_px = _safe_float(getattr(order, "stop_price", None))
        limit_px = _safe_float(getattr(order, "limit_price", None))

        if otype in ("stop", "stop_limit") and stop_px and stop_px > 0:
            result["stop_loss"] = stop_px
        elif otype == "limit" and limit_px and limit_px > 0:
            result["take_profit"] = limit_px

    if result.get("stop_loss") is not None and result.get("take_profit") is not None:
        result["bracket_attached"] = True

    return result


def normalize_position(raw, meta: Optional[Dict] = None, source: str = "UNKNOWN", trading_client=None) -> Dict:
    """
    Normalize a position object or dict to a canonical schema.
    Missing values stay None; they are not replaced with fake zeros.
    """
    meta = meta or {}

    position = {
        "ticker": None,
        "qty": None,
        "shares": None,
        "entry_price": None,
        "current_price": None,
        "market_value": None,
        "unrealized_pnl": None,
        "unrealized_pnl_pct": None,
        "unrealized_pl": None,
        "unrealized_pl_pct": None,
        "stop_loss": None,
        "take_profit": None,
        "target_price": None,
        "direction": None,
        "protection_status": "UNKN",
        "source": source,
        "strategy": None,
        "state": meta.get("exit_mode", "hold"),
        "sector": meta.get("sector"),
        "entry_timestamp": None,
    }

    if hasattr(raw, "symbol"):
        position["ticker"] = str(raw.symbol)
        position["qty"] = _safe_float(getattr(raw, "qty", None))
        position["entry_price"] = _safe_float(getattr(raw, "avg_entry_price", None))
        position["current_price"] = _safe_float(getattr(raw, "current_price", None))
        position["market_value"] = _safe_float(getattr(raw, "market_value", None))
        position["unrealized_pnl"] = _safe_float(getattr(raw, "unrealized_pl", None))
        plpc = _safe_float(getattr(raw, "unrealized_plpc", None))
        position["unrealized_pnl_pct"] = (plpc * 100.0) if plpc is not None else None
        position["source"] = "ALPACA_API"
    elif isinstance(raw, dict):
        nested = raw.get("signal", {}) if isinstance(raw.get("signal"), dict) else {}
        metadata = raw.get("metadata", {}) if isinstance(raw.get("metadata"), dict) else {}

        position["ticker"] = raw.get("ticker") or raw.get("symbol") or raw.get("asset")

        qty = raw.get("qty")
        if qty is None:
            qty = raw.get("shares")
        if qty is None:
            qty = raw.get("position_size")
        position["qty"] = _safe_float(qty)

        entry = raw.get("entry_price")
        if entry is None:
            entry = raw.get("entry")
        if entry is None:
            entry = raw.get("avg_entry_price")
        position["entry_price"] = _safe_float(entry)

        current = raw.get("current_price")
        if current is None:
            current = raw.get("current")
        if current is None:
            current = raw.get("price")
        if current is None:
            current = raw.get("last_price")
        if current is None:
            current = raw.get("close")
        position["current_price"] = _safe_float(current)

        market_value = raw.get("market_value")
        position["market_value"] = _safe_float(market_value)
        if position["market_value"] is None and position["qty"] is not None and position["current_price"] is not None:
            position["market_value"] = position["qty"] * position["current_price"]

        pnl = raw.get("unrealized_pnl")
        if pnl is None:
            pnl = raw.get("unrealized_pl")
        if pnl is None:
            pnl = raw.get("pnl")
        position["unrealized_pnl"] = _safe_float(pnl)
        if position["unrealized_pnl"] is None and None not in (position["qty"], position["entry_price"], position["current_price"]):
            position["unrealized_pnl"] = (position["current_price"] - position["entry_price"]) * position["qty"]

        pnl_pct = raw.get("unrealized_pnl_pct")
        if pnl_pct is None:
            pnl_pct = raw.get("unrealized_pl_pct")
        if pnl_pct is None:
            pnl_pct = raw.get("pnl_pct")
        position["unrealized_pnl_pct"] = _safe_float(pnl_pct)
        if position["unrealized_pnl_pct"] is None and None not in (position["entry_price"], position["current_price"]) and position["entry_price"]:
            position["unrealized_pnl_pct"] = ((position["current_price"] - position["entry_price"]) / position["entry_price"]) * 100.0

        stop = raw.get("stop_loss")
        if stop is None:
            stop = raw.get("stop")
        if stop is None:
            stop = nested.get("stop_loss")
        if stop is None:
            stop = metadata.get("stop_loss")
        position["stop_loss"] = _safe_float(stop)

        target = raw.get("target_price")
        if target is None:
            target = raw.get("target")
        if target is None:
            target = nested.get("target_price")
        if target is None:
            target = metadata.get("target_price")
        position["take_profit"] = _safe_float(target)

        position["strategy"] = raw.get("strategy") or nested.get("strategy")
        position["direction"] = raw.get("direction")
        position["entry_timestamp"] = raw.get("entry_timestamp") or raw.get("timestamp")
        position["state"] = str(meta.get("exit_mode") or raw.get("state") or position["state"])
        position["sector"] = raw.get("sector") or position["sector"]

    if trading_client and position["ticker"]:
        order_meta = _fetch_orders_for_ticker(trading_client, position["ticker"])
        if order_meta.get("stop_loss") is not None:
            position["stop_loss"] = order_meta["stop_loss"]
        if order_meta.get("take_profit") is not None:
            position["take_profit"] = order_meta["take_profit"]
        if order_meta.get("bracket_attached"):
            meta = {**meta, "bracket_attached": True}

    if meta:
        if meta.get("stop_loss") is not None:
            position["stop_loss"] = _safe_float(meta.get("stop_loss"))
        if meta.get("take_profit") is not None:
            position["take_profit"] = _safe_float(meta.get("take_profit"))
        if meta.get("target_price") is not None:
            position["take_profit"] = _safe_float(meta.get("target_price"))
        if meta.get("strategy") is not None:
            position["strategy"] = meta.get("strategy")
        if meta.get("direction") is not None:
            position["direction"] = meta.get("direction")
        if meta.get("sector") is not None:
            position["sector"] = meta.get("sector")
        if meta.get("exit_mode") is not None:
            position["state"] = str(meta.get("exit_mode"))

    if position["qty"] is not None:
        position["shares"] = abs(position["qty"])
        if not position["direction"]:
            if position["qty"] > 0:
                position["direction"] = "LONG"
            elif position["qty"] < 0:
                position["direction"] = "SHORT"
            else:
                position["direction"] = "CLOSED"

    # Canonical label for unnamed short-book positions.
    if not position["strategy"] and position["direction"] == "SHORT":
        position["strategy"] = "SHRT"

    has_stop = position["stop_loss"] is not None
    has_target = position["take_profit"] is not None
    if meta.get("bracket_attached"):
        position["protection_status"] = "BRKT"
    elif has_stop and has_target:
        position["protection_status"] = "S+T"
    elif has_stop:
        position["protection_status"] = "STOP"
    elif has_target:
        position["protection_status"] = "TGT"
    elif position["qty"] is not None:
        position["protection_status"] = "BARE"

    position["target_price"] = position["take_profit"]
    position["unrealized_pl"] = position["unrealized_pnl"]
    position["unrealized_pl_pct"] = position["unrealized_pnl_pct"]

    return position


def normalize_positions(raw_positions: list, meta_map: Optional[Dict] = None, source: str = "UNKNOWN", trading_client=None) -> list:
    meta_map = meta_map or {}
    normalized = []
    for raw in raw_positions:
        ticker = None
        if hasattr(raw, "symbol"):
            ticker = raw.symbol
        elif isinstance(raw, dict):
            ticker = raw.get("ticker") or raw.get("symbol")
        normalized.append(
            normalize_position(
                raw,
                meta=meta_map.get(ticker, {}),
                source=source,
                trading_client=trading_client,
            )
        )
    return normalized


def write_heartbeat(source: str, scan_ts: Optional[datetime] = None, extra: Optional[Dict] = None):
    scan_ts = scan_ts or datetime.now()
    extra = extra or {}
    heartbeat = {
        "source": source,
        "last_heartbeat_ts": datetime.now().isoformat(),
        "last_scan_ts": scan_ts.isoformat(),
        "scan_count": int(extra.get("scan_count", 0) or 0),
        "positions_count": int(extra.get("positions_count", 0) or 0),
        "market_status": extra.get("market_status", "UNKNOWN"),
        "is_trading": bool(extra.get("is_trading", False)),
        "version": str(extra.get("version", "3.0")),
    }
    for key, value in extra.items():
        if key not in heartbeat:
            heartbeat[key] = value
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            json.dump(heartbeat, f, indent=2)
    except Exception:
        pass


def read_heartbeat() -> Dict:
    if not os.path.exists(HEARTBEAT_FILE):
        return {}
    try:
        with open(HEARTBEAT_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return {}

    try:
        last_heartbeat = datetime.fromisoformat(data["last_heartbeat_ts"])
        age_seconds = max(0, int((datetime.now() - last_heartbeat).total_seconds()))
        data["age_seconds"] = age_seconds
        market_status = str(data.get("market_status", "UNKNOWN") or "UNKNOWN").upper()

        # Freshness thresholds must match daemon cadence.
        # OPEN: daemon writes a keep-alive heartbeat every 60s during inter-scan sleep.
        #   live_cutoff=150s gives 2.5x the write interval — robust to one missed tick.
        #   stale_cutoff=600s (10 min) flags genuine prolonged silence.
        # PRE_MARKET/CLOSED: daemon intentionally sleeps 30-60 minutes between checks.
        if market_status == "OPEN":
            live_cutoff = 150
            stale_cutoff = 600
        elif market_status in ("PRE_MARKET", "AFTER_HOURS", "AFTER-HRS"):
            live_cutoff = 45 * 60
            stale_cutoff = 90 * 60
        elif market_status in ("CLOSED",):
            live_cutoff = 75 * 60
            stale_cutoff = 135 * 60
        elif market_status in ("STOPPED",):
            live_cutoff = 0
            stale_cutoff = 0
        else:
            live_cutoff = 60
            stale_cutoff = 300

        if market_status == "STOPPED":
            data["status"] = "DEAD"
        elif age_seconds <= live_cutoff:
            data["status"] = "LIVE"
        elif age_seconds <= stale_cutoff:
            data["status"] = "STALE"
        else:
            data["status"] = "DEAD"
    except Exception:
        data["age_seconds"] = None
        data["status"] = "UNKN"

    try:
        last_scan = datetime.fromisoformat(data["last_scan_ts"])
        data["scan_age_seconds"] = max(0, int((datetime.now() - last_scan).total_seconds()))
    except Exception:
        data["scan_age_seconds"] = None

    return data


def get_daemon_status() -> str:
    heartbeat = read_heartbeat()
    if not heartbeat:
        return "NO DAEMON"
    return heartbeat.get("status", "UNKN")
