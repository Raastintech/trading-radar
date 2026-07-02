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
