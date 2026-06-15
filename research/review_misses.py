"""
research/review_misses.py — Phase 8B weekly mistake / false-confidence review.

Reads the existing forward-tracking ledgers and forward-summary artifacts
that the resolver has already populated, plus the Phase 8A research
journal, and writes a single weekly review report:

  cache/research/weekly_review_latest.json
  logs/weekly_review_latest.txt

It is *cache-only*: no provider calls, no resolver pass, no scoring
changes.  All sections degrade gracefully — sections with no data say
"missing" instead of fabricating.

Sections:
  1. high_confidence_misses    — high-conf forecasts/lens calls that were wrong
  2. best_calls                — high-conf calls that worked (anti-survivorship)
  3. worst_calls               — biggest adverse moves regardless of confidence
  4. stock_lens_misses         — bullish-fell / bearish-rose / extended-played-out
                                 / Entry Validator state vs forward return
  5. forecast_misses           — regime calls that failed; sector leaders that
                                 underperformed laggards; risk-off warnings
                                 that did not materialize
  6. follow_up                 — open snapshots near maturity; journal notes due

Usage:
  python research/review_misses.py [--lookback-days N] [--out-json PATH]
                                    [--out-text PATH] [--print-text]

Guardrails (Phase 8B):
  - research-only; no trade approval, no paper evidence mutation
  - reads ledger + summary + journal; never invokes a resolver or provider
  - if a ledger/summary is missing, the section reports "missing" and
    the run still produces an artifact so the dashboard strip can render
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Cache-only module — paths are resolved directly from _ROOT so we never
# depend on core.config (which requires provider creds at import time).
# Mirrors the same pattern used by core.forecast_forward_tracker.

logger = logging.getLogger("review_misses")


STATE_DIR = _ROOT / "data" / "state"
FORECAST_LOG_PATH   = STATE_DIR / "regime_forecast_forward_log.jsonl"
STOCK_LENS_LOG_PATH = STATE_DIR / "stock_lens_forward_log.jsonl"

CACHE_RESEARCH_DIR = _ROOT / "cache" / "research"
FORECAST_SUMMARY_PATH = CACHE_RESEARCH_DIR / "forecast_forward_summary_latest.json"
LENS_SUMMARY_PATH     = CACHE_RESEARCH_DIR / "stock_lens_forward_summary_latest.json"

WEEKLY_REVIEW_JSON = CACHE_RESEARCH_DIR / "weekly_review_latest.json"
WEEKLY_REVIEW_TEXT = _ROOT / "logs" / "weekly_review_latest.txt"


# ── helpers ─────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.warning("failed reading %s: %s", path, exc)
    return out


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("failed reading %s: %s", path, exc)
        return None


def _parse_iso_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _is_high_conf(conf: Any) -> bool:
    return str(conf or "").strip().lower() == "high"


# ── direction helpers (mirror forward_tracker semantics, do not mutate it) ──


def _bias_direction(label: Any) -> Optional[int]:
    """+1 / -1 / 0 / None — research-only mirror of the ledger's hit logic."""
    s = str(label or "").lower()
    if not s:
        return None
    if any(k in s for k in ("bull", "construct")):
        return +1
    if any(k in s for k in ("bear", "defens", "risk-off")):
        return -1
    if any(k in s for k in ("neutral", "chop")):
        return 0
    return None


def _hit_for_lens(row: Dict[str, Any], horizon: int) -> Optional[bool]:
    """Prefer the resolver-stamped hit_<H>d; fall back to label vs return sign."""
    outcomes = row.get("outcomes") or {}
    h = outcomes.get(f"hit_{horizon}d")
    if isinstance(h, bool):
        return h
    ret = outcomes.get(f"return_{horizon}d_pct")
    direction = _bias_direction(row.get("label"))
    if ret is None or direction is None:
        return None
    if direction == 0:
        return abs(float(ret)) <= 1.0
    return (float(ret) > 0) if direction > 0 else (float(ret) < 0)


def _hit_for_forecast(row: Dict[str, Any], horizon: int) -> Optional[bool]:
    outcomes = row.get("outcomes") or {}
    h = outcomes.get(f"spy_{horizon}d_hit")
    if isinstance(h, bool):
        return h
    ret = outcomes.get(f"spy_{horizon}d_return_pct")
    direction = _bias_direction(row.get(f"bias_{horizon}d"))
    if ret is None or direction is None:
        return None
    if direction == 0:
        return abs(float(ret)) <= 1.0
    return (float(ret) > 0) if direction > 0 else (float(ret) < 0)


# ── filtering ───────────────────────────────────────────────────────────────


def _within_lookback(row: Dict[str, Any], lookback_days: int, today: date) -> bool:
    if lookback_days <= 0:
        return True
    anchor = _parse_iso_date(row.get("anchor_date"))
    if anchor is None:
        return True   # keep — better to include than to drop on a missing date
    return (today - anchor).days <= lookback_days


# ── section builders: stock lens ────────────────────────────────────────────


def _lens_summary_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = row.get("outcomes") or {}
    layers = row.get("layers") or {}
    return {
        "snapshot_id": row.get("snapshot_id"),
        "ticker": row.get("ticker"),
        "anchor_date": row.get("anchor_date"),
        "label": row.get("label"),
        "confidence": row.get("confidence"),
        "horizon_view_5d": row.get("horizon_view_5d"),
        "horizon_view_10d": row.get("horizon_view_10d"),
        "scores": row.get("scores"),
        "hard_caps_fired": row.get("hard_caps_fired") or [],
        "entry_state": (layers.get("entry") or {}).get("view"),
        "tech_extended": (layers.get("tech") or {}).get("extended"),
        "outcomes": {
            "return_5d_pct": out.get("return_5d_pct"),
            "return_10d_pct": out.get("return_10d_pct"),
            "return_20d_pct": out.get("return_20d_pct"),
            "rel_spy_5d_pct": out.get("rel_spy_5d_pct"),
            "rel_spy_10d_pct": out.get("rel_spy_10d_pct"),
            "max_drawdown_5d_pct": out.get("max_drawdown_5d_pct"),
            "max_favorable_5d_pct": out.get("max_favorable_5d_pct"),
            "hit_5d": out.get("hit_5d"),
            "hit_10d": out.get("hit_10d"),
            "hit_20d": out.get("hit_20d"),
        },
        "status": row.get("status"),
    }


def _forecast_summary_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = row.get("outcomes") or {}
    return {
        "snapshot_id": row.get("snapshot_id"),
        "anchor_date": row.get("anchor_date"),
        "current_regime": row.get("current_regime"),
        "bias_5d": row.get("bias_5d"),
        "bias_10d": row.get("bias_10d"),
        "confidence": row.get("confidence"),
        "predicted_top_basket": row.get("predicted_top_basket") or [],
        "predicted_bottom_basket": row.get("predicted_bottom_basket") or [],
        "outcomes": {
            "spy_5d_return_pct": out.get("spy_5d_return_pct"),
            "spy_10d_return_pct": out.get("spy_10d_return_pct"),
            "spy_5d_hit": out.get("spy_5d_hit"),
            "spy_10d_hit": out.get("spy_10d_hit"),
            "top_minus_bottom_5d_pct": out.get("top_minus_bottom_5d_pct"),
            "top_minus_bottom_10d_pct": out.get("top_minus_bottom_10d_pct"),
            "leaders_beat_laggards_5d": out.get("leaders_beat_laggards_5d"),
            "leaders_beat_laggards_10d": out.get("leaders_beat_laggards_10d"),
        },
        "status": row.get("status"),
    }


# ── core build ──────────────────────────────────────────────────────────────


def build_review(
    *,
    lookback_days: int = 30,
    today: Optional[date] = None,
    forecast_log_path: Path = FORECAST_LOG_PATH,
    lens_log_path: Path = STOCK_LENS_LOG_PATH,
    forecast_summary_path: Path = FORECAST_SUMMARY_PATH,
    lens_summary_path: Path = LENS_SUMMARY_PATH,
) -> Dict[str, Any]:
    today = today or date.today()

    forecast_rows = _load_jsonl(forecast_log_path)
    lens_rows     = _load_jsonl(lens_log_path)
    forecast_summary = _load_json(forecast_summary_path)
    lens_summary     = _load_json(lens_summary_path)

    in_window_forecasts = [r for r in forecast_rows if _within_lookback(r, lookback_days, today)]
    in_window_lens      = [r for r in lens_rows     if _within_lookback(r, lookback_days, today)]

    matured_lens     = [r for r in in_window_lens     if r.get("status") == "matured"]
    matured_forecast = [r for r in in_window_forecasts if r.get("status") == "matured"]

    # ── 1. high-confidence misses ────────────────────────────────────────────
    high_conf_misses: List[Dict[str, Any]] = []
    for r in matured_lens:
        if not _is_high_conf(r.get("confidence")):
            continue
        for h in (5, 10, 20):
            hit = _hit_for_lens(r, h)
            if hit is False:
                row = _lens_summary_row(r)
                row["kind"] = "lens"
                row["miss_horizon"] = h
                row["why"] = (f"label '{r.get('label')}' (conf high) but {h}d return "
                              f"{(r.get('outcomes') or {}).get(f'return_{h}d_pct')}%")
                high_conf_misses.append(row)
                break  # one row per snapshot
    for r in matured_forecast:
        if not _is_high_conf(r.get("confidence")):
            continue
        for h in (5, 10):
            hit = _hit_for_forecast(r, h)
            if hit is False:
                row = _forecast_summary_row(r)
                row["kind"] = "forecast"
                row["miss_horizon"] = h
                row["why"] = (f"bias_{h}d '{r.get(f'bias_{h}d')}' (conf high) but SPY "
                              f"{h}d return {(r.get('outcomes') or {}).get(f'spy_{h}d_return_pct')}%")
                high_conf_misses.append(row)
                break

    # ── 2. best calls (high-conf, hit at any horizon) ────────────────────────
    best_calls: List[Dict[str, Any]] = []
    for r in matured_lens:
        if not _is_high_conf(r.get("confidence")):
            continue
        if any(_hit_for_lens(r, h) is True for h in (5, 10, 20)):
            row = _lens_summary_row(r); row["kind"] = "lens"
            best_calls.append(row)
    for r in matured_forecast:
        if not _is_high_conf(r.get("confidence")):
            continue
        if any(_hit_for_forecast(r, h) is True for h in (5, 10)):
            row = _forecast_summary_row(r); row["kind"] = "forecast"
            best_calls.append(row)

    # ── 3. worst calls — biggest adverse forward move (lens) ─────────────────
    def _worst_metric(r: Dict[str, Any]) -> float:
        out = r.get("outcomes") or {}
        direction = _bias_direction(r.get("label"))
        # adverse move: drawdown for bullish; max favorable rise for bearish
        if direction == +1:
            dd = out.get("max_drawdown_5d_pct")
            return float(dd) if dd is not None else 0.0
        if direction == -1:
            mx = out.get("max_favorable_5d_pct")
            return -float(mx) if mx is not None else 0.0
        return 0.0

    worst_calls: List[Dict[str, Any]] = []
    if matured_lens:
        ranked = sorted(matured_lens, key=_worst_metric)[:5]
        for r in ranked:
            if _worst_metric(r) >= 0.0:
                continue   # nothing genuinely adverse
            row = _lens_summary_row(r); row["kind"] = "lens"
            row["adverse_metric_pct"] = round(_worst_metric(r), 3)
            worst_calls.append(row)

    # ── 4. stock-lens-specific misses ────────────────────────────────────────
    bullish_fell: List[Dict[str, Any]] = []
    bearish_rose: List[Dict[str, Any]] = []
    bullish_extended_outcomes: List[Dict[str, Any]] = []
    entry_state_vs_perf: Dict[str, Dict[str, Any]] = {}

    for r in matured_lens:
        d = _bias_direction(r.get("label"))
        out = r.get("outcomes") or {}
        ret5 = out.get("return_5d_pct")
        if d == +1 and ret5 is not None and float(ret5) < 0:
            row = _lens_summary_row(r); row["kind"] = "lens"; bullish_fell.append(row)
        if d == -1 and ret5 is not None and float(ret5) > 0:
            row = _lens_summary_row(r); row["kind"] = "lens"; bearish_rose.append(row)
        # "Bullish but extended" — heuristic: bullish label AND tech.extended=True
        layers = r.get("layers") or {}
        if d == +1 and (layers.get("tech") or {}).get("extended") is True:
            row = _lens_summary_row(r); row["kind"] = "lens"
            row["extended_at_call"] = True
            bullish_extended_outcomes.append(row)
        # entry-state vs perf bucketing
        es = (layers.get("entry") or {}).get("view") or "—"
        bucket = entry_state_vs_perf.setdefault(es, {"n": 0, "ret_5d_sum": 0.0,
                                                       "ret_5d_n": 0, "hit_5d_n": 0,
                                                       "hit_5d_total": 0})
        bucket["n"] += 1
        if ret5 is not None:
            bucket["ret_5d_sum"] += float(ret5)
            bucket["ret_5d_n"] += 1
        h5 = _hit_for_lens(r, 5)
        if h5 is not None:
            bucket["hit_5d_total"] += 1
            if h5:
                bucket["hit_5d_n"] += 1
    for es, b in entry_state_vs_perf.items():
        b["avg_return_5d_pct"] = (round(b["ret_5d_sum"] / b["ret_5d_n"], 3)
                                   if b["ret_5d_n"] else None)
        b["hit_rate_5d"] = (round(b["hit_5d_n"] / b["hit_5d_total"], 3)
                             if b["hit_5d_total"] else None)
        # drop work fields
        for k in ("ret_5d_sum", "ret_5d_n", "hit_5d_n", "hit_5d_total"):
            b.pop(k, None)

    # ── 5. forecast-specific misses ──────────────────────────────────────────
    regime_calls_failed: List[Dict[str, Any]] = []
    sector_basket_failed: List[Dict[str, Any]] = []
    risk_off_failed: List[Dict[str, Any]] = []
    for r in matured_forecast:
        out = r.get("outcomes") or {}
        for h in (5, 10):
            hit = _hit_for_forecast(r, h)
            if hit is False:
                row = _forecast_summary_row(r); row["kind"] = "forecast"; row["miss_horizon"] = h
                regime_calls_failed.append(row)
                break
        for h in (5, 10):
            sb = out.get(f"leaders_beat_laggards_{h}d")
            if sb is False:
                row = _forecast_summary_row(r); row["kind"] = "forecast"; row["sector_horizon"] = h
                row["spread_pct"] = out.get(f"top_minus_bottom_{h}d_pct")
                sector_basket_failed.append(row)
                break
        d5 = _bias_direction(r.get("bias_5d"))
        if d5 == -1:
            spy5 = out.get("spy_5d_return_pct")
            if spy5 is not None and float(spy5) > 1.0:
                row = _forecast_summary_row(r); row["kind"] = "forecast"
                row["risk_off_failure"] = f"warned bearish but SPY +{float(spy5):.2f}%"
                risk_off_failed.append(row)

    # ── 6. follow-up ─────────────────────────────────────────────────────────
    open_lens     = [_lens_summary_row(r)     for r in in_window_lens     if r.get("status") != "matured"]
    open_forecast = [_forecast_summary_row(r) for r in in_window_forecasts if r.get("status") != "matured"]

    # journal notes due
    notes_due: List[Dict[str, Any]] = []
    try:
        from research import research_journal as rj
        notes_due_raw = rj.due_notes(as_of=today)
        for n in notes_due_raw:
            notes_due.append({
                "note_id": n.get("note_id"),
                "ticker": n.get("ticker"),
                "status": n.get("status"),
                "review_date": n.get("review_date"),
                "conclusion": n.get("conclusion"),
                "next_action": n.get("next_action"),
            })
    except Exception as exc:
        logger.info("journal load failed: %s", exc)

    artifact: Dict[str, Any] = {
        "kind": "weekly_review",
        "version": "WEEKLY_REVIEW_V1",
        "generated_at": _now_iso(),
        "as_of": today.isoformat(),
        "lookback_days": lookback_days,
        "data_sources": {
            "forecast_log": str(forecast_log_path),
            "stock_lens_log": str(lens_log_path),
            "forecast_summary": str(forecast_summary_path) if forecast_summary else "missing",
            "stock_lens_summary": str(lens_summary_path) if lens_summary else "missing",
            "research_notes": str(_ROOT / "data" / "state" / "research_notes.jsonl"),
        },
        "counts": {
            "forecast_total": len(forecast_rows),
            "forecast_in_window": len(in_window_forecasts),
            "forecast_matured": len(matured_forecast),
            "lens_total": len(lens_rows),
            "lens_in_window": len(in_window_lens),
            "lens_matured": len(matured_lens),
            "high_conf_misses": len(high_conf_misses),
            "best_calls": len(best_calls),
            "worst_calls": len(worst_calls),
            "notes_due": len(notes_due),
        },
        "high_confidence_misses": high_conf_misses,
        "best_calls": best_calls,
        "worst_calls": worst_calls,
        "stock_lens_misses": {
            "bullish_that_fell": bullish_fell,
            "bearish_that_rose": bearish_rose,
            "bullish_extended_outcomes": bullish_extended_outcomes,
            "entry_state_vs_performance": entry_state_vs_perf,
        },
        "forecast_misses": {
            "regime_calls_failed": regime_calls_failed,
            "sector_basket_failed": sector_basket_failed,
            "risk_off_warnings_failed": risk_off_failed,
        },
        "follow_up": {
            "open_lens_snapshots": open_lens,
            "open_forecast_snapshots": open_forecast,
            "notes_due": notes_due,
        },
        "guardrails": [
            "research-only · not trade approval · not paper evidence",
            "cache-only inputs · no provider calls · no resolver pass",
            "scoring logic for forecasts/lens unchanged",
        ],
    }

    # If everything matured is empty, still emit an artifact with a clear note.
    if (
        artifact["counts"]["lens_matured"] == 0
        and artifact["counts"]["forecast_matured"] == 0
    ):
        artifact["notes"] = [
            "no matured snapshots in window — run resolve to populate outcomes,"
            " or wait for forward horizons to complete"
        ]

    return artifact


# ── rendering ───────────────────────────────────────────────────────────────


def _row_lens_line(r: Dict[str, Any]) -> str:
    o = r.get("outcomes") or {}
    return (f"  {r.get('ticker','?'):<6} {str(r.get('label','—'))[:18]:<18} "
            f"conf {str(r.get('confidence','—'))[:6]:<6} "
            f"5d={_fmt_pct(o.get('return_5d_pct'))} "
            f"10d={_fmt_pct(o.get('return_10d_pct'))} "
            f"rel5d={_fmt_pct(o.get('rel_spy_5d_pct'))} "
            f"id={r.get('snapshot_id','?')[-10:]}")


def _row_fc_line(r: Dict[str, Any]) -> str:
    o = r.get("outcomes") or {}
    return (f"  {r.get('anchor_date','?')} {str(r.get('current_regime','—'))[:22]:<22} "
            f"5d {str(r.get('bias_5d','—'))[:8]:<8} 10d {str(r.get('bias_10d','—'))[:8]:<8} "
            f"conf {str(r.get('confidence','—'))[:6]:<6} "
            f"SPY5d={_fmt_pct(o.get('spy_5d_return_pct'))} "
            f"SPY10d={_fmt_pct(o.get('spy_10d_return_pct'))}")


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "    —"
    try:
        return f"{float(v):+5.2f}%"
    except Exception:
        return "    —"


def render_text(art: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("=" * 78)
    L.append(f"WEEKLY RESEARCH REVIEW — as of {art.get('as_of','—')}  "
             f"(lookback={art.get('lookback_days')}d)")
    L.append("=" * 78)
    L.append(f"generated_at: {art.get('generated_at','—')}")
    counts = art.get("counts") or {}
    L.append("")
    L.append("COUNTS")
    L.append("-" * 78)
    for k in ("forecast_total","forecast_in_window","forecast_matured",
              "lens_total","lens_in_window","lens_matured",
              "high_conf_misses","best_calls","worst_calls","notes_due"):
        L.append(f"  {k:<22s} {counts.get(k, 0)}")
    if art.get("notes"):
        L.append("")
        for n in art["notes"]:
            L.append(f"  · {n}")

    def _section(title: str, rows: List[Dict[str, Any]], renderer):
        L.append("")
        L.append(title)
        L.append("-" * 78)
        if not rows:
            L.append("  (none)")
            return
        for r in rows[:25]:
            L.append(renderer(r))
        if len(rows) > 25:
            L.append(f"  … {len(rows)-25} more")

    _section("1. HIGH-CONFIDENCE MISSES",
             art.get("high_confidence_misses") or [],
             lambda r: _row_lens_line(r) if r.get("kind") == "lens" else _row_fc_line(r))
    _section("2. BEST CALLS (high-conf, hit at any horizon)",
             art.get("best_calls") or [],
             lambda r: _row_lens_line(r) if r.get("kind") == "lens" else _row_fc_line(r))
    _section("3. WORST CALLS (largest adverse 5d move)",
             art.get("worst_calls") or [],
             lambda r: f"  {_row_lens_line(r)}  adverse={_fmt_pct(r.get('adverse_metric_pct'))}")

    sl = art.get("stock_lens_misses") or {}
    _section("4a. STOCK LENS — BULLISH THAT FELL (5d)",
             sl.get("bullish_that_fell") or [], _row_lens_line)
    _section("4b. STOCK LENS — BEARISH THAT ROSE (5d)",
             sl.get("bearish_that_rose") or [], _row_lens_line)
    _section("4c. STOCK LENS — BULLISH-EXTENDED OUTCOMES",
             sl.get("bullish_extended_outcomes") or [], _row_lens_line)

    L.append("")
    L.append("4d. ENTRY-STATE vs FORWARD PERFORMANCE")
    L.append("-" * 78)
    es_map = sl.get("entry_state_vs_performance") or {}
    if not es_map:
        L.append("  (none)")
    else:
        L.append(f"  {'state':<22s} {'n':>4s}  {'avg 5d':>9s}  {'hit 5d':>9s}")
        for es, b in sorted(es_map.items(), key=lambda kv: kv[0]):
            avg = b.get("avg_return_5d_pct")
            hr = b.get("hit_rate_5d")
            avg_s = f"{avg:+6.2f}%" if avg is not None else "    —"
            hr_s  = f"{hr*100:5.1f}%" if hr is not None else "    —"
            L.append(f"  {es[:22]:<22s} {b.get('n',0):>4d}  {avg_s:>9s}  {hr_s:>9s}")

    fm = art.get("forecast_misses") or {}
    _section("5a. FORECAST — REGIME CALLS THAT FAILED",
             fm.get("regime_calls_failed") or [], _row_fc_line)
    _section("5b. FORECAST — SECTOR LEADERS UNDERPERFORMED LAGGARDS",
             fm.get("sector_basket_failed") or [],
             lambda r: f"{_row_fc_line(r)}  spread={_fmt_pct(r.get('spread_pct'))}")
    _section("5c. FORECAST — RISK-OFF WARNINGS THAT DID NOT MATERIALIZE",
             fm.get("risk_off_warnings_failed") or [],
             lambda r: f"{_row_fc_line(r)}  note={r.get('risk_off_failure','')}")

    fu = art.get("follow_up") or {}
    L.append("")
    L.append("6. FOLLOW-UP")
    L.append("-" * 78)
    open_lens = fu.get("open_lens_snapshots") or []
    open_fc   = fu.get("open_forecast_snapshots") or []
    notes_due = fu.get("notes_due") or []
    L.append(f"  open lens snapshots     : {len(open_lens)}")
    for r in open_lens[:10]:
        L.append(_row_lens_line(r))
    if len(open_lens) > 10:
        L.append(f"  … {len(open_lens)-10} more")
    L.append(f"  open forecast snapshots : {len(open_fc)}")
    for r in open_fc[:10]:
        L.append(_row_fc_line(r))
    if len(open_fc) > 10:
        L.append(f"  … {len(open_fc)-10} more")
    L.append(f"  journal notes due       : {len(notes_due)}")
    for n in notes_due[:25]:
        L.append(f"  - [{n.get('review_date','—')}] {n.get('ticker','?'):<6} "
                 f"{str(n.get('status','—'))[:8]:<8} "
                 f"{(n.get('conclusion') or '—')[:80]}")

    L.append("")
    L.append("GUARDRAILS")
    L.append("-" * 78)
    for g in art.get("guardrails") or []:
        L.append(f"  · {g}")
    L.append("")
    return "\n".join(L) + "\n"


# ── runner ──────────────────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    art = build_review(
        lookback_days=int(args.lookback_days),
    )
    out_json = Path(args.out_json) if args.out_json else WEEKLY_REVIEW_JSON
    out_text = Path(args.out_text) if args.out_text else WEEKLY_REVIEW_TEXT
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_text.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(art, indent=2, default=str), encoding="utf-8")
    text = render_text(art)
    out_text.write_text(text, encoding="utf-8")
    counts = art.get("counts") or {}
    print(
        f"weekly_review: matured(lens={counts.get('lens_matured',0)} "
        f"fc={counts.get('forecast_matured',0)})  "
        f"high_conf_misses={counts.get('high_conf_misses',0)}  "
        f"notes_due={counts.get('notes_due',0)}  "
        f"→ {out_json}"
    )
    if getattr(args, "print_text", False):
        print(text)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Weekly research mistake / false-confidence review (Phase 8B).")
    p.add_argument("--lookback-days", default=30, type=int,
                   help="Restrict the review to snapshots within this many days. "
                        "Use 0 for all history.")
    p.add_argument("--out-json", default=None)
    p.add_argument("--out-text", default=None)
    p.add_argument("--print-text", action="store_true")
    args = p.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
