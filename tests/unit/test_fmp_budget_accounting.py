"""``FMPClient._get`` must charge the budget for what the provider served.

Before 2026-09 the counter was incremented ahead of the HTTP request while
``log_endpoint()`` ran only after a success.  Anything that failed — a stub
key 401, a timeout, a blocked call in a unit test — therefore moved the
monthly counter with no ``fmp_endpoint_log`` row to explain it.  That counter
is not idle telemetry: ``research/fmp_budget.py`` sizes every provider run
off it, so phantom spend quietly shrank the real budget.

The rule pinned here: the rate bucket gates attempts, the counter and the log
book a call together once a response comes back (error status included,
because FMP served and charged for it), and a request that never got a
response is charged to neither.
"""

from __future__ import annotations

import json

import pytest
import requests

from core.fmp_client import FMPClient

# tests/conftest.py's autouse guard replaces FMPClient._get to keep unit tests
# off the wire.  Capture the real function at import time — before the fixture
# runs — so these tests can exercise it directly with a stubbed session.
_REAL_GET = FMPClient._get


class _Gate:
    def __init__(self):
        self.budget = 0
        self.logged = []

    def budget_consume(self, n=1):
        self.budget += n
        return True

    def log_endpoint(self, endpoint, saved=0, resp_bytes=0):
        self.logged.append((endpoint, saved, resp_bytes))


class _Bucket:
    def __init__(self):
        self.consumed = 0

    def consume(self, n=1):
        self.consumed += n


class _Resp:
    """Minimal requests.Response stand-in."""

    def __init__(self, body=b'{"ok": true}', status=200):
        self.content = body
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise requests.HTTPError(f"{self._status} Server Error")

    def json(self):
        return json.loads(self.content)


class _Session:
    def __init__(self, resp=None, exc=None, on_get=None):
        self._resp, self._exc, self._on_get = resp, exc, on_get
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self._on_get:
            self._on_get()
        if self._exc:
            raise self._exc
        return self._resp


def _client(session):
    c = FMPClient.__new__(FMPClient)          # no creds, no real session
    c._gate, c._bucket, c._session = _Gate(), _Bucket(), session
    return c


def test_success_books_one_call_and_one_log_row():
    c = _client(_Session(_Resp(b'{"symbol": "NVDA"}')))
    assert _REAL_GET(c, "/profile", {"symbol": "NVDA"}) == {"symbol": "NVDA"}
    assert c._gate.budget == 1
    assert c._gate.logged == [("/profile", 0, len(b'{"symbol": "NVDA"}'))]
    assert c._bucket.consumed == 1


def test_budget_is_not_charged_before_the_request():
    """The original defect, stated directly."""
    seen = {}

    def _at_request_time():
        seen["budget"] = c._gate.budget
        seen["logged"] = len(c._gate.logged)
        seen["bucket"] = c._bucket.consumed

    c = _client(_Session(_Resp(), on_get=_at_request_time))
    _REAL_GET(c, "/profile")
    # Rate limiting still applies to the attempt...
    assert seen["bucket"] == 1
    # ...but nothing is charged or logged until a response exists.
    assert seen["budget"] == 0 and seen["logged"] == 0
    assert c._gate.budget == 1


def test_transport_failure_charges_nothing():
    """No response came back, so the provider served nothing."""
    c = _client(_Session(exc=requests.ConnectionError("no route")))
    with pytest.raises(requests.ConnectionError):
        _REAL_GET(c, "/profile")
    assert c._gate.budget == 0
    assert c._gate.logged == []
    assert c._bucket.consumed == 1, "an attempt still consumes the rate bucket"


def test_blocked_call_charges_nothing():
    """The unit-suite guard raises in place of the request — the leak that was
    measured at 14 real calls per run must now cost zero."""
    class _Blocked(RuntimeError):
        pass

    c = _client(_Session(exc=_Blocked("real FMP call")))
    for _ in range(3):
        with pytest.raises(_Blocked):
            _REAL_GET(c, "/profile")
    assert c._gate.budget == 0


def test_http_error_status_is_charged_and_logged():
    """A 401/429/5xx is a request FMP served — counter and log must agree."""
    c = _client(_Session(_Resp(b"Unauthorized", status=401)))
    with pytest.raises(requests.HTTPError):
        _REAL_GET(c, "/profile")
    assert c._gate.budget == 1
    assert c._gate.logged == [("/profile", 0, len(b"Unauthorized"))]


def test_unreadable_body_is_charged_and_logged():
    c = _client(_Session(_Resp(b"<html>maintenance</html>")))
    with pytest.raises(Exception):
        _REAL_GET(c, "/profile")
    assert c._gate.budget == 1
    assert len(c._gate.logged) == 1


def test_counter_and_log_never_diverge_across_a_mixed_run():
    """The invariant behind the fix: every counted call has a log row."""
    outcomes = [
        _Session(_Resp()),
        _Session(_Resp(b"nope", status=500)),
        _Session(exc=requests.Timeout("slow")),
        _Session(_Resp()),
        _Session(exc=requests.ConnectionError("down")),
    ]
    gate = _Gate()
    for s in outcomes:
        c = _client(s)
        c._gate = gate
        try:
            _REAL_GET(c, "/profile")
        except Exception:
            pass
    assert gate.budget == 3, "two transport failures must not be charged"
    assert len(gate.logged) == gate.budget


def test_budget_cost_is_honoured_on_both_gates():
    c = _client(_Session(_Resp()))
    _REAL_GET(c, "/batch-quote", {"symbols": "A,B"}, budget_cost=4)
    assert c._gate.budget == 4
    assert c._bucket.consumed == 4
