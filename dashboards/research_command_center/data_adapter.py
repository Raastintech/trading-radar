"""Research Command Center — read-only data adapter.

Normalizes the research engine's JSON sidecars into dashboard models:
status strip, heatmap leaderboard rows, and per-ticker detail.

Doctrine:
  - RESEARCH_ONLY.  No signals, no execution, no order language.
  - CACHE-ONLY.  Reads artifacts written by the research cycle; never calls
    providers, never invokes scanners, never writes anything anywhere.
  - CRED-FREE.  No dependency on the env-validated config module, so it
    runs without any env vars.
  - HONEST.  Missing/immature/suspect data is surfaced as explicit fallback
    states, never hidden or interpolated.

Fallback states (per row `warnings` and board-level `fallback`):
  NO_DATA, MISSING_ARTIFACT, IMMATURE_FORWARD_EVIDENCE,
  INSUFFICIENT_HISTORY, STALE_PRICE, SUSPECT_FEED, BENCHMARK_MISSING
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]

RESEARCH_ONLY_FOOTER = "Research only — not a signal or recommendation."

# Fallback / data-quality states
NO_DATA = "NO_DATA"
MISSING_ARTIFACT = "MISSING_ARTIFACT"
IMMATURE_FORWARD_EVIDENCE = "IMMATURE_FORWARD_EVIDENCE"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
STALE_PRICE = "STALE_PRICE"
SUSPECT_FEED = "SUSPECT_FEED"
BENCHMARK_MISSING = "BENCHMARK_MISSING"

# A price older than this (calendar days) is flagged STALE_PRICE.  Matches the
# scanner's own --max-stale-days default; display-only, changes no behavior.
STALE_PRICE_DAYS = 7

# Scanner sidecar older than this (hours) renders the freshness chip STALE.
FRESHNESS_STALE_HOURS = 18.0


@dataclass
class ArtifactStore:
    """Resolved paths to every artifact the dashboard reads.  Injectable root
    so tests point at a tmp dir and can simulate missing artifacts."""

    root: Path = field(default_factory=lambda: ROOT)

    @property
    def research_dir(self) -> Path:
        return self.root / "cache" / "research"

    @property
    def scanner_json(self) -> Path:
        return self.research_dir / "research_scanner_latest.json"

    @property
    def forward_json(self) -> Path:
        return self.research_dir / "research_forward_latest.json"

    @property
    def radar_json(self) -> Path:
        return self.research_dir / "daily_alpha_radar_latest.json"

    @property
    def summary_json(self) -> Path:
        return self.research_dir / "nightly_operator_summary_latest.json"

    @property
    def universe_json(self) -> Path:
        return self.research_dir / "research_universe_build_latest.json"

    @property
    def history_jsonl(self) -> Path:
        return self.root / "data" / "research" / "research_watchlist_history.jsonl"

    @property
    def moment_of_truth_json(self) -> Path:
        return self.research_dir / "moment_of_truth_2026_06_27.json"

    @property
    def prices_dir(self) -> Path:
        return self.root / "cache" / "prices"

    @property
    def prices_deep_dir(self) -> Path:
        return self.root / "cache" / "prices_deep"


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _age_hours(iso_ts: Optional[str]) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    except Exception:
        return None


def _days_since(date_s: Optional[str]) -> Optional[int]:
    if not date_s:
        return None
    try:
        d = datetime.strptime(str(date_s)[:10], "%Y-%m-%d").date()
        return (datetime.now(timezone.utc).date() - d).days
    except Exception:
        return None


# ── Status strip ─────────────────────────────────────────────────────────────


def build_status(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Top status strip model.  Every field degrades to an explicit unknown."""
    store = store or ArtifactStore()
    summary = _load_json(store.summary_json)
    scanner = _load_json(store.scanner_json)
    forward = _load_json(store.forward_json)
    radar = _load_json(store.radar_json)

    missing = [
        name for name, obj in [
            ("nightly_operator_summary", summary),
            ("research_scanner", scanner),
            ("research_forward", forward),
            ("daily_alpha_radar", radar),
        ] if obj is None
    ]

    overall = (summary or {}).get("overall_status") or {}
    market = (summary or {}).get("market_context") or {}
    fwd = (forward or {}).get("overall") or {}

    tracker_verdict = fwd.get("verdict") or (summary or {}).get(
        "forward_evidence", {}).get("verdict")
    # Operating verdict is intentionally conservative: anything short of an
    # affirmative PROMISING tracker verdict stays NEED_MORE_DATA.  Mirrors the
    # 2026-07-02 delta-report doctrine; display-only.
    operating_verdict = "PASS" if tracker_verdict == "PROMISING" else "NEED_MORE_DATA"
    phase_4b = {
        "status": "UNBLOCKED" if operating_verdict == "PASS" else "BLOCKED",
        "reason": (
            "forward evidence insufficient"
            + (f" (tracker verdict: {tracker_verdict})" if tracker_verdict else "")
        ) if operating_verdict != "PASS" else "gates met",
    }

    scanner_age_h = _age_hours((scanner or {}).get("generated_at"))
    freshness = "UNKNOWN"
    if scanner_age_h is not None:
        freshness = "FRESH" if scanner_age_h <= FRESHNESS_STALE_HOURS else "STALE"

    quarantine = (radar or {}).get("quarantine_breakdown") or {}

    return {
        "mode": "RESEARCH_ONLY",
        "research_only": True,
        "nightly_status": overall.get("overall") or "UNKNOWN",
        "nightly_completed": bool(overall.get("nightly_completed")),
        "market_regime": market.get("regime") or "UNKNOWN",
        "phase_4b": phase_4b,
        "tracker_verdict": tracker_verdict or "UNKNOWN",
        "operating_verdict": operating_verdict,
        "matured_5d": fwd.get("matured_5d_entries"),
        "matured_10d": fwd.get("matured_entries"),
        "total_tracked": fwd.get("total_entries"),
        "sample_status": fwd.get("sample_status"),
        "quarantine_breakdown": quarantine or {},
        "benchmark_readiness": (summary or {}).get("forward_evidence", {}).get(
            "benchmark_readiness") or "UNKNOWN",
        "data_freshness": freshness,
        "scanner_age_hours": round(scanner_age_h, 1) if scanner_age_h is not None else None,
        "stale_skipped": (scanner or {}).get("stale_price_skipped_count"),
        "suspect_skipped": (scanner or {}).get("data_suspect_skipped_count"),
        "quarantine_count": sum(quarantine.values()) if quarantine else None,
        "warnings": (summary or {}).get("warnings") or [],
        "missing_artifacts": missing,
        "fallback": MISSING_ARTIFACT if missing else None,
        "generated_at": (scanner or {}).get("generated_at"),
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Forward evidence per ticker ──────────────────────────────────────────────


def _latest_forward_by_ticker(store: ArtifactStore) -> Dict[str, Dict[str, Any]]:
    """Most recent history entry per ticker (prefers entries with returns)."""
    out: Dict[str, Dict[str, Any]] = {}
    path = store.history_jsonl
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            t = str(e.get("ticker") or "").upper()
            if not t:
                continue
            prev = out.get(t)
            # Prefer the entry with the most matured horizons; tie-break on
            # the later appearance_date.
            def _rank(x: Dict[str, Any]):
                horizons = sum(1 for k in ("ret_5d", "ret_10d", "ret_20d")
                               if x.get(k) is not None)
                return (horizons, str(x.get("appearance_date") or ""))
            if prev is None or _rank(e) >= _rank(prev):
                out[t] = e
    except Exception:
        return out
    return out


def _forward_block(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    e = entry or {}
    return {
        "appearance_date": e.get("appearance_date"),
        "returns": {
            "5d": e.get("ret_5d"),
            "10d": e.get("ret_10d"),
            "20d": e.get("ret_20d"),
        },
        "benchmarks": {
            "spy": e.get("ret_5d_vs_spy") if e.get("ret_5d_vs_spy") is not None else e.get("ret_10d_vs_spy"),
            "qqq": e.get("ret_5d_vs_qqq") if e.get("ret_5d_vs_qqq") is not None else e.get("ret_10d_vs_qqq"),
            "sector": e.get("ret_5d_vs_sector") if e.get("ret_5d_vs_sector") is not None else e.get("ret_10d_vs_sector"),
        },
        "benchmarks_10d": {
            "spy": e.get("ret_10d_vs_spy"),
            "qqq": e.get("ret_10d_vs_qqq"),
            "sector": e.get("ret_10d_vs_sector"),
        },
        "sector_etf": e.get("benchmark_sector_etf"),
        "forward_maturity": {
            "has_5d": e.get("ret_5d") is not None,
            "has_10d": e.get("ret_10d") is not None,
            "has_20d": e.get("ret_20d") is not None,
        },
    }


# ── Leaderboard ──────────────────────────────────────────────────────────────


def _priority_by_ticker(radar: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Radar priority label per ticker.  The artifact stores
    `priority_tickers` as a dict {label: [tickers]}; older snapshots used a
    list of [label, tickers] pairs — accept both."""
    out: Dict[str, str] = {}
    raw = (radar or {}).get("priority_tickers") or {}
    pairs = raw.items() if isinstance(raw, dict) else raw
    for pair in pairs:
        try:
            label, tickers = pair
        except Exception:
            continue
        for t in tickers or []:
            out.setdefault(str(t).upper(), str(label))
    return out


def _source_by_ticker(universe: Optional[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for e in ((universe or {}).get("universe_full") or []):
        t = str(e.get("ticker") or "").upper()
        if t:
            out[t] = str(e.get("source") or "unknown")
    return out


def _row_quality(item: Dict[str, Any], warnings: List[str]) -> str:
    if SUSPECT_FEED in warnings or INSUFFICIENT_HISTORY in warnings:
        return "QUARANTINE"
    if warnings:
        return "WARN"
    conf = str(item.get("data_confidence") or "").upper()
    if conf == "LOW":
        return "WARN"
    return "OK"


def build_leaderboard(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Heatmap leaderboard rows from the scanner watchlist + overlays."""
    store = store or ArtifactStore()
    scanner = _load_json(store.scanner_json)
    if scanner is None:
        return {"rows": [], "fallback": MISSING_ARTIFACT,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    radar = _load_json(store.radar_json)
    universe = _load_json(store.universe_json)
    fwd_by_ticker = _latest_forward_by_ticker(store)
    prio = _priority_by_ticker(radar)
    src = _source_by_ticker(universe)
    suspect = {s.upper() for s in scanner.get("data_suspect_skipped_sample") or []}
    stale_px = {s.upper() for s in scanner.get("stale_price_skipped_sample") or []}

    rows: List[Dict[str, Any]] = []
    for item in scanner.get("watchlist") or []:
        t = str(item.get("ticker") or "").upper()
        if not t:
            continue
        fwd_entry = fwd_by_ticker.get(t)
        fwd = _forward_block(fwd_entry)

        warnings: List[str] = []
        price = item.get("latest_close")
        price_age = _days_since(item.get("latest_price_date"))
        if price is None:
            warnings.append(NO_DATA)
        elif price_age is not None and price_age > STALE_PRICE_DAYS:
            warnings.append(STALE_PRICE)
        if t in stale_px:
            if STALE_PRICE not in warnings:
                warnings.append(STALE_PRICE)
        if t in suspect:
            warnings.append(SUSPECT_FEED)
        if item.get("insufficient_history_for_ma200") or (
                item.get("bars_available") is not None
                and item["bars_available"] < 63):
            warnings.append(INSUFFICIENT_HISTORY)
        if not any(fwd["forward_maturity"].values()):
            warnings.append(IMMATURE_FORWARD_EVIDENCE)
        elif all(v is None for v in fwd["benchmarks"].values()):
            warnings.append(BENCHMARK_MISSING)

        rows.append({
            "ticker": t,
            "company": item.get("company_name"),
            "price": price,
            "price_date": item.get("latest_price_date"),
            "returns": {
                "1d": None,  # trailing 1d not published by the scanner sidecar
                "5d": fwd["returns"]["5d"],
                "10d": fwd["returns"]["10d"],
                "20d": fwd["returns"]["20d"],
            },
            "benchmarks": fwd["benchmarks"],
            "rs_20d_vs_spy": item.get("rs_20d_vs_spy"),
            "rs_63d_vs_spy": item.get("rs_63d_vs_spy"),
            "sector": item.get("sector"),
            "sector_etf": item.get("company_sector_etf") or fwd.get("sector_etf"),
            "label": prio.get(t) or item.get("watchlist_label"),
            "watchlist_label": item.get("watchlist_label"),
            "scanner_category": item.get("category"),
            "all_categories": item.get("all_categories") or [],
            "source": src.get(t, "unknown"),
            "confidence": item.get("data_confidence"),
            "research_score": item.get("research_score"),
            "data_quality": _row_quality(item, warnings),
            "warnings": warnings,
            "forward_maturity": fwd["forward_maturity"],
            "research_only_footer": RESEARCH_ONLY_FOOTER,
        })

    rows.sort(key=lambda r: -(r.get("research_score") or 0))
    return {
        "rows": rows,
        "count": len(rows),
        "generated_at": scanner.get("generated_at"),
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Ticker detail ────────────────────────────────────────────────────────────


def build_ticker_detail(ticker: str,
                        store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Right-drawer model for one ticker.  Always returns a dict; unknown
    tickers get an explicit NO_DATA fallback."""
    store = store or ArtifactStore()
    t = str(ticker or "").upper()
    scanner = _load_json(store.scanner_json)
    if scanner is None:
        return {"ticker": t, "fallback": MISSING_ARTIFACT,
                "manual_review_required": True,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    item = next((w for w in scanner.get("watchlist") or []
                 if str(w.get("ticker") or "").upper() == t), None)
    if item is None:
        return {"ticker": t, "fallback": NO_DATA,
                "manual_review_required": True,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    board = build_leaderboard(store)
    row = next((r for r in board["rows"] if r["ticker"] == t), None) or {}
    fwd_entry = _latest_forward_by_ticker(store).get(t)
    fwd = _forward_block(fwd_entry)

    social_status = None
    cats = item.get("all_categories") or [item.get("category")]
    if "social_arb_attention" in cats:
        social_status = "SOCIAL_ATTENTION_SIGNAL"

    return {
        "ticker": t,
        "company": item.get("company_name"),
        "sector": item.get("sector"),
        "industry": item.get("industry"),
        "sector_etf": row.get("sector_etf"),
        "price": item.get("latest_close"),
        "price_date": item.get("latest_price_date"),
        "label": row.get("label"),
        "watchlist_label": item.get("watchlist_label"),
        "scanner_category": item.get("category"),
        "all_categories": cats,
        "source": row.get("source", "unknown"),
        "reason": item.get("why_appeared"),
        "confirms_if": item.get("confirms_if"),
        "invalidates_if": item.get("invalidates_if"),
        "confidence": item.get("data_confidence"),
        "research_score": item.get("research_score"),
        "rs_20d_vs_spy": item.get("rs_20d_vs_spy"),
        "rs_63d_vs_spy": item.get("rs_63d_vs_spy"),
        "market_cap": item.get("market_cap"),
        "extension_state": item.get("extension_state"),
        "bars_available": item.get("bars_available"),
        "data_quality": row.get("data_quality", "UNKNOWN"),
        "warnings": row.get("warnings", []),
        "forward": fwd,
        "social_status": social_status,
        "claude_review": None,  # not published by any current artifact
        "manual_review_required": True,
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 2: per-ticker chart series ─────────────────────────────────────────
#
# Read-only research visuals.  No trade markers of any kind — the payload
# carries prices, volume, moving averages, date-aligned relative-strength
# series, and the ticker's forward-vs-benchmark evidence block.

SERIES_MAX_BARS = 260  # ~1 trading year; keeps the JSON payload small


def _read_price_frame(store: ArtifactStore, symbol: str):
    """Merged deep+shallow OHLCV frame for one symbol, or None.

    pandas is imported lazily so the status/leaderboard paths (and their
    tests) never need it.
    """
    import pandas as pd  # lazy — chart path only

    def _read(path: Path):
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            if df is None or df.empty:
                return None
            if "close" not in df.columns and "Close" in df.columns:
                df = df.rename(columns={"Close": "close"})
            if "close" not in df.columns:
                return None
            df.index = pd.to_datetime(df.index)
            return df.sort_index()
        except Exception:
            return None

    sym = symbol.upper()
    shallow = _read(store.prices_dir / f"{sym}.parquet")
    deep = _read(store.prices_deep_dir / f"{sym}.parquet")
    if shallow is None and deep is None:
        return None
    if shallow is None:
        return deep
    if deep is None:
        return shallow
    try:
        import pandas as pd
        combined = pd.concat([deep, shallow])
        combined = combined[~combined.index.duplicated(keep="last")]
        return combined.sort_index()
    except Exception:
        return shallow


def _rel_strength_series(t_close, b_close) -> List[Optional[float]]:
    """Date-aligned cumulative relative return of ticker vs benchmark, %.

    rel[t] = ((P_t / P_0) / (B_t / B_0) - 1) * 100 over the ticker's dates;
    dates missing from the benchmark yield None (rendered as gaps, never
    interpolated).
    """
    b = b_close.reindex(t_close.index)
    out: List[Optional[float]] = []
    p0 = float(t_close.iloc[0])
    b0 = None
    for i in range(len(t_close)):
        bv = b.iloc[i]
        if bv is None or (bv != bv):  # NaN
            out.append(None)
            continue
        if b0 is None:
            b0 = float(bv)
            p0 = float(t_close.iloc[i])
        try:
            rel = ((float(t_close.iloc[i]) / p0) / (float(bv) / b0) - 1.0) * 100.0
            out.append(round(rel, 3))
        except Exception:
            out.append(None)
    return out


def build_ticker_series(ticker: str,
                        store: Optional[ArtifactStore] = None,
                        max_bars: int = SERIES_MAX_BARS) -> Dict[str, Any]:
    """Chart payload for one ticker: dates, close, volume, MA50/MA200,
    RS vs SPY / QQQ / sector ETF, and the forward-vs-benchmark block."""
    store = store or ArtifactStore()
    t = str(ticker or "").upper()

    df = _read_price_frame(store, t)
    if df is None:
        return {"ticker": t, "fallback": NO_DATA,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    close_full = df["close"].astype(float)
    ma50_full = close_full.rolling(50).mean()
    ma200_full = close_full.rolling(200).mean()

    df = df.tail(max_bars)
    close = close_full.tail(max_bars)
    dates = [str(d)[:10] for d in df.index]

    def _col(series) -> List[Optional[float]]:
        return [None if (v != v) else round(float(v), 4)
                for v in series.tail(max_bars).tolist()]

    volume = None
    for col in ("volume", "Volume"):
        if col in df.columns:
            volume = [None if (v != v) else float(v)
                      for v in df[col].astype(float).tolist()]
            break

    # Sector ETF from the scanner watchlist entry (display metadata only)
    sector_etf = None
    scanner = _load_json(store.scanner_json)
    if scanner is not None:
        item = next((w for w in scanner.get("watchlist") or []
                     if str(w.get("ticker") or "").upper() == t), None)
        if item:
            sector_etf = item.get("company_sector_etf")

    def _rs(bench_symbol: Optional[str]) -> Optional[List[Optional[float]]]:
        if not bench_symbol:
            return None
        bdf = _read_price_frame(store, bench_symbol)
        if bdf is None:
            return None
        return _rel_strength_series(close, bdf["close"].astype(float))

    warnings: List[str] = []
    price_age = _days_since(dates[-1] if dates else None)
    if price_age is not None and price_age > STALE_PRICE_DAYS:
        warnings.append(STALE_PRICE)

    rs_spy = _rs("SPY")
    rs_qqq = _rs("QQQ")
    rs_sector = _rs(sector_etf)
    if rs_spy is None:
        warnings.append(BENCHMARK_MISSING)

    fwd = _forward_block(_latest_forward_by_ticker(store).get(t))

    return {
        "ticker": t,
        "sector_etf": sector_etf,
        "dates": dates,
        "close": _col(close_full),
        "volume": volume,
        "ma50": _col(ma50_full),
        "ma200": _col(ma200_full),
        "rs_vs_spy": rs_spy,
        "rs_vs_qqq": rs_qqq,
        "rs_vs_sector": rs_sector,
        "forward": fwd,
        "bars": len(dates),
        "warnings": warnings,
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 3: forward-evidence cohort analytics ───────────────────────────────
#
# Two provenance tiers, kept visually separate in the UI:
#   1. `by_label` — the tracker's OWN published verdicts, passed through
#      unmodified (single source of truth; the dashboard never recomputes
#      or overrides a published verdict).
#   2. `computed` — cohorts the tracker does not publish (by scanner
#      category, by universe source, pre/post price-fix split), computed
#      here from the history JSONL with explicit caveats.
#
# Sample-maturity ladder mirrors research_watchlist_forward_tracker.py
# (constants duplicated, not imported — the dashboard stays decoupled):
#   TOO_EARLY < 10, PROVISIONAL 10-29, MEANINGFUL 30-99, ROBUST >= 100.

SAMPLE_THRESHOLD_PROVISIONAL = 10
SAMPLE_THRESHOLD_MEANINGFUL = 30
SAMPLE_THRESHOLD_ROBUST = 100

# Cohort boundary from the 2026-07-02 delta report: entries before this date
# were SELECTED by a scanner running on stale prices; pooled analytics would
# blend two different engines.  Display-only constant.
PRICE_FIX_BOUNDARY = "2026-07-02"


def _sample_status(n_matured: int) -> str:
    if n_matured < SAMPLE_THRESHOLD_PROVISIONAL:
        return "TOO_EARLY"
    if n_matured < SAMPLE_THRESHOLD_MEANINGFUL:
        return "PROVISIONAL"
    if n_matured < SAMPLE_THRESHOLD_ROBUST:
        return "MEANINGFUL"
    return "ROBUST"


def _median(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _cohort_stats(name: str, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Descriptive stats for one cohort.  10d is the primary horizon (matches
    the tracker); 5d shown alongside.  No verdicts — computed cohorts carry
    stats + maturity only, never a verdict of their own."""
    def _vals(field: str) -> List[float]:
        return [float(e[field]) for e in entries if e.get(field) is not None]

    r5, r10 = _vals("ret_5d"), _vals("ret_10d")
    vs_spy, vs_qqq, vs_sec = (_vals("ret_10d_vs_spy"), _vals("ret_10d_vs_qqq"),
                              _vals("ret_10d_vs_sector"))
    n10 = len(r10)
    return {
        "cohort": name,
        "n_entries": len(entries),
        "n_matured_5d": len(r5),
        "n_matured_10d": n10,
        "avg_ret_5d": round(sum(r5) / len(r5), 2) if r5 else None,
        "avg_ret_10d": round(sum(r10) / n10, 2) if r10 else None,
        "median_ret_10d": round(_median(r10), 2) if r10 else None,
        "win_rate_10d": round(sum(1 for v in r10 if v > 0) / n10, 3) if n10 else None,
        "mean_vs_spy_10d": round(sum(vs_spy) / len(vs_spy), 2) if vs_spy else None,
        "mean_vs_qqq_10d": round(sum(vs_qqq) / len(vs_qqq), 2) if vs_qqq else None,
        "mean_vs_sector_10d": round(sum(vs_sec) / len(vs_sec), 2) if vs_sec else None,
        "n_with_spy_10d": len(vs_spy),
        "sample_status": _sample_status(n10),
    }


def _all_history_entries(store: ArtifactStore) -> List[Dict[str, Any]]:
    path = store.history_jsonl
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return out


def build_forward_cohorts(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Cohort analytics payload for the Forward Evidence page."""
    store = store or ArtifactStore()
    forward = _load_json(store.forward_json)
    entries = _all_history_entries(store)
    if forward is None and not entries:
        return {"fallback": MISSING_ARTIFACT,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    # Tier 1 — tracker-published label verdicts, passed through unmodified.
    by_label = (forward or {}).get("verdicts_by_label") or []

    # Tier 2 — computed cohorts (stats only, no verdicts).
    def _group(keyfn) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for e in entries:
            k = keyfn(e)
            if k:
                groups.setdefault(str(k), []).append(e)
        return groups

    by_category = [
        _cohort_stats(k, v) for k, v in sorted(
            _group(lambda e: e.get("category")).items())
    ]

    # Source attribution caveat: history entries do not record the universe
    # source at appearance time, so we join on the CURRENT universe map.
    src_map = _source_by_ticker(_load_json(store.universe_json))
    by_source = [
        _cohort_stats(k, v) for k, v in sorted(
            _group(lambda e: src_map.get(str(e.get("ticker") or "").upper())).items())
    ]

    # Pre/post price-fix split (delta-report doctrine: never pool the eras).
    def _era(e) -> Optional[str]:
        d = str(e.get("appearance_date") or "")[:10]
        if not d:
            return None
        return "post_fix" if d >= PRICE_FIX_BOUNDARY else "pre_fix"
    by_era = [_cohort_stats(k, v) for k, v in sorted(_group(_era).items())]

    return {
        "generated_at": (forward or {}).get("generated_at"),
        "overall": (forward or {}).get("overall") or {},
        "by_label": by_label,
        "by_category": by_category,
        "by_source": by_source,
        "by_era": by_era,
        "price_fix_boundary": PRICE_FIX_BOUNDARY,
        "caveats": [
            "by_label rows are the tracker's published verdicts (unmodified).",
            "by_category / by_source / by_era are descriptive stats computed "
            "from the history ledger — no verdicts are assigned to them.",
            "by_source joins on the CURRENT universe source map; "
            "appearance-time source is not recorded in the ledger.",
            f"Entries before {PRICE_FIX_BOUNDARY} were selected on stale "
            "price data (see delta report) — do not pool the eras.",
        ],
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 3: sector compass ──────────────────────────────────────────────────

SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLU",
               "XLB", "XLRE", "XLC"]
SECTOR_NAMES = {
    "XLK": "Technology", "XLF": "Financials", "XLV": "Health Care",
    "XLE": "Energy", "XLI": "Industrials", "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples", "XLU": "Utilities", "XLB": "Materials",
    "XLRE": "Real Estate", "XLC": "Communication Services",
}


def build_sector_compass(store: Optional[ArtifactStore] = None,
                         spark_bars: int = 64) -> Dict[str, Any]:
    """Sector rotation view: date-aligned RS vs SPY for each sector ETF
    (20d / 63d cumulative relative return + a short spark series)."""
    store = store or ArtifactStore()
    spy = _read_price_frame(store, "SPY")
    if spy is None:
        return {"fallback": NO_DATA, "sectors": [],
                "research_only_footer": RESEARCH_ONLY_FOOTER}
    spy_close = spy["close"].astype(float)

    sectors: List[Dict[str, Any]] = []
    for etf in SECTOR_ETFS:
        df = _read_price_frame(store, etf)
        if df is None:
            sectors.append({"etf": etf, "name": SECTOR_NAMES.get(etf),
                            "fallback": NO_DATA})
            continue
        close = df["close"].astype(float)
        rel_full = _rel_strength_series(close.tail(spark_bars), spy_close)

        def _rs(lookback: int) -> Optional[float]:
            c = close.tail(lookback + 1)
            if len(c) < lookback + 1:
                return None
            rel = _rel_strength_series(c, spy_close)
            return rel[-1] if rel and rel[-1] is not None else None

        sectors.append({
            "etf": etf,
            "name": SECTOR_NAMES.get(etf),
            "rs_20d_vs_spy": _rs(20),
            "rs_63d_vs_spy": _rs(63),
            "spark_rs_vs_spy": rel_full,
            "last_date": str(df.index.max())[:10],
            "fallback": None,
        })

    ranked = [s for s in sectors if s.get("rs_20d_vs_spy") is not None]
    ranked.sort(key=lambda s: -(s["rs_20d_vs_spy"] or 0))
    return {
        "sectors": sectors,
        "leaders": [s["etf"] for s in ranked[:4]],
        "laggards": [s["etf"] for s in ranked[-4:]] if len(ranked) >= 4 else [],
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }


# ── Phase 3: social attention overview ───────────────────────────────────────


def build_social_overview(store: Optional[ArtifactStore] = None) -> Dict[str, Any]:
    """Social attention cohort view: the social forward validator's published
    payload (verdict passed through unmodified) + the scanner's current
    social lane candidates."""
    store = store or ArtifactStore()
    social_path = store.research_dir / "social_attention_forward_latest.json"
    social = _load_json(social_path)
    scanner = _load_json(store.scanner_json)

    lane: List[Dict[str, Any]] = []
    for item in ((scanner or {}).get("categories") or {}).get(
            "social_arb_attention", []):
        if item.get("social_data_available") is False:
            continue
        lane.append({
            "ticker": item.get("ticker"),
            "research_score": item.get("research_score"),
            "watchlist_label": item.get("watchlist_label"),
        })

    if social is None and not lane:
        return {"fallback": MISSING_ARTIFACT,
                "research_only_footer": RESEARCH_ONLY_FOOTER}

    return {
        "generated_at": (social or {}).get("generated_at"),
        "verdict": (social or {}).get("verdict"),
        "verdict_reason": (social or {}).get("verdict_reason"),
        "matured_social_led": (social or {}).get("matured_social_led_primary"),
        "by_cohort": (social or {}).get("by_cohort") or {},
        "comparisons": (social or {}).get("comparisons") or {},
        "history_days": (social or {}).get("history_days"),
        "current_lane": lane,
        "fallback": None,
        "research_only_footer": RESEARCH_ONLY_FOOTER,
    }
