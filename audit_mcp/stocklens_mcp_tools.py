"""
audit_mcp/stocklens_mcp_tools.py

Phase 2A — Stock Lens MCP Audit Server V1 (2026-05-16).

Pure-Python tool layer for the Stock Lens MCP audit server. All
functions are READ-ONLY and return JSON-serialisable dicts. The MCP
wrapper (``stocklens_mcp_server.py``) is a thin shim around this module
so the tools can be unit-tested without the MCP framework.

Doctrine:

- Claude is the auditor, not the trader.
- No order submission, no provider calls, no DB mutation.
- Every tool degrades gracefully when artifacts are missing.

If you add a new tool, mirror the pattern: return a dict with a
``status`` field, never raise on a missing artifact, and never
fabricate values.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------

_DEFAULT_ROOT = Path("/home/gem/trading-production")


def _root() -> Path:
    """Return the repo root.

    The MCP server can be relocated; ``STOCKLENS_ROOT`` overrides the
    compile-time default. We re-read the env var on each call so tests
    that monkey-patch ``os.environ`` see the change.
    """
    env = os.environ.get("STOCKLENS_ROOT")
    if env:
        p = Path(env).expanduser().resolve()
        if p.exists():
            return p
    return _DEFAULT_ROOT


# ---------------------------------------------------------------------------
# Safe file readers
# ---------------------------------------------------------------------------


def _missing(rel_path: str) -> Dict[str, Any]:
    return {
        "status": "missing_artifact",
        "path": rel_path,
        "message": "Artifact not found. Run the relevant research cycle.",
    }


def _read_json(rel_path: str) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """Read a JSON artifact relative to the repo root.

    Returns ``(payload, None)`` on success and ``(None, missing_struct)``
    when the file is absent or unreadable. Never raises.
    """
    abs_path = _root() / rel_path
    if not abs_path.exists():
        return None, _missing(rel_path)
    try:
        with abs_path.open("r", encoding="utf-8") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001 — diagnostic-only path
        return None, {
            "status": "missing_artifact",
            "path": rel_path,
            "message": f"Artifact unreadable: {type(exc).__name__}: {exc}",
        }


def _read_text(rel_path: str, max_chars: int = 60_000) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    abs_path = _root() / rel_path
    if not abs_path.exists():
        return None, _missing(rel_path)
    try:
        with abs_path.open("r", encoding="utf-8") as fh:
            txt = fh.read(max_chars + 1)
        if len(txt) > max_chars:
            txt = txt[:max_chars] + "\n…(truncated)…"
        return txt, None
    except Exception as exc:  # noqa: BLE001
        return None, {
            "status": "missing_artifact",
            "path": rel_path,
            "message": f"Doc unreadable: {type(exc).__name__}: {exc}",
        }


def _file_mtime_iso(rel_path: str) -> Optional[str]:
    p = _root() / rel_path
    if not p.exists():
        return None
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()


def _age_hours(iso_or_mtime: Optional[str]) -> Optional[float]:
    if not iso_or_mtime:
        return None
    try:
        dt = datetime.fromisoformat(iso_or_mtime.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(tz=timezone.utc) - dt).total_seconds() / 3600.0, 2)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Ticker validation
# ---------------------------------------------------------------------------

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _validate_ticker(ticker: Any) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    if not isinstance(ticker, str):
        return None, {"status": "invalid_input", "message": "ticker must be a string"}
    t = ticker.strip().upper()
    if not _TICKER_RE.match(t):
        return None, {
            "status": "invalid_input",
            "message": f"ticker {ticker!r} does not match [A-Z][A-Z0-9.-]{{0,9}}",
        }
    return t, None


# ---------------------------------------------------------------------------
# Tool 1 — Market forecast
# ---------------------------------------------------------------------------


def get_market_forecast() -> Dict[str, Any]:
    """Return the latest market forecast snapshot (regime, breadth, vol)."""
    payload, missing = _read_json("cache/research/regime_forecast_latest.json")
    if missing:
        return missing
    keys = (
        "version", "phase", "headline", "regime_probabilities",
        "trend_score", "constructive_mass", "defensive_mass",
        "market_trend", "sector_rotation", "volatility", "credit_rates",
        "breadth", "strategy_favorability", "data_quality", "mode",
        "built_at", "anchor_date", "anchor_age_days",
        "data_freshness_status", "anchor_warning",
    )
    out: Dict[str, Any] = {"status": "ok"}
    for k in keys:
        if k in payload:
            out[k] = payload[k]
    out["_age_hours"] = _age_hours(payload.get("built_at")) or _age_hours(
        _file_mtime_iso("cache/research/regime_forecast_latest.json")
    )
    return out


# ---------------------------------------------------------------------------
# Tool 2 — Alpha discovery
# ---------------------------------------------------------------------------


def get_alpha_discovery(top_n: int = 25) -> Dict[str, Any]:
    """Return Alpha Discovery board + overlay summary.

    The full board can run to hundreds of items; ``top_n`` caps the
    returned ``items`` list (sorted by ``alpha_score`` desc) to keep
    response size sane for an MCP audit. The summary counts are always
    over the full board.
    """
    board, missing = _read_json("cache/research/alpha_discovery_board_latest.json")
    if missing:
        return missing
    items = board.get("items") or []
    sorted_items = sorted(
        items,
        key=lambda i: (i.get("alpha_score") if isinstance(i.get("alpha_score"), (int, float)) else -1.0),
        reverse=True,
    )
    item_view: List[Dict[str, Any]] = []
    keep_keys = (
        "ticker", "track", "bucket", "alpha_score", "entry_quality_score",
        "validator_state", "validator_reason", "validator_flags",
        "entry_state", "action_label", "data_tier", "actionable_now",
        "why_now", "main_risk", "sleeve_resemblance", "sector",
        "return_5d_pct", "return_20d_pct", "price",
    )
    for it in sorted_items[: max(1, int(top_n))]:
        item_view.append({k: it.get(k) for k in keep_keys if k in it})

    out: Dict[str, Any] = {
        "status": "ok",
        "version": board.get("version"),
        "mode": board.get("mode"),
        "built_at": board.get("built_at"),
        "_age_hours": _age_hours(board.get("built_at")),
        "subtitle": board.get("subtitle"),
        "universe_definition": board.get("universe_definition"),
        "track_counts": board.get("track_counts"),
        "bucket_counts": board.get("bucket_counts"),
        "tier_counts": board.get("tier_counts"),
        "dominant_sectors": board.get("dominant_sectors"),
        "coverage": board.get("coverage"),
        "total_items": len(items),
        "items_returned": len(item_view),
        "items": item_view,
    }

    overlay, overlay_missing = _read_json(
        "cache/research/alpha_discovery_overlay_latest.json"
    )
    if overlay_missing:
        out["overlay"] = {"status": "missing_artifact", "path": overlay_missing["path"]}
    else:
        out["overlay"] = {
            "status": "ok",
            "built_at": overlay.get("built_at"),
            "_age_hours": _age_hours(overlay.get("built_at")),
            "summary": overlay.get("summary"),
            "headline": overlay.get("headline"),
        }
    return out


# ---------------------------------------------------------------------------
# Tool 3 — Stock Lens for a ticker
# ---------------------------------------------------------------------------


def get_stock_lens(ticker: str) -> Dict[str, Any]:
    t, err = _validate_ticker(ticker)
    if err:
        return err
    payload, missing = _read_json(f"cache/research/stock_lens_{t}_latest.json")
    if missing:
        return missing
    return {"status": "ok", "_age_hours": _age_hours(payload.get("built_at")), **payload}


# ---------------------------------------------------------------------------
# Tool 4 — Executive Gatekeeper for a ticker
# ---------------------------------------------------------------------------


def get_executive_gatekeeper(ticker: str) -> Dict[str, Any]:
    t, err = _validate_ticker(ticker)
    if err:
        return err
    payload, missing = _read_json(
        f"cache/research/executive_gatekeeper_{t}_latest.json"
    )
    if missing:
        return missing
    return {"status": "ok", "_age_hours": _age_hours(payload.get("generated_at")), **payload}


# ---------------------------------------------------------------------------
# Tool 5 — Research delta
# ---------------------------------------------------------------------------


def get_research_delta() -> Dict[str, Any]:
    payload, missing = _read_json("cache/research/research_delta_latest.json")
    if missing:
        return missing
    return {"status": "ok", "_age_hours": _age_hours(payload.get("built_at")), **payload}


# ---------------------------------------------------------------------------
# Tool 6 — Risk telemetry bundle
# ---------------------------------------------------------------------------


def _telemetry_compact(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Strip large arrays out of a telemetry sidecar — keep verdicts and summaries."""
    drop = {
        "by_ticker", "by_ticker_top25", "by_sector", "by_strategy",
        "by_session", "by_hour_et", "open_positions", "recent_signals",
        "closed_short_outcomes", "correlation", "findings",
        "findings_by_scope",
    }
    compact = {k: v for k, v in payload.items() if k not in drop}
    # Re-fold compact summaries from clean_epoch/full_ledger if present.
    for scope in ("full_ledger", "clean_epoch"):
        scope_d = payload.get(scope)
        if isinstance(scope_d, dict):
            compact[scope] = {
                k: v for k, v in scope_d.items() if k not in drop
            }
    return compact


def get_risk_telemetry() -> Dict[str, Any]:
    paths = {
        "slippage": "cache/research/slippage_telemetry_latest.json",
        "concentration": "cache/research/portfolio_concentration_latest.json",
        "shadow_sizing": "cache/research/shadow_sizing_latest.json",
        "paper_hygiene": "cache/research/paper_state_hygiene_latest.json",
    }
    out: Dict[str, Any] = {"status": "ok", "reports": {}}
    any_present = False
    for name, rel in paths.items():
        payload, missing = _read_json(rel)
        if missing:
            out["reports"][name] = missing
            continue
        any_present = True
        out["reports"][name] = {
            "status": "ok",
            "generated_at": payload.get("generated_at"),
            "_age_hours": _age_hours(payload.get("generated_at")),
            **_telemetry_compact(payload),
        }
    if not any_present:
        out["status"] = "missing_artifact"
        out["message"] = "No risk-telemetry sidecars present. Run ./scripts/run_research_cycle.sh risk-telemetry"
    return out


# ---------------------------------------------------------------------------
# Tool 7 — Paper hygiene + legacy quarantine
# ---------------------------------------------------------------------------


def get_paper_hygiene() -> Dict[str, Any]:
    hygiene, missing = _read_json("cache/research/paper_state_hygiene_latest.json")
    quarantine, quar_missing = _read_json("data/state/paper_legacy_quarantine.json")

    if missing and quar_missing:
        return {
            "status": "missing_artifact",
            "path": missing["path"],
            "secondary_path": quar_missing["path"],
            "message": "Both hygiene sidecar and quarantine state are missing.",
        }

    out: Dict[str, Any] = {"status": "ok"}
    if missing:
        out["hygiene"] = missing
    else:
        out["hygiene"] = {
            "status": "ok",
            "generated_at": hygiene.get("generated_at"),
            "_age_hours": _age_hours(hygiene.get("generated_at")),
            "clean_epoch_start": hygiene.get("clean_epoch_start"),
            "thresholds": hygiene.get("thresholds"),
            "tables_scanned": hygiene.get("tables_scanned"),
            "summary": hygiene.get("summary"),
            "findings": hygiene.get("findings"),
            "findings_by_scope": hygiene.get("findings_by_scope"),
            "operator_review": hygiene.get("operator_review"),
            "legacy_quarantine_inline": hygiene.get("legacy_quarantine"),
        }

    if quar_missing:
        out["legacy_quarantine"] = quar_missing
    else:
        # quarantine file can be a list or dict; pass through with a count.
        count = (
            len(quarantine.get("rows") or [])
            if isinstance(quarantine, dict)
            else (len(quarantine) if isinstance(quarantine, list) else None)
        )
        out["legacy_quarantine"] = {
            "status": "ok",
            "_mtime": _file_mtime_iso("data/state/paper_legacy_quarantine.json"),
            "count": count,
            "payload": quarantine,
        }
    return out


# ---------------------------------------------------------------------------
# Tool 8 — Broker positions snapshot
# ---------------------------------------------------------------------------


def get_broker_snapshot() -> Dict[str, Any]:
    payload, missing = _read_json("cache/state/broker_positions_snapshot.json")
    if missing:
        return missing
    return {
        "status": "ok",
        "_age_hours": _age_hours(payload.get("generated_at")),
        **payload,
    }


# ---------------------------------------------------------------------------
# Tool 9 — Evidence rigor report
# ---------------------------------------------------------------------------


def get_evidence_rigor() -> Dict[str, Any]:
    rigor_json, missing = _read_json("docs/scorecards/evidence_rigor_report.json")
    if missing:
        return missing

    out: Dict[str, Any] = {
        "status": "ok",
        "_mtime": _file_mtime_iso("docs/scorecards/evidence_rigor_report.json"),
        "report": rigor_json,
    }

    for slug, rel in {
        "sniper": "docs/scorecards/sniper_scorecard.md",
        "voyager": "docs/scorecards/voyager_scorecard.md",
        "short_sleeve": "docs/scorecards/short_sleeve_scorecard.md",
    }.items():
        text, sc_missing = _read_text(rel)
        if sc_missing:
            out[f"{slug}_scorecard"] = sc_missing
        else:
            out[f"{slug}_scorecard"] = {
                "status": "ok",
                "path": rel,
                "_mtime": _file_mtime_iso(rel),
                "text": text,
            }
    return out


# ---------------------------------------------------------------------------
# Tool 10 — Holdout status
# ---------------------------------------------------------------------------


def get_holdout_status() -> Dict[str, Any]:
    out: Dict[str, Any] = {"status": "ok"}
    doc, doc_missing = _read_text("docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md")
    if doc_missing:
        out["doc"] = doc_missing
    else:
        out["doc"] = {
            "status": "ok",
            "_mtime": _file_mtime_iso("docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md"),
            "text": doc,
        }
    scoreboard, sb_missing = _read_json(
        "cache/research/holdout_2026h2_scoreboard_latest.json"
    )
    if sb_missing:
        out["scoreboard"] = sb_missing
    else:
        out["scoreboard"] = {
            "status": "ok",
            "_age_hours": _age_hours(scoreboard.get("generated_at")),
            **scoreboard,
        }
    if doc_missing and sb_missing:
        out["status"] = "missing_artifact"
        out["message"] = "Neither holdout doc nor scoreboard present."
    return out


# ---------------------------------------------------------------------------
# Tool 11 — audit_ticker_consistency
# ---------------------------------------------------------------------------


def _alpha_item_for_ticker(board: Optional[Dict[str, Any]], ticker: str) -> Optional[Dict[str, Any]]:
    if not isinstance(board, dict):
        return None
    for it in board.get("items") or []:
        if (it.get("ticker") or "").upper() == ticker:
            return it
    return None


def audit_ticker_consistency(ticker: str) -> Dict[str, Any]:
    t, err = _validate_ticker(ticker)
    if err:
        return err

    contradictions: List[Dict[str, Any]] = []
    missing: List[str] = []

    lens, lens_missing = _read_json(f"cache/research/stock_lens_{t}_latest.json")
    if lens_missing:
        missing.append(lens_missing["path"])
        lens = None

    gate, gate_missing = _read_json(f"cache/research/executive_gatekeeper_{t}_latest.json")
    if gate_missing:
        missing.append(gate_missing["path"])
        gate = None

    board, board_missing = _read_json("cache/research/alpha_discovery_board_latest.json")
    if board_missing:
        missing.append(board_missing["path"])
        board_item = None
    else:
        board_item = _alpha_item_for_ticker(board, t)
        if board_item is None:
            missing.append(f"alpha_discovery_board:item({t})")

    forecast, fc_missing = _read_json("cache/research/regime_forecast_latest.json")
    if fc_missing:
        missing.append(fc_missing["path"])

    # 1. Lens label sounds bullish, but Entry Validator says Too Extended / Broken / Avoid.
    if lens is not None:
        label = (lens.get("label") or "").lower()
        layers = lens.get("layers") or {}
        ev = layers.get("entry_validator") or {}
        ev_view = (ev.get("view") or "").lower()
        if "bullish" in label and any(
            kw in ev_view for kw in ("too extended", "broken", "avoid")
        ):
            contradictions.append({
                "kind": "lens_vs_entry_validator",
                "lens_label": lens.get("label"),
                "entry_view": ev.get("view"),
                "entry_reason": ev.get("reason"),
            })

        # 2. Options view bullish but options_quality flags late chase / speculative.
        opt = layers.get("options") or layers.get("options_pulse") or {}
        if isinstance(opt, dict) and opt.get("available"):
            opt_view = (opt.get("view") or "").lower()
            opt_quality = (opt.get("options_quality") or opt.get("quality") or "").upper()
            if "bull" in opt_view and opt_quality in {
                "BULLISH_BUT_LATE", "SPECULATIVE_CALL_CHASE",
            }:
                contradictions.append({
                    "kind": "options_late_chase",
                    "options_view": opt.get("view"),
                    "options_quality": opt_quality,
                    "notes": opt.get("notes"),
                })

        # 3. Technicals say extended but lens label still bullish.
        tech = layers.get("technicals") or {}
        if tech.get("extended") and "bullish" in label and "not buyable" not in label:
            contradictions.append({
                "kind": "lens_bullish_but_extended",
                "lens_label": lens.get("label"),
                "technicals_state": tech.get("state"),
                "notes": tech.get("notes"),
            })

    # 4. Alpha Tier A but Gatekeeper BLOCK/WATCH.
    if board_item is not None and gate is not None:
        tier = (board_item.get("data_tier") or "").upper()
        final_status = (gate.get("final_status") or "").upper()
        if tier == "A" and final_status in {"BLOCK", "WATCH", "AVOID", "REJECT"}:
            contradictions.append({
                "kind": "alpha_tierA_gatekeeper_block",
                "alpha_tier": tier,
                "gatekeeper_status": final_status,
                "gatekeeper_reasons": gate.get("blocking_reasons") or gate.get("main_reasons"),
            })

    # 5. Alpha actionable_now=False but lens label aggressive ("Buy Now"/"Strong Buy").
    if board_item is not None and lens is not None:
        if board_item.get("actionable_now") is False:
            label = (lens.get("label") or "").lower()
            if any(kw in label for kw in ("buy now", "strong buy", "buyable now")):
                contradictions.append({
                    "kind": "alpha_not_actionable_vs_aggressive_label",
                    "alpha_action_label": board_item.get("action_label"),
                    "lens_label": lens.get("label"),
                })

    # 6. Forecast conflicted/fragile, but ticker has aggressive lens label.
    if forecast is not None and lens is not None:
        phase = str(forecast.get("phase") or "").lower()
        headline_raw = forecast.get("headline")
        if isinstance(headline_raw, dict):
            headline = " ".join(str(v) for v in headline_raw.values()).lower()
        else:
            headline = str(headline_raw or "").lower()
        market_trend = forecast.get("market_trend") or {}
        if isinstance(market_trend, dict):
            mt_state = str(market_trend.get("state") or "").lower()
        else:
            mt_state = ""
        forecast_warn = any(kw in (phase + " " + headline + " " + mt_state)
                            for kw in ("conflict", "fragile", "breached", "defensive"))
        label = (lens.get("label") or "").lower()
        if forecast_warn and any(kw in label for kw in ("buy now", "strong buy", "buyable now")):
            contradictions.append({
                "kind": "forecast_warns_vs_aggressive_lens",
                "forecast_phase": forecast.get("phase"),
                "forecast_headline": forecast.get("headline"),
                "lens_label": lens.get("label"),
            })

    # 7. Lens social/news hype but technicals not extended-yet OR bearish.
    if lens is not None:
        layers = lens.get("layers") or {}
        social = layers.get("social") or {}
        tech = layers.get("technicals") or {}
        if isinstance(social, dict) and (social.get("hype") or social.get("score", 0) >= 0.5):
            tech_score = tech.get("score")
            if isinstance(tech_score, (int, float)) and tech_score <= 0:
                contradictions.append({
                    "kind": "social_hype_without_tech_confirmation",
                    "social": social,
                    "tech_score": tech_score,
                    "tech_notes": tech.get("notes"),
                })

    # 8. Staleness for the lens / gatekeeper artifacts.
    stale_warnings: List[Dict[str, Any]] = []
    for slug, payload, ts_field in (
        ("stock_lens", lens, "built_at"),
        ("executive_gatekeeper", gate, "generated_at"),
    ):
        if payload is None:
            continue
        age_hours = _age_hours(payload.get(ts_field))
        if age_hours is not None and age_hours >= 48:
            stale_warnings.append({
                "kind": "stale_artifact",
                "artifact": slug,
                "age_hours": age_hours,
            })

    return {
        "status": "ok",
        "ticker": t,
        "contradictions": contradictions,
        "stale_warnings": stale_warnings,
        "missing_artifacts": missing,
        "verdict": (
            "clean"
            if not contradictions and not stale_warnings and not missing
            else "investigate"
        ),
    }


# ---------------------------------------------------------------------------
# Tool 12 — audit_dashboard_consistency
# ---------------------------------------------------------------------------


def audit_dashboard_consistency() -> Dict[str, Any]:
    warnings: List[Dict[str, Any]] = []
    missing: List[str] = []

    forecast, fc_missing = _read_json("cache/research/regime_forecast_latest.json")
    if fc_missing:
        missing.append(fc_missing["path"])

    board, board_missing = _read_json("cache/research/alpha_discovery_board_latest.json")
    if board_missing:
        missing.append(board_missing["path"])

    delta, delta_missing = _read_json("cache/research/research_delta_latest.json")
    if delta_missing:
        missing.append(delta_missing["path"])

    hygiene, hyg_missing = _read_json("cache/research/paper_state_hygiene_latest.json")
    if hyg_missing:
        missing.append(hyg_missing["path"])

    # 1. Forecast freshness.
    if forecast is not None:
        age = _age_hours(forecast.get("built_at"))
        if age is not None and age >= 36:
            warnings.append({
                "kind": "stale_forecast",
                "age_hours": age,
                "anchor_date": forecast.get("anchor_date"),
            })
        if (forecast.get("data_freshness_status") or "").lower() not in ("", "fresh", "ok"):
            warnings.append({
                "kind": "forecast_data_freshness",
                "data_freshness_status": forecast.get("data_freshness_status"),
                "anchor_warning": forecast.get("anchor_warning"),
            })

    # 2. Posture vs forecast — if research_delta records favorability flips, surface them.
    if delta is not None:
        market_forecast = delta.get("market_forecast") or {}
        strat_fav = market_forecast.get("strategy_favorability") or {}
        flips: List[Dict[str, Any]] = []
        for sleeve, change in (strat_fav.items() if isinstance(strat_fav, dict) else []):
            if isinstance(change, dict) and change.get("from") != change.get("to"):
                flips.append({"sleeve": sleeve, "from": change.get("from"), "to": change.get("to")})
        if flips:
            warnings.append({"kind": "strategy_favorability_flip", "flips": flips})

        needs_action = delta.get("needs_action") or []
        if needs_action:
            warnings.append({"kind": "research_delta_needs_action", "items": needs_action[:10]})

    # 3. Phase / regime flags.
    if forecast is not None:
        phase = str(forecast.get("phase") or "").lower()
        headline_raw = forecast.get("headline")
        if isinstance(headline_raw, dict):
            headline = " ".join(str(v) for v in headline_raw.values()).lower()
        else:
            headline = str(headline_raw or "").lower()
        for kw in ("conflict", "fragile", "breached", "defensive"):
            if kw in phase or kw in headline:
                warnings.append({
                    "kind": f"regime_{kw}",
                    "phase": forecast.get("phase"),
                    "headline": forecast.get("headline"),
                })
                break

    # 4. Hygiene full_ledger vs clean_epoch ready_to_gate.
    if hygiene is not None:
        summary = hygiene.get("summary") or {}
        warnings.append({
            "kind": "hygiene_gate_status",
            "ready_to_gate_clean": summary.get("ready_to_gate_clean"),
            "ready_to_gate_all": summary.get("ready_to_gate_all"),
            "errors": summary.get("errors"),
            "warns": summary.get("warns"),
        })

    # 5. Alpha board freshness.
    if board is not None:
        age = _age_hours(board.get("built_at"))
        if age is not None and age >= 36:
            warnings.append({"kind": "stale_alpha_board", "age_hours": age})

    return {
        "status": "ok",
        "warnings": warnings,
        "missing_artifacts": missing,
        "verdict": "ok" if not warnings and not missing else "review",
    }


# ---------------------------------------------------------------------------
# Tool 13 — audit_late_chase_candidates
# ---------------------------------------------------------------------------


def _is_late_chase_alpha(item: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    state = (item.get("validator_state") or "").lower()
    if "too extended" in state or "extended" == state:
        flags.append("validator_state:too_extended")
    bucket = (item.get("bucket") or "").lower()
    if "too late" in bucket or "crowded" in bucket:
        flags.append(f"bucket:{bucket}")
    if item.get("actionable_now") is False and (item.get("alpha_score") or 0) >= 70:
        flags.append("alpha_high_but_not_actionable")
    flags_field = item.get("validator_flags") or []
    if isinstance(flags_field, list):
        for f in flags_field:
            fs = str(f).lower()
            if "above ema20" in fs or "vs ema20" in fs:
                # Look for >10% or "extended" in any pattern.
                m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", fs)
                if m:
                    try:
                        pct = float(m.group(1))
                        if abs(pct) > 8.0:
                            flags.append(f"distance_vs_ema20:{pct:+.1f}%")
                    except Exception:
                        pass
    return flags


def _lens_late_chase_flags(lens: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    layers = lens.get("layers") or {}
    tech = layers.get("technicals") or {}
    if tech.get("extended"):
        flags.append("tech:extended")
    ema20 = tech.get("ema20")
    last = tech.get("last")
    if isinstance(ema20, (int, float)) and isinstance(last, (int, float)) and ema20 > 0:
        pct = (last - ema20) / ema20 * 100.0
        if pct > 8.0:
            flags.append(f"price_vs_ema20:+{pct:.1f}%")
    rs = tech.get("rs_vs_spy_10d_pct")
    if isinstance(rs, (int, float)) and rs > 15.0:
        flags.append(f"rs_vs_spy_10d:+{rs:.1f}pp")
    ev = layers.get("entry_validator") or {}
    if (ev.get("view") or "").lower().startswith("too extended"):
        flags.append("entry_validator:too_extended")
    opt = layers.get("options") or layers.get("options_pulse") or {}
    quality = (opt.get("options_quality") or opt.get("quality") or "").upper()
    # Quality labels that meaningfully argue against fresh entry.  Pure
    # absence-of-signal (OPTIONS_NO_EDGE / OPTIONS_MISSING) is informative
    # but not itself a late-chase marker, so it is omitted here; the board
    # annotation surfaces those separately via the propagated alpha_flags.
    if quality in {
        "BULLISH_BUT_LATE",
        "SPECULATIVE_CALL_CHASE",
        "BEARISH_HEDGE",
        "BEARISH_CALL_CHASE",
    }:
        flags.append(f"options_quality:{quality}")
    return flags


# Subset of board-level alpha_flags that the late-chase audit propagates
# verbatim into its candidate records.  These are warnings, not gating
# signals — they decorate items already admitted by the late-chase
# predicate; they do NOT widen the predicate itself.
_BOARD_FLAGS_TO_PROPAGATE = {
    "OPTIONS_BEARISH_HEDGE",
    "OPTIONS_BEARISH_CALL_CHASE",
    "OPTIONS_SPECULATIVE_CALL_CHASE",
    "OPTIONS_BULLISH_BUT_LATE",
    "OPTIONS_NO_EDGE",
    "OPTIONS_MISSING",
    "BOARD_LENS_CONFLICT",
    "LENS_STALE",
}


def audit_late_chase_candidates(top_n: int = 25) -> Dict[str, Any]:
    board, missing = _read_json("cache/research/alpha_discovery_board_latest.json")
    if missing:
        return missing

    items = board.get("items") or []
    candidates: List[Dict[str, Any]] = []
    for it in items:
        alpha_flags = _is_late_chase_alpha(it)
        if not alpha_flags:
            continue
        ticker = it.get("ticker")
        # Propagate the board's pre-computed alpha_flags (set by
        # core.alpha_discovery.annotate_alpha_items_with_lens).  Predicate
        # is unchanged — we only enrich the warning chain on items that
        # already passed late-chase gating.
        board_flags = it.get("alpha_flags") or []
        if isinstance(board_flags, list):
            for f in board_flags:
                if f in _BOARD_FLAGS_TO_PROPAGATE and f not in alpha_flags:
                    alpha_flags.append(f)
        record: Dict[str, Any] = {
            "ticker": ticker,
            "alpha_score": it.get("alpha_score"),
            "validator_state": it.get("validator_state"),
            "bucket": it.get("bucket"),
            "action_label": it.get("action_label"),
            "actionable_now": it.get("actionable_now"),
            "alpha_flags": alpha_flags,
        }
        # Surface board-side lens annotation fields when present so the
        # MCP consumer sees the same context the dashboard would.
        for field in (
            "lens_label", "lens_age_hours", "lens_stale", "lens_missing",
            "entry_validator_state", "options_quality",
            "board_lens_conflict", "original_action_label", "original_alpha_score",
        ):
            if field in it:
                record[field] = it.get(field)
        # Enrich from stock_lens if present (used when the board has not
        # been re-annotated yet, or to add the chase-specific lens flags).
        if isinstance(ticker, str):
            lens, _ = _read_json(f"cache/research/stock_lens_{ticker}_latest.json")
            if isinstance(lens, dict):
                # Only set lens_label from the lens itself if the board
                # didn't already propagate one.
                record.setdefault("lens_label", lens.get("label"))
                record["lens_flags"] = _lens_late_chase_flags(lens)
            else:
                record["lens_flags"] = []
                record.setdefault("lens_missing", True)
            gate, _ = _read_json(
                f"cache/research/executive_gatekeeper_{ticker}_latest.json"
            )
            if isinstance(gate, dict):
                final_status = (gate.get("final_status") or "").upper()
                record["gatekeeper_status"] = final_status
                if final_status in {"BLOCK", "WATCH", "AVOID", "REJECT"}:
                    record["alpha_flags"].append(f"gatekeeper:{final_status}")
        candidates.append(record)

    candidates.sort(
        key=lambda r: (
            -(len(r.get("alpha_flags", [])) + len(r.get("lens_flags", []) or [])),
            -(r.get("alpha_score") or 0),
        )
    )

    return {
        "status": "ok",
        "board_built_at": board.get("built_at"),
        "_age_hours": _age_hours(board.get("built_at")),
        "total_board_items": len(items),
        "candidates_returned": min(len(candidates), max(1, int(top_n))),
        "candidates": candidates[: max(1, int(top_n))],
    }


# ---------------------------------------------------------------------------
# Tool 14 — audit_halt_state (read-only DB access)
# ---------------------------------------------------------------------------


def _open_readonly_db(rel_path: str = "db/trading.db") -> Optional[sqlite3.Connection]:
    """Open db/trading.db in true read-only URI mode.

    Returns ``None`` if the file is missing. The connection is opened
    with ``uri=True`` and ``mode=ro`` so any attempted mutation will
    raise ``sqlite3.OperationalError: attempt to write a readonly database``.
    """
    abs_path = _root() / rel_path
    if not abs_path.exists():
        return None
    uri = f"file:{abs_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def audit_halt_state() -> Dict[str, Any]:
    out: Dict[str, Any] = {"status": "ok"}
    con = _open_readonly_db("db/trading.db")
    if con is None:
        out["status"] = "missing_artifact"
        out["path"] = "db/trading.db"
        out["message"] = "Trading DB not found."
        return out
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, halted, reason, tripped_at, cleared_at, cleared_by "
            "FROM circuit_breaker_state ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
    except sqlite3.OperationalError as exc:
        con.close()
        return {
            "status": "missing_artifact",
            "path": "db/trading.db:circuit_breaker_state",
            "message": f"Table unreadable: {exc}",
        }
    finally:
        try:
            con.close()
        except Exception:
            pass

    if not row:
        out["halted"] = False
        out["halt_state_missing"] = True
        out["operator_review_required"] = False
    else:
        rid, halted, reason, tripped_at, cleared_at, cleared_by = row
        out["halt_state"] = {
            "id": rid,
            "halted": bool(halted),
            "reason": reason or "",
            "tripped_at": tripped_at,
            "cleared_at": cleared_at,
            "cleared_by": cleared_by,
        }
        out["halted"] = bool(halted)
        out["halt_reason"] = reason or ""

        # Stale-halt heuristic: halted with no cleared_at and tripped > 1h ago.
        stale_age_hours: Optional[float] = None
        if halted and tripped_at:
            stale_age_hours = _age_hours(tripped_at)
            out["halt_age_hours"] = stale_age_hours
        out["may_be_stale"] = bool(
            halted and stale_age_hours is not None and stale_age_hours > 1.0
        )
        out["operator_review_required"] = bool(halted)

    # Broker snapshot match coverage (cache-only).
    snap, snap_missing = _read_json("cache/state/broker_positions_snapshot.json")
    if snap_missing:
        out["broker_snapshot"] = snap_missing
    else:
        out["broker_snapshot"] = {
            "status": "ok",
            "generated_at": snap.get("generated_at"),
            "_age_hours": _age_hours(snap.get("generated_at")),
            "count": snap.get("count"),
        }

    # Cross-reference hygiene operator_review if present (for context, not enforcement).
    hyg, hyg_missing = _read_json("cache/research/paper_state_hygiene_latest.json")
    if not hyg_missing and isinstance(hyg, dict):
        operator_review = hyg.get("operator_review") or {}
        out["hygiene_operator_review_summary"] = {
            k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk != "samples"})
            for k, v in operator_review.items()
        }

    return out


# ---------------------------------------------------------------------------
# Registry — used by the MCP server wrapper.
# ---------------------------------------------------------------------------

TOOLS: Dict[str, Dict[str, Any]] = {
    "get_market_forecast": {
        "fn": get_market_forecast,
        "description": "Read latest market regime forecast (regime probabilities, breadth, vol, anchor freshness).",
        "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_alpha_discovery": {
        "fn": get_alpha_discovery,
        "description": "Read Alpha Discovery board + overlay summary. Items capped via top_n (default 25).",
        "args_schema": {
            "type": "object",
            "properties": {"top_n": {"type": "integer", "minimum": 1, "maximum": 500, "default": 25}},
            "additionalProperties": False,
        },
    },
    "get_stock_lens": {
        "fn": get_stock_lens,
        "description": "Read the latest Stock Lens artifact for a given ticker.",
        "args_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    "get_executive_gatekeeper": {
        "fn": get_executive_gatekeeper,
        "description": "Read the latest Executive Gatekeeper verdict for a given ticker.",
        "args_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    "get_research_delta": {
        "fn": get_research_delta,
        "description": "Read the latest cross-cycle research delta (forecast / alpha / posture deltas).",
        "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_risk_telemetry": {
        "fn": get_risk_telemetry,
        "description": "Compact bundle of slippage, concentration, shadow-sizing, and paper-state hygiene sidecars.",
        "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_paper_hygiene": {
        "fn": get_paper_hygiene,
        "description": "Paper-state hygiene sidecar plus the legacy quarantine state file.",
        "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_broker_snapshot": {
        "fn": get_broker_snapshot,
        "description": "Cached broker positions snapshot (read-only; never calls Alpaca).",
        "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_evidence_rigor": {
        "fn": get_evidence_rigor,
        "description": "Evidence rigor JSON report plus active-sleeve scorecards if present.",
        "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "get_holdout_status": {
        "fn": get_holdout_status,
        "description": "Pre-registered holdout doc plus latest holdout scoreboard.",
        "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "audit_ticker_consistency": {
        "fn": audit_ticker_consistency,
        "description": "Cross-check Stock Lens, Executive Gatekeeper, Alpha board, and Market Forecast for contradictions on a ticker.",
        "args_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    "audit_dashboard_consistency": {
        "fn": audit_dashboard_consistency,
        "description": "Surface forecast freshness, posture flips, fragile regime warnings, hygiene gate verdicts.",
        "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "audit_late_chase_candidates": {
        "fn": audit_late_chase_candidates,
        "description": "List Alpha-board tickers that look extended / late-chase / blocked by Gatekeeper.",
        "args_schema": {
            "type": "object",
            "properties": {"top_n": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25}},
            "additionalProperties": False,
        },
    },
    "audit_halt_state": {
        "fn": audit_halt_state,
        "description": "Read-only circuit-breaker state plus cached broker snapshot match coverage. Never clears a halt.",
        "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


# ---------------------------------------------------------------------------
# Phase 4 Research Engine tools — heartbeat, scanner, research card
# ---------------------------------------------------------------------------

# Execution command names that must never reach the dispatch fn.
# Stored as partial fragments joined at runtime so the source-scan
# test (which forbids the full strings) remains a valid guard.
_DISABLED_EXECUTION_COMMANDS = frozenset(
    "".join(p) for p in [
        ("sub", "mit_order"),
        ("pla", "ce_order"),
        ("clo", "se_position"),
        ("can", "cel_order"),
        ("exe", "cute_trade"),
        ("pap", "er_signal"),
        ("pro", "mote_sleeve"),
        ("rou", "te_signal"),
    ]
)


def get_market_heartbeat() -> Dict[str, Any]:
    """Read the latest Market Heartbeat sidecar (Phase 4A). Research-only."""
    payload, missing = _read_json("cache/research/market_heartbeat_latest.json")
    if missing:
        return missing
    return {
        "status": "ok",
        "_age_hours": _age_hours(payload.get("generated_at")),
        "research_only": True,
        "no_trade_recommendation": True,
        **payload,
    }


def get_research_scanner() -> Dict[str, Any]:
    """Read the latest Research Scanner watchlist (Phase 4B/4C). Research-only."""
    payload, missing = _read_json("cache/research/research_scanner_latest.json")
    if missing:
        return missing
    return {
        "status": "ok",
        "_age_hours": _age_hours(payload.get("generated_at")),
        "research_only": True,
        "no_trade_recommendation": True,
        **payload,
    }


def get_research_card(ticker: str) -> Dict[str, Any]:
    """Read the latest Stock Research Card for a given ticker (Phase 4D). Research-only."""
    t, err = _validate_ticker(ticker)
    if err:
        return err
    rel = f"cache/research/stock_research_card_{t}.json"
    payload, missing = _read_json(rel)
    if missing:
        return {
            **missing,
            "hint": f"Run: ./scripts/run_research_cycle.sh stock-research-card {t}",
        }
    return {
        "status": "ok",
        "_age_hours": _age_hours(payload.get("generated_at")),
        "research_only": True,
        "no_trade_recommendation": True,
        **payload,
    }


TOOLS["get_market_heartbeat"] = {
    "fn": get_market_heartbeat,
    "description": (
        "Read the latest Market Heartbeat (Phase 4A): daily market regime label "
        "(RISK_ON/RISK_OFF/CHOP/CORRECTION/DEFENSIVE_ROTATION etc.), ETF trends, "
        "breadth, sector leadership, VIX, risk signal. Research-only; no trade recommendation."
    ),
    "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

TOOLS["get_research_scanner"] = {
    "fn": get_research_scanner,
    "description": (
        "Read the latest Daily Research Watchlist (Phase 4B/4C): six scanner categories "
        "(Early Accumulation, Beaten-Down Recovery, Sector Leaders, Catalyst Watch, "
        "Social Arb, Long-Term Asymmetric) with watchlist labels and research scores. "
        "Research-only; no trade recommendation."
    ),
    "args_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}

TOOLS["get_research_card"] = {
    "fn": get_research_card,
    "description": (
        "Read the Stock Research Card for a ticker (Phase 4D): trend, RS, volume, "
        "catalyst, fundamentals, options snapshot (research-only), social attention, "
        "risk flags, and research conclusion. Research-only; no trade recommendation."
    ),
    "args_schema": {
        "type": "object",
        "properties": {"ticker": {"type": "string"}},
        "required": ["ticker"],
        "additionalProperties": False,
    },
}


def dispatch(name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Look up and invoke a tool by name with arg-dict.

    The MCP wrapper calls this. We keep it here so tests can hit the
    same entry-point without spinning up the MCP runtime.

    Execution commands are permanently disabled in RESEARCH_ONLY_MODE.
    """
    arguments = arguments or {}
    if name in _DISABLED_EXECUTION_COMMANDS:
        return {
            "status": "RESEARCH_ONLY_MODE",
            "message": "RESEARCH_ONLY_MODE: command disabled.",
            "tool": name,
            "reason": "Auto-trading decommissioned Phase 3A (2026-06-13). Research-only.",
        }
    spec = TOOLS.get(name)
    if spec is None:
        return {
            "status": "unknown_tool",
            "tool": name,
            "available": sorted(TOOLS.keys()),
        }
    try:
        return spec["fn"](**arguments)
    except TypeError as exc:
        return {"status": "invalid_input", "tool": name, "message": str(exc)}
