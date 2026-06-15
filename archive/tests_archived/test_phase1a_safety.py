from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def test_config_defaults_are_paper_safe(monkeypatch):
    monkeypatch.setenv("GEM_TRADER_SKIP_DOTENV", "true")
    monkeypatch.setenv("ALPACA_API_KEY", "test_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
    monkeypatch.setenv("FMP_API_KEY", "test_fmp")
    monkeypatch.delenv("ALPACA_PAPER", raising=False)
    monkeypatch.delenv("PAPER_TRADING", raising=False)
    monkeypatch.delenv("ALLOW_LIVE_CAPITAL", raising=False)

    import core.config as cfg

    cfg = importlib.reload(cfg)
    assert cfg.ALPACA_PAPER is True
    assert cfg.PAPER_TRADING is True
    assert cfg.ALLOW_LIVE_CAPITAL is False


class _FakeOrder:
    id = "ord-test"
    client_order_id = "client-test"
    status = "filled"
    symbol = "AAPL"
    side = SimpleNamespace(value="buy")
    qty = 3
    filled_qty = 3
    filled_avg_price = 101.0
    limit_price = 101.0
    submitted_at = None
    filled_at = None


class _FakeTrading:
    def __init__(self) -> None:
        self.submit_calls = []
        self.close_calls = []

    def submit_order(self, request):
        self.submit_calls.append(request)
        return _FakeOrder()

    def close_position(self, ticker):
        self.close_calls.append(ticker)
        return _FakeOrder()


def _client_with_fake_trading(alp_mod):
    client = alp_mod.AlpacaClient.__new__(alp_mod.AlpacaClient)
    client._trading = _FakeTrading()
    return client


def _set_live_keys(monkeypatch, alp_mod, *, paper_trading, alpaca_paper, allow_live):
    monkeypatch.setattr(alp_mod.cfg, "PAPER_TRADING", paper_trading)
    monkeypatch.setattr(alp_mod.cfg, "ALPACA_PAPER", alpaca_paper)
    monkeypatch.setattr(alp_mod.cfg, "ALLOW_LIVE_CAPITAL", allow_live)
    monkeypatch.setattr(alp_mod.cfg, "LIVE_CONFIRM_FILE", "")


@pytest.mark.parametrize(
    "method,args",
    [
        ("submit_market_order", ("AAPL", 3, "buy")),
        ("submit_limit_order", ("AAPL", 3, "buy", 101.0)),
        ("close_position", ("AAPL",)),
    ],
)
def test_live_capital_gate_blocks_broker_actions_until_all_keys_enabled(
    monkeypatch, method, args
):
    from core import alpaca_client as alp_mod

    client = _client_with_fake_trading(alp_mod)
    _set_live_keys(
        monkeypatch,
        alp_mod,
        paper_trading=False,
        alpaca_paper=False,
        allow_live=False,
    )

    result = getattr(client, method)(*args)

    assert result is None
    assert client._trading.submit_calls == []
    assert client._trading.close_calls == []


@pytest.mark.parametrize(
    "method,args,call_attr",
    [
        ("submit_market_order", ("AAPL", 3, "buy"), "submit_calls"),
        ("submit_limit_order", ("AAPL", 3, "buy", 101.0), "submit_calls"),
        ("close_position", ("AAPL",), "close_calls"),
    ],
)
def test_live_capital_gate_allows_broker_actions_only_after_all_keys(
    monkeypatch, method, args, call_attr
):
    from core import alpaca_client as alp_mod

    client = _client_with_fake_trading(alp_mod)
    _set_live_keys(
        monkeypatch,
        alp_mod,
        paper_trading=False,
        alpaca_paper=False,
        allow_live=True,
    )

    result = getattr(client, method)(*args)

    assert result is not None
    assert getattr(client._trading, call_attr)


def test_close_position_still_works_in_paper(monkeypatch):
    from core import alpaca_client as alp_mod

    client = _client_with_fake_trading(alp_mod)
    _set_live_keys(
        monkeypatch,
        alp_mod,
        paper_trading=True,
        alpaca_paper=True,
        allow_live=False,
    )

    result = client.close_position("AAPL")

    assert result is not None
    assert client._trading.close_calls == ["AAPL"]


def test_reconciler_detects_required_mismatch_cases():
    from execution.position_reconciler import reconcile

    broker_only = reconcile([{"ticker": "AAPL", "qty": 10}], [])
    assert [d.kind for d in broker_only.drifts] == ["BROKER_ONLY"]

    decisions_only = reconcile([], [{"id": "d1", "ticker": "MSFT", "fill_qty": 5}])
    assert [d.kind for d in decisions_only.drifts] == ["DECISIONS_ONLY"]

    qty_mismatch = reconcile(
        [{"ticker": "NVDA", "qty": 10}],
        [{"id": "d2", "ticker": "NVDA", "fill_qty": 8}],
    )
    assert [d.kind for d in qty_mismatch.drifts] == ["QTY_MISMATCH"]

    price_mismatch = reconcile(
        [{"ticker": "TSLA", "qty": 10, "entry_price": 102.0}],
        [{"id": "d3", "ticker": "TSLA", "fill_qty": 10, "fill_price": 100.0}],
    )
    assert [d.kind for d in price_mismatch.drifts] == ["PRICE_DRIFT"]
    assert "broker_avg_px=102.0000" in price_mismatch.drifts[0].detail


def test_reconcile_and_audit_writes_reconcile_drift_row():
    from execution.position_reconciler import reconcile_and_audit

    class _Alpaca:
        def get_positions(self):
            return [{"ticker": "AAPL", "qty": 10}]

    class _Logger:
        def __init__(self):
            self.vetoes = []

        def get_open_decisions(self):
            return []

        def log_veto(self, **kw):
            self.vetoes.append(kw)

    logger = _Logger()
    report = reconcile_and_audit(
        alpaca=_Alpaca(),
        decision_logger=logger,
        halt_on_drift=False,
    )

    assert report.has_drift is True
    assert logger.vetoes
    assert logger.vetoes[0]["verdict"] == "RECONCILE_DRIFT"
    assert logger.vetoes[0]["agent"] == "position_reconciler"


def _write_regime_artifact(cache_dir: Path, *, stale_minutes: int = 0) -> Path:
    research_dir = cache_dir / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    path = research_dir / "regime_forecast_latest.json"
    path.write_text(
        json.dumps(
            {
                "strategy_favorability": {
                    "VOYAGER": {"stance": "favored", "reason": "ok"}
                }
            }
        ),
        encoding="utf-8",
    )
    if stale_minutes:
        ts = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).timestamp()
        os.utime(path, (ts, ts))
    return path


def _signal():
    return {
        "ticker": "AAPL",
        "direction": "LONG",
        "strategy": "VOYAGER",
    }


def _approved():
    return {"verdict": "APPROVED", "votes": {}}


def test_regime_missing_warns_open_in_paper(monkeypatch, tmp_path, caplog):
    from core import submission_gate as gate_mod

    monkeypatch.setattr(gate_mod.cfg, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(gate_mod.cfg, "PAPER_TRADING", True)
    monkeypatch.setattr(gate_mod.cfg, "ALPACA_PAPER", True)
    monkeypatch.setattr(gate_mod.cfg, "REGIME_STALE_BEHAVIOR_PAPER", "warn")
    monkeypatch.setattr(gate_mod.cfg, "REGIME_FRESHNESS_MAX_MINUTES", 10)

    allowed, reason, gate = gate_mod.evaluate(
        _signal(), _approved(), is_execution_allowed=lambda *_: True
    )

    assert allowed is True
    assert reason == ""
    assert gate == ""
    assert "REGIME_FRESHNESS_WARN" in caplog.text


def test_regime_stale_blocks_when_paper_configured_to_block(monkeypatch, tmp_path):
    from core import submission_gate as gate_mod

    _write_regime_artifact(tmp_path, stale_minutes=60)
    monkeypatch.setattr(gate_mod.cfg, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(gate_mod.cfg, "PAPER_TRADING", True)
    monkeypatch.setattr(gate_mod.cfg, "ALPACA_PAPER", True)
    monkeypatch.setattr(gate_mod.cfg, "REGIME_STALE_BEHAVIOR_PAPER", "block")
    monkeypatch.setattr(gate_mod.cfg, "REGIME_FRESHNESS_MAX_MINUTES", 10)

    allowed, reason, gate = gate_mod.evaluate(
        _signal(), _approved(), is_execution_allowed=lambda *_: True
    )

    assert allowed is False
    assert gate == "regime_freshness"
    assert "stale regime artifact" in reason


def test_regime_missing_blocks_live_side(monkeypatch, tmp_path):
    from core import submission_gate as gate_mod

    monkeypatch.setattr(gate_mod.cfg, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(gate_mod.cfg, "PAPER_TRADING", False)
    monkeypatch.setattr(gate_mod.cfg, "ALPACA_PAPER", False)
    monkeypatch.setattr(gate_mod.cfg, "REGIME_STALE_BEHAVIOR_LIVE", "block")
    monkeypatch.setattr(gate_mod.cfg, "REGIME_FRESHNESS_MAX_MINUTES", 10)

    allowed, reason, gate = gate_mod.evaluate(
        _signal(), _approved(), is_execution_allowed=lambda *_: True
    )

    assert allowed is False
    assert gate == "regime_freshness"
    assert "missing regime artifact" in reason


def test_regime_malformed_blocks_live_side(monkeypatch, tmp_path):
    from core import submission_gate as gate_mod

    research_dir = tmp_path / "research"
    research_dir.mkdir(parents=True, exist_ok=True)
    (research_dir / "regime_forecast_latest.json").write_text(
        json.dumps({"headline": {"current_regime": "unknown"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate_mod.cfg, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(gate_mod.cfg, "PAPER_TRADING", False)
    monkeypatch.setattr(gate_mod.cfg, "ALPACA_PAPER", False)
    monkeypatch.setattr(gate_mod.cfg, "REGIME_STALE_BEHAVIOR_LIVE", "block")
    monkeypatch.setattr(gate_mod.cfg, "REGIME_FRESHNESS_MAX_MINUTES", 10)

    allowed, reason, gate = gate_mod.evaluate(
        _signal(), _approved(), is_execution_allowed=lambda *_: True
    )

    assert allowed is False
    assert gate == "regime_freshness"
    assert "missing non-object strategy_favorability" in reason


def test_heartbeat_stale_trips_circuit_breaker_in_test_db(tmp_path):
    from scripts import heartbeat_deadman

    hb = tmp_path / "trader_heartbeat.json"
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    hb.write_text(
        json.dumps({"last_heartbeat_ts": old_ts, "heartbeat_stage": "test"}),
        encoding="utf-8",
    )
    db_path = tmp_path / "cb.db"

    rc = heartbeat_deadman.main(
        [
            "--heartbeat",
            str(hb),
            "--db-path",
            str(db_path),
            "--threshold-seconds",
            "300",
        ]
    )

    assert rc == 0
    with sqlite3.connect(str(db_path)) as con:
        row = con.execute(
            "SELECT halted, reason FROM circuit_breaker_state WHERE id=1"
        ).fetchone()
    assert row[0] == 1
    assert "heartbeat dead-man" in row[1]
