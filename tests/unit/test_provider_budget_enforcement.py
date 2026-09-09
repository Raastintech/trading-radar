"""Guards for the provider spend gate (System Diet Phase 1, 2026-09-09).

Until 2026-09-09 the system described itself as budget-guarded while enforcing
nothing: ``budget_consume()`` always returned True and ``FMP_MONTHLY_BUDGET=0``
read as unlimited. A manual study spent 20,260 calls in one day and a single
unbounded ``--max-calls`` authorised 3,257 more.

These tests pin the three things that changed:

* a ceiling exists by default, and ``0`` means "use the default", not "unlimited";
* unlimited is reachable only through the named opt-in;
* a large PLANNED run refuses before the first call.
"""

from __future__ import annotations

import pytest

from core import provider_budget as PB


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts from an environment that configures nothing."""
    for k in ("ALLOW_UNLIMITED_PROVIDER_CALLS", "FMP_MONTHLY_BUDGET",
              "FMP_DAILY_BUDGET"):
        monkeypatch.delenv(k, raising=False)
    yield


# ── 1. a ceiling exists by default ──────────────────────────────────────────


def test_zero_does_not_mean_unlimited(monkeypatch):
    """The historical spelling of "unset" must not disable the gate."""
    monkeypatch.setenv("FMP_MONTHLY_BUDGET", "0")
    monkeypatch.setenv("FMP_DAILY_BUDGET", "0")
    assert PB.monthly_cap() == PB.DEFAULT_MONTHLY_CAP
    assert PB.daily_cap() == PB.DEFAULT_DAILY_CAP
    assert PB.allow_unlimited() is False


def test_defaults_are_enforced_when_nothing_is_configured():
    assert PB.monthly_cap() == PB.DEFAULT_MONTHLY_CAP
    assert PB.daily_cap() == PB.DEFAULT_DAILY_CAP
    assert PB.describe()["enforced"] is True


def test_spending_past_the_monthly_cap_is_refused():
    with pytest.raises(PB.ProviderBudgetExceeded, match="monthly provider budget"):
        PB.check_spend(used_month=PB.DEFAULT_MONTHLY_CAP, used_today=0, n=1)


def test_spending_past_the_daily_cap_is_refused():
    with pytest.raises(PB.ProviderBudgetExceeded, match="daily provider budget"):
        PB.check_spend(used_month=0, used_today=PB.DEFAULT_DAILY_CAP, n=1)


def test_the_20260905_shaped_day_would_now_be_refused():
    """20,260 calls in one day is what this gate exists to stop."""
    with pytest.raises(PB.ProviderBudgetExceeded):
        PB.check_spend(used_month=40_000, used_today=12_000, n=8_260)


def test_an_ordinary_cycle_is_not_refused():
    """A normal nightly (~1,200-6,000 calls) must pass untouched."""
    PB.check_spend(used_month=52_199, used_today=4_500, n=1)
    PB.check_spend(used_month=0, used_today=0, n=1_100)


def test_a_custom_ceiling_is_honoured(monkeypatch):
    monkeypatch.setenv("FMP_DAILY_BUDGET", "500")
    assert PB.daily_cap() == 500
    with pytest.raises(PB.ProviderBudgetExceeded):
        PB.check_spend(used_month=0, used_today=500, n=1)


def test_a_garbage_ceiling_falls_back_rather_than_disabling_the_gate(monkeypatch):
    monkeypatch.setenv("FMP_MONTHLY_BUDGET", "not-a-number")
    assert PB.monthly_cap() == PB.DEFAULT_MONTHLY_CAP


# ── 2. unlimited is an explicit, named opt-in ───────────────────────────────


def test_unlimited_requires_the_named_flag(monkeypatch):
    monkeypatch.setenv("ALLOW_UNLIMITED_PROVIDER_CALLS", "true")
    assert PB.allow_unlimited() is True
    assert PB.monthly_cap() is None and PB.daily_cap() is None
    assert PB.describe()["enforced"] is False
    PB.check_spend(used_month=10**9, used_today=10**9, n=10**6)   # no raise


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "maybe"])
def test_anything_other_than_an_affirmative_keeps_the_gate_on(monkeypatch, value):
    monkeypatch.setenv("ALLOW_UNLIMITED_PROVIDER_CALLS", value)
    assert PB.allow_unlimited() is False
    assert PB.monthly_cap() == PB.DEFAULT_MONTHLY_CAP


# ── 3. large PLANNED runs refuse before the first call ──────────────────────


def test_a_large_planned_run_refuses_by_default():
    with pytest.raises(PB.PlannedCallsNotAuthorised, match="confirmation threshold"):
        PB.assert_planned_calls_authorised(3_257, where="M1 price refresh")


def test_a_small_planned_run_passes():
    PB.assert_planned_calls_authorised(PB.PLANNED_CALL_CONFIRM_THRESHOLD)
    PB.assert_planned_calls_authorised(0)


def test_the_override_authorises_a_large_planned_run():
    PB.assert_planned_calls_authorised(3_257, override=True)


def test_the_refusal_names_the_flag_that_lifts_it():
    with pytest.raises(PB.PlannedCallsNotAuthorised, match=r"--allow-large-run"):
        PB.assert_planned_calls_authorised(500, override_flag="--allow-large-run")


def test_a_per_run_hard_cap_refuses_without_its_own_override():
    with pytest.raises(PB.PlannedCallsNotAuthorised, match="hard cap"):
        PB.assert_within_hard_cap(3_257, hard_cap=1_200)
    PB.assert_within_hard_cap(3_257, hard_cap=1_200, override=True)
    PB.assert_within_hard_cap(1_200, hard_cap=1_200)      # at the cap is fine


# ── 4. the gate is wired into the client path ───────────────────────────────


def test_the_gatekeeper_exposes_a_check_that_raises():
    """FMPClient._get calls budget_check() before the request."""
    from core.data_gatekeeper import Gatekeeper

    assert hasattr(Gatekeeper, "budget_check")

    class _Stub:
        budget_check = Gatekeeper.budget_check
        def budget_used_month(self):
            return PB.DEFAULT_MONTHLY_CAP
        def budget_used_today(self):
            return 0

    with pytest.raises(PB.ProviderBudgetExceeded):
        _Stub().budget_check(1)


def test_budget_check_runs_before_the_wire_in_fmp_client():
    """Order matters: refusing after the request would refuse a paid-for call.

    Read from the file, not from the attribute: ``tests/conftest.py`` replaces
    ``FMPClient._get`` with a blocker so no test can reach the wire, and that
    stub is what ``inspect.getsource`` on the attribute would return.
    """
    from pathlib import Path

    import core.fmp_client as fmp_client

    src = Path(fmp_client.__file__).read_text(encoding="utf-8")
    body = src[src.index("    def _get(self, path"):]
    body = body[:body.index("\n    def ", 1)]

    assert "budget_check" in body, "the spend gate is not wired into _get"
    assert body.index("budget_check") < body.index("self._session.get"), \
        "budget_check must run before the HTTP request"


# ── 5. the high-cost research module is wired to both gates ─────────────────


def test_the_price_refresh_module_has_a_per_run_hard_cap():
    """--max-calls was unbounded until 2026-09-09; one flag authorised 3,257."""
    import research.backtests.m1_price_refresh as MPR

    assert MPR.HARD_MAX_CALLS > 0
    assert MPR.HARD_MAX_CALLS < 3_257, \
        "the hard cap must sit below the run that motivated it"


def test_the_price_refresh_fetch_checks_both_gates_before_fetching():
    from pathlib import Path

    import research.backtests.m1_price_refresh as MPR

    src = Path(MPR.__file__).read_text(encoding="utf-8")
    assert "assert_planned_calls_authorised" in src
    assert "assert_within_hard_cap" in src
    # both must precede the authorisation-to-fetch call, so a refused run
    # never reaches the wire
    assert src.index("assert_within_hard_cap(") < src.index("require_execute_fetch(")


def test_the_price_refresh_exposes_both_override_flags():
    import research.backtests.m1_price_refresh as MPR

    dests = {a.dest for a in MPR.build_parser()._actions}
    assert "allow_large_run" in dests
    assert "allow_huge_run" in dests


def test_a_universe_wide_refresh_refuses_without_the_override():
    """The 3,257-call run this session made would now stop here."""
    import research.backtests.m1_price_refresh as MPR

    with pytest.raises(PB.PlannedCallsNotAuthorised):
        PB.assert_within_hard_cap(3_257, hard_cap=MPR.HARD_MAX_CALLS)
