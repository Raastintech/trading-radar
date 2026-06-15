#!/usr/bin/env python3
"""
research/forecast_forward_report.py — Phase 5 forward-tracking report for the
Market & Sector Regime Forecaster.

Reads:
  - data/state/regime_forecast_forward_log.jsonl

Optionally first runs the resolver (default), then writes:
  - cache/research/forecast_forward_summary_latest.json
  - logs/forecast_forward_summary_latest.txt

Research-only.  Never tunes the forecast or touches paper / governance / exec.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \
    .venv/bin/python research/forecast_forward_report.py

  # Print the rendered text after writing.
  .venv/bin/python research/forecast_forward_report.py --print

  # Skip running the resolver (just summarize what's on disk).
  .venv/bin/python research/forecast_forward_report.py --no-resolve
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import forecast_forward_tracker as fft


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Forecast forward-tracking report")
    p.add_argument("--no-resolve", action="store_true",
                   help="skip the resolver pass; only summarize what's on disk")
    p.add_argument("--print", dest="do_print", action="store_true",
                   help="print the rendered text summary after writing")
    p.add_argument("--print-json", action="store_true",
                   help="print the JSON summary after writing")
    p.add_argument("--log-path", type=Path, default=fft.FORECAST_LOG_PATH)
    args = p.parse_args(argv)

    if not args.no_resolve:
        result = fft.resolve_forecast_outcomes(log_path=args.log_path)
        print(
            f"resolver: rows={result['rows']} matured={result['matured']} "
            f"open={result['still_open']} updated_fields={result.get('fields_updated', 0)}"
        )

    paths = fft.write_forecast_summary(log_path=args.log_path)
    summary = fft.forecast_summary(log_path=args.log_path)
    print(
        f"summary: snapshots={summary['snapshots_total']} "
        f"matured={summary['snapshots_matured']} open={summary['snapshots_open']} "
        f"→ {paths['json']}"
    )
    if args.do_print:
        print()
        print(Path(paths["text"]).read_text(encoding="utf-8"))
    if args.print_json:
        print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
