"""
research/executive_gatekeeper_report.py — CLI for the V1 gatekeeper.

Runs `core.executive_gatekeeper.run_executive_gatekeeper(ticker)` and writes
two artefacts:
  - cache/research/executive_gatekeeper_<TICKER>_latest.json  (full result)
  - logs/executive_gatekeeper_<TICKER>_latest.txt             (prose summary)

Pure research. No provider calls, no signals, no execution, no Kelly sizing.

Usage
-----
  cd /home/gem/trading-production
  .venv/bin/python research/executive_gatekeeper_report.py --ticker AAPL
  .venv/bin/python research/executive_gatekeeper_report.py --ticker NVDA --with-llm-summary
  .venv/bin/python research/executive_gatekeeper_report.py --ticker AAPL --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core.executive_gatekeeper import (  # noqa: E402
    run_executive_gatekeeper,
    GatekeeperResult,
)

CACHE_DIR = REPO / "cache" / "research"
LOG_DIR = REPO / "logs"


def _write_artifacts(result: GatekeeperResult) -> tuple[Path, Path]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    json_path = CACHE_DIR / f"executive_gatekeeper_{result.ticker}_latest.json"
    txt_path = LOG_DIR / f"executive_gatekeeper_{result.ticker}_latest.txt"
    json_path.write_text(json.dumps(result.to_dict(), indent=2, default=str))
    txt_path.write_text(result.llm_summary or "")
    return json_path, txt_path


def _render_text(result: GatekeeperResult) -> str:
    g_lines: List[str] = []
    for g in result.gates:
        g_lines.append(f"  · {g.name:<22}  {g.verdict:<10}  {('; '.join(g.reasons))[:140]}")
    out = [
        f"Executive Gatekeeper V1 — {result.ticker}",
        f"Generated: {result.generated_at}",
        "",
        f"Final status:   {result.final_status}",
        f"Confidence:     {result.confidence}",
        f"Sizing:         {result.sizing_guidance}",
        "",
        "Gate verdicts:",
        *g_lines,
        "",
        "Main reasons:",
        *[f"  - {r}" for r in result.main_reasons],
    ]
    if result.blocking_reasons:
        out += ["", "Blocking reasons:", *[f"  - {r}" for r in result.blocking_reasons]]
    if result.supporting_evidence:
        out += ["", "Supporting:", *[f"  - {r}" for r in result.supporting_evidence]]
    if result.risks:
        out += ["", "Risks:", *[f"  - {r}" for r in result.risks]]
    if result.hedge_suggestion:
        out += ["", f"Hedge suggestion (research-only, no order placement): {result.hedge_suggestion}"]
    if result.next_manual_check:
        out += ["", "Next manual check:", *[f"  - {r}" for r in result.next_manual_check]]
    out += [
        "",
        "Data sources present: " + ", ".join(
            f"{k}={'Y' if v else 'N'}" for k, v in result.data_sources.items()
        ),
        "",
        "Guardrails: " + " | ".join(result.guardrails),
    ]
    if result.llm_summary:
        out += ["", "── Plain-English summary ──", result.llm_summary]
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", required=True, help="Ticker symbol, e.g. AAPL")
    ap.add_argument("--json", action="store_true", help="Print JSON instead of prose")
    ap.add_argument("--with-llm-summary", action="store_true",
                    help="Attach an LLM-generated plain-English summary "
                         "(descriptive only — cannot mutate the deterministic verdict). "
                         "Falls back to deterministic prose if no LLM is configured.")
    ap.add_argument("--db", default=None, help="Override db/trading.db path (testing)")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else None
    result = run_executive_gatekeeper(
        args.ticker,
        with_llm_summary=args.with_llm_summary,
        db_path=db_path,
    )
    json_path, txt_path = _write_artifacts(result)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(_render_text(result))
    print(f"\n(JSON written to {json_path})", file=sys.stderr)
    print(f"(TXT  written to {txt_path})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
