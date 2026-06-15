"""
tests/unit/test_mcp_audit_workflows.py — Phase 2B workflow runner tests.

These tests use a tmp directory as ``STOCKLENS_ROOT`` so the audit
helpers (read-only) read fake artifacts we seed, and the workflow
runner writes its sidecars under the same tmp root. We never touch the
real repo artifacts.

The tests verify:
  - Each workflow runs against present artifacts and writes the expected
    sidecars under cache/research/ and logs/.
  - Each workflow degrades gracefully when artifacts are missing.
  - Ticker audit handles a missing Stock Lens cleanly.
  - mcp_audit_* sidecars never overwrite the core research artifacts.
  - The runner does not import provider, broker, or execution modules.
  - The runner contains no INSERT/UPDATE/DELETE/CREATE SQL.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterable, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ── Fixture: isolated repo root with seeded artifacts ────────────────

def _write_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_forecast(root: Path) -> None:
    _write_json(root / "cache/research/regime_forecast_latest.json", {
        "version": "REGIME_V1",
        "phase": 1,
        "headline": {
            "current_regime": "Bull Continuation",
            "bias_5d": "constructive",
            "bias_10d": "constructive",
            "confidence": "low",
            "invalidation_breached": True,
            "invalidation_breach_reasons": ["leading sectors 0 < 2"],
        },
        "built_at": "2026-05-16T18:36:56+00:00",
        "anchor_date": "2026-05-15",
        "data_freshness_status": "fresh",
    })


def _seed_alpha_board(root: Path, tickers: Iterable[str] = ("MDB", "SNPS", "STLD")) -> None:
    items = []
    for tk in tickers:
        items.append({
            "ticker": tk,
            "alpha_score": 75.0,
            "validator_state": ("Too Extended" if tk == "MDB" else "Watch Reclaim"),
            "bucket": ("Too Late / Crowded" if tk == "MDB" else "Early Discovery"),
            "actionable_now": False,
            "action_label": ("Too Extended" if tk == "MDB" else "Watch Reclaim"),
            "data_tier": "B",
        })
    _write_json(root / "cache/research/alpha_discovery_board_latest.json", {
        "version": "ALPHA_V1",
        "built_at": "2026-05-16T18:38:06+00:00",
        "items": items,
    })


def _seed_hygiene(root: Path, *, clean_gate: bool = True) -> None:
    _write_json(root / "cache/research/paper_state_hygiene_latest.json", {
        "generated_at": "2026-05-16T19:18:39+00:00",
        "clean_epoch_start": "2026-05-08T00:00:00+00:00",
        "tables_scanned": {"decisions": 89},
        "summary": {
            "ready_to_gate_all": False,
            "ready_to_gate_clean": clean_gate,
            "errors": 2 if not clean_gate else 0,
            "warns": 0,
            "infos": 1,
            "full_ledger": {"errors": 2, "warns": 0, "infos": 1},
            "clean_epoch": {"errors": 0, "warns": 0, "infos": 1},
        },
        "findings": [
            {"code": "RECONCILER_DRIFT_HISTORICAL", "severity": "INFO",
             "scope": "clean", "count": 3282, "detail": "resolved"},
        ],
        "operator_review": {},
    })


def _seed_quarantine(root: Path) -> None:
    _write_json(root / "data/state/paper_legacy_quarantine.json", {
        "count": 10, "entries": []})


def _seed_broker_snapshot(root: Path) -> None:
    _write_json(root / "cache/state/broker_positions_snapshot.json", {
        "generated_at": "2026-05-16T19:22:29+00:00",
        "source": "test",
        "count": 1,
        "positions": [{"ticker": "AAA", "qty": 10.0, "side": "long"}],
    })


def _seed_telemetry(root: Path) -> None:
    for name in ("slippage_telemetry_latest", "portfolio_concentration_latest",
                 "shadow_sizing_latest"):
        _write_json(root / f"cache/research/{name}.json", {
            "generated_at": "2026-05-16T19:18:34+00:00", "summary": {},
        })


def _seed_circuit_breaker_db(root: Path, *, halted: bool = False) -> None:
    db_path = root / "db/trading.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE circuit_breaker_state (
            id INTEGER PRIMARY KEY,
            halted INTEGER,
            reason TEXT,
            tripped_at TEXT,
            cleared_at TEXT,
            cleared_by TEXT
        );
    """)
    con.execute(
        "INSERT INTO circuit_breaker_state (id, halted, reason, tripped_at, cleared_at, cleared_by) "
        "VALUES (1, ?, '', NULL, '2026-05-16T15:16:19+00:00', 'operator')",
        (1 if halted else 0,),
    )
    con.commit()
    con.close()


@pytest.fixture()
def isolated_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point STOCKLENS_ROOT at tmp_path, reload modules that captured the
    repo root at import time. Returns the tmp root."""
    monkeypatch.setenv("STOCKLENS_ROOT", str(tmp_path))
    # Re-import so any cached default_root values are recomputed via env.
    import audit_mcp.stocklens_mcp_tools as smt
    import research.mcp_audit_workflows as w
    importlib.reload(smt)
    importlib.reload(w)
    return tmp_path


@pytest.fixture()
def fully_seeded_repo(isolated_repo: Path) -> Path:
    _seed_forecast(isolated_repo)
    _seed_alpha_board(isolated_repo)
    _seed_hygiene(isolated_repo, clean_gate=True)
    _seed_quarantine(isolated_repo)
    _seed_broker_snapshot(isolated_repo)
    _seed_telemetry(isolated_repo)
    _seed_circuit_breaker_db(isolated_repo, halted=False)
    return isolated_repo


# ── Workflow: daily dashboard ────────────────────────────────────────

def test_daily_workflow_writes_sidecars(fully_seeded_repo: Path):
    import research.mcp_audit_workflows as w
    w._run_daily(print_text=False)
    j = fully_seeded_repo / "cache/research/mcp_audit_daily_latest.json"
    x = fully_seeded_repo / "logs/mcp_audit_daily_latest.txt"
    assert j.exists() and x.exists()
    payload = json.loads(j.read_text())
    assert payload["workflow"] == "daily_dashboard_audit"
    assert payload["market_forecast"]["status"] == "ok"
    assert payload["dashboard_audit"]["status"] == "ok"
    assert payload["halt_state"]["halted"] is False
    assert payload["paper_hygiene"]["status"] == "ok"
    assert payload["risk_telemetry"]["status"] == "ok"
    text = x.read_text()
    assert "MCP AUDIT" in text
    assert "[market state]" in text


def test_daily_workflow_graceful_when_artifacts_missing(isolated_repo: Path):
    """Only the DB is seeded — most cache artifacts are missing. The
    workflow must still produce a bundle and a renderable text report."""
    _seed_circuit_breaker_db(isolated_repo, halted=False)
    import research.mcp_audit_workflows as w
    w._run_daily(print_text=False)
    j = isolated_repo / "cache/research/mcp_audit_daily_latest.json"
    payload = json.loads(j.read_text())
    # forecast / hygiene / etc. should report missing_artifact, not crash
    assert payload["market_forecast"]["status"] == "missing_artifact"
    assert payload["risk_telemetry"]["status"] == "missing_artifact"


# ── Workflow: late chase ─────────────────────────────────────────────

def test_late_chase_workflow_buckets_and_missing_lens(fully_seeded_repo: Path):
    import research.mcp_audit_workflows as w
    w._run_late_chase(top_n=25, print_text=False)
    j = fully_seeded_repo / "cache/research/mcp_audit_late_chase_latest.json"
    payload = json.loads(j.read_text())
    assert payload["raw"]["status"] == "ok"
    buckets = payload["buckets"]
    # MDB is "Too Extended"; SNPS/STLD are "Watch Reclaim"
    tickers_extended = {c["ticker"] for c in buckets["extended"]}
    tickers_watch    = {c["ticker"] for c in buckets["watch_reclaim"]}
    assert "MDB" in tickers_extended
    assert {"SNPS", "STLD"} <= tickers_watch
    # No lenses seeded → every candidate is missing a lens.
    assert "MDB" in buckets["missing_lens"]
    assert payload["counts"]["missing_lens"] >= 1


def test_late_chase_workflow_missing_board(isolated_repo: Path):
    import research.mcp_audit_workflows as w
    w._run_late_chase(top_n=25, print_text=False)
    j = isolated_repo / "cache/research/mcp_audit_late_chase_latest.json"
    payload = json.loads(j.read_text())
    assert payload["raw"]["status"] == "missing_artifact"
    assert "buckets" not in payload


# ── Workflow: ticker consistency ─────────────────────────────────────

def test_ticker_audit_with_missing_lens(fully_seeded_repo: Path):
    import research.mcp_audit_workflows as w
    w._run_ticker("MDB", print_text=False)
    j = fully_seeded_repo / "cache/research/mcp_audit_MDB_latest.json"
    assert j.exists()
    payload = json.loads(j.read_text())
    assert payload["ticker"] == "MDB"
    # No lens seeded; helper reports missing_artifact and consistency
    # surfaces the missing path.
    assert payload["stock_lens"]["status"] == "missing_artifact"
    assert any(
        "stock_lens_MDB" in m for m in payload["consistency"]["missing_artifacts"]
    )


def test_ticker_audit_rejects_invalid_input(fully_seeded_repo: Path):
    import research.mcp_audit_workflows as w
    report = w.ticker_consistency_audit("bad ticker!")
    assert report["status"] == "invalid_input"


# ── Workflow: system health ──────────────────────────────────────────

def test_system_health_safe_for_paper_observation(fully_seeded_repo: Path):
    import research.mcp_audit_workflows as w
    w._run_system_health(print_text=False)
    j = fully_seeded_repo / "cache/research/mcp_audit_system_health_latest.json"
    payload = json.loads(j.read_text())
    v = payload["verdict"]
    assert v["halted"] is False
    assert v["ready_to_gate_clean"] is True
    assert v["active_drift"] is False
    assert v["unknown_drift"] is False
    assert v["safe_for_paper_observation"] is True


def test_system_health_blocks_on_failed_clean_gate(isolated_repo: Path):
    _seed_circuit_breaker_db(isolated_repo, halted=False)
    _seed_hygiene(isolated_repo, clean_gate=False)
    _seed_quarantine(isolated_repo)
    _seed_broker_snapshot(isolated_repo)
    _seed_telemetry(isolated_repo)
    import research.mcp_audit_workflows as w
    w._run_system_health(print_text=False)
    j = isolated_repo / "cache/research/mcp_audit_system_health_latest.json"
    payload = json.loads(j.read_text())
    assert payload["verdict"]["safe_for_paper_observation"] is False


# ── Invariant: sidecars do not collide with core artifacts ───────────

CORE_ARTIFACT_NAMES = {
    "paper_state_hygiene_latest.json",
    "regime_forecast_latest.json",
    "alpha_discovery_board_latest.json",
    "alpha_discovery_overlay_latest.json",
    "research_delta_latest.json",
    "slippage_telemetry_latest.json",
    "portfolio_concentration_latest.json",
    "shadow_sizing_latest.json",
    "holdout_2026h2_scoreboard_latest.json",
    "paper_legacy_quarantine.json",
    "broker_positions_snapshot.json",
}


def test_sidecars_are_namespaced_under_mcp_audit_prefix(fully_seeded_repo: Path):
    """Every file the workflows write must start with mcp_audit_."""
    import research.mcp_audit_workflows as w
    # Snapshot pre-existing artifacts so we can assert none mutated.
    seeded_artifacts: Dict[Path, str] = {}
    for rel in ("cache/research/regime_forecast_latest.json",
                "cache/research/alpha_discovery_board_latest.json",
                "cache/research/paper_state_hygiene_latest.json",
                "cache/research/slippage_telemetry_latest.json",
                "cache/state/broker_positions_snapshot.json",
                "data/state/paper_legacy_quarantine.json"):
        p = fully_seeded_repo / rel
        if p.exists():
            seeded_artifacts[p] = p.read_text()

    w._run_daily(print_text=False)
    w._run_late_chase(top_n=25, print_text=False)
    w._run_system_health(print_text=False)
    w._run_ticker("MDB", print_text=False)

    # No core artifact contents changed.
    for p, original in seeded_artifacts.items():
        assert p.read_text() == original, f"workflow mutated {p}"

    # Every workflow output is under the mcp_audit_ namespace.
    written = list((fully_seeded_repo / "cache/research").glob("mcp_audit_*"))
    written += list((fully_seeded_repo / "logs").glob("mcp_audit_*"))
    assert written
    for f in written:
        assert f.name.startswith("mcp_audit_")
        assert f.name not in CORE_ARTIFACT_NAMES


# ── Invariant: no DB mutation ────────────────────────────────────────

def test_no_db_mutation_after_workflows(fully_seeded_repo: Path):
    import hashlib
    import research.mcp_audit_workflows as w
    db_path = fully_seeded_repo / "db/trading.db"
    before = hashlib.md5(db_path.read_bytes()).hexdigest()
    w._run_daily(print_text=False)
    w._run_system_health(print_text=False)
    w._run_ticker("MDB", print_text=False)
    after = hashlib.md5(db_path.read_bytes()).hexdigest()
    assert before == after


# ── Invariant: no provider/broker/execution imports ──────────────────

# Check actual import statements via AST so docstring mentions of these
# modules (e.g. "no alpaca/fmp imports") don't false-positive.
FORBIDDEN_IMPORT_PREFIXES = (
    "alpaca",
    "fmp",
    "yfinance",
    "core.alpaca_client",
    "core.fmp_client",
    "execution.",
    "execution",
    "council.",
    "council",
    "strategies.",
    "strategies",
    "order_manager",
    "paper_governance",
    "decision_logger",
)


def _module_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imported_module_names(src: str) -> List[str]:
    """Return the dotted module names that the source actually imports.
    Uses ast to avoid matching docstring text."""
    import ast
    names: List[str] = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names.append(mod)
            for alias in node.names:
                if mod:
                    names.append(f"{mod}.{alias.name}")
                else:
                    names.append(alias.name)
    return names


def test_runner_does_not_import_provider_or_execution_modules():
    src = _module_source(
        Path(__file__).resolve().parents[2] / "research" / "mcp_audit_workflows.py"
    )
    imports = _imported_module_names(src)
    for name in imports:
        for bad in FORBIDDEN_IMPORT_PREFIXES:
            assert not (name == bad or name.startswith(bad + ".")), (
                f"mcp_audit_workflows.py imports forbidden module {name!r}"
            )


def test_runner_contains_no_mutating_sql():
    src = _module_source(
        Path(__file__).resolve().parents[2] / "research" / "mcp_audit_workflows.py"
    )
    for stmt in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER"):
        assert not re.search(rf"\b{stmt}\b\s+(?:INTO|TABLE|FROM)", src, re.IGNORECASE), (
            f"mcp_audit_workflows.py must not contain mutating SQL ({stmt})"
        )
