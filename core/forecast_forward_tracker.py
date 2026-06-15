"""
core/forecast_forward_tracker.py
================================

Forward tracking ledger for the Market & Sector Regime Forecaster (Phase 1)
and the Single-Stock Research Lens (Phase 3).

Phase 5 is *tracking + attribution + workflow* only.  This module never:
  - mutates forecast or lens scoring logic
  - calls providers (it reads cached parquets / artifacts)
  - touches paper evidence, governance, sleeve allocation, or execution
  - feeds outcome data back into the forecast

It writes JSONL ledgers under ``data/state/`` and provides:

  * ``append_forecast_snapshot(forecast_artifact)``        — idempotent
  * ``append_stock_lens_snapshot(lens_artifact)``          — idempotent
  * ``resolve_forecast_outcomes()``                        — fill matured rows
  * ``resolve_stock_lens_outcomes()``                      — fill matured rows
  * ``load_forecast_log()`` / ``load_stock_lens_log()``    — read helpers
  * ``forecast_summary()`` / ``stock_lens_summary()``      — analytics
  * ``write_forecast_summary()`` / ``write_stock_lens_summary()``

Honesty guardrail:  resolvers populate raw forward returns regardless of
whether the call was correct.  Hit-rate logic is computed only at summary
time and uses the recorded forward returns — never re-derived from the
forecast itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- locations -------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "data" / "state"
LOG_DIR_DEFAULT = ROOT / "logs"
CACHE_DIR_DEFAULT = ROOT / "cache"

FORECAST_LOG_PATH    = STATE_DIR / "regime_forecast_forward_log.jsonl"
STOCK_LENS_LOG_PATH  = STATE_DIR / "stock_lens_forward_log.jsonl"

PRICE_DIRS_DEFAULT: Tuple[Path, ...] = (
    CACHE_DIR_DEFAULT / "research" / "regime_validation_prices",
    CACHE_DIR_DEFAULT / "prices",
)

FORECAST_HORIZONS_DAYS    = (1, 5, 10, 20)
STOCK_LENS_HORIZONS_DAYS  = (5, 10, 20)

# Trading-day to calendar-day fudge (US equities ≈ 1.45 calendar / trading
# day). Used when expiring "still open" — we need at least horizon trading
# days of forward bars before resolving.
_TRADING_TO_CALENDAR = 1.5

# --- helpers ---------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _ensure_state_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "::".join(str(p) for p in parts)
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception as exc:
            logger.warning("skipping bad jsonl line in %s: %s", path, exc)
    return rows


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    _ensure_state_dir(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")
    tmp.replace(path)


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    _ensure_state_dir(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


# --- price loader ----------------------------------------------------------


def _load_price_frame(symbol: str, *, price_dirs: Iterable[Path] = PRICE_DIRS_DEFAULT):
    """
    Load a daily-bar frame for ``symbol`` from cached parquets only.

    Phase 1G.2: prior behaviour returned the first non-empty parquet in
    ``price_dirs``. ``cache/research/regime_validation_prices/`` is a
    backtest-history cache that freezes once warmed, so the first-match
    code path read a multi-year history with a stale tail and missed
    the forward bars needed to mature recent forecast / lens snapshots
    (root cause: every ``fields_updated=0`` resolver pass since
    2026-04-27). Fix: read every available parquet for ``symbol`` and
    merge them, keeping the later (fresher) row on any duplicated
    index. This preserves the long history from one cache and the
    fresh tail from the other.

    Reading is provider-free; this is what makes the resolver safe to
    run from cron without disturbing the live cache. Returns ``None``
    if pandas / pyarrow are unavailable or no parquet exists for the
    symbol in any directory.
    """
    try:
        import pandas as pd  # noqa: F401
    except Exception:
        return None
    import pandas as pd

    frames = []
    for d in price_dirs:
        p = Path(d) / f"{symbol}.parquet"
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p)
        except Exception as exc:
            logger.debug("parquet read failed for %s: %s", p, exc)
            continue
        if df is None or len(df) == 0:
            continue
        if "close" not in df.columns and "Close" in df.columns:
            df = df.rename(columns={"Close": "close"})
        df.index = pd.to_datetime(df.index)
        frames.append(df)

    if not frames:
        return None
    if len(frames) == 1:
        return frames[0].sort_index()

    merged = pd.concat(frames, axis=0)
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()


def _anchor_close(frame, anchor_date: date) -> Optional[float]:
    """Last close on or before ``anchor_date``."""
    if frame is None or len(frame) == 0:
        return None
    import pandas as pd
    cutoff = pd.Timestamp(anchor_date) + pd.Timedelta(hours=23, minutes=59)
    sub = frame.loc[frame.index <= cutoff]
    if sub.empty:
        return None
    val = sub["close"].iloc[-1]
    try:
        return float(val)
    except Exception:
        return None


def _close_after_n_trading_days(frame, anchor_date: date, n_days: int) -> Optional[Tuple[float, date]]:
    """
    Close ``n_days`` trading days after ``anchor_date``.  If the frame doesn't
    have that many forward bars yet the row is treated as still-open.
    """
    if frame is None or len(frame) == 0 or n_days <= 0:
        return None
    import pandas as pd
    cutoff = pd.Timestamp(anchor_date) + pd.Timedelta(hours=23, minutes=59)
    fwd = frame.loc[frame.index > cutoff]
    if len(fwd) < n_days:
        return None
    target = fwd.iloc[n_days - 1]
    target_date = target.name
    if hasattr(target_date, "date"):
        target_date = target_date.date()
    try:
        return float(target["close"]), target_date
    except Exception:
        return None


def _forward_return_pct(frame, anchor_date: date, n_days: int) -> Optional[float]:
    base = _anchor_close(frame, anchor_date)
    if base is None or base == 0:
        return None
    fwd = _close_after_n_trading_days(frame, anchor_date, n_days)
    if fwd is None:
        return None
    end_close, _ = fwd
    return round((end_close / base - 1.0) * 100.0, 4)


def _forward_min_low_pct(frame, anchor_date: date, n_days: int) -> Optional[float]:
    """Largest drawdown (min low / anchor close − 1) over the forward window."""
    if frame is None or len(frame) == 0:
        return None
    base = _anchor_close(frame, anchor_date)
    if base is None or base == 0:
        return None
    import pandas as pd
    cutoff = pd.Timestamp(anchor_date) + pd.Timedelta(hours=23, minutes=59)
    fwd = frame.loc[frame.index > cutoff].head(n_days)
    if len(fwd) < n_days:
        return None
    if "low" in fwd.columns:
        min_val = float(fwd["low"].min())
    else:
        min_val = float(fwd["close"].min())
    return round((min_val / base - 1.0) * 100.0, 4)


def _forward_max_high_pct(frame, anchor_date: date, n_days: int) -> Optional[float]:
    if frame is None or len(frame) == 0:
        return None
    base = _anchor_close(frame, anchor_date)
    if base is None or base == 0:
        return None
    import pandas as pd
    cutoff = pd.Timestamp(anchor_date) + pd.Timedelta(hours=23, minutes=59)
    fwd = frame.loc[frame.index > cutoff].head(n_days)
    if len(fwd) < n_days:
        return None
    if "high" in fwd.columns:
        max_val = float(fwd["high"].max())
    else:
        max_val = float(fwd["close"].max())
    return round((max_val / base - 1.0) * 100.0, 4)


# --- snapshot extraction ---------------------------------------------------


def _safe_get(d: Optional[Dict[str, Any]], *keys: str, default: Any = None) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _parse_iso_date(value: Any) -> Optional[date]:
    if not value:
        return None
    s = str(value)[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _resolve_forecast_anchor(forecast: Dict[str, Any]) -> Tuple[date, str, Optional[str]]:
    """
    Phase 6 anchor resolution.

    Resolution order (most specific → least):
      1. artifact ``anchor_date``                  (Phase 6 explicit)
      2. artifact ``market_data_last_bar_date``    (Phase 6 explicit)
      3. ``market_trend.SPY.last_bar_date``        (legacy hint)
      4. ``frames_summary.SPY.last_bar``           (text-format fallback)
      5. ``built_at`` date                         (last-resort)

    Returns ``(anchor_date, source, warning)``.  ``warning`` is a short
    human-readable string when we had to fall back; this is recorded on the
    snapshot so downstream readers can flag less-trustworthy rows.
    """
    explicit = _parse_iso_date(forecast.get("anchor_date"))
    if explicit is not None:
        artifact_warning = forecast.get("anchor_warning")
        if artifact_warning:
            return explicit, "artifact_anchor_date", str(artifact_warning)
        return explicit, "artifact_anchor_date", None

    market_last = _parse_iso_date(forecast.get("market_data_last_bar_date"))
    if market_last is not None:
        return market_last, "artifact_market_data_last_bar_date", None

    spy = _safe_get(forecast, "market_trend", "SPY") or {}
    spy_last = _parse_iso_date(spy.get("last_bar_date") or spy.get("last_date"))
    if spy_last is not None:
        return spy_last, "legacy_market_trend_spy_last_bar_date", "fell back to legacy SPY last_bar_date"

    fs_spy = _safe_get(forecast, "frames_summary", "SPY") or {}
    fs_last = _parse_iso_date(fs_spy.get("last_bar"))
    if fs_last is not None:
        return fs_last, "legacy_frames_summary_spy_last_bar", "fell back to frames_summary SPY last_bar"

    built = _parse_iso_date(forecast.get("built_at"))
    if built is not None:
        return built, "legacy_built_at", "no anchor_date — using built_at calendar date (may be off by a trading day)"

    return date.today(), "today_fallback", "no anchor available — using today (forward returns will be unreliable)"


# Back-compat alias for older callers.
def _forecast_anchor_date(forecast: Dict[str, Any]) -> date:
    return _resolve_forecast_anchor(forecast)[0]


def _extract_forecast_snapshot(forecast: Dict[str, Any]) -> Dict[str, Any]:
    head = forecast.get("headline") or {}
    sec = forecast.get("sector_rotation") or {}
    sf = forecast.get("strategy_favorability") or {}
    dq = forecast.get("data_quality") or {}
    probs = list(forecast.get("regime_probabilities") or [])

    leaders = list(sec.get("leading") or [])
    improving = list(sec.get("improving") or [])
    weakening = list(sec.get("weakening") or [])
    defensive = list(sec.get("defensive") or [])
    # Predicted top basket: leaders → improving → first available rows
    rows = list(sec.get("rows") or [])

    def _rank_key(r: Dict[str, Any]) -> float:
        try:
            return float(r.get("rs_10d_pct") or r.get("rs_5d_pct") or 0.0)
        except Exception:
            return 0.0

    ranked = sorted(rows, key=_rank_key, reverse=True)
    top_basket = leaders or improving or [r.get("sector") for r in ranked[:3] if r.get("sector")]
    bottom_basket = weakening or defensive or [r.get("sector") for r in ranked[-3:] if r.get("sector")]
    top_basket = [s for s in top_basket if s][:3]
    bottom_basket = [s for s in bottom_basket if s][:3]

    anchor, anchor_source, anchor_warning = _resolve_forecast_anchor(forecast)
    snap = {
        "kind": "forecast",
        "snapshot_id": _stable_id(
            "fc",
            forecast.get("built_at") or _now_iso(),
            anchor.isoformat(),
            forecast.get("version") or "v?",
        ),
        "version": forecast.get("version"),
        "phase": forecast.get("phase"),
        "logged_at": _now_iso(),
        "built_at": forecast.get("built_at"),
        "anchor_date": anchor.isoformat(),
        "anchor_source": anchor_source,
        "anchor_warning": anchor_warning,
        "spy_last_bar_date": forecast.get("spy_last_bar_date"),
        "data_freshness_status": forecast.get("data_freshness_status"),
        "current_regime": head.get("current_regime"),
        "bias_5d": head.get("bias_5d"),
        "bias_10d": head.get("bias_10d"),
        "confidence": head.get("confidence"),
        "main_invalidation": head.get("main_invalidation"),
        "regime_probabilities": [
            {"regime": r.get("regime"), "probability": float(r.get("probability") or 0.0)}
            for r in probs
        ],
        "trend_score": forecast.get("trend_score"),
        "constructive_mass": forecast.get("constructive_mass"),
        "defensive_mass": forecast.get("defensive_mass"),
        "sector_leaders": leaders,
        "sector_improving": improving,
        "sector_weakening": weakening,
        "sector_defensive": defensive,
        "predicted_top_basket": top_basket,
        "predicted_bottom_basket": bottom_basket,
        "strategy_favorability": {
            name: {"stance": (sf.get(name) or {}).get("stance")}
            for name in ("VOYAGER", "SNIPER_V6", "SHORT_A", "ALPHA_DISCOVERY")
            if sf.get(name)
        },
        "data_quality": {
            "spy_bars": dq.get("spy_bars"),
            "sector_frames_available": dq.get("sector_frames_available"),
            "missing_layers": list(dq.get("missing_layers") or []),
        },
        "outcomes": {},        # filled by resolver
        "status": "open",      # → "matured" after final horizon resolved
    }
    return snap


def _extract_stock_lens_snapshot(lens: Dict[str, Any]) -> Dict[str, Any]:
    layers = lens.get("layers") or {}
    horizon = lens.get("horizon_view") or {}
    scores = lens.get("scores") or {}
    market = layers.get("market_regime") or {}
    sector = layers.get("sector") or {}
    tech = layers.get("technicals") or {}
    entry = layers.get("entry_validator") or {}
    alpha = layers.get("alpha") or {}
    posture = layers.get("posture") or {}
    options = layers.get("options") or {}
    social = layers.get("social") or {}

    ticker = (lens.get("ticker") or "").upper()
    built_at = lens.get("built_at") or _now_iso()
    try:
        anchor = date.fromisoformat(str(built_at)[:10])
    except Exception:
        anchor = date.today()

    snap = {
        "kind": "stock_lens",
        "snapshot_id": _stable_id("lens", ticker, built_at, anchor.isoformat()),
        "ticker": ticker,
        "logged_at": _now_iso(),
        "built_at": built_at,
        "anchor_date": anchor.isoformat(),
        "label": lens.get("label"),
        "confidence": lens.get("confidence"),
        "horizon_view_5d": horizon.get("5d"),
        "horizon_view_10d": horizon.get("10d"),
        "horizon_view_20d": horizon.get("20d"),
        "scores": {
            "composite": scores.get("composite"),
            "bullish_score": scores.get("bullish_score"),
            "bearish_score": scores.get("bearish_score"),
            "entry_quality_score": scores.get("entry_quality_score"),
            "risk_score": scores.get("risk_score"),
        },
        "hard_caps_fired": list(lens.get("hard_caps_fired") or []),
        "layers": {
            "market": {"view": market.get("view"), "regime": market.get("regime"),
                        "confidence": market.get("confidence")},
            "sector": {"view": sector.get("view"), "etf": sector.get("etf"),
                        "rs_10d_pct": sector.get("rs_vs_spy_10d_pct")},
            "tech":   {"view": tech.get("view"), "state": tech.get("state"),
                        "extended": tech.get("extended"),
                        "rs_10d_pct": tech.get("rs_vs_spy_10d_pct")},
            "entry":  {"view": entry.get("view"), "actionable_now": entry.get("actionable_now")},
            "alpha":  {"view": alpha.get("view"), "track": alpha.get("track"),
                        "tier": alpha.get("tier"), "alpha_score": alpha.get("alpha_score")},
            "posture":{"view": posture.get("view")},
            "options":{"view": options.get("view"), "available": options.get("available")},
            "social": {"view": social.get("view")},
        },
        "data_quality_notes": list(lens.get("data_quality_notes") or []),
        "outcomes": {},
        "status": "open",
    }
    return snap


# --- public: snapshot append ----------------------------------------------


def append_forecast_snapshot(
    forecast: Dict[str, Any],
    *,
    log_path: Path = FORECAST_LOG_PATH,
) -> str:
    """
    Append a canonical forecast snapshot to the forward-tracking ledger.
    Idempotent: a snapshot with the same ``built_at + anchor_date + version``
    is not duplicated.

    Returns the snapshot_id (existing or newly written).
    """
    snap = _extract_forecast_snapshot(forecast or {})
    sid = snap["snapshot_id"]
    existing = _read_jsonl(log_path)
    if any(r.get("snapshot_id") == sid for r in existing):
        logger.debug("forecast snapshot %s already logged; skipping", sid)
        return sid
    _append_jsonl(log_path, snap)
    return sid


def append_stock_lens_snapshot(
    lens: Dict[str, Any],
    *,
    log_path: Path = STOCK_LENS_LOG_PATH,
) -> str:
    snap = _extract_stock_lens_snapshot(lens or {})
    sid = snap["snapshot_id"]
    existing = _read_jsonl(log_path)
    if any(r.get("snapshot_id") == sid for r in existing):
        logger.debug("stock lens snapshot %s already logged; skipping", sid)
        return sid
    _append_jsonl(log_path, snap)
    return sid


# --- read helpers ----------------------------------------------------------


def load_forecast_log(*, log_path: Path = FORECAST_LOG_PATH) -> List[Dict[str, Any]]:
    return _read_jsonl(log_path)


def load_stock_lens_log(*, log_path: Path = STOCK_LENS_LOG_PATH) -> List[Dict[str, Any]]:
    return _read_jsonl(log_path)


# --- resolvers -------------------------------------------------------------


_BULL_BIAS_TOKENS = ("constructive", "bullish", "buy", "bull")
_BEAR_BIAS_TOKENS = ("risk-off", "bearish", "stress", "defensive")


def _bias_direction(label: Optional[str]) -> int:
    """+1 bullish, -1 bearish, 0 neutral / unknown."""
    if not label:
        return 0
    s = str(label).lower()
    if any(t in s for t in _BULL_BIAS_TOKENS):
        return +1
    if any(t in s for t in _BEAR_BIAS_TOKENS):
        return -1
    return 0


def _hit_from_return(direction: int, ret_pct: Optional[float]) -> Optional[bool]:
    if ret_pct is None or direction == 0:
        return None
    if direction > 0:
        return ret_pct > 0.0
    return ret_pct < 0.0


def resolve_forecast_outcomes(
    *,
    log_path: Path = FORECAST_LOG_PATH,
    price_dirs: Iterable[Path] = PRICE_DIRS_DEFAULT,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Walk the forecast log and resolve any horizon outcome whose forward bars
    are now available in the cache.

    Modifies the JSONL in place (atomic rewrite via tmp file).  Returns a
    summary dict.
    """
    rows = _read_jsonl(log_path)
    if not rows:
        return {"rows": 0, "matured": 0, "still_open": 0, "errors": 0}

    today = today or date.today()
    horizons = list(FORECAST_HORIZONS_DAYS)

    cache: Dict[str, Any] = {}

    def _frame(sym: str):
        if sym not in cache:
            cache[sym] = _load_price_frame(sym, price_dirs=price_dirs)
        return cache[sym]

    matured = 0
    still_open = 0
    errors = 0
    updated = 0

    for row in rows:
        try:
            anchor_str = row.get("anchor_date")
            if not anchor_str:
                still_open += 1
                continue
            anchor = date.fromisoformat(str(anchor_str)[:10])

            outcomes = row.get("outcomes") or {}

            for sym in ("SPY", "QQQ", "IWM"):
                f = _frame(sym)
                if f is None:
                    continue
                for h in horizons:
                    key = f"{sym.lower()}_{h}d_return_pct"
                    if outcomes.get(key) is not None:
                        continue
                    ret = _forward_return_pct(f, anchor, h)
                    if ret is not None:
                        outcomes[key] = ret
                        updated += 1

            spy_f = _frame("SPY")
            if spy_f is not None:
                for h in horizons:
                    key = f"spy_{h}d_max_drawdown_pct"
                    if outcomes.get(key) is None:
                        dd = _forward_min_low_pct(spy_f, anchor, h)
                        if dd is not None:
                            outcomes[key] = dd
                            updated += 1

            top_basket = list(row.get("predicted_top_basket") or [])
            bot_basket = list(row.get("predicted_bottom_basket") or [])
            for h in horizons:
                top_key = f"top_basket_{h}d_return_pct"
                bot_key = f"bottom_basket_{h}d_return_pct"
                spread_key = f"top_minus_bottom_{h}d_pct"
                if outcomes.get(top_key) is None and top_basket:
                    rets = [_forward_return_pct(_frame(s), anchor, h) for s in top_basket]
                    rets = [r for r in rets if r is not None]
                    if len(rets) == len(top_basket):
                        outcomes[top_key] = round(sum(rets) / len(rets), 4)
                        updated += 1
                if outcomes.get(bot_key) is None and bot_basket:
                    rets = [_forward_return_pct(_frame(s), anchor, h) for s in bot_basket]
                    rets = [r for r in rets if r is not None]
                    if len(rets) == len(bot_basket):
                        outcomes[bot_key] = round(sum(rets) / len(rets), 4)
                        updated += 1
                if outcomes.get(spread_key) is None and outcomes.get(top_key) is not None and outcomes.get(bot_key) is not None:
                    outcomes[spread_key] = round(outcomes[top_key] - outcomes[bot_key], 4)
                    outcomes[f"leaders_beat_laggards_{h}d"] = bool(outcomes[spread_key] > 0.0)

            # hits using bias_5d / bias_10d
            dir5 = _bias_direction(row.get("bias_5d"))
            dir10 = _bias_direction(row.get("bias_10d"))
            spy5 = outcomes.get("spy_5d_return_pct")
            spy10 = outcomes.get("spy_10d_return_pct")
            outcomes["spy_5d_hit"] = _hit_from_return(dir5, spy5)
            outcomes["spy_10d_hit"] = _hit_from_return(dir10, spy10)

            row["outcomes"] = outcomes

            final_h = max(horizons)
            need_calendar = int(final_h * _TRADING_TO_CALENDAR) + 1
            if (today - anchor).days >= need_calendar and outcomes.get(f"spy_{final_h}d_return_pct") is not None:
                row["status"] = "matured"
                row["matured_at"] = _now_iso()
                matured += 1
            else:
                row["status"] = "open"
                still_open += 1
        except Exception as exc:  # pragma: no cover
            logger.warning("forecast resolver error on row %s: %s", row.get("snapshot_id"), exc)
            errors += 1

    _write_jsonl(log_path, rows)
    return {
        "rows": len(rows),
        "matured": matured,
        "still_open": still_open,
        "errors": errors,
        "fields_updated": updated,
        "log_path": str(log_path),
    }


def resolve_stock_lens_outcomes(
    *,
    log_path: Path = STOCK_LENS_LOG_PATH,
    price_dirs: Iterable[Path] = PRICE_DIRS_DEFAULT,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    rows = _read_jsonl(log_path)
    if not rows:
        return {"rows": 0, "matured": 0, "still_open": 0, "errors": 0}
    today = today or date.today()
    horizons = list(STOCK_LENS_HORIZONS_DAYS)
    cache: Dict[str, Any] = {}

    def _frame(sym: str):
        if sym not in cache:
            cache[sym] = _load_price_frame(sym, price_dirs=price_dirs)
        return cache[sym]

    matured = 0
    still_open = 0
    errors = 0
    updated = 0

    for row in rows:
        try:
            ticker = (row.get("ticker") or "").upper()
            anchor = date.fromisoformat(str(row.get("anchor_date"))[:10])
            outcomes = row.get("outcomes") or {}

            f_t = _frame(ticker)
            f_spy = _frame("SPY")
            label_dir = _bias_direction(row.get("label"))

            for h in horizons:
                key = f"return_{h}d_pct"
                if outcomes.get(key) is None:
                    ret = _forward_return_pct(f_t, anchor, h)
                    if ret is not None:
                        outcomes[key] = ret
                        updated += 1
                spy_key = f"spy_{h}d_pct"
                if outcomes.get(spy_key) is None:
                    s = _forward_return_pct(f_spy, anchor, h) if f_spy is not None else None
                    if s is not None:
                        outcomes[spy_key] = s
                        updated += 1
                rel_key = f"rel_spy_{h}d_pct"
                if (outcomes.get(rel_key) is None
                        and outcomes.get(key) is not None
                        and outcomes.get(spy_key) is not None):
                    outcomes[rel_key] = round(outcomes[key] - outcomes[spy_key], 4)
                mae_key = f"max_drawdown_{h}d_pct"
                if outcomes.get(mae_key) is None:
                    dd = _forward_min_low_pct(f_t, anchor, h)
                    if dd is not None:
                        outcomes[mae_key] = dd
                mfe_key = f"max_favorable_{h}d_pct"
                if outcomes.get(mfe_key) is None:
                    mx = _forward_max_high_pct(f_t, anchor, h)
                    if mx is not None:
                        outcomes[mfe_key] = mx

            for h in horizons:
                ret = outcomes.get(f"return_{h}d_pct")
                outcomes[f"hit_{h}d"] = _hit_from_return(label_dir, ret)

            row["outcomes"] = outcomes

            final_h = max(horizons)
            need_calendar = int(final_h * _TRADING_TO_CALENDAR) + 1
            if ((today - anchor).days >= need_calendar
                    and outcomes.get(f"return_{final_h}d_pct") is not None):
                row["status"] = "matured"
                row["matured_at"] = _now_iso()
                matured += 1
            else:
                row["status"] = "open"
                still_open += 1
        except Exception as exc:
            logger.warning("stock lens resolver error on row %s: %s", row.get("snapshot_id"), exc)
            errors += 1

    _write_jsonl(log_path, rows)
    return {
        "rows": len(rows),
        "matured": matured,
        "still_open": still_open,
        "errors": errors,
        "fields_updated": updated,
        "log_path": str(log_path),
    }


# --- summary analytics -----------------------------------------------------


def _safe_mean(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _hit_rate(values: List[Optional[bool]]) -> Optional[float]:
    bools = [v for v in values if v is True or v is False]
    if not bools:
        return None
    return round(sum(1 for b in bools if b) / len(bools), 4)


def forecast_summary(*, log_path: Path = FORECAST_LOG_PATH) -> Dict[str, Any]:
    rows = _read_jsonl(log_path)
    matured_rows = [r for r in rows if r.get("status") == "matured"]
    open_rows    = [r for r in rows if r.get("status") != "matured"]

    out: Dict[str, Any] = {
        "kind": "forecast_forward_summary",
        "generated_at": _now_iso(),
        "log_path": str(log_path),
        "snapshots_total": len(rows),
        "snapshots_open": len(open_rows),
        "snapshots_matured": len(matured_rows),
        "horizons": list(FORECAST_HORIZONS_DAYS),
    }

    spy_5d   = [r.get("outcomes", {}).get("spy_5d_return_pct") for r in matured_rows]
    spy_10d  = [r.get("outcomes", {}).get("spy_10d_return_pct") for r in matured_rows]
    spy_20d  = [r.get("outcomes", {}).get("spy_20d_return_pct") for r in matured_rows]
    spy_5d_hit  = [r.get("outcomes", {}).get("spy_5d_hit") for r in matured_rows]
    spy_10d_hit = [r.get("outcomes", {}).get("spy_10d_hit") for r in matured_rows]
    out["matured_aggregate"] = {
        "spy_5d_avg_pct":   _safe_mean(spy_5d),
        "spy_10d_avg_pct":  _safe_mean(spy_10d),
        "spy_20d_avg_pct":  _safe_mean(spy_20d),
        "spy_5d_hit_rate":  _hit_rate(spy_5d_hit),
        "spy_10d_hit_rate": _hit_rate(spy_10d_hit),
    }

    by_regime: Dict[str, Dict[str, Any]] = {}
    for r in matured_rows:
        reg = r.get("current_regime") or "unknown"
        d = by_regime.setdefault(reg, {"n": 0, "spy_5d": [], "spy_10d": [], "hit_5d": [], "hit_10d": []})
        d["n"] += 1
        oc = r.get("outcomes", {})
        d["spy_5d"].append(oc.get("spy_5d_return_pct"))
        d["spy_10d"].append(oc.get("spy_10d_return_pct"))
        d["hit_5d"].append(oc.get("spy_5d_hit"))
        d["hit_10d"].append(oc.get("spy_10d_hit"))
    out["by_regime"] = {
        reg: {
            "n": d["n"],
            "avg_spy_5d_pct":   _safe_mean(d["spy_5d"]),
            "avg_spy_10d_pct":  _safe_mean(d["spy_10d"]),
            "spy_5d_hit_rate":  _hit_rate(d["hit_5d"]),
            "spy_10d_hit_rate": _hit_rate(d["hit_10d"]),
        }
        for reg, d in sorted(by_regime.items())
    }

    by_conf: Dict[str, Dict[str, Any]] = {}
    for r in matured_rows:
        c = (r.get("confidence") or "unknown").lower()
        d = by_conf.setdefault(c, {"n": 0, "hit_5d": [], "hit_10d": []})
        d["n"] += 1
        oc = r.get("outcomes", {})
        d["hit_5d"].append(oc.get("spy_5d_hit"))
        d["hit_10d"].append(oc.get("spy_10d_hit"))
    out["by_confidence"] = {
        c: {
            "n": d["n"],
            "spy_5d_hit_rate":  _hit_rate(d["hit_5d"]),
            "spy_10d_hit_rate": _hit_rate(d["hit_10d"]),
        }
        for c, d in sorted(by_conf.items())
    }

    sec_5 = [r.get("outcomes", {}).get("top_minus_bottom_5d_pct") for r in matured_rows]
    sec_10 = [r.get("outcomes", {}).get("top_minus_bottom_10d_pct") for r in matured_rows]
    sec_5_win = [r.get("outcomes", {}).get("leaders_beat_laggards_5d") for r in matured_rows]
    sec_10_win = [r.get("outcomes", {}).get("leaders_beat_laggards_10d") for r in matured_rows]
    out["sector_basket"] = {
        "avg_top_minus_bottom_5d_pct":  _safe_mean(sec_5),
        "avg_top_minus_bottom_10d_pct": _safe_mean(sec_10),
        "leaders_beat_laggards_5d_rate":  _hit_rate(sec_5_win),
        "leaders_beat_laggards_10d_rate": _hit_rate(sec_10_win),
        "n_with_basket": sum(1 for r in matured_rows
                              if r.get("predicted_top_basket") and r.get("predicted_bottom_basket")),
    }

    falses: List[Dict[str, Any]] = []
    for r in matured_rows:
        oc = r.get("outcomes", {})
        if (r.get("confidence") or "").lower() == "high" and oc.get("spy_5d_hit") is False:
            falses.append({
                "snapshot_id": r.get("snapshot_id"),
                "anchor_date": r.get("anchor_date"),
                "current_regime": r.get("current_regime"),
                "bias_5d": r.get("bias_5d"),
                "spy_5d_return_pct": oc.get("spy_5d_return_pct"),
                "spy_10d_return_pct": oc.get("spy_10d_return_pct"),
            })
    out["false_confidence_examples"] = falses[:10]

    out["open_snapshots"] = [
        {
            "snapshot_id": r.get("snapshot_id"),
            "anchor_date": r.get("anchor_date"),
            "current_regime": r.get("current_regime"),
            "bias_5d": r.get("bias_5d"),
            "confidence": r.get("confidence"),
        }
        for r in open_rows[-20:]
    ]
    return out


def stock_lens_summary(*, log_path: Path = STOCK_LENS_LOG_PATH) -> Dict[str, Any]:
    rows = _read_jsonl(log_path)
    matured_rows = [r for r in rows if r.get("status") == "matured"]
    open_rows    = [r for r in rows if r.get("status") != "matured"]

    out: Dict[str, Any] = {
        "kind": "stock_lens_forward_summary",
        "generated_at": _now_iso(),
        "log_path": str(log_path),
        "snapshots_total": len(rows),
        "snapshots_open": len(open_rows),
        "snapshots_matured": len(matured_rows),
        "horizons": list(STOCK_LENS_HORIZONS_DAYS),
    }

    by_label: Dict[str, Dict[str, Any]] = {}
    by_conf:  Dict[str, Dict[str, Any]] = {}
    by_entry: Dict[str, Dict[str, Any]] = {}

    def _bucket_init() -> Dict[str, Any]:
        return {"n": 0, "ret_5d": [], "ret_10d": [], "ret_20d": [],
                "rel_5d": [], "rel_10d": [], "rel_20d": [],
                "hit_5d": [], "hit_10d": [], "hit_20d": []}

    def _push(b: Dict[str, Any], r: Dict[str, Any]) -> None:
        b["n"] += 1
        oc = r.get("outcomes") or {}
        for h in (5, 10, 20):
            b[f"ret_{h}d"].append(oc.get(f"return_{h}d_pct"))
            b[f"rel_{h}d"].append(oc.get(f"rel_spy_{h}d_pct"))
            b[f"hit_{h}d"].append(oc.get(f"hit_{h}d"))

    for r in matured_rows:
        _push(by_label.setdefault(r.get("label") or "—", _bucket_init()), r)
        _push(by_conf.setdefault((r.get("confidence") or "—").lower(), _bucket_init()), r)
        entry_view = ((r.get("layers") or {}).get("entry") or {}).get("view") or "—"
        _push(by_entry.setdefault(entry_view, _bucket_init()), r)

    def _bucket_summary(b: Dict[str, Any]) -> Dict[str, Any]:
        out_b = {"n": b["n"]}
        for h in (5, 10, 20):
            out_b[f"avg_return_{h}d_pct"] = _safe_mean(b[f"ret_{h}d"])
            out_b[f"avg_rel_spy_{h}d_pct"] = _safe_mean(b[f"rel_{h}d"])
            out_b[f"hit_rate_{h}d"] = _hit_rate(b[f"hit_{h}d"])
        return out_b

    out["by_label"] = {k: _bucket_summary(v) for k, v in sorted(by_label.items())}
    out["by_confidence"] = {k: _bucket_summary(v) for k, v in sorted(by_conf.items())}
    out["by_entry_state"] = {k: _bucket_summary(v) for k, v in sorted(by_entry.items())}

    falses: List[Dict[str, Any]] = []
    for r in matured_rows:
        oc = r.get("outcomes") or {}
        if (r.get("confidence") or "").lower() == "high":
            for h in (5, 10):
                hit = oc.get(f"hit_{h}d")
                if hit is False:
                    falses.append({
                        "snapshot_id": r.get("snapshot_id"),
                        "ticker": r.get("ticker"),
                        "anchor_date": r.get("anchor_date"),
                        "label": r.get("label"),
                        f"return_{h}d_pct": oc.get(f"return_{h}d_pct"),
                        f"rel_spy_{h}d_pct": oc.get(f"rel_spy_{h}d_pct"),
                        "horizon": f"{h}d",
                    })
                    break
    out["false_confidence_examples"] = falses[:10]

    out["open_snapshots"] = [
        {
            "snapshot_id": r.get("snapshot_id"),
            "ticker": r.get("ticker"),
            "anchor_date": r.get("anchor_date"),
            "label": r.get("label"),
            "confidence": r.get("confidence"),
        }
        for r in open_rows[-20:]
    ]
    return out


# --- summary writers (cache + logs) ---------------------------------------


def _format_pct(v: Optional[float], width: int = 7) -> str:
    if v is None:
        return f"{'—':>{width}}"
    return f"{v:+{width}.2f}"


def _format_rate(v: Optional[float]) -> str:
    if v is None:
        return "  —  "
    return f"{v*100:5.1f}%"


def render_forecast_summary_text(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("=" * 68)
    lines.append("FORECAST FORWARD SUMMARY  research-only · phase 5")
    lines.append("=" * 68)
    lines.append(f"generated_at        {summary.get('generated_at')}")
    lines.append(f"snapshots_total     {summary.get('snapshots_total')}")
    lines.append(f"  matured           {summary.get('snapshots_matured')}")
    lines.append(f"  open              {summary.get('snapshots_open')}")
    lines.append("")
    agg = summary.get("matured_aggregate") or {}
    lines.append("Matured aggregate")
    lines.append(f"  spy 5d  avg {_format_pct(agg.get('spy_5d_avg_pct'))}%   hit {_format_rate(agg.get('spy_5d_hit_rate'))}")
    lines.append(f"  spy 10d avg {_format_pct(agg.get('spy_10d_avg_pct'))}%   hit {_format_rate(agg.get('spy_10d_hit_rate'))}")
    lines.append(f"  spy 20d avg {_format_pct(agg.get('spy_20d_avg_pct'))}%")
    lines.append("")

    by_reg = summary.get("by_regime") or {}
    if by_reg:
        lines.append("By predicted regime")
        lines.append(f"  {'regime':<32} {'n':>4}  {'5d_avg':>8}  {'10d_avg':>9}  {'5d_hit':>7}  {'10d_hit':>8}")
        for reg, d in by_reg.items():
            lines.append(
                f"  {str(reg)[:32]:<32} {d.get('n',0):>4}  "
                f"{_format_pct(d.get('avg_spy_5d_pct'))}  "
                f"{_format_pct(d.get('avg_spy_10d_pct'))}  "
                f"{_format_rate(d.get('spy_5d_hit_rate'))}  "
                f"{_format_rate(d.get('spy_10d_hit_rate'))}"
            )
        lines.append("")

    by_conf = summary.get("by_confidence") or {}
    if by_conf:
        lines.append("By confidence")
        for c, d in by_conf.items():
            lines.append(
                f"  {c:<8} n={d.get('n',0):<3}  5d hit {_format_rate(d.get('spy_5d_hit_rate'))}  "
                f"10d hit {_format_rate(d.get('spy_10d_hit_rate'))}"
            )
        lines.append("")

    sb = summary.get("sector_basket") or {}
    if sb.get("n_with_basket"):
        lines.append("Sector basket (predicted top minus bottom)")
        lines.append(f"  n={sb.get('n_with_basket')}  "
                     f"5d spread {_format_pct(sb.get('avg_top_minus_bottom_5d_pct'))}pp  "
                     f"10d spread {_format_pct(sb.get('avg_top_minus_bottom_10d_pct'))}pp")
        lines.append(f"  leaders beat laggards: 5d {_format_rate(sb.get('leaders_beat_laggards_5d_rate'))}  "
                     f"10d {_format_rate(sb.get('leaders_beat_laggards_10d_rate'))}")
        lines.append("")

    falses = summary.get("false_confidence_examples") or []
    if falses:
        lines.append("False-confidence examples (high-confidence + 5d miss)")
        for ex in falses[:5]:
            lines.append(
                f"  {ex.get('anchor_date')}  {ex.get('current_regime')}  "
                f"5d {_format_pct(ex.get('spy_5d_return_pct'))}%  10d {_format_pct(ex.get('spy_10d_return_pct'))}%"
            )
        lines.append("")

    opens = summary.get("open_snapshots") or []
    lines.append(f"Open snapshots: {len(opens)} (most recent shown)")
    for ex in opens[-10:]:
        lines.append(
            f"  {ex.get('anchor_date')}  {ex.get('current_regime'):<28}  "
            f"5d:{ex.get('bias_5d','—'):<14}  conf:{ex.get('confidence','—')}"
        )
    lines.append("")
    lines.append("Honesty note: hit-rate uses recorded forward returns only — no")
    lines.append("forecast logic was tuned on these outcomes.")
    return "\n".join(lines) + "\n"


def render_stock_lens_summary_text(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("=" * 68)
    lines.append("STOCK LENS FORWARD SUMMARY  research-only · phase 5")
    lines.append("=" * 68)
    lines.append(f"generated_at        {summary.get('generated_at')}")
    lines.append(f"snapshots_total     {summary.get('snapshots_total')}")
    lines.append(f"  matured           {summary.get('snapshots_matured')}")
    lines.append(f"  open              {summary.get('snapshots_open')}")
    lines.append("")

    def _bucket_block(title: str, by_key: Dict[str, Any]) -> None:
        if not by_key:
            return
        lines.append(title)
        lines.append(f"  {'bucket':<32} {'n':>4}  {'5d_avg':>8}  {'10d_avg':>9}  {'5d_rel':>8}  {'5d_hit':>7}  {'10d_hit':>8}")
        for k, d in by_key.items():
            lines.append(
                f"  {str(k)[:32]:<32} {d.get('n',0):>4}  "
                f"{_format_pct(d.get('avg_return_5d_pct'))}  "
                f"{_format_pct(d.get('avg_return_10d_pct'))}  "
                f"{_format_pct(d.get('avg_rel_spy_5d_pct'))}  "
                f"{_format_rate(d.get('hit_rate_5d'))}  "
                f"{_format_rate(d.get('hit_rate_10d'))}"
            )
        lines.append("")

    _bucket_block("By label",          summary.get("by_label") or {})
    _bucket_block("By confidence",     summary.get("by_confidence") or {})
    _bucket_block("By entry state",    summary.get("by_entry_state") or {})

    falses = summary.get("false_confidence_examples") or []
    if falses:
        lines.append("False-confidence examples (high-confidence + miss)")
        for ex in falses[:5]:
            h = ex.get("horizon", "5d")
            ret_key = f"return_{h}_pct"
            rel_key = f"rel_spy_{h}_pct"
            lines.append(
                f"  {ex.get('anchor_date')}  {ex.get('ticker'):<6}  "
                f"label={ex.get('label')}  {h} {_format_pct(ex.get(ret_key))}%  "
                f"rel-spy {_format_pct(ex.get(rel_key))}pp"
            )
        lines.append("")

    opens = summary.get("open_snapshots") or []
    lines.append(f"Open snapshots: {len(opens)} (most recent shown)")
    for ex in opens[-10:]:
        lines.append(
            f"  {ex.get('anchor_date')}  {ex.get('ticker'):<6}  "
            f"label={ex.get('label'):<30}  conf={ex.get('confidence')}"
        )
    lines.append("")
    lines.append("Honesty note: directional hits only.  No lens-scoring weights were")
    lines.append("changed based on these outcomes.")
    return "\n".join(lines) + "\n"


def write_forecast_summary(
    *,
    log_path: Path = FORECAST_LOG_PATH,
    cache_dir: Path = CACHE_DIR_DEFAULT,
    log_dir: Path = LOG_DIR_DEFAULT,
) -> Dict[str, str]:
    summary = forecast_summary(log_path=log_path)
    json_path = cache_dir / "research" / "forecast_forward_summary_latest.json"
    text_path = log_dir / "forecast_forward_summary_latest.txt"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    text_path.write_text(render_forecast_summary_text(summary), encoding="utf-8")
    return {"json": str(json_path), "text": str(text_path)}


def write_stock_lens_summary(
    *,
    log_path: Path = STOCK_LENS_LOG_PATH,
    cache_dir: Path = CACHE_DIR_DEFAULT,
    log_dir: Path = LOG_DIR_DEFAULT,
) -> Dict[str, str]:
    summary = stock_lens_summary(log_path=log_path)
    json_path = cache_dir / "research" / "stock_lens_forward_summary_latest.json"
    text_path = log_dir / "stock_lens_forward_summary_latest.txt"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    text_path.write_text(render_stock_lens_summary_text(summary), encoding="utf-8")
    return {"json": str(json_path), "text": str(text_path)}
