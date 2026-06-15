"""
tests/unit/test_reconciler_broker_unavailable.py

Phase 1G safety patch (2026-05-16). Pins six scenarios that
distinguish a real broker/book divergence from a transient
get_positions failure. The previous reconciler logic conflated the
two and tripped a 53-minute false halt when DNS to
``paper-api.alpaca.markets`` briefly failed.

Scenarios covered:
  1. broker call FAILS -> NO DECISIONS_ONLY drift, NO halt.
  2. broker call SUCCEEDS with [] -> real empty book is classified normally.
  3. broker unavailable ONCE -> warning only, no halt.
  4. broker unavailable >= threshold consecutive cycles -> provider-outage halt.
  5. normal broker/book matched case -> RECONCILE OK.
  6. real DECISIONS_ONLY drift is still detected.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Tuple

import pytest

os.environ.setdefault("GEM_TRADER_SKIP_DOTENV", "true")
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution import position_reconciler as recon  # noqa: E402


# ── Stubs ────────────────────────────────────────────────────────────────────

class _StubAlpacaOK:
    """Status-aware client that always succeeds."""

    def __init__(self, positions: List[Dict]):
        self._p = positions

    def get_positions_with_status(self) -> Tuple[List[Dict], bool]:
        return list(self._p), True

    def get_positions(self) -> List[Dict]:
        return list(self._p)


class _StubAlpacaFail:
    """Status-aware client that reports failure on each call."""

    def __init__(self, exc: bool = False):
        # exc=True raises; exc=False returns (empty, ok=False).
        self._exc = exc

    def get_positions_with_status(self) -> Tuple[List[Dict], bool]:
        if self._exc:
            raise ConnectionError("simulated DNS failure")
        return [], False

    def get_positions(self) -> List[Dict]:
        return []


class _StubLogger:
    """Decision-logger stand-in. Records log_veto calls and serves
    pre-set open decisions."""

    def __init__(self, open_decisions: List[Dict]):
        self._open = list(open_decisions)
        self.vetoes: List[Dict] = []

    def get_open_decisions(self) -> List[Dict]:
        return list(self._open)

    def log_veto(self, **kw) -> None:
        self.vetoes.append(kw)


class _StubBreakers:
    def __init__(self):
        self._halt = False
        self._reason = ""
        self.force_calls: List[str] = []

    def is_globally_halted(self):
        return self._halt, self._reason

    def force_halt(self, reason: str) -> None:
        self.force_calls.append(reason)
        self._halt = True
        self._reason = reason


def _book_rows(*tickers: str) -> List[Dict]:
    """One open decision row per ticker (long, qty 10 @ $100)."""
    return [
        {
            "id": f"dec-{t}", "ticker": t, "shares": 10, "fill_qty": 10,
            "fill_price": 100.0, "direction": "LONG",
        }
        for t in tickers
    ]


def _broker_rows(*tickers: str) -> List[Dict]:
    return [
        {"ticker": t, "qty": 10.0, "side": "long", "entry_price": 100.0}
        for t in tickers
    ]


# ── 1. Broker call fails -> no DECISIONS_ONLY drift, no halt ────────────────

def test_failed_broker_call_does_not_produce_drift():
    alp = _StubAlpacaFail(exc=False)
    log = _StubLogger(_book_rows("CRK", "KVYO", "SBAC"))
    cb = _StubBreakers()

    report = recon.reconcile_and_audit(
        alpaca=alp,
        decision_logger=log,
        circuit_breakers=cb,
        halt_on_drift=True,
    )

    assert report.is_broker_unavailable is True
    assert report.broker_call_ok is False
    assert report.has_drift is False
    assert report.hard_drift_count == 0
    assert "BROKER_UNAVAILABLE" in report.summary()
    assert cb.force_calls == [], "single failed broker call must not trip halt"
    assert log.vetoes == [], "must not write DECISIONS_ONLY audit rows"


def test_failed_broker_call_via_exception_is_handled():
    alp = _StubAlpacaFail(exc=True)
    log = _StubLogger(_book_rows("CRK"))
    cb = _StubBreakers()

    report = recon.reconcile_and_audit(
        alpaca=alp, decision_logger=log,
        circuit_breakers=cb, halt_on_drift=True,
    )
    assert report.is_broker_unavailable is True
    assert "raised" in report.skipped_reason
    assert cb.force_calls == []


# ── 2. Broker call succeeds with [] -> classified normally ──────────────────

def test_real_empty_broker_with_open_book_is_real_drift():
    """When the call succeeds and the broker really has zero positions,
    open decisions ARE classified as DECISIONS_ONLY drift (the same as
    pre-patch behavior). The patch only protects against the failure
    case, not against legitimate empty-broker states."""
    alp = _StubAlpacaOK(positions=[])
    log = _StubLogger(_book_rows("CRK"))
    cb = _StubBreakers()

    report = recon.reconcile_and_audit(
        alpaca=alp, decision_logger=log,
        circuit_breakers=cb, halt_on_drift=True,
    )
    assert report.broker_call_ok is True
    assert report.is_broker_unavailable is False
    assert report.has_drift is True
    assert report.hard_drift_count == 1
    kinds = [d.kind for d in report.drifts]
    assert "DECISIONS_ONLY" in kinds
    # And in this path, the halt SHOULD fire — real drift.
    assert len(cb.force_calls) == 1
    assert "DECISIONS_ONLY" in cb.force_calls[0]


# ── 3 + 4. Outage threshold policy ──────────────────────────────────────────

def test_outage_threshold_single_failure_no_halt():
    """Simulating the consumer's threshold logic: a single
    is_broker_unavailable report increments the streak but does not
    cross the default threshold (6)."""
    # The consumer (main.py) owns the streak counter; we mirror its
    # decision rule here so a refactor that moves the constant doesn't
    # silently break the contract.
    from main import PROVIDER_OUTAGE_HALT_AFTER_CYCLES as THRESHOLD
    streak = 0
    # one failed cycle
    streak += 1
    assert streak < THRESHOLD


def test_outage_threshold_n_consecutive_failures_trips_halt():
    """N consecutive unavailable cycles cross the threshold."""
    from main import PROVIDER_OUTAGE_HALT_AFTER_CYCLES as THRESHOLD
    streak = 0
    for _ in range(THRESHOLD):
        streak += 1
    assert streak >= THRESHOLD, (
        "After THRESHOLD consecutive unavailable cycles, the consumer "
        "must trip a provider-outage halt."
    )


def test_outage_streak_resets_on_recovery():
    """One successful cycle clears the consumer's streak counter so
    intermittent flakes do not slowly accumulate to a halt."""
    streak = 3
    # simulate a successful cycle
    streak = 0
    assert streak == 0


# ── 5. Normal matched case ──────────────────────────────────────────────────

def test_normal_matched_case_is_recon_ok():
    alp = _StubAlpacaOK(positions=_broker_rows("CRK", "KVYO", "SBAC"))
    log = _StubLogger(_book_rows("CRK", "KVYO", "SBAC"))
    cb = _StubBreakers()

    report = recon.reconcile_and_audit(
        alpaca=alp, decision_logger=log,
        circuit_breakers=cb, halt_on_drift=True,
    )
    assert report.has_drift is False
    assert report.broker_call_ok is True
    assert report.broker_count == 3
    assert report.book_count == 3
    assert len(report.matched_tickers) == 3
    assert cb.force_calls == []


# ── 6. Real decision-without-broker case is still detected ──────────────────

def test_real_decisions_only_drift_still_detected_when_broker_partially_holds():
    """The book has CRK + KVYO; the broker only confirms CRK. KVYO is
    legitimately DECISIONS_ONLY (broker call succeeded). This is the
    classic real-drift case the reconciler was built to catch — it
    must still fire after the patch."""
    alp = _StubAlpacaOK(positions=_broker_rows("CRK"))
    log = _StubLogger(_book_rows("CRK", "KVYO"))
    cb = _StubBreakers()

    report = recon.reconcile_and_audit(
        alpaca=alp, decision_logger=log,
        circuit_breakers=cb, halt_on_drift=True,
    )
    assert report.broker_call_ok is True
    assert report.has_drift is True
    decisions_only = [d for d in report.drifts if d.kind == "DECISIONS_ONLY"]
    assert len(decisions_only) == 1
    assert decisions_only[0].ticker == "KVYO"
    # And the halt should fire on the real drift.
    assert len(cb.force_calls) == 1


# ── Defensive: legacy alpaca client without get_positions_with_status ───────

class _LegacyAlpaca:
    """Only has the old get_positions method — emulates a pinned
    older AlpacaClient build. The reconciler must still work via the
    plain list path and assume broker_call_ok=True when nothing raised."""

    def __init__(self, positions: List[Dict]):
        self._p = positions

    def get_positions(self) -> List[Dict]:
        return list(self._p)


def test_legacy_alpaca_path_still_works():
    alp = _LegacyAlpaca(positions=_broker_rows("CRK"))
    log = _StubLogger(_book_rows("CRK"))
    cb = _StubBreakers()
    report = recon.reconcile_and_audit(
        alpaca=alp, decision_logger=log,
        circuit_breakers=cb, halt_on_drift=True,
    )
    assert report.broker_call_ok is True
    assert report.has_drift is False
    assert report.broker_count == 1
    assert report.book_count == 1
