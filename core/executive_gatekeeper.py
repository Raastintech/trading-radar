"""
core/executive_gatekeeper.py — Executive Research Gatekeeper V1.

Research-only final review layer that synthesises existing system outputs
(Stock Lens, Alpha Discovery, Daily Entry Validator, Market Forecast,
options quality, FMP fundamentals cache, portfolio state in db/trading.db)
into a deterministic verdict before a ticker is considered for manual
trading research.

This module:
  * does NOT generate trade signals
  * does NOT execute orders or hedges
  * does NOT use Kelly sizing
  * does NOT promote sleeves
  * does NOT mutate Alpha Discovery / Stock Lens / Sniper / Voyager / Short_A
    / paper governance / execution
  * does NOT call any provider (FMP, Tradier, Alpaca) directly — it reads
    only cache artefacts and the local SQLite DB
  * does NOT let any LLM make the final decision — LLMs may only summarise
    the deterministic evidence; their output cannot mutate the verdict.

Public surface
--------------
  GateVerdict        — Enum-like constants for per-gate verdicts
  GateResult         — dataclass returned by each of the 7 gate functions
  GatekeeperResult   — dataclass with the final aggregated verdict
  build_ticker_state(ticker) -> dict
  run_executive_gatekeeper(ticker, *, with_llm_summary=False, db_path=None)
                     -> GatekeeperResult
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Repo paths — module is import-safe even when env credentials are absent
# (we only reach for paths, never for FMP/Tradier secrets here).
_REPO = Path(__file__).resolve().parent.parent
RESEARCH_CACHE = _REPO / "cache" / "research"
FUND_CACHE = _REPO / "cache" / "fundamentals"
DB_PATH_DEFAULT = _REPO / "db" / "trading.db"


# ──────────────────────────────────────────────────────────────────────────────
# Verdict & dataclasses
# ──────────────────────────────────────────────────────────────────────────────

class GateVerdict:
    """Per-gate verdicts. Severity order: BLOCK > DOWNGRADE > CAUTION > PASS;
    MISSING is reported separately and feeds the data-quality / confidence
    score, never the severity score."""
    PASS = "PASS"
    CAUTION = "CAUTION"
    DOWNGRADE = "DOWNGRADE"
    BLOCK = "BLOCK"
    MISSING = "MISSING"

    SEVERITY = {PASS: 0, CAUTION: 1, DOWNGRADE: 2, BLOCK: 3, MISSING: 0}


class FinalStatus:
    PASS_RESEARCH = "PASS_RESEARCH"
    WATCH = "WATCH"
    BLOCK = "BLOCK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Confidence:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class GateResult:
    name: str
    verdict: str                    # one of GateVerdict.*
    reasons: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def is_blocking(self) -> bool:
        return self.verdict == GateVerdict.BLOCK


@dataclass
class GatekeeperResult:
    ticker: str
    final_status: str               # FinalStatus.*
    confidence: str                 # Confidence.*
    sizing_guidance: str            # plain-English research sizing only
    main_reasons: List[str]
    blocking_reasons: List[str]
    supporting_evidence: List[str]
    risks: List[str]
    hedge_suggestion: Optional[str]
    next_manual_check: List[str]
    gates: List[GateResult]
    data_sources: Dict[str, Any]
    llm_summary: Optional[str]
    generated_at: str
    schema_version: str = "executive_gatekeeper.v1"
    guardrails: List[str] = field(default_factory=lambda: [
        "research-only / not trade approval / not paper evidence / not execution",
        "no provider calls — reads only cached artefacts and local DB",
        "no Kelly sizing; only PASS_RESEARCH / WATCH / BLOCK / INSUFFICIENT_DATA",
        "no Alpha Discovery / Stock Lens / Sniper / Voyager / Short_A / governance / execution mutation",
        "LLM summary is descriptive only; it cannot override or alter the deterministic verdict",
    ])

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["gates"] = [asdict(g) for g in self.gates]
        return d


# ──────────────────────────────────────────────────────────────────────────────
# Cache loaders (best-effort — never raise; missing input → empty dict)
# ──────────────────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        logger.debug("could not load %s", path, exc_info=True)
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def _last_n(rows: Optional[Sequence[Dict[str, Any]]], n: int) -> List[Dict[str, Any]]:
    if not rows:
        return []
    return list(rows[:n])  # FMP cache is already sorted newest-first


def build_ticker_state(ticker: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Best-effort load of every artefact the gatekeeper consumes for a
    ticker. All keys are present (with None / [] when unavailable) so gate
    functions can read defensively without try/except.

    No provider calls.
    """
    t = ticker.upper().strip()
    db = db_path or DB_PATH_DEFAULT

    state: Dict[str, Any] = {
        "ticker": t,
        "stock_lens": _load_json(RESEARCH_CACHE / f"stock_lens_{t}_latest.json"),
        "regime_forecast": _load_json(RESEARCH_CACHE / "regime_forecast_latest.json"),
        "alpha_board": _load_json(RESEARCH_CACHE / "alpha_discovery_board_latest.json"),
        "alpha_overlay": _load_json(RESEARCH_CACHE / "alpha_discovery_overlay_latest.json"),
        "alpha_enrichment": _load_json(RESEARCH_CACHE / "alpha_discovery_enrichment_latest.json"),
        "fundamentals": _load_json(FUND_CACHE / f"{t}.json"),
        "social_arb": _load_json(RESEARCH_CACHE / "social_arb_latest.json"),
        "research_delta": _load_json(RESEARCH_CACHE / "research_delta_latest.json"),
        "portfolio": _load_portfolio_state(t, db),
    }
    # Resolve alpha row for the ticker (board first, then overlay)
    state["alpha_row"] = _alpha_row_for_ticker(t, state["alpha_board"], state["alpha_overlay"])
    return state


def _alpha_row_for_ticker(ticker: str,
                          board: Optional[Dict[str, Any]],
                          overlay: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for src in (board, overlay):
        if not src:
            continue
        for it in src.get("items") or []:
            if str(it.get("ticker", "")).upper() == ticker:
                return it
    return None


def _load_portfolio_state(ticker: str, db_path: Path) -> Dict[str, Any]:
    """Return the local portfolio context: open paper_signals across sleeves
    plus a sector aggregation. Best-effort — empty dict if DB is missing.

    No provider calls; pure SQLite reads on the local file.
    """
    out: Dict[str, Any] = {
        "open_signals": [],
        "open_count_by_sleeve": {},
        "open_count_by_sector": {},
        "ticker_already_open": False,
        "voyager_open": [],
    }
    try:
        if not Path(db_path).exists():
            return out
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        # paper_signals — only "open" rows count as portfolio exposure
        rows = [dict(r) for r in con.execute(
            "SELECT id, sleeve, strategy, ticker, side, sector, status FROM paper_signals "
            "WHERE status='open' ORDER BY logged_at DESC"
        ).fetchall()]
        out["open_signals"] = rows
        for r in rows:
            sl = r.get("sleeve") or "UNKNOWN"
            sec = r.get("sector") or "UNKNOWN"
            out["open_count_by_sleeve"][sl] = out["open_count_by_sleeve"].get(sl, 0) + 1
            out["open_count_by_sector"][sec] = out["open_count_by_sector"].get(sec, 0) + 1
            if str(r.get("ticker", "")).upper() == ticker:
                out["ticker_already_open"] = True
        # Voyager paper signals (separate table)
        try:
            v_rows = [dict(r) for r in con.execute(
                "SELECT id, ticker, direction, archetype FROM voyager_paper_signals "
                "ORDER BY logged_at DESC LIMIT 50"
            ).fetchall()]
            out["voyager_open"] = v_rows
            if any(str(r.get("ticker", "")).upper() == ticker for r in v_rows):
                out["ticker_already_open"] = True
        except Exception:
            pass
        con.close()
    except Exception:
        logger.debug("portfolio state load failed", exc_info=True)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Gate 1 — Entry Quality
# ──────────────────────────────────────────────────────────────────────────────

# DEV / Stock Lens entry-validator state vocabulary (verbatim from upstream).
_DEV_BLOCK_STATES = {"too extended", "broken", "avoid"}
_DEV_DOWNGRADE_STATES = {"watch reclaim", "wait for confirmation", "watch", "neutral"}
_DEV_PASS_STATES = {"actionable now", "buyable", "ready", "actionable"}


def gate_entry_quality(state: Dict[str, Any]) -> GateResult:
    sl = (state.get("stock_lens") or {}).get("layers", {}).get("entry_validator") or {}
    alpha_row = state.get("alpha_row") or {}

    # Prefer Stock Lens entry-validator layer; fall back to Alpha Discovery row.
    view = (sl.get("view") or alpha_row.get("validator_state") or "").strip()
    actionable = sl.get("actionable_now") if "actionable_now" in sl else alpha_row.get("actionable_now")
    score = _safe_float(sl.get("score"))

    evidence = {
        "stock_lens_view": sl.get("view"),
        "stock_lens_actionable_now": sl.get("actionable_now"),
        "alpha_validator_state": alpha_row.get("validator_state"),
        "alpha_action_label": alpha_row.get("action_label"),
        "alpha_validator_flags": alpha_row.get("validator_flags"),
        "stock_lens_entry_quality_score": (state.get("stock_lens") or {}).get("scores", {}).get("entry_quality_score"),
    }

    if not view and actionable is None and not alpha_row:
        return GateResult(
            name="entry_quality",
            verdict=GateVerdict.MISSING,
            reasons=["no Daily Entry Validator state available — Stock Lens and Alpha Discovery both silent"],
            evidence=evidence,
            note="Options activity cannot override a missing entry signal in V1.",
        )

    view_low = view.lower()
    if any(b in view_low for b in _DEV_BLOCK_STATES):
        reasons = [f"Daily Entry Validator state is {view!r} — fresh entry is disqualified"]
        # Surface alpha flags if present, e.g. "7.6% vs EMA20"
        flags = alpha_row.get("validator_flags") or []
        if flags:
            reasons.append(f"alpha flags: {', '.join(flags)}")
        return GateResult(
            name="entry_quality", verdict=GateVerdict.BLOCK, reasons=reasons,
            evidence=evidence,
            note="Options bullish-confirming activity cannot override a bad entry.",
        )

    if any(d in view_low for d in _DEV_DOWNGRADE_STATES):
        return GateResult(
            name="entry_quality", verdict=GateVerdict.DOWNGRADE,
            reasons=[f"Daily Entry Validator says {view!r} — setup needs confirmation before fresh research entry"],
            evidence=evidence,
        )

    if any(p in view_low for p in _DEV_PASS_STATES) or actionable is True:
        return GateResult(
            name="entry_quality", verdict=GateVerdict.PASS,
            reasons=[f"Daily Entry Validator is constructive ({view!r})"],
            evidence=evidence,
        )

    return GateResult(
        name="entry_quality", verdict=GateVerdict.CAUTION,
        reasons=[f"Daily Entry Validator state {view!r} not on the explicit pass-list — caution"],
        evidence=evidence,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Gate 2 — Regime / Sector
# ──────────────────────────────────────────────────────────────────────────────

def _sector_etf_for_ticker(state: Dict[str, Any]) -> Optional[str]:
    sl = state.get("stock_lens") or {}
    if sl.get("sector_etf"):
        return sl["sector_etf"]
    return None


def _sector_row_from_regime(rf: Optional[Dict[str, Any]], etf: Optional[str]) -> Optional[Dict[str, Any]]:
    if not rf or not etf:
        return None
    rows = ((rf.get("sector_rotation") or {}).get("rows")) or []
    for r in rows:
        if str(r.get("sector", "")).upper() == etf.upper():
            return r
    return None


def gate_regime_sector(state: Dict[str, Any]) -> GateResult:
    rf = state.get("regime_forecast") or {}
    sl = state.get("stock_lens") or {}
    headline = rf.get("headline") or {}
    vol = rf.get("volatility") or {}
    sector_layer = (sl.get("layers") or {}).get("sector") or {}
    etf = _sector_etf_for_ticker(state)
    sector_row = _sector_row_from_regime(rf, etf)
    technicals = (sl.get("layers") or {}).get("technicals") or {}

    market_regime = headline.get("current_regime")
    bias_5d = headline.get("bias_5d")
    bias_10d = headline.get("bias_10d")
    vix = _safe_float(vol.get("vix"))
    rs10 = _safe_float(sector_layer.get("rs_vs_spy_10d_pct"))
    rs10_stock = _safe_float(technicals.get("rs_vs_spy_10d_pct"))

    evidence = {
        "market_regime": market_regime,
        "bias_5d": bias_5d, "bias_10d": bias_10d,
        "vix": vix,
        "sector_etf": etf,
        "sector_view": sector_layer.get("view"),
        "sector_rs_vs_spy_10d_pct": rs10,
        "sector_above_ma50": sector_layer.get("above_ma50"),
        "stock_rs_vs_spy_10d_pct": rs10_stock,
    }

    if not rf and not sl:
        return GateResult(name="regime_sector", verdict=GateVerdict.MISSING,
                          reasons=["no regime forecast or Stock Lens artefact found"],
                          evidence=evidence)

    reasons: List[str] = []
    severity = GateVerdict.PASS

    market_bearish = (
        (market_regime and "stress" in market_regime.lower()) or
        (str(bias_5d or "").lower() in {"defensive", "bearish"}) or
        (str(bias_10d or "").lower() in {"defensive", "bearish"}) or
        (vix is not None and vix >= 25)
    )
    if market_bearish:
        reasons.append(
            f"market context risk-off "
            f"(regime={market_regime}, bias_5d={bias_5d}, vix={vix})"
        )
        severity = GateVerdict.DOWNGRADE

    sector_view_l = str(sector_layer.get("view") or "").lower()
    sector_weak = sector_view_l in {"weakening", "lagging", "rolling over"} or (rs10 is not None and rs10 < -1.0)
    if sector_weak:
        # Stock outperforming its weakening sector is a redeeming feature.
        if rs10_stock is not None and rs10_stock > 1.0:
            reasons.append(
                f"sector {etf} weakening (rs10={rs10}) but stock outperforms (rs10_stock={rs10_stock}) — partial offset"
            )
            severity = GateVerdict.CAUTION if severity == GateVerdict.PASS else severity
        else:
            reasons.append(
                f"sector {etf} weakening (rs10={rs10}) and stock not outperforming — downgrade"
            )
            severity = GateVerdict.DOWNGRADE

    if severity == GateVerdict.PASS:
        if etf:
            reasons.append(
                f"market {market_regime} ({bias_5d}/{bias_10d}, vix {vix}); "
                f"sector {etf} {sector_layer.get('view')} (rs10={rs10})"
            )
        else:
            reasons.append(
                f"market {market_regime} ({bias_5d}/{bias_10d}, vix {vix}); "
                f"sector context unknown (no Stock Lens artefact)"
            )

    return GateResult(name="regime_sector", verdict=severity, reasons=reasons, evidence=evidence)


# ──────────────────────────────────────────────────────────────────────────────
# Gate 3 — Options / Whale Quality
# ──────────────────────────────────────────────────────────────────────────────

_OPTIONS_QUALITY_VERDICT = {
    "BULLISH_CONFIRMING": GateVerdict.PASS,
    "BULLISH_BUT_LATE":   GateVerdict.CAUTION,
    "MIXED_OPTIONS":      GateVerdict.CAUTION,
    "BEARISH_HEDGE":      GateVerdict.DOWNGRADE,
    "OPTIONS_NO_EDGE":    GateVerdict.CAUTION,
    "SPECULATIVE_CALL_CHASE": GateVerdict.BLOCK,
}


def gate_options_quality(state: Dict[str, Any]) -> GateResult:
    sl = state.get("stock_lens") or {}
    options = (sl.get("layers") or {}).get("options") or {}
    if not options or not options.get("available"):
        return GateResult(
            name="options_quality", verdict=GateVerdict.MISSING,
            reasons=["no options layer in Stock Lens artefact (Tradier cache empty)"],
            evidence={"options_available": bool(options.get("available")) if options else False},
            note="Treated as 'no edge', not as bullish — call activity alone is not bullish.",
        )

    quality = (options.get("options_quality") or options.get("quality") or "").upper()
    pattern = options.get("pattern")
    oi_tilt = _safe_float(options.get("oi_tilt"))
    vol_tilt = _safe_float(options.get("vol_tilt"))
    iv_skew = _safe_float(options.get("iv_skew"))
    spread = options.get("spread_quality")
    expiry_conf = options.get("expiry_confirmation")
    notes = options.get("notes")
    warning = options.get("options_warning") or options.get("warning")

    verdict = _OPTIONS_QUALITY_VERDICT.get(quality, GateVerdict.CAUTION)
    reasons = [f"options_quality={quality or 'UNKNOWN'} (pattern={pattern}, OI tilt={oi_tilt}, vol tilt={vol_tilt})"]
    if warning:
        reasons.append(f"warning: {warning}")
    if quality == "SPECULATIVE_CALL_CHASE":
        reasons.append("V1 doctrine: speculative call-chase blocks the gate; high call volume alone is not bullish.")
    elif quality == "BULLISH_BUT_LATE":
        reasons.append("OI/volume confirms but late — entry geometry must compensate.")

    return GateResult(
        name="options_quality", verdict=verdict, reasons=reasons,
        evidence={
            "options_quality": quality, "pattern": pattern,
            "oi_tilt": oi_tilt, "vol_tilt": vol_tilt, "iv_skew": iv_skew,
            "spread_quality": spread, "expiry_confirmation": expiry_conf,
            "iv_rank": options.get("iv_rank"),
            "iv_percentile": options.get("iv_percentile"),
            "notes": notes,
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Gate 4 — Fundamental / Moat
# ──────────────────────────────────────────────────────────────────────────────

def _quality_label(score: float) -> str:
    if score >= 0.70:
        return "strong"
    if score >= 0.45:
        return "acceptable"
    if score >= 0.20:
        return "weak"
    return "poor"


def _ttm_sum(rows: Sequence[Dict[str, Any]], key: str) -> Optional[float]:
    vals = [_safe_float(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    if len(vals) < 4:
        return None
    return sum(vals[:4])


def _yoy_growth(rows: Sequence[Dict[str, Any]], key: str) -> Optional[float]:
    """Q vs same-Q-prior-year. Returns growth as a fraction (e.g. 0.12 = +12%)."""
    if len(rows) < 5:
        return None
    cur = _safe_float(rows[0].get(key))
    prev = _safe_float(rows[4].get(key))
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def gate_fundamental_moat(state: Dict[str, Any]) -> GateResult:
    f = state.get("fundamentals") or {}
    income = f.get("income") or []
    balance = f.get("balance") or []
    cashflow = f.get("cashflow") or []

    if not income or not balance or not cashflow:
        return GateResult(
            name="fundamental_moat", verdict=GateVerdict.MISSING,
            reasons=["no FMP fundamentals cache for this ticker"],
            evidence={"income_n": len(income), "balance_n": len(balance), "cashflow_n": len(cashflow)},
            note="V1 does not call FMP from the gatekeeper; cache is the sole source.",
        )

    # ── Profitability: ROE (TTM net income / latest equity), ROIC proxy ─────
    ttm_ni = _ttm_sum(income, "netIncome")
    ttm_op_inc = _ttm_sum(income, "operatingIncome")
    ttm_ebit = _ttm_sum(income, "ebit") or ttm_op_inc
    ttm_revenue = _ttm_sum(income, "revenue")
    ttm_gross = _ttm_sum(income, "grossProfit")
    latest_balance = balance[0]
    eq = _safe_float(latest_balance.get("totalStockholdersEquity")) \
         or _safe_float(latest_balance.get("totalEquity"))
    total_assets = _safe_float(latest_balance.get("totalAssets"))
    total_debt = _safe_float(latest_balance.get("totalDebt")) \
                 or ((_safe_float(latest_balance.get("longTermDebt")) or 0)
                     + (_safe_float(latest_balance.get("shortTermDebt")) or 0))
    total_liab = _safe_float(latest_balance.get("totalLiabilities"))

    roe = (ttm_ni / eq) if (ttm_ni is not None and eq and eq > 0) else None
    invested_capital = (eq or 0) + (total_debt or 0)
    roic = (ttm_ebit / invested_capital) if (ttm_ebit is not None and invested_capital > 0) else None
    debt_to_equity = (total_debt / eq) if (total_debt is not None and eq and eq > 0) else None

    # ── Growth: revenue YoY (Q-over-Q-prior-year) ───────────────────────────
    revenue_yoy = _yoy_growth(income, "revenue")

    # ── Margin trend: gross margin TTM vs gross margin TTM(-4) ──────────────
    gm_now = (ttm_gross / ttm_revenue) if (ttm_gross and ttm_revenue) else None
    gm_prev = None
    if len(income) >= 8:
        prev_rev = _ttm_sum(income[4:], "revenue")
        prev_gross = _ttm_sum(income[4:], "grossProfit")
        if prev_rev and prev_gross:
            gm_prev = prev_gross / prev_rev
    margin_trend = None
    if gm_now is not None and gm_prev is not None:
        margin_trend = gm_now - gm_prev

    # ── FCF / OCF quality ───────────────────────────────────────────────────
    ttm_ocf = _ttm_sum(cashflow, "netCashProvidedByOperatingActivities")
    ttm_capex = _ttm_sum(cashflow, "investmentsInPropertyPlantAndEquipment")
    ttm_fcf = (ttm_ocf - abs(ttm_capex)) if (ttm_ocf is not None and ttm_capex is not None) else None
    fcf_ni_ratio = (ttm_fcf / ttm_ni) if (ttm_fcf is not None and ttm_ni and ttm_ni > 0) else None

    # ── Score components (each component 0..1, then averaged) ───────────────
    components: List[Tuple[str, Optional[float]]] = []

    def _norm(value: Optional[float], lo: float, hi: float, invert: bool = False) -> Optional[float]:
        if value is None:
            return None
        if hi == lo:
            return None
        v = (value - lo) / (hi - lo)
        v = max(0.0, min(1.0, v))
        return 1.0 - v if invert else v

    components.append(("roe", _norm(roe, 0.05, 0.30)))
    components.append(("roic", _norm(roic, 0.05, 0.25)))
    components.append(("debt_to_equity", _norm(debt_to_equity, 0.0, 2.5, invert=True)))
    components.append(("revenue_yoy", _norm(revenue_yoy, -0.05, 0.25)))
    components.append(("margin_trend", _norm(margin_trend, -0.03, 0.05)))
    components.append(("fcf_quality", _norm(fcf_ni_ratio, 0.5, 1.5)))

    used = [(k, v) for k, v in components if v is not None]
    if not used:
        return GateResult(
            name="fundamental_moat", verdict=GateVerdict.MISSING,
            reasons=["FMP cache present but lacks the inputs needed for V1 quality scoring"],
            evidence={"components": dict(components)},
        )

    score = sum(v for _, v in used) / len(used)
    label = _quality_label(score)

    evidence = {
        "components_used": dict(used),
        "components_missing": [k for k, v in components if v is None],
        "ttm_revenue": ttm_revenue,
        "ttm_net_income": ttm_ni,
        "ttm_operating_income": ttm_op_inc,
        "ttm_ocf": ttm_ocf,
        "ttm_fcf_proxy": ttm_fcf,
        "roe": roe, "roic_proxy": roic, "debt_to_equity": debt_to_equity,
        "revenue_yoy_q4_vs_q8": revenue_yoy,
        "gross_margin_ttm": gm_now, "gross_margin_ttm_prev": gm_prev, "margin_trend": margin_trend,
        "fcf_to_netincome_ratio": fcf_ni_ratio,
        "quality_label": label,
        "quality_score": round(score, 3),
    }

    if label == "strong":
        verdict = GateVerdict.PASS
        reasons = [f"fundamental quality strong (score={round(score,2)}: ROE {roe}, ROIC≈{roic}, FCF/NI {fcf_ni_ratio})"]
    elif label == "acceptable":
        verdict = GateVerdict.CAUTION
        reasons = [f"fundamentals acceptable but not standout (score={round(score,2)})"]
    elif label == "weak":
        verdict = GateVerdict.DOWNGRADE
        reasons = [f"fundamentals weak (score={round(score,2)}); profitability or growth signals soft"]
    else:
        verdict = GateVerdict.BLOCK
        reasons = [f"fundamentals poor (score={round(score,2)}); too many components below threshold"]

    return GateResult(name="fundamental_moat", verdict=verdict, reasons=reasons,
                      evidence=evidence,
                      note="V1: no DCF; V1 does not over-rely on point-estimate valuations.")


# ──────────────────────────────────────────────────────────────────────────────
# Gate 5 — Institutional / Insider Context
# ──────────────────────────────────────────────────────────────────────────────

def gate_institutional_insider(state: Dict[str, Any]) -> GateResult:
    """V1 does not call FMP for insider/institutional. We surface only what
    is already in the Stock Lens artefact (`layers.institutional`) and the
    Alpha Discovery sponsorship_score (a coarse 13F-derived signal).
    """
    sl = state.get("stock_lens") or {}
    inst_layer = (sl.get("layers") or {}).get("institutional") or {}
    alpha_row = state.get("alpha_row") or {}

    inst_available = bool(inst_layer.get("available"))
    spons_score = _safe_float(alpha_row.get("sponsorship_score"))
    crowd_pen = _safe_float(alpha_row.get("crowd_penalty"))

    evidence = {
        "stock_lens_institutional_available": inst_available,
        "stock_lens_institutional_view": inst_layer.get("view"),
        "alpha_sponsorship_score": spons_score,
        "alpha_crowd_penalty": crowd_pen,
    }

    if not inst_available and spons_score is None:
        return GateResult(name="institutional_insider", verdict=GateVerdict.MISSING,
                          reasons=["no institutional / insider context in cached artefacts"],
                          evidence=evidence,
                          note="13F is background only — never used as short-term timing.")

    reasons: List[str] = []
    verdict = GateVerdict.CAUTION  # baseline when partial

    # Use sponsorship_score as a coarse 13F-background read (Alpha Discovery scoring).
    if spons_score is not None:
        if spons_score >= 70:
            verdict = GateVerdict.PASS
            reasons.append(f"sponsorship_score={spons_score} (heavy institutional support)")
        elif spons_score >= 50:
            reasons.append(f"sponsorship_score={spons_score} (moderate institutional support)")
        elif spons_score >= 30:
            verdict = GateVerdict.CAUTION
            reasons.append(f"sponsorship_score={spons_score} (light institutional support)")
        else:
            verdict = GateVerdict.DOWNGRADE
            reasons.append(f"sponsorship_score={spons_score} (sparse institutional support)")

    if crowd_pen is not None and crowd_pen >= 2.0:
        # Crowding subtracts; do not block but flag.
        reasons.append(f"crowd_penalty={crowd_pen} — name is crowded; insider/institutional read may be stale")
        if verdict == GateVerdict.PASS:
            verdict = GateVerdict.CAUTION

    if not reasons:
        reasons.append("no actionable institutional / insider read available")

    return GateResult(name="institutional_insider", verdict=verdict, reasons=reasons,
                      evidence=evidence,
                      note="V1: 13F treated as background context only; never short-term timing.")


# ──────────────────────────────────────────────────────────────────────────────
# Gate 6 — Portfolio Risk
# ──────────────────────────────────────────────────────────────────────────────

# Coarse concentration thresholds — research-only, not Kelly.
_SECTOR_OPEN_CAUTION = 2
_SECTOR_OPEN_BLOCK = 4
_TOTAL_OPEN_CAUTION = 6
_TOTAL_OPEN_BLOCK = 10


def gate_portfolio_risk(state: Dict[str, Any]) -> GateResult:
    pf = state.get("portfolio") or {}
    sl = state.get("stock_lens") or {}
    sector_etf = sl.get("sector_etf")
    sector_name = sl.get("sector_name")

    open_signals = pf.get("open_signals") or []
    by_sector = pf.get("open_count_by_sector") or {}
    by_sleeve = pf.get("open_count_by_sleeve") or {}
    total_open = len(open_signals)

    # Count open positions whose sector matches the ticker's sector (case-insensitive
    # exact match). Open positions with unknown / empty sector are NOT inferred to
    # belong to this ticker's sector — that would falsely concentrate every name.
    sector_open = 0
    if sector_name:
        target = sector_name.strip().lower()
        for s, n in by_sector.items():
            if s and str(s).strip().lower() == target:
                sector_open += int(n)
    ticker_already_open = bool(pf.get("ticker_already_open"))

    evidence = {
        "total_open_paper_signals": total_open,
        "open_count_by_sector": by_sector,
        "open_count_by_sleeve": by_sleeve,
        "sector_etf": sector_etf, "sector_name": sector_name,
        "sector_open_count": sector_open,
        "ticker_already_open": ticker_already_open,
        "voyager_open_count": len(pf.get("voyager_open") or []),
    }

    if not pf:
        return GateResult(name="portfolio_risk", verdict=GateVerdict.MISSING,
                          reasons=["could not read portfolio state from db/trading.db"],
                          evidence=evidence)

    if ticker_already_open:
        return GateResult(name="portfolio_risk", verdict=GateVerdict.BLOCK,
                          reasons=[f"{state['ticker']} already has an open paper position; do not stack research"],
                          evidence=evidence)

    if total_open >= _TOTAL_OPEN_BLOCK:
        return GateResult(name="portfolio_risk", verdict=GateVerdict.BLOCK,
                          reasons=[f"total open paper signals = {total_open} ≥ {_TOTAL_OPEN_BLOCK} — concentration block"],
                          evidence=evidence)

    if sector_open >= _SECTOR_OPEN_BLOCK:
        return GateResult(name="portfolio_risk", verdict=GateVerdict.BLOCK,
                          reasons=[f"open positions in sector {sector_name} = {sector_open} ≥ {_SECTOR_OPEN_BLOCK}"],
                          evidence=evidence)

    if total_open >= _TOTAL_OPEN_CAUTION:
        return GateResult(name="portfolio_risk", verdict=GateVerdict.CAUTION,
                          reasons=[f"total open paper signals = {total_open} (≥ {_TOTAL_OPEN_CAUTION}); watch overall exposure"],
                          evidence=evidence)

    if sector_open >= _SECTOR_OPEN_CAUTION:
        return GateResult(name="portfolio_risk", verdict=GateVerdict.CAUTION,
                          reasons=[f"open positions in sector {sector_name} = {sector_open} (≥ {_SECTOR_OPEN_CAUTION}); concentration warning"],
                          evidence=evidence)

    return GateResult(name="portfolio_risk", verdict=GateVerdict.PASS,
                      reasons=[f"portfolio exposure ok (total open {total_open}, sector open {sector_open})"],
                      evidence=evidence)


# ──────────────────────────────────────────────────────────────────────────────
# Gate 7 — Tail Risk / Hedge Suggestion
# ──────────────────────────────────────────────────────────────────────────────

def gate_tail_risk(state: Dict[str, Any]) -> GateResult:
    rf = state.get("regime_forecast") or {}
    sl = state.get("stock_lens") or {}
    vol = rf.get("volatility") or {}
    options = (sl.get("layers") or {}).get("options") or {}

    vix = _safe_float(vol.get("vix"))
    vix_avg20 = _safe_float(vol.get("vix_avg_20"))
    vix_change5 = _safe_float(vol.get("vix_change_5d"))
    vix_above_20ma = (vix is not None and vix_avg20 is not None and vix > vix_avg20)
    iv_skew = _safe_float(options.get("iv_skew"))
    atm_call_iv = _safe_float(options.get("atm_call_iv"))
    atm_put_iv = _safe_float(options.get("atm_put_iv"))

    evidence = {
        "vix": vix, "vix_avg_20": vix_avg20, "vix_change_5d": vix_change5,
        "vix_above_20ma": vix_above_20ma,
        "iv_skew": iv_skew, "atm_call_iv": atm_call_iv, "atm_put_iv": atm_put_iv,
    }

    elevated = (vix is not None and vix >= 22) or vix_above_20ma
    extreme = (vix is not None and vix >= 30)
    iv_elevated = (atm_put_iv is not None and atm_put_iv > 0.40)

    if not vix and not iv_elevated:
        return GateResult(
            name="tail_risk", verdict=GateVerdict.MISSING,
            reasons=["no VIX or option IV available — cannot assess tail-risk environment"],
            evidence=evidence,
        )

    if extreme:
        return GateResult(
            name="tail_risk", verdict=GateVerdict.DOWNGRADE,
            reasons=[f"VIX={vix} ≥ 30 — tail-risk environment is hostile to fresh long research entries"],
            evidence=evidence,
            note="Hedge suggestion: defer entry; if already exposed, consider OTM put protection. NO order placement, NO Kelly sizing.",
        )

    if elevated:
        return GateResult(
            name="tail_risk", verdict=GateVerdict.CAUTION,
            reasons=[f"VIX={vix} elevated (avg20={vix_avg20}, 5d change {vix_change5}); size research conservatively"],
            evidence=evidence,
            note="Hedge idea (research-only): consider a small OTM put or put-spread overlay if conviction is otherwise high. NO order placement.",
        )

    return GateResult(
        name="tail_risk", verdict=GateVerdict.PASS,
        reasons=[f"VIX={vix} (avg20={vix_avg20}); tail-risk environment benign"],
        evidence=evidence,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Aggregator
# ──────────────────────────────────────────────────────────────────────────────

# Severity weights. PASS=0, CAUTION=1, DOWNGRADE=2 each contribute to the
# score; any BLOCK forces the final verdict to BLOCK.
_AGGREGATE_WATCH_THRESHOLD = 3   # cumulative non-block severity → WATCH
_AGGREGATE_PASS_THRESHOLD = 1    # ≤ 1 caution → still PASS_RESEARCH


def _aggregate(gates: Sequence[GateResult]) -> Tuple[str, str, str, str]:
    """Return (final_status, confidence, sizing_guidance, rationale_string)."""
    blockers = [g for g in gates if g.verdict == GateVerdict.BLOCK]
    missing = [g for g in gates if g.verdict == GateVerdict.MISSING]
    cautions = [g for g in gates if g.verdict == GateVerdict.CAUTION]
    downgrades = [g for g in gates if g.verdict == GateVerdict.DOWNGRADE]

    severity_total = sum(GateVerdict.SEVERITY[g.verdict] for g in gates)

    # 1. Hard block — any blocker forces BLOCK.
    if blockers:
        blocker_names = ", ".join(b.name for b in blockers)
        return (
            FinalStatus.BLOCK,
            Confidence.HIGH if not missing else Confidence.MEDIUM,
            "no size — at least one blocking gate fired",
            f"BLOCK fired by: {blocker_names}",
        )

    # 2. Insufficient data — too many missing layers to commit a verdict.
    if len(missing) >= 4:
        return (
            FinalStatus.INSUFFICIENT_DATA,
            Confidence.LOW,
            "no size — insufficient data to grade research",
            f"{len(missing)} of {len(gates)} gates returned MISSING",
        )

    # 3. Watch when cumulative non-block severity is high.
    if severity_total >= _AGGREGATE_WATCH_THRESHOLD or len(downgrades) >= 1 or len(missing) >= 2:
        conf = Confidence.MEDIUM if missing else Confidence.HIGH
        if len(missing) >= 3:
            conf = Confidence.LOW
        return (
            FinalStatus.WATCH,
            conf,
            "small research size only (paper / manual notebook) — conditions are mixed",
            f"WATCH: severity_total={severity_total}, downgrades={len(downgrades)}, "
            f"cautions={len(cautions)}, missing={len(missing)}",
        )

    # 4. Pass with confidence determined by missing-layer count.
    conf = Confidence.HIGH if not missing else (Confidence.MEDIUM if len(missing) == 1 else Confidence.LOW)
    return (
        FinalStatus.PASS_RESEARCH,
        conf,
        "normal research size (paper / manual notebook only — no live capital allocation, no Kelly sizing)",
        f"PASS_RESEARCH: severity_total={severity_total}, missing={len(missing)}",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Optional LLM-summary hook
# ──────────────────────────────────────────────────────────────────────────────

def _deterministic_prose_summary(result: GatekeeperResult) -> str:
    """Default prose summary written without an LLM. Used when
    `with_llm_summary=False` or no LLM is configured."""
    lines = [
        f"{result.ticker} — {result.final_status} (confidence: {result.confidence}).",
        f"Sizing guidance: {result.sizing_guidance}",
        "",
        "Main reasons:",
    ]
    lines += [f"  • {r}" for r in (result.main_reasons or ["—"])]
    if result.blocking_reasons:
        lines.append("")
        lines.append("Blocking reasons:")
        lines += [f"  • {r}" for r in result.blocking_reasons]
    if result.supporting_evidence:
        lines.append("")
        lines.append("Supporting evidence:")
        lines += [f"  • {r}" for r in result.supporting_evidence]
    if result.risks:
        lines.append("")
        lines.append("Risks:")
        lines += [f"  • {r}" for r in result.risks]
    if result.hedge_suggestion:
        lines.append("")
        lines.append(f"Hedge suggestion (research-only, no execution): {result.hedge_suggestion}")
    if result.next_manual_check:
        lines.append("")
        lines.append("Next manual check:")
        lines += [f"  • {r}" for r in result.next_manual_check]
    lines.append("")
    lines.append("Guardrails: " + " | ".join(result.guardrails))
    return "\n".join(lines)


def _try_llm_summary(result: GatekeeperResult) -> Optional[str]:
    """Best-effort: if an Anthropic-style client is present and credentials
    exist, render a plain-English summary. If anything goes wrong, return
    None — the deterministic verdict is unaffected.

    The LLM cannot mutate the result; the caller passes only the
    finalised GatekeeperResult and uses the returned text as a *display
    annotation*. The deterministic verdict is final.
    """
    try:
        import os
        if not os.getenv("ANTHROPIC_API_KEY"):
            return None
        try:
            import anthropic  # type: ignore
        except Exception:
            return None
        client = anthropic.Anthropic()
        prompt_payload = {
            "ticker": result.ticker,
            "final_status_DETERMINISTIC": result.final_status,
            "confidence": result.confidence,
            "sizing_guidance": result.sizing_guidance,
            "main_reasons": result.main_reasons,
            "blocking_reasons": result.blocking_reasons,
            "supporting_evidence": result.supporting_evidence,
            "risks": result.risks,
            "hedge_suggestion": result.hedge_suggestion,
            "gates": [
                {"name": g.name, "verdict": g.verdict, "reasons": g.reasons}
                for g in result.gates
            ],
        }
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=350,
            messages=[{
                "role": "user",
                "content": (
                    "You are a research-only commentator. Summarise the deterministic "
                    "executive-gatekeeper verdict below in plain English (≤200 words). "
                    "You MUST NOT change, override, or argue with the deterministic "
                    "verdict. Do not invent new evidence. Do not recommend orders, "
                    "execution, or sizing beyond what is stated. Begin by restating "
                    "the final_status_DETERMINISTIC verbatim.\n\n"
                    f"Verdict JSON:\n{json.dumps(prompt_payload, indent=2)}"
                ),
            }],
        )
        # Anthropic SDK returns content blocks; concatenate the text parts.
        parts = [b.text for b in getattr(msg, "content", []) if getattr(b, "type", None) == "text"]
        return "\n".join(parts).strip() or None
    except Exception:
        logger.debug("LLM summary failed; falling back to deterministic prose", exc_info=True)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Top-level API
# ──────────────────────────────────────────────────────────────────────────────

GATE_FUNCTIONS = (
    gate_entry_quality,
    gate_regime_sector,
    gate_options_quality,
    gate_fundamental_moat,
    gate_institutional_insider,
    gate_portfolio_risk,
    gate_tail_risk,
)


def _build_summary_lists(gates: Sequence[GateResult]) -> Tuple[List[str], List[str], List[str], List[str], Optional[str], List[str]]:
    main_reasons: List[str] = []
    blocking: List[str] = []
    supporting: List[str] = []
    risks: List[str] = []
    hedge: Optional[str] = None
    next_manual: List[str] = []

    for g in gates:
        line = f"[{g.name} → {g.verdict}] " + "; ".join(g.reasons)
        if g.verdict == GateVerdict.BLOCK:
            blocking.append(line)
            main_reasons.append(line)
        elif g.verdict in (GateVerdict.DOWNGRADE, GateVerdict.CAUTION):
            risks.append(line)
            main_reasons.append(line)
        elif g.verdict == GateVerdict.PASS:
            supporting.append(line)
        elif g.verdict == GateVerdict.MISSING:
            risks.append(line + " (missing data)")
        if g.note:
            if "hedge" in g.note.lower():
                hedge = g.note
            elif "manual" in g.note.lower():
                next_manual.append(g.note)
    return main_reasons, blocking, supporting, risks, hedge, next_manual


def run_executive_gatekeeper(
    ticker: str,
    *,
    with_llm_summary: bool = False,
    db_path: Optional[Path] = None,
) -> GatekeeperResult:
    """Run all gates against the cached state and return the deterministic
    verdict. Optionally attaches an LLM-generated prose summary; the LLM
    summary is descriptive only and cannot modify any other field.
    """
    t = ticker.upper().strip()
    state = build_ticker_state(t, db_path=db_path)
    gates: List[GateResult] = []
    for fn in GATE_FUNCTIONS:
        try:
            gates.append(fn(state))
        except Exception as exc:
            logger.exception("gate %s crashed for %s; recording MISSING", fn.__name__, t)
            gates.append(GateResult(
                name=fn.__name__.replace("gate_", ""),
                verdict=GateVerdict.MISSING,
                reasons=[f"gate raised {type(exc).__name__}: {exc}"],
                evidence={},
            ))

    final_status, confidence, sizing, rationale = _aggregate(gates)
    main_reasons, blocking, supporting, risks, hedge, next_manual = _build_summary_lists(gates)

    # Always include the aggregator rationale at the top of main_reasons so
    # the user sees *why* the deterministic verdict landed where it did.
    main_reasons.insert(0, rationale)

    # Pull next-manual-check hints from Stock Lens if present.
    sl_next = (state.get("stock_lens") or {}).get("next_manual_checks") or []
    if sl_next:
        next_manual.extend([f"(stock_lens) {x}" for x in sl_next])

    result = GatekeeperResult(
        ticker=t,
        final_status=final_status,
        confidence=confidence,
        sizing_guidance=sizing,
        main_reasons=main_reasons,
        blocking_reasons=blocking,
        supporting_evidence=supporting,
        risks=risks,
        hedge_suggestion=hedge,
        next_manual_check=next_manual,
        gates=gates,
        data_sources={
            "stock_lens_present": state.get("stock_lens") is not None,
            "regime_forecast_present": state.get("regime_forecast") is not None,
            "alpha_board_present": state.get("alpha_board") is not None,
            "alpha_row_present": state.get("alpha_row") is not None,
            "fundamentals_present": state.get("fundamentals") is not None,
            "portfolio_db_present": bool(state.get("portfolio")),
        },
        llm_summary=None,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    # LLM summary attaches AFTER the deterministic result is finalised.
    # It is a display annotation only.
    if with_llm_summary:
        result.llm_summary = _try_llm_summary(result)
    if not result.llm_summary:
        result.llm_summary = _deterministic_prose_summary(result)

    return result


__all__ = [
    "GateVerdict", "FinalStatus", "Confidence",
    "GateResult", "GatekeeperResult",
    "build_ticker_state", "run_executive_gatekeeper",
    "gate_entry_quality", "gate_regime_sector", "gate_options_quality",
    "gate_fundamental_moat", "gate_institutional_insider",
    "gate_portfolio_risk", "gate_tail_risk",
]
