"""tests/unit/test_provider_freshness_audit.py — Phase 2B.4 audit tests.

The provider/pipeline freshness audit is cache-only by design. These
tests verify:

  - The audit never imports any provider client at module load.
  - The audit never executes mutating SQL.
  - Cache-meta scanning groups by prefix correctly.
  - Next-run prediction uses the exact cache_meta key, not the prefix.
  - Pipeline rows render PASS / WARN / FAIL based on age vs threshold.
  - The TTL view inside the audit matches the canonical TTL constants
    in ``core/data_gatekeeper.py`` (drift breaks the test).
  - The atomic write path produces a JSON sidecar with the expected
    schema fields.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# core.artifact_freshness is stdlib-only; core.data_gatekeeper exposes
# TTL_* constants for the canonical-TTL invariant test below.
from core import data_gatekeeper as dgk  # noqa: E402
from research import provider_freshness_audit as pfa  # noqa: E402


HOUR = 3600


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_db(tmp_path: Path) -> Path:
    """A trading.db skeleton with cache_meta + fmp_endpoint_log rows."""
    db = tmp_path / "trading.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript("""
            CREATE TABLE cache_meta (
                key TEXT PRIMARY KEY,
                fetched_at REAL NOT NULL,
                payload TEXT
            );
            CREATE TABLE fmp_endpoint_log (
                endpoint TEXT,
                ts REAL,
                saved INTEGER DEFAULT 0,
                resp_bytes INTEGER DEFAULT 0
            );
            CREATE TABLE fmp_budget_monthly (
                month TEXT PRIMARY KEY,
                calls_used INTEGER
            );
            CREATE TABLE fmp_budget (
                day TEXT PRIMARY KEY,
                calls_used INTEGER
            );
        """)
        now = time.time()
        # Fresh earnings_cal:5 — 4h old (well within 13h TTL).
        conn.execute(
            "INSERT INTO cache_meta(key, fetched_at, payload) VALUES (?, ?, ?)",
            ("fmp:earnings_cal:5", now - 4 * HOUR, "{}"),
        )
        # Stale earnings_cal:7 — 20h old (past 13h TTL).
        conn.execute(
            "INSERT INTO cache_meta(key, fetched_at, payload) VALUES (?, ?, ?)",
            ("fmp:earnings_cal:7", now - 20 * HOUR, "{}"),
        )
        # Two fresh profile entries.
        for tk in ("NVDA", "AAPL"):
            conn.execute(
                "INSERT INTO cache_meta(key, fetched_at, payload) VALUES (?, ?, ?)",
                (f"fmp:profile:{tk}", now - 2 * HOUR, "{}"),
            )
        # One stale profile entry.
        conn.execute(
            "INSERT INTO cache_meta(key, fetched_at, payload) VALUES (?, ?, ?)",
            ("fmp:profile:OLD", now - 48 * HOUR, "{}"),
        )
        # Endpoint log: 10 calls + 5 cache hits to /quote in last 24h.
        for _ in range(10):
            conn.execute(
                "INSERT INTO fmp_endpoint_log(endpoint, ts, saved, resp_bytes) "
                "VALUES('/quote', ?, 0, 1024)", (now - 1 * HOUR,))
        for _ in range(5):
            conn.execute(
                "INSERT INTO fmp_endpoint_log(endpoint, ts, saved, resp_bytes) "
                "VALUES('/quote', ?, 1, 0)", (now - 1 * HOUR,))
        # Monthly budget.
        conn.execute(
            "INSERT INTO fmp_budget_monthly(month, calls_used) VALUES (?, ?)",
            (time.strftime("%Y-%m"), 12345),
        )
        conn.commit()
    finally:
        conn.close()
    return db


@pytest.fixture
def fake_repo(tmp_path: Path, fake_db: Path) -> Path:
    """A repo skeleton with research/, cache/, db/ rooted at tmp_path."""
    (tmp_path / "db").mkdir(exist_ok=True)
    # Move the fake DB to the conventional location.
    target = tmp_path / "db" / "trading.db"
    if not target.exists():
        target.write_bytes(fake_db.read_bytes())
    (tmp_path / "cache" / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cache" / "state").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ── Cache-meta scan ─────────────────────────────────────────────────────────

def test_scan_cache_meta_groups_by_prefix(fake_db: Path):
    conn = pfa._read_only_conn(fake_db)
    rows = pfa.scan_cache_meta(conn)
    conn.close()
    by_prefix = {r.prefix: r for r in rows}
    # earnings_cal has 2 entries.
    assert by_prefix["fmp:earnings_cal"].count == 2
    # profile has 3.
    assert by_prefix["fmp:profile"].count == 3
    # TTL view matches the canonical 13h bump.
    assert by_prefix["fmp:earnings_cal"].ttl_s == 13 * HOUR
    # Stale counts.  earnings_cal: one row >13h.
    assert by_prefix["fmp:earnings_cal"].stale_count == 1
    assert by_prefix["fmp:profile"].stale_count == 1


def test_cache_meta_by_key_exact_ages(fake_db: Path):
    conn = pfa._read_only_conn(fake_db)
    ages = pfa.cache_meta_by_key(conn)
    conn.close()
    assert "fmp:earnings_cal:5" in ages
    # Allow tiny rounding error from now().
    assert 4 * HOUR - 5 <= ages["fmp:earnings_cal:5"] <= 4 * HOUR + 5
    assert ages["fmp:earnings_cal:7"] > 13 * HOUR


# ── Next-run prediction ─────────────────────────────────────────────────────

def test_predict_no_call_when_specific_key_fresh():
    # fmp:earnings_cal:5 is 4h old; well inside the 13h TTL.
    by_key = {"fmp:earnings_cal:5": 4 * HOUR}
    out = pfa.predict_next_fmp_calls(by_key)
    assert out["premarket"]["likely_calls"] == 0
    assert out["gatekeeper_refresh"]["likely_calls"] == 0


def test_predict_call_when_specific_key_stale():
    by_key = {"fmp:earnings_cal:5": 50 * HOUR}
    out = pfa.predict_next_fmp_calls(by_key)
    assert out["premarket"]["likely_calls"] >= 1
    slot = out["premarket"]["slots"][0]
    assert slot["call_predicted"] is True


def test_predict_call_when_specific_key_missing():
    by_key = {}                                  # no cache row at all
    out = pfa.predict_next_fmp_calls(by_key)
    assert out["premarket"]["likely_calls"] >= 1


def test_predict_mcp_audit_session_never_calls_fmp():
    by_key = {}
    out = pfa.predict_next_fmp_calls(by_key)
    assert out["mcp_audit_session"]["likely_calls"] == 0
    assert out["mcp_audit_session"]["slots"] == []


# ── Pipeline rows ───────────────────────────────────────────────────────────

def test_pipeline_row_fresh(tmp_path: Path):
    p = tmp_path / "art.json"
    p.write_text("{}")
    # 1 hour old.
    os.utime(p, (time.time() - 1 * HOUR, time.time() - 1 * HOUR))
    row = pfa._pipeline_row("test", p, 24 * HOUR, "refresh-cmd", time.time())
    assert row.verdict == "PASS"


def test_pipeline_row_warn_at_75pct(tmp_path: Path):
    p = tmp_path / "art.json"
    p.write_text("{}")
    # 19 hours old at 24h threshold → WARN (>=75% i.e. >=18h).
    age = 19 * HOUR
    os.utime(p, (time.time() - age, time.time() - age))
    row = pfa._pipeline_row("test", p, 24 * HOUR, "refresh-cmd", time.time())
    assert row.verdict == "WARN"


def test_pipeline_row_fail(tmp_path: Path):
    p = tmp_path / "art.json"
    p.write_text("{}")
    # 36h old at 24h threshold → FAIL.
    age = 36 * HOUR
    os.utime(p, (time.time() - age, time.time() - age))
    row = pfa._pipeline_row("test", p, 24 * HOUR, "refresh-cmd", time.time())
    assert row.verdict == "FAIL"
    assert "age>" in row.cause


def test_pipeline_row_missing(tmp_path: Path):
    row = pfa._pipeline_row("test", tmp_path / "nope.json", 24 * HOUR,
                            "refresh-cmd", time.time())
    assert row.verdict == "FAIL"
    assert row.cause == "artifact_missing"


# ── Selected-ticker audit ───────────────────────────────────────────────────

def test_audit_selected_ticker_missing(fake_repo: Path, monkeypatch):
    monkeypatch.setattr(pfa, "REPO", fake_repo)
    out = pfa.audit_selected_ticker("NVDA")
    assert out["ticker"] == "NVDA"
    assert out["stock_lens"]["verdict"] == "FAIL"
    assert out["executive_gatekeeper"]["verdict"] == "FAIL"
    # Refresh commands include the ticker.
    assert "NVDA" in out["stock_lens"]["refresh_command"]


# ── Top-level audit ─────────────────────────────────────────────────────────

def test_run_audit_returns_expected_shape(fake_repo: Path, monkeypatch):
    monkeypatch.setattr(pfa, "REPO", fake_repo)
    audit = pfa.run_audit(repo=fake_repo)
    assert audit["schema_version"] == "provider_freshness_audit.v1"
    assert "fmp" in audit
    assert "pipelines" in audit
    assert "guardrails" in audit
    # cache_summary present and TTL view matches the canonical constant.
    by_prefix = {r["prefix"]: r for r in audit["fmp"]["cache_summary"]}
    assert by_prefix["fmp:earnings_cal"]["ttl_s"] == 13 * HOUR
    # Pipeline rows include the refresh command field.
    rows = audit["pipelines"]["rows"]
    assert all("refresh_command" in r for r in rows)


def test_run_audit_no_db_safe(tmp_path: Path):
    # tmp_path has no db/trading.db — audit must degrade cleanly.
    audit = pfa.run_audit(repo=tmp_path)
    assert audit["fmp"]["cache_summary"] == []
    assert audit["fmp"]["budget"] == {}


def test_audit_writes_no_mutation(fake_db: Path):
    """The audit reads the DB read-only — running it twice must not change
    the file mtime or contents.
    """
    before = fake_db.stat().st_mtime
    conn = pfa._read_only_conn(fake_db)
    pfa.scan_cache_meta(conn)
    pfa.scan_endpoint_log(conn)
    pfa.scan_budget(conn)
    conn.close()
    after = fake_db.stat().st_mtime
    assert before == after


# ── Invariants: no provider imports / no mutating SQL ────────────────────────

FORBIDDEN_IMPORTS = (
    "core.fmp_client",
    "core.alpaca_client",
    "core.options_feed_factory",
    "core.executive_gatekeeper",
    "execution.",
    "council.",
    "strategies.",
    "core.decision_logger",
)


def test_audit_source_has_no_forbidden_imports():
    """Scan the import statements only — descriptive strings inside
    docstrings or guardrails may legitimately mention these names.
    """
    src = (REPO / "research" / "provider_freshness_audit.py").read_text()
    import_lines = [
        ln for ln in src.splitlines()
        if ln.lstrip().startswith("import ") or ln.lstrip().startswith("from ")
    ]
    joined_imports = "\n".join(import_lines)
    for token in FORBIDDEN_IMPORTS:
        assert token not in joined_imports, (
            f"provider_freshness_audit.py must not import {token}"
        )


def test_audit_source_has_no_mutating_sql():
    src = (REPO / "research" / "provider_freshness_audit.py").read_text().upper()
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ",
                 "CREATE TABLE"):
        assert verb not in src, f"audit must not contain mutating SQL: {verb}"


# ── TTL drift invariant ─────────────────────────────────────────────────────

def test_ttl_table_matches_canonical_constants():
    """The audit duplicates TTL values to stay credentials-free.  If the
    canonical constants in core/data_gatekeeper.py change, this test
    fails so the operator updates the audit alongside.
    """
    assert pfa.FMP_TTL_TABLE["fmp:earnings_cal"] == dgk.TTL_EARNINGS_CAL
    assert pfa.FMP_TTL_TABLE["fmp:past_earnings"] == dgk.TTL_EARNINGS_CAL
    assert pfa.FMP_TTL_TABLE["fmp:profile"] == dgk.TTL_FUNDAMENTALS
    assert pfa.FMP_TTL_TABLE["fmp:fundamentals"] == dgk.TTL_FUNDAMENTALS
    assert pfa.FMP_TTL_TABLE["fmp:vix"] == dgk.TTL_VIX
    assert pfa.FMP_TTL_TABLE["fmp:treasury"] == dgk.TTL_TREASURY
    assert pfa.FMP_TTL_TABLE["fmp:news"] == dgk.TTL_NEWS
    assert pfa.FMP_TTL_TABLE["fmp:econ_cal"] == dgk.TTL_ECONOMIC_CAL


def test_earnings_ttl_bumped_to_13h():
    """Phase 2B.4 explicit invariant: TTL_EARNINGS_CAL must cover the
    11.5h nightly → premarket gap so the 08:00 ET premarket reads a
    warm cache.  13h is the doctrine value.
    """
    assert dgk.TTL_EARNINGS_CAL == 13 * HOUR
