"""
tests/unit/test_options_feed_factory.py — factory + Tradier 401 auto-disable.

Phase 3A: factory is Tradier-only (Alpaca removed from active code path).

Covers:
  - load_options_feed() returns the chain wrapper when Tradier is
    configured, ``None`` otherwise.
  - Tradier auto-disables itself after the first 401/403 response so
    subsequent calls short-circuit (no log spam, no wasted network).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── factory ──────────────────────────────────────────────────────────

def test_factory_returns_none_when_tradier_not_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TRADIER_API_TOKEN", raising=False)
    import core.options_feed_factory as f
    with patch.object(f, "_load_tradier_feed",
                      return_value=_unconfigured_fake("tradier")):
        assert f.load_options_feed() is None


def test_factory_returns_chain_when_tradier_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADIER_API_TOKEN", "fake-token")
    import core.options_feed_factory as f
    with patch.object(f, "_load_tradier_feed",
                      return_value=_configured_fake("tradier")):
        chain = f.load_options_feed()
        assert chain is not None
        assert chain.is_configured()
        assert chain.status()["feed_order"] == ["tradier"]


def test_factory_returns_none_when_tradier_import_fails(monkeypatch: pytest.MonkeyPatch):
    import core.options_feed_factory as f
    with patch.object(f, "_load_tradier_feed", return_value=None):
        assert f.load_options_feed() is None


# ── Tradier 401 auto-disable ─────────────────────────────────────────

def _load_tradier_module():
    """Load the Tradier feed module without polluting other tests."""
    spec = importlib.util.spec_from_file_location(
        "tradier_under_test",
        Path(__file__).resolve().parents[2] / "legacy" / "tradier_options_feed.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _http_response(status_code: int, payload=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = RuntimeError(f"{status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_tradier_auto_disables_on_401(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADIER_API_TOKEN", "fake-token")
    monkeypatch.setenv("TRADIER_USE_SANDBOX", "false")
    module = _load_tradier_module()
    feed = module.TradierOptionsFeed()
    assert feed.is_configured() is True

    with patch.object(module.requests, "get",
                      return_value=_http_response(401)) as g:
        # First call: makes the HTTP request, sees 401, marks unconfigured.
        result = feed.get_expirations("AAPL")
        assert result == ()
        assert feed.is_configured() is False
        # Subsequent calls: short-circuit, no further HTTP.
        feed.get_expirations("MSFT")
        feed.get_chain("MSFT", "2026-06-19")
        assert g.call_count == 1


def test_tradier_auto_disables_on_403(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADIER_API_TOKEN", "fake-token")
    module = _load_tradier_module()
    feed = module.TradierOptionsFeed()
    with patch.object(module.requests, "get",
                      return_value=_http_response(403)):
        feed.get_expirations("AAPL")
    assert feed.is_configured() is False


def test_tradier_does_not_disable_on_500(monkeypatch: pytest.MonkeyPatch):
    """Transient 5xx errors should NOT auto-disable — they may recover."""
    monkeypatch.setenv("TRADIER_API_TOKEN", "fake-token")
    module = _load_tradier_module()
    feed = module.TradierOptionsFeed()
    with patch.object(module.requests, "get",
                      return_value=_http_response(500)):
        feed.get_expirations("AAPL")
    assert feed.is_configured() is True


# ── helpers ──────────────────────────────────────────────────────────

class _FactoryFake:
    def __init__(self, name: str, configured: bool):
        self.NAME = name
        self._configured = configured

    def is_configured(self) -> bool:
        return self._configured

    def get_expirations(self, ticker: str):
        return ()

    def get_chain(self, ticker: str, expiry: str):
        return None


def _configured_fake(name: str) -> _FactoryFake:
    return _FactoryFake(name, configured=True)


def _unconfigured_fake(name: str) -> _FactoryFake:
    return _FactoryFake(name, configured=False)
