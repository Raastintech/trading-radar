"""Shared safety rails for the historical replay harness (research-only).

Every module under ``research/backtests/historical_replay_*`` imports this. It
exists to make three guarantees mechanical rather than a matter of author
discipline:

1. **Write containment.** The replay may only write into replay namespaces
   (:data:`REPLAY_WRITE_PREFIXES`). Any attempt to write a live cache, a live
   ledger, a scanner artifact, or a dashboard artifact raises
   :class:`ReplayWriteViolation` before the write happens.
2. **Live-artifact tripwire.** :class:`LiveArtifactTripwire` snapshots the
   mtimes of live artifacts before a stage runs and fails the stage if any of
   them moved, catching indirect writes from imported production code (the
   price-only replay imports the frozen scanner, whose ``_build_universe``
   unconditionally writes three live artifacts unless patched out).
3. **Provenance.** Every artifact this harness emits carries the replay
   provenance flags (:func:`provenance_flags`), and every verdict is drawn from
   :class:`ReplayVerdict`, an enum that is deliberately disjoint from the live
   verdict ladder. ``VALIDATED_EDGE`` cannot be emitted from here.

Nothing in this package is imported by production code, and nothing here reads
credentials. Modules that fetch from a provider gate the fetch behind an
explicit ``--execute-fetch`` flag; see :func:`require_execute_fetch`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping, Sequence

# ── repo root ───────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[2]


# ── replay namespaces ───────────────────────────────────────────────────────
# Directories the replay owns outright.
REPLAY_DIR_PREFIXES: tuple[str, ...] = (
    "cache/replay_prices",
    "cache/replay_fundamentals",
    "cache/replay_market_cap",
    "cache/replay_universe",
    "cache/research/historical_replay_intermediates",
    "cache/research/alpha_reconstruction_intermediates",
    "cache/research/alpha_tournament_intermediates",
    "cache/research/m1_conviction_intermediates",
    "cache/research/m1_missing_data_intermediates",
    "cache/research/m1_surprise_intermediates",
)

# File-name prefixes the replay owns inside otherwise-live directories.
REPLAY_FILE_PREFIXES: tuple[str, ...] = (
    "cache/research/historical_replay_",
    "cache/research/historical_fundamental_replay_",
    "cache/research/alpha_reconstruction_",
    "cache/research/alpha_tournament_",
    "cache/research/m1_conviction_",
    "cache/research/m1_missing_data_",
    "cache/research/m1_surprise_",
    "logs/historical_replay_",
    "logs/historical_fundamental_replay_",
    "logs/alpha_reconstruction_",
    "logs/alpha_tournament_",
    "logs/m1_conviction_",
    "logs/m1_missing_data_",
    "logs/m1_surprise_overlay_",
)

REPLAY_WRITE_PREFIXES: tuple[str, ...] = REPLAY_DIR_PREFIXES + REPLAY_FILE_PREFIXES

# Paths the replay must never write, stated explicitly so a future edit that
# widens REPLAY_WRITE_PREFIXES by accident still trips here.
FORBIDDEN_WRITE_PREFIXES: tuple[str, ...] = (
    "cache/prices",
    "cache/prices_deep",
    "cache/backtest_prices",
    "cache/fundamentals",
    "cache/universe",
    "cache/state",
    "data",
    "db",
)

# Individual live artifacts guarded by the tripwire. These are the ones the
# frozen scanner and the HC/EO builders touch when they are not sandboxed.
LIVE_ARTIFACT_PATHS: tuple[str, ...] = (
    "cache/research/research_scanner_latest.json",
    "cache/research/research_universe_build_latest.json",
    "cache/research/universe_miss_diagnostic_latest.json",
    "cache/research/latest_scan_programs_latest.json",
    "cache/research/high_conviction_alpha_latest.json",
    "cache/research/emerging_outlier_watch_latest.json",
    "cache/research/alpha_focus_latest.json",
    "cache/research/cohort_attribution_latest.json",
    "cache/research/alpha_failure_root_cause_latest.json",
    "cache/research/nightly_operator_summary_latest.json",
    "logs/research_scanner_latest.txt",
    "logs/research_universe_build_latest.txt",
)

# Whole directories the tripwire watches by (count, newest-mtime).
LIVE_WATCH_DIRS: tuple[str, ...] = (
    "cache/prices",
    "cache/prices_deep",
    "cache/backtest_prices",
    "cache/fundamentals",
    "data/research",
)


class ReplayWriteViolation(RuntimeError):
    """Raised when the replay tries to write outside its own namespaces."""


class LiveArtifactTouched(RuntimeError):
    """Raised when a live artifact changed while a replay stage was running."""


def _rel(path: Path | str) -> str:
    """Repo-relative POSIX string for *path*, for prefix matching."""
    p = Path(path)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    else:
        p = p.resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        # Outside the repo entirely (a tempfile sandbox, pytest tmp_path).
        return p.as_posix()


def _matches_dir_prefix(rel: str, pref: str) -> bool:
    """True only for the directory itself or something genuinely inside it.

    Deliberately NOT a bare ``startswith``: that would admit a sibling like
    ``cache/replay_pricesEVIL/x`` on the strength of a shared prefix.
    """
    return rel == pref or rel.startswith(pref + "/")


def is_replay_path(path: Path | str, root: Path | None = None) -> bool:
    """True when *path* sits inside a replay-owned namespace."""
    rel = _rel(path) if root is None else _rel_to(path, root)
    if os.path.isabs(rel):
        # Outside the repo: sandboxes and tmp dirs are always fine to write.
        return True
    if any(_matches_dir_prefix(rel, pref) for pref in REPLAY_DIR_PREFIXES):
        return True
    # File prefixes name a leading filename fragment inside a shared directory
    # (e.g. cache/research/historical_replay_*), so startswith is correct here,
    # but the match must not escape into a subdirectory of its own.
    return any(
        rel.startswith(pref) and "/" not in rel[len(pref):]
        for pref in REPLAY_FILE_PREFIXES
    )


def _rel_to(path: Path | str, root: Path) -> str:
    p = Path(path)
    p = (root / p) if not p.is_absolute() else p
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def assert_replay_write_path(path: Path | str, root: Path | None = None) -> Path:
    """Guard a write target. Returns the path, or raises.

    Checks the explicit deny list first so that a forbidden path can never be
    admitted by an over-broad allow prefix.
    """
    rel = _rel(path) if root is None else _rel_to(path, root)
    if not os.path.isabs(rel):
        # Deny list first: a forbidden path can never be rescued by an
        # over-broad allow prefix added later.
        for bad in FORBIDDEN_WRITE_PREFIXES:
            if _matches_dir_prefix(rel, bad):
                raise ReplayWriteViolation(
                    f"replay attempted to write a forbidden live path: {rel}"
                )
    if not is_replay_path(path, root=root):
        raise ReplayWriteViolation(
            f"replay attempted to write outside its namespaces: {rel}\n"
            f"allowed prefixes: {', '.join(REPLAY_WRITE_PREFIXES)}"
        )
    return Path(path)


def write_replay_json(path: Path | str, payload: object, *, root: Path | None = None) -> Path:
    """Write JSON to a guarded replay path, creating parents."""
    p = assert_replay_write_path(path, root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=1, default=str))
    return p


def write_replay_text(path: Path | str, text: str, *, root: Path | None = None) -> Path:
    """Write text to a guarded replay path, creating parents."""
    p = assert_replay_write_path(path, root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# ── live-artifact tripwire ──────────────────────────────────────────────────


@dataclass
class LiveArtifactTripwire:
    """Snapshot live-artifact mtimes before a stage; assert they did not move.

    Usage::

        tw = LiveArtifactTripwire.snapshot()
        ...run the replay stage...
        tw.assert_clean()          # raises LiveArtifactTouched on violation
    """

    before: Mapping[str, object]
    root: Path = ROOT

    @staticmethod
    def _probe(root: Path) -> dict[str, object]:
        """Fingerprint each guarded artifact as (mtime_ns, size).

        Size is carried alongside mtime because mtime alone is not sufficient:
        filesystems vary in timestamp granularity, so two writes close together
        can share an mtime and a rewrite would slip past an mtime-only check.
        """
        state: dict[str, object] = {}
        for rel in LIVE_ARTIFACT_PATHS:
            p = root / rel
            if p.exists():
                st = p.stat()
                state[rel] = (st.st_mtime_ns, st.st_size)
            else:
                state[rel] = None
        for rel in LIVE_WATCH_DIRS:
            d = root / rel
            if not d.is_dir():
                state[rel + "/"] = None
                continue
            files = [f for f in d.iterdir() if f.is_file()]
            stats = [f.stat() for f in files]
            newest = max((st.st_mtime_ns for st in stats), default=0)
            total = sum(st.st_size for st in stats)
            state[rel + "/"] = (len(files), newest, total)
        return state

    @classmethod
    def snapshot(cls, root: Path | None = None) -> "LiveArtifactTripwire":
        r = root or ROOT
        return cls(before=cls._probe(r), root=r)

    def diff(self) -> list[str]:
        """Live artifacts whose mtime or directory fingerprint changed."""
        after = self._probe(self.root)
        return sorted(k for k in self.before if self.before[k] != after.get(k))

    def assert_clean(self) -> None:
        touched = self.diff()
        if touched:
            raise LiveArtifactTouched(
                "replay stage modified live artifacts: " + ", ".join(touched)
            )

    def report(self) -> str:
        touched = self.diff()
        return (
            "LIVE-ARTIFACT TRIPWIRE: CLEAN — no live artifact touched"
            if not touched
            else f"LIVE-ARTIFACT TRIPWIRE: !! VIOLATION: {touched}"
        )


# ── provenance ──────────────────────────────────────────────────────────────

NOTE_PRICE_ONLY = (
    "HISTORICAL REPLAY — RESEARCH ONLY. Replay evidence over a fixed historical "
    "window. NOT live forward evidence, NOT backtest evidence for Phase 4B, and "
    "NOT a gate/threshold/score recommendation. Must never be pooled with the "
    "live forward ledger, program verdicts, HC/EO routing, or the dashboard."
)

NOTE_FUNDAMENTAL = (
    NOTE_PRICE_ONLY + " Fundamentals are restatement-contaminated: the provider "
    "serves the CURRENT version of each statement, correctly timestamped by "
    "acceptedDate but not as-originally-filed."
)


def provenance_flags(*, fundamentals: bool = False) -> dict[str, object]:
    """The provenance block every replay artifact must carry."""
    flags: dict[str, object] = {
        "research_only": True,
        "not_live_evidence": True,
        "not_backtest_evidence_for_phase4b": True,
        "replay_verdict_only": True,
        "no_recommendation": True,
        "current_rule_overfitting_risk": True,
        "note": NOTE_FUNDAMENTAL if fundamentals else NOTE_PRICE_ONLY,
    }
    if fundamentals:
        flags["restatement_contaminated"] = True
    return flags


REQUIRED_PROVENANCE_KEYS: tuple[str, ...] = (
    "research_only",
    "not_live_evidence",
    "not_backtest_evidence_for_phase4b",
    "replay_verdict_only",
    "no_recommendation",
    "current_rule_overfitting_risk",
)


def assert_provenance(payload: Mapping[str, object]) -> None:
    """Fail loudly if an artifact is missing its replay provenance flags."""
    missing = [k for k in REQUIRED_PROVENANCE_KEYS if payload.get(k) is not True]
    if missing:
        raise ValueError(f"replay artifact missing provenance flags: {missing}")


# ── verdict ladder (deliberately disjoint from the live ladder) ─────────────


class ReplayVerdict(str, Enum):
    """The only verdicts this harness may emit.

    This ladder is separate from the live evidence ladder on purpose. A replay
    can corroborate or contradict a hypothesis over a historical window; it can
    never validate an edge, promote a candidate, or resolve a program verdict.
    """

    PRICE_ONLY_CORROBORATES = "REPLAY_CORROBORATES"
    PRICE_ONLY_CONTRADICTS = "REPLAY_CONTRADICTS"
    PRICE_ONLY_INCONCLUSIVE = "REPLAY_INCONCLUSIVE"
    FUNDAMENTAL_CORROBORATES = "FUNDAMENTAL_REPLAY_CORROBORATES"
    FUNDAMENTAL_CONTRADICTS = "FUNDAMENTAL_REPLAY_CONTRADICTS"
    FUNDAMENTAL_INCONCLUSIVE = "FUNDAMENTAL_REPLAY_INCONCLUSIVE"


REPLAY_VERDICTS: frozenset[str] = frozenset(v.value for v in ReplayVerdict)


class FingerprintVerdict(str, Enum):
    """Per-fingerprint verdicts for the Alpha Reconstruction Lab.

    A third ladder, disjoint from both the live evidence ladder and
    :class:`ReplayVerdict`. A fingerprint is a *hypothesis about what winners
    looked like beforehand*, scored on historical data the author has already
    seen; the strongest thing it can earn is "worth testing forward".
    """

    PROMISING = "PROMISING_RESEARCH_FINGERPRINT"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED_OVERFIT = "REJECTED_OVERFIT"
    REJECTED_NEGATIVE = "REJECTED_NEGATIVE_SELECTION"


FINGERPRINT_VERDICTS: frozenset[str] = frozenset(v.value for v in FingerprintVerdict)


class TournamentVerdict(str, Enum):
    """Per-strategy verdicts for the open Alpha Discovery Tournament.

    A fourth ladder. It is wider than :class:`FingerprintVerdict` because the
    tournament asks a wider question: not "does this beat the universe on the
    median" but "does this setup have positive asymmetric expectancy". A lane
    can be worth keeping as a special situation, or worth keeping only as an
    avoid-list, without being an alpha pool — and those are different answers
    that a single INCONCLUSIVE would flatten.
    """

    TRUE_ALPHA_CANDIDATE_POOL = "TRUE_ALPHA_CANDIDATE_POOL"
    PROMISING_BUT_UNPROVEN = "PROMISING_BUT_UNPROVEN"
    SPECIAL_SITUATION_ONLY = "SPECIAL_SITUATION_ONLY"
    AVOID_TRAP = "AVOID_TRAP"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED_NEGATIVE_SELECTION = "REJECTED_NEGATIVE_SELECTION"
    REJECTED_OVERFIT = "REJECTED_OVERFIT"
    REJECTED_DATA_UNSAFE = "REJECTED_DATA_UNSAFE"


TOURNAMENT_VERDICTS: frozenset[str] = frozenset(v.value for v in TournamentVerdict)


class ConvictionVerdict(str, Enum):
    """Verdicts for second-stage conviction overlays inside a source pool.

    A fifth ladder, for a narrower question than the tournament's: given a pool
    that already works as a basket, does ranking inside it improve the per-name
    expectancy a human actually experiences when reading a shortlist? An
    overlay can be a useful avoid-filter without being a conviction ranker, and
    a pool can be worth keeping while every overlay on it fails.
    """

    PROMISING_RESEARCH_SOURCE_POOL = "PROMISING_RESEARCH_SOURCE_POOL"
    PROMISING_CONVICTION_OVERLAY = "PROMISING_CONVICTION_OVERLAY"
    # An event overlay can be informative as a TAG on a minority of names
    # without being able to order the whole list. That is a real outcome and
    # deserves its own token rather than being flattened into INCONCLUSIVE.
    USEFUL_EVENT_ANNOTATION = "USEFUL_EVENT_ANNOTATION"
    USEFUL_AVOID_FILTER = "USEFUL_AVOID_FILTER"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED_OVERFIT = "REJECTED_OVERFIT"
    REJECTED_NEGATIVE_SELECTION = "REJECTED_NEGATIVE_SELECTION"
    REJECTED_DATA_UNSAFE = "REJECTED_DATA_UNSAFE"


CONVICTION_VERDICTS: frozenset[str] = frozenset(v.value for v in ConvictionVerdict)

# Verdict tokens owned by the LIVE evidence ladder / program verdicts. The
# replay is forbidden from emitting any of these.
FORBIDDEN_LIVE_VERDICTS: frozenset[str] = frozenset(
    {
        "VALIDATED_EDGE",
        "READY_TO_GATE",
        "LENS_READY",
        "CORE_ENGINE_CANDIDATE",
        "PROMISING_RESEARCH_SURFACE",
        "READY_TO_FEED_LENS",
        "NEED_MORE_DATA",
        "INSUFFICIENT_MATURE_EVIDENCE",
        "MIXED",
        "NO_VALUE",
    }
)


def assert_conviction_verdict(verdict: str) -> str:
    """Guard a conviction-overlay verdict on its way into an artifact."""
    v = str(verdict)
    if v in FORBIDDEN_LIVE_VERDICTS:
        raise ValueError(
            f"{v!r} belongs to the live verdict ladder and must never be emitted "
            "by the replay harness"
        )
    if v not in CONVICTION_VERDICTS:
        raise ValueError(
            f"{v!r} is not a conviction verdict; allowed: {sorted(CONVICTION_VERDICTS)}"
        )
    return v


def assert_tournament_verdict(verdict: str) -> str:
    """Guard a tournament verdict on its way into an artifact."""
    v = str(verdict)
    if v in FORBIDDEN_LIVE_VERDICTS:
        raise ValueError(
            f"{v!r} belongs to the live verdict ladder and must never be emitted "
            "by the replay harness"
        )
    if v not in TOURNAMENT_VERDICTS:
        raise ValueError(
            f"{v!r} is not a tournament verdict; allowed: {sorted(TOURNAMENT_VERDICTS)}"
        )
    return v


def assert_fingerprint_verdict(verdict: str) -> str:
    """Guard a fingerprint verdict on its way into an artifact."""
    v = str(verdict)
    if v in FORBIDDEN_LIVE_VERDICTS:
        raise ValueError(
            f"{v!r} belongs to the live verdict ladder and must never be emitted "
            "by the replay harness"
        )
    if v not in FINGERPRINT_VERDICTS:
        raise ValueError(
            f"{v!r} is not a fingerprint verdict; allowed: "
            f"{sorted(FINGERPRINT_VERDICTS)}"
        )
    return v


def assert_replay_verdict(verdict: str) -> str:
    """Guard a verdict string on its way into an artifact."""
    v = str(verdict)
    if v in FORBIDDEN_LIVE_VERDICTS:
        raise ValueError(
            f"{v!r} belongs to the live verdict ladder and must never be emitted "
            "by the replay harness"
        )
    if v not in REPLAY_VERDICTS:
        raise ValueError(
            f"{v!r} is not a replay verdict; allowed: {sorted(REPLAY_VERDICTS)}"
        )
    return v


# ── provider-fetch gate ─────────────────────────────────────────────────────


class FetchNotAuthorised(RuntimeError):
    """Raised when a fetching stage runs without --execute-fetch."""


def add_safety_args(parser, *, fetches: bool = False) -> None:
    """Attach the standard safety flags to an argparse parser."""
    parser.add_argument(
        "--execute-fetch",
        action="store_true",
        help="Authorise live provider calls. Without it the stage plans only.",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="Hard ceiling on provider calls for this stage.",
    )
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="Repo root (tests point this at a sandbox).",
    )


def require_execute_fetch(args, *, planned_calls: int, what: str) -> None:
    """Refuse to touch a provider unless the operator passed --execute-fetch."""
    if not getattr(args, "execute_fetch", False):
        raise FetchNotAuthorised(
            f"{what}: {planned_calls:,} provider calls planned. "
            "Re-run with --execute-fetch to authorise. No calls were made."
        )


def run_cli(fn, args) -> int:
    """Invoke a stage, turning a refused fetch into a clean exit code.

    A missing --execute-fetch is an expected operator outcome, not a crash: it
    prints one line and returns 3, so a traceback never obscures the fact that
    zero provider calls were made.
    """
    try:
        return fn(args)
    except FetchNotAuthorised as e:
        print(f"\nREFUSED: {e}")
        return 3
    except LiveArtifactTouched as e:
        print(f"\nABORTED: {e}")
        return 4
    except ReplayWriteViolation as e:
        print(f"\nABORTED: {e}")
        return 5


def enforce_call_cap(planned_calls: int, cap: int | None, *, what: str) -> None:
    """Abort before the first call when the plan exceeds the cap."""
    if cap is not None and planned_calls > cap:
        raise FetchNotAuthorised(
            f"{what}: {planned_calls:,} planned calls exceed --max-calls {cap:,}. "
            "No calls were made."
        )


# ── misc ────────────────────────────────────────────────────────────────────

REPLAY_WINDOW_START = "2022-01-01"
REPLAY_WINDOW_END = "2025-12-31"
HORIZONS: tuple[int, ...] = (5, 10, 20, 30, 45, 60)
BENCHMARKS: tuple[str, ...] = ("SPY", "QQQ", "IWM")

# 2021 is excluded from the primary window: the delisted-company harvest
# returned materially incomplete coverage for names that stopped trading in
# 2021, so a 2021 replay would silently re-introduce survivorship bias.
EXCLUDED_YEARS_REASON = (
    "2021 excluded — delisted-company coverage was materially incomplete for "
    "that year, which would re-introduce survivorship bias."
)


def offline_env() -> None:
    """Force the cred-free, provider-free import path for replay stages."""
    os.environ["GEM_TRADER_SKIP_DOTENV"] = "true"
    os.environ.setdefault("FMP_API_KEY", "offline")
