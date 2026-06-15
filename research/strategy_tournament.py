#!/usr/bin/env python3
"""
research/strategy_tournament.py — Phase 1G.4

Strategy Tournament / Profitability Discovery Lab (research-only).

Compares multiple candidate alpha/option strategy families against the SAME
data, SAME friction assumptions, SAME regime segmentation, SAME risk rules, and
the SAME pass/fail gates — so the comparison is apples-to-apples and cannot be
gamed by tuning one family's labels.

It is strictly diagnostic. It does NOT:
  - enable live trading, create broker orders, trade proposals, or paper_signals
  - register or activate a strategy / sleeve
  - mutate decisions / paper_signals / outcomes / veto_log / any forward log
  - call providers, governance, execution, or the live-capital gate
  - tune labels to make a strategy look good

Event spine: ``data/state/stock_lens_forward_log.jsonl`` — the only artifact that
carries *forward* outcomes per historical setup (5d/10d return, MAE, MFE, rel-SPY).
Each snapshot is classified into each family's candidate state using research
filters only, then forward cohorts are compared, segmented by regime, scored
against random + cash baselines, and graded by fixed gates.

Writes:
  - cache/research/strategy_tournament_latest.json
  - logs/strategy_tournament_latest.txt
  - docs/research/STRATEGY_TOURNAMENT_RESULTS.md

Design: docs/research/STRATEGY_TOURNAMENT_DESIGN.md
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import forecast_forward_tracker as fft
from core import paper_evidence_epoch as epoch

# Reuse the LEADER_RESET classifier so the family stays identical to its own study.
from research import leader_reset_event_study as les

# Shared friction benchmark (round-trip pct), same constant the tactical resolver uses.
try:
    from research.paper_trades.resolve_tactical_outcomes import ROUND_TRIP_FRICTION_PCT
except Exception:  # pragma: no cover
    ROUND_TRIP_FRICTION_PCT = 0.30

CACHE = ROOT / "cache" / "research"
JSON_OUT = CACHE / "strategy_tournament_latest.json"
TXT_OUT = ROOT / "logs" / "strategy_tournament_latest.txt"
DOC_OUT = ROOT / "docs" / "research" / "STRATEGY_TOURNAMENT_RESULTS.md"

FORECAST_PATH = CACHE / "regime_forecast_latest.json"
ALPHA_PATH = CACHE / "alpha_discovery_board_latest.json"
SHORT_RADAR_PATH = CACHE / "short_opportunity_radar_latest.json"

VERSION = "STRATEGY_TOURNAMENT_V1"

# ── Event labels (Task 3) ────────────────────────────────────────────
LBL_RESEARCH = "RESEARCH_CANDIDATE"
LBL_WATCH = "WATCH"
LBL_BLOCKED = "BLOCKED"
LBL_NO_EDGE = "NO_EDGE"
LBL_NOT_ENOUGH = "NOT_ENOUGH_DATA"
EVENT_LABELS = (LBL_RESEARCH, LBL_WATCH, LBL_BLOCKED, LBL_NO_EDGE, LBL_NOT_ENOUGH)

# ── Family verdicts (Task 6 ladder) ──────────────────────────────────
V_REJECT = "REJECT"
V_NEED_MORE = "NEED_MORE_DATA"
V_WATCHLIST = "WATCHLIST_RESEARCH"
V_DEEPER = "READY_FOR_DEEPER_BACKTEST"
V_PAPER = "READY_FOR_PAPER_SPEC"

# ── Family keys ──────────────────────────────────────────────────────
F_LEADER_RESET = "LEADER_RESET"
F_OPTIONS_EXPR = "OPTIONS_EXPRESSION_ON_VALID_SETUP"
F_PEAD = "POST_EARNINGS_DRIFT"
F_OPTIONS_FLOW = "OPTIONS_FLOW_CONFIRMATION"
F_13F = "13F_EMERGING"
F_FAILED_SHORT = "FAILED_LEADER_SHORT"
F_RISKOFF_SHORT = "RISK_OFF_RELATIVE_WEAKNESS_SHORT"
F_CASH = "CASH_NO_TRADE"
F_MOMENTUM = "SIMPLE_MOMENTUM_BASELINE"
F_RANDOM = "RANDOM_LIQUID_CONTROL"

LONG_FAMILIES = (F_LEADER_RESET, F_OPTIONS_EXPR, F_PEAD, F_OPTIONS_FLOW, F_13F, F_MOMENTUM)
SHORT_FAMILIES = (F_FAILED_SHORT, F_RISKOFF_SHORT)
BASELINE_FAMILIES = (F_CASH, F_RANDOM)
ALL_FAMILIES = LONG_FAMILIES + SHORT_FAMILIES + BASELINE_FAMILIES

# ── Fixed risk rules / gates (NOT tuned on this sample) ──────────────
MIN_MATURE_SAMPLE = 30          # resolved-5d events required to grade edge
PROPOSED_STOP_PCT = -6.0        # uniform research stop (% from entry)
PROPOSED_TARGET_PCT = 8.0       # uniform research target (% from entry)
MAE_FLOOR_PCT = -8.0            # mean 5d MAE must be better than this
HEAT_CAP_RISK_PCT = 8.0         # |stop| must fit within this per-position heat budget
STOP_HIT_RATE_CEIL = 0.50       # simulated stop-hit rate must stay below this
NEXT_MATURITY_HORIZON_D = 5     # primary maturity horizon

# Random control determinism (no lookahead — keyed on snapshot id, not outcomes).
RANDOM_CONTROL_SEED = 20260526
RANDOM_CONTROL_N = 200

# Options expression labels (Task 7).
OPT_CALL_DEBIT = "CALL_DEBIT_SPREAD"
OPT_PUT_CREDIT = "PUT_CREDIT_SPREAD"
OPT_CSP_WHEEL = "CSP_WHEEL"
OPT_NO_EDGE = "NO_OPTIONS_EDGE"
WIDE_SPREAD_PCT = 1.0           # bid/ask spread wider than this (% of mid) => no edge


# ── tiny helpers ─────────────────────────────────────────────────────
def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _lower(v: Any) -> str:
    return str(v or "").lower()


# ── regime canonicalisation (Task 5) ─────────────────────────────────
CANON_REGIMES = (
    "BULL_CONTINUATION", "BULL_PULLBACK", "FRAGILE", "RISK_OFF", "STRESS",
    "HIGH_VIX", "LOW_VIX", "SECTOR_BREADTH_BROAD", "SECTOR_BREADTH_NARROW",
)


def canon_regime(row: Dict[str, Any]) -> str:
    reg = _lower((row.get("layers") or {}).get("market", {}).get("regime"))
    if "pullback" in reg or "buy-the-dip" in reg or "buy the dip" in reg:
        return "BULL_PULLBACK"
    if "continuation" in reg or ("bull" in reg and "pullback" not in reg):
        return "BULL_CONTINUATION"
    if "risk-off" in reg or "risk off" in reg:
        return "RISK_OFF"
    if "stress" in reg:
        return "STRESS"
    if "fragile" in reg or "conflicted" in reg:
        return "FRAGILE"
    return "OTHER"


def sector_breadth_bucket(row: Dict[str, Any]) -> str:
    sv = _lower((row.get("layers") or {}).get("sector", {}).get("view"))
    if sv in ("leading", "improving"):
        return "SECTOR_BREADTH_BROAD"
    if sv in ("weakening", "defensive"):
        return "SECTOR_BREADTH_NARROW"
    return "SECTOR_NEUTRAL"


# ── standardized event row (Task 3) ──────────────────────────────────
def _bullish_setup(row: Dict[str, Any]) -> bool:
    """A valid long setup: bullish label/tech, not blocked, not broken/avoid."""
    label = _lower(row.get("label"))
    tech = _lower((row.get("layers") or {}).get("tech", {}).get("view"))
    entry = _lower((row.get("layers") or {}).get("entry", {}).get("view"))
    if row.get("hard_caps_fired"):
        return False
    if "broken" in entry or "avoid" in entry:
        return False
    return ("bullish" in label or "bullish" in tech) and "bearish" not in tech


def _extended(row: Dict[str, Any]) -> bool:
    tech = (row.get("layers") or {}).get("tech", {})
    entry = _lower((row.get("layers") or {}).get("entry", {}).get("view"))
    return bool(tech.get("extended")) or "extended" in _lower(tech.get("view")) \
        or "too extended" in entry or "extended" in _lower(row.get("label"))


def base_event_fields(row: Dict[str, Any], family: str, side: str,
                      label: str, reason_codes: List[str],
                      reject_codes: List[str]) -> Dict[str, Any]:
    layers = row.get("layers") or {}
    return {
        "ticker": row.get("ticker"),
        "event_date": row.get("anchor_date"),
        "strategy_family": family,
        "side": side,
        "regime_state": canon_regime(row),
        "sector_state": (layers.get("sector") or {}).get("view") or "unknown",
        "entry_state": (layers.get("entry") or {}).get("view") or "unknown",
        "options_quality": (layers.get("options") or {}).get("view") or "unknown",
        "gatekeeper_status": row.get("gatekeeper_status") or "unknown",
        "earnings_proximity": row.get("earnings_proximity") or "unknown",
        "liquidity_spread_quality": row.get("liquidity_spread_quality") or "unknown",
        "proposed_stop_pct": PROPOSED_STOP_PCT if side != "cash" else 0.0,
        "proposed_target_pct": (PROPOSED_TARGET_PCT if side == "long"
                                else (-PROPOSED_TARGET_PCT if side == "short" else 0.0)),
        "risk_at_stop_pct": abs(PROPOSED_STOP_PCT) if side != "cash" else 0.0,
        "heat_cap_fit": (abs(PROPOSED_STOP_PCT) <= HEAT_CAP_RISK_PCT) if side != "cash" else True,
        "reason_codes": reason_codes,
        "reject_codes": reject_codes,
        "label": label,
        "snapshot_id": row.get("snapshot_id"),
        "in_clean_epoch": not epoch.is_legacy(row.get("anchor_date")),
        "outcomes": row.get("outcomes") or {},
    }


# ── family classifiers ───────────────────────────────────────────────
def classify_leader_reset(row: Dict[str, Any]) -> Dict[str, Any]:
    c = les.classify_state(row)
    state = c["state"]
    mapping = {
        les.STATE_READY: LBL_RESEARCH,
        les.STATE_WATCH: LBL_WATCH,
        les.STATE_LATE: LBL_NO_EDGE,
        les.STATE_BLOCKED: LBL_BLOCKED,
        les.STATE_NO_EDGE: LBL_NO_EDGE,
    }
    label = mapping[state]
    reject = [] if label in (LBL_RESEARCH, LBL_WATCH) else [c["reason"]]
    reason = [c["reason"]] if label in (LBL_RESEARCH, LBL_WATCH) else []
    return base_event_fields(row, F_LEADER_RESET, "long", label, reason, reject)


def classify_options_expression(row: Dict[str, Any]) -> Dict[str, Any]:
    """Options structure is chosen ONLY after a valid stock setup. The expression
    decision uses the historical options.view as a coarse proxy; precise spread/IV
    inputs are unavailable in the spine, so the structure is research-only."""
    if row.get("hard_caps_fired"):
        return base_event_fields(row, F_OPTIONS_EXPR, "long", LBL_BLOCKED, [],
                                 ["hard caps fired"])
    if not _bullish_setup(row):
        return base_event_fields(row, F_OPTIONS_EXPR, "long", LBL_NO_EDGE, [],
                                 ["no valid long stock setup"])
    options = (row.get("layers") or {}).get("options", {})
    expr = evaluate_options_expression(
        setup_valid=True,
        extended=_extended(row),
        options_view=options.get("view"),
        options_available=options.get("available"),
        quality_tier=(row.get("layers") or {}).get("alpha", {}).get("tier"),
    )
    ev = base_event_fields(row, F_OPTIONS_EXPR, "long",
                           LBL_WATCH if expr["label"] != OPT_NO_EDGE else LBL_NO_EDGE,
                           expr["reason_codes"], expr["reject_codes"])
    ev["options_expression"] = expr
    return ev


def classify_pead(row: Dict[str, Any]) -> Dict[str, Any]:
    """Post-earnings drift. The spine has no per-event earnings-reaction gap or
    earnings date, so this family is honestly NOT_ENOUGH_DATA — flagged as a data
    gap rather than approximated from unrelated fields."""
    return base_event_fields(row, F_PEAD, "long", LBL_NOT_ENOUGH, [],
                             ["no earnings-reaction field in event spine"])


def classify_options_flow(row: Dict[str, Any]) -> Dict[str, Any]:
    """Unusual options flow CONFIRMS a spot/tape setup. No options-only chase."""
    if row.get("hard_caps_fired"):
        return base_event_fields(row, F_OPTIONS_FLOW, "long", LBL_BLOCKED, [],
                                 ["hard caps fired"])
    if not _bullish_setup(row):
        return base_event_fields(row, F_OPTIONS_FLOW, "long", LBL_NO_EDGE, [],
                                 ["no spot/tape setup to confirm (no options-only chase)"])
    ov = _lower((row.get("layers") or {}).get("options", {}).get("view"))
    confirming = ov in ("bullish confirming", "bullish positioning")
    chase = ov in ("speculative chase", "bullish (late)")
    if chase:
        return base_event_fields(row, F_OPTIONS_FLOW, "long", LBL_NO_EDGE, [],
                                 ["options flow is late/speculative chase, not confirmation"])
    if confirming and not _extended(row):
        return base_event_fields(row, F_OPTIONS_FLOW, "long", LBL_RESEARCH,
                                 [f"spot setup confirmed by options flow ({ov})"], [])
    if confirming and _extended(row):
        return base_event_fields(row, F_OPTIONS_FLOW, "long", LBL_WATCH,
                                 ["confirming flow but entry extended"], [])
    return base_event_fields(row, F_OPTIONS_FLOW, "long", LBL_NO_EDGE, [],
                             [f"no confirming options flow (view={ov or 'n/a'})"])


def classify_13f_emerging(row: Dict[str, Any]) -> Dict[str, Any]:
    """New institutional accumulation, not already-crowded winners."""
    if row.get("hard_caps_fired"):
        return base_event_fields(row, F_13F, "long", LBL_BLOCKED, [], ["hard caps fired"])
    alpha = (row.get("layers") or {}).get("alpha", {})
    track = _lower(alpha.get("track"))
    if "emerging" not in track:
        return base_event_fields(row, F_13F, "long", LBL_NO_EDGE, [],
                                 [f"not an emerging-accumulation name (track={track or 'n/a'})"])
    if not _bullish_setup(row):
        return base_event_fields(row, F_13F, "long", LBL_NO_EDGE, [],
                                 ["emerging track but setup not constructive"])
    if _extended(row):
        return base_event_fields(row, F_13F, "long", LBL_NO_EDGE, [],
                                 ["emerging name but already extended/crowded"])
    return base_event_fields(row, F_13F, "long", LBL_RESEARCH,
                             ["emerging institutional accumulation, not yet crowded"], [])


def classify_failed_leader_short(row: Dict[str, Any]) -> Dict[str, Any]:
    """Former leader losing trend/support with distribution — RESEARCH-ONLY short."""
    layers = row.get("layers") or {}
    track = _lower(layers.get("alpha", {}).get("track"))
    tech = _lower(layers.get("tech", {}).get("view"))
    entry = _lower(layers.get("entry", {}).get("view"))
    was_leader = bool(track)
    breaking = "bearish" in tech or "broken" in entry
    if was_leader and breaking:
        return base_event_fields(row, F_FAILED_SHORT, "short", LBL_WATCH,
                                 ["former leader breaking trend/support (research-only)"], [])
    return base_event_fields(row, F_FAILED_SHORT, "short", LBL_NO_EDGE, [],
                             ["not a failed-leader short setup"])


def classify_riskoff_short(row: Dict[str, Any]) -> Dict[str, Any]:
    """Only active in fragile / risk-off regimes. Current sample is bull-only, so
    this is structurally NOT_ENOUGH_DATA."""
    reg = canon_regime(row)
    if reg in ("RISK_OFF", "STRESS", "FRAGILE"):
        weak = _lower((row.get("layers") or {}).get("sector", {}).get("view")) in ("weakening", "defensive")
        if weak:
            return base_event_fields(row, F_RISKOFF_SHORT, "short", LBL_WATCH,
                                     ["relative weakness in risk-off regime (research-only)"], [])
    return base_event_fields(row, F_RISKOFF_SHORT, "short", LBL_NOT_ENOUGH, [],
                             [f"not a risk-off regime (regime={reg})"])


def classify_simple_momentum(row: Dict[str, Any]) -> Dict[str, Any]:
    """Simple relative-strength buy rule: bullish tech / positive RS, ignore entry
    quality. Baseline to test whether the complex system adds value."""
    tech = (row.get("layers") or {}).get("tech", {})
    rs = _f(tech.get("rs_10d_pct"))
    bullish = "bullish" in _lower(tech.get("view"))
    positive_rs = rs is not None and rs > 0
    if bullish or positive_rs:
        return base_event_fields(row, F_MOMENTUM, "long", LBL_RESEARCH,
                                 ["simple momentum/RS positive"], [])
    return base_event_fields(row, F_MOMENTUM, "long", LBL_NO_EDGE, [],
                             ["no simple momentum"])


CLASSIFIERS = {
    F_LEADER_RESET: classify_leader_reset,
    F_OPTIONS_EXPR: classify_options_expression,
    F_PEAD: classify_pead,
    F_OPTIONS_FLOW: classify_options_flow,
    F_13F: classify_13f_emerging,
    F_FAILED_SHORT: classify_failed_leader_short,
    F_RISKOFF_SHORT: classify_riskoff_short,
    F_MOMENTUM: classify_simple_momentum,
}


# ── options expression evaluator (Task 7, pure function) ─────────────
def evaluate_options_expression(
    *,
    setup_valid: bool,
    extended: bool,
    options_view: Any = None,
    options_available: Any = None,
    spread_pct: Optional[float] = None,
    iv_rank: Optional[float] = None,
    liquidity_score: Optional[float] = None,
    assignment_acceptable: Optional[bool] = None,
    buying_power_ok: Optional[bool] = None,
    near_earnings: bool = False,
    quality_tier: Any = None,
    strikes: Optional[Dict[str, float]] = None,
    premium: Optional[float] = None,
) -> Dict[str, Any]:
    """Choose an options expression for a *validated stock setup*, or reject.

    Options are never standalone alpha here — `setup_valid` must be True first.
    `max_loss / max_profit / breakeven / risk_reward` are filled only when real
    `strikes` + `premium` inputs are supplied; otherwise they are 'unavailable'
    (the historical spine has no chain, so it never fabricates numbers).
    """
    reject: List[str] = []
    reason: List[str] = []
    ov = _lower(options_view)

    if not setup_valid:
        reject.append("no valid stock setup")
    if options_available is False:
        reject.append("options chain unavailable")
    if spread_pct is not None and spread_pct > WIDE_SPREAD_PCT:
        reject.append(f"spread too wide ({spread_pct:.2f}% > {WIDE_SPREAD_PCT}%)")
    if liquidity_score is not None and liquidity_score < 30:
        reject.append("options chain too thin")
    if near_earnings:
        reject.append("near-earnings binary risk")
    if extended:
        reject.append("entry too extended for a defined-risk options buy")
    if ov in ("no data", "no edge"):
        reject.append(f"options layer has no edge (view={ov})")
    if "bearish hedge" in ov:
        reject.append("unresolved bearish options hedge")

    if reject:
        return {"label": OPT_NO_EDGE, "reason_codes": reason, "reject_codes": reject,
                **_options_pl(None, strikes, premium)}

    # Structure selection (valid setup, no blockers).
    structure = OPT_NO_EDGE
    if "bullish" in ov:
        # Confirmed bullish view: prefer a defined-risk call debit spread, unless
        # IV is rich enough to favour selling premium under support.
        if iv_rank is not None and iv_rank >= 50:
            structure = OPT_PUT_CREDIT
            reason.append("strong stock, elevated IV — sell premium below support")
        else:
            structure = OPT_CALL_DEBIT
            reason.append("valid bullish setup, defined upside — call debit spread")
        # CSP/Wheel only for ownable quality names with assignment + BP confirmed.
        if (str(quality_tier).upper() == "A" and assignment_acceptable is True
                and buying_power_ok is True and not near_earnings):
            structure = OPT_CSP_WHEEL
            reason = ["ownable quality (tier A), assignment + buying power OK — CSP/Wheel"]
    else:
        reject.append(f"options view not confirming bullish (view={ov or 'n/a'})")
        return {"label": OPT_NO_EDGE, "reason_codes": [], "reject_codes": reject,
                **_options_pl(None, strikes, premium)}

    return {"label": structure, "reason_codes": reason, "reject_codes": [],
            "assignment_risk": (None if structure != OPT_CSP_WHEEL
                                else ("acceptable" if assignment_acceptable else "unconfirmed")),
            **_options_pl(structure, strikes, premium)}


def _options_pl(structure: Optional[str], strikes: Optional[Dict[str, float]],
                premium: Optional[float]) -> Dict[str, Any]:
    """Compute max_loss/max_profit/breakeven/risk_reward only from explicit
    strike+premium inputs. Without them everything is 'unavailable' — never guessed."""
    if not strikes or premium is None or structure is None:
        return {"max_loss": "unavailable", "max_profit": "unavailable",
                "breakeven": "unavailable", "risk_reward": "unavailable"}
    lo = strikes.get("long")
    sh = strikes.get("short")
    if lo is None or sh is None:
        return {"max_loss": "unavailable", "max_profit": "unavailable",
                "breakeven": "unavailable", "risk_reward": "unavailable"}
    width = abs(sh - lo)
    if structure == OPT_CALL_DEBIT:
        max_loss = round(premium, 4)
        max_profit = round(width - premium, 4)
        breakeven = round(lo + premium, 4)
    elif structure == OPT_PUT_CREDIT:
        max_profit = round(premium, 4)
        max_loss = round(width - premium, 4)
        breakeven = round(sh - premium, 4)
    else:  # CSP / Wheel
        max_profit = round(premium, 4)
        max_loss = round(sh - premium, 4)  # assigned cost basis
        breakeven = round(sh - premium, 4)
    rr = round(max_profit / max_loss, 4) if max_loss not in (0, None) else "unavailable"
    return {"max_loss": max_loss, "max_profit": max_profit,
            "breakeven": breakeven, "risk_reward": rr}


# ── forward outcome metrics (Task 4) ─────────────────────────────────
def _mature(ev: Dict[str, Any]) -> bool:
    return _f(ev["outcomes"].get(f"return_{NEXT_MATURITY_HORIZON_D}d_pct")) is not None


def _simulate_stop_target(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Stop/target simulation from MAE/MFE only. Order unknown when both hit, so we
    conservatively assume the stop filled first (and flag ambiguity)."""
    oc = ev["outcomes"]
    side = ev["side"]
    mae = _f(oc.get("max_drawdown_5d_pct"))
    mfe = _f(oc.get("max_favorable_5d_pct"))
    raw = _f(oc.get("return_5d_pct"))
    if raw is None:
        return {"stop_hit": None, "target_hit": None, "ambiguous": None,
                "sim_return_5d_pct": None}
    if side == "long":
        stop_hit = mae is not None and mae <= PROPOSED_STOP_PCT
        target_hit = mfe is not None and mfe >= PROPOSED_TARGET_PCT
        stop_val, tgt_val = PROPOSED_STOP_PCT, PROPOSED_TARGET_PCT
    elif side == "short":
        # short profits when price falls: favorable excursion is the drawdown.
        stop_hit = mfe is not None and mfe >= abs(PROPOSED_STOP_PCT)
        target_hit = mae is not None and mae <= -PROPOSED_TARGET_PCT
        stop_val, tgt_val = abs(PROPOSED_STOP_PCT), PROPOSED_TARGET_PCT  # short return sign handled below
        raw = -raw  # short return = negative of underlying return
    else:
        return {"stop_hit": None, "target_hit": None, "ambiguous": None,
                "sim_return_5d_pct": raw}
    ambiguous = bool(stop_hit and target_hit)
    if stop_hit:
        sim = -abs(PROPOSED_STOP_PCT)
    elif target_hit:
        sim = abs(PROPOSED_TARGET_PCT)
    else:
        sim = raw
    return {"stop_hit": stop_hit, "target_hit": target_hit, "ambiguous": ambiguous,
            "sim_return_5d_pct": round(sim, 4)}


def cohort_metrics(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Forward metrics for a cohort. Immature events are excluded from every figure.
    Net = raw − round-trip friction. Short returns are sign-flipped."""
    mature = [e for e in events if _mature(e)]
    immature = [e for e in events if not _mature(e)]

    def signed(e: Dict[str, Any], key: str) -> Optional[float]:
        v = _f(e["outcomes"].get(key))
        if v is None:
            return None
        return -v if e["side"] == "short" else v

    r5 = [signed(e, "return_5d_pct") for e in mature]
    r5 = [x for x in r5 if x is not None]
    r10 = [signed(e, "return_10d_pct") for e in mature]
    r10 = [x for x in r10 if x is not None]
    # rel-SPY only meaningful for longs; for shorts we report raw flip vs spy.
    # Route through signed() (None-guarded) rather than negating inline — a short
    # event with a missing rel_spy_5d_pct would otherwise hit `-None` and crash.
    rel5 = [signed(e, "rel_spy_5d_pct") for e in mature]
    rel5 = [x for x in rel5 if x is not None]
    mae5 = [_f(e["outcomes"].get("max_drawdown_5d_pct")) for e in mature]
    mae5 = [x for x in mae5 if x is not None]
    mfe5 = [_f(e["outcomes"].get("max_favorable_5d_pct")) for e in mature]
    mfe5 = [x for x in mfe5 if x is not None]

    sims = [_simulate_stop_target(e) for e in mature]
    stop_hits = [s["stop_hit"] for s in sims if s["stop_hit"] is not None]
    tgt_hits = [s["target_hit"] for s in sims if s["target_hit"] is not None]
    sim_rets = [s["sim_return_5d_pct"] for s in sims if s["sim_return_5d_pct"] is not None]

    def mean(v: List[float]) -> Optional[float]:
        return round(statistics.mean(v), 4) if v else None

    def winrate(v: List[float]) -> Optional[float]:
        return round(sum(1 for x in v if x > 0) / len(v), 4) if v else None

    def rate(v: List[bool]) -> Optional[float]:
        return round(sum(1 for x in v if x) / len(v), 4) if v else None

    e5_net = (round(statistics.mean(r5) - ROUND_TRIP_FRICTION_PCT, 4) if r5 else None)
    e10_net = (round(statistics.mean(r10) - ROUND_TRIP_FRICTION_PCT, 4) if r10 else None)

    # Single-outlier dependence: drop the single best 5d event, recompute net.
    e5_net_drop_best = None
    if len(r5) >= 2:
        trimmed = sorted(r5)[:-1]
        e5_net_drop_best = round(statistics.mean(trimmed) - ROUND_TRIP_FRICTION_PCT, 4)

    return {
        "n_events": len(events),
        "n_mature": len(mature),
        "n_immature": len(immature),
        "n_resolved_5d": len(r5),
        "n_resolved_10d": len(r10),
        "expectancy_5d_raw": mean(r5),
        "expectancy_10d_raw": mean(r10),
        "expectancy_5d_net": e5_net,
        "expectancy_10d_net": e10_net,
        "expectancy_5d_net_drop_best": e5_net_drop_best,
        "win_rate_5d": winrate(r5),
        "win_rate_10d": winrate(r10),
        "mean_rel_spy_5d": mean(rel5),
        "mean_mae_5d": mean(mae5),
        "mean_mfe_5d": mean(mfe5),
        "stop_hit_rate": rate(stop_hits),
        "target_hit_rate": rate(tgt_hits),
        "sim_expectancy_5d_net": (round(statistics.mean(sim_rets) - ROUND_TRIP_FRICTION_PCT, 4)
                                  if sim_rets else None),
        "rel_qq_sector": "unavailable (spine has no per-event QQQ/sector returns)",
    }


# ── random + cash baselines (Task 4 / Task 6) ────────────────────────
def random_control(rows: List[Dict[str, Any]], *, seed: int = RANDOM_CONTROL_SEED,
                   n: int = RANDOM_CONTROL_N) -> List[Dict[str, Any]]:
    """Deterministic random sample of all snapshots. Selection keys ONLY on
    snapshot identity (sorted snapshot_id) + a fixed seed — never on outcomes — so
    it carries no lookahead. Each picked row becomes a long event."""
    ids = sorted(str(r.get("snapshot_id") or i) for i, r in enumerate(rows))
    rng = random.Random(seed)
    pick = set(rng.sample(ids, min(n, len(ids)))) if ids else set()
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(rows):
        if str(r.get("snapshot_id") or i) in pick:
            out.append(base_event_fields(r, F_RANDOM, "long", LBL_RESEARCH,
                                         ["random liquid control"], []))
    return out


def cash_baseline_metrics() -> Dict[str, Any]:
    """Doing nothing: zero net expectancy, zero drawdown. The bar every long
    family must clear to justify taking risk."""
    m = {k: None for k in cohort_metrics([]).keys()}
    m.update({
        "n_events": 0, "n_mature": 0, "n_immature": 0, "n_resolved_5d": 0,
        "expectancy_5d_net": 0.0, "expectancy_10d_net": 0.0,
        "mean_mae_5d": 0.0, "stop_hit_rate": 0.0,
        "note": "cash/no-trade baseline — flat, no risk taken",
    })
    return m


# ── gates + verdict (Task 6) ─────────────────────────────────────────
def grade_family(family: str, side: str, clean: Dict[str, Any],
                 random_m: Dict[str, Any]) -> Dict[str, Any]:
    n = clean.get("n_resolved_5d") or 0
    e5 = clean.get("expectancy_5d_net")
    e10 = clean.get("expectancy_10d_net")
    rel5 = clean.get("mean_rel_spy_5d")
    mae = clean.get("mean_mae_5d")
    stop_rate = clean.get("stop_hit_rate")
    drop_best = clean.get("expectancy_5d_net_drop_best")
    rand_e5 = random_m.get("expectancy_5d_net")

    gates = [
        {"gate": "min_mature_sample", "need": f">= {MIN_MATURE_SAMPLE} resolved 5d",
         "got": n, "pass": n >= MIN_MATURE_SAMPLE},
        {"gate": "net_5d_expectancy_positive", "need": "> 0", "got": e5,
         "pass": bool(e5 is not None and e5 > 0)},
        {"gate": "net_10d_expectancy_positive", "need": "> 0", "got": e10,
         "pass": bool(e10 is not None and e10 > 0)},
        {"gate": "beats_spy", "need": "mean rel-SPY 5d > 0", "got": rel5,
         "pass": bool(rel5 is not None and rel5 > 0)},
        {"gate": "beats_random_control", "need": "net 5d > random control",
         "got": [e5, rand_e5],
         "pass": bool(e5 is not None and rand_e5 is not None and e5 > rand_e5)},
        {"gate": "beats_cash", "need": "net 5d > 0", "got": e5,
         "pass": bool(e5 is not None and e5 > 0)},
        {"gate": "mae_acceptable", "need": f"mean 5d MAE > {MAE_FLOOR_PCT}", "got": mae,
         "pass": bool(mae is not None and mae > MAE_FLOOR_PCT)},
        {"gate": "stop_hit_rate_acceptable", "need": f"< {STOP_HIT_RATE_CEIL}", "got": stop_rate,
         "pass": bool(stop_rate is not None and stop_rate < STOP_HIT_RATE_CEIL)},
        {"gate": "not_one_outlier", "need": "net 5d > 0 after dropping best event",
         "got": drop_best, "pass": bool(drop_best is not None and drop_best > 0)},
        {"gate": "risk_definable", "need": "stop defined & heat-cap fit",
         "got": abs(PROPOSED_STOP_PCT) <= HEAT_CAP_RISK_PCT,
         "pass": abs(PROPOSED_STOP_PCT) <= HEAT_CAP_RISK_PCT},
    ]
    blockers = [g["gate"] for g in gates if not g["pass"]]

    # Short families never promote past research in this phase.
    research_only = side == "short"

    sample_ok = n >= MIN_MATURE_SAMPLE
    edge_gates = ("net_5d_expectancy_positive", "net_10d_expectancy_positive",
                  "beats_spy", "beats_random_control", "beats_cash")
    edge_ok = all(g["pass"] for g in gates if g["gate"] in edge_gates)
    risk_ok = all(g["pass"] for g in gates if g["gate"] in
                  ("mae_acceptable", "stop_hit_rate_acceptable", "not_one_outlier", "risk_definable"))

    if not sample_ok:
        verdict = V_NEED_MORE
        rationale = (f"Only {n} resolved-5d clean-epoch events (< {MIN_MATURE_SAMPLE}). "
                     "Cannot accept or reject — accumulate more matured evidence.")
    elif (e5 is not None and e5 < 0) and (e10 is not None and e10 < 0):
        verdict = V_REJECT
        rationale = "Negative net expectancy at both horizons on an adequate sample."
    elif edge_ok and risk_ok:
        # Single-regime sample (current bull-only tape) can't earn a full paper spec.
        verdict = V_DEEPER
        rationale = ("Clears edge + control + risk gates, but on a single-regime "
                     "(bull-tape) sample — earns a point-in-time backtest, not yet paper.")
    elif edge_ok:
        verdict = V_DEEPER
        rationale = "Edge beats baselines but a risk gate is soft — backtest before paper."
    else:
        verdict = V_WATCHLIST
        rationale = "Adequate sample but edge is mixed/marginal vs baselines."

    if research_only and verdict in (V_PAPER, V_DEEPER):
        verdict = V_WATCHLIST
        rationale = "Short-side: research-only this phase regardless of in-sample edge. " + rationale

    return {"verdict": verdict, "rationale": rationale, "gates": gates,
            "blockers": blockers, "research_only": research_only}


# ── anti-label-fix honesty block (Task 9) ────────────────────────────
def honesty_block(family: str, clean: Dict[str, Any], full: Dict[str, Any],
                  verdict: Dict[str, Any]) -> Dict[str, Any]:
    n = clean.get("n_resolved_5d") or 0
    caution = []
    if n < MIN_MATURE_SAMPLE:
        caution.append("sample below minimum")
    if (clean.get("n_immature") or 0) > 0:
        caution.append(f"{clean.get('n_immature')} immature events excluded")
    e5 = clean.get("expectancy_5d_net")
    drop_best = clean.get("expectancy_5d_net_drop_best")
    if e5 is not None and drop_best is not None and e5 > 0 and drop_best <= 0:
        caution.append("edge collapses without the single best event (outlier-dependent)")
    weakness = verdict["rationale"]
    if family == F_PEAD:
        weakness = "No earnings-reaction data in the event spine — cannot be tested here."
    falsifier = {
        F_LEADER_RESET: "reset cohorts fail to beat late/extended momentum across regimes",
        F_OPTIONS_EXPR: "options structures add no edge over the underlying long after fees",
        F_PEAD: "drift cohorts show no continuation once earnings data is added",
        F_OPTIONS_FLOW: "confirming-flow cohort underperforms the no-flow long cohort",
        F_13F: "emerging names underperform crowded leaders / random control",
        F_FAILED_SHORT: "short cohort is net-negative or worse than cash in any regime",
        F_RISKOFF_SHORT: "no positive edge once a real risk-off sample exists",
        F_MOMENTUM: "complex families fail to beat this simple baseline",
        F_RANDOM: "n/a (this IS the control)",
        F_CASH: "n/a (this IS the do-nothing baseline)",
    }.get(family, "")
    return {
        "sample_size": n,
        "caution_flag": "; ".join(caution) if caution else "none",
        "biggest_weakness": weakness,
        "falsifier": falsifier,
    }


# ── current sleeve comparison (Task 8) ───────────────────────────────
def current_sleeve_verdicts(events_by_family: Dict[str, List[Dict[str, Any]]],
                            clean_by_family: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Evidence-tied disposition of current/frozen sleeves. Verdicts are
    conservative and reference registry status + tournament cohorts; they are
    recommendations, not registry changes."""
    def _status(key: str) -> str:
        try:
            import core.strategy_registry as reg
            st = reg.sleeve_status(key)
            return getattr(st, "status", None) or str(st)
        except Exception:
            return "unknown"

    status = {k: _status(k) for k in
              ("SNIPER", "VOYAGER", "SHORT", "REMORA", "CONTRARIAN", "PATHFINDER")}

    lr = clean_by_family.get(F_LEADER_RESET, {})
    emg = clean_by_family.get(F_13F, {})
    mom = clean_by_family.get(F_MOMENTUM, {})

    def beats_momentum(c: Dict[str, Any]) -> Optional[bool]:
        a, b = c.get("expectancy_5d_net"), mom.get("expectancy_5d_net")
        if a is None or b is None:
            return None
        return a > b

    rows = [
        {"sleeve": "SNIPER", "registry_status": str(status.get("SNIPER")),
         "disposition": "KEEP PAPER (low evidence)",
         "evidence": "Active paper but too few matured outcomes to judge; keep gathering.",
         "beats_baseline": None, "over_gated": True, "regime_aligned": True},
        {"sleeve": "VOYAGER", "registry_status": str(status.get("VOYAGER")),
         "disposition": "RESEARCH ONLY",
         "evidence": "Weak approval->signal conversion (see voyager_conversion_audit); "
                     "noisy, rarely converts. Keep paper logging but treat as research.",
         "beats_baseline": None, "over_gated": True, "regime_aligned": True},
        {"sleeve": "SHORT_A", "registry_status": str(status.get("SHORT")),
         "disposition": "FREEZE (research only)",
         "evidence": "Frozen 2026-05-24; net-negative, noisy, fights bull tape. No "
                     "risk-off sample exists to revalidate. Keep frozen.",
         "beats_baseline": False, "over_gated": False, "regime_aligned": False},
        {"sleeve": "REMORA", "registry_status": str(status.get("REMORA")),
         "disposition": "FREEZE",
         "evidence": "Frozen; no tournament cohort supports reactivation.",
         "beats_baseline": None, "over_gated": None, "regime_aligned": None},
        {"sleeve": "CONTRARIAN", "registry_status": str(status.get("CONTRARIAN")),
         "disposition": "FREEZE",
         "evidence": "Frozen; mean-reversion thesis untested in this dataset.",
         "beats_baseline": None, "over_gated": None, "regime_aligned": None},
        {"sleeve": "PATHFINDER", "registry_status": str(status.get("PATHFINDER")),
         "disposition": "RESEARCH ONLY",
         "evidence": "Future-research only; no active cohort.",
         "beats_baseline": None, "over_gated": None, "regime_aligned": None},
        {"sleeve": "ALPHA_DISCOVERY", "registry_status": "research engine (strongest component)",
         "disposition": "KEEP / RESEARCH ENGINE",
         "evidence": ("Strongest current component: its tracks drive the LEADER_RESET "
                      f"(emerging->13F net5d={emg.get('expectancy_5d_net')}, "
                      f"reset net5d={lr.get('expectancy_5d_net')}) cohorts. Keep as the "
                      "discovery feed, not a direct executor."),
         "beats_baseline": beats_momentum(lr), "over_gated": False, "regime_aligned": True},
    ]
    return rows


# ── assemble (Task 10) ───────────────────────────────────────────────
def _next_maturity_date(rows: List[Dict[str, Any]], *,
                        today: Optional[date] = None) -> Optional[str]:
    """When the next batch of *recently* logged snapshots will mature at the 5d
    horizon. Only considers anchors from the last ~10 calendar days so that old
    rows stuck on a price-cache gap (which will never resolve) don't drag the date
    into the past. Returns None if every recent snapshot is already resolved."""
    today = today or date.today()
    horizon_cal = int(NEXT_MATURITY_HORIZON_D * 1.5) + 1
    pending: List[date] = []
    for r in rows:
        if (r.get("outcomes") or {}).get("return_5d_pct") is not None:
            continue
        try:
            a = date.fromisoformat(str(r.get("anchor_date"))[:10])
        except Exception:
            continue
        if (today - a).days <= 10:        # ignore stale, never-maturing rows
            pending.append(a)
    if not pending:
        return None
    due = max(min(pending) + timedelta(days=horizon_cal), today)
    return due.isoformat()


def build_tournament() -> Dict[str, Any]:
    rows = fft.load_stock_lens_log()
    forecast = _load_json(FORECAST_PATH)
    short_radar = _load_json(SHORT_RADAR_PATH)

    # Classify every snapshot into every family.
    events_by_family: Dict[str, List[Dict[str, Any]]] = {f: [] for f in ALL_FAMILIES}
    for r in rows:
        for fam, fn in CLASSIFIERS.items():
            events_by_family[fam].append(fn(r))
    events_by_family[F_RANDOM] = random_control(rows)

    # "Eligible" events = anything not NO_EDGE / NOT_ENOUGH (i.e. a real setup the
    # family would act on: RESEARCH_CANDIDATE or WATCH). Cohort metrics run on those.
    def eligible(evs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [e for e in evs if e["label"] in (LBL_RESEARCH, LBL_WATCH)]

    full_by_family: Dict[str, Dict[str, Any]] = {}
    clean_by_family: Dict[str, Dict[str, Any]] = {}
    regime_by_family: Dict[str, Dict[str, Dict[str, Any]]] = {}
    label_counts: Dict[str, Dict[str, int]] = {}

    for fam in ALL_FAMILIES:
        evs = events_by_family[fam]
        elig = eligible(evs) if fam != F_RANDOM else evs
        clean_elig = [e for e in elig if e["in_clean_epoch"]]
        full_by_family[fam] = cohort_metrics(elig)
        clean_by_family[fam] = cohort_metrics(clean_elig)
        # label distribution (anti-label-fix: keep BLOCKED/NO_EDGE visible)
        lc = {lbl: 0 for lbl in EVENT_LABELS}
        for e in evs:
            lc[e["label"]] = lc.get(e["label"], 0) + 1
        label_counts[fam] = lc
        # regime segmentation on eligible events
        seg: Dict[str, List[Dict[str, Any]]] = {}
        for e in elig:
            seg.setdefault(e["regime_state"], []).append(e)
        regime_by_family[fam] = {reg: cohort_metrics(evs2) for reg, evs2 in seg.items()}

    random_m = clean_by_family[F_RANDOM]

    # Grade families (cash + random are baselines, not graded).
    graded: Dict[str, Any] = {}
    for fam in LONG_FAMILIES + SHORT_FAMILIES:
        side = "short" if fam in SHORT_FAMILIES else "long"
        v = grade_family(fam, side, clean_by_family[fam], random_m)
        v["honesty"] = honesty_block(fam, clean_by_family[fam], full_by_family[fam], v)
        graded[fam] = v

    clean_by_family[F_CASH] = cash_baseline_metrics()

    # Ranking: families with an adequate clean sample, ranked by net 5d expectancy.
    ranked = []
    for fam in LONG_FAMILIES + SHORT_FAMILIES:
        c = clean_by_family[fam]
        ranked.append({
            "family": fam,
            "verdict": graded[fam]["verdict"],
            "n_resolved_5d": c.get("n_resolved_5d"),
            "expectancy_5d_net": c.get("expectancy_5d_net"),
            "expectancy_10d_net": c.get("expectancy_10d_net"),
            "mean_rel_spy_5d": c.get("mean_rel_spy_5d"),
            "caution_flag": graded[fam]["honesty"]["caution_flag"],
        })
    # Adequate-sample families ranked first by net 5d; the rest by sample size.
    ranked.sort(key=lambda x: (
        -(1 if (x["n_resolved_5d"] or 0) >= MIN_MATURE_SAMPLE else 0),
        -((x["expectancy_5d_net"] if x["expectancy_5d_net"] is not None else -999)),
        -(x["n_resolved_5d"] or 0),
    ))

    # Best next paper candidate (Task 6 honesty rule).
    paper_ready = [r for r in ranked if r["verdict"] == V_PAPER]
    deeper = [r for r in ranked if r["verdict"] == V_DEEPER]
    if paper_ready:
        best = paper_ready[0]["family"]
        best_verdict = V_PAPER
        no_trade_line = None
    elif deeper:
        best = deeper[0]["family"]
        best_verdict = V_DEEPER
        no_trade_line = ("No strategy ready for paper. Best candidate earns a deeper "
                         "point-in-time backtest only. Stay research-only.")
    else:
        best = None
        best_verdict = V_NEED_MORE
        no_trade_line = "No strategy ready. Stay research-only."

    # Short-side readiness from the radar (cache-only).
    short_state = (short_radar or {}).get("state") or "UNKNOWN"
    short_map = {"SHORTS_OFF": "OFF", "WATCH": "RESEARCH", "RESEARCH_ACTIVE": "RESEARCH",
                 "SHORT_SLEEVE_TEST_CANDIDATE": "TEST_CANDIDATE"}
    short_side = short_map.get(short_state, "OFF")

    # Best options expression across eligible OPTIONS_EXPRESSION events.
    opt_labels: Dict[str, int] = {}
    for e in events_by_family[F_OPTIONS_EXPR]:
        oe = e.get("options_expression")
        if oe:
            opt_labels[oe["label"]] = opt_labels.get(oe["label"], 0) + 1
    options_expression = "NONE" if not any(k != OPT_NO_EDGE for k in opt_labels) else "RESEARCH_ONLY"

    sleeve_verdicts = current_sleeve_verdicts(events_by_family, clean_by_family)

    headline_regime = ((forecast or {}).get("headline") or {}).get("current_regime")

    return {
        "kind": "strategy_tournament",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "phase": "1G.4",
        "event_spine": "data/state/stock_lens_forward_log.jsonl",
        "n_snapshots": len(rows),
        "clean_epoch_start": epoch.CLEAN_PAPER_EVIDENCE_START,
        "friction_round_trip_pct": ROUND_TRIP_FRICTION_PCT,
        "primary_horizon_days": NEXT_MATURITY_HORIZON_D,
        "headline_regime": headline_regime,
        "next_maturity_due": _next_maturity_date(rows),
        "risk_rules": {
            "proposed_stop_pct": PROPOSED_STOP_PCT,
            "proposed_target_pct": PROPOSED_TARGET_PCT,
            "min_mature_sample": MIN_MATURE_SAMPLE,
            "mae_floor_pct": MAE_FLOOR_PCT,
            "heat_cap_risk_pct": HEAT_CAP_RISK_PCT,
            "stop_hit_rate_ceil": STOP_HIT_RATE_CEIL,
        },
        "label_counts": label_counts,
        "cohorts_clean": clean_by_family,
        "cohorts_full": full_by_family,
        "cohorts_by_regime": regime_by_family,
        "graded": graded,
        "ranking": ranked,
        "best_candidate": best,
        "best_candidate_verdict": best_verdict,
        "no_trade_recommendation": no_trade_line,
        "short_side": short_side,
        "short_side_state": short_state,
        "options_expression": options_expression,
        "options_expression_label_counts": opt_labels,
        "current_sleeve_verdicts": sleeve_verdicts,
        "data_limitations": [
            "Single-regime sample: only Bull Continuation / Bull Pullback present "
            "(no risk-off / stress / high-VIX) — short & risk-off families cannot be tested.",
            "No per-event earnings-reaction field — POST_EARNINGS_DRIFT is NOT_ENOUGH_DATA.",
            "No historical options chain — options theoretical P/L is unavailable; "
            "options expression is structure-label only.",
            "rel-return vs QQQ / sector ETF not present per event (only vs SPY).",
            "20d forward outcomes not yet resolved in any row.",
            "Stop/target order unknown when both touched — sim assumes stop first (conservative).",
        ],
        "anti_label_fix": [
            "Immature events are excluded from every pass/fail figure.",
            "BLOCKED / NO_EDGE / NOT_ENOUGH_DATA counts are reported, not hidden.",
            "Gate thresholds are fixed in-module, not tuned on this sample.",
            "Verdicts derive from gates; no verdict is upgraded by renaming.",
            "Single-outlier dependence is tested (drop-best-event recompute).",
        ],
        "recommended_next_phase": _recommended_next_phase(best, best_verdict),
    }


def _recommended_next_phase(best: Optional[str], verdict: str) -> str:
    if verdict == V_PAPER and best:
        return (f"Write a paper spec for {best} (one sleeve in deep validation at a time). "
                "Keep all other sleeves frozen.")
    if verdict == V_DEEPER and best:
        return (f"Run a point-in-time backtest for {best} across regimes before any paper "
                "spec. Stay research-only; do not activate.")
    return ("No family qualifies. Stay research-only: accumulate matured forward "
            "outcomes (esp. a non-bull regime) and re-run the tournament.")


# ── rendering ────────────────────────────────────────────────────────
def render_text(s: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append("=" * 72)
    L.append(f"STRATEGY TOURNAMENT — {s['generated_at'][:19]}  (research-only, Phase 1G.4)")
    L.append("=" * 72)
    L.append(f"spine: {s['event_spine']}  |  snapshots: {s['n_snapshots']}  |  "
             f"regime: {s['headline_regime']}")
    L.append(f"clean epoch >= {s['clean_epoch_start'][:10]}  |  friction {s['friction_round_trip_pct']}%  |  "
             f"next maturity due {s['next_maturity_due']}")
    L.append("")
    L.append("RANKING (clean epoch, eligible events; immature excluded):")
    hdr = f"  {'family':<34}{'verdict':<26}{'n5d':>5}{'exp5d_net':>11}{'relSPY5d':>10}"
    L.append(hdr)
    L.append("  " + "-" * (len(hdr) - 2))
    for r in s["ranking"]:
        L.append(f"  {r['family']:<34}{r['verdict']:<26}{str(r['n_resolved_5d']):>5}"
                 f"{str(r['expectancy_5d_net']):>11}{str(r['mean_rel_spy_5d']):>10}")
    cash = s["cohorts_clean"][F_CASH]
    rnd = s["cohorts_clean"][F_RANDOM]
    L.append(f"  {'CASH_NO_TRADE (baseline)':<34}{'—':<26}{'—':>5}{str(cash['expectancy_5d_net']):>11}{'—':>10}")
    L.append(f"  {'RANDOM_LIQUID_CONTROL (baseline)':<34}{'—':<26}{str(rnd['n_resolved_5d']):>5}"
             f"{str(rnd['expectancy_5d_net']):>11}{str(rnd['mean_rel_spy_5d']):>10}")
    L.append("")
    L.append(f"BEST CANDIDATE: {s['best_candidate']}  →  {s['best_candidate_verdict']}")
    if s["no_trade_recommendation"]:
        L.append(f"  *** {s['no_trade_recommendation']} ***")
    L.append(f"SHORT SIDE: {s['short_side']} (radar state {s['short_side_state']})")
    L.append(f"OPTIONS EXPRESSION: {s['options_expression']}  {s['options_expression_label_counts']}")
    L.append("")
    L.append("PER-FAMILY VERDICTS:")
    for fam, g in s["graded"].items():
        h = g["honesty"]
        L.append(f"  [{g['verdict']}] {fam}")
        L.append(f"      {g['rationale']}")
        L.append(f"      sample={h['sample_size']} caution={h['caution_flag']}")
        L.append(f"      weakness: {h['biggest_weakness']}")
        L.append(f"      falsifier: {h['falsifier']}")
        if g["blockers"]:
            L.append(f"      failed gates: {', '.join(g['blockers'])}")
    L.append("")
    L.append("CURRENT SLEEVE VERDICTS:")
    for r in s["current_sleeve_verdicts"]:
        L.append(f"  {r['sleeve']:<16} {r['disposition']:<28} [{r['registry_status']}]")
        L.append(f"      {r['evidence']}")
    L.append("")
    L.append("DATA LIMITATIONS:")
    for d in s["data_limitations"]:
        L.append(f"  - {d}")
    L.append("")
    L.append(f"RECOMMENDED NEXT PHASE: {s['recommended_next_phase']}")
    L.append("=" * 72)
    return "\n".join(L)


def render_doc(s: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Strategy Tournament — Results (research-only)")
    lines.append("")
    lines.append(f"**Generated:** {s['generated_at'][:19]}  ")
    lines.append(f"**Phase:** {s['phase']} · research-only (no signals, no execution, no live capital)  ")
    lines.append(f"**Event spine:** `{s['event_spine']}` ({s['n_snapshots']} snapshots)  ")
    lines.append(f"**Clean epoch:** ≥ {s['clean_epoch_start'][:10]} · **friction** {s['friction_round_trip_pct']}% round-trip  ")
    lines.append(f"**Headline regime:** {s['headline_regime']} · **next maturity due** {s['next_maturity_due']}")
    lines.append("")
    lines.append("## 1. Executive summary")
    lines.append("")
    if s["no_trade_recommendation"]:
        lines.append(f"> **{s['no_trade_recommendation']}**")
        lines.append("")
    lines.append(f"- **Best candidate:** `{s['best_candidate']}` → **{s['best_candidate_verdict']}**")
    lines.append(f"- **Short side:** {s['short_side']} (radar: {s['short_side_state']})")
    lines.append(f"- **Options expression:** {s['options_expression']}")
    lines.append(f"- **Recommended next phase:** {s['recommended_next_phase']}")
    lines.append("")
    lines.append("## 2. Strategy ranking (clean epoch)")
    lines.append("")
    lines.append("| rank | family | verdict | n(5d) | net 5d | net 10d | rel-SPY 5d | caution |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(s["ranking"], 1):
        lines.append(f"| {i} | {r['family']} | {r['verdict']} | {r['n_resolved_5d']} | "
                     f"{r['expectancy_5d_net']} | {r['expectancy_10d_net']} | "
                     f"{r['mean_rel_spy_5d']} | {r['caution_flag']} |")
    cash = s["cohorts_clean"][F_CASH]
    rnd = s["cohorts_clean"][F_RANDOM]
    lines.append(f"| — | CASH_NO_TRADE (baseline) | — | 0 | {cash['expectancy_5d_net']} | "
                 f"{cash['expectancy_10d_net']} | — | flat |")
    lines.append(f"| — | RANDOM_LIQUID_CONTROL (baseline) | — | {rnd['n_resolved_5d']} | "
                 f"{rnd['expectancy_5d_net']} | {rnd['expectancy_10d_net']} | {rnd['mean_rel_spy_5d']} | control |")
    lines.append("")
    lines.append("## 3. Pass/fail verdicts")
    lines.append("")
    for fam, g in s["graded"].items():
        h = g["honesty"]
        lines.append(f"### {fam} — **{g['verdict']}**")
        lines.append("")
        lines.append(f"{g['rationale']}")
        lines.append("")
        lines.append(f"- sample (resolved 5d): **{h['sample_size']}** · caution: {h['caution_flag']}")
        lines.append(f"- biggest weakness: {h['biggest_weakness']}")
        lines.append(f"- falsifier: {h['falsifier']}")
        lines.append("")
        lines.append("| gate | need | got | pass |")
        lines.append("|---|---|---|---|")
        for gt in g["gates"]:
            lines.append(f"| {gt['gate']} | {gt['need']} | `{gt['got']}` | {'✅' if gt['pass'] else '❌'} |")
        lines.append("")
    lines.append("## 4. Current sleeve verdicts")
    lines.append("")
    lines.append("| sleeve | registry status | disposition | evidence |")
    lines.append("|---|---|---|---|")
    for r in s["current_sleeve_verdicts"]:
        lines.append(f"| {r['sleeve']} | {r['registry_status']} | {r['disposition']} | {r['evidence']} |")
    lines.append("")
    lines.append("## 5. Best next paper candidate")
    lines.append("")
    lines.append(f"{s['recommended_next_phase']}")
    lines.append("")
    lines.append("## 6. Best options expression")
    lines.append("")
    lines.append(f"{s['options_expression']} — label counts across valid-setup events: "
                 f"`{s['options_expression_label_counts']}`. Theoretical option P/L is "
                 "**unavailable** (no historical chain); only structure labels are emitted.")
    lines.append("")
    lines.append("## 7. Short-side readiness")
    lines.append("")
    lines.append(f"Short side: **{s['short_side']}** (radar state `{s['short_side_state']}`). "
                 "No risk-off/stress sample exists in the spine, so short families are "
                 "structurally untestable and stay research-only.")
    lines.append("")
    lines.append("## 8. No-trade / cash recommendation")
    lines.append("")
    lines.append(s["no_trade_recommendation"] or
                 "A candidate cleared the gates — see best candidate above.")
    lines.append("")
    lines.append("## 9. Data limitations")
    lines.append("")
    for d in s["data_limitations"]:
        lines.append(f"- {d}")
    lines.append("")
    lines.append("## 10. Recommended next phase")
    lines.append("")
    lines.append(s["recommended_next_phase"])
    lines.append("")
    lines.append("### Anti-label-fix guarantees")
    lines.append("")
    for a in s["anti_label_fix"]:
        lines.append(f"- {a}")
    lines.append("")
    lines.append("> Research-only. This lab emits no signals, no orders, no trade proposals, "
                 "and changes no registry or live-capital setting.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Strategy Tournament (research-only)")
    p.add_argument("--print", dest="do_print", action="store_true")
    args = p.parse_args(argv)

    s = build_tournament()
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    TXT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DOC_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(s, indent=2, default=str))
    text = render_text(s)
    TXT_OUT.write_text(text + "\n")
    DOC_OUT.write_text(render_doc(s))

    if args.do_print:
        print(text)
    else:
        print(f"strategy_tournament: best={s['best_candidate']} "
              f"verdict={s['best_candidate_verdict']} short={s['short_side']} "
              f"options={s['options_expression']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
