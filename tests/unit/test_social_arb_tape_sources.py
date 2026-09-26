"""Social Arb tape sources: yfinance is debug-only, the scheduled tape is cache-only.

Until 2026-09-26 the scheduled nightly run called yfinance for every tape
symbol (about 40 calls a night), and because the Alpaca tape returns nothing
post-decommission, yfinance had become the primary tape. CLAUDE.md says
yfinance is debug-only. These tests pin:

  - yfinance runs only on an explicit --allow-yfinance, never by default;
  - the scheduled runner never passes that flag;
  - the default tape comes from the local price cache (no provider call);
  - the frozen universe snapshot cannot pose as a fresh confirming tape.

Pure-Python tests: no network, no provider calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import pytest

from research import social_arb_radar as sar

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 26, 0, 30, tzinfo=timezone.utc)


def _stats() -> Dict[str, Any]:
    return {"source_status": {}, "source_errors": [], "api_attempts": {}}


def _write_bars(directory: Path, sym: str, last: str, n: int = 30,
                start_close: float = 50.0, step: float = 0.5,
                volume: float = 1_000_000.0) -> None:
    idx = pd.bdate_range(end=pd.Timestamp(last), periods=n)
    closes = [start_close + step * i for i in range(n)]
    pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes,
                  "volume": [volume] * n}, index=idx).to_parquet(directory / f"{sym}.parquet")


# ── the flag ────────────────────────────────────────────────────────────────


def test_yfinance_is_off_by_default():
    assert sar.yfinance_enabled(sar.parse_args(["--mode", "daily"])) is False


def test_yfinance_needs_explicit_allow_and_skip_still_wins():
    assert sar.yfinance_enabled(sar.parse_args(["--allow-yfinance"])) is True
    assert sar.yfinance_enabled(
        sar.parse_args(["--allow-yfinance", "--skip-yfinance"])) is False


def test_scheduled_runner_never_allows_yfinance():
    text = (REPO_ROOT / "scripts" / "run_research_cycle.sh").read_text()
    body = text.split("cmd_social_inner() {", 1)[1].split("\n}\n", 1)[0]
    assert "social_arb_radar.py --mode daily" in body
    invocations = [ln for ln in body.splitlines() if not ln.strip().startswith("#")]
    assert not any("--allow-yfinance" in ln for ln in invocations)


# ── score_candidates wiring ─────────────────────────────────────────────────


class _Stop(Exception):
    pass


def _run_to_tape_stage(monkeypatch, argv, cache_tape):
    calls = []
    monkeypatch.setattr(sar, "_alpaca_tape_context", lambda *a, **k: {})
    monkeypatch.setattr(sar, "_fetch_cache_tape", lambda symbols, stats, *a, **k: {
        s: v for s, v in cache_tape.items() if s in symbols})

    def fake_yf(symbols, stats):
        calls.append(list(symbols))
        return {}
    monkeypatch.setattr(sar, "_fetch_yfinance_tape", fake_yf)

    def stop(*a, **k):
        raise _Stop
    monkeypatch.setattr(sar, "_profile_context", stop)
    groups = {s: {"ticker": s, "mapping_confidence": 0.9, "freshness_hours": 5.0}
              for s in ("AAA", "BBB")}
    stats = _stats()
    with pytest.raises(_Stop):
        sar.score_candidates(groups, {}, sar.parse_args(argv), stats, {})
    return calls, stats


def test_scheduled_path_makes_no_yfinance_call(monkeypatch):
    calls, stats = _run_to_tape_stage(monkeypatch, ["--mode", "daily"],
                                      {"AAA": {"available": True}})
    assert calls == []
    assert "yfinance_history" not in stats["api_attempts"]
    assert "skipped" in stats["source_status"]["yfinance"]


def test_debug_flag_only_fills_symbols_the_cache_did_not_cover(monkeypatch):
    calls, _ = _run_to_tape_stage(monkeypatch, ["--mode", "daily", "--allow-yfinance"],
                                  {"AAA": {"available": True}})
    assert calls == [["BBB"]]


# ── the cache tape ──────────────────────────────────────────────────────────


def test_cache_tape_reads_local_parquet_without_a_provider(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("yfinance must not be touched")
    monkeypatch.setattr(sar, "yf", type("NoYF", (), {"Ticker": staticmethod(boom)}))
    _write_bars(tmp_path, "AAA", "2026-09-25")
    stats = _stats()
    tape = sar._fetch_cache_tape(["AAA", "MISSING"], stats, prices_dir=tmp_path, now=NOW)
    assert set(tape) == {"AAA"}
    row = tape["AAA"]
    assert row["source"] == "price_cache" and row["available"] is True
    assert row["last_bar_date"] == "2026-09-25" and row["bars_stale"] is False
    assert row["confirmation"] is True          # $50+ price, $50M+ volume, rising
    assert "yfinance_history" not in stats["api_attempts"]


def test_cache_tape_marks_old_bars_stale_and_unconfirmed(tmp_path):
    _write_bars(tmp_path, "OLD", "2026-09-10")
    row = sar._fetch_cache_tape(["OLD"], _stats(), prices_dir=tmp_path, now=NOW)["OLD"]
    assert row["bars_stale"] is True and row["confirmation"] is False


# ── the universe snapshot fallback ──────────────────────────────────────────


def _snapshot(generated_at: str) -> Dict[str, Any]:
    return {"generated_at": generated_at, "metadata": {"AAA": {
        "price": 50.0, "avg_dollar_vol_20": 5e7, "return_5d_pct": 2.0,
        "return_20d_pct": 5.0, "volume_ratio_5d": 1.2, "bars_stale": False}}}


def test_frozen_snapshot_supplies_no_tape():
    """The live snapshot was last written 2026-06-12 and still says bars_stale=False."""
    tape = sar._metadata_tape("AAA", _snapshot("2026-06-12T19:34:03+00:00"), now=NOW)
    assert tape["available"] is False
    assert tape["source"] == "universe_snapshot_stale"


def test_fresh_snapshot_still_works():
    fresh = (NOW - timedelta(days=1)).isoformat()
    tape = sar._metadata_tape("AAA", _snapshot(fresh), now=NOW)
    assert tape["available"] is True and tape["source"] == "universe_snapshot"
