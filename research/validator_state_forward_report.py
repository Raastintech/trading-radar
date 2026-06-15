#!/usr/bin/env python3
"""
research/validator_state_forward_report.py — diagnostic forward-outcome
tracker for Alpha board + Stock Lens validator states.

Reads (cache-only):
  - data/state/stock_lens_forward_log.jsonl (Phase 5 ledger; one row per
    lens build) — primary source of historical validator states.
  - cache/prices/<TICKER>.parquet — daily bars for forward-return windows.
  - cache/research/executive_gatekeeper_<TICKER>_latest.json — optional;
    per-ticker Gatekeeper state for the latest period.

Measures forward 1d / 3d / 5d / 10d windows from each snapshot's anchor
date and aggregates by validator state, options quality, and alpha bucket:
  - mean / median return
  - max favorable excursion (MFE)
  - max adverse excursion (MAE)
  - became_actionable_later (any subsequent snapshot for the same ticker
    flips entry.view to a buyable/actionable label)
  - blocked_by_gatekeeper (current Gatekeeper artifact says BLOCK / WATCH /
    AVOID / REJECT)
  - options_quality_delta (oldest → newest options quality for the ticker;
    coarse improved / worsened / same / n/a bucket)

Writes:
  - cache/research/validator_state_forward_latest.json
  - logs/validator_state_forward_latest.txt

Doctrine guardrails:
  - Diagnostic only. Does NOT change scoring weights, governance, gating,
    or execution.
  - Cache-only (no providers; never mutates DB / paper evidence).
  - Read-only of the lens log (no rewrite, no resolver pass).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Honour the same env-load convention as other research scripts.
_CRED = os.environ.get("SNIPER_ENV_PATH")
if _CRED and Path(_CRED).exists():
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(_CRED, override=False)
    except Exception:
        pass

import core.config as cfg
from core.forecast_forward_tracker import (
    PRICE_DIRS_DEFAULT,
    STOCK_LENS_LOG_PATH,
    _anchor_close,
    _forward_return_pct,
    _forward_min_low_pct,
    _forward_max_high_pct,
    _load_price_frame,
    load_stock_lens_log,
)

logger = logging.getLogger("validator_state_forward_report")

RESEARCH_DIR = cfg.CACHE_DIR / "research"
SUMMARY_JSON = RESEARCH_DIR / "validator_state_forward_latest.json"
SUMMARY_TXT = cfg.LOG_DIR / "validator_state_forward_latest.txt"

# Forward windows we track; 5d/10d already match the Phase 5 lens resolver.
FORWARD_HORIZONS_DAYS: Tuple[int, ...] = (1, 3, 5, 10)

# Labels considered "later became actionable" when a subsequent snapshot
# carries any of them in layers.entry.view.
_ACTIONABLE_LABELS = {"Buyable Now", "Buyable Pullback"}

# Gatekeeper statuses that count as "blocked".
_BLOCKED_GATEKEEPER = {"BLOCK", "WATCH", "AVOID", "REJECT"}


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    s = str(value)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s[: len(fmt) + 6 if "%f" in fmt else len(fmt)], fmt).date()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _safe_str(value: Any) -> str:
    return str(value).strip() if value else ""


def _read_gatekeeper(ticker: str) -> Optional[Dict[str, Any]]:
    p = RESEARCH_DIR / f"executive_gatekeeper_{ticker.upper()}_latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _classify_options_delta(oldest: Optional[str], newest: Optional[str]) -> str:
    """Coarse classification of options-quality movement for a ticker."""
    if not oldest and not newest:
        return "n/a"
    if not oldest or not newest:
        return "n/a"
    if oldest == newest:
        return "same"
    # Rank by quality: positive bullish-with-confirmation > neutral > bearish/chase
    rank = {
        "BROAD_CONFIRMATION": 4,
        "BACK_MONTH_CONFIRMATION": 3,
        "BULLISH_BUT_LATE": 2,
        "OPTIONS_NO_EDGE": 1,
        "OPTIONS_MISSING": 0,
        "BEARISH_CALL_CHASE": -1,
        "SPECULATIVE_CALL_CHASE": -1,
        "BEARISH_HEDGE": -2,
    }
    o = rank.get(str(oldest).upper(), 0)
    n = rank.get(str(newest).upper(), 0)
    if n > o:
        return "improved"
    if n < o:
        return "worsened"
    return "same"


def _summary_stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None}
    vs = sorted(values)
    def _q(p: float) -> float:
        if len(vs) == 1:
            return vs[0]
        k = (len(vs) - 1) * p
        f = int(k)
        c = min(f + 1, len(vs) - 1)
        if f == c:
            return vs[f]
        return vs[f] + (vs[c] - vs[f]) * (k - f)
    return {
        "n": len(vs),
        "mean": round(statistics.fmean(vs), 4),
        "median": round(statistics.median(vs), 4),
        "p25": round(_q(0.25), 4),
        "p75": round(_q(0.75), 4),
    }


def _hit_rate(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(1 for v in values if v > 0.0) / len(values), 4)


def _extract_states(row: Dict[str, Any]) -> Dict[str, Optional[str]]:
    layers = row.get("layers") or {}
    entry = layers.get("entry") or layers.get("entry_validator") or {}
    options = layers.get("options") or {}
    alpha = layers.get("alpha") or {}
    return {
        "validator_state": _safe_str(entry.get("view") or entry.get("state")) or None,
        "options_state": _safe_str(
            options.get("options_quality")
            or options.get("quality")
            or options.get("view")
        ) or None,
        "alpha_state": _safe_str(alpha.get("view")) or None,
    }


def compute_forward_records(
    *,
    log_path: Path = STOCK_LENS_LOG_PATH,
    price_dirs: Iterable[Path] = PRICE_DIRS_DEFAULT,
    today: Optional[date] = None,
    read_gatekeeper=_read_gatekeeper,
    rows: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Walk the lens log, computing forward windows + later-state lookups.

    Returns one record per (ticker, anchor_date) snapshot.
    """
    today = today or date.today()
    rows = rows if rows is not None else load_stock_lens_log(log_path=log_path)
    rows = [r for r in rows if (r.get("kind") == "stock_lens" or "ticker" in r)]
    if not rows:
        return []

    # Order by anchor for "became actionable later" lookups.
    rows.sort(key=lambda r: (str(r.get("ticker") or ""), str(r.get("anchor_date") or r.get("logged_at") or "")))

    # Pre-cache the per-ticker price frame so we don't repeatedly hit disk.
    frame_cache: Dict[str, Any] = {}

    def _frame(sym: str):
        if sym not in frame_cache:
            frame_cache[sym] = _load_price_frame(sym, price_dirs=price_dirs)
        return frame_cache[sym]

    # Pre-build per-ticker indexed list of (anchor_date, validator_state, options_state).
    by_ticker: Dict[str, List[Tuple[date, Dict[str, Optional[str]]]]] = defaultdict(list)
    for r in rows:
        ticker = _safe_str(r.get("ticker")).upper()
        if not ticker:
            continue
        ad = _parse_date(r.get("anchor_date") or r.get("logged_at"))
        if ad is None:
            continue
        states = _extract_states(r)
        by_ticker[ticker].append((ad, states))

    records: List[Dict[str, Any]] = []
    for r in rows:
        ticker = _safe_str(r.get("ticker")).upper()
        if not ticker:
            continue
        anchor = _parse_date(r.get("anchor_date") or r.get("logged_at"))
        if anchor is None:
            continue
        states = _extract_states(r)
        frame = _frame(ticker)

        anchor_close = _anchor_close(frame, anchor)
        forward_returns: Dict[str, Optional[float]] = {}
        forward_mfe: Dict[str, Optional[float]] = {}
        forward_mae: Dict[str, Optional[float]] = {}
        for n in FORWARD_HORIZONS_DAYS:
            forward_returns[f"return_{n}d_pct"] = _forward_return_pct(frame, anchor, n)
            mfe = _forward_max_high_pct(frame, anchor, n)
            mae = _forward_min_low_pct(frame, anchor, n)
            forward_mfe[f"mfe_{n}d_pct"] = mfe
            forward_mae[f"mae_{n}d_pct"] = mae

        # Later actionability lookup — strictly later anchors only.
        became_actionable_later = False
        later_first_actionable_date: Optional[str] = None
        for later_anchor, later_states in by_ticker.get(ticker, []):
            if later_anchor <= anchor:
                continue
            label = _safe_str(later_states.get("validator_state"))
            if label in _ACTIONABLE_LABELS:
                became_actionable_later = True
                later_first_actionable_date = later_anchor.isoformat()
                break

        # Options-quality delta: from this snapshot forward to most recent.
        options_seq = [s.get("options_state") for d, s in by_ticker.get(ticker, []) if d >= anchor]
        options_seq = [s for s in options_seq if s]
        options_delta = _classify_options_delta(
            options_seq[0] if options_seq else None,
            options_seq[-1] if options_seq else None,
        )

        gk_state: Optional[str] = None
        gk_blocked = False
        gk = read_gatekeeper(ticker) if read_gatekeeper else None
        if isinstance(gk, dict):
            gk_state = _safe_str(gk.get("final_status")).upper() or None
            gk_blocked = bool(gk_state in _BLOCKED_GATEKEEPER)

        # Maturity: a 10d window is mature when all forward returns resolve.
        mature_horizons = [n for n in FORWARD_HORIZONS_DAYS
                           if forward_returns[f"return_{n}d_pct"] is not None]
        max_mature = max(mature_horizons) if mature_horizons else 0

        records.append({
            "snapshot_id": r.get("snapshot_id"),
            "ticker": ticker,
            "anchor_date": anchor.isoformat(),
            "validator_state": states.get("validator_state"),
            "options_state": states.get("options_state"),
            "alpha_state": states.get("alpha_state"),
            "anchor_close": anchor_close,
            **forward_returns,
            **forward_mfe,
            **forward_mae,
            "became_actionable_later": became_actionable_later,
            "later_first_actionable_date": later_first_actionable_date,
            "options_quality_delta": options_delta,
            "gatekeeper_status": gk_state,
            "blocked_by_gatekeeper": gk_blocked,
            "max_mature_window_days": max_mature,
            "status": "matured" if max_mature >= max(FORWARD_HORIZONS_DAYS) else "open",
        })

    return records


def aggregate_by(
    records: List[Dict[str, Any]],
    *,
    group_field: str,
) -> Dict[str, Dict[str, Any]]:
    """Per-state aggregate: counts, forward-return stats, MFE/MAE, hit-rate,
    became-actionable fraction, options-delta fraction, gatekeeper-blocked
    fraction."""
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        key = _safe_str(r.get(group_field)) or "—"
        buckets[key].append(r)

    out: Dict[str, Dict[str, Any]] = {}
    for state, rs in buckets.items():
        n_total = len(rs)
        n_matured = sum(1 for r in rs if r.get("status") == "matured")
        per_horizon: Dict[str, Any] = {}
        for n in FORWARD_HORIZONS_DAYS:
            returns = [r[f"return_{n}d_pct"] for r in rs
                       if r.get(f"return_{n}d_pct") is not None]
            mfes = [r[f"mfe_{n}d_pct"] for r in rs
                    if r.get(f"mfe_{n}d_pct") is not None]
            maes = [r[f"mae_{n}d_pct"] for r in rs
                    if r.get(f"mae_{n}d_pct") is not None]
            per_horizon[f"{n}d"] = {
                "return_stats": _summary_stats(returns),
                "mfe_stats": _summary_stats(mfes),
                "mae_stats": _summary_stats(maes),
                "hit_rate": _hit_rate(returns),
            }
        n_actionable_later = sum(1 for r in rs if r.get("became_actionable_later"))
        n_gk_blocked = sum(1 for r in rs if r.get("blocked_by_gatekeeper"))
        opt_deltas = [r.get("options_quality_delta") or "n/a" for r in rs]
        opt_delta_counts = {
            label: sum(1 for d in opt_deltas if d == label)
            for label in ("improved", "worsened", "same", "n/a")
        }
        out[state] = {
            "n_total": n_total,
            "n_matured": n_matured,
            "horizons": per_horizon,
            "became_actionable_later_frac":
                round(n_actionable_later / n_total, 4) if n_total else None,
            "blocked_by_gatekeeper_frac":
                round(n_gk_blocked / n_total, 4) if n_total else None,
            "options_quality_delta_counts": opt_delta_counts,
        }
    return out


def build_summary(
    *,
    log_path: Path = STOCK_LENS_LOG_PATH,
    price_dirs: Iterable[Path] = PRICE_DIRS_DEFAULT,
    today: Optional[date] = None,
    read_gatekeeper=_read_gatekeeper,
) -> Dict[str, Any]:
    records = compute_forward_records(
        log_path=log_path, price_dirs=price_dirs,
        today=today, read_gatekeeper=read_gatekeeper,
    )
    summary = {
        "version": "VALIDATOR_FORWARD_V1",
        "built_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "log_path": str(log_path),
        "horizons_days": list(FORWARD_HORIZONS_DAYS),
        "research_only": True,
        "diagnostic_only": True,
        "guardrails": [
            "diagnostic-only: never changes scoring weights or gates",
            "cache-only: no provider calls, no DB writes",
            "lens log is read-only here; the Phase 5 resolver owns updates",
        ],
        "record_counts": {
            "total": len(records),
            "matured": sum(1 for r in records if r.get("status") == "matured"),
            "open":    sum(1 for r in records if r.get("status") == "open"),
        },
        "by_validator_state": aggregate_by(records, group_field="validator_state"),
        "by_options_state":   aggregate_by(records, group_field="options_state"),
        "by_alpha_state":     aggregate_by(records, group_field="alpha_state"),
        "records": records,
    }
    return summary


def _render_state_block(title: str, agg: Dict[str, Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    lines.append(f"-- {title} --")
    if not agg:
        lines.append("  (no records)")
        return lines
    states = sorted(agg.keys(), key=lambda s: (-agg[s]["n_total"], s))
    header = (f"  {'state':<28s} {'n':>4s} {'mat':>4s} "
              f"{'r5d_med':>8s} {'mfe5':>7s} {'mae5':>7s} "
              f"{'hit5':>6s} {'act-later':>9s} {'gk-blk':>7s}")
    lines.append(header)
    for s in states:
        row = agg[s]
        h5 = (row.get("horizons") or {}).get("5d") or {}
        rstats = h5.get("return_stats") or {}
        mfes = h5.get("mfe_stats") or {}
        maes = h5.get("mae_stats") or {}
        r_med = rstats.get("median")
        hit = h5.get("hit_rate")
        act = row.get("became_actionable_later_frac")
        gkb = row.get("blocked_by_gatekeeper_frac")
        def _fmt_pct(v):
            return "—" if v is None else f"{v:+.2f}"
        def _fmt_frac(v):
            return "—" if v is None else f"{v*100:.0f}%"
        lines.append(
            f"  {s[:28]:<28s} {row['n_total']:>4d} {row['n_matured']:>4d} "
            f"{_fmt_pct(r_med):>8s} {_fmt_pct(mfes.get('median')):>7s} {_fmt_pct(maes.get('median')):>7s} "
            f"{_fmt_frac(hit):>6s} {_fmt_frac(act):>9s} {_fmt_frac(gkb):>7s}"
        )
    return lines


def render_text(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("=" * 88)
    lines.append("VALIDATOR STATE FORWARD REPORT — diagnostic only · cache-only")
    lines.append("=" * 88)
    lines.append(f"built_at:     {summary.get('built_at','—')}")
    lines.append(f"horizons:     {','.join(str(n)+'d' for n in summary.get('horizons_days', []))}")
    counts = summary.get("record_counts") or {}
    lines.append(
        f"records:      total={counts.get('total',0)}  "
        f"matured={counts.get('matured',0)}  open={counts.get('open',0)}"
    )
    lines.append("")
    lines.extend(_render_state_block("BY VALIDATOR STATE (lens entry.view)",
                                     summary.get("by_validator_state") or {}))
    lines.append("")
    lines.extend(_render_state_block("BY OPTIONS STATE (lens options quality)",
                                     summary.get("by_options_state") or {}))
    lines.append("")
    lines.extend(_render_state_block("BY ALPHA STATE (lens alpha.view)",
                                     summary.get("by_alpha_state") or {}))
    lines.append("")
    lines.append("Notes:")
    lines.append("  r5d_med   — median 5d forward return %")
    lines.append("  mfe5/mae5 — median 5d max-favorable / max-adverse excursion %")
    lines.append("  hit5      — fraction with positive 5d return")
    lines.append("  act-later — fraction whose later snapshots reached Buyable Now/Pullback")
    lines.append("  gk-blk    — fraction whose current Gatekeeper status is BLOCK/WATCH/AVOID/REJECT")
    lines.append("")
    lines.append("research-only · diagnostic · no scoring/gate changes")
    return "\n".join(lines) + "\n"


def write_summary(
    *,
    summary: Optional[Dict[str, Any]] = None,
    json_path: Path = SUMMARY_JSON,
    text_path: Path = SUMMARY_TXT,
    log_path: Path = STOCK_LENS_LOG_PATH,
    price_dirs: Iterable[Path] = PRICE_DIRS_DEFAULT,
    read_gatekeeper=_read_gatekeeper,
) -> Dict[str, str]:
    if summary is None:
        summary = build_summary(
            log_path=log_path, price_dirs=price_dirs,
            read_gatekeeper=read_gatekeeper,
        )
    json_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    text_path.write_text(render_text(summary), encoding="utf-8")
    return {"json": str(json_path), "text": str(text_path)}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Validator-state forward-outcome diagnostic (cache-only)"
    )
    p.add_argument("--log-path", type=Path, default=STOCK_LENS_LOG_PATH)
    p.add_argument("--json", dest="print_json", action="store_true",
                   help="print the JSON summary to stdout after writing")
    p.add_argument("--print", dest="do_print", action="store_true",
                   help="print the text summary to stdout after writing")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    paths = write_summary(log_path=args.log_path)
    summary = build_summary(log_path=args.log_path)
    counts = summary.get("record_counts") or {}
    print(
        f"validator_state_forward: records={counts.get('total',0)} "
        f"matured={counts.get('matured',0)} open={counts.get('open',0)} "
        f"→ {paths['json']}"
    )
    if args.do_print:
        print()
        print(Path(paths["text"]).read_text(encoding="utf-8"))
    if args.print_json:
        print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
