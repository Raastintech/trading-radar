"""
tests/unit/test_research_startup_checks.py — Phase 4 / Phase 3A startup check tests.

Proves that:
  1. The research-stack check set contains exactly the right checks.
  2. Alpaca auth and Alpaca bars checks are NOT present.
  3. FMP auth is CRITICAL.
  4. price_cache, tradier are NON-CRITICAL.
  5. StartupState.halted / .degraded / .degraded_reasons work correctly.
  6. Individual check functions behave correctly when mocked.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_state(*results):
    """Build a StartupState from (name, passed, critical) tuples."""
    from core.startup_checks import CheckResult, StartupState
    s = StartupState()
    for name, passed, critical in results:
        s.results.append(CheckResult(
            name=name, passed=passed, critical=critical,
            message="ok" if passed else "failed", elapsed_ms=0.0
        ))
    return s


# ── StartupState property tests ───────────────────────────────────────────────

def test_state_ok_when_all_pass():
    s = _make_state(
        ("timezone", True, True),
        ("database", True, True),
        ("fmp_auth", True, True),
        ("price_cache", True, False),
    )
    assert s.halted is False
    assert s.degraded is False


def test_state_halted_on_critical_failure():
    s = _make_state(
        ("timezone", True, True),
        ("fmp_auth", False, True),   # critical fail
    )
    assert s.halted is True
    assert s.degraded is False


def test_state_degraded_on_non_critical_failure():
    s = _make_state(
        ("timezone", True, True),
        ("fmp_auth", True, True),
        ("price_cache", False, False),   # non-critical fail
    )
    assert s.halted is False
    assert s.degraded is True
    assert "price_cache" in s.degraded_reasons


def test_degraded_reasons_lists_only_non_critical():
    s = _make_state(
        ("fmp_auth",    True,  True),
        ("price_cache", False, False),
        ("tradier",     False, False),
    )
    reasons = s.degraded_reasons
    assert "price_cache" in reasons
    assert "tradier" in reasons
    assert "fmp_auth" not in reasons


def test_halted_overrides_degraded():
    """When a critical check fails, .degraded must be False even with non-critical fails."""
    s = _make_state(
        ("fmp_auth",    False, True),   # critical
        ("price_cache", False, False),  # non-critical
    )
    assert s.halted is True
    assert s.degraded is False


# ── run_startup_checks result shape ──────────────────────────────────────────

def test_run_startup_checks_check_names(monkeypatch):
    """run_startup_checks() must include the research-stack checks, not Alpaca checks."""
    from core import startup_checks

    # Stub every check fn to pass instantly
    _pass = lambda: None
    monkeypatch.setattr(startup_checks, "_check_timezone",    _pass)
    monkeypatch.setattr(startup_checks, "_check_database",    _pass)
    monkeypatch.setattr(startup_checks, "_check_fmp_auth",    _pass)
    monkeypatch.setattr(startup_checks, "_check_price_cache", _pass)
    monkeypatch.setattr(startup_checks, "_check_fmp_calendar",_pass)
    monkeypatch.setattr(startup_checks, "_check_cache_dir",   _pass)
    monkeypatch.setattr(startup_checks, "_check_tradier_configured", _pass)

    state = startup_checks.run_startup_checks()
    names = {r.name for r in state.results}

    # Must have research-stack checks
    assert "timezone"    in names
    assert "database"    in names
    assert "fmp_auth"    in names
    assert "price_cache" in names
    assert "tradier"     in names

    # Must NOT have Alpaca checks
    assert "alpaca_auth" not in names
    assert "alpaca_bars" not in names


def test_fmp_auth_is_critical(monkeypatch):
    """FMP auth must be a critical check — failure must halt."""
    from core import startup_checks

    _pass = lambda: None
    monkeypatch.setattr(startup_checks, "_check_timezone",    _pass)
    monkeypatch.setattr(startup_checks, "_check_database",    _pass)
    monkeypatch.setattr(startup_checks, "_check_fmp_auth",    lambda: (_ for _ in ()).throw(RuntimeError("bad key")))
    monkeypatch.setattr(startup_checks, "_check_price_cache", _pass)
    monkeypatch.setattr(startup_checks, "_check_fmp_calendar",_pass)
    monkeypatch.setattr(startup_checks, "_check_cache_dir",   _pass)
    monkeypatch.setattr(startup_checks, "_check_tradier_configured", _pass)

    state = startup_checks.run_startup_checks()
    assert state.halted is True
    fmp_result = next(r for r in state.results if r.name == "fmp_auth")
    assert fmp_result.critical is True
    assert fmp_result.passed is False


def test_price_cache_is_non_critical(monkeypatch):
    """price_cache failure must degrade, not halt."""
    from core import startup_checks

    _pass = lambda: None
    monkeypatch.setattr(startup_checks, "_check_timezone",    _pass)
    monkeypatch.setattr(startup_checks, "_check_database",    _pass)
    monkeypatch.setattr(startup_checks, "_check_fmp_auth",    _pass)
    monkeypatch.setattr(startup_checks, "_check_price_cache", lambda: (_ for _ in ()).throw(RuntimeError("no cache")))
    monkeypatch.setattr(startup_checks, "_check_fmp_calendar",_pass)
    monkeypatch.setattr(startup_checks, "_check_cache_dir",   _pass)
    monkeypatch.setattr(startup_checks, "_check_tradier_configured", _pass)

    state = startup_checks.run_startup_checks()
    assert state.halted is False
    assert state.degraded is True
    pc = next(r for r in state.results if r.name == "price_cache")
    assert pc.critical is False


def test_tradier_is_non_critical(monkeypatch):
    """tradier failure must degrade, not halt."""
    from core import startup_checks

    _pass = lambda: None
    monkeypatch.setattr(startup_checks, "_check_timezone",    _pass)
    monkeypatch.setattr(startup_checks, "_check_database",    _pass)
    monkeypatch.setattr(startup_checks, "_check_fmp_auth",    _pass)
    monkeypatch.setattr(startup_checks, "_check_price_cache", _pass)
    monkeypatch.setattr(startup_checks, "_check_fmp_calendar",_pass)
    monkeypatch.setattr(startup_checks, "_check_cache_dir",   _pass)
    monkeypatch.setattr(startup_checks, "_check_tradier_configured",
                        lambda: (_ for _ in ()).throw(RuntimeError("no token")))

    state = startup_checks.run_startup_checks()
    assert state.halted is False
    t = next(r for r in state.results if r.name == "tradier")
    assert t.critical is False


# ── Individual check functions ────────────────────────────────────────────────

def test_check_timezone_passes():
    from core.startup_checks import _check_timezone
    _check_timezone()  # should not raise


def test_check_fmp_auth_passes():
    import core.startup_checks as sc
    mock_fmp = MagicMock()
    mock_fmp.get_vix.return_value = 18.5
    # get_fmp is imported lazily inside _check_fmp_auth; patch at the source module
    with patch("core.fmp_client.get_fmp", return_value=mock_fmp):
        sc._check_fmp_auth()   # should not raise


def test_check_fmp_auth_raises_when_vix_none():
    import core.startup_checks as sc
    mock_fmp = MagicMock()
    mock_fmp.get_vix.return_value = None
    with patch("core.fmp_client.get_fmp", return_value=mock_fmp):
        with pytest.raises(RuntimeError, match="VIX"):
            sc._check_fmp_auth()


def test_check_fmp_auth_raises_when_vix_zero():
    import core.startup_checks as sc
    mock_fmp = MagicMock()
    mock_fmp.get_vix.return_value = 0
    with patch("core.fmp_client.get_fmp", return_value=mock_fmp):
        with pytest.raises(RuntimeError, match="VIX"):
            sc._check_fmp_auth()


def test_check_price_cache_passes(tmp_path, monkeypatch):
    import pandas as pd
    import numpy as np
    import core.config as cfg
    monkeypatch.setattr(cfg, "CACHE_DIR", tmp_path)
    prices = tmp_path / "prices"
    prices.mkdir()
    # Write 6 parquets (>= 5 threshold) including SPY with 10 rows
    for sym in ["SPY", "QQQ", "IWM", "XLK", "XLF", "AAPL"]:
        idx = pd.date_range("2025-01-01", periods=10, freq="B")
        df = pd.DataFrame({
            "open":   np.ones(10),
            "high":   np.ones(10),
            "low":    np.ones(10),
            "close":  np.ones(10) * 100,
            "volume": np.ones(10) * 1e6,
        }, index=idx)
        df.to_parquet(prices / f"{sym}.parquet")
    from core.startup_checks import _check_price_cache
    _check_price_cache()  # should not raise


def test_check_price_cache_raises_when_dir_missing(tmp_path, monkeypatch):
    import core.config as cfg
    monkeypatch.setattr(cfg, "CACHE_DIR", tmp_path / "nonexistent")
    from core.startup_checks import _check_price_cache
    with pytest.raises(RuntimeError, match="cache/prices/"):
        _check_price_cache()


def test_check_price_cache_raises_when_too_few_parquets(tmp_path, monkeypatch):
    import pandas as pd
    import numpy as np
    import core.config as cfg
    monkeypatch.setattr(cfg, "CACHE_DIR", tmp_path)
    prices = tmp_path / "prices"
    prices.mkdir()
    # Only 2 parquets (< 5 threshold)
    for sym in ["SPY", "QQQ"]:
        idx = pd.date_range("2025-01-01", periods=10, freq="B")
        df = pd.DataFrame({"close": np.ones(10) * 100}, index=idx)
        df.to_parquet(prices / f"{sym}.parquet")
    from core.startup_checks import _check_price_cache
    with pytest.raises(RuntimeError, match="only"):
        _check_price_cache()


def test_check_price_cache_raises_when_spy_missing(tmp_path, monkeypatch):
    import pandas as pd
    import numpy as np
    import core.config as cfg
    monkeypatch.setattr(cfg, "CACHE_DIR", tmp_path)
    prices = tmp_path / "prices"
    prices.mkdir()
    # 5 parquets but SPY not among them
    for sym in ["QQQ", "IWM", "XLK", "XLF", "AAPL"]:
        idx = pd.date_range("2025-01-01", periods=10, freq="B")
        df = pd.DataFrame({"close": np.ones(10) * 100}, index=idx)
        df.to_parquet(prices / f"{sym}.parquet")
    from core.startup_checks import _check_price_cache
    with pytest.raises(RuntimeError, match="SPY"):
        _check_price_cache()


def test_check_tradier_configured_passes(monkeypatch):
    monkeypatch.setenv("TRADIER_API_TOKEN", "tok_abc123")
    from core.startup_checks import _check_tradier_configured
    _check_tradier_configured()  # should not raise


def test_check_tradier_configured_raises_when_missing(monkeypatch):
    monkeypatch.delenv("TRADIER_API_TOKEN", raising=False)
    from core.startup_checks import _check_tradier_configured
    with pytest.raises(RuntimeError, match="TRADIER_API_TOKEN"):
        _check_tradier_configured()
