"""
tests/conftest.py — Shared pytest fixtures and environment setup.

Sets stub environment variables so core.config does not raise
RuntimeError during test collection. All API clients must be mocked
in individual tests — these stubs only prevent import failures.
"""
import os

# Stub Alpaca credentials — prevents core.config from raising at import time.
# Tests must mock AlpacaClient and FMPClient; these stubs never reach the wire.
_STUB_ENV = {
    "ALPACA_API_KEY":    "test_key",
    "ALPACA_SECRET_KEY": "test_secret",
    "FMP_API_KEY":       "test_fmp_key",
    "PAPER_TRADING":     "true",
    "ALLOW_SHORTS":      "true",
}

for _key, _val in _STUB_ENV.items():
    if not os.environ.get(_key):
        os.environ[_key] = _val


# ── Production-sidecar write sandbox ─────────────────────────────────────────
# research_scanner's build_scanner() writes universe-build sidecars via
# module-level path constants.  Tests that exercise build_scanner without
# patching those paths were overwriting the REAL cache/research/ and logs/
# artifacts (2026-07-02 incident: a pytest run clobbered
# research_universe_build_latest.json with a 2-ticker fixture universe —
# the nightly refresh-universe-prices step reads that sidecar for its
# ticker list, so this would have degraded the price refresh to SPY-only).
# This autouse fixture redirects every scanner output path to tmp_path for
# every test; tests that explicitly patch the constants still win because
# their patch is applied after this one.
import pytest


# ── Real-provider network guard ──────────────────────────────────────────────
# The stub FMP_API_KEY above is not recognised as "offline" by
# research_scanner._is_offline_fmp() (which only treats ""/"offline"/"stub" as
# offline), so a test that reaches an un-mocked FMP code path issues a REAL
# HTTP request. It 401s on the stub key — and a 401 is a call FMP served and
# charged for, so it costs real monthly budget (measured: 14 calls per full
# unit run).
#
# This fixture makes that failure loud instead of silent. Any attempt to reach
# the FMP host during a unit test raises; tests must mock the client. Non-FMP
# hosts are untouched.
_FMP_HOSTS = ("financialmodelingprep.com",)


class RealProviderCallBlocked(RuntimeError):
    """A unit test attempted a real provider HTTP call."""


@pytest.fixture(autouse=True)
def _block_real_fmp_http(monkeypatch):
    """Two layers, because blocking the socket alone is not enough.

    Layer 1 intercepts FMPClient._get itself, so the failure names the fix
    (mock the client) instead of surfacing as a socket error from deep inside
    requests. Layer 2 catches anything that builds its own session and talks
    to FMP without going through the client at all.

    Neither layer costs budget: _get charges the counter only once a response
    comes back (tests/unit/test_fmp_budget_accounting.py pins that), so a
    blocked call is free by construction rather than by luck.

    Both raise RealProviderCallBlocked. Callers that already wrap provider
    access in try/except (get_company_profile and friends) degrade to None
    exactly as they do for any other request failure, so behaviour under test
    is unchanged — only the budget hit and the wire traffic disappear.
    """
    import requests

    # ── layer 1: the client's own entry point ───────────────────────────────
    try:
        from core.fmp_client import FMPClient

        def _blocked_get(self, path, params=None, budget_cost=1):
            raise RealProviderCallBlocked(
                f"unit test attempted a real FMP call: {path}\n"
                "Mock the client (e.g. patch research_scanner._batch_fmp_profiles "
                "or core.fmp_client.get_fmp) instead of reaching the wire."
            )

        monkeypatch.setattr(FMPClient, "_get", _blocked_get)
    except Exception:  # pragma: no cover - client unavailable in this context
        pass

    # ── layer 2: anything bypassing FMPClient ───────────────────────────────
    _real_request = requests.Session.request

    def _guarded(self, method, url, *args, **kwargs):
        if any(h in str(url) for h in _FMP_HOSTS):
            raise RealProviderCallBlocked(
                f"unit test attempted a real FMP call: {method} {url}\n"
                "Mock the client instead of reaching the wire."
            )
        return _real_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "request", _guarded)
    yield


@pytest.fixture(autouse=True)
def _sandbox_research_scanner_outputs(tmp_path, monkeypatch):
    import sys
    mod = sys.modules.get("research.research_scanner")
    if mod is None:
        try:
            import research.research_scanner as mod  # noqa: F811
        except Exception:
            yield
            return
    out = tmp_path / "_scanner_sandbox"
    monkeypatch.setattr(mod, "UNIVERSE_BUILD_JSON", out / "research_universe_build_latest.json", raising=False)
    monkeypatch.setattr(mod, "UNIVERSE_MISS_JSON",  out / "universe_miss_diagnostic_latest.json", raising=False)
    monkeypatch.setattr(mod, "UNIVERSE_BUILD_TXT",  out / "research_universe_build_latest.txt", raising=False)
    monkeypatch.setattr(mod, "SCANNER_JSON",        out / "research_scanner_latest.json", raising=False)
    monkeypatch.setattr(mod, "SCANNER_TXT",         out / "research_scanner_latest.txt", raising=False)
    yield
