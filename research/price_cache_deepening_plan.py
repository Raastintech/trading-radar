"""
research/price_cache_deepening_plan.py — Phase 1G.7 Task 1.

Builds a PLAN (no provider calls, ever) for deepening the research price cache so
the MA200 / 260-bar gates can be honestly evaluated. The production daemon
overwrites cache/prices/*.parquet with ~90-day windows each scan, so deep history
must be written to a SEPARATE deep cache (cache/prices_deep) by the gated refresh
tool scripts/deepen_price_cache.py — this module only PLANS that refresh.

It reports current ticker count + bar-depth distribution, prioritised batches
(Alpha board → RS lane → theme leaders → top missed winners → watchlist/open
positions), estimated provider calls + storage, and the exact safe command.

Outputs:
  cache/research/price_cache_deepening_plan_latest.json
  logs/price_cache_deepening_plan_latest.txt
  docs/research/PRICE_CACHE_DEEPENING_PLAN.md

RESEARCH-ONLY / CACHE-ONLY. No provider calls, no DB writes, no signals.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Set

from research.scanner_truth import dataio

TARGET_BARS = 300
CHUNK_SIZE = 200                 # mirrors core/universe.py _CHUNK_SIZE / Alpaca batch
APPROX_BYTES_PER_BAR = 70        # snappy parquet OHLCV row, empirical ballpark

WINNER_UNI = dataio.RESEARCH_CACHE / "missed_winner_universe_latest.json"
ALPHA_BOARD = dataio.RESEARCH_CACHE / "alpha_discovery_board_latest.json"
RS_LANE = dataio.RESEARCH_CACHE / "rs_recall_lane_latest.json"
THEME = dataio.RESEARCH_CACHE / "theme_leadership_latest.json"
BROKER_SNAP = dataio.REPO / "cache" / "state" / "broker_positions_snapshot.json"
DOCS_DIR = dataio.REPO / "docs" / "research"


def _load(p) -> Dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _bar_count(t: str) -> int:
    df = dataio.load_prices(t)
    return 0 if df is None else int(df["close"].notna().sum())


def _alpha_tickers() -> List[str]:
    b = _load(ALPHA_BOARD)
    return [(i.get("ticker") or i.get("symbol") or "").upper()
            for i in b.get("items", []) if (i.get("ticker") or i.get("symbol"))]


def _rs_tickers() -> List[str]:
    r = _load(RS_LANE)
    return [x["ticker"].upper() for x in r.get("live", {}).get("top_rs_leaders", [])]


def _theme_leaders() -> List[str]:
    th = _load(THEME)
    out: List[str] = []
    for name, d in (th.get("themes") or {}).items():
        if d.get("theme_state") in ("LEADING", "EMERGING"):
            out += [t.upper() for t in d.get("top_leaders", [])]
    return out


def _top_winners(n: int = 150) -> List[str]:
    uni = _load(WINNER_UNI)
    ws = sorted(uni.get("winners", []), key=lambda w: -w.get("best_max_return", 0))
    return [w["ticker"].upper() for w in ws[:n]]


def _open_positions() -> List[str]:
    snap = _load(BROKER_SNAP)
    rows = snap.get("positions", snap if isinstance(snap, list) else [])
    out = []
    for r in (rows or []):
        sym = (r.get("symbol") or r.get("ticker") or "").upper() if isinstance(r, dict) else ""
        if sym:
            out.append(sym)
    return out


def build() -> Dict:
    all_t = [t for t in dataio.all_price_tickers() if t not in dataio.BENCHMARKS]
    bars = {t: _bar_count(t) for t in all_t}
    depth = {
        "ge_60": sum(1 for v in bars.values() if v >= 60),
        "ge_120": sum(1 for v in bars.values() if v >= 120),
        "ge_200": sum(1 for v in bars.values() if v >= 200),
        "ge_260": sum(1 for v in bars.values() if v >= 260),
        "ge_300": sum(1 for v in bars.values() if v >= 300),
    }

    # Priority batches, de-duplicated in priority order.
    seen: Set[str] = set()
    batches: List[Dict] = []
    for label, tickers in [
        ("1_alpha_board", _alpha_tickers()),
        ("2_rs_lane", _rs_tickers()),
        ("3_theme_leaders", _theme_leaders()),
        ("4_top_missed_winners", _top_winners()),
        ("5_open_positions", _open_positions()),
    ]:
        uniq = [t for t in dict.fromkeys(tickers) if t and t not in seen
                and t not in dataio.BENCHMARKS]
        for t in uniq:
            seen.add(t)
        need_deepen = [t for t in uniq if bars.get(t, 0) < TARGET_BARS]
        batches.append({
            "batch": label,
            "n_total": len(uniq),
            "n_needing_deepen": len(need_deepen),
            "already_deep": len(uniq) - len(need_deepen),
            "tickers_needing_deepen": need_deepen,
        })

    to_deepen = sorted(seen)
    n_deepen = len([t for t in to_deepen if bars.get(t, 0) < TARGET_BARS])
    est_calls = -(-n_deepen // CHUNK_SIZE)  # ceil; Alpaca batch = 1 call / chunk
    est_storage_mb = round(n_deepen * TARGET_BARS * APPROX_BYTES_PER_BAR / 1e6, 1)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "PLAN ONLY — no provider calls made. Refresh is a separate, "
                      "gated, operator-approved command (default dry-run).",
        "target_bars": TARGET_BARS,
        "deep_cache_dir": str(dataio.DEEP_PRICES_DIR.relative_to(dataio.REPO)),
        "current_universe_size": len(all_t),
        "current_depth_distribution": depth,
        "median_bars": sorted(bars.values())[len(bars) // 2] if bars else 0,
        "priority_batches": batches,
        "n_priority_tickers": len(to_deepen),
        "n_needing_deepen": n_deepen,
        "estimated_provider_calls": {
            "alpaca_batch_requests": est_calls,
            "chunk_size": CHUNK_SIZE,
            "note": "Alpaca SIP batched daily-bars; ~1 request per %d symbols. "
                    "FMP is NOT used for OHLCV — zero FMP budget impact." % CHUNK_SIZE},
        "estimated_storage_mb": est_storage_mb,
        "safe_refresh_command": {
            "dry_run": "SNIPER_ENV_PATH=/home/gem/secure/trading.env "
                       ".venv/bin/python scripts/deepen_price_cache.py --priority",
            "execute": "SNIPER_ENV_PATH=/home/gem/secure/trading.env "
                       ".venv/bin/python scripts/deepen_price_cache.py --priority --execute",
            "note": "Default is DRY-RUN (no provider calls). --execute is required to "
                    "fetch; output is written to %s (merge-on-write), never "
                    "cache/prices, so the daemon's 90-day overwrite cannot clobber it."
                    % str(dataio.DEEP_PRICES_DIR.relative_to(dataio.REPO))},
        "provider_impact": {
            "alpaca": "Daily bars via SIP, batched; well within rate limits at this size.",
            "fmp": "None — OHLCV deepening does not touch FMP; monthly FMP budget unaffected.",
            "dashboard": "Unaffected — dashboard stays cache-only and never triggers refresh."},
    }


def _render_txt(res: Dict) -> List[str]:
    L = [
        f"== PRICE CACHE DEEPENING PLAN ({res['generated_at']}) ==",
        res["disclaimer"],
        f"target: ≥{res['target_bars']} bars  ·  deep cache: {res['deep_cache_dir']}",
        f"universe: {res['current_universe_size']}  ·  median bars: {res['median_bars']}",
        "",
        "current depth: " + ", ".join(f"{k}={v}" for k, v in res["current_depth_distribution"].items()),
        "",
        f"{'batch':<24}{'total':>7}{'deepen':>8}{'deep':>6}",
    ]
    for b in res["priority_batches"]:
        L.append(f"{b['batch']:<24}{b['n_total']:>7}{b['n_needing_deepen']:>8}{b['already_deep']:>6}")
    L += [
        "",
        f"priority tickers: {res['n_priority_tickers']}  ·  needing deepen: {res['n_needing_deepen']}",
        f"estimated Alpaca batch requests: {res['estimated_provider_calls']['alpaca_batch_requests']}",
        f"estimated storage: {res['estimated_storage_mb']} MB",
        "",
        "SAFE REFRESH (dry-run default):",
        "  " + res["safe_refresh_command"]["dry_run"],
        "TO EXECUTE (provider calls):",
        "  " + res["safe_refresh_command"]["execute"],
    ]
    return L


def _write_doc(res: Dict) -> None:
    p = DOCS_DIR / "PRICE_CACHE_DEEPENING_PLAN.md"
    d = res["current_depth_distribution"]
    L = [
        "# Price Cache Deepening Plan — Phase 1G.7 (Task 1)",
        "",
        f"*Generated {res['generated_at']} · research-only · PLAN ONLY (no provider "
        "calls made by this report).*",
        "",
        "## Why",
        "Phase 1G.6 found the research price cache is uniformly shallow (median "
        f"~{res['median_bars']} bars). The production daemon overwrites "
        "`cache/prices/*.parquet` with ~90-day windows on every scan (see "
        "`core/data_gatekeeper.py` and `core/universe.py:_DAYS_BACK=90`), so MA200 and "
        "the Voyager 260-bar gate are effectively non-functional on the research cache "
        "and the scanner-truth Voyager-structural verdicts are fidelity-limited.",
        "",
        "## Strategy: a separate deep cache",
        f"Because the daemon clobbers `cache/prices`, the deepening refresh writes "
        f"**merge-on-write** parquets to `{res['deep_cache_dir']}` instead. Research "
        "`dataio.load_prices` prefers the deep parquet when present and falls back to "
        "`cache/prices` otherwise — additive and a no-op until the refresh runs. The "
        "live execution path is untouched.",
        "",
        "## Current depth distribution",
        "",
        "| bars | tickers |",
        "|---|--:|",
        f"| ≥60 | {d['ge_60']} |",
        f"| ≥120 | {d['ge_120']} |",
        f"| ≥200 | {d['ge_200']} |",
        f"| ≥260 | {d['ge_260']} |",
        f"| ≥300 | {d['ge_300']} |",
        "",
        f"Universe size: **{res['current_universe_size']}** · median **{res['median_bars']}** bars.",
        "",
        "## Priority batches",
        "",
        "| batch | total | needing deepen | already deep |",
        "|---|--:|--:|--:|",
    ]
    for b in res["priority_batches"]:
        L.append(f"| {b['batch']} | {b['n_total']} | {b['n_needing_deepen']} | {b['already_deep']} |")
    L += [
        "",
        f"**Priority tickers:** {res['n_priority_tickers']} · "
        f"**needing deepen:** {res['n_needing_deepen']}.",
        "",
        "## Estimated impact",
        f"- **Alpaca batch requests:** ~{res['estimated_provider_calls']['alpaca_batch_requests']} "
        f"(batched at {res['estimated_provider_calls']['chunk_size']}/request, SIP daily bars).",
        "- **FMP budget:** none — OHLCV deepening does not touch FMP.",
        f"- **Storage:** ~{res['estimated_storage_mb']} MB additional parquet.",
        "- **Dashboard:** unaffected; stays cache-only.",
        "",
        "## Safe refresh command",
        "",
        "Dry-run (default — **no provider calls**, prints the plan the tool would run):",
        "```bash",
        res["safe_refresh_command"]["dry_run"],
        "```",
        "Execute (provider calls; writes to the deep cache, merge-on-write):",
        "```bash",
        res["safe_refresh_command"]["execute"],
        "```",
        "",
        "**Do not run the `--execute` form until this plan is reviewed.** The tool "
        "defaults to dry-run and requires the explicit `--execute` flag. It never writes "
        "`cache/prices`, never touches the DB, governance, or live capital.",
        "",
        "## After refresh",
        "Re-run `research/price_cache_coverage_audit.py` (Phase 1G.7 Task 2 buckets) to "
        "confirm coverage, then re-run the scanner-truth review to confirm or withdraw "
        "the Voyager-structural conclusions.",
        "",
    ]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(L) + "\n")


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "price_cache_deepening_plan_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "price_cache_deepening_plan_latest.txt", lines)
    _write_doc(res)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
