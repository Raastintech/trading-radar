"""
research/recall_shadow_gk_cohort_freeze.py — Phase 1G.17A cohort freeze.

Freezes the 20-name recall-shadow → Lens/Gatekeeper cohort produced by the
2026-06-11 feeder refresh as an IMMUTABLE, WRITE-ONCE evidence file, so the
forward validation (research/recall_shadow_gk_forward.py) measures a cohort
that cannot be redrawn after outcomes start arriving (the 1G.10/1G.11
frozen-cohort pattern — verdicts must not be re-rolled on a sliding board).

Per name, frozen at freeze time:
  ticker · feeder rank/label · theme · sector · price_at_refresh (last cached
  close at-or-before the anchor date, with its actual bar date recorded) ·
  lane extension state (extension_label / ext_pct) · Lens state (label +
  confidence + generated_at) · Gatekeeper final_status (WATCH/BLOCK) +
  confidence + per-gate verdicts + blocking gates · too_extended_block flag
  (entry_quality BLOCK that cites 'Too Extended').

WRITE-ONCE: if the frozen cohort file already exists the script refuses to
overwrite it (exit 0, message) — pass --reprint to re-render the txt view
from the existing frozen file. A convenience copy of the frozen JSON is
mirrored to cache/research/ for dashboards; the data/research file is the
canonical immutable artifact.

RESEARCH-ONLY / CACHE-ONLY / READ-ONLY on all inputs. No paper signals, no
trade proposals, no execution/governance/live-capital change, no DB writes.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from research.scanner_truth import dataio

FEEDER_JSON = dataio.RESEARCH_CACHE / "recall_shadow_lens_feeder_latest.json"
LANE_JSON = dataio.RESEARCH_CACHE / "recall_repair_shadow_lane_latest.json"
FROZEN = dataio.HISTORY_DIR / "recall_shadow_gk_cohort_1g17a.json"
MIRROR = dataio.RESEARCH_CACHE / "recall_shadow_gk_cohort_1g17a_latest.json"
OUT_TXT = dataio.LOGS_DIR / "recall_shadow_gk_cohort_1g17a_latest.txt"

COHORT_VERSION = "1G.17A"


def _gk(ticker: str) -> Optional[Dict]:
    p = dataio.RESEARCH_CACHE / f"executive_gatekeeper_{ticker}_latest.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _lens(ticker: str) -> Optional[Dict]:
    p = dataio.RESEARCH_CACHE / f"stock_lens_{ticker}_latest.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _price_at(ticker: str, anchor: str) -> Dict:
    """Last cached close at-or-before the anchor date (no look-ahead).
    Records the actual bar date used so a half-day/holiday anchor is honest."""
    df = dataio.load_prices(ticker)
    if df is None or df.empty:
        return {"price_at_refresh": None, "price_bar_date": None}
    d = df[df.index <= anchor]
    if d.empty:
        return {"price_at_refresh": None, "price_bar_date": None}
    return {"price_at_refresh": round(float(d["close"].iloc[-1]), 4),
            "price_bar_date": str(d.index[-1].date())}


def build_row(cand: Dict, lane_by_ticker: Dict[str, Dict],
              anchor: str) -> Dict:
    t = str(cand["ticker"]).upper()
    gk = _gk(t) or {}
    lens = _lens(t) or {}
    lane = lane_by_ticker.get(t) or {}

    gates = gk.get("gates") or []
    blocking_gates = sorted({g.get("name") for g in gates
                             if g.get("verdict") == "BLOCK" and g.get("name")})
    blocking_reasons = gk.get("blocking_reasons") or []
    too_extended_block = (
        gk.get("final_status") == "BLOCK"
        and any("too extended" in str(r).lower() for r in blocking_reasons))

    row = {
        "ticker": t,
        "feeder_rank": cand.get("rank"),
        "shadow_label": cand.get("label"),
        "theme": cand.get("theme"),
        "sector": cand.get("sector"),
        "extension_label": lane.get("extension_label"),
        "ext_pct": lane.get("ext_pct"),
        "lens_state": lens.get("label"),
        "lens_confidence": lens.get("confidence"),
        "lens_generated_at": lens.get("generated_at"),
        "gk_status": gk.get("final_status"),
        "gk_confidence": gk.get("confidence"),
        "gk_generated_at": gk.get("generated_at"),
        "gk_blocking_gates": blocking_gates,
        "gk_blocking_reasons": blocking_reasons,
        "too_extended_block": too_extended_block,
        "note": ("frozen research cohort row · NOT a signal · NOT a trade "
                 "proposal · NOT paper evidence"),
    }
    row.update(_price_at(t, anchor))
    return row


def build(anchor: Optional[str] = None) -> Dict:
    feeder = json.loads(FEEDER_JSON.read_text())
    cands = feeder.get("candidates") or []
    if not cands:
        raise RuntimeError("feeder sidecar has no candidates — nothing to freeze")
    lane_by_ticker: Dict[str, Dict] = {}
    try:
        lane = json.loads(LANE_JSON.read_text())
        lane_by_ticker = {str(c.get("ticker")).upper(): c
                          for c in lane.get("candidates") or []}
    except Exception:
        pass
    anchor = anchor or datetime.now(timezone.utc).date().isoformat()
    rows = [build_row(c, lane_by_ticker, anchor) for c in cands]
    n_watch = sum(1 for r in rows if r["gk_status"] == "WATCH")
    n_block = sum(1 for r in rows if r["gk_status"] == "BLOCK")
    return {
        "kind": "recall_shadow_gk_cohort_freeze",
        "version": COHORT_VERSION,
        "research_only": True,
        "immutable": True,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "anchor_date": anchor,
        "disclaimer": ("immutable research cohort · no signals, no proposals, "
                       "no execution/governance/DB side effects · forward "
                       "outcomes are measured by "
                       "research/recall_shadow_gk_forward.py"),
        "source_feeder_generated_at": feeder.get("generated_at"),
        "n_total": len(rows),
        "n_watch": n_watch,
        "n_block": n_block,
        "n_too_extended_block": sum(1 for r in rows if r["too_extended_block"]),
        "rows": rows,
    }


def _render_txt(res: Dict) -> List[str]:
    lines = [
        f"RECALL-SHADOW × GATEKEEPER COHORT {res['version']} — frozen "
        f"{res['frozen_at'][:16]}Z (immutable; research-only)",
        "=" * 96,
        f"anchor={res['anchor_date']}  n={res['n_total']}  "
        f"WATCH={res['n_watch']}  BLOCK={res['n_block']}  "
        f"too_extended blocks={res['n_too_extended_block']}",
        "",
        f"{'tkr':6s} {'gk':6s} {'price':>9s} {'bar_date':10s} "
        f"{'ext':>7s} {'lens':14s} {'theme':16s} blocking",
    ]
    for r in res["rows"]:
        ext = f"{r['ext_pct']:+.1%}" if r.get("ext_pct") is not None else "—"
        lines.append(
            f"{r['ticker']:6s} {str(r['gk_status']):6s} "
            f"{r['price_at_refresh'] if r['price_at_refresh'] is not None else '—':>9} "
            f"{str(r['price_bar_date']):10s} {ext:>7s} "
            f"{str(r['lens_state']):14s} {str(r['theme']):16s} "
            f"{','.join(r['gk_blocking_gates']) or '—'}")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 1G.17A cohort freeze (write-once)")
    ap.add_argument("--anchor", default=None,
                    help="anchor date ISO (default: today UTC)")
    ap.add_argument("--reprint", action="store_true",
                    help="re-render txt from the existing frozen file")
    args = ap.parse_args(argv)

    if FROZEN.exists():
        res = json.loads(FROZEN.read_text())
        msg = (f"cohort already frozen at {res.get('frozen_at')} — refusing "
               f"to overwrite (immutable evidence)")
        if args.reprint:
            lines = _render_txt(res)
            dataio.write_text(OUT_TXT, lines)
            print("\n".join(lines))
        print(msg)
        return 0

    res = build(anchor=args.anchor)
    dataio.write_json(FROZEN, res)
    dataio.write_json(MIRROR, res)
    lines = _render_txt(res)
    dataio.write_text(OUT_TXT, lines)
    print("\n".join(lines))
    print(f"\nfroze {dataio.rel_to_repo(FROZEN)} (write-once) · mirror "
          f"{dataio.rel_to_repo(MIRROR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
