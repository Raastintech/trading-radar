"""
Execution Policy - Global Fail-Closed Trade Safety Checks
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional


FAIL_CLOSED_DEFAULT = True


@dataclass
class ExecutionDecision:
    allowed: bool
    reason: str
    required_actions: List[str] = field(default_factory=list)


def allow(reason: str = "OK", required_actions: Optional[List[str]] = None) -> ExecutionDecision:
    return ExecutionDecision(True, reason, required_actions or [])


def deny(reason: str, required_actions: Optional[List[str]] = None) -> ExecutionDecision:
    return ExecutionDecision(False, reason, required_actions or [])


def require_quote(quote) -> ExecutionDecision:
    if quote is None:
        return deny("QUOTE_MISSING", ["fetch_valid_quote"])

    if isinstance(quote, dict):
        bid = float(quote.get("bid_price", 0) or 0)
        ask = float(quote.get("ask_price", 0) or 0)
    else:
        bid = float(getattr(quote, "bid_price", 0) or 0)
        ask = float(getattr(quote, "ask_price", 0) or 0)
    if bid <= 0 or ask <= 0:
        return deny("QUOTE_INVALID_BID_ASK", ["require_positive_bid_ask"])
    if ask <= bid:
        return deny("QUOTE_CROSSED_OR_LOCKED", ["require_ask_gt_bid"])
    return allow("QUOTE_OK")


def require_spread_ok(bid: float, ask: float, vix_regime: str = "NORMAL") -> ExecutionDecision:
    try:
        bid = float(bid)
        ask = float(ask)
    except Exception:
        return deny("SPREAD_INPUT_INVALID", ["require_numeric_bid_ask"])

    if bid <= 0 or ask <= 0 or ask <= bid:
        return deny("SPREAD_PRICES_INVALID", ["require_positive_orderly_bid_ask"])

    spread_pct = ((ask - bid) / ((ask + bid) / 2.0)) * 100.0
    regime = (vix_regime or "NORMAL").upper()
    max_spread_by_regime = {
        "CALM": 0.50,
        "NORMAL": 0.50,
        "ELEVATED": 0.75,
        "HIGH": 1.00,
        "EXTREME": 1.50,
    }
    max_allowed = max_spread_by_regime.get(regime, 0.50)
    mid_price = (ask + bid) / 2.0
    price_cap = 0.20 if mid_price > 50 else max_allowed
    effective_max = min(max_allowed, price_cap)

    if spread_pct > effective_max:
        return deny(
            f"SPREAD_TOO_WIDE_{spread_pct:.2f}_GT_{effective_max:.2f}",
            ["wait_for_tighter_spread"],
        )
    return allow("SPREAD_OK")


def require_bracket_available(direction: str, stop, target, entry=None) -> ExecutionDecision:
    d = (direction or "").upper().strip()
    if d not in ("LONG", "SHORT"):
        return deny("DIRECTION_INVALID", ["use_LONG_or_SHORT"])

    try:
        s = float(stop)
        t = float(target)
    except Exception:
        return deny("BRACKET_LEVELS_MISSING_OR_INVALID", ["provide_numeric_stop_and_target"])

    if s <= 0 or t <= 0:
        return deny("BRACKET_LEVELS_NON_POSITIVE", ["use_positive_stop_target"])

    if d == "LONG" and not (s < t):
        return deny("BRACKET_LOGIC_INVALID_LONG", ["ensure_stop_below_target"])
    if d == "SHORT" and not (s > t):
        return deny("BRACKET_LOGIC_INVALID_SHORT", ["ensure_stop_above_target"])

    if entry is not None:
        try:
            e = float(entry)
        except Exception:
            return deny("BRACKET_ENTRY_INVALID", ["provide_numeric_entry"])
        if e <= 0:
            return deny("BRACKET_ENTRY_NON_POSITIVE", ["use_positive_entry"])
        if d == "LONG" and not (s < e < t):
            return deny("BRACKET_ENTRY_RELATION_INVALID_LONG", ["ensure_stop_lt_entry_lt_target"])
        if d == "SHORT" and not (t < e < s):
            return deny("BRACKET_ENTRY_RELATION_INVALID_SHORT", ["ensure_target_lt_entry_lt_stop"])
    return allow("BRACKET_OK")


def require_position_size_valid(qty) -> ExecutionDecision:
    try:
        q = int(qty)
    except Exception:
        return deny("QTY_INVALID", ["use_integer_qty"])
    if q <= 0:
        return deny("QTY_NON_POSITIVE", ["use_qty_gt_zero"])
    return allow("QTY_OK")


def require_shortable_if_short(
    ticker: str,
    direction: str,
    shortable_checker: Optional[Callable[[str], bool]] = None,
    allow_shorts: bool = True,
) -> ExecutionDecision:
    d = (direction or "").upper().strip()
    if d != "SHORT":
        return allow("NOT_SHORT")

    if not allow_shorts:
        return deny("SHORTS_DISABLED_BY_CONFIG", ["enable_shorts_or_use_long_only"])

    if shortable_checker is None:
        return deny("SHORTABILITY_UNVERIFIED", ["provide_shortable_checker"])

    try:
        ok = bool(shortable_checker(ticker))
    except Exception:
        ok = False

    if not ok:
        return deny("SHORT_NOT_BORROWABLE_OR_UNVERIFIED", ["use_shortable_symbol"])
    return allow("SHORTABLE_OK")


def require_not_halted_or_frozen(quote, max_quote_age_seconds: int = 30) -> ExecutionDecision:
    """
    Fail-closed guard against halts, LULD, stale quotes, and frozen liquidity.
    Works with dict quotes or SDK quote objects.
    """

    if quote is None:
        return deny("HALT_GUARD_QUOTE_MISSING", ["fetch_valid_quote"])

    def g(key, default=None):
        if isinstance(quote, dict):
            return quote.get(key, default)
        return getattr(quote, key, default)

    bid = float(g("bid_price", 0) or 0)
    ask = float(g("ask_price", 0) or 0)

    if bid <= 0 or ask <= 0 or ask <= bid:
        return deny("HALT_GUARD_QUOTE_INVALID", ["require_positive_orderly_bid_ask"])

    halted = g("halted", None)
    luld = g("luld", None) or g("limit_up_limit_down", None)
    trading_status = (g("trading_status", "") or g("status", "") or "").upper()

    if halted is True:
        return deny("HALTED", ["skip_symbol_until_resumed"])
    if isinstance(luld, str) and luld.upper() in ("LULD", "LIMIT", "LIMIT_UP", "LIMIT_DOWN"):
        return deny(f"LULD_{luld}", ["skip_symbol_until_stable"])
    if trading_status in ("HALTED", "H", "PAUSE", "SUSPENDED", "LULD"):
        return deny(f"TRADING_STATUS_{trading_status}", ["skip_symbol_until_resumed"])

    ts = g("timestamp", None) or g("t", None)
    now = datetime.now(timezone.utc)
    try:
        if isinstance(ts, datetime):
            qts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        elif isinstance(ts, (int, float)):
            qts = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        elif isinstance(ts, str):
            s = ts.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            qts = datetime.fromisoformat(s)
            qts = qts if qts.tzinfo else qts.replace(tzinfo=timezone.utc)
        else:
            qts = None
    except Exception:
        qts = None

    if qts is not None:
        age = (now - qts).total_seconds()
        if age > float(max_quote_age_seconds):
            return deny(
                f"QUOTE_STALE_{int(age)}S_GT_{max_quote_age_seconds}S",
                ["wait_for_fresh_quote"],
            )

    if abs(ask - bid) < 1e-9:
        return deny("FROZEN_MARKET_BID_EQ_ASK", ["skip_symbol_or_wait"])

    return allow("HALT_GUARD_OK")
