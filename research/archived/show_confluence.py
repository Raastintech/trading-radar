"""
Show confluence opportunities.

Default behavior is signal-aware:
- Voyager approved opportunities (long + short)
- Sniper approved breakouts
- Remora approved catalysts
"""

import argparse
import json
import logging
import sys
from typing import Dict, List, Set
sys.path.append('/Users/hraastin/Desktop/SniperTradingAI')

from alpaca_data import AlpacaDataFeed
from alpaca_client_factory import build_trading_client
from universe_snapshot_builder import UniverseSnapshotBuilder
from voyager_adaptive_universe import VoyagerAdaptiveUniverse
from sniper_adaptive_universe import SniperAdaptiveUniverse
from remora_adaptive_universe import RemoraAdaptiveUniverse
from voyager_production_v2_complete import VoyagerProductionV2Complete
from sniper_scanner_v2 import SniperScannerV2
from remora_scanner_v2 import RemoraScanner
from short_scanner_v1 import ShortScanner
from contrarian_scanner import ContrarianScanner
from triple_confluence_detector import TripleConfluenceDetector
from decision_logger import DecisionLogger
from datetime import datetime
import yfinance as yf

logger = logging.getLogger(__name__)


RESEARCH_SCORE_THRESHOLDS = {
    "VOYAGER": 75.0,
    "SNIPER": 60.0,
    "REMORA": 70.0,
}

RESEARCH_SCORE_FLOORS = {
    "VOYAGER": 45.0,
    "SNIPER": 45.0,
    "REMORA": 50.0,
}

RESEARCH_RR_THRESHOLDS = {
    "SNIPER": 2.5,
}

RESEARCH_ALLOWED_REASONS = {
    "VOYAGER": {"no_voyager_pathway_qualified", "score_below_threshold", "risk_validation_failed"},
    "SNIPER": {"risk_reward_too_low", "score_below_threshold", "position_sizing_failed"},
    "REMORA": {"score_below_threshold", "position_sizing_failed"},
}

HIGH_QUALITY_MAX_GATES = 1
HIGH_QUALITY_MAX_SCORE_SHORTFALL = 8.0
HIGH_QUALITY_MAX_RR_SHORTFALL = 0.30


def _extract_tickers(candidates):
    tickers = set()
    for c in candidates:
        if isinstance(c, dict):
            ticker = c.get('ticker', '')
        else:
            ticker = c
        if ticker:
            tickers.add(str(ticker).upper().strip())
    return {t for t in tickers if t}


def _build_strategy_universes(data_feed: AlpacaDataFeed) -> Dict[str, object]:
    """Build one shared universe snapshot for research/runtime tooling."""
    snapshot_builder = UniverseSnapshotBuilder(
        data_feed,
        trading_client=build_trading_client(logger=logger),
    )
    snapshot = snapshot_builder.build_snapshot()
    base_universe = set(snapshot.get("base_universe", []))

    if base_universe:
        summary = snapshot.get("summary", {})
        print("🔍 Shared dynamic universe initialized")
        print(f"   Source assets: {summary.get('source_assets', 0)}")
        print(f"   Base universe: {len(base_universe)} stocks")
        print(f"   Voyager routed: {summary.get('voyager_universe_size', 0)}")
        print(f"   Sniper routed:  {summary.get('sniper_universe_size', 0)}")
        print(f"   Remora routed:  {summary.get('remora_universe_size', 0)}")
        print(f"   Short routed:   {summary.get('short_universe_size', 0)}")
        print(f"   Reaper routed:  {summary.get('contrarian_universe_size', 0)}")
        return {
            "source": "shared_dynamic_snapshot",
            "snapshot": snapshot,
            "base": base_universe,
            "voyager": set(snapshot.get("voyager_universe", [])),
            "sniper": set(snapshot.get("sniper_universe", [])),
            "remora": set(snapshot.get("remora_universe", [])),
            "short": set(snapshot.get("short_universe", [])),
            "contrarian": set(snapshot.get("contrarian_universe", [])),
        }

    fallback_reason = snapshot.get("fallback_reason") or "dynamic_snapshot_unavailable"
    print("⚠️  Shared dynamic universe unavailable - falling back to legacy builders")
    print(f"   Reason: {fallback_reason}")

    voyager_builder = VoyagerAdaptiveUniverse(data_feed)
    voyager_result = voyager_builder.build_universe(scan_type='quick')

    sniper_builder = SniperAdaptiveUniverse(data_feed)
    sniper_result = sniper_builder.build_universe()

    remora_builder = RemoraAdaptiveUniverse(data_feed)
    remora_result = remora_builder.build_universe()

    voyager_long = voyager_result.get('long_candidates', [])
    voyager_short = voyager_result.get('short_candidates', [])
    voyager_tickers = _extract_tickers(voyager_long) | _extract_tickers(voyager_short)
    sniper_tickers = set(sniper_result.get('tickers', []))
    remora_tickers = set(remora_result.get('tickers', []))
    short_tickers = set(voyager_tickers | sniper_tickers)
    contrarian_tickers = set(voyager_tickers | sniper_tickers)

    return {
        "source": "legacy_fallback",
        "snapshot": snapshot,
        "base": set(voyager_tickers | sniper_tickers | remora_tickers),
        "voyager": voyager_tickers,
        "sniper": sniper_tickers,
        "remora": remora_tickers,
        "short": short_tickers,
        "contrarian": contrarian_tickers,
    }


def _get_current_vix() -> float:
    """Best-effort VIX snapshot for Reaper gate in standalone script."""
    try:
        hist = yf.Ticker("^VIX").history(period="5d", interval="1d")
        if hist is not None and not hist.empty:
            close = hist["Close"].dropna()
            if not close.empty:
                return float(close.iloc[-1])
    except Exception:
        pass
    return 0.0


def _regime_context_from_vix(vix_level: float) -> dict:
    """Lightweight regime context for run/decision logging."""
    try:
        vix = float(vix_level or 0.0)
    except Exception:
        vix = 0.0
    if vix >= 30:
        status = "BEAR"
    elif vix >= 25:
        status = "SIDEWAYS"
    elif vix > 0:
        status = "BULL"
    else:
        status = "UNKNOWN"
    volatility = "HIGH" if vix >= 25 else ("NORMAL" if vix > 0 else "UNKNOWN")
    return {"status": status, "volatility": volatility, "vix_level": vix}


def _market_session_now() -> str:
    """Simple market-session tag for run logging."""
    now = datetime.now()
    if now.weekday() >= 5:
        return "CLOSED"
    mins = now.hour * 60 + now.minute
    if 4 * 60 <= mins < 9 * 60 + 30:
        return "PRE"
    if 9 * 60 + 30 <= mins < 16 * 60:
        return "REGULAR"
    if 16 * 60 <= mins < 20 * 60:
        return "POST"
    return "CLOSED"


def _normalize_reason(reason: object) -> str:
    raw = str(reason or "unknown").strip().lower()
    out = []
    prev_sep = False
    for ch in raw:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            out.append(ch)
            prev_sep = False
        else:
            if not prev_sep:
                out.append("_")
                prev_sep = True
    normalized = "".join(out).strip("_")
    return normalized or "unknown"


def _safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _extract_voyager_long_rejects(voyager_engine) -> list:
    """
    Voyager long pipeline keeps candidate audit rows in _last_long_candidates.
    Convert rejected rows to scanner-reject schema for decisions logging.
    """
    rows = getattr(voyager_engine, "_last_long_candidates", None) or []
    rejects = []
    for c in rows:
        if not isinstance(c, dict):
            continue
        if str(c.get("status", "")).strip().lower() != "rejected":
            continue
        ticker = str(c.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        metrics = c.get("metrics") if isinstance(c.get("metrics"), dict) else {}
        score_raw = c.get("score")
        try:
            score_val = float(score_raw)
        except Exception:
            score_val = 0.0
        rejects.append({
            "ticker": ticker,
            "reason": _normalize_reason(c.get("rejection_reason") or c.get("reason") or "unknown"),
            "score": score_val,
            "grade": c.get("grade", "N/A"),
            "rr": c.get("risk_reward") or metrics.get("risk_reward"),
            "entry": metrics.get("entry_price"),
            "stop": c.get("stop_loss"),
            "target": c.get("target_price"),
            "primary_pathway": (c.get("score_result") or {}).get("pathway_qualification", {}).get("primary_pathway")
            if isinstance(c.get("score_result"), dict) else None,
            "pathways_failed": (c.get("score_result") or {}).get("pathway_qualification", {}).get("pathways_failed", [])
            if isinstance(c.get("score_result"), dict) else [],
        })
    return rejects


def _log_scanner_rejects(
    decision_logger: DecisionLogger,
    run_id: str,
    rejects: list,
    strategy: str,
    direction: str,
    market_session: str,
    regime_context: dict = None,
) -> int:
    """Persist scanner reject rows into decisions table."""
    if not run_id or not rejects:
        return 0
    logged = 0
    for r in rejects:
        ticker = str(r.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        reason = _normalize_reason(r.get("reason"))
        try:
            decision_logger.log_decision(
                run_id=run_id,
                ticker=ticker,
                council_decision={
                    "decision": "SCANNER_REJECT",
                    "reason": reason,
                    "strategy": strategy,
                    "direction": direction,
                    "shares": 0,
                    "avg_score": r.get("score", 0.0),
                    "approve_count": 0,
                    "caution_count": 0,
                    "veto_count": 1,
                    "veto_reasons": [reason],
                    "raw_votes": {},
                },
                signal={
                    "strategy": strategy,
                    "direction": direction,
                    "shares": 0,
                    "entry_price": r.get("entry"),
                    "stop_loss": r.get("stop"),
                    "target_price": r.get("target"),
                    "risk_reward": r.get("rr") or None,
                },
                market_session=market_session,
                regime=regime_context or {},
                execution_denied=1,
                execution_deny_reason=reason,
                options_pcr=r.get("options_pcr"),
                options_gamma=r.get("options_gamma"),
                notes=json.dumps({
                    "type": "scanner_reject",
                    "source": "show_confluence",
                    "score": r.get("score", 0.0),
                    "grade": r.get("grade", "N/A"),
                    "primary_pathway": r.get("primary_pathway"),
                    "pathways_failed": r.get("pathways_failed", []),
                    "rr": r.get("rr"),
                    "gates_failed": r.get("gates_failed", []),
                }, default=str),
            )
            logged += 1
        except Exception:
            pass
    return logged


def _build_near_miss_signals(
    strategy: str,
    rejects: list,
    direction: str,
    score_gap: float,
    rr_relax: float,
    max_per_strategy: int,
    high_quality_only: bool = False,
) -> List[Dict]:
    """
    Convert scanner rejects into research-only near-miss pseudo-signals.
    """
    strategy_u = str(strategy or "").upper()
    allowed_reasons = RESEARCH_ALLOWED_REASONS.get(strategy_u, set())
    if not rejects or not allowed_reasons:
        return []

    score_threshold = RESEARCH_SCORE_THRESHOLDS.get(strategy_u, 70.0)
    score_floor = RESEARCH_SCORE_FLOORS.get(strategy_u, 45.0)
    rr_threshold = RESEARCH_RR_THRESHOLDS.get(strategy_u)
    cap = max(1, int(max_per_strategy or 25))

    by_ticker: Dict[str, Dict] = {}
    for row in rejects:
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        reason = _normalize_reason(row.get("reason"))
        if reason not in allowed_reasons:
            continue
        gates_raw = row.get("gates_failed", [])
        gates = gates_raw if isinstance(gates_raw, list) else []
        gate_count = len({str(g).strip().lower() for g in gates if str(g).strip()})
        if gate_count > 2:
            continue

        score = _safe_float(row.get("score"))
        rr = _safe_float(row.get("rr"))
        score_close = score is not None and score >= (score_threshold - score_gap)
        rr_close = rr_threshold is not None and rr is not None and rr >= (rr_threshold - rr_relax)

        # Keep only meaningful close misses; skip broad discovery fails.
        if not score_close and not rr_close:
            continue
        if score is not None and score < score_floor and not rr_close:
            continue

        score_shortfall = (
            max(0.0, score_threshold - score) if score is not None else score_threshold
        )
        rr_shortfall = (
            max(0.0, rr_threshold - rr)
            if rr_threshold is not None and rr is not None else 0.0
        )
        distance = float(score_shortfall + rr_shortfall)

        if high_quality_only:
            if gate_count > HIGH_QUALITY_MAX_GATES:
                continue
            if score_shortfall > HIGH_QUALITY_MAX_SCORE_SHORTFALL:
                continue
            if rr_shortfall > HIGH_QUALITY_MAX_RR_SHORTFALL:
                continue

        candidate = {
            "ticker": ticker,
            "strategy": strategy_u,
            "direction": direction,
            "score": float(score or 0.0),
            "risk_reward": rr,
            "near_miss": True,
            "near_miss_reason": reason,
            "near_miss_score_shortfall": round(score_shortfall, 2),
            "near_miss_rr_shortfall": round(rr_shortfall, 2),
            "near_miss_gate_count": gate_count,
            "_distance": distance,
        }

        prev = by_ticker.get(ticker)
        if prev is None:
            by_ticker[ticker] = candidate
            continue
        prev_rank = (prev["_distance"], -float(prev.get("score", 0.0)))
        cand_rank = (candidate["_distance"], -float(candidate.get("score", 0.0)))
        if cand_rank < prev_rank:
            by_ticker[ticker] = candidate

    ranked = sorted(
        by_ticker.values(),
        key=lambda x: (
            int(x.get("near_miss_gate_count", 99)),
            x["_distance"],
            -float(x.get("score", 0.0)),
            x["ticker"],
        ),
    )
    for item in ranked:
        item.pop("_distance", None)
    return ranked[:cap]


def _build_research_near_miss_map(
    voyager_rejects: list,
    sniper_rejects: list,
    remora_rejects: list,
    score_gap: float,
    rr_relax: float,
    max_per_strategy: int,
    high_quality_only: bool = False,
) -> Dict[str, List[Dict]]:
    return {
        "VOYAGER": _build_near_miss_signals(
            strategy="VOYAGER",
            rejects=voyager_rejects,
            direction="LONG",
            score_gap=score_gap,
            rr_relax=rr_relax,
            max_per_strategy=max_per_strategy,
            high_quality_only=high_quality_only,
        ),
        "SNIPER": _build_near_miss_signals(
            strategy="SNIPER",
            rejects=sniper_rejects,
            direction="LONG",
            score_gap=score_gap,
            rr_relax=rr_relax,
            max_per_strategy=max_per_strategy,
            high_quality_only=high_quality_only,
        ),
        "REMORA": _build_near_miss_signals(
            strategy="REMORA",
            rejects=remora_rejects,
            direction="LONG",
            score_gap=score_gap,
            rr_relax=rr_relax,
            max_per_strategy=max_per_strategy,
            high_quality_only=high_quality_only,
        ),
    }


def _print_research_near_misses(near_misses: Dict[str, List[Dict]], max_rows: int = 8):
    print("\n📚 RESEARCH MODE — NEAR-MISS CANDIDATES")
    print("-" * 72)
    for strategy in ("VOYAGER", "SNIPER", "REMORA"):
        items = near_misses.get(strategy, []) or []
        print(f"{strategy:8s}: {len(items)} candidates")
        for row in items[:max_rows]:
            rr_val = row.get("risk_reward")
            rr_txt = f"{rr_val:.2f}" if rr_val is not None else "—"
            print(
                f"   {row['ticker']:6s} score={row.get('score', 0):5.1f} "
                f"rr={rr_txt:>5s} "
                f"reason={row.get('near_miss_reason')} "
                f"gates={int(row.get('near_miss_gate_count', 0))} "
                f"Δscore={row.get('near_miss_score_shortfall', 0):.1f} "
                f"Δrr={row.get('near_miss_rr_shortfall', 0):.2f}"
            )


def show_confluence(
    use_legacy_overlap: bool = False,
    research_mode: bool = False,
    research_score_gap: float = 12.0,
    research_rr_relax: float = 0.5,
    research_max_per_strategy: int = 25,
    research_high_quality: bool = False,
):
    """Confluence check."""

    print("\n" + "="*80)
    print("💎 CONFLUENCE CHECK")
    print("="*80 + "\n")

    data_feed = AlpacaDataFeed()

    universe_state = _build_strategy_universes(data_feed)
    base_universe = set(universe_state.get("base", set()))
    voyager_tickers = set(universe_state.get("voyager", set()))
    sniper_tickers = set(universe_state.get("sniper", set()))
    remora_tickers = set(universe_state.get("remora", set()))
    short_tickers = set(universe_state.get("short", set()))
    contrarian_tickers = set(universe_state.get("contrarian", set()))

    detector = TripleConfluenceDetector()
    short_opps = []
    reaper_opps = []
    research_near_misses: Dict[str, List[Dict]] = {"VOYAGER": [], "SNIPER": [], "REMORA": []}
    reaper_status = "STANDBY (VIX unavailable)"
    run_id = None
    logged_rejects = {}

    if use_legacy_overlap:
        print("Mode: LEGACY overlap (universe membership only)\n")
        confluence = detector.analyze_confluence(
            list(voyager_tickers),
            list(sniper_tickers),
            list(remora_tickers),
        )
    else:
        if research_mode:
            quality_tag = "HIGH-QUALITY" if research_high_quality else "ALL"
            print(f"Mode: RESEARCH overlap (approved + near-miss candidates, {quality_tag})\n")
        else:
            print("Mode: SIGNAL-AWARE overlap (approved scanner opportunities)\n")
        market_session = _market_session_now()
        vix_level = _get_current_vix()
        regime_context = _regime_context_from_vix(vix_level)
        watchlist_size = len(base_universe or (voyager_tickers | sniper_tickers | remora_tickers))
        decision_logger = DecisionLogger()
        try:
            run_id = decision_logger.start_run(
                engine_name="SHOW_CONFLUENCE",
                notes="signal_aware",
                watchlist_size=watchlist_size,
                market_session=market_session,
                regime_status=regime_context.get("status"),
                regime_volatility=regime_context.get("volatility"),
            )
        except Exception:
            run_id = None

        # Voyager: approved long/short opportunities from full pipeline.
        voyager_engine = VoyagerProductionV2Complete(account_size=100000, verbose=False, growth_mode=False)
        voyager_scan = voyager_engine.scan_complete(
            raw_universe_override=sorted(voyager_tickers) if voyager_tickers else None
        )
        voyager_opps = (
            list(voyager_scan.get('long_opportunities', []))
            + list(voyager_scan.get('short_opportunities', []))
        )
        voyager_rejects = _extract_voyager_long_rejects(voyager_engine)

        # Sniper: run approved breakout scanner on adaptive universe.
        sniper_scanner = SniperScannerV2(data_feed=data_feed, account_equity=100000)
        sniper_opps = sniper_scanner.scan_universe(list(sniper_tickers))
        sniper_rejects = list(getattr(sniper_scanner, "_scan_rejects", []) or [])

        # Remora: run approved catalyst scanner on adaptive universe.
        remora_scanner = RemoraScanner(trading_client=data_feed, account_equity=100000)
        remora_opps = remora_scanner.scan_for_catalysts(list(remora_tickers))
        remora_rejects = list(getattr(remora_scanner, "_scan_rejects", []) or [])

        # Short: dedicated deterioration scanner (separate from Voyager shorts).
        short_scanner = ShortScanner(trading_client=data_feed, account_equity=100000)
        short_opps = short_scanner.scan_for_shorts(sorted(short_tickers)) if short_tickers else []
        short_rejects = list(getattr(short_scanner, "_scan_rejects", []) or [])

        # Reaper (Contrarian): active only when VIX >= trigger.
        if vix_level >= ContrarianScanner.VIX_TRIGGER:
            contrarian_scanner = ContrarianScanner()
            reaper_opps = contrarian_scanner.scan(sorted(contrarian_tickers), vix_level)
            reaper_status = f"ACTIVE (VIX {vix_level:.1f})"
        elif vix_level > 0:
            reaper_status = f"STANDBY (VIX {vix_level:.1f} < {ContrarianScanner.VIX_TRIGGER:.0f})"

        if run_id:
            logged_rejects["VOYAGER"] = _log_scanner_rejects(
                decision_logger, run_id, voyager_rejects, "VOYAGER", "LONG", market_session, regime_context
            )
            logged_rejects["SNIPER"] = _log_scanner_rejects(
                decision_logger, run_id, sniper_rejects, "SNIPER", "LONG", market_session, regime_context
            )
            logged_rejects["REMORA"] = _log_scanner_rejects(
                decision_logger, run_id, remora_rejects, "REMORA", "LONG", market_session, regime_context
            )
            logged_rejects["SHORT"] = _log_scanner_rejects(
                decision_logger, run_id, short_rejects, "SHORT", "SHORT", market_session, regime_context
            )
            try:
                decision_logger.finalize_run(run_id)
            except Exception:
                pass

        print("\nRouted strategy universes:")
        print(f"   Base:    {len(base_universe)}")
        print(f"   Voyager: {len(voyager_tickers)}")
        print(f"   Sniper:  {len(sniper_tickers)}")
        print(f"   Remora:  {len(remora_tickers)}")
        print(f"   Short:   {len(short_tickers)}")
        print(f"   Reaper:  {len(contrarian_tickers)}")

        print(f"\nApproved opportunities:")
        print(f"   Voyager: {len(voyager_opps)}")
        print(f"   Sniper:  {len(sniper_opps)}")
        print(f"   Remora:  {len(remora_opps)}")
        print(f"   Short:   {len(short_opps)}")
        print(f"   Reaper:  {len(reaper_opps)}  [{reaper_status}]")
        if research_mode:
            research_near_misses = _build_research_near_miss_map(
                voyager_rejects=voyager_rejects,
                sniper_rejects=sniper_rejects,
                remora_rejects=remora_rejects,
                score_gap=research_score_gap,
                rr_relax=research_rr_relax,
                max_per_strategy=research_max_per_strategy,
                high_quality_only=research_high_quality,
            )
            print(
                f"   Near-miss pool: "
                f"V {len(research_near_misses.get('VOYAGER', []))} | "
                f"S {len(research_near_misses.get('SNIPER', []))} | "
                f"R {len(research_near_misses.get('REMORA', []))}"
            )
            _print_research_near_misses(research_near_misses)

        voyager_for_confluence = list(voyager_opps)
        sniper_for_confluence = list(sniper_opps)
        remora_for_confluence = list(remora_opps)
        if research_mode:
            voyager_for_confluence += list(research_near_misses.get("VOYAGER", []))
            sniper_for_confluence += list(research_near_misses.get("SNIPER", []))
            remora_for_confluence += list(research_near_misses.get("REMORA", []))

        confluence = detector.analyze_from_opportunities(
            voyager_for_confluence,
            sniper_for_confluence,
            remora_for_confluence,
        )

    # Display
    triple_count = confluence.get('stats', {}).get('triple_count', 0)
    if triple_count > 0:
        print(f"\n🎯 {triple_count} TRIPLE CONFLUENCE DETECTED!\n")
        for opp in confluence.get('triple', [])[:15]:
            print(f"⭐⭐⭐ {opp.ticker}")
            print(f"   Direction: {getattr(opp, 'direction', 'LONG')}")
            print(f"   Conviction: {opp.conviction_score:.1f}/100")
            print(f"   Scores: {getattr(opp, 'strategy_scores', {})}")
            print(f"   Position: {opp.recommended_position_multiplier:.2f}x")
            print(f"   {opp.analysis}\n")
    else:
        print("No triple confluence at this time\n")

    print("Double confluence summary:")
    for combo, items in confluence.get('double', {}).items():
        print(f"   {combo}: {len(items)}")
        for opp in items[:5]:
            print(
                f"      ⭐⭐ {opp.ticker} {getattr(opp, 'direction', 'LONG')} "
                f"| score {opp.conviction_score:.1f} | x{opp.recommended_position_multiplier:.2f}"
            )

    if not use_legacy_overlap:
        print("\nOverlay strategy summary:")
        print(f"   short:  {len(short_opps)} approved")
        print(f"   reaper: {len(reaper_opps)} setups  [{reaper_status}]")
        if research_mode:
            print(
                "   Note: triple/double confluence includes near-miss candidates "
                "(research-only, not executable)."
            )
        else:
            print("   Note: triple/double confluence is computed on Voyager+Sniper+Remora only.")
            print("   Note: approved coverage below is not the same as routed/scanned universe size.")
        if run_id:
            total_logged = sum(int(v or 0) for v in logged_rejects.values())
            print(f"   Decisions run: {run_id}")
            print(
                f"   Logged rejects: {total_logged} "
                f"(VOYAGER {logged_rejects.get('VOYAGER', 0)}, "
                f"SNIPER {logged_rejects.get('SNIPER', 0)}, "
                f"REMORA {logged_rejects.get('REMORA', 0)}, "
                f"SHORT {logged_rejects.get('SHORT', 0)})"
            )

    print("="*80 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show strategy confluence opportunities.")
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Use legacy universe-overlap mode instead of signal-aware mode.",
    )
    parser.add_argument(
        "--research",
        action="store_true",
        help="Include near-miss rejected candidates for research-only confluence.",
    )
    parser.add_argument(
        "--research-score-gap",
        type=float,
        default=12.0,
        help="Max score shortfall for near-miss candidates (default: 12).",
    )
    parser.add_argument(
        "--research-rr-relax",
        type=float,
        default=0.5,
        help="R:R tolerance below live threshold for near-miss candidates (default: 0.5).",
    )
    parser.add_argument(
        "--research-max-per-strategy",
        type=int,
        default=25,
        help="Max retained near-miss candidates per strategy (default: 25).",
    )
    parser.add_argument(
        "--research-high-quality",
        action="store_true",
        help=(
            "Only show high-quality near-miss candidates "
            "(<=1 failed gate, small score/RR shortfall)."
        ),
    )
    args = parser.parse_args()
    show_confluence(
        use_legacy_overlap=args.legacy,
        research_mode=args.research,
        research_score_gap=args.research_score_gap,
        research_rr_relax=args.research_rr_relax,
        research_max_per_strategy=args.research_max_per_strategy,
        research_high_quality=args.research_high_quality,
    )
