#!/usr/bin/env python3
"""
research/strategy_lab_portfolio.py - Phase 1H.2 Strategy Lab method audit.

Research-only portfolio construction and methodology audit layer for the
Strategy Research Lab. This module reuses Strategy Lab signal and trade-path
simulation, then applies explicit portfolio assumptions in separate sidecars.
It does not import execution, broker, governance, or live-capital modules and
does not create paper signals or trade proposals.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_research_lab as lab  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "research"
LOGS = ROOT / "logs"
DOCS = ROOT / "docs" / "research"

VERSION = "STRATEGY_LAB_METHOD_AUDIT_V1"

METHOD_JSON = CACHE / "strategy_lab_method_audit_latest.json"
METHOD_TXT = LOGS / "strategy_lab_method_audit_latest.txt"
METHOD_DOC = DOCS / "STRATEGY_LAB_METHOD_AUDIT.md"

PORTFOLIO_JSON = CACHE / "strategy_lab_portfolio_sim_latest.json"
PORTFOLIO_TXT = LOGS / "strategy_lab_portfolio_sim_latest.txt"
PORTFOLIO_DOC = DOCS / "STRATEGY_LAB_PORTFOLIO_SIM_RESULTS.md"

DECOMP_JSON = CACHE / "strategy_lab_drawdown_decomp_latest.json"
DECOMP_TXT = LOGS / "strategy_lab_drawdown_decomp_latest.txt"

EVAL_VARIANTS = (
    "PROD_SNIPER_CURRENT",
    "SNIPER_NO_ATR_CONTRACTION",
    "PROD_VOYAGER_CURRENT",
    "CORRECTION_LEADER_RECLAIM",
    "RECALL_SHADOW_RS_MOMENTUM",
    "RECALL_SHADOW_PULLBACK",
    "POWER_TREND_EXTENSION",
    "QQQ_TECH_TACTICAL_SHORT",
    "SIMPLE_SECTOR_RS",
    "SIMPLE_MOM_20_60",
    "RANDOM_LIQUID",
)

REPORT_VARIANTS = EVAL_VARIANTS + ("SPY", "QQQ", "cash")
LARGE_DD_VARIANTS = (
    "SNIPER_NO_ATR_CONTRACTION",
    "PROD_VOYAGER_CURRENT",
    "QQQ_TECH_TACTICAL_SHORT",
    "POWER_TREND_EXTENSION",
)
PRIMARY_WINDOW_NAMES = ("2024_available", "2025_available", "2026_ytd")


@dataclass(frozen=True)
class PortfolioConfig:
    max_open_positions: int = 5
    max_position_pct: float = 0.10
    max_sector_pct: float = 0.30
    max_gross_exposure_pct: float = 0.50
    apply_costs: bool = True
    no_margin: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class OpenPosition:
    trade: Dict[str, Any]
    ticker: str
    side: str
    sector: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    notional: float
    entry_cost: float
    exit_cost: float


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:+.2f}%"


def _round(value: Any, digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    try:
        value = float(value)
    except Exception:
        return None
    return round(value, digits) if math.isfinite(value) else None


def _annualized_return(total_return: float, start: str, end: str) -> Optional[float]:
    days = max(1, (pd.Timestamp(end) - pd.Timestamp(start)).days)
    if total_return <= -1.0:
        return -1.0
    return (1.0 + total_return) ** (365.25 / days) - 1.0


def _max_drawdown_from_equity(equity: Sequence[float]) -> float:
    if not equity:
        return 0.0
    peak = float(equity[0])
    max_dd = 0.0
    for value in equity:
        value = float(value)
        peak = max(peak, value)
        if peak > 0:
            max_dd = min(max_dd, value / peak - 1.0)
    return max_dd


def _sharpe(daily_returns: Sequence[float]) -> Optional[float]:
    vals = [float(x) for x in daily_returns if math.isfinite(float(x))]
    if len(vals) < 2:
        return None
    stdev = statistics.pstdev(vals)
    if stdev <= 0:
        return None
    return statistics.mean(vals) / stdev * math.sqrt(252)


def _sortino(daily_returns: Sequence[float]) -> Optional[float]:
    vals = [float(x) for x in daily_returns if math.isfinite(float(x))]
    downside = [x for x in vals if x < 0]
    if len(vals) < 2 or len(downside) < 2:
        return None
    stdev = statistics.pstdev(downside)
    if stdev <= 0:
        return None
    return statistics.mean(vals) / stdev * math.sqrt(252)


def _month_returns_from_equity(rows: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    if not rows:
        return {}
    by_month: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        by_month[str(row["date"])[:7]].append(float(row.get("daily_return") or 0.0))
    out: Dict[str, float] = {}
    for month, vals in sorted(by_month.items()):
        equity = 1.0
        for value in vals:
            equity *= 1.0 + value
        out[month] = round(equity - 1.0, 6)
    return out


def _best_worst_month(month_returns: Dict[str, float]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not month_returns:
        return None, None
    worst_key = min(month_returns, key=month_returns.get)
    best_key = max(month_returns, key=month_returns.get)
    return (
        {"month": worst_key, "return": month_returns[worst_key]},
        {"month": best_key, "return": month_returns[best_key]},
    )


def _date_range(start: str, end: str) -> List[pd.Timestamp]:
    return lab.d.trading_dates_between(start, end)


def _trade_span_calendar(trades: Sequence[Dict[str, Any]], start: str, end: str) -> List[pd.Timestamp]:
    max_exit = pd.Timestamp(end)
    for trade in trades:
        if trade.get("exit_date"):
            max_exit = max(max_exit, pd.Timestamp(trade["exit_date"]))
    return _date_range(start, str(max_exit.date()))


def _primary_window_span() -> Tuple[str, str, List[Dict[str, str]], List[Dict[str, str]]]:
    windows, unavailable = lab.full_windows()
    primary = [w for w in windows if w["name"] in PRIMARY_WINDOW_NAMES]
    if not primary:
        primary = windows
    start = str(min(pd.Timestamp(w["start"]) for w in primary).date())
    end = str(max(pd.Timestamp(w["end"]) for w in primary).date())
    return start, end, primary, unavailable


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def collect_trades_for_span(
    start: str,
    end: str,
    *,
    variants: Sequence[str] = EVAL_VARIANTS,
    params: lab.StrategyParams = lab.StrategyParams(),
    config: lab.BacktestConfig = lab.BacktestConfig(),
    cost_model: Dict[str, Any] = lab.BASE_COST,
) -> Dict[str, Any]:
    """Generate Strategy Lab trades for a non-overlapping signal span.

    This mirrors lab.run_backtest_window but returns every simulated trade so
    portfolio construction can be audited without changing Phase 1H.1 output.
    """
    dates = _date_range(start, end)
    signals_by_variant: Dict[str, List[lab.Signal]] = {v: [] for v in variants}
    ticker_days = 0
    skipped_dates: List[Dict[str, str]] = []
    for asof in dates:
        features = lab.d.compute_universe_features_asof(
            asof,
            mode=config.universe_mode,
            cap=config.universe_cap,
            min_bars=config.min_bars,
            min_avg_dvol=params.min_avg_dvol,
        )
        ticker_days += len(features)
        if not features:
            skipped_dates.append({
                "date": str(pd.Timestamp(asof).date()),
                "reason": "no_retained_ticker_meets_min_bars_and_liquidity_on_this_date",
            })
        daily = lab.generate_signals_for_date(
            asof,
            variants=variants,
            params=params,
            config=config,
            features=features,
        )
        for variant, signals in daily.items():
            signals_by_variant.setdefault(variant, []).extend(signals)

    trades_by_variant: Dict[str, List[Dict[str, Any]]] = {v: [] for v in variants}
    for variant in variants:
        for sig in signals_by_variant.get(variant, []):
            trade = lab.simulate_trade(
                sig,
                params=params,
                cost_model=cost_model,
                entry_timing=config.entry_timing,
            )
            if trade is None:
                continue
            trade["source_signal_window_start"] = start
            trade["source_signal_window_end"] = end
            trades_by_variant[variant].append(trade)
    return {
        "start": start,
        "end": end,
        "dates_evaluated": len(dates),
        "ticker_days_evaluated": ticker_days,
        "skipped_dates": skipped_dates[:100],
        "skipped_date_count": len(skipped_dates),
        "signals_generated": {k: len(v) for k, v in signals_by_variant.items()},
        "trades_by_variant": trades_by_variant,
    }


def _concurrency_stats(trades: Sequence[Dict[str, Any]], calendar: Sequence[pd.Timestamp]) -> Dict[str, Any]:
    if not trades or not calendar:
        return {
            "average_concurrent_positions": 0.0,
            "max_concurrent_positions": 0,
            "duplicate_open_ticker_days": 0,
            "max_duplicate_same_ticker_open": 0,
            "exposure_pct_uncapped": 0.0,
        }
    counts = []
    duplicate_days = 0
    max_dup = 0
    exposure_days = 0
    for date in calendar:
        open_trades = [
            t for t in trades
            if pd.Timestamp(t["entry_date"]) <= date <= pd.Timestamp(t["exit_date"])
        ]
        n_open = len(open_trades)
        counts.append(n_open)
        exposure_days += n_open
        tickers = Counter(t["ticker"] for t in open_trades)
        if any(v > 1 for v in tickers.values()):
            duplicate_days += 1
            max_dup = max(max_dup, max(tickers.values()))
    return {
        "average_concurrent_positions": round(statistics.mean(counts), 4) if counts else 0.0,
        "max_concurrent_positions": max(counts) if counts else 0,
        "duplicate_open_ticker_days": duplicate_days,
        "max_duplicate_same_ticker_open": max_dup,
        "exposure_pct_uncapped": round(exposure_days / max(1, len(calendar)), 6),
    }


def independent_trade_metrics(trades: Sequence[Dict[str, Any]], *, start: str, end: str) -> Dict[str, Any]:
    ordered = sorted(trades, key=lambda t: (t.get("exit_date") or "", t.get("signal_date") or "", t.get("ticker") or ""))
    returns = [float(t["net_return"]) for t in ordered]
    equity = 1.0
    equity_path = [equity]
    for value in returns:
        equity *= 1.0 + value
        equity_path.append(equity)
    total_return = equity - 1.0
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    calendar = _trade_span_calendar(ordered, start, end)
    conc = _concurrency_stats(ordered, calendar)
    by_exit_month: Dict[str, List[float]] = defaultdict(list)
    for trade in ordered:
        by_exit_month[str(trade.get("exit_date", ""))[:7]].append(float(trade["net_return"]))
    month_returns: Dict[str, float] = {}
    for month, vals in by_exit_month.items():
        month_equity = 1.0
        for value in vals:
            month_equity *= 1.0 + value
        month_returns[month] = round(month_equity - 1.0, 6)
    worst_month, best_month = _best_worst_month(month_returns)
    days = max(1, (pd.Timestamp(end) - pd.Timestamp(start)).days)
    years = max(days / 365.25, 1 / 365.25)
    stdev = statistics.pstdev(returns) if len(returns) > 1 else None
    downside = [r for r in returns if r < 0]
    down_stdev = statistics.pstdev(downside) if len(downside) > 1 else None
    avg = statistics.mean(returns) if returns else None
    return {
        "mode": "independent_trade",
        "trade_count": len(ordered),
        "total_return": round(total_return, 6),
        "cagr": _round(_annualized_return(total_return, start, end)),
        "max_drawdown": round(_max_drawdown_from_equity(equity_path), 6),
        "win_rate": round(len(wins) / len(returns), 4) if returns else None,
        "expectancy": round(avg, 6) if avg is not None else None,
        "sharpe": round((avg / stdev), 4) if avg is not None and stdev and stdev > 0 else None,
        "sortino": round((avg / down_stdev), 4) if avg is not None and down_stdev and down_stdev > 0 else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else (999.0 if wins else None),
        "exposure_pct": conc["exposure_pct_uncapped"],
        "turnover": round(len(ordered) / years, 4),
        "average_concurrent_positions": conc["average_concurrent_positions"],
        "max_concurrent_positions": conc["max_concurrent_positions"],
        "duplicate_open_ticker_days": conc["duplicate_open_ticker_days"],
        "max_duplicate_same_ticker_open": conc["max_duplicate_same_ticker_open"],
        "worst_month": worst_month,
        "best_month": best_month,
        "notes": [
            "Current Strategy Lab style: every signal is measured independently.",
            "Total return and drawdown are not deployable portfolio figures because each trade is implicitly full-size.",
        ],
    }


def equal_weight_basket_metrics(trades: Sequence[Dict[str, Any]], *, start: str, end: str) -> Dict[str, Any]:
    by_signal_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_signal_date[str(trade.get("signal_date"))].append(trade)
    basket_rows: List[Dict[str, Any]] = []
    equity = 1.0
    equity_path = [equity]
    for signal_date in sorted(by_signal_date):
        group = by_signal_date[signal_date]
        basket_return = statistics.mean(float(t["net_return"]) for t in group)
        equity *= 1.0 + basket_return
        basket_rows.append({
            "date": signal_date,
            "basket_return": basket_return,
            "signal_count": len(group),
        })
        equity_path.append(equity)
    total_return = equity - 1.0
    returns = [r["basket_return"] for r in basket_rows]
    wins = [r for r in returns if r > 0]
    month_returns: Dict[str, List[float]] = defaultdict(list)
    for row in basket_rows:
        month_returns[str(row["date"])[:7]].append(float(row["basket_return"]))
    month_compounded = {}
    for month, vals in month_returns.items():
        month_equity = 1.0
        for value in vals:
            month_equity *= 1.0 + value
        month_compounded[month] = round(month_equity - 1.0, 6)
    worst_month, best_month = _best_worst_month(month_compounded)
    days = max(1, (pd.Timestamp(end) - pd.Timestamp(start)).days)
    years = max(days / 365.25, 1 / 365.25)
    calendar = _date_range(start, end)
    stdev = statistics.pstdev(returns) if len(returns) > 1 else None
    downside = [r for r in returns if r < 0]
    down_stdev = statistics.pstdev(downside) if len(downside) > 1 else None
    avg = statistics.mean(returns) if returns else None
    avg_signals = statistics.mean([r["signal_count"] for r in basket_rows]) if basket_rows else 0.0
    return {
        "mode": "equal_weight_basket",
        "basket_count": len(basket_rows),
        "trade_count": len(trades),
        "total_return": round(total_return, 6),
        "cagr": _round(_annualized_return(total_return, start, end)),
        "max_drawdown": round(_max_drawdown_from_equity(equity_path), 6),
        "win_rate": round(len(wins) / len(returns), 4) if returns else None,
        "expectancy": round(avg, 6) if avg is not None else None,
        "sharpe": round((avg / stdev), 4) if avg is not None and stdev and stdev > 0 else None,
        "sortino": round((avg / down_stdev), 4) if avg is not None and down_stdev and down_stdev > 0 else None,
        "exposure_pct": round(len(basket_rows) / max(1, len(calendar)), 6),
        "turnover": round(len(basket_rows) / years, 4),
        "average_concurrent_positions": round(avg_signals, 4),
        "max_concurrent_positions": max((r["signal_count"] for r in basket_rows), default=0),
        "worst_month": worst_month,
        "best_month": best_month,
        "notes": [
            "All signals on one signal date share equal basket capital.",
            "Open-basket overlap is not modeled; this is a date-bucket construction check, not a deployable portfolio.",
        ],
    }


@lru_cache(maxsize=400_000)
def _close_asof(ticker: str, date_str: str) -> Optional[float]:
    frame = lab.d.load_price_frame_asof(ticker, date_str)
    if frame is None or frame.empty:
        return None
    value = float(frame.iloc[-1]["close"])
    return value if math.isfinite(value) and value > 0 else None


def _position_mark_value(pos: OpenPosition, date: pd.Timestamp) -> float:
    if date >= pos.exit_date:
        raw = float(pos.trade.get("raw_return") or 0.0)
        return pos.notional * (1.0 + raw)
    price = _close_asof(pos.ticker, str(date.date()))
    if price is None:
        price = pos.entry_price
    if pos.side == "short":
        raw = (pos.entry_price - price) / pos.entry_price
    else:
        raw = price / pos.entry_price - 1.0
    return pos.notional * (1.0 + raw)


def _portfolio_equity(cash: float, positions: Sequence[OpenPosition], date: pd.Timestamp) -> float:
    return cash + sum(_position_mark_value(pos, date) for pos in positions)


def _open_exposure(positions: Sequence[OpenPosition], equity: float) -> Dict[str, Any]:
    gross = sum(pos.notional for pos in positions)
    by_sector: Dict[str, float] = defaultdict(float)
    by_side: Dict[str, float] = defaultdict(float)
    for pos in positions:
        by_sector[pos.sector] += pos.notional
        by_side[pos.side] += pos.notional
    denom = max(equity, 1e-9)
    return {
        "gross": gross / denom,
        "long": by_side.get("long", 0.0) / denom,
        "short": by_side.get("short", 0.0) / denom,
        "sector": {k: v / denom for k, v in by_sector.items()},
    }


def realistic_portfolio_metrics(
    trades: Sequence[Dict[str, Any]],
    *,
    start: str,
    end: str,
    config: PortfolioConfig = PortfolioConfig(),
) -> Dict[str, Any]:
    candidates = [
        t for t in trades
        if t.get("entry_date") and t.get("exit_date") and _f(t.get("entry_price")) > 0
    ]
    candidates.sort(key=lambda t: (t["entry_date"], t.get("signal_date") or "", -_f(t.get("score")), t.get("ticker") or ""))
    calendar = _trade_span_calendar(candidates, start, end)
    by_entry: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in candidates:
        by_entry[str(pd.Timestamp(trade["entry_date"]).date())].append(trade)

    cash = 1.0
    positions: List[OpenPosition] = []
    last_equity = 1.0
    daily_rows: List[Dict[str, Any]] = []
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    turnover_notional = 0.0

    for date in calendar:
        date_key = str(date.date())
        equity_for_sizing = max(last_equity, 1e-9)

        for trade in by_entry.get(date_key, []):
            ticker = str(trade["ticker"])
            side = str(trade.get("side") or "long")
            sector = str(trade.get("sector") or "UNKNOWN")
            current_exposure = _open_exposure(positions, equity_for_sizing)
            sector_pct = current_exposure["sector"].get(sector, 0.0)
            gross_pct = current_exposure["gross"]
            cost_fraction = max(0.0, float(trade.get("cost_return") or 0.0)) if config.apply_costs else 0.0
            entry_cost_fraction = cost_fraction / 2.0
            exit_cost_fraction = cost_fraction - entry_cost_fraction
            reasons = []
            if len(positions) >= config.max_open_positions:
                reasons.append("max_positions")
            if any(pos.ticker == ticker for pos in positions):
                reasons.append("duplicate_ticker_open")
            if sector_pct >= config.max_sector_pct - 1e-12:
                reasons.append("sector_cap")
            if gross_pct >= config.max_gross_exposure_pct - 1e-12:
                reasons.append("gross_exposure_cap")
            if reasons:
                rejected.append({"trade": trade, "reasons": reasons, "date": date_key})
                continue

            sector_room = max(0.0, (config.max_sector_pct - sector_pct) * equity_for_sizing)
            gross_room = max(0.0, (config.max_gross_exposure_pct - gross_pct) * equity_for_sizing)
            # Optional per-trade downsizing hook (Phase 1I.2 vol-scaled sizing):
            # trades may carry size_weight in (0, 1]; absent key keeps the
            # historical fixed-fraction behavior exactly.
            size_weight = min(1.0, max(0.0, _f(trade.get("size_weight"), 1.0)))
            notional = min(config.max_position_pct * size_weight * equity_for_sizing, sector_room, gross_room)
            entry_cost = notional * entry_cost_fraction
            if config.no_margin and cash < notional + entry_cost - 1e-12:
                rejected.append({"trade": trade, "reasons": ["cash_cap_no_margin"], "date": date_key})
                continue
            if notional <= 1e-9:
                rejected.append({"trade": trade, "reasons": ["zero_available_notional"], "date": date_key})
                continue

            pos = OpenPosition(
                trade=trade,
                ticker=ticker,
                side=side,
                sector=sector,
                entry_date=pd.Timestamp(trade["entry_date"]),
                exit_date=pd.Timestamp(trade["exit_date"]),
                entry_price=float(trade["entry_price"]),
                exit_price=float(trade["exit_price"]),
                notional=notional,
                entry_cost=entry_cost,
                exit_cost=notional * exit_cost_fraction,
            )
            cash -= notional + pos.entry_cost
            positions.append(pos)
            turnover_notional += notional
            accepted.append({
                "ticker": ticker,
                "side": side,
                "sector": sector,
                "signal_date": trade.get("signal_date"),
                "entry_date": trade.get("entry_date"),
                "exit_date": trade.get("exit_date"),
                "raw_return": trade.get("raw_return"),
                "net_return": trade.get("net_return"),
                "score": trade.get("score"),
                "notional_pct_at_entry": round(notional / equity_for_sizing, 6),
            })

        still_open: List[OpenPosition] = []
        for pos in positions:
            if pos.exit_date <= date:
                cash += _position_mark_value(pos, date) - pos.exit_cost
            else:
                still_open.append(pos)
        positions = still_open

        equity = _portfolio_equity(cash, positions, date)
        daily_return = equity / last_equity - 1.0 if last_equity > 0 else 0.0
        exposure = _open_exposure(positions, max(equity, 1e-9))
        daily_rows.append({
            "date": date_key,
            "equity": round(equity, 8),
            "daily_return": round(daily_return, 8),
            "open_positions": len(positions),
            "gross_exposure": round(exposure["gross"], 6),
            "long_exposure": round(exposure["long"], 6),
            "short_exposure": round(exposure["short"], 6),
        })
        last_equity = max(equity, 1e-12)

    equity_values = [float(row["equity"]) for row in daily_rows] or [1.0]
    daily_returns = [float(row["daily_return"]) for row in daily_rows[1:]]
    month_returns = _month_returns_from_equity(daily_rows)
    worst_month, best_month = _best_worst_month(month_returns)
    accepted_returns = [float(t["net_return"]) for t in accepted if t.get("net_return") is not None]
    wins = [r for r in accepted_returns if r > 0]
    days = max(1, (pd.Timestamp(end) - pd.Timestamp(start)).days)
    years = max(days / 365.25, 1 / 365.25)
    reject_reasons = Counter(reason for row in rejected for reason in row["reasons"])
    total_return = equity_values[-1] - 1.0
    return {
        "mode": "realistic_portfolio",
        "portfolio_config": config.as_dict(),
        "candidate_trade_count": len(candidates),
        "accepted_trade_count": len(accepted),
        "rejected_trade_count": len(rejected),
        "reject_reasons": dict(sorted(reject_reasons.items())),
        "total_return": round(total_return, 6),
        "cagr": _round(_annualized_return(total_return, start, end)),
        "max_drawdown": round(_max_drawdown_from_equity(equity_values), 6),
        "sharpe": _round(_sharpe(daily_returns), 4),
        "sortino": _round(_sortino(daily_returns), 4),
        "win_rate": round(len(wins) / len(accepted_returns), 4) if accepted_returns else None,
        "expectancy": round(statistics.mean(accepted_returns), 6) if accepted_returns else None,
        "exposure_pct": round(statistics.mean([float(r["gross_exposure"]) for r in daily_rows]), 6) if daily_rows else 0.0,
        "average_concurrent_positions": round(statistics.mean([int(r["open_positions"]) for r in daily_rows]), 4) if daily_rows else 0.0,
        "max_concurrent_positions": max((int(r["open_positions"]) for r in daily_rows), default=0),
        "turnover": round(turnover_notional / years, 6),
        "worst_month": worst_month,
        "best_month": best_month,
        "final_equity": round(equity_values[-1], 8),
        "daily_rows": daily_rows,
        "accepted_sample": accepted[:25],
        "rejected_sample": [
            {
                "ticker": r["trade"].get("ticker"),
                "signal_date": r["trade"].get("signal_date"),
                "entry_date": r["trade"].get("entry_date"),
                "reasons": r["reasons"],
            }
            for r in rejected[:25]
        ],
    }


def benchmark_metrics(symbol: str, *, start: str, end: str, cost_model: Dict[str, Any] = lab.BASE_COST) -> Dict[str, Any]:
    if symbol.lower() == "cash":
        calendar = _date_range(start, end)
        rows = [{"date": str(pd.Timestamp(d).date()), "equity": 1.0, "daily_return": 0.0} for d in calendar]
        return {
            "mode": "benchmark",
            "symbol": "cash",
            "total_return": 0.0,
            "cagr": 0.0,
            "max_drawdown": 0.0,
            "sharpe": None,
            "sortino": None,
            "win_rate": None,
            "exposure_pct": 0.0,
            "turnover": 0.0,
            "average_concurrent_positions": 0.0,
            "worst_month": None,
            "best_month": None,
            "daily_rows": rows,
        }
    df = lab.d.get_forward_window(symbol, start, 3000)
    df = df[df.index <= pd.Timestamp(end)]
    if df.empty:
        return benchmark_metrics("cash", start=start, end=end, cost_model=cost_model)
    entry = lab._entry_price(df.iloc[0], "next_open")
    if entry <= 0:
        return benchmark_metrics("cash", start=start, end=end, cost_model=cost_model)
    cost = lab.round_trip_cost_fraction(cost_model, "long", len(df))
    rows = []
    prev_equity = 1.0
    for idx, row in df.iterrows():
        equity = float(row["close"]) / entry
        daily_return = equity / prev_equity - 1.0 if prev_equity > 0 else 0.0
        rows.append({
            "date": str(pd.Timestamp(idx).date()),
            "equity": round(equity, 8),
            "daily_return": round(daily_return, 8),
        })
        prev_equity = equity
    if rows:
        rows[-1]["equity"] = round(max(0.0, rows[-1]["equity"] - cost), 8)
        if len(rows) > 1:
            prev = rows[-2]["equity"]
            rows[-1]["daily_return"] = round(rows[-1]["equity"] / prev - 1.0 if prev else 0.0, 8)
    equity_values = [float(row["equity"]) for row in rows]
    daily_returns = [float(row["daily_return"]) for row in rows[1:]]
    month_returns = _month_returns_from_equity(rows)
    worst_month, best_month = _best_worst_month(month_returns)
    total_return = equity_values[-1] - 1.0
    return {
        "mode": "benchmark",
        "symbol": symbol,
        "start": str(df.index[0].date()),
        "end": str(df.index[-1].date()),
        "total_return": round(total_return, 6),
        "cagr": _round(_annualized_return(total_return, str(df.index[0].date()), str(df.index[-1].date()))),
        "max_drawdown": round(_max_drawdown_from_equity(equity_values), 6),
        "sharpe": _round(_sharpe(daily_returns), 4),
        "sortino": _round(_sortino(daily_returns), 4),
        "win_rate": None,
        "exposure_pct": 1.0,
        "turnover": 0.0,
        "average_concurrent_positions": 1.0,
        "worst_month": worst_month,
        "best_month": best_month,
        "daily_rows": rows,
    }


def _prior_phase_1h1_table() -> Dict[str, Any]:
    decision = _load_json(CACHE / "strategy_lab_decision_latest.json") or {}
    rows = {}
    for row in decision.get("table") or []:
        rows[row.get("variant")] = {
            "trade_count": row.get("trade_count"),
            "expectancy_base_cost": row.get("expectancy_base_cost"),
            "rel_spy": row.get("rel_spy"),
            "rel_qqq": row.get("rel_qqq"),
            "max_drawdown": row.get("max_drawdown"),
            "final_verdict": row.get("final_verdict"),
        }
    return {
        "generated_at": decision.get("generated_at"),
        "paper_shadow": decision.get("paper_shadow"),
        "rows": rows,
    }


def _methodology_verdict(variant: str, independent: Dict[str, Any], realistic: Dict[str, Any]) -> Dict[str, Any]:
    if variant in {"SPY", "QQQ", "cash"}:
        return {"label": "BENCHMARK", "reasons": ["benchmark row, not a strategy candidate"]}
    if variant == "RANDOM_LIQUID":
        return {
            "label": "REJECT",
            "reasons": ["random-liquid control is a baseline, not a strategy edge"],
        }
    reasons: List[str] = []
    n = int(independent.get("trade_count") or 0)
    ind_exp = _f(independent.get("expectancy"))
    ind_dd = _f(independent.get("max_drawdown"))
    real_return = _f(realistic.get("total_return"))
    real_dd = _f(realistic.get("max_drawdown"))
    accepted = int(realistic.get("accepted_trade_count") or 0)
    if variant == "PROD_SNIPER_CURRENT":
        reasons.append("highest-quality candidate but low flow in non-overlapping primary span")
        if n < 80 or accepted < 40:
            return {"label": "HIGH_QUALITY_LOW_FLOW", "reasons": reasons + ["sample/accepted flow remains too low"]}
        return {"label": "NEED_MORE_DATA", "reasons": reasons}
    if n == 0 or accepted == 0:
        return {"label": "REJECT", "reasons": ["no usable trades under realistic constraints"]}
    if ind_exp <= 0:
        return {"label": "REJECT", "reasons": ["independent-trade expectancy is not positive"]}
    if real_return <= 0:
        return {"label": "REJECT", "reasons": ["realistic capped portfolio return is not positive"]}
    if ind_dd < -0.50:
        return {
            "label": "PROMISING_BUT_PORTFOLIO_RISK",
            "reasons": [
                "realistic caps improve the result, but independent-trade drawdown is not acceptable",
                "result is partly dependent on portfolio construction constraints",
            ],
        }
    if real_dd < -0.25:
        return {"label": "PROMISING_BUT_PORTFOLIO_RISK", "reasons": ["realistic portfolio drawdown remains above risk tolerance"]}
    if ind_dd < -0.75 and real_dd > -0.25:
        reasons.append("large independent drawdown is materially reduced by exposure caps")
    if accepted < 40:
        return {"label": "NEED_MORE_DATA", "reasons": reasons + ["accepted realistic-portfolio sample below 40"]}
    if real_return < 0.05:
        return {"label": "NEED_MORE_DATA", "reasons": reasons + ["edge survives but return is economically thin after cash drag"]}
    return {
        "label": "BACKTEST_EDGE_DETECTED_BUT_NEEDS_PAPER",
        "reasons": reasons + ["both independent and realistic views are positive, but this audit alone cannot promote"],
    }


def build_method_audit() -> Dict[str, Any]:
    full = _load_json(CACHE / "strategy_lab_full_windows_latest.json") or {}
    windows = full.get("windows") or {}
    return {
        "kind": "strategy_lab_method_audit",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "source_artifacts": {
            "strategy_lab_full_windows_latest": full.get("generated_at"),
            "strategy_lab_decision_latest": (_load_json(CACHE / "strategy_lab_decision_latest.json") or {}).get("generated_at"),
        },
        "audited_files": [
            "research/strategy_research_lab.py",
            "research/strategy_lab_data.py",
            "research/strategy_walk_forward.py",
            "research/strategy_threshold_sweep.py",
            "research/strategy_lab_decision.py",
        ],
        "backtest_assumptions": {
            "entry_price_assumption": "next trading bar open by default; falls back to close if open is missing or invalid",
            "exit_price_assumption": "intraday stop or target at configured stop/target price, otherwise close on max_hold day",
            "position_sizing_assumption": "independent-trade summaries implicitly treat every signal as a full independent trade; no capital allocation exists in Phase 1H.1 summaries",
            "compounding_assumption": "max drawdown compounds the sequence of trade returns in exit-order, not a dated portfolio equity curve",
            "overlapping_trades_handling": "overlapping signals are all retained and measured independently",
            "max_concurrent_positions": "none in Phase 1H.1 independent-trade mode",
            "duplicate_ticker_handling": "no duplicate-open ticker guard in Phase 1H.1 independent-trade mode",
            "sector_concentration_handling": "reported as concentration counters only; no sector cap is enforced in Phase 1H.1",
            "portfolio_exposure_cap": "none in Phase 1H.1 independent-trade mode",
            "benchmark_comparison_method": "per-trade net return minus SPY/QQQ return over each trade's entry-to-exit window; buy-hold rows are separate window-level trades",
            "drawdown_calculation_method": "lab._max_drawdown compounds independent trade returns; benchmark buy-hold drawdown in Phase 1H.1 is not daily mark-to-market",
            "slippage_cost_model": "round-trip bps model: slippage both ways, one spread charge, optional commission both ways, and annualized borrow for shorts",
            "short_return_signing": "short raw return is (entry - exit) / entry; SPY/QQQ relative returns are sign-flipped for short comparisons",
        },
        "window_construction": {
            "exact_full_windows": [
                {"name": name, "start": w.get("start"), "end": w.get("end"), "trading_dates": w.get("trading_dates")}
                for name, w in windows.items()
            ],
            "overlap_finding": (
                "Exact-full includes yearly windows plus recent/rolling diagnostics, so aggregate trade counts and expectancy "
                "intentionally double-count calendar periods. Portfolio correction should use non-overlapping primary windows "
                "for deployable comparison and keep rolling windows diagnostic."
            ),
            "primary_non_overlapping_windows": list(PRIMARY_WINDOW_NAMES),
        },
        "methodology_findings": [
            "Large Phase 1H.1 drawdowns are partly a construction artifact because independent trades can create uncapped overlapping exposure.",
            "The strategy logic still matters: portfolio caps can reduce drawdown, but they cannot make negative expectancy or poor timing acceptable.",
            "Benchmark fairness requires dated portfolio returns, daily benchmark drawdown, exposure/cash adjustment, and a separate fully invested benchmark table.",
            "Per-trade expectancy should not be compared directly with SPY/QQQ buy-hold total return.",
        ],
        "safety": lab.safety_confirmations(),
    }


def build_portfolio_sim() -> Dict[str, Any]:
    start, end, primary_windows, unavailable = _primary_window_span()
    params = lab.StrategyParams()
    config = lab.BacktestConfig(universe_cap=140, date_stride=1)
    portfolio_config = PortfolioConfig()
    collected = collect_trades_for_span(
        start,
        end,
        variants=EVAL_VARIANTS,
        params=params,
        config=config,
        cost_model=lab.BASE_COST,
    )
    trades_by_variant: Dict[str, List[Dict[str, Any]]] = collected["trades_by_variant"]
    results: Dict[str, Any] = {}
    for variant in EVAL_VARIANTS:
        trades = trades_by_variant.get(variant, [])
        independent = independent_trade_metrics(trades, start=start, end=end)
        basket = equal_weight_basket_metrics(trades, start=start, end=end)
        realistic = realistic_portfolio_metrics(trades, start=start, end=end, config=portfolio_config)
        results[variant] = {
            "independent_trade": independent,
            "equal_weight_basket": basket,
            "realistic_portfolio": realistic,
            "methodology_verdict": _methodology_verdict(variant, independent, realistic),
        }
    for symbol in ("SPY", "QQQ", "cash"):
        bench = benchmark_metrics(symbol, start=start, end=end, cost_model=lab.BASE_COST)
        results[symbol] = {
            "benchmark": bench,
            "realistic_portfolio": bench,
            "methodology_verdict": _methodology_verdict(symbol, {}, bench),
        }

    benchmark_table = {
        symbol: {
            key: results[symbol]["benchmark"].get(key)
            for key in ("total_return", "cagr", "max_drawdown", "sharpe", "sortino", "exposure_pct", "worst_month", "best_month")
        }
        for symbol in ("SPY", "QQQ", "cash")
    }
    variant_table = {}
    for variant in EVAL_VARIANTS:
        ind = results[variant]["independent_trade"]
        real = results[variant]["realistic_portfolio"]
        variant_table[variant] = {
            "independent_trade_count": ind.get("trade_count"),
            "independent_expectancy": ind.get("expectancy"),
            "independent_max_drawdown": ind.get("max_drawdown"),
            "realistic_accepted": real.get("accepted_trade_count"),
            "realistic_total_return": real.get("total_return"),
            "realistic_cagr": real.get("cagr"),
            "realistic_max_drawdown": real.get("max_drawdown"),
            "realistic_exposure_pct": real.get("exposure_pct"),
            "realistic_avg_concurrent": real.get("average_concurrent_positions"),
            "methodology_label": results[variant]["methodology_verdict"]["label"],
        }

    return {
        "kind": "strategy_lab_portfolio_sim",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "source_phase": "Phase 1H.2",
        "signal_window": {"start": start, "end": end},
        "primary_non_overlapping_windows": primary_windows,
        "unavailable_windows": unavailable,
        "config": config.as_dict(),
        "params": params.as_dict(),
        "portfolio_config": portfolio_config.as_dict(),
        "cost_model": lab.BASE_COST,
        "collection": {k: v for k, v in collected.items() if k != "trades_by_variant"},
        "prior_phase_1h1_windowed_aggregate": _prior_phase_1h1_table(),
        "results": results,
        "variant_table": variant_table,
        "benchmark_table": benchmark_table,
        "benchmark_fairness": {
            "fair_comparison_method": [
                "Use the same non-overlapping signal date window for strategies and benchmarks.",
                "Report strategy portfolio total return/CAGR/maxDD from a dated equity curve with cash drag.",
                "Report SPY/QQQ buy-hold over the same start/end dates with daily mark-to-market drawdown.",
                "Compare risk-adjusted return and exposure-adjusted behavior, not per-trade expectancy against buy-hold total return.",
                "For theme-heavy strategies, SPY/QQQ remain broad baselines, but sector ETF context such as QQQ/SMH/XLK should be a diagnostic benchmark, not the sole promotion gate.",
            ],
            "current_comparison_flaws": [
                "Phase 1H.1 independent-trade maxDD is not a capital-constrained drawdown.",
                "Phase 1H.1 exact-full aggregate double-counts periods by mixing yearly and rolling windows.",
                "Strategy per-trade expectancy and buy-hold total return are different units.",
                "Buy-hold benchmark maxDD in Phase 1H.1 is summarized as one trade, so daily benchmark drawdown is understated.",
                "Strategies often sit partly or mostly in cash under realistic caps while SPY/QQQ are fully invested.",
            ],
            "corrected_benchmark_table": benchmark_table,
        },
        "paper_shadow": {
            "proposal_created": False,
            "status": "NO_VARIANT_READY_FOR_PAPER_SHADOW",
            "reason": "Methodology-aware portfolio audit only; no variant can be promoted from this audit alone.",
        },
        "safety": lab.safety_confirmations(),
    }


def _variant_source_breakdown(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_month: Dict[str, List[float]] = defaultdict(list)
    by_sector: Dict[str, List[float]] = defaultdict(list)
    by_theme: Dict[str, List[float]] = defaultdict(list)
    by_ticker: Dict[str, List[float]] = defaultdict(list)
    reliability = Counter()
    stop_hits = 0
    target_hits = 0
    worst_trades = sorted(trades, key=lambda t: float(t.get("net_return") or 0.0))[:10]
    for trade in trades:
        ret = float(trade.get("net_return") or 0.0)
        by_month[str(trade.get("exit_date", ""))[:7]].append(ret)
        by_sector[str(trade.get("sector") or "UNKNOWN")].append(ret)
        by_theme[str(trade.get("theme") or "unknown")].append(ret)
        by_ticker[str(trade.get("ticker") or "UNKNOWN")].append(ret)
        data_rel = trade.get("data_reliability") or {}
        if isinstance(data_rel, dict):
            reliability[str(data_rel.get("metadata") or "UNKNOWN_METADATA")] += 1
        else:
            reliability["UNKNOWN_RELIABILITY"] += 1
        stop_hits += int(bool(trade.get("stop_hit")))
        target_hits += int(bool(trade.get("target_hit")))

    def compounded(vals: Sequence[float]) -> float:
        equity = 1.0
        for value in vals:
            equity *= 1.0 + value
        return equity - 1.0

    worst_month = None
    if by_month:
        rows = {k: compounded(v) for k, v in by_month.items()}
        key = min(rows, key=rows.get)
        worst_month = {"month": key, "return": round(rows[key], 6), "trades": len(by_month[key])}

    def worst_bucket(rows: Dict[str, List[float]]) -> Optional[Dict[str, Any]]:
        if not rows:
            return None
        scored = {k: compounded(v) for k, v in rows.items()}
        key = min(scored, key=scored.get)
        return {
            "name": key,
            "return": round(scored[key], 6),
            "trades": len(rows[key]),
            "average_return": round(statistics.mean(rows[key]), 6),
        }

    return {
        "worst_month": worst_month,
        "worst_sector": worst_bucket(by_sector),
        "worst_theme": worst_bucket(by_theme),
        "worst_ticker": worst_bucket(by_ticker),
        "ticker_concentration": Counter(t.get("ticker") for t in trades).most_common(10),
        "sector_concentration": Counter(t.get("sector") or "UNKNOWN" for t in trades).most_common(10),
        "theme_concentration": Counter(t.get("theme") or "unknown" for t in trades).most_common(10),
        "stop_hit_rate": round(stop_hits / len(trades), 4) if trades else None,
        "target_hit_rate": round(target_hits / len(trades), 4) if trades else None,
        "data_reliability_counts": dict(sorted(reliability.items())),
        "worst_10_trades": [
            {
                "ticker": t.get("ticker"),
                "side": t.get("side"),
                "signal_date": t.get("signal_date"),
                "entry_date": t.get("entry_date"),
                "exit_date": t.get("exit_date"),
                "net_return": t.get("net_return"),
                "exit_reason": t.get("exit_reason"),
                "sector": t.get("sector"),
                "theme": t.get("theme"),
            }
            for t in worst_trades
        ],
    }


def build_drawdown_decomp(portfolio: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if portfolio is None:
        portfolio = _load_json(PORTFOLIO_JSON)
    if not portfolio:
        portfolio = build_portfolio_sim()
    start = portfolio["signal_window"]["start"]
    end = portfolio["signal_window"]["end"]
    config = lab.BacktestConfig(universe_cap=140, date_stride=1)
    collected = collect_trades_for_span(
        start,
        end,
        variants=LARGE_DD_VARIANTS,
        params=lab.StrategyParams(),
        config=config,
        cost_model=lab.BASE_COST,
    )
    rows: Dict[str, Any] = {}
    for variant in LARGE_DD_VARIANTS:
        trades = collected["trades_by_variant"].get(variant, [])
        independent = independent_trade_metrics(trades, start=start, end=end)
        realistic = (((portfolio.get("results") or {}).get(variant) or {}).get("realistic_portfolio") or {})
        breakdown = _variant_source_breakdown(trades)
        dd_delta = _f(realistic.get("max_drawdown")) - _f(independent.get("max_drawdown"))
        overlap_artifact = bool(
            independent.get("max_concurrent_positions", 0) > 5
            or independent.get("exposure_pct", 0.0) > 1.0
            or independent.get("duplicate_open_ticker_days", 0) > 0
        )
        short_squeeze = variant == "QQQ_TECH_TACTICAL_SHORT" and _f(breakdown.get("stop_hit_rate")) > 0.50
        rows[variant] = {
            "independent_trade_drawdown": independent.get("max_drawdown"),
            "realistic_portfolio_drawdown": realistic.get("max_drawdown"),
            "drawdown_improvement_from_caps": round(dd_delta, 6),
            "overlapping_trades": {
                "caused_or_amplified": overlap_artifact,
                "max_concurrent_positions": independent.get("max_concurrent_positions"),
                "average_concurrent_positions": independent.get("average_concurrent_positions"),
                "uncapped_exposure_pct": independent.get("exposure_pct"),
            },
            "duplicate_ticker": {
                "caused_or_amplified": independent.get("duplicate_open_ticker_days", 0) > 0,
                "duplicate_open_ticker_days": independent.get("duplicate_open_ticker_days"),
                "max_duplicate_same_ticker_open": independent.get("max_duplicate_same_ticker_open"),
            },
            "one_bad_regime_or_month": breakdown.get("worst_month"),
            "sector_or_theme": {
                "worst_sector": breakdown.get("worst_sector"),
                "worst_theme": breakdown.get("worst_theme"),
                "sector_concentration": breakdown.get("sector_concentration"),
                "theme_concentration": breakdown.get("theme_concentration"),
            },
            "repeated_same_ticker": {
                "worst_ticker": breakdown.get("worst_ticker"),
                "ticker_concentration": breakdown.get("ticker_concentration"),
            },
            "no_exposure_cap": {
                "caused_or_amplified": overlap_artifact,
                "evidence": "Realistic capped portfolio maxDD should be read against independent trade maxDD.",
            },
            "high_cost_sensitivity": {
                "average_cost_return": round(statistics.mean([float(t.get("cost_return") or 0.0) for t in trades]), 6) if trades else None,
                "stop_hit_rate": breakdown.get("stop_hit_rate"),
                "target_hit_rate": breakdown.get("target_hit_rate"),
            },
            "short_squeeze_or_gap_risk": {
                "caused_or_amplified": short_squeeze,
                "applies": variant == "QQQ_TECH_TACTICAL_SHORT",
                "stop_hit_rate": breakdown.get("stop_hit_rate"),
                "worst_10_trades": breakdown.get("worst_10_trades"),
            },
            "stale_or_unreliable_data": {
                "caused_or_amplified": False,
                "data_reliability_counts": breakdown.get("data_reliability_counts"),
                "note": "Price bars are point-in-time sliced; sector/theme metadata remains current-metadata approximation.",
            },
        }
    return {
        "kind": "strategy_lab_drawdown_decomp",
        "version": VERSION,
        "generated_at": _utc_now(),
        "research_only": True,
        "status": "RESEARCH_ONLY",
        "signal_window": {"start": start, "end": end},
        "variants": rows,
        "safety": lab.safety_confirmations(),
    }


def render_method_text(res: Dict[str, Any]) -> List[str]:
    a = res["backtest_assumptions"]
    lines = [
        f"STRATEGY LAB METHOD AUDIT - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"entry_price={a['entry_price_assumption']}",
        f"exit_price={a['exit_price_assumption']}",
        f"position_sizing={a['position_sizing_assumption']}",
        f"compounding={a['compounding_assumption']}",
        f"overlap={a['overlapping_trades_handling']}",
        f"portfolio_cap={a['portfolio_exposure_cap']}",
        "",
        "Findings:",
    ]
    lines.extend(f"- {x}" for x in res.get("methodology_findings") or [])
    return lines


def render_method_doc(res: Dict[str, Any]) -> str:
    a = res["backtest_assumptions"]
    lines = [
        "# Strategy Lab Method Audit",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        "## Assumptions Found",
        "",
        "| Item | Finding |",
        "|---|---|",
    ]
    for key, value in a.items():
        lines.append(f"| {key} | {value} |")
    lines += [
        "",
        "## Methodology Findings",
        "",
    ]
    lines.extend(f"- {x}" for x in res.get("methodology_findings") or [])
    lines += [
        "",
        "## Window Construction",
        "",
        res["window_construction"]["overlap_finding"],
        "",
        "Primary non-overlapping windows: `" + ", ".join(PRIMARY_WINDOW_NAMES) + "`.",
        "",
        "## Safety",
        "",
        "No live trading, broker orders, paper signals, trade proposals, production threshold changes, Gatekeeper/Veto changes, live-capital changes, or historical evidence mutation.",
        "",
    ]
    return "\n".join(lines)


def render_portfolio_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"STRATEGY LAB PORTFOLIO SIM - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        f"signal_window={res['signal_window']['start']}..{res['signal_window']['end']}",
        f"paper_shadow={res['paper_shadow']['status']}",
        "",
        f"{'variant':32s} {'label':38s} {'ind_n':>6s} {'ind_exp':>9s} {'ind_dd':>9s} {'real_ret':>9s} {'real_dd':>9s} {'accepted':>8s}",
    ]
    for variant, row in res["variant_table"].items():
        lines.append(
            f"{variant:32s} {row['methodology_label']:38s} "
            f"{int(row.get('independent_trade_count') or 0):6d} "
            f"{_pct(row.get('independent_expectancy')):>9s} "
            f"{_pct(row.get('independent_max_drawdown')):>9s} "
            f"{_pct(row.get('realistic_total_return')):>9s} "
            f"{_pct(row.get('realistic_max_drawdown')):>9s} "
            f"{int(row.get('realistic_accepted') or 0):8d}"
        )
    return lines


def render_portfolio_doc(res: Dict[str, Any]) -> str:
    lines = [
        "# Strategy Lab Portfolio Simulation Results",
        "",
        f"Generated: {res['generated_at']}",
        "",
        lab.RESEARCH_DISCLAIMER,
        "",
        f"Signal window: `{res['signal_window']['start']}` to `{res['signal_window']['end']}`.",
        "",
        "This is the corrected primary comparison over non-overlapping windows. Rolling/recent exact-full windows remain diagnostic because they overlap the yearly windows.",
        "",
        "## Variant Summary",
        "",
        "| Variant | Method Label | Independent N | Independent Exp | Independent MaxDD | Realistic Accepted | Realistic Return | Realistic CAGR | Realistic MaxDD | Exposure | Avg Concurrent |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, row in res["variant_table"].items():
        lines.append(
            f"| {variant} | {row['methodology_label']} | {row['independent_trade_count']} | "
            f"{_pct(row['independent_expectancy'])} | {_pct(row['independent_max_drawdown'])} | "
            f"{row['realistic_accepted']} | {_pct(row['realistic_total_return'])} | "
            f"{_pct(row['realistic_cagr'])} | {_pct(row['realistic_max_drawdown'])} | "
            f"{_pct(row['realistic_exposure_pct'])} | {row['realistic_avg_concurrent']} |"
        )
    lines += [
        "",
        "## Benchmark Table",
        "",
        "| Benchmark | Total Return | CAGR | MaxDD | Sharpe | Sortino | Exposure | Worst Month | Best Month |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for symbol, row in res["benchmark_table"].items():
        lines.append(
            f"| {symbol} | {_pct(row.get('total_return'))} | {_pct(row.get('cagr'))} | "
            f"{_pct(row.get('max_drawdown'))} | {row.get('sharpe')} | {row.get('sortino')} | "
            f"{_pct(row.get('exposure_pct'))} | "
            f"{(row.get('worst_month') or {}).get('month', 'n/a')} {_pct((row.get('worst_month') or {}).get('return'))} | "
            f"{(row.get('best_month') or {}).get('month', 'n/a')} {_pct((row.get('best_month') or {}).get('return'))} |"
        )
    lines += [
        "",
        "## Fairness Review",
        "",
    ]
    for item in res["benchmark_fairness"]["current_comparison_flaws"]:
        lines.append(f"- {item}")
    lines += [
        "",
        "## Decision Update",
        "",
        res["paper_shadow"]["status"],
        "",
        "No variant is promoted by this audit. A paper-shadow proposal remains disallowed unless independent-trade, realistic-portfolio, exact walk-forward, and operator review all agree.",
        "",
    ]
    return "\n".join(lines)


def render_decomp_text(res: Dict[str, Any]) -> List[str]:
    lines = [
        f"STRATEGY LAB DRAWDOWN DECOMP - {res['generated_at']}",
        lab.RESEARCH_DISCLAIMER,
        f"signal_window={res['signal_window']['start']}..{res['signal_window']['end']}",
        "",
        f"{'variant':32s} {'ind_dd':>9s} {'real_dd':>9s} {'max_conc':>8s} {'dupe_days':>10s} {'worst_month':>20s}",
    ]
    for variant, row in res["variants"].items():
        wm = row.get("one_bad_regime_or_month") or {}
        lines.append(
            f"{variant:32s} {_pct(row.get('independent_trade_drawdown')):>9s} "
            f"{_pct(row.get('realistic_portfolio_drawdown')):>9s} "
            f"{int((row.get('overlapping_trades') or {}).get('max_concurrent_positions') or 0):8d} "
            f"{int((row.get('duplicate_ticker') or {}).get('duplicate_open_ticker_days') or 0):10d} "
            f"{wm.get('month', 'n/a')} {_pct(wm.get('return')):>9s}"
        )
    return lines


def write_method_outputs(res: Dict[str, Any]) -> None:
    METHOD_JSON.parent.mkdir(parents=True, exist_ok=True)
    METHOD_TXT.parent.mkdir(parents=True, exist_ok=True)
    METHOD_DOC.parent.mkdir(parents=True, exist_ok=True)
    METHOD_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    METHOD_TXT.write_text("\n".join(render_method_text(res)) + "\n", encoding="utf-8")
    METHOD_DOC.write_text(render_method_doc(res), encoding="utf-8")


def write_portfolio_outputs(res: Dict[str, Any]) -> None:
    PORTFOLIO_JSON.parent.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_TXT.parent.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_DOC.parent.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    PORTFOLIO_TXT.write_text("\n".join(render_portfolio_text(res)) + "\n", encoding="utf-8")
    PORTFOLIO_DOC.write_text(render_portfolio_doc(res), encoding="utf-8")


def write_decomp_outputs(res: Dict[str, Any]) -> None:
    DECOMP_JSON.parent.mkdir(parents=True, exist_ok=True)
    DECOMP_TXT.parent.mkdir(parents=True, exist_ok=True)
    DECOMP_JSON.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    DECOMP_TXT.write_text("\n".join(render_decomp_text(res)) + "\n", encoding="utf-8")
