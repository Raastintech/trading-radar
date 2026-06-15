#!/usr/bin/env python3
"""
research/annotate_alpha_board.py — re-annotate the cached Alpha Discovery
board (+ overlay) with the current Stock Lens artifacts on disk.

Cache-only.  Invoked by ``./scripts/run_research_cycle.sh alpha-lens-refresh``
after the lens prebuild has refreshed the per-ticker lens JSONs.  Never
calls a provider, never mutates execution / governance / paper evidence.

Loads SNIPER_ENV_PATH the same way prebuild_stock_lenses.py does so the
``core.config`` import succeeds whether or not the caller already loaded
the dotenv.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_CRED = os.environ.get("SNIPER_ENV_PATH")
if _CRED and Path(_CRED).exists():
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(_CRED, override=True)
    except ImportError:
        pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Re-annotate the cached Alpha board + overlay with Stock Lens evidence."
    )
    p.add_argument("--fresh-hours", type=float, default=24.0,
                   help="lens freshness window in hours (default 24)")
    p.add_argument("--quiet", action="store_true",
                   help="suppress the summary print")
    args = p.parse_args(argv)

    from core.alpha_discovery import annotate_alpha_board_on_disk
    summary = annotate_alpha_board_on_disk(fresh_hours=args.fresh_hours)
    if not args.quiet:
        print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
