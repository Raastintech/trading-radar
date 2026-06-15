#!/usr/bin/env python3
"""Phase 1H.2 Strategy Lab methodology audit entry point."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research import strategy_lab_portfolio as portfolio  # noqa: E402


def main(argv: Optional[Sequence[str]] = None) -> int:
    argparse.ArgumentParser(description="Strategy Lab methodology audit (research-only)").parse_args(argv)
    res = portfolio.build_method_audit()
    portfolio.write_method_outputs(res)
    print("\n".join(portfolio.render_method_text(res)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
