"""
tests/unit/test_paper_state_hygiene.py — Phase 1C hygiene report tests.

Builds synthetic SQLite fixtures with seeded violations and asserts each
finding code is detected exactly once when the violation is present and
absent otherwise. Also verifies:

  - clean fixture → zero findings, ready_to_gate=True
  - empty / missing DB → graceful
  - any ERROR or WARN → ready_to_gate=False
  - INFO-only state still keeps ready_to_gate=False if WARN is present

The fixtures only INSERT rows; the report code itself only SELECTs.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research import paper_state_hygiene_report as h  # noqa: E402


# ── Schema setup helpers ─────────────────────────────────────────────────────

_DECISIONS_DDL = """
CREATE TABLE decisions (
    id TEXT PRIMARY KEY, run_id TEXT, ts TEXT NOT NULL, ticker TEXT NOT NULL,
    strategy TEXT NOT NULL, direction TEXT NOT NULL, signal_score REAL,
    shares REAL, entry_price REAL, stop_loss REAL, target_price REAL,
    risk_reward REAL, order_id TEXT,
    position_opened INTEGER DEFAULT 0, position_closed INTEGER DEFAULT 0,
    exit_price REAL, pnl REAL, pnl_pct REAL, veto_votes TEXT, notes TEXT,
    fill_price REAL, fill_qty REAL, slippage_bps REAL,
    fill_status TEXT, exit_fill_price REAL
)
"""

_PAPER_SIGNALS_DDL = """
CREATE TABLE paper_signals (
    id TEXT PRIMARY KEY, logged_at TEXT NOT NULL, strategy TEXT NOT NULL,
    sleeve TEXT NOT NULL, ticker TEXT NOT NULL, side TEXT NOT NULL,
    signal_version TEXT NOT NULL, entry_price REAL NOT NULL,
    stop_loss REAL, target_price REAL, risk_reward REAL, score REAL,
    sector TEXT, regime_context TEXT, key_features TEXT,
    allocation_bucket TEXT, allocation_pct REAL,
    qualified_reason TEXT, notes TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    exit_price REAL, exit_date TEXT, exit_reason TEXT,
    aux_h3 TEXT
)
"""

_PAPER_OUTCOMES_DDL = """
CREATE TABLE paper_signal_outcomes (
    id TEXT PRIMARY KEY, signal_id TEXT NOT NULL,
    horizon_days INTEGER NOT NULL, outcome_date TEXT,
    measured_at TEXT NOT NULL, return_pct REAL, adjusted_return_pct REAL,
    stop_hit INTEGER, target_hit INTEGER,
    still_open INTEGER, hold_complete INTEGER,
    mae_pct REAL, mfe_pct REAL, path TEXT, notes TEXT
)
"""

_VOYAGER_DDL = """
CREATE TABLE voyager_paper_signals (
    id TEXT PRIMARY KEY, logged_at TEXT NOT NULL, ticker TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'LONG', archetype TEXT NOT NULL,
    base_score INTEGER NOT NULL, thirteen_f_pts INTEGER NOT NULL DEFAULT 0,
    final_score INTEGER NOT NULL, thirteen_f_flow TEXT,
    thirteen_f_confidence TEXT, thirteen_f_buying INTEGER,
    thirteen_f_selling INTEGER, thirteen_f_quarter TEXT,
    size_bucket TEXT, market_cap REAL,
    entry_price REAL, stop_loss REAL, target_price REAL,
    risk_reward REAL, ma50 REAL, ma200 REAL, rs_50d REAL, rs_130d REAL,
    dvol_ratio REAL, up_vol_ratio REAL, extension_ma50 REAL,
    fund_score INTEGER, fund_note TEXT, vix_at_entry REAL,
    spy_above_ma50 INTEGER, spy_above_ma200 INTEGER,
    outcome_30d REAL, outcome_90d REAL, outcome_180d REAL,
    outcome_30d_date TEXT, outcome_90d_date TEXT, outcome_180d_date TEXT,
    above_ma200_at_30d INTEGER,
    signal_status TEXT DEFAULT 'open',
    exit_price REAL, exit_date TEXT, exit_reason TEXT, notes TEXT
)
"""

_VETO_LOG_DDL = """
CREATE TABLE veto_log (
    id TEXT PRIMARY KEY, ts TEXT NOT NULL, ticker TEXT NOT NULL,
    strategy TEXT NOT NULL, verdict TEXT NOT NULL,
    agent TEXT, reason TEXT, run_id TEXT
)
"""


def _now() -> datetime:
    return datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)


def _iso_days_ago(days: float) -> str:
    return (_now() - timedelta(days=days)).isoformat()


def _empty_db() -> Path:
    """Return a path to a fresh DB with all five tables created."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.executescript(
        _DECISIONS_DDL + ";" + _PAPER_SIGNALS_DDL + ";" + _PAPER_OUTCOMES_DDL
        + ";" + _VOYAGER_DDL + ";" + _VETO_LOG_DDL
    )
    conn.commit()
    conn.close()
    return Path(tmp.name)


def _ins_decision(conn: sqlite3.Connection, **kwargs) -> str:
    """Insert one decisions row; fills sensible defaults; returns id."""
    rid = kwargs.pop("id", f"d-{conn.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]}")
    defaults: Dict[str, object] = {
        "id": rid, "run_id": "r", "ts": _iso_days_ago(1),
        "ticker": "AAA", "strategy": "SNIPER", "direction": "LONG",
        "signal_score": 80.0, "shares": 10.0, "entry_price": 100.0,
        "stop_loss": 95.0, "target_price": 110.0, "risk_reward": 2.0,
        "order_id": None, "position_opened": 1, "position_closed": 0,
        "exit_price": None, "pnl": None, "pnl_pct": None,
        "veto_votes": None, "notes": None,
        "fill_price": 100.0, "fill_qty": 10.0, "slippage_bps": 0.0,
        "fill_status": "filled", "exit_fill_price": None,
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(
        f"INSERT INTO decisions ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    return rid


def _ins_paper_signal(conn: sqlite3.Connection, **kwargs) -> str:
    rid = kwargs.pop("id", f"p-{conn.execute('SELECT COUNT(*) FROM paper_signals').fetchone()[0]}")
    defaults: Dict[str, object] = {
        "id": rid, "logged_at": _iso_days_ago(1),
        "strategy": "SNIPER", "sleeve": "SNIPER_V6", "ticker": "AAA",
        "side": "LONG", "signal_version": "SNIPER_V6",
        "entry_price": 100.0, "stop_loss": 95.0, "target_price": 110.0,
        "risk_reward": 2.0, "score": 82.0, "sector": "Technology",
        "regime_context": "{}", "key_features": "{}",
        "allocation_bucket": "tier1", "allocation_pct": 0.05,
        "qualified_reason": "ok", "notes": None,
        "status": "open", "exit_price": None, "exit_date": None,
        "exit_reason": None, "aux_h3": "{}",
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(
        f"INSERT INTO paper_signals ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    return rid


def _ins_outcome(conn: sqlite3.Connection, **kwargs) -> str:
    rid = kwargs.pop("id", f"o-{conn.execute('SELECT COUNT(*) FROM paper_signal_outcomes').fetchone()[0]}")
    defaults: Dict[str, object] = {
        "id": rid, "signal_id": "p-0", "horizon_days": 30,
        "outcome_date": _iso_days_ago(0), "measured_at": _iso_days_ago(0),
        "return_pct": 0.05, "adjusted_return_pct": 0.04,
        "stop_hit": 0, "target_hit": 1,
        "still_open": 0, "hold_complete": 1,
        "mae_pct": -0.01, "mfe_pct": 0.06, "path": None, "notes": None,
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(
        f"INSERT INTO paper_signal_outcomes ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    return rid


def _ins_voyager(conn: sqlite3.Connection, **kwargs) -> str:
    rid = kwargs.pop("id", f"v-{conn.execute('SELECT COUNT(*) FROM voyager_paper_signals').fetchone()[0]}")
    defaults: Dict[str, object] = {
        "id": rid, "logged_at": _iso_days_ago(1), "ticker": "AAA",
        "direction": "LONG", "archetype": "BASE_ACCUMULATION",
        "base_score": 70, "thirteen_f_pts": 0, "final_score": 70,
        "thirteen_f_flow": "NEUTRAL", "thirteen_f_confidence": "LOW",
        "thirteen_f_buying": 0, "thirteen_f_selling": 0,
        "thirteen_f_quarter": "2025Q4", "size_bucket": "large",
        "market_cap": 1e10,
        "entry_price": 100.0, "stop_loss": 90.0, "target_price": 120.0,
        "risk_reward": 2.0, "ma50": None, "ma200": None,
        "rs_50d": None, "rs_130d": None, "dvol_ratio": None,
        "up_vol_ratio": None, "extension_ma50": None,
        "fund_score": None, "fund_note": None, "vix_at_entry": None,
        "spy_above_ma50": None, "spy_above_ma200": None,
        "outcome_30d": 0.03, "outcome_90d": None, "outcome_180d": None,
        "outcome_30d_date": _iso_days_ago(0),
        "outcome_90d_date": None, "outcome_180d_date": None,
        "above_ma200_at_30d": 1,
        "signal_status": "open",
        "exit_price": None, "exit_date": None, "exit_reason": None,
        "notes": None,
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(
        f"INSERT INTO voyager_paper_signals ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    return rid


def _ins_veto(conn: sqlite3.Connection, **kwargs) -> str:
    rid = kwargs.pop("id", f"vl-{conn.execute('SELECT COUNT(*) FROM veto_log').fetchone()[0]}")
    defaults: Dict[str, object] = {
        "id": rid, "ts": _iso_days_ago(1),
        "ticker": "AAA", "strategy": "*", "verdict": "RECONCILE_DRIFT",
        "agent": "position_reconciler", "reason": "test", "run_id": None,
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(
        f"INSERT INTO veto_log ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    return rid


def _seed_clean(conn: sqlite3.Connection) -> None:
    """Add one well-formed row per table so checks have a positive control."""
    _ins_decision(conn, id="d-clean")
    sig_id = _ins_paper_signal(conn, id="p-clean")
    _ins_outcome(conn, id="o-clean", signal_id=sig_id)
    _ins_voyager(conn, id="v-clean")
    # no veto rows on a clean DB


def _findings_by_code(report: Dict) -> Dict[str, Dict]:
    return {f["code"]: f for f in report["findings"]}


def _run(db_path: Path, broker_snapshot_path: Optional[Path] = None) -> Dict:
    return h.build_report(
        db_path, now=_now(),
        broker_snapshot_path=broker_snapshot_path,
    )


def _write_broker_snapshot(
    path: Path,
    positions: List[Dict],
    generated_at: Optional[str] = None,
) -> Path:
    """Phase 1G test helper. Writes a snapshot in the shape
    core/broker_snapshot.py would emit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": generated_at or _now().isoformat(),
        "source": "test",
        "count": len(positions),
        "positions": [
            {
                "ticker": str(p["ticker"]).upper(),
                "qty": float(p["qty"]),
                "side": str(p.get("side")
                            or ("long" if float(p["qty"]) > 0 else "short")
                            ).lower(),
                "entry_price":   p.get("entry_price"),
                "current_price": p.get("current_price"),
                "market_value":  p.get("market_value"),
                "unrealized_pnl": p.get("unrealized_pnl"),
            }
            for p in positions
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ── Clean & empty baseline ───────────────────────────────────────────────────

def test_clean_db_no_findings_ready_to_gate_true():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    conn.commit()
    conn.close()
    r = _run(db)
    assert r["summary"]["total_findings"] == 0, r["findings"]
    assert r["summary"]["errors"] == 0
    assert r["summary"]["warns"] == 0
    assert r["summary"]["ready_to_gate"] is True


def test_empty_db_no_findings_ready_to_gate_true():
    db = _empty_db()
    r = _run(db)
    assert r["summary"]["total_findings"] == 0
    assert r["summary"]["ready_to_gate"] is True
    # tables exist but are empty → all five reported
    assert set(r["tables_scanned"].keys()) >= {
        "decisions", "paper_signals", "paper_signal_outcomes",
        "voyager_paper_signals", "veto_log",
    }


def test_missing_db_path_graceful():
    bogus = Path(tempfile.gettempdir()) / "does_not_exist_paper_state.db"
    if bogus.exists():
        bogus.unlink()
    r = h.build_report(bogus, now=_now())
    assert r["db_missing"] is True
    assert r["summary"]["total_findings"] == 0
    # ready_to_gate stays True because there's nothing to flag — operator
    # action is to point the report at a real DB, not to gate on absence.
    assert r["summary"]["ready_to_gate"] is True


# ── Decisions checks ─────────────────────────────────────────────────────────

def test_decisions_open_no_fill_price():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-bad", position_opened=1, fill_price=None)
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert "DECISIONS_OPEN_NO_FILL_PRICE" in f
    assert f["DECISIONS_OPEN_NO_FILL_PRICE"]["count"] == 1
    assert "d-bad" in f["DECISIONS_OPEN_NO_FILL_PRICE"]["sample_ids"]
    assert f["DECISIONS_OPEN_NO_FILL_PRICE"]["severity"] == "ERROR"
    assert r["summary"]["ready_to_gate"] is False


def test_decisions_open_no_fill_qty():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-null-qty", position_opened=1, fill_qty=None)
    _ins_decision(conn, id="d-zero-qty", position_opened=1, fill_qty=0.0)
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["DECISIONS_OPEN_NO_FILL_QTY"]["count"] == 2
    assert set(f["DECISIONS_OPEN_NO_FILL_QTY"]["sample_ids"]) >= {"d-null-qty", "d-zero-qty"}
    assert f["DECISIONS_OPEN_NO_FILL_QTY"]["severity"] == "ERROR"


def test_decisions_closed_missing_pnl_exit():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-no-pnl", position_opened=1, position_closed=1,
                  exit_price=110.0, pnl=None)
    _ins_decision(conn, id="d-no-exit", position_opened=1, position_closed=1,
                  exit_price=None, pnl=10.0)
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["DECISIONS_CLOSED_MISSING_PNL_EXIT"]["count"] == 2
    assert f["DECISIONS_CLOSED_MISSING_PNL_EXIT"]["severity"] == "WARN"


def test_decisions_extreme_slippage():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-bigslip", slippage_bps=750.0)
    _ins_decision(conn, id="d-bigneg",  slippage_bps=-600.0)
    _ins_decision(conn, id="d-normal",  slippage_bps=15.0)
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["DECISIONS_EXTREME_SLIPPAGE_BPS"]["count"] == 2
    assert "d-normal" not in f["DECISIONS_EXTREME_SLIPPAGE_BPS"]["sample_ids"]
    assert f["DECISIONS_EXTREME_SLIPPAGE_BPS"]["severity"] == "WARN"


def test_decisions_stale_open():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-old",
                  ts=_iso_days_ago(400),
                  position_opened=1, position_closed=0)
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["DECISIONS_STALE_OPEN_OVER_365D"]["count"] == 1
    assert "d-old" in f["DECISIONS_STALE_OPEN_OVER_365D"]["sample_ids"]
    assert f["DECISIONS_STALE_OPEN_OVER_365D"]["severity"] == "WARN"


# ── Paper signals checks ─────────────────────────────────────────────────────

def test_paper_signals_stale_open():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_paper_signal(conn, id="p-old", status="open",
                      logged_at=_iso_days_ago(200))
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["PAPER_SIGNALS_STALE_OPEN_OVER_180D"]["count"] == 1
    assert "p-old" in f["PAPER_SIGNALS_STALE_OPEN_OVER_180D"]["sample_ids"]


def test_paper_signals_invalid_entry_price():
    # paper_signals.entry_price has NOT NULL constraint — use 0 / negative
    # to model the "invalid" case.
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_paper_signal(conn, id="p-zero", entry_price=0.0)
    _ins_paper_signal(conn, id="p-neg",  entry_price=-5.0)
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["PAPER_SIGNALS_INVALID_ENTRY_PRICE"]["count"] == 2
    assert f["PAPER_SIGNALS_INVALID_ENTRY_PRICE"]["severity"] == "ERROR"


def test_paper_signals_sniper_missing_aux_h3():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    # Post-cutoff SNIPER row with no aux_h3
    _ins_paper_signal(conn, id="p-sniper-noh3",
                      strategy="SNIPER",
                      logged_at="2026-05-10T12:00:00+00:00",
                      aux_h3=None)
    # Pre-cutoff SNIPER row with no aux_h3 — must NOT be flagged
    _ins_paper_signal(conn, id="p-sniper-pre",
                      strategy="SNIPER",
                      logged_at="2026-04-01T12:00:00+00:00",
                      aux_h3=None)
    # Non-SNIPER post-cutoff row with no aux_h3 — must NOT be flagged
    _ins_paper_signal(conn, id="p-voyager",
                      strategy="VOYAGER", sleeve="VOYAGER_PAPER",
                      logged_at="2026-05-10T12:00:00+00:00",
                      aux_h3=None)
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["PAPER_SIGNALS_SNIPER_MISSING_AUX_H3"]["count"] == 1
    assert f["PAPER_SIGNALS_SNIPER_MISSING_AUX_H3"]["sample_ids"] == ["p-sniper-noh3"]


def test_paper_signals_duplicate_open_24h():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    base = _now() - timedelta(hours=1)
    # Two opens 2 hours apart (dup), one open 48h later (not dup).
    _ins_paper_signal(
        conn, id="p-dup1",
        strategy="SNIPER", ticker="DUP", side="LONG",
        logged_at=base.isoformat(),
    )
    _ins_paper_signal(
        conn, id="p-dup2",
        strategy="SNIPER", ticker="DUP", side="LONG",
        logged_at=(base + timedelta(hours=2)).isoformat(),
    )
    _ins_paper_signal(
        conn, id="p-far",
        strategy="SNIPER", ticker="DUP", side="LONG",
        logged_at=(base - timedelta(hours=48)).isoformat(),
    )
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["PAPER_SIGNALS_DUPLICATE_OPEN_24H"]["count"] == 1
    # The *second* of the dup pair is the offender.
    assert f["PAPER_SIGNALS_DUPLICATE_OPEN_24H"]["sample_ids"] == ["p-dup2"]


# ── Outcomes checks ──────────────────────────────────────────────────────────

def test_outcomes_orphan():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_outcome(conn, id="o-orphan", signal_id="nonexistent-signal-id")
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["PAPER_SIGNAL_OUTCOMES_ORPHAN"]["count"] == 1
    assert "o-orphan" in f["PAPER_SIGNAL_OUTCOMES_ORPHAN"]["sample_ids"]
    assert f["PAPER_SIGNAL_OUTCOMES_ORPHAN"]["severity"] == "ERROR"


def test_outcomes_incoherent_state():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    sig_id = _ins_paper_signal(conn, id="p-for-incoh")
    _ins_outcome(conn, id="o-incoh", signal_id=sig_id,
                 still_open=1, hold_complete=1)
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["PAPER_SIGNAL_OUTCOMES_INCOHERENT_STATE"]["count"] == 1
    assert f["PAPER_SIGNAL_OUTCOMES_INCOHERENT_STATE"]["severity"] == "ERROR"


# ── Voyager checks ───────────────────────────────────────────────────────────

def test_voyager_stale_open_no_30d_outcome():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_voyager(conn, id="v-stale",
                 signal_status="open",
                 logged_at=_iso_days_ago(120),
                 outcome_30d=None)
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["VOYAGER_STALE_OPEN_NO_30D_OUTCOME"]["count"] == 1


def test_voyager_direction_violation():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_voyager(conn, id="v-short", direction="SHORT")
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["VOYAGER_DIRECTION_VIOLATION"]["count"] == 1
    assert f["VOYAGER_DIRECTION_VIOLATION"]["severity"] == "ERROR"


def test_voyager_missing_required_fields():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_voyager(conn, id="v-noentry",  entry_price=0.0)
    _ins_voyager(conn, id="v-nostop",   stop_loss=None)
    _ins_voyager(conn, id="v-notarget", target_price=-1.0)
    conn.commit(); conn.close()
    r = _run(db)
    f = _findings_by_code(r)
    assert f["VOYAGER_MISSING_REQUIRED_FIELDS"]["count"] == 3


# ── Reconciler audit trail (Phase 1G classifications) ───────────────────────

def test_reconciler_drift_unknown_when_snapshot_missing():
    """Drift rows + no broker snapshot → conservative WARN, gate stays False."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_veto(conn, id="vl-recent", ts=_iso_days_ago(2),
              agent="position_reconciler", verdict="RECONCILE_DRIFT")
    _ins_veto(conn, id="vl-old", ts=_iso_days_ago(30),
              agent="position_reconciler", verdict="RECONCILE_DRIFT")
    # different agent — must be ignored
    _ins_veto(conn, id="vl-other", ts=_iso_days_ago(1),
              agent="veto_council", verdict="VETOED")
    conn.commit(); conn.close()
    r = _run(db, broker_snapshot_path=None)
    f = _findings_by_code(r)
    assert "RECONCILER_DRIFT_UNKNOWN" in f
    assert f["RECONCILER_DRIFT_UNKNOWN"]["count"] == 1
    assert f["RECONCILER_DRIFT_UNKNOWN"]["sample_ids"] == ["vl-recent"]
    assert f["RECONCILER_DRIFT_UNKNOWN"]["severity"] == "WARN"
    assert "RECONCILER_DRIFT_HISTORICAL" not in f
    assert "RECONCILER_DRIFT_ACTIVE" not in f
    assert r["summary"]["ready_to_gate"] is False


def test_reconciler_drift_unknown_when_snapshot_stale(tmp_path: Path):
    """Drift rows + snapshot >1h old → conservative WARN."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_veto(conn, id="vl-recent", ts=_iso_days_ago(2),
              agent="position_reconciler", verdict="RECONCILE_DRIFT")
    conn.commit(); conn.close()
    stale_iso = (_now() - timedelta(hours=5)).isoformat()
    snap = _write_broker_snapshot(
        tmp_path / "snap.json",
        positions=[{"ticker": "AAA", "qty": 10.0, "side": "long"}],
        generated_at=stale_iso,
    )
    r = _run(db, broker_snapshot_path=snap)
    f = _findings_by_code(r)
    assert "RECONCILER_DRIFT_UNKNOWN" in f
    assert "RECONCILER_DRIFT_HISTORICAL" not in f
    assert "RECONCILER_DRIFT_ACTIVE" not in f
    assert r["summary"]["ready_to_gate"] is False


def test_reconciler_drift_historical_only_when_book_matches_snapshot(tmp_path: Path):
    """Drift rows + fresh snapshot that matches book → INFO only, gate True."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    # Two clean open decisions; broker snapshot matches both exactly.
    _ins_decision(conn, id="d-aaa", ticker="AAA",
                  position_opened=1, position_closed=0,
                  direction="LONG", shares=10.0, fill_qty=10.0)
    _ins_decision(conn, id="d-bbb", ticker="BBB",
                  position_opened=1, position_closed=0,
                  direction="SHORT", shares=20.0, fill_qty=20.0)
    _ins_veto(conn, id="vl-old1", ts=_iso_days_ago(2), ticker="AAA",
              agent="position_reconciler", verdict="RECONCILE_DRIFT")
    _ins_veto(conn, id="vl-old2", ts=_iso_days_ago(3), ticker="BBB",
              agent="position_reconciler", verdict="RECONCILE_DRIFT")
    conn.commit(); conn.close()
    snap = _write_broker_snapshot(
        tmp_path / "snap.json",
        positions=[
            {"ticker": "AAA", "qty": 10.0, "side": "long"},
            {"ticker": "BBB", "qty": -20.0, "side": "short"},
        ],
    )
    r = _run(db, broker_snapshot_path=snap)
    f = _findings_by_code(r)
    assert "RECONCILER_DRIFT_HISTORICAL" in f
    assert f["RECONCILER_DRIFT_HISTORICAL"]["severity"] == "INFO"
    assert f["RECONCILER_DRIFT_HISTORICAL"]["count"] == 2
    assert f["RECONCILER_DRIFT_HISTORICAL"]["resolved"] is True
    assert set(f["RECONCILER_DRIFT_HISTORICAL"]["affected_tickers"]) == {"AAA", "BBB"}
    assert "RECONCILER_DRIFT_ACTIVE" not in f
    assert "RECONCILER_DRIFT_UNKNOWN" not in f
    # INFO does not gate
    assert r["summary"]["ready_to_gate"] is True
    assert r["summary"]["ready_to_gate_clean"] is True


def test_reconciler_drift_active_when_book_disagrees_with_snapshot(tmp_path: Path):
    """Open decision X, broker holds Y → ACTIVE WARN + gate False."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _ins_decision(conn, id="d-aaa", ticker="AAA",
                  position_opened=1, position_closed=0,
                  direction="LONG", shares=10.0, fill_qty=10.0)
    conn.commit(); conn.close()
    snap = _write_broker_snapshot(
        tmp_path / "snap.json",
        positions=[{"ticker": "ZZZ", "qty": 5.0, "side": "long"}],
    )
    r = _run(db, broker_snapshot_path=snap)
    f = _findings_by_code(r)
    assert "RECONCILER_DRIFT_ACTIVE" in f
    assert f["RECONCILER_DRIFT_ACTIVE"]["severity"] == "WARN"
    kinds = {m["kind"] for m in f["RECONCILER_DRIFT_ACTIVE"]["mismatches"]}
    assert kinds == {"DECISIONS_ONLY", "BROKER_ONLY"}
    assert r["summary"]["ready_to_gate"] is False
    assert r["summary"]["ready_to_gate_clean"] is False


def test_reconciler_drift_active_on_qty_mismatch(tmp_path: Path):
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _ins_decision(conn, id="d-aaa", ticker="AAA",
                  position_opened=1, position_closed=0,
                  direction="LONG", shares=10.0, fill_qty=10.0)
    conn.commit(); conn.close()
    snap = _write_broker_snapshot(
        tmp_path / "snap.json",
        positions=[{"ticker": "AAA", "qty": 25.0, "side": "long"}],
    )
    r = _run(db, broker_snapshot_path=snap)
    f = _findings_by_code(r)
    assert "RECONCILER_DRIFT_ACTIVE" in f
    kinds = {m["kind"] for m in f["RECONCILER_DRIFT_ACTIVE"]["mismatches"]}
    assert kinds == {"QTY_MISMATCH"}


def test_reconciler_drift_active_on_side_mismatch(tmp_path: Path):
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _ins_decision(conn, id="d-aaa", ticker="AAA",
                  position_opened=1, position_closed=0,
                  direction="LONG", shares=10.0, fill_qty=10.0)
    conn.commit(); conn.close()
    snap = _write_broker_snapshot(
        tmp_path / "snap.json",
        positions=[{"ticker": "AAA", "qty": -10.0, "side": "short"}],
    )
    r = _run(db, broker_snapshot_path=snap)
    f = _findings_by_code(r)
    assert "RECONCILER_DRIFT_ACTIVE" in f
    kinds = {m["kind"] for m in f["RECONCILER_DRIFT_ACTIVE"]["mismatches"]}
    assert kinds == {"SIDE_MISMATCH"}


def test_reconciler_drift_active_and_historical_when_both_present(tmp_path: Path):
    """Active diff + historical drift rows → both findings emitted."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _ins_decision(conn, id="d-aaa", ticker="AAA",
                  position_opened=1, position_closed=0,
                  direction="LONG", shares=10.0, fill_qty=10.0)
    _ins_veto(conn, id="vl-recent", ts=_iso_days_ago(1), ticker="AAA",
              agent="position_reconciler", verdict="RECONCILE_DRIFT")
    conn.commit(); conn.close()
    snap = _write_broker_snapshot(
        tmp_path / "snap.json",
        positions=[{"ticker": "ZZZ", "qty": 5.0, "side": "long"}],
    )
    r = _run(db, broker_snapshot_path=snap)
    f = _findings_by_code(r)
    assert "RECONCILER_DRIFT_ACTIVE" in f
    assert f["RECONCILER_DRIFT_ACTIVE"]["severity"] == "WARN"
    assert "RECONCILER_DRIFT_HISTORICAL" in f
    assert f["RECONCILER_DRIFT_HISTORICAL"]["severity"] == "INFO"
    assert f["RECONCILER_DRIFT_HISTORICAL"]["resolved"] is False
    assert r["summary"]["ready_to_gate"] is False


def test_reconciler_drift_no_findings_when_clean(tmp_path: Path):
    """No drift rows + matching snapshot → no drift findings at all."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _ins_decision(conn, id="d-aaa", ticker="AAA",
                  position_opened=1, position_closed=0,
                  direction="LONG", shares=10.0, fill_qty=10.0)
    conn.commit(); conn.close()
    snap = _write_broker_snapshot(
        tmp_path / "snap.json",
        positions=[{"ticker": "AAA", "qty": 10.0, "side": "long"}],
    )
    r = _run(db, broker_snapshot_path=snap)
    f = _findings_by_code(r)
    assert "RECONCILER_DRIFT_ACTIVE" not in f
    assert "RECONCILER_DRIFT_HISTORICAL" not in f
    assert "RECONCILER_DRIFT_UNKNOWN" not in f
    assert r["summary"]["ready_to_gate"] is True


def test_reconciler_drift_no_db_mutation_under_classification(tmp_path: Path):
    """Phase 1G classification must remain read-only."""
    import hashlib
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _ins_decision(conn, id="d-aaa", ticker="AAA",
                  position_opened=1, position_closed=0,
                  direction="LONG", shares=10.0, fill_qty=10.0)
    _ins_veto(conn, id="vl-recent", ts=_iso_days_ago(1), ticker="AAA",
              agent="position_reconciler", verdict="RECONCILE_DRIFT")
    conn.commit(); conn.close()
    snap = _write_broker_snapshot(
        tmp_path / "snap.json",
        positions=[{"ticker": "AAA", "qty": 10.0, "side": "long"}],
    )
    before = hashlib.md5(db.read_bytes()).hexdigest()
    _ = _run(db, broker_snapshot_path=snap)
    after = hashlib.md5(db.read_bytes()).hexdigest()
    assert before == after


# ── Verdict semantics ────────────────────────────────────────────────────────

def test_warn_only_keeps_ready_to_gate_false():
    """A single WARN must drop ready_to_gate to False."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-closed-nopnl",
                  position_opened=1, position_closed=1,
                  exit_price=110.0, pnl=None)
    conn.commit(); conn.close()
    r = _run(db)
    assert r["summary"]["errors"] == 0
    assert r["summary"]["warns"] >= 1
    assert r["summary"]["ready_to_gate"] is False


def test_error_drops_ready_to_gate():
    """A single ERROR must drop ready_to_gate to False."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-noprice", position_opened=1, fill_price=None)
    conn.commit(); conn.close()
    r = _run(db)
    assert r["summary"]["errors"] >= 1
    assert r["summary"]["ready_to_gate"] is False


def test_render_text_smoke_runs_on_clean_and_dirty():
    """render_text must produce non-empty output in both states and not
    blow up on the dirty fixture."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    conn.commit(); conn.close()
    text_clean = h.render_text(_run(db))
    assert "PAPER STATE HYGIENE" in text_clean
    assert "ready_to_gate_all=True" in text_clean
    assert "ready_to_gate_clean=True" in text_clean

    conn = sqlite3.connect(str(db))
    _ins_decision(conn, id="d-bad", position_opened=1, fill_price=None)
    conn.commit(); conn.close()
    text_dirty = h.render_text(_run(db))
    assert "DECISIONS_OPEN_NO_FILL_PRICE" in text_dirty
    assert "ready_to_gate_all=False" in text_dirty


# ── Phase 1D: dual-scope verdicts, quarantine, --since ───────────────────────

# Calendar layout for these tests:
#   _now()               = 2026-05-11
#   CLEAN epoch default  = 2026-05-08 (per core.paper_evidence_epoch)
#   "pre-epoch" ts       = anything < 2026-05-08
#   "clean-epoch" ts     = anything >= 2026-05-08

PRE_EPOCH_TS  = "2026-04-15T12:00:00+00:00"
CLEAN_TS      = "2026-05-10T12:00:00+00:00"


def test_legacy_dirty_row_excluded_from_clean_ready_to_gate():
    """A dirty row logged before the clean epoch must drop
    ready_to_gate_all=False but leave ready_to_gate_clean=True."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)  # one clean decision with valid fills
    _ins_decision(conn, id="d-legacy",
                  ts=PRE_EPOCH_TS,
                  position_opened=1, fill_price=None, fill_qty=None)
    conn.commit(); conn.close()
    r = _run(db)
    by = r["findings_by_scope"]
    full_codes  = {f["code"] for f in by["full"]}
    clean_codes = {f["code"] for f in by["clean"]}
    assert "DECISIONS_OPEN_NO_FILL_PRICE" in full_codes
    assert "DECISIONS_OPEN_NO_FILL_QTY"   in full_codes
    assert "DECISIONS_OPEN_NO_FILL_PRICE" not in clean_codes
    assert "DECISIONS_OPEN_NO_FILL_QTY"   not in clean_codes
    assert r["summary"]["ready_to_gate_all"]   is False
    assert r["summary"]["ready_to_gate_clean"] is True


def test_clean_epoch_dirty_row_drops_both_verdicts():
    """A dirty row logged INSIDE the clean epoch must drop both
    ready_to_gate_all AND ready_to_gate_clean."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-clean-bad",
                  ts=CLEAN_TS,
                  position_opened=1, fill_price=None)
    conn.commit(); conn.close()
    r = _run(db)
    assert r["summary"]["ready_to_gate_all"]   is False
    assert r["summary"]["ready_to_gate_clean"] is False


def test_clean_fixture_both_verdicts_true():
    """No findings at all → both verdicts True."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    conn.commit(); conn.close()
    r = _run(db)
    assert r["summary"]["ready_to_gate_all"]   is True
    assert r["summary"]["ready_to_gate_clean"] is True
    assert r["summary"]["full_ledger"]["total_findings"]  == 0
    assert r["summary"]["clean_epoch"]["total_findings"]  == 0


def test_quarantine_sidecar_lists_legacy_rows():
    """Pre-epoch decisions with missing fill data are written to the
    quarantine sidecar with the canonical reason and missing_fields."""
    db = _empty_db()
    quarantine_path = Path(tempfile.mkdtemp()) / "quarantine.json"

    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-legacy-1", ts=PRE_EPOCH_TS,
                  position_opened=1, fill_price=None, fill_qty=None)
    _ins_decision(conn, id="d-legacy-2", ts=PRE_EPOCH_TS,
                  position_opened=1, position_closed=1,
                  fill_price=100.0, fill_qty=10.0,
                  exit_price=None, pnl=None)
    # Clean-epoch dirty row — should NOT appear in the quarantine
    # sidecar even though it's flagged in the hygiene findings.
    _ins_decision(conn, id="d-clean-bad", ts=CLEAN_TS,
                  position_opened=1, fill_price=None)
    conn.commit(); conn.close()

    # Drive the report end-to-end so the sidecar is written.
    report = h.build_report(db, now=_now())
    qconn = sqlite3.connect(str(db))
    entries = h.build_legacy_quarantine(qconn, h.CLEAN_PAPER_EVIDENCE_START)
    qconn.close()
    h.write_quarantine_sidecar(
        entries, quarantine_path,
        clean_epoch_iso=h.CLEAN_PAPER_EVIDENCE_START, db_path=db,
    )

    assert quarantine_path.exists()
    payload = json.loads(quarantine_path.read_text())
    assert payload["clean_epoch_start"] == h.CLEAN_PAPER_EVIDENCE_START
    assert payload["count"] == 2
    ids = {e["decision_id"] for e in payload["entries"]}
    assert ids == {"d-legacy-1", "d-legacy-2"}
    assert "d-clean-bad" not in ids
    for e in payload["entries"]:
        assert e["quarantine_reason"] == "pre_fill_telemetry_legacy"
        assert e["broker_position_match"] is None
        assert isinstance(e["missing_fields"], list) and e["missing_fields"]
    # The report itself reflects the quarantine count.
    assert report["legacy_quarantine"]["count"] == 2


def test_since_override_changes_clean_scope():
    """--since override (via clean_epoch_iso kwarg) should shift which
    rows are considered legacy vs clean."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    # Row logged at 2026-05-10 — clean under default cutoff, legacy if
    # the operator passes --since 2026-06-01.
    _ins_decision(conn, id="d-borderline",
                  ts=CLEAN_TS,
                  position_opened=1, fill_price=None)
    conn.commit(); conn.close()

    # Default cutoff: dirty row in clean scope → ready_to_gate_clean=False.
    r_default = h.build_report(db, now=_now())
    assert r_default["summary"]["ready_to_gate_clean"] is False

    # Override cutoff far in the future: dirty row falls into legacy
    # only → ready_to_gate_clean=True (because nothing is in the clean
    # epoch any more) but ready_to_gate_all stays False.
    r_shifted = h.build_report(
        db,
        clean_epoch_iso="2026-06-01T00:00:00+00:00",
        now=_now(),
    )
    assert r_shifted["summary"]["ready_to_gate_all"]   is False
    assert r_shifted["summary"]["ready_to_gate_clean"] is True
    # And the quarantine view widens to cover the formerly-clean row.
    qconn = sqlite3.connect(str(db))
    entries = h.build_legacy_quarantine(qconn, "2026-06-01T00:00:00+00:00")
    qconn.close()
    ids = {e["decision_id"] for e in entries}
    assert "d-borderline" in ids


def test_operator_review_buckets_present():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-legacy", ts=PRE_EPOCH_TS,
                  position_opened=1, fill_price=None, fill_qty=None)
    _ins_decision(conn, id="d-clean-bad", ts=CLEAN_TS,
                  position_opened=1, fill_price=None)
    conn.commit(); conn.close()
    r = _run(db)
    op = r["operator_review"]
    assert op["legacy_rows_needing_review"]["count"] == 1
    assert op["rows_safe_to_ignore_for_clean"]["count"] == 1
    assert op["rows_requiring_manual_investigation"]["count"] >= 1
    # The clean-epoch ERROR finding(s) bubble up under manual investigation.
    inv_codes = {
        f["code"]
        for f in op["rows_requiring_manual_investigation"]["findings"]
    }
    assert "DECISIONS_OPEN_NO_FILL_PRICE" in inv_codes
    assert op["broker_position_match_source"] == "unavailable_no_cached_snapshot"


def test_no_db_mutation_after_report_run():
    """The hygiene report + quarantine pass must not mutate the DB.
    We hash the file before and after a full run."""
    import hashlib
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-legacy", ts=PRE_EPOCH_TS,
                  position_opened=1, fill_price=None, fill_qty=None)
    conn.commit(); conn.close()

    before = hashlib.md5(db.read_bytes()).hexdigest()
    before_size = db.stat().st_size

    # Full pipeline: build_report + collect quarantine + write sidecar
    report = h.build_report(db, now=_now())
    qconn = sqlite3.connect(str(db))
    entries = h.build_legacy_quarantine(qconn, h.CLEAN_PAPER_EVIDENCE_START)
    qconn.close()
    sidecar = Path(tempfile.mkdtemp()) / "q.json"
    h.write_quarantine_sidecar(entries, sidecar, db_path=db)

    after = hashlib.md5(db.read_bytes()).hexdigest()
    after_size = db.stat().st_size
    assert before == after, "DB hash changed — report mutated state"
    assert before_size == after_size
    assert report["summary"]["full_ledger"]["errors"] >= 1
    assert sidecar.exists()


def test_quarantine_skips_pre_epoch_rows_without_issues():
    """Pre-epoch rows that are *not* dirty must not appear in the
    quarantine sidecar (we don't quarantine just for being old)."""
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    # Pre-epoch but fully-filled — fine, must not be quarantined.
    _ins_decision(conn, id="d-legacy-clean",
                  ts=PRE_EPOCH_TS,
                  position_opened=1,
                  fill_price=100.0, fill_qty=10.0)
    conn.commit(); conn.close()
    qconn = sqlite3.connect(str(db))
    entries = h.build_legacy_quarantine(qconn, h.CLEAN_PAPER_EVIDENCE_START)
    qconn.close()
    ids = {e["decision_id"] for e in entries}
    assert "d-legacy-clean" not in ids


def test_legacy_quarantine_count_in_report_matches_sidecar():
    db = _empty_db()
    conn = sqlite3.connect(str(db))
    _seed_clean(conn)
    _ins_decision(conn, id="d-l1", ts=PRE_EPOCH_TS,
                  position_opened=1, fill_price=None)
    _ins_decision(conn, id="d-l2", ts=PRE_EPOCH_TS,
                  position_opened=1, fill_price=None)
    _ins_decision(conn, id="d-l3", ts=PRE_EPOCH_TS,
                  position_opened=1, fill_price=None)
    conn.commit(); conn.close()
    r = _run(db)
    qconn = sqlite3.connect(str(db))
    entries = h.build_legacy_quarantine(qconn, h.CLEAN_PAPER_EVIDENCE_START)
    qconn.close()
    assert r["legacy_quarantine"]["count"] == len(entries) == 3
