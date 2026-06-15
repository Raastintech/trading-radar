"""Phase 1J.2 options snapshot timer / health guardrail tests."""
from __future__ import annotations

import ast
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import strategy_registry as reg  # noqa: E402
from research import options_chain_snapshot_health as health  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_research_cycle.sh"
SERVICE = ROOT / "scripts" / "systemd" / "gem-trader-options-snapshot.service"
TIMER = ROOT / "scripts" / "systemd" / "gem-trader-options-snapshot.timer"


# ---------------------------------------------------------------------------
# Research-cycle command and timer units
# ---------------------------------------------------------------------------

def test_research_cycle_exposes_snapshot_commands():
    text = RUNNER.read_text(encoding="utf-8")
    assert "cmd_options_chain_snapshot()" in text
    assert "cmd_options_chain_snapshot_quality()" in text
    assert "options-chain-snapshot)" in text
    assert "options-chain-snapshot-quality)" in text


def test_timer_command_is_data_collection_only():
    text = RUNNER.read_text(encoding="utf-8")
    block = text[text.index("cmd_options_chain_snapshot()"):text.index("cmd_options_chain_snapshot_quality()")]
    assert "DATA_COLLECTION_ONLY" in block
    assert "options_chain_snapshot_collector.py" in block
    # The collector failure must propagate (return 1), not be swallowed.
    assert "return 1" in block
    for forbidden in ("paper_signal", "submit_", "order_manager", "strategy_registry"):
        assert forbidden not in block


def test_systemd_units_match_repo_conventions():
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")
    assert "run_research_cycle.sh options-chain-snapshot" in service
    assert "WorkingDirectory=/home/gem/trading-production" in service
    assert "EnvironmentFile=/home/gem/secure/trading.env" in service
    assert "OnCalendar=Mon-Fri 15:45 America/New_York" in timer
    assert "Persistent=true" in timer


# ---------------------------------------------------------------------------
# Health checker
# ---------------------------------------------------------------------------

NOW = datetime(2026, 6, 12, 22, 30, tzinfo=timezone.utc)  # Friday, after window
CAL = pd.DatetimeIndex(pd.bdate_range("2026-05-01", "2026-06-12"))


def _write_snapshot(root: Path, day: str, symbol: str = "SPY") -> None:
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"underlying": symbol, "strike": 100.0}]).to_parquet(d / f"{symbol}.parquet", index=False)


@pytest.fixture()
def health_env(tmp_path, monkeypatch):
    root = tmp_path / "options_snapshots"
    monkeypatch.setattr(health, "SNAPSHOT_ROOT", root)
    monkeypatch.setattr(health, "QUALITY_JSON", tmp_path / "quality.json")
    monkeypatch.setattr(health, "trading_calendar", lambda: CAL)
    return root


def test_health_detects_fresh_snapshot(health_env, tmp_path):
    _write_snapshot(health_env, "2026-06-12")
    (tmp_path / "quality.json").write_text(json.dumps({
        "generated_at": "t", "symbols_collected": ["SPY"], "total_contracts_latest_day": 1660,
        "per_symbol": {"SPY": {"verdict": {"usable_for_future_backtesting": True}}},
    }))

    res = health.build_report(now=NOW)

    assert res["status"] == "OK"
    assert res["last_snapshot_date"] == "2026-06-12"
    assert res["todays_snapshot_exists"] is True
    assert res["missed_day_count"] == 0
    assert res["quality_summary"]["usable_symbols"] == ["SPY"]
    assert res["strategy_status"] == "DATA_COLLECTION_ONLY"


def test_health_detects_missing_today(health_env):
    _write_snapshot(health_env, "2026-06-11")

    res = health.build_report(now=NOW)

    assert res["status"] == "MISSING_TODAY"
    assert res["expected_snapshot_date"] == "2026-06-12"
    assert res["todays_snapshot_exists"] is False


def test_health_grace_window_before_collection_time(health_env):
    _write_snapshot(health_env, "2026-06-11")
    early = datetime(2026, 6, 12, 14, 0, tzinfo=timezone.utc)  # before 15:45 ET

    res = health.build_report(now=early)

    assert res["today_pending_collection_window"] is True
    assert res["expected_snapshot_date"] == "2026-06-11"
    assert res["status"] == "OK"


def test_health_detects_stale_and_error(health_env):
    _write_snapshot(health_env, "2026-06-08")
    res = health.build_report(now=NOW)
    assert res["status"] == "STALE"
    assert res["missed_day_count"] >= 1

    for f in (health_env / "2026-06-08").glob("*"):
        f.unlink()
    (health_env / "2026-06-08").rmdir()
    res = health.build_report(now=NOW)
    assert res["status"] == "ERROR"


def test_health_reports_ivr_gate_countdown(health_env):
    for day in ("2026-06-10", "2026-06-11", "2026-06-12"):
        _write_snapshot(health_env, day)

    res = health.build_report(now=NOW)

    assert res["ivr_gates"]["days_until_partial"] == 57
    assert res["ivr_gates"]["days_until_feasible"] == 117
    assert res["ivr_gates"]["partial_eta"] not in (None, "unlocked")


def test_health_checker_is_cache_only():
    forbidden_import_roots = {"execution", "governance", "broker", "live_capital", "council", "core"}
    forbidden_calls = ("load_options_feed(", "get_alpaca(", "requests.", "httpx.")
    text = Path(health.__file__).read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
            assert roots.isdisjoint(forbidden_import_roots)
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in forbidden_import_roots
    for needle in forbidden_calls:
        assert needle not in text, needle


# ---------------------------------------------------------------------------
# Safety guardrails
# ---------------------------------------------------------------------------

def test_no_paper_or_execution_surface_in_phase_1j2_modules():
    forbidden_calls = (
        "create_paper_signal(",
        "emit_paper_signal(",
        "insert_paper_signal(",
        "create_trade_proposal(",
        "emit_trade_proposal(",
        "submit_buy_order",
        "submit_sell_order",
        "submit_order",
    )
    text = Path(health.__file__).read_text(encoding="utf-8")
    for needle in forbidden_calls:
        assert needle not in text, needle


def test_production_registry_unchanged_and_short_a_frozen_after_phase_1j2():
    filters = importlib.import_module("research.scanner_truth.filters")

    assert filters.SNI_ATR_CONTRACTION_THRESH == 0.85
    assert filters.SNI_VOL_SPIKE_THRESH == 1.4
    assert filters.VOY_MAX_EXTENSION_MA50 == 0.12
    assert filters.VOY_MA200_FLOOR == 0.92
    assert reg.is_frozen_strategy("SHORT") is True
    assert reg.is_active_paper_strategy("SHORT") is False
    assert "OPTIONS_PREMIUM" not in reg.SLEEVE_REGISTRY
