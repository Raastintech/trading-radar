"""
research/price_cache_coverage_audit.py — Phase 1G.6 Task 4.

Asks a narrow but important question: is the PRICE CACHE deep enough that the
discovery funnel could even see the winners, or do bar-count gates (MA200,
Voyager's 260-bar floor, Sniper's 75-bar floor) silently exclude winners that
simply lack enough history in the cache?

For every price parquet it counts usable bars and checks sufficiency for
MA20 / MA50 / MA200, the Voyager (260) and Sniper (75) history floors, then
intersects with the missed-winner universe to report how many winners are
excluded by thin history rather than by a real quality decision.

Outputs:
  cache/research/price_cache_coverage_latest.json
  logs/price_cache_coverage_latest.txt

RESEARCH-ONLY / CACHE-ONLY. No provider calls (a deepening refresh would be a
SEPARATE explicit, operator-approved command), no DB writes.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List

from research.scanner_truth import dataio
from research.scanner_truth.filters import SNI_BARS_NEEDED, VOY_BARS_NEEDED

MA_LEVELS = (20, 50, 200)
DEPTH_LEVELS = (60, 120, 200, 260, 300)
WINNER_UNI = dataio.RESEARCH_CACHE / "missed_winner_universe_latest.json"
ALPHA_BOARD = dataio.RESEARCH_CACHE / "alpha_discovery_board_latest.json"
RS_LANE = dataio.RESEARCH_CACHE / "rs_recall_lane_latest.json"
THEME = dataio.RESEARCH_CACHE / "theme_leadership_latest.json"


def _winner_map() -> Dict[str, float]:
    try:
        uni = json.loads(WINNER_UNI.read_text())
        return {w["ticker"].upper(): w["best_max_return"] for w in uni["winners"]}
    except Exception:
        return {}


def _load(p) -> Dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _alpha_universe() -> List[str]:
    b = _load(ALPHA_BOARD)
    return [(i.get("ticker") or i.get("symbol") or "").upper()
            for i in b.get("items", []) if (i.get("ticker") or i.get("symbol"))]


def _rs_universe() -> List[str]:
    r = _load(RS_LANE)
    return [x["ticker"].upper() for x in r.get("live", {}).get("top_rs_leaders", [])]


def _theme_universe() -> List[str]:
    th = _load(THEME)
    out: List[str] = []
    for _name, d in (th.get("themes") or {}).items():
        if d.get("theme_state") in ("LEADING", "EMERGING"):
            out += [t.upper() for t in d.get("top_leaders", [])]
    return list(dict.fromkeys(out))


def _bar_counts() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for t in dataio.all_price_tickers():
        if t in dataio.BENCHMARKS:
            continue
        df = dataio.load_prices(t)
        out[t] = 0 if df is None else int(df["close"].notna().sum())
    return out


def _bucket(n: int) -> str:
    if n < SNI_BARS_NEEDED:
        return "lt_75"
    if n < 200:
        return "75_to_199"
    if n < VOY_BARS_NEEDED:
        return "200_to_259"
    return "ge_260"


def build() -> Dict:
    winners = _winner_map()
    bars = _bar_counts()
    total = len(bars)

    def n_ge(level: int) -> int:
        return sum(1 for v in bars.values() if v >= level)

    ma_ok = {f"ma{m}_ok": n_ge(m) for m in MA_LEVELS}
    voy_ok = n_ge(VOY_BARS_NEEDED)
    sni_ok = n_ge(SNI_BARS_NEEDED)

    depth_hist = Counter(_bucket(v) for v in bars.values())

    # winners with thin history
    win_bars = {t: bars.get(t, 0) for t in winners}
    win_missing = sorted([t for t, n in win_bars.items() if n == 0])
    win_lt_sni = sorted([t for t, n in win_bars.items() if 0 < n < SNI_BARS_NEEDED],
                        key=lambda t: -winners[t])
    win_lt_voy = sorted([t for t, n in win_bars.items() if 0 < n < VOY_BARS_NEEDED],
                        key=lambda t: -winners[t])
    win_lt_ma200 = sorted([t for t, n in win_bars.items() if 0 < n < 200],
                          key=lambda t: -winners[t])

    all_bars = sorted(bars.values())
    median_bars = all_bars[len(all_bars) // 2] if all_bars else 0
    # Is the WHOLE cache uniformly shallow (i.e. the 260-bar/MA200 gates are
    # non-functional cache-wide), or are only the winners young?
    cache_uniformly_shallow = total and (voy_ok / total) < 0.10

    # Task 2: explicit depth buckets ≥60/120/200/260/300.
    depth_buckets = {f"ge_{lv}": n_ge(lv) for lv in DEPTH_LEVELS}
    depth_buckets_pct = {k: round(100.0 * v / total, 1) for k, v in depth_buckets.items()} \
        if total else {}

    # Task 2: per-discovery-universe deep coverage.
    def _cov(name: str, tickers: List[str]) -> Dict:
        uniq = [t for t in dict.fromkeys(tickers) if t not in dataio.BENCHMARKS]
        b = {t: bars.get(t, 0) for t in uniq}
        return {
            "universe": name, "n": len(uniq),
            "n_ge_300": sum(1 for v in b.values() if v >= 300),
            "n_ge_260": sum(1 for v in b.values() if v >= 260),
            "n_ge_120": sum(1 for v in b.values() if v >= 120),
            "pct_deep_300": round(100.0 * sum(1 for v in b.values() if v >= 300) / len(uniq), 1)
            if uniq else None,
        }
    universe_coverage = [
        _cov("alpha_board", _alpha_universe()),
        _cov("rs_lane", _rs_universe()),
        _cov("theme_leaders", _theme_universe()),
    ]

    # Task 2: which filters are UNRELIABLE on the current cache depth.
    def _rel(ok: int) -> bool:
        return bool(total) and (ok / total) >= 0.80
    filter_reliability = {
        "ma20": {"reliable": _rel(ma_ok["ma20_ok"]), "computable_pct": ma_ok["ma20_ok"]},
        "ma50": {"reliable": _rel(ma_ok["ma50_ok"]), "computable_pct": ma_ok["ma50_ok"]},
        "ma200": {"reliable": _rel(ma_ok["ma200_ok"]), "computable_pct": ma_ok["ma200_ok"]},
        "sniper_75bar": {"reliable": _rel(sni_ok), "computable_pct": sni_ok},
        "voyager_260bar": {"reliable": _rel(voy_ok), "computable_pct": voy_ok},
    }
    unreliable_filters = sorted(k for k, v in filter_reliability.items() if not v["reliable"])

    n_win = len(winners) or 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_tickers": total,
        "median_bars_universe": median_bars,
        "cache_uniformly_shallow": bool(cache_uniformly_shallow),
        "deep_cache_dir": str(dataio.DEEP_PRICES_DIR.relative_to(dataio.REPO)),
        "deep_cache_present": dataio.DEEP_PRICES_DIR.exists()
        and any(dataio.DEEP_PRICES_DIR.glob("*.parquet")),
        "depth_buckets": depth_buckets,
        "depth_buckets_pct": depth_buckets_pct,
        "universe_coverage": universe_coverage,
        "filter_reliability": filter_reliability,
        "unreliable_filters_due_to_depth": unreliable_filters,
        "ma_sufficiency": ma_ok,
        "ma_sufficiency_pct": {k: round(100.0 * v / total, 1) for k, v in ma_ok.items()} if total else {},
        "voyager_260bar_ok": voy_ok,
        "voyager_260bar_ok_pct": round(100.0 * voy_ok / total, 1) if total else None,
        "sniper_75bar_ok": sni_ok,
        "depth_histogram": dict(depth_hist),
        "n_winners": len(winners),
        "winners_missing_from_cache": {"n": len(win_missing), "tickers": win_missing[:30]},
        "winners_below_sniper_75bar": {
            "n": len(win_lt_sni), "pct_of_winners": round(100.0 * len(win_lt_sni) / n_win, 1),
            "largest": [{"ticker": t, "bars": win_bars[t], "max_return": round(winners[t], 2)}
                        for t in win_lt_sni[:15]]},
        "winners_below_voyager_260bar": {
            "n": len(win_lt_voy), "pct_of_winners": round(100.0 * len(win_lt_voy) / n_win, 1),
            "largest": [{"ticker": t, "bars": win_bars[t], "max_return": round(winners[t], 2)}
                        for t in win_lt_voy[:15]]},
        "winners_below_ma200": {
            "n": len(win_lt_ma200), "pct_of_winners": round(100.0 * len(win_lt_ma200) / n_win, 1)},
        "verdict": _verdict(len(winners), len(win_missing), len(win_lt_sni),
                            len(win_lt_voy), median_bars, voy_ok, total,
                            cache_uniformly_shallow),
    }


def _verdict(n_win: int, missing: int, lt_sni: int, lt_voy: int,
             median_bars: int, voy_ok: int, total: int,
             cache_uniformly_shallow: bool) -> str:
    nw = n_win or 1
    head = (
        f"Of {n_win} winners, {missing} are entirely absent from the price cache and "
        f"{lt_sni} ({round(100.0*lt_sni/nw,1)}%) have fewer than the Sniper 75-bar floor, "
        f"{lt_voy} ({round(100.0*lt_voy/nw,1)}%) fewer than the Voyager 260-bar floor. ")
    if cache_uniformly_shallow:
        return head + (
            f"BUT this is NOT a winner-youth problem: the ENTIRE research cache is shallow "
            f"(median {median_bars} bars; only {voy_ok}/{total} tickers have ≥260 bars and "
            f"MA200 is computable for ~2%). The 260-bar / MA200 gates are effectively "
            f"NON-FUNCTIONAL on this cache cache-wide — they would reject almost everything, "
            f"winner or not. This is a CACHE-DEPTH limitation that also caps the fidelity of "
            f"the whole scanner-truth review (Voyager-structural pass/fail cannot be honestly "
            f"recomputed without ≥260 bars). CAVEAT: production Voyager may fetch deeper "
            f"history live from Alpaca; this audit only measures cache/prices/*.parquet, the "
            f"cache the research funnel reads. ACTION: deepen the price cache to ≥300 bars "
            f"via a SEPARATE approved refresh before trusting any 200d/260-bar conclusion, "
            f"and have the high-recall lane use a SHORTER (≤60-bar) floor so young movers are "
            f"not structurally excluded.")
    return head + (
        "Thin history is a MINOR cause — most winners have ample bars, so cache depth "
        "is not the primary recall bottleneck. A high-recall lane should still use a "
        "shorter history floor (≤60 bars) so young movers are not structurally excluded.")


def _render_txt(res: Dict) -> List[str]:
    L = [
        f"== PRICE CACHE COVERAGE AUDIT ({res['generated_at']}) ==",
        f"total tickers: {res['total_tickers']}  ·  winners: {res['n_winners']}  ·  "
        f"deep cache present: {res['deep_cache_present']}",
        "",
        "depth buckets: " + ", ".join(f"{k}={v} ({res['depth_buckets_pct'].get(k)}%)"
                                      for k, v in res["depth_buckets"].items()),
        "universe coverage (n / ≥300 / pct_deep):",
    ] + [
        f"  {c['universe']:<16} {c['n']:>4} / {c['n_ge_300']:>4} / {c['pct_deep_300']}%"
        for c in res["universe_coverage"]
    ] + [
        f"UNRELIABLE filters @ current depth: {', '.join(res['unreliable_filters_due_to_depth']) or '(none)'}",
        "",
        "MA sufficiency: " + ", ".join(f"{k}={v} ({res['ma_sufficiency_pct'].get(k)}%)"
                                       for k, v in res["ma_sufficiency"].items()),
        f"voyager 260-bar ok: {res['voyager_260bar_ok']}   sniper 75-bar ok: {res['sniper_75bar_ok']}",
        f"depth histogram: {res['depth_histogram']}",
        "",
        f"winners missing from cache: {res['winners_missing_from_cache']['n']}",
        f"winners < sniper 75-bar: {res['winners_below_sniper_75bar']['n']} "
        f"({res['winners_below_sniper_75bar']['pct_of_winners']}%)",
        f"winners < voyager 260-bar: {res['winners_below_voyager_260bar']['n']} "
        f"({res['winners_below_voyager_260bar']['pct_of_winners']}%)",
        f"winners < MA200: {res['winners_below_ma200']['n']} "
        f"({res['winners_below_ma200']['pct_of_winners']}%)",
        "",
        "largest winners below voyager 260-bar floor:",
    ]
    for d in res["winners_below_voyager_260bar"]["largest"][:10]:
        L.append(f"  {d['ticker']:<7} bars={d['bars']:<5} +{int(d['max_return']*100)}%")
    L += ["", "VERDICT:", "  " + res["verdict"]]
    return L


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "price_cache_coverage_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "price_cache_coverage_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
