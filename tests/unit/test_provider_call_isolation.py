"""The unit suite must never reach a real provider, and never spend budget.

Background (2026-09-05). A full `pytest tests/unit` run was consuming 14 real
FMP calls. Two things combined to hide it:

* ``research_scanner._is_offline_fmp()`` treats only ``""``/``"offline"``/
  ``"stub"`` as offline, and ``tests/conftest.py`` stubs ``FMP_API_KEY`` as
  ``"test_fmp_key"`` — so ``build_scanner(offline=True)`` still called
  ``_batch_fmp_profiles``, which went to the wire.  The helper now honours
  ``offline=`` directly (pinned in ``test_phase5_research_engine.py``); the
  guard below stays for every other un-mocked path.
* ``core/fmp_client.py:_get`` called ``budget_consume()`` **before** the
  request but ``log_endpoint()`` only after a success, so the failed calls
  incremented the monthly counter while leaving no row in
  ``fmp_endpoint_log`` — spend with no audit trail.  That ordering is fixed
  (see ``tests/unit/test_fmp_budget_accounting.py``); this file pins the
  other half, that the calls do not happen at all.

``tests/conftest.py`` now installs a two-layer autouse guard. These tests pin it
in place so removing it fails loudly rather than quietly restoring the leak.
"""

from __future__ import annotations

import pytest
import requests


def _budget() -> int | None:
    """Current monthly FMP counter, or None when the DB is unavailable."""
    import sqlite3
    from pathlib import Path

    db = Path(__file__).resolve().parents[2] / "db" / "trading.db"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "select calls_used from fmp_budget_monthly order by month desc limit 1"
            ).fetchone()
        finally:
            con.close()
        return int(row[0]) if row else None
    except Exception:
        return None


def test_fmp_client_get_is_blocked():
    """Layer 1: FMPClient._get raises before it can consume budget."""
    from core.fmp_client import get_fmp

    with pytest.raises(Exception) as ei:
        get_fmp()._get("/profile", {"symbol": "ZZZZNOTREAL"})
    assert "real FMP call" in str(ei.value)


def test_direct_session_to_fmp_is_blocked():
    """Layer 2: code bypassing FMPClient still cannot reach the FMP host."""
    with pytest.raises(Exception) as ei:
        requests.Session().get("https://financialmodelingprep.com/stable/profile")
    assert "real FMP call" in str(ei.value)


def test_non_fmp_hosts_are_not_blocked():
    """The guard is FMP-specific and must not break other HTTP in tests."""
    try:
        requests.Session().get("http://127.0.0.1:9/nope", timeout=0.2)
    except Exception as e:
        assert "real FMP call" not in str(e)


def test_public_client_methods_degrade_rather_than_crash():
    """Callers that already tolerate provider failure keep their behaviour."""
    from core.fmp_client import get_fmp

    assert get_fmp().get_company_profile("ZZZZNOTREAL") is None


def test_blocked_calls_do_not_consume_budget():
    """The whole point: a blocked call must cost zero monthly budget."""
    before = _budget()
    if before is None:
        pytest.skip("budget table unavailable")
    from core.fmp_client import get_fmp

    for _ in range(3):
        get_fmp().get_company_profile("ZZZZNOTREAL")
        with pytest.raises(Exception):
            get_fmp()._get("/profile", {"symbol": "ZZZZNOTREAL"})
    assert _budget() == before, "a blocked provider call still consumed budget"


def test_scanner_profile_batch_is_safe_under_the_guard():
    """The helper that leaked: it must return cleanly and spend nothing.

    Called without ``offline=`` — as any live nightly run does — the conftest
    stub key still routes it down the live branch. Under the guard that branch
    must return a well-formed result (it catches provider errors per ticker)
    and must not move the budget counter.
    """
    import research.research_scanner as rs

    before = _budget()
    out = rs._batch_fmp_profiles(["ZZZZNOTREAL", "QQQQNOTREAL"])

    assert set(out) == {"ZZZZNOTREAL", "QQQQNOTREAL"}
    assert all(v is None for v in out.values()), "guarded batch must yield no profiles"
    if before is not None:
        assert _budget() == before, "profile batch consumed budget despite the guard"
