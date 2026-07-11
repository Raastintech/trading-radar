"""
tests/unit/test_discovery_bootstrap.py

P1/P2 proofs for the 2026-07-10 pipeline discovery repair:

  6.  Dynamic market symbols without parquet history enter the bootstrap queue.
  7.  New symbols become eligible (ranked-fill visible) after a successful
      bootstrap.
  8.  Invalid / delisted securities remain excluded.
  9.  Dead-symbol tombstoning requires repeated evidence — never one
      transient failure.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import os
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")

import research.universe_discovery_bootstrap as udb
from research.dead_symbol_tombstones import (
    DEAD_THRESHOLD_DAYS, confirmed_dead, derive_states,
    record_failure, record_review, record_success,
)


def _mk_parquet(tmp: Path, sym: str, bars: int = 120, price: float = 50.0,
                vol: float = 2_000_000.0) -> Path:
    # calendar-day frequency: an end date on a weekend breaks freq="B"
    # (length mismatch), and nothing here needs trading-day spacing.
    idx = pd.date_range(end=pd.Timestamp(date.today()), periods=bars, freq="D")
    prices = np.full(bars, price)
    df = pd.DataFrame({
        "open": prices, "high": prices * 1.01, "low": prices * 0.99,
        "close": prices, "volume": np.full(bars, vol),
    }, index=idx)
    path = tmp / f"{sym}.parquet"
    df.to_parquet(path)
    return path


def _screener(symbols: List[str]) -> List[Dict]:
    return [{"symbol": s, "companyName": s, "marketCap": 1e9,
             "price": 25.0, "volume": 900_000,
             "exchangeShortName": "NASDAQ"} for s in symbols]


def _patched(tmp_path, **extra):
    return (
        patch.object(udb, "PRICE_DIR", tmp_path),
        patch.object(udb, "OUT_JSON", tmp_path / "coverage.json"),
        patch.object(udb, "OUT_TXT", tmp_path / "coverage.txt"),
        patch.object(udb, "QUEUE_JSON", tmp_path / "queue.json"),
    )


# ── Proof 6: missing-history symbols enter the bootstrap queue ───────────────


def test_missing_history_symbols_enter_bootstrap_queue(tmp_path):
    _mk_parquet(tmp_path, "OLDCO")
    p = _patched(tmp_path)
    with p[0], p[1], p[2], p[3]:
        payload = udb.run(max_calls=10, execute=False,
                          screener_rows=_screener(["OLDCO", "NEWIPO"]))
        queue = json.loads((tmp_path / "queue.json").read_text())

    assert payload["eligible_security_master_count"] == 2
    assert payload["usable_price_history_count"] == 1
    assert payload["missing_history_count"] == 1
    assert "NEWIPO" in queue["pending"]
    assert "OLDCO" not in queue["pending"]
    assert payload["coverage_pct"] == 50.0


def test_queue_is_resumable_across_runs(tmp_path):
    p = _patched(tmp_path)
    with p[0], p[1], p[2], p[3]:
        udb.run(max_calls=10, execute=False, screener_rows=_screener(["AAA1"]))
        udb.run(max_calls=10, execute=False,
                screener_rows=_screener(["AAA1", "BBB2"]))
        queue = json.loads((tmp_path / "queue.json").read_text())
    assert set(queue["pending"]) == {"AAA1", "BBB2"}  # no dupes, both kept


# ── Proof 7: bootstrapped symbols become eligible for ranked fill ────────────


def test_bootstrap_admits_new_symbol_into_ranked_fill(tmp_path):
    calls: List[str] = []

    def fake_fetch(sym: str):
        calls.append(sym)
        _mk_parquet(tmp_path, sym, bars=150, price=30.0, vol=4_000_000)
        return {"ticker": sym, "ok": True, "provider_status": "ok",
                "bars_fetched": 150}

    p = _patched(tmp_path)
    with p[0], p[1], p[2], p[3], \
            patch.object(udb, "_fetch_bootstrap", fake_fetch), \
            patch.object(udb, "_is_offline_fmp", lambda: False):
        payload = udb.run(max_calls=10, execute=True,
                          screener_rows=_screener(["NEWCO"]))

    assert calls == ["NEWCO"]
    assert payload["bootstrap_attempted"] == 1
    assert payload["bootstrap_succeeded"] == 1
    assert payload["newly_admitted_today"] == ["NEWCO"]
    assert payload["coverage_pct"] == 100.0

    # …and the scanner's ranked fill can now see it.
    import research.research_scanner as rs
    _mk_parquet(tmp_path, "SPY", bars=300, price=100.0)
    with (
        patch.object(rs, "PRICE_DIR", tmp_path),
        patch.object(rs, "DEEP_PRICE_DIR", tmp_path / "_deep"),
    ):
        result, _ = rs._ranked_cache_fill(needed=10, seen=set())
    assert "NEWCO" in [sym for sym, _src in result]


# ── Proof 8: invalid / delisted securities stay excluded ─────────────────────


def test_invalid_symbols_never_enter_discovery(tmp_path):
    p = _patched(tmp_path)
    with p[0], p[1], p[2], p[3]:
        payload = udb.run(
            max_calls=10, execute=False,
            screener_rows=_screener(["GOOD", "AACIW", "BRK.B", "TOOLONGX"]))
        queue = json.loads((tmp_path / "queue.json").read_text())

    assert payload["eligible_security_master_count"] == 1
    assert payload["exclusions_by_reason"]["invalid_symbol"] == 3
    assert set(queue["pending"]) == {"GOOD"}


def test_confirmed_dead_symbols_are_excluded_from_discovery(tmp_path):
    ledger = tmp_path / "tombstones.jsonl"
    for _ in range(DEAD_THRESHOLD_DAYS):
        record_failure("DEADCO", "fmp_empty", ledger_path=ledger)
    # distinct-day requirement: rewrite events onto distinct days
    events = [json.loads(l) for l in ledger.read_text().splitlines()]
    for i, ev in enumerate(events):
        ev["at"] = f"2026-07-{7 + i:02d}T12:00:00+00:00"
    ledger.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    assert "DEADCO" in confirmed_dead(ledger)

    p = _patched(tmp_path)
    with p[0], p[1], p[2], p[3], \
            patch.object(udb, "confirmed_dead", lambda: {"DEADCO"}):
        payload = udb.run(max_calls=10, execute=False,
                          screener_rows=_screener(["DEADCO", "LIVECO"]))
    assert payload["exclusions_by_reason"]["confirmed_dead"] == 1
    assert payload["eligible_security_master_count"] == 1


def test_scanner_universe_excludes_confirmed_dead(tmp_path):
    import research.research_scanner as rs
    _mk_parquet(tmp_path, "SPY", bars=300, price=100.0)
    _mk_parquet(tmp_path, "DEADCO", bars=200, price=20.0, vol=3_000_000)
    _mk_parquet(tmp_path, "LIVECO", bars=200, price=20.0, vol=3_000_000)
    with (
        patch.object(rs, "PRICE_DIR", tmp_path),
        patch.object(rs, "DEEP_PRICE_DIR", tmp_path / "_deep"),
        patch.object(rs, "_alpha_board_tickers", lambda: []),
        patch.object(rs, "_daily_radar_tickers", lambda: []),
        patch.object(rs, "_social_arb_tickers", lambda: []),
        patch("research.dead_symbol_tombstones.confirmed_dead",
              lambda ledger_path=None: {"DEADCO"}),
    ):
        universe, build_info = rs._build_universe(cap=10)
    assert "DEADCO" not in universe
    assert "LIVECO" in universe
    assert build_info["dead_symbol_excluded_count"] >= 1
    assert "DEADCO" in build_info["dead_symbol_excluded"]


# ── Proof 9: tombstoning requires repeated evidence ──────────────────────────


def _failures_on_days(ledger: Path, sym: str, days: List[str],
                      status: str = "fmp_empty") -> None:
    lines = [json.dumps({"ticker": sym, "event": "failure",
                         "provider_status": status, "reason": status,
                         "at": f"{d}T12:00:00+00:00"}) for d in days]
    with ledger.open("a") as fh:
        fh.write("\n".join(lines) + "\n")


def test_single_transient_failure_never_tombstones(tmp_path):
    ledger = tmp_path / "t.jsonl"
    record_failure("FLAKY", "transport_error", ledger_path=ledger)
    states = derive_states(ledger)
    assert states["FLAKY"]["status"] == "WATCH"
    assert confirmed_dead(ledger) == set()


def test_repeated_transport_errors_never_tombstone(tmp_path):
    ledger = tmp_path / "t.jsonl"
    _failures_on_days(ledger, "FLAKY",
                      ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"],
                      status="transport_error")
    assert confirmed_dead(ledger) == set()


def test_same_day_hard_failures_do_not_tombstone(tmp_path):
    ledger = tmp_path / "t.jsonl"
    _failures_on_days(ledger, "GONE", ["2026-07-01"] * 5)  # 5x, one day
    assert confirmed_dead(ledger) == set()


def test_hard_failures_on_distinct_days_tombstone(tmp_path):
    ledger = tmp_path / "t.jsonl"
    days = [f"2026-07-{d:02d}" for d in (1, 2, 3)][:DEAD_THRESHOLD_DAYS]
    _failures_on_days(ledger, "GONE", days)
    assert "GONE" in confirmed_dead(ledger)
    st = derive_states(ledger)["GONE"]
    assert st["status"] == "CONFIRMED_DEAD"
    assert st["distinct_failure_days"] == DEAD_THRESHOLD_DAYS
    assert st["first_failure"] and st["last_failure"]
    assert st["failure_count"] == DEAD_THRESHOLD_DAYS


def test_success_revives_a_failing_symbol(tmp_path):
    ledger = tmp_path / "t.jsonl"
    _failures_on_days(ledger, "BACK", ["2026-07-01", "2026-07-02", "2026-07-03"])
    assert "BACK" in confirmed_dead(ledger)
    record_success("BACK", ledger_path=ledger)
    assert "BACK" not in confirmed_dead(ledger)
    assert derive_states(ledger)["BACK"]["status"] == "OK"


def test_operator_reinstate_overrides_tombstone(tmp_path):
    ledger = tmp_path / "t.jsonl"
    _failures_on_days(ledger, "REDEEMED", ["2026-07-01", "2026-07-02", "2026-07-03"])
    record_review("REDEEMED", decision="reinstate",
                  note="relisted after merger", ledger_path=ledger)
    assert "REDEEMED" not in confirmed_dead(ledger)
    assert derive_states(ledger)["REDEEMED"]["review_status"] == "reinstated"


def test_ledger_is_append_only_events(tmp_path):
    ledger = tmp_path / "t.jsonl"
    record_failure("X", "fmp_empty", ledger_path=ledger)
    n1 = len(ledger.read_text().splitlines())
    record_success("X", ledger_path=ledger)
    n2 = len(ledger.read_text().splitlines())
    assert n2 == n1 + 1  # events only appended, never rewritten
