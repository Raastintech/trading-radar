#!/usr/bin/env python3
"""research/core_satellite_forward_shadow.py — Phase 2A forward-shadow ledger.

Core-Satellite 2A (research/core_satellite_portfolio.py) is the only
strategy family in this repo that has passed a full pre-registered
backtest gate (REGIME_THROTTLED_QQQ and REGIME_THROTTLED_SPY_QQQ_BLEND:
+52.7% CAGR, maxDD -10% vs QQQ's -22.8%, Calmar 1.90, positive every
year). It has no forward-validation venue: paper-trading and broker
execution were permanently decommissioned 2026-06-13
(scripts/run_paper_evidence.py no longer runs), so the strategy that
earned the next rung of the promotion ladder has nowhere to go. This
module is that venue — a narrow, cache-only, arithmetic forward ledger,
not a trade, not a signal, not an execution path.

Reuses the EXACT backtested rules rather than re-deriving them:
research.strategy_lab_regime.classify_regime for the as-of regime label,
and core_satellite_portfolio.target_exposure / blend_asset for the
exposure ladder and QQQ/SPY blend choice (research/
CORE_SATELLITE_REGIME_SPEC.md). Only two variants are shadowed — the two
that actually passed the gate; the satellite overlays and the leveraged
variant (which missed its own gate) are out of scope here.

No lookahead, matching core_satellite_portfolio.simulate_variant's rule
exactly: the exposure applied to day i's return is decided from day
i-1's as-of regime. Each run: (1) realizes the PREVIOUS row's decided
exposure against today's close, updating NAV; (2) classifies TODAY's
regime to decide TOMORROW's exposure, and appends one new row. The
first-ever run seeds the ledger at NAV=1.0 from whatever session is
current when it first runs — this intentionally does NOT backfill or
reconstruct history retroactively.

Never imports broker, execution, governance, paper-signal, or
live-capital modules. Writes only:
  - data/research/core_satellite_forward_shadow.jsonl (append-only,
    idempotent per session)
  - cache/research/core_satellite_forward_shadow_latest.json (metrics
    sidecar, overwritten each run)
Both outputs are watermarked SHADOW / NOT A TRADE and feed nothing back
into scores, gates, rankings, or candidate selection.

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \
      research/core_satellite_forward_shadow.py --dry-run
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \
      research/core_satellite_forward_shadow.py            # append + report
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \
      research/core_satellite_forward_shadow.py --report-only
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LEDGER_REL = Path("data") / "research" / "core_satellite_forward_shadow.jsonl"
SIDECAR_REL = Path("cache") / "research" / "core_satellite_forward_shadow_latest.json"

VERSION = "CORE_SATELLITE_FORWARD_SHADOW_V1"
WATERMARK = "SHADOW — NOT A TRADE. Research-only arithmetic bookkeeping."
VARIANTS = ("qqq_variant", "blend_variant")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── ledger IO (append-only, idempotent per session) ─────────────────────────


def load_ledger(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(root) if root else REPO_ROOT
    path = root / LEDGER_REL
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("date"):
            rows.append(row)
    rows.sort(key=lambda r: r["date"])
    return rows


def append_row(row: Dict[str, Any], root: Optional[Path] = None) -> bool:
    """Append one ledger row; idempotent per date (re-runs same day no-op)."""
    root = Path(root) if root else REPO_ROOT
    existing = {r.get("date") for r in load_ledger(root)}
    if row["date"] in existing:
        return False
    path = root / LEDGER_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


# ── pure row-building logic (no I/O — fully unit-testable) ──────────────────


def build_next_row(*, today: str, qqq_close: float, spy_close: float,
                   prev: Optional[Dict[str, Any]],
                   regime_label: Optional[str],
                   qqq_vs_spy_20: Optional[float],
                   target_exposure_fn, blend_asset_fn) -> Dict[str, Any]:
    """One ledger row. Realizes ``prev``'s decided exposure (if any) against
    today's close, then decides tomorrow's exposure from today's regime.
    ``target_exposure_fn``/``blend_asset_fn`` are injected so this stays a
    pure function in tests while production code passes the real,
    already-backtested rules from core_satellite_portfolio.py."""
    row: Dict[str, Any] = {
        "date": today,
        "qqq_close": float(qqq_close),
        "spy_close": float(spy_close),
        "registered_at": _utcnow(),
    }

    if prev is None:
        row["seed"] = True
        row["realized_return_qqq_variant"] = None
        row["realized_return_blend_variant"] = None
        row["shadow_nav_qqq_variant"] = 1.0
        row["shadow_nav_blend_variant"] = 1.0
    else:
        prior_qqq = prev.get("qqq_close")
        prior_spy = prev.get("spy_close")
        qqq_ret = (row["qqq_close"] / prior_qqq - 1.0) \
            if prior_qqq else 0.0
        spy_ret = (row["spy_close"] / prior_spy - 1.0) \
            if prior_spy else 0.0
        target_w = float(prev.get("decided_target_exposure") or 0.0)
        blend_choice = prev.get("decided_blend_asset") or "QQQ"
        r_qqq_variant = target_w * qqq_ret
        r_blend_variant = target_w * (qqq_ret if blend_choice == "QQQ"
                                      else spy_ret)
        prev_nav_qqq = float(prev.get("shadow_nav_qqq_variant") or 1.0)
        prev_nav_blend = float(prev.get("shadow_nav_blend_variant") or 1.0)
        row["realized_return_qqq_variant"] = round(r_qqq_variant, 8)
        row["realized_return_blend_variant"] = round(r_blend_variant, 8)
        row["shadow_nav_qqq_variant"] = round(
            prev_nav_qqq * (1.0 + r_qqq_variant), 8)
        row["shadow_nav_blend_variant"] = round(
            prev_nav_blend * (1.0 + r_blend_variant), 8)

    row["regime_label"] = regime_label
    row["qqq_vs_spy_20"] = qqq_vs_spy_20
    row["decided_target_exposure"] = target_exposure_fn(regime_label)
    row["decided_blend_asset"] = blend_asset_fn(qqq_vs_spy_20)
    row["research_only"] = True
    row["watermark"] = WATERMARK
    return row


def build_metrics_report(ledger: List[Dict[str, Any]]) -> Dict[str, Any]:
    """CAGR / maxDD / Calmar per variant from the ledger's NAV series alone
    (pure function, no I/O). Annualization on a young ledger is noisy by
    construction; metrics are suppressed below MIN_DAYS_FOR_ANNUALIZED and
    the raw cumulative return / day count are always shown instead."""
    MIN_DAYS_FOR_ANNUALIZED = 20
    out: Dict[str, Any] = {
        "n_rows": len(ledger),
        "first_date": ledger[0]["date"] if ledger else None,
        "last_date": ledger[-1]["date"] if ledger else None,
        "variants": {},
    }
    if len(ledger) < 2:
        out["note"] = ("Fewer than 2 rows — no realized return yet "
                       "(seed row only).")
        return out

    n_calendar_days = (
        _date_diff_days(ledger[0]["date"], ledger[-1]["date"]))

    for variant in VARIANTS:
        navs = [r.get(f"shadow_nav_{variant}") for r in ledger
                if r.get(f"shadow_nav_{variant}") is not None]
        if len(navs) < 2:
            out["variants"][variant] = {"note": "insufficient history"}
            continue
        total_return = navs[-1] / navs[0] - 1.0
        peak = navs[0]
        max_dd = 0.0
        for nav in navs:
            peak = max(peak, nav)
            dd = nav / peak - 1.0
            max_dd = min(max_dd, dd)
        variant_report: Dict[str, Any] = {
            "n_realized_days": len(navs) - 1,
            "total_return_pct": round(total_return * 100, 3),
            "max_drawdown_pct": round(max_dd * 100, 3),
            "current_nav": round(navs[-1], 6),
        }
        if len(navs) - 1 >= MIN_DAYS_FOR_ANNUALIZED and n_calendar_days > 0:
            cagr = (navs[-1] / navs[0]) ** (365.25 / n_calendar_days) - 1.0
            calmar = (cagr / abs(max_dd)) if max_dd != 0 else None
            variant_report["cagr_pct"] = round(cagr * 100, 3)
            variant_report["calmar"] = (round(calmar, 3)
                                        if calmar is not None else None)
        else:
            variant_report["cagr_pct"] = None
            variant_report["calmar"] = None
            variant_report["annualized_metrics_note"] = (
                f"suppressed — needs >= {MIN_DAYS_FOR_ANNUALIZED} realized "
                f"days, has {len(navs) - 1}")
        out["variants"][variant] = variant_report
    return out


def _date_diff_days(a: str, b: str) -> int:
    fmt = "%Y-%m-%d"
    return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).days


# ── real-data glue (I/O — thin, not unit-tested directly) ───────────────────


def _load_closes():
    from research import strategy_lab_data as d
    closes = {}
    for sym in ("QQQ", "SPY"):
        frame = d._full_frame(sym)
        if frame is None or frame.empty:
            raise RuntimeError(f"no cached price frame for {sym}")
        closes[sym] = frame["close"].astype(float).sort_index()
    return closes


def _classify_today(asof):
    from research import strategy_lab_regime as regime
    classified = regime.classify_regime(asof)
    label = classified.get("label")
    qqq_vs_spy_20 = (classified.get("inputs") or {}).get("qqq_vs_spy_20")
    return label, qqq_vs_spy_20


def run(root: Optional[Path] = None, *, write: bool = True,
       ) -> Optional[Dict[str, Any]]:
    """Real-data glue: reads cached QQQ/SPY closes + the as-of regime
    classifier, builds the next row, and (if write) appends it. Returns
    the row that was built (or would have been built, under --dry-run),
    or None if there is no new session to record."""
    from research.core_satellite_portfolio import target_exposure, blend_asset

    root = Path(root) if root else REPO_ROOT
    closes = _load_closes()
    qqq, spy = closes["QQQ"], closes["SPY"]
    common_idx = qqq.index.intersection(spy.index)
    if common_idx.empty:
        return None
    today = common_idx.max()
    today_key = str(today.date())

    ledger = load_ledger(root)
    if ledger and ledger[-1]["date"] == today_key:
        return None  # already recorded this session

    label, qqq_vs_spy_20 = _classify_today(today)
    row = build_next_row(
        today=today_key,
        qqq_close=float(qqq.loc[today]),
        spy_close=float(spy.loc[today]),
        prev=(ledger[-1] if ledger else None),
        regime_label=label,
        qqq_vs_spy_20=qqq_vs_spy_20,
        target_exposure_fn=target_exposure,
        blend_asset_fn=blend_asset,
    )
    if write:
        append_row(row, root)
    return row


def write_report(root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    ledger = load_ledger(root)
    report = {
        "kind": "core_satellite_forward_shadow",
        "version": VERSION,
        "research_only": True,
        "watermark": WATERMARK,
        "generated_at": _utcnow(),
        "guardrails": {
            "no_gate_change": True,
            "no_execution": True,
            "proposal_requires_human_review": True,
        },
        **build_metrics_report(ledger),
    }
    sidecar = root / SIDECAR_REL
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return report


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Core-Satellite 2A forward-shadow ledger "
                    "(cache-only; not a trade, not a signal).")
    p.add_argument("--dry-run", action="store_true",
                  help="build today's row but do not append it")
    p.add_argument("--report-only", action="store_true",
                  help="skip the daily row entirely; just (re)write the "
                       "metrics sidecar from the existing ledger")
    args = p.parse_args(argv)

    if not args.report_only:
        row = run(write=not args.dry_run)
        if row is None:
            print("[core_satellite_forward_shadow] no new session to "
                 "record (already up to date, or no common QQQ/SPY bars).")
        else:
            tag = "[dry-run] would append" if args.dry_run else "appended"
            print(f"[core_satellite_forward_shadow] {tag}: "
                 f"{json.dumps(row, ensure_ascii=False)}")

    report = write_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
