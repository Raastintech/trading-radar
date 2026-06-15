from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

import short_backtester as sb
from research_data_provider import RoutingEarningsProvider, ProviderCoverageReport


EARNINGS_EVENT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
SUPPORTED_SOURCES = (
    "fmp_earnings",
    "alpha_vantage_earnings",
    "yfinance_earnings",
)


def _raw_cache_path(ticker: str) -> Path:
    return Path("logs") / "short_backtester_cache" / "earnings_event_store_raw" / f"{sb._cache_key(ticker)}.json"


def _cache_fresh(path: Path, ttl_seconds: int) -> bool:
    if not path.exists():
        return False
    try:
        return (time.time() - path.stat().st_mtime) <= float(ttl_seconds)
    except OSError:
        return False


def _normalize_timestamp(value: Any) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is not None:
            ts = ts.tz_convert(None)
        return ts
    except Exception:
        return None


def infer_session_flag(event_timestamp: Optional[pd.Timestamp]) -> str:
    if event_timestamp is None:
        return "unknown"
    ts = _normalize_timestamp(event_timestamp)
    if ts is None:
        return "unknown"
    mins = int(ts.hour) * 60 + int(ts.minute)
    if mins < (9 * 60 + 30):
        return "pre_market"
    if mins >= (16 * 60):
        return "after_hours"
    return "unknown"


def parse_source_rows(frame: Any, *, ticker: str, source_identifier: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if frame is None:
        return rows

    def _append(ts_like: Any) -> None:
        ts = _normalize_timestamp(ts_like)
        if ts is None:
            return
        rows.append(
            {
                "ticker": str(ticker).upper(),
                "earnings_date": ts.normalize(),
                "event_timestamp": ts,
                "source_identifier": str(source_identifier),
                "session_flag": infer_session_flag(ts),
            }
        )

    try:
        if isinstance(frame, pd.DataFrame):
            if isinstance(frame.index, pd.DatetimeIndex):
                for ts in frame.index.tolist():
                    _append(ts)
            elif "Earnings Date" in frame.columns:
                for ts in pd.to_datetime(frame["Earnings Date"], errors="coerce").dropna().tolist():
                    _append(ts)
        elif isinstance(frame, pd.Series):
            if isinstance(frame.index, pd.DatetimeIndex):
                for ts in frame.index.tolist():
                    _append(ts)
            else:
                for ts in pd.to_datetime(frame, errors="coerce").dropna().tolist():
                    _append(ts)
    except Exception:
        return []
    return rows


def _read_cached_raw_rows(ticker: str) -> Optional[List[Dict[str, Any]]]:
    path = _raw_cache_path(ticker)
    if not _cache_fresh(path, EARNINGS_EVENT_CACHE_TTL_SECONDS):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("raw_rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return None
        # Empty earnings-event caches are usually transient Yahoo failures,
        # not credible evidence that a liquid US equity has no earnings history.
        return rows or None
    except Exception:
        return None


def _write_cached_raw_rows(ticker: str, rows: Sequence[Dict[str, Any]]) -> None:
    path = _raw_cache_path(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticker": str(ticker).upper(),
        "raw_rows": list(rows),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def fetch_raw_earnings_event_rows(
    ticker: str,
    *,
    limit: int = 40,
    provider: Optional[RoutingEarningsProvider] = None,
) -> List[Dict[str, Any]]:
    cached = _read_cached_raw_rows(ticker)
    if cached is not None:
        return cached

    rows: List[Dict[str, Any]] = []
    earnings_provider = provider or RoutingEarningsProvider()
    result = earnings_provider.fetch_dates(str(ticker))
    if result.success and result.value:
        events = list(result.value)[: int(limit)]
        for event in events:
            rows.append(
                {
                    "ticker": str(getattr(event, "ticker", ticker)).upper(),
                    "earnings_date": getattr(event, "reported_date", None),
                    "event_timestamp": getattr(event, "metadata", {}).get("event_timestamp"),
                    "source_identifier": str(getattr(event, "source", result.provider)),
                    "session_flag": str(getattr(event, "session_flag", "unknown")),
                    "confidence_label": str(getattr(event, "confidence_label", result.confidence_label)),
                }
            )

    deduped: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str, str]] = set()
    for row in rows:
        ts = _normalize_timestamp(row.get("event_timestamp"))
        key = (
            str(row.get("ticker") or "").upper(),
            str(row.get("source_identifier") or ""),
            ts.isoformat() if ts is not None else str(row.get("earnings_date") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        clean = dict(row)
        clean["event_timestamp"] = ts.isoformat() if ts is not None else None
        clean["earnings_date"] = _normalize_timestamp(row.get("earnings_date")).date().isoformat() if _normalize_timestamp(row.get("earnings_date")) is not None else None
        deduped.append(clean)

    if deduped:
        try:
            _write_cached_raw_rows(ticker, deduped)
        except Exception:
            pass
    return deduped


def _first_tradable_session_date(
    earnings_date: pd.Timestamp,
    session_flag: str,
    price_history: pd.DataFrame,
) -> Optional[pd.Timestamp]:
    history = sb._normalize_price_frame(price_history)
    if history.empty:
        return None
    ed = pd.Timestamp(earnings_date).normalize()
    if str(session_flag) == "after_hours":
        mask = history.index > ed
    else:
        mask = history.index >= ed
    candidates = history.index[mask]
    if len(candidates) == 0:
        return None
    return pd.Timestamp(candidates[0]).normalize()


def _next_session_gap_pct(first_session: Optional[pd.Timestamp], price_history: pd.DataFrame) -> Optional[float]:
    if first_session is None:
        return None
    history = sb._normalize_price_frame(price_history)
    if history.empty or first_session not in history.index:
        return None
    loc = history.index.get_loc(first_session)
    if isinstance(loc, slice) or isinstance(loc, list):
        return None
    idx = int(loc)
    if idx <= 0:
        return None
    prev_close = float(history["close"].iloc[idx - 1] or 0.0)
    open_px = float(history["open"].iloc[idx] or 0.0)
    if prev_close <= 0 or open_px <= 0:
        return None
    return ((open_px / prev_close) - 1.0) * 100.0


def _confidence_flag(source_count: int, has_timestamp: bool, event_complete: bool, source_labels: Sequence[str]) -> str:
    if not event_complete:
        return "low"
    labels = set(str(v) for v in source_labels if str(v))
    if "verified_primary" in labels and (source_count >= 1 or has_timestamp):
        return "high"
    if source_count >= 2 or has_timestamp:
        return "high"
    if "fallback_secondary" in labels and source_count >= 1:
        return "medium"
    if event_complete and source_count >= 1:
        return "medium"
    return "low"


def _confidence_label(source_labels: Sequence[str], *, event_complete: bool, source_count: int) -> str:
    labels = set(str(v) for v in source_labels if str(v))
    if not event_complete:
        return "debug_only" if "debug_only" in labels else "unverified"
    if "verified_primary" in labels:
        return "verified_primary"
    if "fallback_secondary" in labels:
        return "fallback_secondary"
    if labels == {"debug_only"}:
        return "debug_only"
    if source_count >= 1:
        return "fallback_secondary"
    return "debug_only" if "debug_only" in labels else "unverified"


def build_canonical_event_rows(
    *,
    ticker: str,
    raw_rows: Sequence[Dict[str, Any]],
    price_history: pd.DataFrame,
) -> List[Dict[str, Any]]:
    grouped: Dict[pd.Timestamp, List[Dict[str, Any]]] = {}
    for row in raw_rows:
        ed = _normalize_timestamp(row.get("earnings_date"))
        if ed is None:
            continue
        ed = ed.normalize()
        grouped.setdefault(ed, []).append(dict(row))

    canonical: List[Dict[str, Any]] = []
    for earnings_date, rows in sorted(grouped.items(), key=lambda item: item[0]):
        timestamps = [_normalize_timestamp(r.get("event_timestamp")) for r in rows]
        timestamps = [ts for ts in timestamps if ts is not None]
        source_ids = sorted({str(r.get("source_identifier") or "") for r in rows if str(r.get("source_identifier") or "")})
        source_labels = [str(r.get("confidence_label") or "") for r in rows]
        known_sessions = [str(r.get("session_flag") or "unknown") for r in rows if str(r.get("session_flag") or "unknown") != "unknown"]
        session_flag = known_sessions[0] if known_sessions else "unknown"
        event_timestamp = min(timestamps) if timestamps else None
        first_tradable = _first_tradable_session_date(earnings_date, session_flag, price_history)
        gap_pct = _next_session_gap_pct(first_tradable, price_history)
        complete = first_tradable is not None and gap_pct is not None
        confidence = _confidence_flag(len(source_ids), bool(event_timestamp is not None), bool(complete), source_labels)
        confidence_label = _confidence_label(source_labels, event_complete=bool(complete), source_count=len(source_ids))
        verified = bool(complete and confidence_label in {"verified_primary", "fallback_secondary"})
        canonical.append(
            {
                "ticker": str(ticker).upper(),
                "earnings_date": earnings_date,
                "event_timestamp": event_timestamp,
                "session_flag": session_flag,
                "source_identifier": "|".join(source_ids),
                "source_count": len(source_ids),
                "confidence_flag": confidence,
                "confidence_label": confidence_label,
                "first_tradable_session_date": first_tradable,
                "next_session_gap_pct": gap_pct,
                "event_completeness_flag": bool(complete),
                "verified_event_flag": verified,
                "validation_status": "verified" if verified else "unverified",
            }
        )
    return canonical


def build_event_store_for_universe(
    *,
    tickers: Sequence[str],
    price_histories: Dict[str, pd.DataFrame],
    limit: int = 40,
    max_workers: int = 8,
    provider: Optional[RoutingEarningsProvider] = None,
    coverage: Optional[ProviderCoverageReport] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    raw_rows: List[Dict[str, Any]] = []
    canonical_rows: List[Dict[str, Any]] = []
    symbols_with_no_events: List[str] = []
    symbols_with_no_price: List[str] = []

    earnings_provider = provider or RoutingEarningsProvider(coverage=coverage or ProviderCoverageReport())

    def _build_one(ticker: str) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], bool, bool]:
        price_history = sb._normalize_price_frame(price_histories.get(ticker))
        no_price = bool(price_history.empty)
        rows = fetch_raw_earnings_event_rows(ticker, limit=int(limit), provider=earnings_provider)
        no_events = not bool(rows)
        canonical = build_canonical_event_rows(ticker=ticker, raw_rows=rows, price_history=price_history)
        return ticker, rows, canonical, no_price, no_events

    tickers_norm = [str(t).upper() for t in tickers if str(t).strip()]
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        futures = {executor.submit(_build_one, ticker): ticker for ticker in tickers_norm}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                ticker, rows, canonical, no_price, no_events = fut.result()
            except Exception:
                ticker, rows, canonical, no_price, no_events = ticker, [], [], True, True
            if no_price:
                symbols_with_no_price.append(ticker)
            if no_events:
                symbols_with_no_events.append(ticker)
            raw_rows.extend(rows)
            canonical_rows.extend(canonical)

    raw_df = pd.DataFrame(raw_rows)
    canonical_df = pd.DataFrame(canonical_rows)
    meta = {
        "tickers": len([t for t in tickers if str(t).strip()]),
        "raw_rows": int(len(raw_rows)),
        "canonical_rows": int(len(canonical_rows)),
        "symbols_with_no_events": sorted(symbols_with_no_events),
        "symbols_with_no_price": sorted(symbols_with_no_price),
        "provider_coverage": (coverage.summary() if coverage is not None else earnings_provider.coverage.summary()),
    }
    return raw_df, canonical_df, meta


def event_integrity_summary(raw_df: pd.DataFrame, canonical_df: pd.DataFrame, universe_tickers: Sequence[str]) -> Dict[str, Any]:
    raw_df = raw_df.copy() if raw_df is not None else pd.DataFrame()
    canonical_df = canonical_df.copy() if canonical_df is not None else pd.DataFrame()

    counts_by_ticker: Dict[str, int] = {}
    counts_by_year: Dict[str, int] = {}
    if not canonical_df.empty:
        temp = canonical_df.copy()
        temp["earnings_date"] = pd.to_datetime(temp["earnings_date"], errors="coerce")
        counts_by_ticker = temp.groupby("ticker").size().sort_values(ascending=False).astype(int).to_dict()
        temp["year"] = temp["earnings_date"].dt.year.astype("Int64").astype(str)
        counts_by_year = temp.groupby("year").size().sort_values().astype(int).to_dict()

    missingness_rates: Dict[str, float] = {}
    for col in [
        "event_timestamp",
        "session_flag",
        "first_tradable_session_date",
        "next_session_gap_pct",
        "confidence_flag",
    ]:
        if canonical_df.empty or col not in canonical_df.columns:
            missingness_rates[col] = 1.0
        else:
            missingness_rates[col] = float(canonical_df[col].isna().mean())

    duplicate_exact = 0
    conflicting_dates = 0
    if not raw_df.empty:
        temp = raw_df.copy()
        duplicate_exact = int(temp.duplicated(subset=["ticker", "source_identifier", "earnings_date", "event_timestamp"]).sum())
        temp["earnings_date"] = pd.to_datetime(temp["earnings_date"], errors="coerce")
        temp = temp.sort_values(["ticker", "earnings_date"])
        conflicts = 0
        for ticker, grp in temp.groupby("ticker"):
            dates = [pd.Timestamp(v) for v in grp["earnings_date"].dropna().tolist()]
            for i in range(1, len(dates)):
                if abs((dates[i] - dates[i - 1]).days) <= 1:
                    conflicts += 1
        conflicting_dates = int(conflicts)

    after_hours_mapping_issues = 0
    pre_market_mapping_issues = 0
    tradable_events = 0
    non_tradable_events = 0
    symbols_dropped_due_to_missing_metadata = 0
    if not canonical_df.empty:
        temp = canonical_df.copy()
        temp["earnings_date"] = pd.to_datetime(temp["earnings_date"], errors="coerce")
        temp["first_tradable_session_date"] = pd.to_datetime(temp["first_tradable_session_date"], errors="coerce")
        tradable_events = int(temp["event_completeness_flag"].fillna(False).sum())
        non_tradable_events = int((~temp["event_completeness_flag"].fillna(False)).sum())
        after_hours_mapping_issues = int(
            (
                (temp["session_flag"] == "after_hours")
                & (temp["first_tradable_session_date"].notna())
                & (temp["first_tradable_session_date"].dt.normalize() <= temp["earnings_date"].dt.normalize())
            ).sum()
        )
        pre_market_mapping_issues = int(
            (
                (temp["session_flag"] == "pre_market")
                & (temp["first_tradable_session_date"].notna())
                & (temp["first_tradable_session_date"].dt.normalize() > temp["earnings_date"].dt.normalize())
            ).sum()
        )

    covered_symbols = set(canonical_df["ticker"].dropna().astype(str).tolist()) if not canonical_df.empty and "ticker" in canonical_df.columns else set()
    symbols_dropped_due_to_missing_metadata = int(sum(1 for t in universe_tickers if str(t).upper() not in covered_symbols))

    return {
        "counts_by_ticker": counts_by_ticker,
        "counts_by_year": counts_by_year,
        "missingness_rates": missingness_rates,
        "duplicate_exact_rows": duplicate_exact,
        "conflicting_event_dates": conflicting_dates,
        "after_hours_mapping_issues": after_hours_mapping_issues,
        "pre_market_mapping_issues": pre_market_mapping_issues,
        "symbols_dropped_due_to_missing_metadata": symbols_dropped_due_to_missing_metadata,
        "tradable_events": tradable_events,
        "non_tradable_events": non_tradable_events,
    }


def nearest_verified_event(
    trade_date: pd.Timestamp,
    canonical_events: pd.DataFrame,
    *,
    max_lag_days: int = 8,
    verified_only: bool = True,
) -> Optional[Dict[str, Any]]:
    if canonical_events is None or canonical_events.empty:
        return None
    temp = canonical_events.copy()
    if verified_only:
        temp = temp[temp["verified_event_flag"].fillna(False)]
    if temp.empty:
        return None
    temp["earnings_date"] = pd.to_datetime(temp["earnings_date"], errors="coerce")
    temp["first_tradable_session_date"] = pd.to_datetime(temp["first_tradable_session_date"], errors="coerce")
    temp = temp.dropna(subset=["first_tradable_session_date"])
    if temp.empty:
        return None
    td = pd.Timestamp(trade_date).normalize()
    temp["lag_days"] = (td - temp["first_tradable_session_date"].dt.normalize()).dt.days
    temp = temp[(temp["lag_days"] >= 0) & (temp["lag_days"] <= int(max_lag_days))]
    if temp.empty:
        return None
    row = temp.sort_values(["lag_days", "earnings_date"]).iloc[0].to_dict()
    return row


def reconcile_trades_to_events(
    trades: Sequence[Dict[str, Any]] | pd.DataFrame,
    canonical_events: pd.DataFrame,
    *,
    max_lag_days: int = 8,
) -> pd.DataFrame:
    if isinstance(trades, pd.DataFrame):
        trades_df = trades.copy()
    else:
        trades_df = pd.DataFrame(list(trades or []))
    if trades_df.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "entry_date",
                "event_link_status",
                "matched_verified_event",
                "matched_event_date",
                "matched_first_tradable_session_date",
                "matched_confidence_flag",
                "matched_source_identifier",
                "matched_session_flag",
                "matched_verified_event_flag",
                "lag_days",
                "coverage_issue",
            ]
        )

    canonical_df = canonical_events.copy() if canonical_events is not None else pd.DataFrame()
    if not canonical_df.empty:
        canonical_df["ticker"] = canonical_df["ticker"].astype(str).str.upper()

    rows: List[Dict[str, Any]] = []
    for _, trade in trades_df.iterrows():
        ticker = str(trade.get("ticker") or "").upper()
        entry_date = pd.Timestamp(trade.get("entry_date")).normalize()
        ticker_events = canonical_df[canonical_df["ticker"] == ticker].copy() if not canonical_df.empty else pd.DataFrame()
        verified_match = nearest_verified_event(entry_date, ticker_events, max_lag_days=max_lag_days, verified_only=True)
        any_match = nearest_verified_event(entry_date, ticker_events, max_lag_days=max_lag_days, verified_only=False)

        has_any_rows = not ticker_events.empty
        has_verified_any = bool(has_any_rows and ticker_events["verified_event_flag"].fillna(False).any())

        event_link_status = "coverage_missing"
        coverage_issue = "no_event_rows"
        matched = verified_match or any_match or {}

        if verified_match is not None:
            event_link_status = "verified_match"
            coverage_issue = ""
        elif any_match is not None:
            event_link_status = "event_alignment_unverified"
            coverage_issue = "matched_unverified_event"
        elif has_verified_any:
            event_link_status = "false_positive_heuristic"
            coverage_issue = "verified_events_exist_but_not_near_trade"
        elif has_any_rows:
            event_link_status = "coverage_incomplete"
            coverage_issue = "only_low_confidence_or_incomplete_events"

        first_tradable = matched.get("first_tradable_session_date")
        lag_days = None
        if first_tradable is not None and pd.notna(first_tradable):
            try:
                lag_days = int((entry_date - pd.Timestamp(first_tradable).normalize()).days)
            except Exception:
                lag_days = None

        row = dict(trade)
        row.update(
            {
                "ticker": ticker,
                "entry_date": entry_date,
                "event_link_status": event_link_status,
                "matched_verified_event": bool(verified_match is not None),
                "matched_event_date": matched.get("earnings_date"),
                "matched_first_tradable_session_date": matched.get("first_tradable_session_date"),
                "matched_confidence_flag": matched.get("confidence_flag"),
                "matched_source_identifier": matched.get("source_identifier"),
                "matched_session_flag": matched.get("session_flag"),
                "matched_verified_event_flag": matched.get("verified_event_flag"),
                "lag_days": lag_days,
                "coverage_issue": coverage_issue,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)
