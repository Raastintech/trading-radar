"""The runner's [CACHE]/[PROVIDER] cost labels must tell the truth.

``scripts/run_research_cycle.sh`` documents the two labels as a cost
contract: [PROVIDER] calls FMP/Alpaca/yfinance/the LLM provider, [CACHE]
"reads cached parquets / JSON only; no network cost".  Operators read those
labels to decide what is safe to rerun mid-month against a budget, and
require_env() tells them [CACHE] still works without credentials.

The research-scanner step carried [CACHE] while calling FMP for the earnings
calendar, analyst grades, fundamentals, and one company profile per watchlist
name — every one of them degrades to None on failure, so the mislabel was
invisible in the output.  These tests pin the corrected label and the wider
invariant behind it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "run_research_cycle.sh"

# Scripts that reach a provider.  Not exhaustive — it is the set a [CACHE]
# label would most obviously misrepresent.
_PROVIDER_SCRIPTS = (
    "research/research_scanner.py",
    "research/regime_forecast.py",
    "research/alpha_discovery_board.py",
    "research/refresh_universe_prices.py",
    "research/universe_discovery_bootstrap.py",
    "research/scan_universe_manifest.py",
)


def _run_dry(*args: str) -> str:
    env = dict(os.environ)
    env.setdefault("SNIPER_ENV_PATH", str(REPO / "tests" / "_no_such.env"))
    r = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run", *args],
        cwd=str(REPO), capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode == 0, f"dry-run exited {r.returncode}\n{r.stdout}\n{r.stderr}"
    return r.stdout + r.stderr


def _labelled_steps(out: str):
    """[(label, text)] for every cost-labelled step in a dry run."""
    return re.findall(r"\[(CACHE|PROVIDER)\] (.+)", out)


def test_scanner_step_is_labelled_provider():
    out = _run_dry("research-scanner")
    labels = [lbl for lbl, txt in _labelled_steps(out) if "research scanner" in txt]
    assert labels == ["PROVIDER"], (
        "the scanner calls FMP (earnings calendar, analyst grades, "
        "fundamentals, per-name profile) — it is not a [CACHE] step")


def test_scanner_offline_flag_still_reaches_the_scanner():
    """--offline is the escape hatch the label's note points operators to."""
    out = _run_dry("research-scanner", "--offline")
    line = next(ln for ln in out.splitlines() if "research_scanner.py" in ln)
    assert "--offline" in line


def test_midday_cycle_has_no_provider_steps():
    """Midday is documented cache-only; that is what makes the label useful."""
    out = _run_dry("midday")
    assert [t for lbl, t in _labelled_steps(out) if lbl == "PROVIDER"] == []


def test_cache_labelled_steps_do_not_invoke_provider_scripts():
    """A [CACHE] line must not be followed by a known provider script.

    Walks each cycle's dry-run transcript pairing every echoed command with
    the cost label that most recently preceded it.
    """
    checked = set()
    for cycle in ("nightly", "premarket", "midday"):
        out = _run_dry(cycle)
        label = None
        for line in out.splitlines():
            m = re.search(r"\[(CACHE|PROVIDER)\]", line)
            if m:
                label = m.group(1)
                continue
            if "DRY_RUN $" not in line:
                continue
            for script in _PROVIDER_SCRIPTS:
                if script in line:
                    checked.add(script)
                    assert label == "PROVIDER", (
                        f"{cycle}: {script} runs under a [{label}] label")
    # A renamed script would otherwise make this test pass by matching nothing.
    missing = set(_PROVIDER_SCRIPTS) - checked
    assert not missing, f"never saw these in any cycle — renamed? {sorted(missing)}"
