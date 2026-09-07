"""The FMP API key must never reach a log record.

The defect this pins, measured on 2026-09-07: ``requests`` puts the full
request URL inside its exception strings, the FMP key rides on every call as
the ``apikey`` query parameter, and ``_get`` logged those exceptions verbatim.
One rate-limited research pass wrote the live key to disk 1,546 times.

The rule pinned here: nothing in ``core.fmp_client`` renders a provider
exception, URL, or parameter map into a log record without redaction — on the
transport-failure path, the HTTP-status path, and the unreadable-response path
alike — while the endpoint path, the status code and the non-secret parameters
survive, because an unusable log is its own kind of failure.
"""

from __future__ import annotations

import json
import logging

import pytest
import requests

import core.config as cfg
import core.fmp_client as FC
from core.fmp_client import FMPClient

# tests/conftest.py's autouse guard replaces FMPClient._get to keep unit tests
# off the wire; capture the real function before the fixture runs.
_REAL_GET = FMPClient._get

KEY = cfg.FMP_API_KEY                      # the stub key from tests/conftest.py
URL = ("https://financialmodelingprep.com/stable/grades"
       f"?apikey={KEY}&symbol=TGEN&limit=1000")


class _Gate:
    def budget_consume(self, n=1):
        return True

    def log_endpoint(self, endpoint, saved=0, resp_bytes=0):
        pass


class _Bucket:
    def consume(self, n=1):
        pass


class _Resp:
    def __init__(self, body=b'{"ok": true}', status=200, url=URL):
        self.content = body
        self.status_code = status
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Client Error: for url: {self.url}", response=self)

    def json(self):
        return json.loads(self.content)


class _Session:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc

    def get(self, url, params=None, timeout=None):
        if self._exc:
            raise self._exc
        return self._resp


def _client(session):
    c = FMPClient.__new__(FMPClient)
    c._gate, c._bucket, c._session = _Gate(), _Bucket(), session
    return c


def _positions(text: str, needle: str) -> list[int]:
    out, i = [], text.find(needle)
    while i != -1:
        out.append(i)
        i = text.find(needle, i + 1)
    return out


def _assert_clean(caplog) -> str:
    """Every record emitted must be free of credentials, in every rendering."""
    assert caplog.records, "the failure path must still log something"
    blobs = [caplog.text] + [r.getMessage() for r in caplog.records]
    for blob in blobs:
        assert KEY not in blob, "the API key reached a log record"
        assert "apikey=" not in blob
        assert "https://" not in blob and "http://" not in blob
        # The parameter NAME may survive — that is useful debugging — but only
        # ever immediately followed by the redaction marker.
        for i in _positions(blob, "apikey"):
            assert FC._REDACTED in blob[i:i + 40], "an apikey value was rendered"
    return caplog.text


# ── the three logging paths inside _get ─────────────────────────────────────


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_http_error_status_paths_are_redacted(status, caplog):
    c = _client(_Session(_Resp(b"denied", status=status)))
    with caplog.at_level(logging.DEBUG, logger="core.fmp_client"):
        with pytest.raises(requests.HTTPError):
            _REAL_GET(c, "/grades", {"symbol": "TGEN", "limit": 1000})
    text = _assert_clean(caplog)
    assert "path=/grades" in text
    assert f"status={status}" in text
    assert "'symbol': 'TGEN'" in text and "1000" in text
    assert "HTTPError" in text


def test_network_failure_path_is_redacted(caplog):
    # No response came back, but the exception still carries the URL.
    exc = requests.ConnectionError(f"Max retries exceeded with url: {URL}")
    c = _client(_Session(exc=exc))
    with caplog.at_level(logging.DEBUG, logger="core.fmp_client"):
        with pytest.raises(requests.ConnectionError):
            _REAL_GET(c, "/grades", {"symbol": "TGEN"})
    text = _assert_clean(caplog)
    assert "path=/grades" in text and "ConnectionError" in text


def test_unreadable_response_path_is_redacted(caplog):
    c = _client(_Session(_Resp(b"<html>not json</html>")))
    with caplog.at_level(logging.DEBUG, logger="core.fmp_client"):
        with pytest.raises(Exception):
            _REAL_GET(c, "/grades", {"symbol": "TGEN"})
    text = _assert_clean(caplog)
    assert "path=/grades" in text


def test_a_secret_passed_as_a_param_is_masked_not_echoed(caplog):
    # Defensive: callers do not pass the key (it lives on the session), but if
    # one ever did, the parameter map must not echo it.
    c = _client(_Session(_Resp(b"denied", status=401)))
    with caplog.at_level(logging.DEBUG, logger="core.fmp_client"):
        with pytest.raises(requests.HTTPError):
            _REAL_GET(c, "/grades", {"symbol": "TGEN", "apikey": KEY})
    text = _assert_clean(caplog)
    assert FC._REDACTED in text
    assert "'symbol': 'TGEN'" in text, "non-secret params must survive"


# ── the helper methods that swallow exceptions ──────────────────────────────


@pytest.mark.parametrize("method,args", [
    ("get_analyst_grades", ("TGEN",)),
    ("get_insider_trading", ("TGEN",)),
    ("get_company_profile", ("TGEN",)),
])
def test_swallowing_helpers_redact_before_logging(method, args, caplog, monkeypatch):
    c = FMPClient.__new__(FMPClient)
    c._bucket, c._session = _Bucket(), _Session()

    class _MissGate(_Gate):
        def get(self, key, ttl):
            return None

        def put(self, key, value):
            pass

    c._gate = _MissGate()
    monkeypatch.setattr(
        FMPClient, "_get",
        lambda self, path, params=None, budget_cost=1: (_ for _ in ()).throw(
            requests.HTTPError(f"429 Client Error: for url: {URL}")))
    with caplog.at_level(logging.DEBUG, logger="core.fmp_client"):
        assert getattr(c, method)(*args) in ([], None)
    _assert_clean(caplog)


# ── the scrubbers themselves ────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    URL,
    f"429 Client Error: Too Many Requests for url: {URL}",
    f"{{'apikey': '{KEY}', 'symbol': 'AAPL'}}",
    f'params={{"api_key": "{KEY}"}}',
    f"access_token: {KEY}",
    f"Authorization: Bearer {KEY}",
    f"the key is {KEY} in plain prose",
])
def test_scrub_removes_every_shape_of_the_secret(raw):
    out = FC._scrub(raw)
    assert KEY not in out
    assert "apikey=" not in out
    assert "https://" not in out


def test_scrub_leaves_ordinary_text_alone():
    assert FC._scrub("the monkey=banana stays") == "the monkey=banana stays"
    assert FC._scrub("timeout after 15s") == "timeout after 15s"


def test_scrub_masks_an_underscored_key_name():
    # `\b` would not fire on the `key` inside `api_key`; the boundary used has to.
    assert "hunter2hunter2" not in FC._scrub("api_key=hunter2hunter2")


def test_safe_params_masks_only_secrets():
    out = FC._safe_params({"symbol": "AAPL", "limit": 1000, "apikey": KEY,
                           "token": "abc123456", "from": "2024-01-01"})
    assert out["symbol"] == "AAPL" and out["limit"] == 1000
    assert out["from"] == "2024-01-01"
    assert out["apikey"] == FC._REDACTED and out["token"] == FC._REDACTED


def test_safe_params_handles_none_and_empty():
    assert FC._safe_params(None) == {} and FC._safe_params({}) == {}


def test_safe_exc_keeps_the_type_and_drops_the_url():
    e = requests.HTTPError(f"429 Client Error: for url: {URL}")
    out = FC._safe_exc(e)
    assert out.startswith("HTTPError:")
    assert KEY not in out and "https://" not in out


def test_safe_exc_is_bounded():
    out = FC._safe_exc(requests.HTTPError("x" * 5000))
    assert len(out) <= FC._MAX_LOGGED_ERROR_CHARS + 40


def test_status_of_reads_a_response_when_there_is_one():
    r = _Resp(status=429)
    assert FC._status_of(requests.HTTPError("boom", response=r)) == 429
    assert FC._status_of(requests.ConnectionError("no route")) is None


def test_no_logging_call_in_the_module_renders_a_bare_exception():
    """A future edit that logs `exc` directly must fail here, not in production."""
    import re
    from pathlib import Path

    src = Path(FC.__file__).read_text()
    bad = [ln.strip() for ln in src.splitlines()
           if re.search(r"logger\.(error|warning|info|debug)\(", ln) or
           re.search(r"^\s+(path|ticker|chunk\[:3\]|_safe_)", ln)]
    joined = "\n".join(bad)
    assert not re.search(r",\s*exc\)", joined), (
        "a logger call renders a raw exception; wrap it in _safe_exc()")
    assert not re.search(r",\s*params\)", joined), (
        "a logger call renders a raw params map; wrap it in _safe_params()")
