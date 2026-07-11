"""TUI scanner-board strip — latest-scan-by-program line.

The Mode-4 board strip gains a third line showing, per research program,
the candidate count + top names from the most recent production scan
(cache-only read of cache/research/latest_scan_programs_latest.json).
Verifies: renders counts/top tickers, honors NEW counts, degrades
silently when the sidecar is missing, and flags a stale scan.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dashboards.gem_trader_hq import PB  # noqa: E402


def _render(renderable) -> str:
    c = Console(record=True, width=200, force_terminal=False,
                color_system=None)
    c.print(renderable)
    return c.export_text()


class _StubDataLayer:
    """Minimal DataLayer stand-in returning only cached dicts."""

    def __init__(self, **values: Any):
        self._values = values

    def get(self, key: str, default: Any = None):
        return self._values.get(key, default)


def _scan_payload(stale: bool = False):
    def cands(*tickers):
        return [{"ticker": t} for t in tickers]
    return {
        "present": True,
        "scan_stale": stale,
        "scan_age_hours": 26.4 if stale else 1.2,
        "programs": {
            "TACTICAL": {"candidate_count": 33, "new_count": 9,
                         "candidates": cands("AVGO", "HCC", "AEHR",
                                             "PKE", "JNJ")},
            "SWING": {"candidate_count": 58, "new_count": 0,
                      "candidates": cands("MEI", "UMC", "RXT", "AGL")},
            "LONG_TERM": {"candidate_count": 10, "new_count": 0,
                          "candidates": cands("NUE", "TKR")},
        },
    }


def test_strip_shows_top_candidates_per_program():
    data = _StubDataLayer(research_scanner={},
                          latest_scan_programs=_scan_payload())
    out = _render(PB.scanner_board_strip(data))
    assert "latest scan" in out
    # counts + top-4 cap
    assert "TACTICAL 33" in out
    assert "(+9 new)" in out
    assert "AVGO HCC AEHR PKE" in out
    assert "JNJ" not in out          # 5th ticker capped
    assert "+29" in out              # 33 - 4 shown
    assert "SWING 58" in out and "MEI UMC RXT AGL" in out
    assert "LONG_TERM 10" in out and "NUE TKR" in out and "+8" in out


def test_strip_omits_line_when_sidecar_missing():
    data = _StubDataLayer(research_scanner={},
                          latest_scan_programs={"_missing": True})
    out = _render(PB.scanner_board_strip(data))
    assert "latest scan" not in out


def test_strip_flags_stale_scan():
    data = _StubDataLayer(research_scanner={},
                          latest_scan_programs=_scan_payload(stale=True))
    out = _render(PB.scanner_board_strip(data))
    assert "26.4h old" in out


def test_strip_no_new_count_suffix_when_zero():
    data = _StubDataLayer(research_scanner={},
                          latest_scan_programs=_scan_payload())
    out = _render(PB.scanner_board_strip(data))
    assert "SWING 58 (" not in out   # zero new → no "(+0 new)" noise
