#!/usr/bin/env python3
"""Cohort attribution for repaired research forward evidence.

Additive evidence-analysis only: reads existing cache sidecars/history ledgers,
writes cohort_attribution artifacts, and never changes scanner, scoring,
routing, gates, thresholds, factor weights, or research-selection rules.
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

VERSION = "COHORT_ATTRIBUTION_V1"
HORIZONS = (5, 10, 15, 20, 30, 45, 60)
PRIMARY_HORIZON = 10
MIN_MATURED_TO_EVALUATE = 10
MIN_MATURED_TO_INTERPRET = 30
MIN_MATURED_ROBUST = 100

REPO_ROOT = Path(__file__).resolve().parents[1]
HISTORY_REL = Path("data/research/research_watchlist_history.jsonl")
HC_HISTORY_REL = Path("data/research/high_conviction_history.jsonl")
EO_HISTORY_REL = Path("data/research/emerging_outlier_history.jsonl")
FORWARD_REL = Path("cache/research/research_forward_latest.json")
SCANNER_REL = Path("cache/research/research_scanner_latest.json")
ROUTING_REL = Path("cache/research/latest_scan_programs_latest.json")
HC_REL = Path("cache/research/high_conviction_alpha_latest.json")
EO_REL = Path("cache/research/emerging_outlier_watch_latest.json")
SCAN_EXCLUSION_REL = Path("cache/research/scan_exclusion_impact_latest.json")
SUMMARY_REL = Path("cache/research/nightly_operator_summary_latest.json")
FUNDAMENTALS_REL = Path("cache/fundamentals")
PRICE_REL = Path("cache/prices")
PRICE_DEEP_REL = Path("cache/prices_deep")
OUT_JSON_REL = Path("cache/research/cohort_attribution_latest.json")
OUT_TXT_REL = Path("logs/cohort_attribution_latest.txt")
DOC_REL = Path("docs/research/COHORT_ATTRIBUTION_REVIEW.md")

PROFITABLE_LABELS = {"PROFITABLE_CASHGEN", "PROFITABLE"}
UNPROFITABLE_LABELS = {
    "UNPROFITABLE_FUNDED",
    "UNPROFITABLE_STRESSED",
    "UNPROFITABLE_WATCH",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _num(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def _median(values: Sequence[float]) -> Optional[float]:
    return round(statistics.median(values), 2) if values else None


def _winsorized_mean(values: Sequence[float]) -> Optional[float]:
    vals = sorted(float(v) for v in values)
    n = len(vals)
    if not vals:
        return None
    if n < 5:
        return _mean(vals)
    k = max(1, int(n * 0.05))
    lo = vals[k]
    hi = vals[-k - 1]
    return _mean([min(max(v, lo), hi) for v in vals])


def outlier_driven(h: Dict[str, Any]) -> bool:
    """True when a horizon-stats dict's raw mean is disproportionately
    driven by 1-2 extreme rows (see _outlier_analysis)."""
    return bool((h.get("outlier_analysis") or {}).get("driven_by_one_or_two_outliers"))


def headline_mean_return_pct(h: Dict[str, Any]) -> Optional[float]:
    """Outlier-guarded headline mean: the winsorized mean when the raw mean
    is outlier-driven (e.g. one bad/corrupted price-cache row producing a
    triple-digit-percent return), else the raw mean. Reporting-only — does
    not change mean_return_pct itself, any score, ranking, gate, threshold,
    or HC/EO/program-verdict logic; it only picks which already-computed
    number is used for best/worst-cohort headlines and narrative text."""
    if outlier_driven(h) and h.get("winsorized_mean_return_pct") is not None:
        return h["winsorized_mean_return_pct"]
    return h.get("mean_return_pct")


def headline_excess_pct(h: Dict[str, Any], name: str = "spy") -> Optional[float]:
    """Outlier-guarded headline excess-vs-benchmark, mirroring
    headline_mean_return_pct."""
    winsorized_key = f"winsorized_excess_vs_{name}_pct"
    raw_key = f"excess_vs_{name}_pct"
    if outlier_driven(h) and h.get(winsorized_key) is not None:
        return h[winsorized_key]
    return h.get(raw_key)


def _sample_status(n_matured: int) -> str:
    if n_matured < MIN_MATURED_TO_EVALUATE:
        return "TOO_EARLY"
    if n_matured < MIN_MATURED_TO_INTERPRET:
        return "PROVISIONAL"
    if n_matured < MIN_MATURED_ROBUST:
        return "MEANINGFUL"
    return "ROBUST"


def _result_label(n_matured: int, mean_return: Optional[float], win_rate: Optional[float]) -> str:
    if n_matured < MIN_MATURED_TO_EVALUATE or mean_return is None or win_rate is None:
        return "immature"
    if win_rate >= 0.60 and mean_return > 0:
        return "promising"
    if win_rate < 0.40 and mean_return <= 0:
        return "no edge"
    if mean_return < 0:
        return "weak"
    return "mixed"


def _human_result(label: str, n_matured: int) -> str:
    return "Too early to evaluate." if n_matured < MIN_MATURED_TO_EVALUATE else label


def _read_price_dates(root: Path, symbol: str = "SPY") -> List[str]:
    try:
        import pandas as pd
    except Exception:
        return []
    dates: set[str] = set()
    for rel in (PRICE_DEEP_REL, PRICE_REL):
        path = root / rel / f"{symbol.upper()}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
            date_col = next((c for c in ("date", "Date", "timestamp", "Timestamp") if c in df.columns), None)
            vals = df[date_col].astype(str).str[:10] if date_col else df.index.astype(str).str[:10]
            dates.update(str(v)[:10] for v in vals if str(v))
        except Exception:
            continue
    return sorted(dates)


def _expected_mature(row: Dict[str, Any], horizon: int, price_dates: Sequence[str]) -> bool:
    if row.get(f"ret_{horizon}d") is not None:
        return True
    appearance = str(row.get("appearance_date") or "")[:10]
    if not appearance or not price_dates:
        return False
    idx = next((i for i, d in enumerate(price_dates) if d >= appearance), None)
    return idx is not None and idx + horizon < len(price_dates)


def _date_bucket(row: Dict[str, Any]) -> str:
    date = str(row.get("appearance_date") or "")[:10]
    return date[:7] if date else "UNKNOWN"


def _security_type(ticker: str) -> str:
    t = str(ticker or "").upper().strip()
    if not t:
        return "UNKNOWN"
    if "." in t or "-" in t:
        return "UNSUPPORTED_SHARE_CLASS_FORMAT"
    if t.endswith("W") or t.endswith("WS") or t.endswith("WT"):
        return "POSSIBLE_WARRANT"
    if t.endswith("U") or t.endswith("UN") or t.endswith("R") or t.endswith("RT"):
        return "POSSIBLE_UNIT_OR_RIGHT"
    return "COMMON_STOCK_OR_ADR"


def _market_cap_bucket(value: Any = None, existing: Any = None) -> str:
    if existing:
        text = str(existing).upper()
        lookup = {"MEGA": "MEGA_CAP", "LARGE": "LARGE_CAP", "MID": "MID_CAP", "SMALL": "SMALL_CAP", "MICRO": "MICRO_CAP"}
        if text in lookup:
            return lookup[text]
        if text in set(lookup.values()):
            return text
    cap = _num(value)
    if cap is None or cap <= 0:
        return "UNKNOWN"
    if cap >= 200_000_000_000:
        return "MEGA_CAP"
    if cap >= 10_000_000_000:
        return "LARGE_CAP"
    if cap >= 2_000_000_000:
        return "MID_CAP"
    if cap >= 300_000_000:
        return "SMALL_CAP"
    return "MICRO_CAP"


def _liquidity_bucket(value: Any) -> str:
    adv = _num(value)
    if adv is None:
        return "UNKNOWN"
    if adv >= 50_000_000:
        return "HIGH_LIQUIDITY"
    if adv >= 10_000_000:
        return "MEDIUM_LIQUIDITY"
    if adv >= 3_000_000:
        return "LOW_LIQUIDITY"
    return "VERY_LOW_LIQUIDITY"


def _quality_from_fundamentals(root: Path, ticker: str) -> str:
    raw = _load_json(root / FUNDAMENTALS_REL / f"{ticker.upper()}.json")
    if not raw:
        return "UNKNOWN"

    def rows(stmt: str) -> List[Dict[str, Any]]:
        vals = raw.get(stmt) or []
        try:
            return sorted(vals, key=lambda r: str(r.get("date") or ""), reverse=True)[:4]
        except Exception:
            return vals[:4]

    income = rows("income")
    cashflow = rows("cashflow")
    balance = rows("balance")[:1]
    ni_vals = [_num(r.get("netIncome")) for r in income]
    fcf_vals = [_num(r.get("freeCashFlow")) for r in cashflow]
    ttm_ni = sum(v for v in ni_vals if v is not None) if any(v is not None for v in ni_vals) else None
    ttm_fcf = sum(v for v in fcf_vals if v is not None) if any(v is not None for v in fcf_vals) else None
    if ttm_ni is None:
        return "UNKNOWN"
    if ttm_ni > 0:
        return "PROFITABLE_CASHGEN" if (ttm_fcf or 0) > 0 else "PROFITABLE"
    b0 = balance[0] if balance else {}
    cash = _num(b0.get("cashAndShortTermInvestments"))
    net_debt = _num(b0.get("netDebt"))
    burn = abs(ttm_fcf) if (ttm_fcf or 0) < 0 else None
    if (net_debt is not None and net_debt < 0) or (burn and cash is not None and cash >= 2 * burn):
        return "UNPROFITABLE_FUNDED"
    if burn and cash is not None and cash < burn:
        return "UNPROFITABLE_STRESSED"
    return "UNPROFITABLE_FUNDED" if burn is None else "UNPROFITABLE_WATCH"


def _profitability_bucket(quality: str) -> str:
    q = str(quality or "UNKNOWN").upper()
    if q in PROFITABLE_LABELS:
        return "PROFITABLE"
    if q in UNPROFITABLE_LABELS or q.startswith("UNPROFITABLE"):
        return "UNPROFITABLE"
    return "UNKNOWN"


def _quality_bucket(quality: str) -> str:
    q = str(quality or "UNKNOWN").upper()
    if q in PROFITABLE_LABELS:
        return "HIGH_QUALITY"
    if q in UNPROFITABLE_LABELS or q.startswith("UNPROFITABLE"):
        return "LOW_QUALITY"
    return "UNKNOWN_QUALITY"


def _extension_bucket(row: Dict[str, Any]) -> str:
    values = " ".join(str(row.get(k) or "").upper() for k in (
        "priority_label", "priority", "earliness_label", "extension_state", "watchlist_label", "classification"
    ))
    if "RESET_WATCH" in values or "RECLAIM_WATCH" in values or "WATCH_FOR_ENTRY" in values:
        return "RESET_OR_WATCH_FOR_ENTRY"
    if "EXTENDED" in values or "LATE" in values or "CROWDED" in values:
        return "EXTENDED"
    return "NORMAL_OR_UNKNOWN"


def _merge_attrs(base: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    for key, value in incoming.items():
        if value is not None and value != "" and key not in base:
            base[key] = value


def _current_attributes(root: Path) -> Dict[str, Dict[str, Any]]:
    attrs: Dict[str, Dict[str, Any]] = {}
    scanner = _load_json(root / SCANNER_REL) or {}
    for rows in (scanner.get("categories") or {}).values():
        for item in rows or []:
            ticker = str(item.get("ticker") or "").upper()
            if ticker:
                _merge_attrs(attrs.setdefault(ticker, {}), item)
    for item in scanner.get("watchlist") or []:
        ticker = str(item.get("ticker") or "").upper()
        if ticker:
            _merge_attrs(attrs.setdefault(ticker, {}), item)
    routing = _load_json(root / ROUTING_REL) or {}
    for program, doc in (routing.get("programs") or {}).items():
        for item in doc.get("candidates") or []:
            ticker = str(item.get("ticker") or "").upper()
            if ticker:
                merged = dict(item)
                merged.setdefault("research_program", program)
                _merge_attrs(attrs.setdefault(ticker, {}), merged)
    hc = _load_json(root / HC_REL) or {}
    for key in ("shortlist", "qualified", "improving_but_unproven", "quality_but_extended", "rejected"):
        for item in hc.get(key) or []:
            ticker = str(item.get("ticker") or "").upper()
            if ticker:
                _merge_attrs(attrs.setdefault(ticker, {}), item)
    eo = _load_json(root / EO_REL) or {}
    for item in eo.get("watch") or []:
        ticker = str(item.get("ticker") or "").upper()
        if ticker:
            _merge_attrs(attrs.setdefault(ticker, {}), item)
    return attrs


def _excluded_tickers(root: Path) -> set[str]:
    report = _load_json(root / SCAN_EXCLUSION_REL) or {}
    return {str(r.get("ticker") or "").upper() for r in report.get("excluded") or [] if r.get("ticker")}


def _membership_by_key(root: Path) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for rel, lane in ((HC_HISTORY_REL, "HIGH_CONVICTION_ALPHA"), (EO_HISTORY_REL, "EMERGING_OUTLIER_WATCH")):
        for row in _load_jsonl(root / rel):
            ticker = str(row.get("ticker") or "").upper()
            date = str(row.get("appearance_date") or "")[:10]
            if ticker and date:
                out.setdefault(f"{ticker}|{date}", []).append(lane)
    return out


def _sequence_labels(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    by_ticker: Dict[str, List[str]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        date = str(row.get("appearance_date") or "")[:10]
        if ticker and date:
            by_ticker.setdefault(ticker, []).append(date)
    labels: Dict[str, str] = {}
    for ticker, dates in by_ticker.items():
        prev: Optional[str] = None
        for idx, date in enumerate(sorted(set(dates))):
            label = "NEW" if idx == 0 else "RETURNING"
            if prev is not None:
                try:
                    if (datetime.fromisoformat(date).date() - datetime.fromisoformat(prev).date()).days <= 3:
                        label = "REPEAT"
                except Exception:
                    pass
            labels[f"{ticker}|{date}"] = label
            prev = date
    return labels


def _enrich_rows(root: Path, rows: List[Dict[str, Any]], *, lane: str = "BROAD_SCANNER") -> List[Dict[str, Any]]:
    attrs = _current_attributes(root)
    excluded = _excluded_tickers(root)
    memberships = _membership_by_key(root)
    seq = _sequence_labels(rows)
    enriched: List[Dict[str, Any]] = []
    quality_cache: Dict[str, str] = {}
    for row in rows:
        out = dict(row)
        ticker = str(out.get("ticker") or "").upper()
        date = str(out.get("appearance_date") or "")[:10]
        attr = attrs.get(ticker) or {}
        for field in (
            "fundamental_quality", "market_cap", "market_cap_bucket", "avg_dollar_volume",
            "liquidity_ok", "priority", "priority_label", "earliness_label", "extension_state",
            "scan_status", "same_session", "data_confidence", "program", "classification",
        ):
            if out.get(field) in (None, "") and attr.get(field) not in (None, ""):
                out[field] = attr.get(field)
        quality = str(out.get("fundamental_quality") or out.get("quality_label") or "").upper()
        if not quality:
            if ticker not in quality_cache:
                quality_cache[ticker] = _quality_from_fundamentals(root, ticker)
            quality = quality_cache[ticker]
        key = f"{ticker}|{date}"
        out["quality_label"] = quality or "UNKNOWN"
        out["profitability_bucket"] = _profitability_bucket(out["quality_label"])
        out["quality_bucket"] = _quality_bucket(out["quality_label"])
        out["security_type"] = _security_type(ticker)
        out["security_bucket"] = "COMMON_STOCK_OR_ADR" if out["security_type"] == "COMMON_STOCK_OR_ADR" else "NON_COMMON_SECURITY"
        out["market_cap_bucket"] = _market_cap_bucket(out.get("market_cap"), out.get("market_cap_bucket"))
        out["liquidity_bucket"] = _liquidity_bucket(out.get("avg_dollar_volume"))
        out["extension_bucket"] = _extension_bucket(out)
        out["candidate_sequence"] = seq.get(key, "UNKNOWN")
        out["membership_lanes"] = sorted(set(memberships.get(key) or []))
        out["membership_bucket"] = "+".join(out["membership_lanes"]) if out["membership_lanes"] else "BROAD_SCANNER_ONLY"
        out["source_lane"] = lane
        if ticker in excluded:
            out["source_cleanliness_bucket"] = "DEGRADED_SOURCE_EXCLUDED_TICKER"
        elif out.get("same_session") is True and str(out.get("data_confidence") or "").upper() == "HIGH":
            out["source_cleanliness_bucket"] = "SAME_SESSION_CLEAN"
        else:
            out["source_cleanliness_bucket"] = "HISTORICAL_OR_SOURCE_UNKNOWN"
        out["date_bucket"] = _date_bucket(out)
        enriched.append(out)
    return enriched


def _joined_lane_rows(root: Path, rel: Path, broad_by_key: Dict[str, Dict[str, Any]], lane: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for lane_row in _load_jsonl(root / rel):
        ticker = str(lane_row.get("ticker") or "").upper()
        date = str(lane_row.get("appearance_date") or "")[:10]
        base = dict(broad_by_key.get(f"{ticker}|{date}") or {})
        base.update({k: v for k, v in lane_row.items() if v is not None})
        base["ticker"] = ticker
        base["appearance_date"] = date
        base["source_lane"] = lane
        rows.append(base)
    return _enrich_rows(root, rows, lane=lane)


def _contributor(row: Dict[str, Any], horizon: int) -> Dict[str, Any]:
    return {
        "ticker": row.get("ticker"),
        "appearance_date": row.get("appearance_date"),
        "return_pct": row.get(f"ret_{horizon}d"),
        "program": row.get("research_program") or row.get("program"),
        "category": row.get("category"),
        "sector": row.get("sector"),
    }


def _outlier_analysis(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"top_1_abs_share_pct": None, "top_2_abs_share_pct": None, "driven_by_one_or_two_outliers": False}
    total_abs = sum(abs(v) for v in values)
    ordered = sorted((abs(v) for v in values), reverse=True)
    share1 = ordered[0] / total_abs * 100.0 if total_abs else 0.0
    share2 = sum(ordered[:2]) / total_abs * 100.0 if total_abs else 0.0
    return {
        "top_1_abs_share_pct": round(share1, 1),
        "top_2_abs_share_pct": round(share2, 1),
        "driven_by_one_or_two_outliers": len(values) >= 5 and share2 >= 50.0,
    }


def _horizon_stats(rows: List[Dict[str, Any]], horizon: int, price_dates: Sequence[str], total_negative_abs: float) -> Dict[str, Any]:
    field = f"ret_{horizon}d"
    expected_rows = [r for r in rows if _expected_mature(r, horizon, price_dates)]
    resolved_rows = [r for r in rows if r.get(field) is not None]
    values = [float(r[field]) for r in resolved_rows]
    positives = [r for r in resolved_rows if float(r[field]) > 0]
    negatives = [r for r in resolved_rows if float(r[field]) < 0]
    unresolved = max(len(expected_rows) - len(resolved_rows), 0)
    excess: Dict[str, Optional[float]] = {}
    for name in ("spy", "qqq", "iwm", "sector"):
        vals = [float(r[f"ret_{horizon}d_vs_{name}"]) for r in rows if r.get(f"ret_{horizon}d_vs_{name}") is not None]
        excess[f"excess_vs_{name}_pct"] = _mean(vals)
        excess[f"winsorized_excess_vs_{name}_pct"] = _winsorized_mean(vals)
    mae_vals = [float(r[f"mae_{horizon}d"]) for r in rows if r.get(f"mae_{horizon}d") is not None]
    neg_sum = sum(float(r[field]) for r in negatives)
    share = abs(neg_sum) / total_negative_abs * 100.0 if total_negative_abs > 0 else None
    resolved_sorted = sorted(resolved_rows, key=lambda r: float(r[field]))
    win_rate = len(positives) / len(resolved_rows) if resolved_rows else None
    return {
        "count": len(rows),
        "expected_mature_count": len(expected_rows),
        "matured_count": len(resolved_rows),
        "resolved_count": len(resolved_rows),
        "unresolved_count": unresolved,
        "resolution_pct": round(len(resolved_rows) / len(expected_rows) * 100.0, 1) if expected_rows else None,
        "mean_return_pct": _mean(values),
        "median_return_pct": _median(values),
        "winsorized_mean_return_pct": _winsorized_mean(values),
        "win_rate": round(win_rate, 3) if win_rate is not None else None,
        **excess,
        "max_adverse_excursion_pct": round(min(mae_vals), 2) if mae_vals else None,
        "avg_adverse_excursion_pct": _mean(mae_vals),
        "max_favorable_excursion_pct": None,
        "max_favorable_excursion_available": False,
        "negative_return_sum_pct_points": round(neg_sum, 2),
        "share_of_total_negative_return_pct": round(share, 1) if share is not None else None,
        "top_positive_contributors": [_contributor(r, horizon) for r in sorted(positives, key=lambda r: float(r[field]), reverse=True)[:5]],
        "top_negative_contributors": [_contributor(r, horizon) for r in resolved_sorted[:5]],
        "outlier_analysis": _outlier_analysis(values),
        "distinct_scan_dates": len({str(r.get("appearance_date") or "")[:10] for r in rows if r.get("appearance_date")}),
        "scan_date_sample": sorted({str(r.get("appearance_date") or "")[:10] for r in rows if r.get("appearance_date")})[:8],
        "sample_status": _sample_status(len(resolved_rows)),
        "mature_enough_to_interpret": len(resolved_rows) >= MIN_MATURED_TO_INTERPRET,
    }


def _total_negative_abs_by_horizon(rows: List[Dict[str, Any]]) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for horizon in HORIZONS:
        vals = [float(r[f"ret_{horizon}d"]) for r in rows if r.get(f"ret_{horizon}d") is not None and float(r[f"ret_{horizon}d"]) < 0]
        out[horizon] = abs(sum(vals))
    return out


def _cohort(cohort_id: str, label: str, family: str, rows: List[Dict[str, Any]], price_dates: Sequence[str], total_negative_abs: Dict[int, float], rule: str) -> Dict[str, Any]:
    horizons = {f"{h}d": _horizon_stats(rows, h, price_dates, total_negative_abs.get(h, 0.0)) for h in HORIZONS}
    primary = horizons[f"{PRIMARY_HORIZON}d"]
    result = _result_label(primary["matured_count"], primary["mean_return_pct"], primary["win_rate"])
    return {
        "id": cohort_id,
        "label": label,
        "family": family,
        "membership_rule": rule,
        "count": len(rows),
        "unique_tickers_count": len({str(r.get("ticker") or "").upper() for r in rows if r.get("ticker")}),
        "distinct_scan_dates": primary["distinct_scan_dates"],
        "scan_date_sample": primary["scan_date_sample"],
        "primary_horizon": f"{PRIMARY_HORIZON}d",
        "primary_result": result,
        "primary_result_text": _human_result(result, primary["matured_count"]),
        "sample_status": primary["sample_status"],
        "mature_enough_to_interpret": primary["mature_enough_to_interpret"],
        "horizons": horizons,
    }


def _add(cohorts: List[Dict[str, Any]], cohort_id: str, label: str, family: str, rows: List[Dict[str, Any]], price_dates: Sequence[str], total_negative_abs: Dict[int, float], rule: str, *, include_empty: bool = False) -> None:
    if rows or include_empty:
        cohorts.append(_cohort(cohort_id, label, family, rows, price_dates, total_negative_abs, rule))


def _group(rows: Iterable[Dict[str, Any]], keyfn: Callable[[Dict[str, Any]], str]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = keyfn(row) or "UNKNOWN"
        out.setdefault(str(key), []).append(row)
    return out


def _slug(text: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(text)).strip("_") or "unknown"


def _rank_item(cohort: Optional[Dict[str, Any]], horizon: str) -> Optional[Dict[str, Any]]:
    if not cohort:
        return None
    h = cohort["horizons"][horizon]
    return {
        "id": cohort["id"],
        "label": cohort["label"],
        "family": cohort["family"],
        "matured_count": h.get("matured_count"),
        "mean_return_pct": h.get("mean_return_pct"),
        "median_return_pct": h.get("median_return_pct"),
        "winsorized_mean_return_pct": h.get("winsorized_mean_return_pct"),
        "outlier_flagged": outlier_driven(h),
        "headline_mean_return_pct": headline_mean_return_pct(h),
        "win_rate": h.get("win_rate"),
        "excess_vs_spy_pct": h.get("excess_vs_spy_pct"),
        "headline_excess_vs_spy_pct": headline_excess_pct(h, "spy"),
        "negative_return_sum_pct_points": h.get("negative_return_sum_pct_points"),
        "share_of_total_negative_return_pct": h.get("share_of_total_negative_return_pct"),
        "sample_status": h.get("sample_status"),
        "primary_result": cohort.get("primary_result"),
    }


def _rankings(cohorts: List[Dict[str, Any]], horizon: str = "10d") -> Dict[str, Any]:
    candidates = [c for c in cohorts if c["horizons"].get(horizon, {}).get("mature_enough_to_interpret")]
    if not candidates:
        candidates = [c for c in cohorts if c["horizons"].get(horizon, {}).get("matured_count", 0) >= MIN_MATURED_TO_EVALUATE]
    worst = sorted(candidates, key=lambda c: (float(c["horizons"][horizon].get("negative_return_sum_pct_points") or 0.0), c["id"]))[:10]
    best = sorted(candidates, key=lambda c: (-(float(headline_mean_return_pct(c["horizons"][horizon]) or -1e9)), c["id"]))[:10]
    excluded = {
        "broad_scanner",
        "did_not_qualify",
        "era_pre_high_conviction",
        "security_common",
        "source_historical_or_source_unknown",
        "market_cap_bucket_unknown",
        "quality_unknown_quality",
        "profitability_unknown",
    }
    explanatory = [
        c for c in candidates
        if c.get("id") not in excluded
        and not str(c.get("id") or "").endswith("_unknown")
        and c.get("family") not in {"source_cleanliness", "date_bucket"}
    ]
    expl_worst = sorted(explanatory, key=lambda c: (float(c["horizons"][horizon].get("negative_return_sum_pct_points") or 0.0), c["id"]))[:10]
    return {
        "horizon": horizon,
        "worst_by_negative_sum": [_rank_item(c, horizon) for c in worst],
        "best_by_mean_return": [_rank_item(c, horizon) for c in best],
        "biggest_drag": _rank_item(worst[0], horizon) if worst else None,
        "biggest_explanatory_drag": _rank_item(expl_worst[0], horizon) if expl_worst else (_rank_item(worst[0], horizon) if worst else None),
        "explanatory_worst_by_negative_sum": [_rank_item(c, horizon) for c in expl_worst],
        "best_current": _rank_item(best[0], horizon) if best else None,
    }


def _find(cohorts: List[Dict[str, Any]], cohort_id: str) -> Optional[Dict[str, Any]]:
    return next((c for c in cohorts if c.get("id") == cohort_id), None)


def _summary_line(cohort: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not cohort:
        return {"status": "missing", "matured_count": 0, "result": "missing"}
    h = cohort["horizons"][f"{PRIMARY_HORIZON}d"]
    return {
        "status": cohort.get("primary_result"),
        "result": cohort.get("primary_result_text"),
        "matured_count": h.get("matured_count"),
        "mean_return_pct": h.get("mean_return_pct"),
        "winsorized_mean_return_pct": h.get("winsorized_mean_return_pct"),
        "outlier_flagged": outlier_driven(h),
        "headline_mean_return_pct": headline_mean_return_pct(h),
        "win_rate": h.get("win_rate"),
        "sample_status": h.get("sample_status"),
    }


def _dashboard_summary(cohorts: List[Dict[str, Any]], rankings: Dict[str, Any]) -> Dict[str, Any]:
    broad = _find(cohorts, "broad_scanner")
    hc = _find(cohorts, "high_conviction_alpha")
    eo = _find(cohorts, "emerging_outlier_watch")
    warnings = []
    for name, cohort in (("High Conviction", hc), ("Emerging Outlier", eo)):
        if not cohort or cohort["horizons"]["10d"].get("matured_count", 0) < MIN_MATURED_TO_EVALUATE:
            warnings.append(f"{name}: Too early to evaluate.")
    return {
        "broad_scanner": _summary_line(broad),
        "high_conviction": _summary_line(hc),
        "emerging_outlier": _summary_line(eo),
        "biggest_drag_cohort": rankings.get("biggest_explanatory_drag") or rankings.get("biggest_drag"),
        "best_current_cohort": rankings.get("best_current"),
        "sample_maturity_warning": " ".join(warnings) if warnings else None,
        "what_not_to_conclude": "Do not infer alpha from immature HC/EO samples; cleaned evidence is diagnostic, not a positive alpha claim.",
    }


def _special_focus(cohorts: List[Dict[str, Any]]) -> Dict[str, Any]:
    ids = {
        "unprofitable": "profitability_unprofitable",
        "profitable": "profitability_profitable",
        "extreme_rs_momentum": "rs_momentum_leaders",
        "beaten_down_or_early_accumulation": "beaten_down_or_early_accumulation",
        "healthcare_biotech": "sector_healthcare",
        "small_micro_caps": "market_cap_small_micro",
        "low_liquidity": "liquidity_low_or_very_low",
        "extended": "extension_extended",
        "reset_or_watch_for_entry": "extension_reset_or_watch_for_entry",
        "non_common_securities": "security_non_common",
        "pre_high_conviction_era": "era_pre_high_conviction",
    }
    return {key: _rank_item(_find(cohorts, cohort_id), "10d") for key, cohort_id in ids.items()}


def build_report(root: Optional[Path] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()
    forward = _load_json(root / FORWARD_REL) or {}
    summary = _load_json(root / SUMMARY_REL) or {}
    raw_history = _load_jsonl(root / HISTORY_REL)
    history = _enrich_rows(root, raw_history, lane="BROAD_SCANNER")
    broad_by_key = {f"{str(r.get('ticker') or '').upper()}|{str(r.get('appearance_date') or '')[:10]}": r for r in history}
    hc_rows = _joined_lane_rows(root, HC_HISTORY_REL, broad_by_key, "HIGH_CONVICTION_ALPHA")
    eo_rows = _joined_lane_rows(root, EO_HISTORY_REL, broad_by_key, "EMERGING_OUTLIER_WATCH")
    price_dates = _read_price_dates(root, "SPY")
    total_negative_abs = _total_negative_abs_by_horizon(history)
    cohorts: List[Dict[str, Any]] = []

    _add(cohorts, "broad_scanner", "Broad scanner", "core", history, price_dates, total_negative_abs, "all research_watchlist_history rows", include_empty=True)
    _add(cohorts, "high_conviction_alpha", "High-Conviction Alpha", "core", hc_rows, price_dates, total_negative_abs, "high_conviction_history joined to repaired forward rows", include_empty=True)
    _add(cohorts, "emerging_outlier_watch", "Emerging Outlier Watch", "core", eo_rows, price_dates, total_negative_abs, "emerging_outlier_history joined to repaired forward rows", include_empty=True)

    for program in ("TACTICAL", "SWING", "LONG_TERM"):
        rows = [r for r in history if str(r.get("research_program") or r.get("program") or "").upper() == program]
        _add(cohorts, f"program_{program.lower()}", f"{program.replace('_', ' ').title()} Research", "program", rows, price_dates, total_negative_abs, f"research_program == {program}", include_empty=True)

    reset_rows = [r for r in history if r.get("extension_bucket") == "RESET_OR_WATCH_FOR_ENTRY"]
    did_not_qualify = [r for r in history if r.get("membership_bucket") == "BROAD_SCANNER_ONLY"]
    _add(cohorts, "strong_profiles_wait_for_reset", "Strong Profiles - Wait for Reset", "qualification", reset_rows, price_dates, total_negative_abs, "reset/reclaim/watch-for-entry labels")
    _add(cohorts, "did_not_qualify", "Did Not Qualify", "qualification", did_not_qualify, price_dates, total_negative_abs, "broad scanner rows with no HC/EO membership")

    category_sets = {
        "beaten_down_or_early_accumulation": ("Beaten-down / early accumulation", {"beaten_down_recovery", "early_accumulation"}),
        "beaten_down": ("Beaten-down names", {"beaten_down_recovery"}),
        "early_accumulation": ("Early accumulation names", {"early_accumulation"}),
        "rs_momentum_leaders": ("RS momentum leaders", {"rs_momentum_leader"}),
        "catalyst_names": ("Catalyst names", {"catalyst_watch"}),
        "social_attention_names": ("Social attention names", {"social_arb_attention"}),
    }
    for cid, (label, cats) in category_sets.items():
        rows = [r for r in history if str(r.get("category") or "") in cats or str(r.get("watchlist_label") or "").lower() in cats]
        _add(cohorts, cid, label, "scanner_category", rows, price_dates, total_negative_abs, f"category in {sorted(cats)}")

    for bucket, rows in sorted(_group(history, lambda r: r.get("profitability_bucket") or "UNKNOWN").items()):
        _add(cohorts, f"profitability_{bucket.lower()}", bucket.title(), "profitability", rows, price_dates, total_negative_abs, f"profitability_bucket == {bucket}")
    for bucket, rows in sorted(_group(history, lambda r: r.get("quality_bucket") or "UNKNOWN_QUALITY").items()):
        _add(cohorts, f"quality_{bucket.lower()}", bucket.replace("_", " ").title(), "quality", rows, price_dates, total_negative_abs, f"quality_bucket == {bucket}")
    for bucket, rows in sorted(_group(history, lambda r: r.get("extension_bucket") or "NORMAL_OR_UNKNOWN").items()):
        _add(cohorts, f"extension_{bucket.lower()}", bucket.replace("_", " ").title(), "extension", rows, price_dates, total_negative_abs, f"extension_bucket == {bucket}")

    common_rows = [r for r in history if r.get("security_bucket") == "COMMON_STOCK_OR_ADR"]
    non_common_rows = [r for r in history if r.get("security_bucket") == "NON_COMMON_SECURITY"]
    _add(cohorts, "security_common", "Common stocks / ADRs", "security_type", common_rows, price_dates, total_negative_abs, "ticker format is common stock or ADR")
    _add(cohorts, "security_non_common", "Non-common securities", "security_type", non_common_rows, price_dates, total_negative_abs, "warrant/unit/right/share-class ticker format")

    large_mega = [r for r in history if r.get("market_cap_bucket") in {"LARGE_CAP", "MEGA_CAP"}]
    mid = [r for r in history if r.get("market_cap_bucket") == "MID_CAP"]
    small_micro = [r for r in history if r.get("market_cap_bucket") in {"SMALL_CAP", "MICRO_CAP"}]
    _add(cohorts, "market_cap_large_mega", "Large/mega-cap", "market_cap", large_mega, price_dates, total_negative_abs, "market cap bucket LARGE_CAP or MEGA_CAP")
    _add(cohorts, "market_cap_mid", "Mid-cap", "market_cap", mid, price_dates, total_negative_abs, "market cap bucket MID_CAP")
    _add(cohorts, "market_cap_small_micro", "Small/micro-cap", "market_cap", small_micro, price_dates, total_negative_abs, "market cap bucket SMALL_CAP or MICRO_CAP")
    for bucket, rows in sorted(_group(history, lambda r: r.get("market_cap_bucket") or "UNKNOWN").items()):
        _add(cohorts, f"market_cap_bucket_{bucket.lower()}", bucket.replace("_", " ").title(), "market_cap_bucket", rows, price_dates, total_negative_abs, f"market_cap_bucket == {bucket}")

    low_liq = [r for r in history if r.get("liquidity_bucket") in {"LOW_LIQUIDITY", "VERY_LOW_LIQUIDITY"}]
    _add(cohorts, "liquidity_low_or_very_low", "Low-liquidity names", "liquidity", low_liq, price_dates, total_negative_abs, "avg dollar volume below 10M where known")

    for sector, rows in sorted(_group(history, lambda r: r.get("sector") or "UNKNOWN").items()):
        _add(cohorts, "sector_" + _slug(sector), f"Sector - {sector}", "sector", rows, price_dates, total_negative_abs, f"sector == {sector}")
    for bucket, rows in sorted(_group(history, lambda r: r.get("candidate_sequence") or "UNKNOWN").items()):
        _add(cohorts, f"sequence_{bucket.lower()}", f"{bucket.title()} candidates", "candidate_sequence", rows, price_dates, total_negative_abs, f"candidate_sequence == {bucket}")
    for bucket, rows in sorted(_group(history, lambda r: r.get("source_cleanliness_bucket") or "UNKNOWN").items()):
        _add(cohorts, f"source_{bucket.lower()}", bucket.replace("_", " ").title(), "source_cleanliness", rows, price_dates, total_negative_abs, f"source_cleanliness_bucket == {bucket}")

    hc_dates = [str(r.get("appearance_date") or "")[:10] for r in _load_jsonl(root / HC_HISTORY_REL) if r.get("appearance_date")]
    hc_start = min(hc_dates) if hc_dates else None
    if hc_start:
        _add(cohorts, "era_pre_high_conviction", "Pre-High-Conviction-era candidates", "date_window", [r for r in history if str(r.get("appearance_date") or "")[:10] < hc_start], price_dates, total_negative_abs, f"appearance_date < {hc_start}")
        _add(cohorts, "era_high_conviction", "High-Conviction-era candidates", "date_window", [r for r in history if str(r.get("appearance_date") or "")[:10] >= hc_start], price_dates, total_negative_abs, f"appearance_date >= {hc_start}")
    for bucket, rows in sorted(_group(history, lambda r: r.get("date_bucket") or "UNKNOWN").items()):
        _add(cohorts, f"date_bucket_{bucket.replace('-', '_')}", f"Date bucket {bucket}", "date_bucket", rows, price_dates, total_negative_abs, f"appearance month == {bucket}")

    rankings = _rankings(cohorts, "10d")
    dashboard = _dashboard_summary(cohorts, rankings)
    alpha_proven = (summary.get("forward_evidence") or {}).get("alpha_proven")
    return {
        "kind": "cohort_attribution",
        "version": VERSION,
        "generated_at": now.isoformat(),
        "research_only": True,
        "source_artifacts": {
            "forward": str(FORWARD_REL),
            "watchlist_history": str(HISTORY_REL),
            "high_conviction_history": str(HC_HISTORY_REL),
            "emerging_outlier_history": str(EO_HISTORY_REL),
            "scanner": str(SCANNER_REL),
            "routing": str(ROUTING_REL),
            "scan_exclusion_impact": str(SCAN_EXCLUSION_REL),
        },
        "source_timestamps": {
            "forward_generated_at": forward.get("generated_at"),
            "scanner_generated_at": (_load_json(root / SCANNER_REL) or {}).get("generated_at"),
            "routing_generated_at": (_load_json(root / ROUTING_REL) or {}).get("generated_at"),
        },
        "horizons": [f"{h}d" for h in HORIZONS],
        "primary_horizon": f"{PRIMARY_HORIZON}d",
        "overall_forward": forward.get("overall") or {},
        "alpha_proven": alpha_proven,
        "cohorts": cohorts,
        "rankings": rankings,
        "dashboard_summary": dashboard,
        "special_focus": _special_focus(cohorts),
        "data_gaps": {
            "max_favorable_excursion": "not recorded in research_watchlist_history; left null",
            "market_regime_at_entry": "not recorded per episode; current regime cannot be back-applied",
            "historical_same_session_cleanliness": "only current routing sidecar has same_session/data_confidence; older rows are HISTORICAL_OR_SOURCE_UNKNOWN",
            "profitability_quality": "derived from cached fundamentals or current routing when absent from history; UNKNOWN is preserved",
        },
        "guardrails": {
            "no_scanner_logic_change": True,
            "no_selection_change": True,
            "no_score_or_ranking_change": True,
            "no_gate_threshold_or_factor_weight_change": True,
            "no_high_conviction_rule_change": True,
            "no_emerging_outlier_rule_change": True,
            "research_only": True,
            "cache_only_inputs": True,
            "hc_eo_immature_not_overstated": True,
        },
        "caveats": [
            "Cohorts overlap; contribution shares are attribution diagnostics and may sum above 100%.",
            "Best cohort is not an alpha claim unless sample maturity and statistical evidence support it.",
            "High-Conviction and Emerging Outlier are separate hypotheses and are too early at 10d if matured_count < 10.",
        ],
    }


def _fmt_pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}%"


def _outlier_note(h: Dict[str, Any]) -> str:
    if not outlier_driven(h):
        return ""
    return f" [outlier-capped; raw mean {_fmt_pct(h.get('mean_return_pct'))}]"


def _cohort_brief(cohort: Optional[Dict[str, Any]], horizon: str = "10d") -> str:
    if not cohort:
        return "missing"
    h = cohort["horizons"][horizon]
    wr = h.get("win_rate")
    return (
        f"{cohort['label']}: n={h.get('matured_count')}, "
        f"win={_fmt_pct(wr * 100 if wr is not None else None)}, "
        f"mean={_fmt_pct(headline_mean_return_pct(h))}, "
        f"vs SPY={_fmt_pct(headline_excess_pct(h, 'spy'))}, "
        f"status={cohort.get('primary_result_text')}"
        f"{_outlier_note(h)}"
    )


def render_text(report: Dict[str, Any]) -> str:
    cohorts = report.get("cohorts") or []
    find = lambda cid: next((c for c in cohorts if c.get("id") == cid), None)
    dash = report.get("dashboard_summary") or {}
    worst = dash.get("biggest_drag_cohort") or {}
    best = dash.get("best_current_cohort") or {}
    lines = [
        f"COHORT ATTRIBUTION - {report.get('generated_at')}",
        f"Research only: {report.get('research_only')}",
        f"Alpha proven: {report.get('alpha_proven')}",
        f"Forward verdict: {(report.get('overall_forward') or {}).get('verdict')}",
        "",
        _cohort_brief(find("broad_scanner")),
        _cohort_brief(find("high_conviction_alpha")),
        _cohort_brief(find("emerging_outlier_watch")),
        "",
        f"Biggest drag cohort: {worst.get('label')} mean={_fmt_pct(worst.get('headline_mean_return_pct', worst.get('mean_return_pct')))} negative_sum={_fmt_pct(worst.get('negative_return_sum_pct_points'))}"
        f"{' [outlier-capped; raw mean ' + _fmt_pct(worst.get('mean_return_pct')) + ']' if worst.get('outlier_flagged') else ''}",
        f"Best current cohort: {best.get('label')} mean={_fmt_pct(best.get('headline_mean_return_pct', best.get('mean_return_pct')))} win={_fmt_pct((best.get('win_rate') or 0) * 100 if best.get('win_rate') is not None else None)}"
        f"{' [outlier-capped; raw mean ' + _fmt_pct(best.get('mean_return_pct')) + ']' if best.get('outlier_flagged') else ''}",
        f"Sample warning: {dash.get('sample_maturity_warning') or 'none'}",
        "",
        "Special focus (10d):",
    ]
    for key, item in sorted((report.get("special_focus") or {}).items()):
        if not item:
            lines.append(f"- {key}: no data")
        else:
            wr = item.get("win_rate")
            outlier_suffix = (
                f" [outlier-capped; raw mean {_fmt_pct(item.get('mean_return_pct'))}]"
                if item.get("outlier_flagged") else ""
            )
            lines.append(
                f"- {key}: {item.get('label')} n={item.get('matured_count')} "
                f"mean={_fmt_pct(item.get('headline_mean_return_pct', item.get('mean_return_pct')))} "
                f"win={_fmt_pct(wr * 100 if wr is not None else None)}{outlier_suffix}"
            )
    lines.extend(["", "Caveats:"])
    lines.extend(f"- {c}" for c in report.get("caveats") or [])
    return "\n".join(lines) + "\n"


def render_markdown(report: Dict[str, Any]) -> str:
    dash = report.get("dashboard_summary") or {}
    worst = dash.get("biggest_drag_cohort") or {}
    best = dash.get("best_current_cohort") or {}
    return "\n".join([
        "# Cohort Attribution Review",
        "",
        f"Generated: `{report.get('generated_at')}`",
        "",
        "This is an evidence-analysis artifact only. It does not change scanner, scoring, routing, gates, thresholds, factor weights, High-Conviction rules, Emerging Outlier rules, or research-only safeguards.",
        "",
        "## Summary",
        f"- Forward verdict: `{(report.get('overall_forward') or {}).get('verdict')}`",
        f"- Alpha proven: `{report.get('alpha_proven')}`",
        f"- Biggest drag cohort: `{worst.get('label')}`",
        f"- Best current cohort: `{best.get('label')}`",
        f"- Sample maturity warning: {dash.get('sample_maturity_warning') or 'none'}",
        "",
        "## What Not To Conclude",
        str(dash.get("what_not_to_conclude") or ""),
        "",
    ]) + "\n"


def write_artifacts(report: Dict[str, Any], root: Optional[Path] = None, *, docs: bool = False) -> Dict[str, str]:
    root = Path(root) if root else REPO_ROOT
    json_path = root / OUT_JSON_REL
    txt_path = root / OUT_TXT_REL
    _write_json(json_path, report)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(render_text(report), encoding="utf-8")
    paths = {"json": str(json_path), "text": str(txt_path)}
    if docs:
        doc_path = root / DOC_REL
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(render_markdown(report), encoding="utf-8")
        paths["docs"] = str(doc_path)
    return paths


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build research cohort attribution sidecars.")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--docs", action="store_true", help="Also write docs/research/COHORT_ATTRIBUTION_REVIEW.md")
    parser.add_argument("--json", action="store_true", help="Print the report JSON to stdout")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT
    report = build_report(root)
    paths = write_artifacts(report, root, docs=args.docs)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"wrote {paths['json']}")
        print(f"wrote {paths['text']}")
        if "docs" in paths:
            print(f"wrote {paths['docs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
