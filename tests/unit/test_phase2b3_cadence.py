"""tests/unit/test_phase2b3_cadence.py — Phase 2B.3 cadence wiring.

Drives the bash script in `--dry-run` mode and asserts the ordering of
the Python invocations it would fire.  No provider calls, no DB writes:
`--dry-run` echoes commands instead of executing them.

Targets:

  - `premarket` runs gatekeeper-refresh AFTER delta and before cycle
    completion.
  - `nightly` runs gatekeeper-refresh AFTER lenses-nightly and before
    risk-telemetry.
  - `mcp-audit-session regular` (no flag) does NOT trigger
    gatekeeper-refresh.
  - `mcp-audit-session regular --refresh-gatekeeper` runs gatekeeper-
    refresh BEFORE the orchestrator.

Also asserts the priority-class invariant for Phase 2B.3 Task 3:
earnings tickers outrank stale-artifact Alpha tickers.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "run_research_cycle.sh"

sys.path.insert(0, str(REPO))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _run_dry(*args: str) -> str:
    """Run the bash script with --dry-run; returns combined stdout+stderr.

    The script's require_env() warning is suppressed for dry-run, and the
    DRY_RUN top-level flag echoes each underlying command instead of
    executing it, so this is hermetic and credentials-free.
    """
    env = dict(os.environ)
    # Avoid any auto-loading of credentials.
    env.setdefault("SNIPER_ENV_PATH", str(REPO / "tests" / "_no_such.env"))
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run", *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    # Bash subcommands return 0 in dry-run on success.  Tests check ordering,
    # not exit codes, but a non-zero result is still surprising.
    assert result.returncode == 0, (
        f"dry-run exited {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return result.stdout + "\n" + result.stderr


def _positions(text: str, *needles: str) -> list[int]:
    out: list[int] = []
    for needle in needles:
        idx = text.find(needle)
        out.append(idx)
    return out


# ── Premarket ───────────────────────────────────────────────────────────────

def test_premarket_includes_gatekeeper_refresh():
    out = _run_dry("premarket")
    assert "research/gatekeeper_refresh.py" in out
    assert "premarket cycle complete" in out


def test_premarket_gatekeeper_after_delta():
    out = _run_dry("premarket")
    delta_idx, gk_idx, done_idx = _positions(
        out,
        "research/research_delta.py",
        "research/gatekeeper_refresh.py",
        "premarket cycle complete",
    )
    assert delta_idx >= 0 and gk_idx >= 0 and done_idx >= 0
    assert delta_idx < gk_idx < done_idx


# ── Nightly ─────────────────────────────────────────────────────────────────

def test_nightly_includes_gatekeeper_refresh():
    out = _run_dry("nightly")
    assert "research/gatekeeper_refresh.py" in out


def test_nightly_gatekeeper_between_lenses_and_risk_telemetry():
    out = _run_dry("nightly")
    lenses_idx, gk_idx, slip_idx = _positions(
        out,
        "research/prebuild_stock_lenses.py",
        "research/gatekeeper_refresh.py",
        "research/slippage_telemetry_report.py",
    )
    assert lenses_idx >= 0 and gk_idx >= 0 and slip_idx >= 0
    assert lenses_idx < gk_idx < slip_idx


def test_nightly_gatekeeper_before_resolve():
    """Resolve / holdout / risk-telemetry are the cache-only tail; the
    refresh must precede them so any new lens artifacts get a fresh
    Gatekeeper before downstream resolution / reports.
    """
    out = _run_dry("nightly")
    gk_idx = out.find("research/gatekeeper_refresh.py")
    resolve_idx = out.find("research/forecast_forward_report.py")
    assert gk_idx >= 0 and resolve_idx >= 0
    assert gk_idx < resolve_idx


# ── MCP audit session: opt-in only ──────────────────────────────────────────

def test_mcp_audit_session_default_no_refresh():
    out = _run_dry("mcp-audit-session", "regular")
    assert "research/mcp_audit_orchestrator.py" in out
    assert "research/gatekeeper_refresh.py" not in out
    # The Phase 2B.3 log marker must not appear without the flag.
    assert "refreshing Executive Gatekeeper before MCP audit" not in out


def test_mcp_audit_session_flag_triggers_refresh_first():
    out = _run_dry("mcp-audit-session", "regular", "--refresh-gatekeeper")
    gk_idx, mcp_idx = _positions(
        out,
        "research/gatekeeper_refresh.py",
        "research/mcp_audit_orchestrator.py",
    )
    assert gk_idx >= 0 and mcp_idx >= 0
    assert gk_idx < mcp_idx
    assert "refreshing Executive Gatekeeper before MCP audit" in out


def test_mcp_audit_session_flag_strips_from_orchestrator_args():
    """The --refresh-gatekeeper flag must be consumed by the bash wrapper
    and NOT forwarded to mcp_audit_orchestrator.py (which would fail
    argparse).  We verify by inspecting the echoed dry-run command line."""
    out = _run_dry("mcp-audit-session", "regular", "--refresh-gatekeeper")
    # The line that echoes the orchestrator command should not contain the
    # flag.  Look at the actual command line after the DRY_RUN marker.
    orchestrator_line = ""
    for line in out.splitlines():
        if "mcp_audit_orchestrator.py" in line:
            orchestrator_line = line
            break
    assert orchestrator_line, "orchestrator command line not found"
    assert "--refresh-gatekeeper" not in orchestrator_line
    assert "--session regular" in orchestrator_line


# ── Earnings priority invariant (Phase 2B.3 Task 3) ─────────────────────────

def test_earnings_tickers_outrank_alpha_top():
    from research import gatekeeper_refresh as gr
    assert gr.PRIORITY["earnings_today"] < gr.PRIORITY["alpha_top"]
    assert gr.PRIORITY["earnings_tomorrow"] < gr.PRIORITY["alpha_top"]
    assert gr.PRIORITY["earnings_week"] < gr.PRIORITY["alpha_top"]


def test_open_position_outranks_everything_else():
    from research import gatekeeper_refresh as gr
    base = gr.PRIORITY["open_position"]
    for key, val in gr.PRIORITY.items():
        if key == "open_position":
            continue
        assert base < val, f"open_position should outrank {key}"


def test_explicit_watch_outranks_alpha_and_stale():
    from research import gatekeeper_refresh as gr
    assert gr.PRIORITY["explicit_watch"] < gr.PRIORITY["alpha_top"]
    assert gr.PRIORITY["explicit_watch"] < gr.PRIORITY["stale_artifact"]


# ── source-count log breakdown ──────────────────────────────────────────────

def test_summarize_sources_counts_by_prefix():
    from research import gatekeeper_refresh as gr
    cands = [
        gr.Candidate(ticker="NVDA", priority=10,
                     reasons=["open_position", "earnings_today"]),
        gr.Candidate(ticker="AMZN", priority=50,
                     reasons=["alpha_top:A:77.1", "missing_gatekeeper"]),
        gr.Candidate(ticker="MOH",  priority=50,
                     reasons=["alpha_top:A:73.8"]),
    ]
    out = gr.summarize_sources(cands)
    # alpha_top tagged twice (AMZN, MOH); open_position once (NVDA);
    # earnings_today once; missing_gatekeeper once.
    assert out["alpha_top"] == 2
    assert out["open_position"] == 1
    assert out["earnings_today"] == 1
    assert out["missing_gatekeeper"] == 1


def test_summarize_sources_dedupes_same_source_per_ticker():
    """A ticker tagged twice with the same source key counts once."""
    from research import gatekeeper_refresh as gr
    cands = [
        gr.Candidate(ticker="DINO", priority=50,
                     reasons=["alpha_top:A:76.1", "alpha_top:A:74.0"]),
    ]
    out = gr.summarize_sources(cands)
    assert out == {"alpha_top": 1}


# ── Phase 2B.4: provider-audit subcommand + nightly tail ────────────────────

def test_nightly_includes_provider_audit_at_tail():
    out = _run_dry("nightly")
    assert "research/provider_freshness_audit.py" in out
    # Provider audit is the last [CACHE] step before "nightly cycle complete".
    pa_idx = out.find("research/provider_freshness_audit.py")
    done_idx = out.find("nightly cycle complete")
    risk_idx = out.find("research/paper_state_hygiene_report.py")
    assert risk_idx >= 0 and pa_idx >= 0 and done_idx >= 0
    assert risk_idx < pa_idx < done_idx


def test_provider_audit_subcommand_runs():
    out = _run_dry("provider-audit")
    assert "research/provider_freshness_audit.py" in out


def test_provider_audit_forwards_flags():
    out = _run_dry("provider-audit", "--ticker", "NVDA", "--print")
    # Forwarded flags are visible in the echoed dry-run command line.
    line = ""
    for ln in out.splitlines():
        if "provider_freshness_audit.py" in ln:
            line = ln
            break
    assert "--ticker NVDA" in line
    assert "--print" in line


def test_premarket_does_not_include_provider_audit():
    """Provider audit is a nightly tail only; premarket stays compact."""
    out = _run_dry("premarket")
    assert "research/provider_freshness_audit.py" not in out
