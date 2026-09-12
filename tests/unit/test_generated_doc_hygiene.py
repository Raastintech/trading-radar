"""Generated reports must not be versioned; durable ones must stay versioned.

Six reports under docs/research are rewritten by a scheduled cycle on every
run, so tracking them put a diff in the working tree every night and trained
the reader to ignore a dirty tree. They are ignored and untracked as of
2026-09-12; the files themselves stay on disk and are still read.

The opposite mistake matters just as much: a dated study, a doctrine document
or a report written once must stay tracked, because its value is that it is
fixed and quotable. These tests pin both halves.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Rewritten by a scheduled cycle — must be ignored and untracked.
GENERATED_REPORTS = (
    "docs/research/DAILY_ALPHA_RADAR_REPORT.md",
    "docs/research/NIGHTLY_OPERATOR_SUMMARY.md",
    "docs/research/OPTIONS_CHAIN_SNAPSHOT_HEALTH.md",
    "docs/research/OPTIONS_CHAIN_SNAPSHOT_QUALITY.md",
    "docs/research/PARTICIPATION_BOTTLENECK_AUDIT.md",
    "docs/research/SOCIAL_ATTENTION_FORWARD_RESULTS.md",
    "docs/research/RESEARCH_GOVERNOR_REPORT_latest.md",
)

#: Durable: written once, on demand, or dated. Must stay tracked.
DURABLE_REPORTS = (
    "docs/research/ALPHA_FOCUS.md",
    "docs/research/ALPHA_FAILURE_ROOT_CAUSE_AUDIT.md",
    "docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md",
    "docs/research/SCANNER_TRUTH_REVIEW_2026_09.md",
    "docs/research/RS_THEME_LENS_TRIAGE.md",
    "docs/ops/DAILY_OPERATING_NOTE.md",
    "docs/ops/RESEARCH_GOVERNOR_RUNBOOK.md",
)


def _git(*argv: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "--no-optional-locks", *argv], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git unavailable: {exc}")


def _is_tracked(rel: str) -> bool:
    return _git("ls-files", "--error-unmatch", rel).returncode == 0


@pytest.mark.parametrize("rel", GENERATED_REPORTS)
def test_a_generated_report_is_not_tracked(rel):
    assert not _is_tracked(rel), (
        f"{rel} is regenerated every run; tracking it dirties the tree nightly")


@pytest.mark.parametrize("rel", GENERATED_REPORTS)
def test_a_generated_report_is_ignored(rel):
    assert _git("check-ignore", "-q", rel).returncode == 0, (
        f"{rel} is untracked but not ignored, so it shows up as noise instead")


@pytest.mark.parametrize("rel", GENERATED_REPORTS)
def test_untracking_did_not_delete_the_report(rel):
    """Quarantine of a file from git must never mean removal from disk."""
    if rel.endswith("RESEARCH_GOVERNOR_REPORT_latest.md"):
        pytest.skip("written on demand by the governor, not by a scheduled cycle")
    assert (ROOT / rel).exists(), f"{rel} was removed from disk, not just from git"


@pytest.mark.parametrize("rel", DURABLE_REPORTS)
def test_a_durable_report_stays_tracked(rel):
    if not (ROOT / rel).exists():
        pytest.skip(f"{rel} not present in this checkout")
    assert _is_tracked(rel), (
        f"{rel} is durable (dated, doctrine, or written once) and must stay "
        "versioned; only per-run output is ignored")
