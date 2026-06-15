"""
research/scanner_truth/funnel_trace.py — TASK 2 (funnel trace) + TASK 3 (root cause).

For each winner, trace whether/where it appeared in the funnel. Honest hybrid:

  • Price-derived stages (universe liquidity, Voyager structure, Sniper breakout,
    Alpha market-cap band) are RE-COMPUTED point-in-time from bars at-or-before
    the as-of date — pure, no look-ahead.
  • DB-logged stages (scan_results, veto_log, paper_signals, decisions) are read
    from historized rows.
  • Alpha board / Stock Lens / Gatekeeper / options / entry-validator / actionable
    are NOT historized — only a single latest snapshot exists. Those stages are
    marked status=NOT_RETAINED for history and only the TODAY snapshot is checked.
    (This gap is itself a primary finding; see the historizer + recommendations.)

Root cause is a transparent priority cascade documented in ``classify_root_cause``.
Where historization is absent the cause is marked ``*_INFERRED`` so no certainty
is over-claimed.

Outputs:
  cache/research/scanner_funnel_trace_latest.json
  logs/scanner_funnel_trace_latest.txt
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

from . import dataio
from .filters import (
    ALPHA_MCAP_CEILING, ALPHA_MCAP_FLOOR, VOY_MAX_EXTENSION_MA50,
    alpha_market_cap_eligible, liquidity_gate, sma, sniper_breakout,
    voyager_structural,
)

# Stage status vocabulary.
PASS, FAIL, MISSING, STALE, NOT_EVAL, NOT_RETAINED = (
    "pass", "fail", "missing", "stale", "not_evaluated", "not_retained")

ROOT_CAUSES = [
    "UNIVERSE_MISS", "DATA_MISS", "FILTER_TOO_STRICT", "THEME_BLIND",
    "RANKING_MISS", "TOP_N_CAP_MISS", "STALE_LENS_OR_GATEKEEPER",
    "LATE_CHASE_ONLY", "ENTRY_VALIDATOR_TOO_STRICT", "GOVERNANCE_OR_SIZE_BLOCK",
    "VALID_NO_TRADE",
]


def _move_start_date(close: pd.Series, calendar: pd.DatetimeIndex,
                     windows: List[int]) -> Optional[pd.Timestamp]:
    """Earliest window-start among windows where the name is a +50% winner —
    the earliest date the system had a chance to catch the move."""
    best = None
    for w in sorted(windows, reverse=True):  # longest window first = earliest start
        if len(close) <= w:
            continue
        seg = close.iloc[-(w + 1):]
        if pd.isna(seg.iloc[0]) or seg.iloc[0] <= 0:
            continue
        if float(seg.max() / seg.iloc[0] - 1.0) >= 0.50:
            best = calendar[-(w + 1)]
    return best


def _first_date_crossing(close: pd.Series, start: pd.Timestamp, level: float) -> Optional[str]:
    """First date at-or-after ``start`` where return-from-start ≥ level."""
    seg = close[close.index >= start]
    if seg.empty or pd.isna(seg.iloc[0]) or seg.iloc[0] <= 0:
        return None
    rel = seg / seg.iloc[0] - 1.0
    hit = rel[rel >= level]
    return str(hit.index[0])[:10] if len(hit) else None


def _first_too_extended(df: pd.DataFrame, calendar: pd.DatetimeIndex) -> Optional[str]:
    """First calendar date the close is >12% above its MA50 (Voyager's
    too_extended onset) — when Voyager would start rejecting it."""
    c = df["close"]
    ma50 = sma(c, 50)
    ext = (c - ma50) / ma50
    over = ext[ext > VOY_MAX_EXTENSION_MA50]
    return str(over.index[0])[:10] if len(over) else None


def _first_liquidity_pass(df: pd.DataFrame, calendar: pd.DatetimeIndex) -> Optional[str]:
    """First calendar date the base liquidity gate passes (vectorised)."""
    c = df["close"].reindex(calendar).ffill()
    vol = df["volume"].reindex(calendar).ffill()
    dvol = (c * vol)
    avg_vol = vol.rolling(20, min_periods=20).mean()
    avg_dvol = dvol.rolling(20, min_periods=20).mean()
    from .filters import (UNIV_MAX_PRICE, UNIV_MIN_AVG_DVOL, UNIV_MIN_AVG_VOL,
                          UNIV_MIN_PRICE)
    ok = ((c >= UNIV_MIN_PRICE) & (c <= UNIV_MAX_PRICE)
          & (avg_vol >= UNIV_MIN_AVG_VOL) & (avg_dvol >= UNIV_MIN_AVG_DVOL))
    hit = ok[ok.fillna(False)]
    return str(hit.index[0])[:10] if len(hit) else None


class DBPresence:
    """Earliest-row-per-ticker presence across the historized funnel tables.
    Loaded once for the whole winner set (read-only)."""

    def __init__(self) -> None:
        self.veto: Dict[str, Dict] = {}
        self.scan: Dict[str, Dict] = {}
        self.paper: Dict[str, Dict] = {}
        self.decisions: Dict[str, Dict] = {}
        with dataio._ro_conn() as con:
            for t, ts, verdict, agent, reason in con.execute(
                "SELECT ticker, MIN(ts), verdict, agent, reason FROM veto_log GROUP BY ticker"
            ):
                self.veto[t.upper()] = {"first_ts": ts, "verdict": verdict,
                                        "agent": agent, "reason": reason}
            # veto verdict distribution per ticker (any APPROVED?)
            self._veto_any_approved = {
                t.upper() for (t,) in con.execute(
                    "SELECT DISTINCT ticker FROM veto_log WHERE verdict='APPROVED'")
            }
            for t, ts, status in con.execute(
                "SELECT ticker, MIN(ts), status FROM scan_results GROUP BY ticker"
            ):
                self.scan[t.upper()] = {"first_ts": ts, "status": status}
            for t, ts, status in con.execute(
                "SELECT ticker, MIN(logged_at), status FROM paper_signals GROUP BY ticker"
            ):
                self.paper[t.upper()] = {"first_ts": ts, "status": status}
            # voyager paper table (separate)
            try:
                for t, ts in con.execute(
                    "SELECT ticker, MIN(logged_at) FROM voyager_paper_signals GROUP BY ticker"
                ):
                    self.paper.setdefault(t.upper(), {"first_ts": ts, "status": "voyager"})
            except Exception:
                pass
            for t, ts, opened in con.execute(
                "SELECT ticker, MIN(ts), MAX(position_opened) FROM decisions GROUP BY ticker"
            ):
                self.decisions[t.upper()] = {"first_ts": ts, "ever_opened": bool(opened)}

    def first_seen(self, ticker: str) -> Optional[str]:
        cands = [d["first_ts"] for d in (
            self.veto.get(ticker), self.scan.get(ticker),
            self.paper.get(ticker), self.decisions.get(ticker)) if d and d.get("first_ts")]
        return min(cands) if cands else None


class TodaySnapshot:
    """The single retained snapshot of the non-historized stages."""

    def __init__(self) -> None:
        self.alpha_board: set = set()
        self.alpha_built_at: Optional[str] = None
        try:
            board = json.loads((dataio.RESEARCH_CACHE / "alpha_discovery_board_latest.json").read_text())
            self.alpha_built_at = board.get("built_at")
            for item in board.get("items", []):
                sym = (item.get("symbol") or item.get("ticker") or "").upper()
                if sym:
                    self.alpha_board.add(sym)
        except Exception:
            pass

    def has_lens(self, ticker: str) -> bool:
        return (dataio.RESEARCH_CACHE / f"stock_lens_{ticker.upper()}_latest.json").exists()

    def has_gatekeeper(self, ticker: str) -> bool:
        return (dataio.RESEARCH_CACHE / f"executive_gatekeeper_{ticker.upper()}_latest.json").exists()


def classify_root_cause(trace: Dict) -> Tuple[str, str]:
    """Transparent priority cascade → (primary_cause, evidence).

    Fidelity rules:
      • Voyager's 260-bar gates are only used when ``voyager_reconstruction ==
        'reliable'`` (cache has ≥260 bars). When cache-limited we do NOT treat
        Voyager's insufficient-history as a scanner rejection — that would
        attribute our price-cache depth to the live scanner, which pulls full
        history from Alpha. Such cases route to the DB-fact cascade instead.
      • Sniper (75-bar) and liquidity (20-bar) gates are reliable for the
        ~110-bar winner cache and DO drive classification.
      • ``not seen`` rests on historized DB tables (robust); _INFERRED is
        appended where the verdict leans on the non-historized snapshot."""
    g = trace["price_gates"]
    db = trace["db_presence"]
    snap = trace["today_snapshot"]
    seen = trace["first_date_actually_saw"] is not None or snap["in_alpha_board"]
    voy_reliable = trace["voyager_reconstruction"] == "reliable"

    # System acted (opened a position) → valid engagement, regardless of else.
    if db.get("ever_opened"):
        return "VALID_NO_TRADE", "system opened a position on this name (engaged)"

    # Governance: council saw it and only ever VETOED it.
    v = db.get("veto")
    if v and v["verdict"] == "VETOED" and not db.get("ever_approved"):
        agent = (v.get("agent") or "").lower()
        if "score" in agent:
            return "RANKING_MISS", f"council vetoed on score: {v.get('reason')}"
        return "GOVERNANCE_OR_SIZE_BLOCK", f"council vetoed by {v.get('agent')}: {v.get('reason')}"

    # Late chase: first seen only after too-extended onset.
    fext = trace["first_date_too_extended"]
    fseen = trace["first_date_actually_saw"]
    if seen and fext and fseen and fseen[:10] > fext:
        return "LATE_CHASE_ONLY", f"first seen {fseen[:10]} > too-extended onset {fext}"

    # Genuine data gap: no profile/market-cap AND never seen.
    if trace["market_cap"] in (None, 0) and not seen:
        return "DATA_MISS", "no FMP profile / market-cap cached; never seen in funnel"

    # Alpha market-cap band exclusion (reliably computable from profile).
    mc = g["alpha_mcap"]["reasons"]
    if mc and not seen:
        suffix = "_INFERRED"  # other stages might still have surfaced it; not historized
        return "FILTER_TOO_STRICT" + suffix, f"alpha market-cap band excludes it ({','.join(mc)})"

    # Voyager structural rejection — ONLY when reconstruction is reliable.
    vr = g["voyager"]["reasons"]
    sr = g["sniper"]["reasons"]
    if voy_reliable and ("too_extended" in vr or "below_ma200_floor" in vr):
        return "FILTER_TOO_STRICT", f"voyager (reliable recon) rejected: {','.join(vr)}"

    # The robust DB fact for the bulk: never surfaced to any historized stage,
    # despite passing the reliable liquidity gate at some point. We cannot fully
    # decompose WHY (no universe-snapshot history; Voyager replay cache-limited),
    # so the honest label is UNIVERSE_MISS with that caveat in evidence.
    if not seen and g["liquidity"]["passed"]:
        recon = "voyager-recon cache-limited" if not voy_reliable else "voyager-recon reliable"
        sniper_note = ("sniper breakout WAS available at move-start (missed setup)"
                       if not sr else f"sniper no-setup ({','.join(sr)})")
        return "UNIVERSE_MISS", (
            f"passed liquidity, never in scan/veto/paper/decisions; {recon}; {sniper_note}")

    if snap["in_alpha_board"] and not snap["has_gatekeeper"]:
        return "STALE_LENS_OR_GATEKEEPER", "on today's Alpha board but no gatekeeper artifact"

    if not seen:
        return "UNIVERSE_MISS", "never observed in any historized funnel stage"
    return "VALID_NO_TRADE", "seen by funnel; no disqualifying evidence isolated"


def trace_winner(rec: Dict, calendar, db: DBPresence, snap: TodaySnapshot,
                 windows: List[int]) -> Dict:
    ticker = rec["ticker"]
    df = dataio.load_prices(ticker)
    close = df["close"].reindex(calendar).ffill()
    move_start = _move_start_date(close, calendar, windows)
    asof = move_start if move_start is not None else calendar[-1]

    from .filters import VOY_BARS_NEEDED
    cache_bars = int(len(df))
    # Voyager replay is only faithful when our cache holds ≥ its 260-bar
    # lookback. Below that, the gate's insufficient-history reflects OUR cache
    # depth, not the live scanner — flagged so the classifier won't blame the
    # scanner for a data-cache limitation.
    voyager_reconstruction = "reliable" if cache_bars >= VOY_BARS_NEEDED else "cache_limited"

    liq = liquidity_gate(df, asof)
    voy = voyager_structural(df, asof)
    sni = sniper_breakout(df, asof)
    amc = alpha_market_cap_eligible(rec.get("market_cap"))

    v = db.veto.get(ticker)
    trace = {
        "ticker": ticker,
        "theme": rec["theme"],
        "sector": rec["sector"],
        "market_cap": rec.get("market_cap"),
        "best_max_return": rec["best_max_return"],
        "cache_bars": cache_bars,
        "voyager_reconstruction": voyager_reconstruction,
        "move_start_date": str(move_start)[:10] if move_start is not None else None,
        "first_date_could_see": _first_liquidity_pass(df, calendar),
        "first_date_actually_saw": db.first_seen(ticker),
        "first_date_+20pct": _first_date_crossing(close, asof, 0.20) if move_start is not None else None,
        "first_date_+50pct": _first_date_crossing(close, asof, 0.50) if move_start is not None else None,
        "first_date_too_extended": _first_too_extended(df, calendar),
        "price_gates": {
            "liquidity": liq.as_dict(),
            "voyager": voy.as_dict(),
            "sniper": sni.as_dict(),
            "alpha_mcap": amc.as_dict(),
        },
        "db_presence": {
            "in_scan_results": ticker in db.scan,
            "in_veto_log": ticker in db.veto,
            "veto": v,
            "ever_approved": ticker in db._veto_any_approved,
            "in_paper_signals": ticker in db.paper,
            "in_decisions": ticker in db.decisions,
            "ever_opened": db.decisions.get(ticker, {}).get("ever_opened", False),
        },
        "today_snapshot": {
            "in_alpha_board": ticker in snap.alpha_board,
            "has_lens": snap.has_lens(ticker),
            "has_gatekeeper": snap.has_gatekeeper(ticker),
            "alpha_built_at": snap.alpha_built_at,
        },
        "non_historized_stages": {
            s: NOT_RETAINED for s in (
                "alpha_candidate_pool", "alpha_top_board", "stock_lens",
                "gatekeeper", "options_enrichment", "entry_validator", "actionable_now")
        },
    }
    # early / late / blind verdict
    fseen = trace["first_date_actually_saw"]
    f50 = trace["first_date_+50pct"]
    if fseen is None and not trace["today_snapshot"]["in_alpha_board"]:
        timing = "blind"
    elif f50 and fseen and fseen[:10] <= f50:
        timing = "early"
    else:
        timing = "late"
    trace["detection_timing"] = timing

    cause, evidence = classify_root_cause(trace)
    trace["root_cause"] = cause
    trace["root_cause_evidence"] = evidence
    return trace


def build(*, limit: Optional[int] = None, min_return: float = 0.80) -> Dict:
    """Trace winners with best_max_return ≥ ``min_return`` (default +80% — the
    strongest, least-ambiguous movers). ``limit`` caps the count for speed."""
    from .winner_universe import WINDOWS
    uni_path = dataio.RESEARCH_CACHE / "missed_winner_universe_latest.json"
    uni = json.loads(uni_path.read_text())
    winners = [w for w in uni["winners"] if w["best_max_return"] >= min_return]
    if limit:
        winners = winners[:limit]
    calendar = dataio.benchmark_calendar()
    db = DBPresence()
    snap = TodaySnapshot()

    traces = [trace_winner(w, calendar, db, snap, WINDOWS) for w in winners]

    by_cause: Dict[str, int] = {}
    by_timing: Dict[str, int] = {"early": 0, "late": 0, "blind": 0}
    by_theme_cause: Dict[str, Dict[str, int]] = {}
    delays = []
    n_voy_cache_limited = sum(1 for t in traces if t["voyager_reconstruction"] == "cache_limited")
    n_sniper_missed = sum(1 for t in traces
                          if not t["price_gates"]["sniper"]["reasons"]
                          and not (t["first_date_actually_saw"] or t["today_snapshot"]["in_alpha_board"]))
    n_ever_in_funnel = sum(1 for t in traces
                           if t["first_date_actually_saw"] or t["today_snapshot"]["in_alpha_board"])
    for t in traces:
        c = t["root_cause"].replace("_INFERRED", "")
        by_cause[c] = by_cause.get(c, 0) + 1
        by_timing[t["detection_timing"]] += 1
        by_theme_cause.setdefault(t["theme"], {}).setdefault(c, 0)
        by_theme_cause[t["theme"]][c] += 1
        # delay from could-see to actually-saw (calendar days)
        cs, ss = t["first_date_could_see"], t["first_date_actually_saw"]
        if cs and ss:
            try:
                d = (datetime.fromisoformat(ss[:10]) - datetime.fromisoformat(cs)).days
                if d >= 0:
                    delays.append(d)
            except Exception:
                pass

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_return_traced": min_return,
        "n_traced": len(traces),
        "alpha_built_at": snap.alpha_built_at,
        "summary": {
            "by_root_cause": dict(sorted(by_cause.items(), key=lambda x: -x[1])),
            "by_detection_timing": by_timing,
            "by_theme_cause": by_theme_cause,
            "avg_detection_delay_days": round(sum(delays) / len(delays), 1) if delays else None,
            "n_with_delay_sample": len(delays),
            "n_ever_in_funnel": n_ever_in_funnel,
            "winner_recall_pct": round(100.0 * n_ever_in_funnel / len(traces), 1) if traces else None,
            "fidelity": {
                "n_voyager_recon_cache_limited": n_voy_cache_limited,
                "voyager_recon_note": (
                    "Voyager 260-bar gates indeterminate for these; cache holds "
                    "~110 bars (2025-12-15 onset). Not attributed to the scanner."),
                "n_sniper_missed_available_setup": n_sniper_missed,
                "sniper_note": (
                    "winners with a reliably-computable Sniper breakout setup at "
                    "move-start that never appeared in any funnel log"),
            },
        },
        "traces": traces,
    }


def _render_txt(res: Dict) -> List[str]:
    s = res["summary"]
    L = [
        f"== SCANNER FUNNEL TRACE ({res['generated_at']}) ==",
        f"traced {res['n_traced']} winners (best_max_return ≥ +{int(res['min_return_traced']*100)}%)",
        f"alpha snapshot built_at: {res['alpha_built_at']}  (Alpha/lens/gatekeeper history = NOT_RETAINED)",
        "",
        f"WINNER RECALL: {s['n_ever_in_funnel']}/{res['n_traced']} ever appeared in any "
        f"historized funnel stage  →  {s['winner_recall_pct']}%",
        "",
        "FIDELITY CAVEATS:",
        f"  voyager-recon cache-limited (<260 bars): {s['fidelity']['n_voyager_recon_cache_limited']}"
        f"/{res['n_traced']}  — not blamed on scanner",
        f"  sniper breakout setup available but never logged: "
        f"{s['fidelity']['n_sniper_missed_available_setup']}",
        "",
        "ROOT CAUSE (primary):",
    ]
    for c, n in s["by_root_cause"].items():
        L.append(f"  {c:<28} {n}")
    L += [
        "",
        f"DETECTION TIMING: early={s['by_detection_timing']['early']} "
        f"late={s['by_detection_timing']['late']} blind={s['by_detection_timing']['blind']}",
        f"avg detection delay (could-see → saw): "
        f"{s['avg_detection_delay_days']} days (n={s['n_with_delay_sample']})",
        "",
        "TOP 20 TRACED WINNERS:",
        f"{'ticker':<7}{'maxret':>7} {'timing':<6} {'rootcause':<26} seen?",
    ]
    for t in res["traces"][:20]:
        seen = "Y" if t["first_date_actually_saw"] or t["today_snapshot"]["in_alpha_board"] else "—"
        L.append(f"{t['ticker']:<7}{t['best_max_return']*100:>6.0f}% "
                 f"{t['detection_timing']:<6} {t['root_cause']:<26} {seen}")
    return L


def main() -> int:
    res = build()
    dataio.write_json(dataio.RESEARCH_CACHE / "scanner_funnel_trace_latest.json", res)
    lines = _render_txt(res)
    dataio.write_text(dataio.LOGS_DIR / "scanner_funnel_trace_latest.txt", lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
