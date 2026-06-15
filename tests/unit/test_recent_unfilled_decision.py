"""
tests/unit/test_recent_unfilled_decision.py

Unit coverage for DecisionLogger.has_recent_unfilled_decision — the
book-side backstop for resting-order dedup (complements has_open_decision,
which only sees filled positions). See the 2026-04-23 PEGA / 2026-05-04 CRS
stuck-row bursts: a DAY limit order left resting on a prior scan cycle
(position_opened=0, position_closed=0, order_id set) must block a re-submit
within the same session so duplicate resting orders don't accumulate.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def logger(tmp_path, monkeypatch):
    """A DecisionLogger bound to a throwaway DB."""
    import core.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "t.db")
    from core.decision_logger import DecisionLogger
    return DecisionLogger()


def _insert(log, *, ticker, strategy, ts, opened, closed, order_id):
    log._conn.execute(
        "INSERT INTO decisions (id, ts, ticker, strategy, direction, "
        "order_id, position_opened, position_closed) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (f"{ticker}-{ts}", ts, ticker, strategy, "LONG",
         order_id, opened, closed),
    )
    log._conn.commit()


def _ts(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def test_recent_unfilled_order_blocks(logger):
    _insert(logger, ticker="CRS", strategy="VOYAGER", ts=_ts(10),
            opened=0, closed=0, order_id="ord-1")
    assert logger.has_recent_unfilled_decision("CRS", "VOYAGER") is True
    assert logger.has_recent_unfilled_decision("crs") is True  # case-insensitive


def test_filled_position_is_not_unfilled(logger):
    _insert(logger, ticker="CRS", strategy="VOYAGER", ts=_ts(10),
            opened=1, closed=0, order_id="ord-1")
    assert logger.has_recent_unfilled_decision("CRS", "VOYAGER") is False


def test_closed_row_is_not_unfilled(logger):
    _insert(logger, ticker="CRS", strategy="VOYAGER", ts=_ts(10),
            opened=0, closed=1, order_id="ord-1")
    assert logger.has_recent_unfilled_decision("CRS", "VOYAGER") is False


def test_row_without_order_id_is_ignored(logger):
    # No order was ever submitted → nothing resting at the broker.
    _insert(logger, ticker="CRS", strategy="VOYAGER", ts=_ts(10),
            opened=0, closed=0, order_id="")
    assert logger.has_recent_unfilled_decision("CRS", "VOYAGER") is False


def test_stale_unfilled_beyond_window_does_not_block(logger):
    # Older than the default 390-min session window → DAY order has expired;
    # the next session may legitimately re-submit.
    _insert(logger, ticker="CRS", strategy="VOYAGER", ts=_ts(500),
            opened=0, closed=0, order_id="ord-old")
    assert logger.has_recent_unfilled_decision("CRS", "VOYAGER") is False
    # But a tighter caller-supplied window still sees a fresh one.
    _insert(logger, ticker="CRS", strategy="VOYAGER", ts=_ts(3),
            opened=0, closed=0, order_id="ord-new")
    assert logger.has_recent_unfilled_decision(
        "CRS", "VOYAGER", within_minutes=5) is True


def test_strategy_scoping(logger):
    _insert(logger, ticker="CRS", strategy="VOYAGER", ts=_ts(10),
            opened=0, closed=0, order_id="ord-1")
    # Same ticker, different strategy → not blocked when strategy-scoped.
    assert logger.has_recent_unfilled_decision("CRS", "SNIPER") is False
    # Strategy omitted → any strategy matches.
    assert logger.has_recent_unfilled_decision("CRS") is True
