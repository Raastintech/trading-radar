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
