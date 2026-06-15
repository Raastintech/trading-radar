"""
research/mcp_audit_workflows.py — Phase 2B MCP audit workflow runner.

Composes the read-only helpers in ``audit_mcp.stocklens_mcp_tools`` into
four named workflows and writes JSON + text sidecars. The MCP server
exposes the underlying tools one at a time; this runner bundles them
into the audit *workflows* the operator runs on a cadence (and the
docs/ops/MCP_AUDIT_WORKFLOWS.md doctrine describes).

Workflows:
  - daily_dashboard_audit  → cache/research/mcp_audit_daily_latest.{json,txt}
  - late_chase_audit       → cache/research/mcp_audit_late_chase_latest.{json,txt}
  - system_health_audit    → cache/research/mcp_audit_system_health_latest.{json,txt}
  - ticker_consistency_audit(TICKER)
        → cache/research/mcp_audit_<TICKER>_latest.{json,txt}

Guardrails (must all hold):
  - Read-only. Never mutates the DB, never writes to existing core
    artifacts (stock_lens / alpha / gatekeeper / paper hygiene /
    evidence / quarantine). Only writes the dedicated mcp_audit_*
    sidecars.
  - Cache-only. No provider imports (alpaca, fmp, yfinance). No
    execution imports (execution.*, order managers).
  - Composes existing helpers; does not re-implement audit logic.
  - Degrades gracefully when artifacts are missing — the underlying
    helpers already return a ``missing_artifact`` shape that we just
    pass through.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# We import the existing read-only audit helpers. This is intentionally
# the only audit-logic dependency — no provider, broker, execution, or
# governance imports allowed in this module.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_mcp import stocklens_mcp_tools as t  # noqa: E402


# ── Paths ─────────────────────────────────────────────────────────────

def _repo_root() -> Path:
    """Mirror ``audit_mcp.stocklens_mcp_tools._root()`` so we write the
    sidecars next to the artifacts the helpers read."""
    env = os.environ.get("STOCKLENS_ROOT")
    if env:
        p = Path(env).expanduser().resolve()
        if p.exists():
            return p
    return Path(__file__).resolve().parents[1]


def _cache_dir() -> Path:
    return _repo_root() / "cache" / "research"


def _log_dir() -> Path:
    return _repo_root() / "logs"


def _sidecar_paths(slug: str) -> Tuple[Path, Path]:
    """Return (json_path, txt_path) for a workflow slug.

    The slug is namespaced under ``mcp_audit_`` so these sidecars never
    collide with the existing research artifacts (lens, gatekeeper,
    hygiene, etc.)."""
    json_path = _cache_dir() / f"mcp_audit_{slug}_latest.json"
    txt_path  = _log_dir()   / f"mcp_audit_{slug}_latest.txt"
    return json_path, txt_path


# ── Workflow: daily dashboard audit ───────────────────────────────────

def daily_dashboard_audit() -> Dict[str, Any]:
    """Compose: market forecast, dashboard consistency, halt state, paper
    hygiene, risk telemetry — into one bundle.

    The bundle preserves each tool's response under a stable key so
    downstream readers can rely on the existing tool contracts."""
    return {
        "status":              "ok",
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "workflow":            "daily_dashboard_audit",
        "market_forecast":     t.get_market_forecast(),
        "dashboard_audit":     t.audit_dashboard_consistency(),
        "halt_state":          t.audit_halt_state(),
        "paper_hygiene":       t.get_paper_hygiene(),
        "risk_telemetry":      t.get_risk_telemetry(),
    }


# ── Workflow: late chase audit ────────────────────────────────────────

def late_chase_audit(top_n: int = 25) -> Dict[str, Any]:
    """Wrap audit_late_chase_candidates with bucketing for the report."""
    raw = t.audit_late_chase_candidates(top_n=top_n)
    out: Dict[str, Any] = {
        "status":       raw.get("status", "ok"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow":     "late_chase_audit",
        "raw":          raw,
    }
    if raw.get("status") != "ok":
        return out

    extended:        List[Dict[str, Any]] = []
    watch_reclaim:   List[Dict[str, Any]] = []
    blocked:         List[Dict[str, Any]] = []
    broken:          List[Dict[str, Any]] = []
    missing_lens:    List[str]            = []

    for c in raw.get("candidates") or []:
        ticker = c.get("ticker")
        state = (c.get("validator_state") or "").lower()
        if c.get("lens_missing"):
            if isinstance(ticker, str):
                missing_lens.append(ticker)
        if (c.get("gatekeeper_status") or "").upper() in {"BLOCK", "AVOID", "REJECT"}:
            blocked.append(c)
        if "too extended" in state or state == "extended":
            extended.append(c)
        elif "broken" in state or "avoid" in state:
            broken.append(c)
        elif "watch reclaim" in state or "watch" in state:
            watch_reclaim.append(c)

    out["buckets"] = {
        "extended":      extended,
        "watch_reclaim": watch_reclaim,
        "blocked":       blocked,
        "broken":        broken,
        "missing_lens":  sorted(set(missing_lens)),
    }
    out["counts"] = {
        "candidates":    len(raw.get("candidates") or []),
        "extended":      len(extended),
        "watch_reclaim": len(watch_reclaim),
        "blocked":       len(blocked),
        "broken":        len(broken),
        "missing_lens":  len(set(missing_lens)),
    }
    return out


# ── Workflow: ticker consistency audit ────────────────────────────────

def ticker_consistency_audit(ticker: str) -> Dict[str, Any]:
    """Compose audit_ticker_consistency + stock_lens + executive_gatekeeper
    for a single ticker. Each helper validates the ticker independently;
    we surface any input error verbatim."""
    out: Dict[str, Any] = {
        "status":       "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow":     "ticker_consistency_audit",
        "ticker":       ticker,
    }
    consistency = t.audit_ticker_consistency(ticker)
    if consistency.get("status") == "invalid_input":
        return {**out, "status": "invalid_input", "message": consistency.get("message")}
    out["consistency"]         = consistency
    out["stock_lens"]          = t.get_stock_lens(ticker)
    out["executive_gatekeeper"] = t.get_executive_gatekeeper(ticker)
    # Normalise the ticker for the file name only after the helpers have
    # validated the input.
    out["ticker"] = consistency.get("ticker") or str(ticker).strip().upper()
    return out


# ── Workflow: system health audit ─────────────────────────────────────

def system_health_audit() -> Dict[str, Any]:
    """Compose halt state, paper hygiene, risk telemetry, broker snapshot
    into a single "is the platform safe to keep observing" view.

    The verdict ``safe_for_paper_observation`` is a derived flag — it is
    *not* a gate, it is a published audit verdict (mirrors Phase 1D
    ready_to_gate doctrine)."""
    halt        = t.audit_halt_state()
    hygiene     = t.get_paper_hygiene()
    telemetry   = t.get_risk_telemetry()
    broker_snap = t.get_broker_snapshot()

    bundle = {
        "status":          "ok",
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "workflow":        "system_health_audit",
        "halt_state":      halt,
        "paper_hygiene":   hygiene,
        "risk_telemetry":  telemetry,
        "broker_snapshot": broker_snap,
    }

    # Derived verdicts — read-only, never enforced.
    hyg_payload = (hygiene or {}).get("hygiene")
    if isinstance(hyg_payload, dict):
        summary = hyg_payload.get("summary") or {}
        ready_to_gate_clean = bool(summary.get("ready_to_gate_clean"))
        ready_to_gate_all   = bool(summary.get("ready_to_gate_all"))
        findings = hyg_payload.get("findings") or []
        active_drift = any(
            f.get("code") == "RECONCILER_DRIFT_ACTIVE" for f in findings
        )
        unknown_drift = any(
            f.get("code") == "RECONCILER_DRIFT_UNKNOWN" for f in findings
        )
    else:
        ready_to_gate_clean = False
        ready_to_gate_all   = False
        active_drift        = False
        unknown_drift       = False

    halted = bool((halt or {}).get("halted"))

    bundle["verdict"] = {
        "halted":                       halted,
        "ready_to_gate_clean":          ready_to_gate_clean,
        "ready_to_gate_all":            ready_to_gate_all,
        "active_drift":                 active_drift,
        "unknown_drift":                unknown_drift,
        "safe_for_paper_observation":   (
            not halted
            and not active_drift
            and not unknown_drift
            and ready_to_gate_clean
        ),
    }
    return bundle


# ── Rendering ─────────────────────────────────────────────────────────

def _bar(c: str = "─", n: int = 80) -> str:
    return c * n


def _missing_or(field: Any, default: str = "n/a") -> str:
    if field is None or field == "":
        return default
    return str(field)


def _render_market_state(forecast: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    if forecast.get("status") != "ok":
        out.append(f"market forecast: {forecast.get('status')} ({forecast.get('path', '')})")
        return out
    headline = forecast.get("headline") or {}
    if isinstance(headline, dict):
        regime = headline.get("current_regime") or headline.get("regime")
        bias5  = headline.get("bias_5d")
        bias10 = headline.get("bias_10d")
        conf   = headline.get("confidence")
        out.append(
            f"regime         {_missing_or(regime)}  "
            f"bias5={_missing_or(bias5)}  bias10={_missing_or(bias10)}  "
            f"conf={_missing_or(conf)}"
        )
        if headline.get("invalidation_breached"):
            reasons = headline.get("invalidation_breach_reasons") or []
            out.append(f"invalidation   BREACHED — {', '.join(map(str, reasons))}")
    age = forecast.get("_age_hours")
    if age is not None:
        out.append(f"forecast age   {age:.2f}h")
    return out


def render_text_daily(report: Dict[str, Any]) -> str:
    lines: List[str] = [
        _bar(),
        "MCP AUDIT — daily_dashboard_audit (cache-only, read-only)",
        f"generated_at={report.get('generated_at')}",
        _bar(),
    ]
    lines.append("[market state]")
    lines.extend(_render_market_state(report.get("market_forecast") or {}))

    lines.append("")
    lines.append("[dashboard audit]")
    dash = report.get("dashboard_audit") or {}
    lines.append(f"verdict        {dash.get('verdict')}")
    for w in dash.get("warnings") or []:
        lines.append(f"  - {w.get('kind')}: {json.dumps({k: v for k, v in w.items() if k != 'kind'}, default=str)}")
    if dash.get("missing_artifacts"):
        lines.append("missing:")
        for m in dash["missing_artifacts"]:
            lines.append(f"  - {m}")

    lines.append("")
    lines.append("[halt state]")
    halt = report.get("halt_state") or {}
    lines.append(
        f"halted={halt.get('halted')}  reason={_missing_or(halt.get('halt_reason'))}  "
        f"operator_review={halt.get('operator_review_required')}"
    )
    snap = halt.get("broker_snapshot") or {}
    if snap.get("status") == "ok":
        lines.append(
            f"broker snap    age={snap.get('_age_hours')}h  count={snap.get('count')}"
        )
    elif snap:
        lines.append(f"broker snap    {snap.get('status', 'missing')}")

    lines.append("")
    lines.append("[paper hygiene]")
    hyg_outer = report.get("paper_hygiene") or {}
    hyg = hyg_outer.get("hygiene") or {}
    summary = hyg.get("summary") or {}
    lines.append(
        f"ready_to_gate_clean={summary.get('ready_to_gate_clean')}  "
        f"ready_to_gate_all={summary.get('ready_to_gate_all')}  "
        f"errors={summary.get('errors')}  warns={summary.get('warns')}  "
        f"infos={summary.get('infos')}"
    )
    for f in hyg.get("findings") or []:
        if f.get("scope") != "clean":
            continue
        lines.append(
            f"  [clean·{f.get('severity')}] {f.get('code')} n={f.get('count')}"
        )

    lines.append("")
    lines.append("[risk telemetry]")
    rt = (report.get("risk_telemetry") or {}).get("reports") or {}
    for name in ("slippage", "concentration", "shadow_sizing", "paper_hygiene"):
        r = rt.get(name) or {}
        if r.get("status") == "ok":
            lines.append(f"  {name:<14} age={r.get('_age_hours')}h")
        else:
            lines.append(f"  {name:<14} {r.get('status', 'missing')}")

    lines.append("")
    lines.append("[top concerns]")
    concerns = _top_concerns(report)
    if not concerns:
        lines.append("  none")
    else:
        for c in concerns:
            lines.append(f"  - {c}")

    lines.append(_bar())
    return "\n".join(lines)


def _top_concerns(report: Dict[str, Any]) -> List[str]:
    """Distill a short list of operator concerns from the bundle. Same
    facts the human would read off the longer panels — just the headline
    line for each."""
    concerns: List[str] = []
    halt = report.get("halt_state") or {}
    if halt.get("halted"):
        concerns.append(f"HALTED: {halt.get('halt_reason')}")
    forecast = report.get("market_forecast") or {}
    head = forecast.get("headline") or {}
    if isinstance(head, dict) and head.get("invalidation_breached"):
        reasons = head.get("invalidation_breach_reasons") or []
        concerns.append(f"forecast invalidation breached: {', '.join(map(str, reasons))}")
    dash = report.get("dashboard_audit") or {}
    for w in dash.get("warnings") or []:
        kind = w.get("kind")
        if kind == "hygiene_gate_status":
            if not w.get("ready_to_gate_clean"):
                concerns.append(
                    f"hygiene clean gate FALSE  errors={w.get('errors')} warns={w.get('warns')}"
                )
        elif kind in ("stale_forecast", "stale_alpha_board"):
            concerns.append(f"{kind} age_hours={w.get('age_hours')}")
        elif kind == "research_delta_needs_action":
            items = w.get("items") or []
            for it in items[:3]:
                concerns.append(f"research_delta: {it}")
        elif kind == "forecast_data_freshness":
            concerns.append(
                f"forecast freshness: {w.get('data_freshness_status')} "
                f"({w.get('anchor_warning')})"
            )
        elif kind == "strategy_favorability_flip":
            concerns.append(f"favorability flips: {w.get('flips')}")
    hyg_outer = report.get("paper_hygiene") or {}
    hyg = (hyg_outer.get("hygiene") or {}) if isinstance(hyg_outer, dict) else {}
    for f in (hyg.get("findings") or []):
        if f.get("scope") == "clean" and f.get("severity") in ("ERROR", "WARN"):
            concerns.append(
                f"hygiene clean {f.get('severity')}: {f.get('code')} n={f.get('count')}"
            )
    return concerns


def render_text_late_chase(report: Dict[str, Any]) -> str:
    lines: List[str] = [
        _bar(),
        "MCP AUDIT — late_chase_audit (cache-only, read-only)",
        f"generated_at={report.get('generated_at')}",
        _bar(),
    ]
    raw = report.get("raw") or {}
    if raw.get("status") != "ok":
        lines.append(f"status: {raw.get('status')} ({raw.get('path', '')})")
        lines.append(_bar())
        return "\n".join(lines)
    counts = report.get("counts") or {}
    lines.append(
        f"board_age={raw.get('_age_hours')}h  candidates={counts.get('candidates')}  "
        f"extended={counts.get('extended')}  watch={counts.get('watch_reclaim')}  "
        f"blocked={counts.get('blocked')}  broken={counts.get('broken')}  "
        f"missing_lens={counts.get('missing_lens')}"
    )
    buckets = report.get("buckets") or {}

    def _row(label: str, items: List[Dict[str, Any]]) -> None:
        if not items:
            return
        lines.append("")
        lines.append(f"[{label}]")
        for c in items:
            ticker = c.get("ticker")
            alpha  = c.get("alpha_score")
            flags  = ",".join((c.get("alpha_flags") or []) + (c.get("lens_flags") or []))
            lines.append(f"  {ticker:<6} alpha={alpha}  {flags}")

    _row("extended / late-chase", buckets.get("extended") or [])
    _row("watch reclaim",         buckets.get("watch_reclaim") or [])
    _row("blocked",               buckets.get("blocked") or [])
    _row("broken / avoid",        buckets.get("broken") or [])

    if buckets.get("missing_lens"):
        lines.append("")
        lines.append("[missing lens]")
        lines.append("  " + ", ".join(buckets["missing_lens"]))

    lines.append(_bar())
    return "\n".join(lines)


def render_text_ticker(report: Dict[str, Any]) -> str:
    t_sym = report.get("ticker")
    lines: List[str] = [
        _bar(),
        f"MCP AUDIT — ticker_consistency_audit ({t_sym}) (cache-only, read-only)",
        f"generated_at={report.get('generated_at')}",
        _bar(),
    ]
    if report.get("status") != "ok":
        lines.append(f"status: {report.get('status')}  message: {report.get('message')}")
        lines.append(_bar())
        return "\n".join(lines)

    cons = report.get("consistency") or {}
    lines.append(f"verdict: {cons.get('verdict')}")
    contras = cons.get("contradictions") or []
    if contras:
        lines.append("contradictions:")
        for c in contras:
            lines.append(f"  - {c.get('kind')}: {json.dumps({k: v for k, v in c.items() if k != 'kind'}, default=str)}")
    stale = cons.get("stale_warnings") or []
    if stale:
        lines.append("stale artifacts:")
        for s in stale:
            lines.append(f"  - {s.get('artifact')}: age_hours={s.get('age_hours')}")
    missing = cons.get("missing_artifacts") or []
    if missing:
        lines.append("missing artifacts:")
        for m in missing:
            lines.append(f"  - {m}")

    lens = report.get("stock_lens") or {}
    if lens.get("status") == "ok":
        layers = lens.get("layers") or {}
        ev = layers.get("entry_validator") or {}
        opt = layers.get("options") or layers.get("options_pulse") or {}
        lines.append("")
        lines.append("[stock lens]")
        lines.append(f"  label={lens.get('label')}  age={lens.get('_age_hours')}h")
        if ev:
            lines.append(f"  entry: {ev.get('view')} — {ev.get('reason')}")
        if opt and opt.get("available"):
            lines.append(f"  options: {opt.get('view')}  quality={opt.get('options_quality') or opt.get('quality')}")
    else:
        lines.append("")
        lines.append(f"[stock lens] {lens.get('status', 'missing')} ({lens.get('path', '')})")

    gate = report.get("executive_gatekeeper") or {}
    lines.append("")
    if gate.get("status") == "ok":
        lines.append("[executive gatekeeper]")
        lines.append(
            f"  status={gate.get('final_status')}  age={gate.get('_age_hours')}h"
        )
        reasons = gate.get("blocking_reasons") or gate.get("main_reasons") or []
        for r in reasons[:5]:
            lines.append(f"  - {r}")
    else:
        lines.append(f"[executive gatekeeper] {gate.get('status', 'missing')} ({gate.get('path', '')})")

    lines.append(_bar())
    return "\n".join(lines)


def render_text_system_health(report: Dict[str, Any]) -> str:
    v = report.get("verdict") or {}
    lines: List[str] = [
        _bar(),
        "MCP AUDIT — system_health_audit (cache-only, read-only)",
        f"generated_at={report.get('generated_at')}",
        _bar(),
        f"halted={v.get('halted')}  "
        f"ready_to_gate_clean={v.get('ready_to_gate_clean')}  "
        f"ready_to_gate_all={v.get('ready_to_gate_all')}",
        f"active_drift={v.get('active_drift')}  "
        f"unknown_drift={v.get('unknown_drift')}  "
        f"safe_for_paper_observation={v.get('safe_for_paper_observation')}",
    ]

    brok = report.get("broker_snapshot") or {}
    if brok.get("status") == "ok":
        lines.append(f"broker snap   age={brok.get('_age_hours')}h  count={brok.get('count')}")
    else:
        lines.append(f"broker snap   {brok.get('status', 'missing')}")

    rt = (report.get("risk_telemetry") or {}).get("reports") or {}
    lines.append("risk telemetry:")
    for name in ("slippage", "concentration", "shadow_sizing", "paper_hygiene"):
        r = rt.get(name) or {}
        if r.get("status") == "ok":
            warns = r.get("warnings")
            warn_n = len(warns) if isinstance(warns, list) else "n/a"
            lines.append(f"  {name:<14} age={r.get('_age_hours')}h  warns={warn_n}")
        else:
            lines.append(f"  {name:<14} {r.get('status', 'missing')}")

    lines.append(_bar())
    return "\n".join(lines)


# ── Sidecar writer ────────────────────────────────────────────────────

def write_sidecar(
    json_path: Path,
    txt_path: Path,
    payload: Dict[str, Any],
    text: str,
) -> None:
    """Atomic JSON + text write. Tmp-file + rename per the existing
    research-script convention."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(json_path)
    txt_path.write_text(text, encoding="utf-8")


# ── Driver helpers ────────────────────────────────────────────────────

def _run_daily(print_text: bool = False) -> Path:
    report = daily_dashboard_audit()
    text = render_text_daily(report)
    j, x = _sidecar_paths("daily")
    write_sidecar(j, x, report, text)
    if print_text:
        print(text)
    print(f"wrote {j}")
    print(f"wrote {x}")
    return j


def _run_late_chase(top_n: int, print_text: bool = False) -> Path:
    report = late_chase_audit(top_n=top_n)
    text = render_text_late_chase(report)
    j, x = _sidecar_paths("late_chase")
    write_sidecar(j, x, report, text)
    if print_text:
        print(text)
    print(f"wrote {j}")
    print(f"wrote {x}")
    return j


def _run_system_health(print_text: bool = False) -> Path:
    report = system_health_audit()
    text = render_text_system_health(report)
    j, x = _sidecar_paths("system_health")
    write_sidecar(j, x, report, text)
    if print_text:
        print(text)
    print(f"wrote {j}")
    print(f"wrote {x}")
    return j


def _slug_for_ticker(ticker: str) -> str:
    """File-name slug for a ticker. We rely on the helper's regex to have
    already rejected anything that would render unsafe characters."""
    return ticker.strip().upper().replace("/", "-")


def _run_ticker(ticker: str, print_text: bool = False) -> Path:
    report = ticker_consistency_audit(ticker)
    text = render_text_ticker(report)
    t_norm = report.get("ticker") or _slug_for_ticker(ticker)
    j, x = _sidecar_paths(t_norm)
    write_sidecar(j, x, report, text)
    if print_text:
        print(text)
    print(f"wrote {j}")
    print(f"wrote {x}")
    return j


# ── CLI ───────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 2B MCP audit workflow runner (cache-only)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_daily = sub.add_parser("daily", help="daily_dashboard_audit")
    p_daily.add_argument("--print", action="store_true")

    p_lc = sub.add_parser("late-chase", help="late_chase_audit")
    p_lc.add_argument("--top-n", type=int, default=25)
    p_lc.add_argument("--print", action="store_true")

    p_sh = sub.add_parser("system-health", help="system_health_audit")
    p_sh.add_argument("--print", action="store_true")

    p_tk = sub.add_parser("ticker", help="ticker_consistency_audit TICKER [TICKER...]")
    p_tk.add_argument("tickers", nargs="+")
    p_tk.add_argument("--print", action="store_true")

    p_all = sub.add_parser("all", help="run daily + late-chase + system-health")
    p_all.add_argument("--print", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "daily":
        _run_daily(print_text=args.print)
    elif args.cmd == "late-chase":
        _run_late_chase(top_n=args.top_n, print_text=args.print)
    elif args.cmd == "system-health":
        _run_system_health(print_text=args.print)
    elif args.cmd == "ticker":
        for tk in args.tickers:
            _run_ticker(tk, print_text=args.print)
    elif args.cmd == "all":
        _run_daily(print_text=args.print)
        _run_late_chase(top_n=25, print_text=args.print)
        _run_system_health(print_text=args.print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
