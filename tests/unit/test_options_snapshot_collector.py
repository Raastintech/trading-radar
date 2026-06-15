"""Phase 1J.1 options chain snapshot collector guardrail tests."""
from __future__ import annotations

import ast
import importlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import options_chain_snapshot_collector as coll  # noqa: E402
from research import options_chain_snapshot_quality as qual  # noqa: E402

TODAY = date(2026, 6, 12)
GUARD = coll.BudgetGuard()


class FakeFeed:
    """Mimics core.options_feed_factory's chained feed surface."""

    def __init__(self, expirations=None, calls=2, fail=False):
        self.expirations = expirations or ["2026-07-02", "2026-07-24", "2026-08-21", "2026-12-18"]
        self.last_served = "alpaca"
        self.last_enriched_by = "tradier"
        self.fail = fail
        self.chain_calls = 0

    def get_expirations(self, sym):
        if self.fail:
            raise RuntimeError("provider down")
        return list(self.expirations)

    def get_chain(self, sym, expiry):
        self.chain_calls += 1
        df = pd.DataFrame([
            {"strike": 100.0, "bid": 2.0, "ask": 2.2, "volume": 10, "openInterest": 500,
             "impliedVolatility": 0.22, "delta": 0.45, "gamma": 0.02, "theta": -0.05,
             "vega": 0.10, "rho": 0.01, "lastPrice": 2.1, "contractSymbol": f"{sym}X{expiry}"},
            {"strike": 105.0, "bid": 0.0, "ask": 0.0, "volume": 0, "openInterest": None,
             "impliedVolatility": 0.0, "delta": None, "lastPrice": None, "contractSymbol": f"{sym}Y{expiry}"},
            {"strike": 250.0, "bid": 1.0, "ask": 1.1, "volume": 5, "openInterest": 10,
             "impliedVolatility": 0.30, "delta": 0.05, "lastPrice": 1.05, "contractSymbol": f"{sym}Z{expiry}"},
        ])
        return {"calls": df, "puts": df.copy()}


@pytest.fixture()
def snapshot_root(tmp_path, monkeypatch):
    root = tmp_path / "options_snapshots"
    monkeypatch.setattr(coll, "SNAPSHOT_ROOT", root)
    monkeypatch.setattr(qual, "SNAPSHOT_ROOT", root)
    return root


def _collect(snapshot_root, **kwargs):
    return coll.collect(
        symbols=["SPY"], feed=kwargs.pop("feed", FakeFeed()),
        spot_fn=kwargs.pop("spot_fn", lambda sym: 100.0),
        today=TODAY, **kwargs,
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_snapshot_rows_match_schema(snapshot_root):
    res = _collect(snapshot_root)
    assert res["symbols"]["SPY"]["write"]["written"] is True
    frame = pd.read_parquet(coll.snapshot_path("2026-06-12", "SPY"))

    assert list(frame.columns) == list(coll.SCHEMA_COLUMNS)
    row = frame.iloc[0]
    assert row["underlying"] == "SPY"
    assert row["option_type"] in {"call", "put"}
    assert row["as_of_date"] == "2026-06-12"
    assert row["provider"] == "alpaca+tradier"
    assert 20 <= int(row["dte"]) <= 70
    json.loads(row["data_quality_flags"])
    json.loads(row["raw_provider_fields"])


def test_strike_band_filters_far_strikes(snapshot_root):
    _collect(snapshot_root)
    frame = pd.read_parquet(coll.snapshot_path("2026-06-12", "SPY"))
    # spot=100, band 30% -> the 250 strike row must be excluded.
    assert frame["strike"].max() <= 130.0


# ---------------------------------------------------------------------------
# Append-only / idempotent
# ---------------------------------------------------------------------------

def test_same_day_rerun_is_idempotent_and_never_overwrites(snapshot_root):
    first = _collect(snapshot_root)
    path = Path(first["symbols"]["SPY"]["write"]["path"])
    original_bytes = path.read_bytes()
    original_calls = first["provider_calls_used"]

    second = _collect(snapshot_root)

    assert second["symbols"]["SPY"]["status"] == "skipped_idempotent"
    assert second["provider_calls_used"] == 0 < original_calls
    assert path.read_bytes() == original_bytes


def test_write_snapshot_refuses_to_overwrite(snapshot_root):
    rows = [{c: None for c in coll.SCHEMA_COLUMNS}]
    rows[0].update({"as_of_date": "2026-06-12", "underlying": "QQQ", "strike": 1.0})
    first = coll.write_snapshot(rows, as_of_date="2026-06-12", underlying="QQQ")
    assert first["written"] is True
    second = coll.write_snapshot(rows, as_of_date="2026-06-12", underlying="QQQ")
    assert second["written"] is False
    assert second["reason"] == "exists_idempotent_skip"


# ---------------------------------------------------------------------------
# Data quality flags
# ---------------------------------------------------------------------------

def test_quality_flags():
    good = {"bid": 2.0, "ask": 2.2, "mid": 2.1, "implied_volatility": 0.2, "delta": 0.4,
            "open_interest": 100.0, "volume": 10.0}
    assert coll.quality_flags(good, dte=45, guard=GUARD) == []

    dead = {"bid": None, "ask": None, "mid": None, "implied_volatility": None, "delta": None,
            "open_interest": None, "volume": 0.0}
    flags = coll.quality_flags(dead, dte=45, guard=GUARD)
    for expected in ("missing_bid_ask", "missing_iv", "missing_greeks", "missing_oi", "zero_volume", "stale_quote"):
        assert expected in flags

    crossed = dict(good, bid=2.5, ask=2.2)
    assert "crossed_market" in coll.quality_flags(crossed, dte=45, guard=GUARD)

    wide = dict(good, bid=1.0, ask=2.0, mid=1.5)
    assert "wide_spread" in coll.quality_flags(wide, dte=45, guard=GUARD)

    assert "expiration_out_of_range" in coll.quality_flags(good, dte=400, guard=GUARD)


# ---------------------------------------------------------------------------
# DTE filtering
# ---------------------------------------------------------------------------

def test_dte_selection_window_and_45_target():
    expirations = ["2026-06-19", "2026-07-02", "2026-07-24", "2026-08-21", "2026-12-18"]
    picks = coll.select_expirations(expirations, today=TODAY, guard=GUARD)
    # 2026-06-19 (7 DTE) and 2026-12-18 (189 DTE) are outside 20-70.
    assert "2026-06-19" not in picks and "2026-12-18" not in picks
    # 2026-07-24 (42 DTE) is closest to the 45 target and ranks first.
    assert picks[0] == "2026-07-24"


def test_dte_selection_respects_expiry_cap():
    guard = coll.BudgetGuard(max_expiries_per_symbol=1)
    picks = coll.select_expirations(["2026-07-02", "2026-07-24", "2026-08-07"], today=TODAY, guard=guard)
    assert len(picks) == 1


# ---------------------------------------------------------------------------
# Provider budget guard
# ---------------------------------------------------------------------------

def test_provider_budget_cap_stops_calls(snapshot_root):
    feed = FakeFeed()
    guard = coll.BudgetGuard(max_provider_calls_per_run=3)  # spot+expirations+1 chain
    res = coll.collect(symbols=["SPY"], feed=feed, spot_fn=lambda s: 100.0, today=TODAY, guard=guard)
    assert res["provider_calls_used"] <= 3
    assert feed.chain_calls == 1
    assert res["symbols"]["SPY"]["status"] == "partial_budget_exhausted"


def test_symbol_cap_limits_universe(snapshot_root):
    guard = coll.BudgetGuard(max_symbols_per_run=1)
    res = coll.collect(symbols=["SPY", "QQQ", "IWM"], feed=FakeFeed(), spot_fn=lambda s: 100.0, today=TODAY, guard=guard)
    assert res["universe"] == ["SPY"]


def test_dry_run_makes_zero_provider_calls(snapshot_root):
    feed = FakeFeed()
    res = coll.collect(symbols=["SPY"], feed=feed, dry_run=True, today=TODAY)
    assert res["dry_run"] is True
    assert feed.chain_calls == 0
    assert res["provider_calls_used"] == 0
    assert res["provider_configured"] == "not_checked_in_dry_run"
    assert "plan" in res


def test_clear_error_when_provider_not_configured(snapshot_root, monkeypatch):
    monkeypatch.setattr(coll, "_load_feed", lambda: None)

    res = coll.collect(symbols=["SPY"], spot_fn=lambda s: None, today=TODAY)

    assert res["provider_configured"] is False
    assert "not configured" in res["error"]
    assert res["provider_calls_used"] == 0


# ---------------------------------------------------------------------------
# Quality audit
# ---------------------------------------------------------------------------

def test_quality_audit_reads_snapshots_and_verdicts(snapshot_root):
    _collect(snapshot_root)
    res = qual.build_report()
    assert res["snapshot_days"] == ["2026-06-12"]
    assert res["symbols_collected"] == ["SPY"]
    spy = res["per_symbol"]["SPY"]
    assert spy["latest"]["contracts"] > 0
    assert spy["latest"]["bid_ask_coverage"] is not None
    assert isinstance(spy["verdict"]["usable_for_future_backtesting"], bool)
    assert res["strategy_status"] == "DATA_COLLECTION_ONLY"


# ---------------------------------------------------------------------------
# Safety guardrails
# ---------------------------------------------------------------------------

def test_status_marker_is_data_collection_only():
    assert coll.OPTIONS_PREMIUM_STRATEGY_STATUS == "DATA_COLLECTION_ONLY"


def test_no_paper_trade_or_execution_governance_imports_for_phase_1j1():
    forbidden_import_roots = {"execution", "governance", "broker", "live_capital", "council"}
    forbidden_calls = (
        "create_paper_signal(",
        "emit_paper_signal(",
        "insert_paper_signal(",
        "create_trade_proposal(",
        "emit_trade_proposal(",
        "submit_buy_order",
        "submit_sell_order",
        "close_position(",
        "submit_order",
        "strategy_registry.register",
    )
    for module in (coll, qual):
        text = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
                assert roots.isdisjoint(forbidden_import_roots), module.__name__
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in forbidden_import_roots, module.__name__
        for needle in forbidden_calls:
            assert needle not in text, f"{module.__name__}: {needle}"


def test_production_registry_unchanged_and_short_a_frozen_after_phase_1j1():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False
    assert "OPTIONS_PREMIUM" not in reg.SLEEVE_REGISTRY
