"""Tests for the journal digest audit reviewer (research-only audit layer).

Covers the eight required behaviors:
  1. Phase 4B BLOCKED  -> research_verdict RESEARCH_ONLY
  2. low scanner recall -> HIGH scanner_recall flaw
  3. MIXED forward evidence -> alpha_discovery_quality never STRONG
  4. options overlay DISABLED -> options_overlay flaw
  5. dilution red flags -> fundamental_overlay flaw
  6. LLM failure -> deterministic fallback audit
  7. promote_to_signal is always false (every path, even a lying LLM)
  8. run_audit persists the sidecar + feedback queue and the dashboard
     status payload carries journal_audit

All tests run against temp roots with the LLM disabled or mocked — no
network, no credentials, and the production sidecar/queue are never
touched.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dashboards.research_command_center.data_adapter import (
    ArtifactStore,
    build_journal_audit,
    build_status,
)
from research import journal_audit_reviewer as jar

FORBIDDEN = re.compile(
    r"buy now|sell now|entry price|stop loss|target price|position size|"
    r"trade recommendation|paper signal|live signal|auto trade|place order|"
    r"submit order", re.IGNORECASE)


# ── digest fixtures ──────────────────────────────────────────────────────────


def make_digest(*, phase4b: str = "BLOCKED",
                verdict: str = "NEED_MORE_DATA",
                recall_line: str = "",
                options_line: str = "",
                fundamentals_line: str = "- AAPL: quality OK | GM 44% | "
                                          "OM 30% | net cash",
                quarantined: int = 0,
                warnings: str = "- No major warnings.") -> str:
    return f"""# Daily Research Digest

## 1. Data Quality
- Freshness: FRESH (scanner age 2.0h)
- Stale-price skipped: 3 | suspect-feed skipped: 1 | quarantined: {quarantined}
- Missing artifacts: 0
- Price refresh: healthy
{warnings}

## 2. Scanner / Why Names Appeared
- Total candidates: 42
- High-priority: AAPL, NVDA
{recall_line}

## 3. Sector / Regime
- Regime: UPTREND (confidence Medium; bias 5d Up / 10d Up / 30d Up)
{options_line}

## 4. Forward Evidence
- Tracked entries: 118 | matured 5d: 40 | matured 10d: 22
- Tracker verdict: {verdict}
- Phase 4B: {phase4b} (forward evidence insufficient)

## 5. Fundamental Overlay
{fundamentals_line}

## 6. Journal Review Queue
- Review first: AAPL, NVDA

## 7. Final Finding
Pipeline is operational.

RESEARCH-ONLY. Not a trade signal.
"""


@pytest.fixture(autouse=True)
def _no_llm_env(monkeypatch):
    """Never let a real key leak into the LLM path during tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEM_TRADER_SKIP_DOTENV", "true")  # no env-file load


# ── 1. Phase 4B BLOCKED => RESEARCH_ONLY ─────────────────────────────────────


def test_phase4b_blocked_forces_research_only():
    audit = jar.audit_daily_digest(make_digest(phase4b="BLOCKED"))
    assert audit["research_verdict"] == "RESEARCH_ONLY"


def test_phase4b_blocked_overrides_llm_verdict(monkeypatch):
    """Even if the LLM claims READY_FOR_HUMAN_REVIEW, a blocked Phase 4B
    is a hard invariant."""
    monkeypatch.setattr(jar, "_llm_audit", lambda text: {
        "research_verdict": "READY_FOR_HUMAN_REVIEW",
        "alpha_discovery_quality": "IMPROVING",
        "engine_health": "OPERATIONAL",
        "promote_to_signal": False,
        "one_line_summary": "all good",
        "what_is_working": [], "flaws_detected": [],
        "recommended_claude_code_tasks": [],
        "audit_source": "llm", "model": "test",
    })
    audit = jar.audit_daily_digest(make_digest(phase4b="BLOCKED"))
    assert audit["research_verdict"] == "RESEARCH_ONLY"


# ── 2. low scanner recall => HIGH scanner_recall flaw ───────────────────────


def test_low_scanner_recall_creates_high_flaw():
    digest = make_digest(
        recall_line="- Warning: scanner recall 1.1% vs 18% baseline")
    audit = jar.audit_daily_digest(digest)
    flaws = [f for f in audit["flaws_detected"]
             if f["area"] == "scanner_recall"]
    assert flaws and flaws[0]["severity"] == "HIGH"


def test_healthy_recall_creates_no_recall_flaw():
    digest = make_digest(recall_line="- Scanner recall 18.0% this week")
    audit = jar.audit_daily_digest(digest)
    assert not [f for f in audit["flaws_detected"]
                if f["area"] == "scanner_recall"]


# ── 3. MIXED forward evidence => never STRONG ───────────────────────────────


@pytest.mark.parametrize("verdict", ["MIXED", "INCONCLUSIVE",
                                     "NEED_MORE_DATA"])
def test_immature_forward_evidence_never_strong(verdict):
    audit = jar.audit_daily_digest(make_digest(verdict=verdict))
    assert audit["alpha_discovery_quality"] != "STRONG"


def test_immature_forward_evidence_caps_llm_strong_claim(monkeypatch):
    monkeypatch.setattr(jar, "_llm_audit", lambda text: {
        "research_verdict": "CAUTION",
        "alpha_discovery_quality": "STRONG",   # lying
        "engine_health": "OPERATIONAL",
        "promote_to_signal": False,
        "one_line_summary": "s", "what_is_working": [],
        "flaws_detected": [], "recommended_claude_code_tasks": [],
        "audit_source": "llm", "model": "test",
    })
    audit = jar.audit_daily_digest(make_digest(verdict="MIXED"))
    assert audit["alpha_discovery_quality"] != "STRONG"


# ── 4. options overlay disabled => options_overlay flaw ─────────────────────


def test_options_overlay_disabled_creates_flaw():
    digest = make_digest(options_line="- Options overlay: DISABLED "
                                      "(Tradier token pending)")
    audit = jar.audit_daily_digest(digest)
    flaws = [f for f in audit["flaws_detected"]
             if f["area"] == "options_overlay"]
    assert flaws and flaws[0]["severity"] == "MEDIUM"


# ── 5. dilution red flags => fundamental_overlay flaw ───────────────────────


def test_dilution_red_flag_creates_fundamental_flaw():
    digest = make_digest(
        fundamentals_line="- XYZ: quality LOW | GM -4.0% | OM -12% | "
                          "net debt | dilution 3q +22.0% — RED FLAG: high "
                          "dilution +22.0%/3q; negative gross margin")
    audit = jar.audit_daily_digest(digest)
    assert [f for f in audit["flaws_detected"]
            if f["area"] == "fundamental_overlay"]


def test_data_quarantine_creates_data_quality_flaw():
    audit = jar.audit_daily_digest(make_digest(quarantined=23))
    assert [f for f in audit["flaws_detected"] if f["area"] == "data_quality"]


# ── 6. LLM failure => deterministic fallback ────────────────────────────────


def test_llm_failure_returns_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-test")

    def boom(text):
        raise RuntimeError("simulated API timeout")

    monkeypatch.setattr(jar, "_llm_audit", boom)
    audit = jar.audit_daily_digest(make_digest())
    assert audit["audit_source"] == "rule_based_fallback"
    assert "simulated API timeout" in (audit["fallback_reason"] or "")
    assert audit["research_verdict"] in jar.RESEARCH_VERDICTS


def test_no_api_key_returns_fallback():
    audit = jar.audit_daily_digest(make_digest())
    assert audit["audit_source"] == "rule_based_fallback"


def test_llm_invalid_json_returns_fallback(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stub-key-for-test")
    monkeypatch.setattr(
        jar, "_llm_audit",
        lambda text: jar._extract_json_object("this is not json at all"))
    audit = jar.audit_daily_digest(make_digest())
    assert audit["audit_source"] == "rule_based_fallback"


def test_fallback_schema_is_complete_and_clean():
    audit = jar.audit_daily_digest(make_digest())
    for key in ("research_verdict", "alpha_discovery_quality",
                "engine_health", "promote_to_signal", "one_line_summary",
                "what_is_working", "flaws_detected",
                "recommended_claude_code_tasks"):
        assert key in audit
    assert audit["research_verdict"] in jar.RESEARCH_VERDICTS
    assert audit["alpha_discovery_quality"] in jar.QUALITY_LEVELS
    assert audit["engine_health"] in jar.ENGINE_HEALTH
    for f in audit["flaws_detected"]:
        assert f["severity"] in jar.SEVERITIES
        assert f["area"] in jar.FLAW_AREAS
    blob = json.dumps(audit)
    assert not FORBIDDEN.search(blob)


def test_empty_digest_is_insufficient_data():
    audit = jar.audit_daily_digest("")
    assert audit["engine_health"] == "INSUFFICIENT_DATA"
    assert audit["research_verdict"] == "RESEARCH_ONLY"


# ── 7. promote_to_signal is ALWAYS false ────────────────────────────────────


def test_promote_to_signal_false_in_fallback():
    audit = jar.audit_daily_digest(make_digest(phase4b="UNBLOCKED",
                                               verdict="PROMISING"))
    assert audit["promote_to_signal"] is False


def test_promote_to_signal_forced_false_even_if_llm_lies(monkeypatch):
    monkeypatch.setattr(jar, "_llm_audit", lambda text: {
        "research_verdict": "READY_FOR_HUMAN_REVIEW",
        "alpha_discovery_quality": "IMPROVING",
        "engine_health": "OPERATIONAL",
        "promote_to_signal": True,   # must be crushed
        "one_line_summary": "s", "what_is_working": [],
        "flaws_detected": [], "recommended_claude_code_tasks": [],
        "audit_source": "llm", "model": "test",
    })
    audit = jar.audit_daily_digest(make_digest(phase4b="UNBLOCKED",
                                               verdict="PROMISING"))
    assert audit["promote_to_signal"] is False


# ── 8. persistence + dashboard payload ──────────────────────────────────────


def test_run_audit_writes_sidecar_and_feedback_queue(tmp_path):
    digest = make_digest(quarantined=23)
    audit = jar.run_audit(digest, root=tmp_path, use_llm=False)

    sidecar = tmp_path / "cache" / "research" / "journal_audit_latest.json"
    queue = tmp_path / "logs" / "research_engine_feedback_queue.jsonl"
    assert sidecar.exists()
    saved = json.loads(sidecar.read_text(encoding="utf-8"))
    assert saved["promote_to_signal"] is False
    assert saved["one_line_summary"] == audit["one_line_summary"]

    assert queue.exists()
    entries = [json.loads(ln) for ln in
               queue.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert entries
    assert all(e["source"] == "journal_audit_reviewer" for e in entries)
    assert {e["task"] for e in entries} == \
        set(audit["recommended_claude_code_tasks"])


def test_feedback_queue_dedupes_same_digest(tmp_path):
    digest = make_digest(quarantined=5)
    jar.run_audit(digest, root=tmp_path, use_llm=False)
    jar.run_audit(digest, root=tmp_path, use_llm=False)  # re-run, same digest
    queue = tmp_path / "logs" / "research_engine_feedback_queue.jsonl"
    entries = [ln for ln in queue.read_text(encoding="utf-8").splitlines()
               if ln.strip()]
    n_tasks = len(jar.run_audit(digest, root=tmp_path, use_llm=False,
                                write=False)["recommended_claude_code_tasks"])
    assert len(entries) == n_tasks  # not doubled


def test_llm_reaudit_of_same_digest_still_queues(tmp_path, monkeypatch):
    """A fallback audit queues first; a later LLM audit of the SAME digest
    must still append its (richer) tasks — dedupe is per audit source."""
    digest = make_digest(quarantined=5)
    jar.run_audit(digest, root=tmp_path, use_llm=False)
    monkeypatch.setattr(jar, "_llm_audit", lambda text: {
        "research_verdict": "CAUTION",
        "alpha_discovery_quality": "MIXED",
        "engine_health": "OPERATIONAL_WITH_BLOCKERS",
        "promote_to_signal": False,
        "one_line_summary": "s", "what_is_working": [],
        "flaws_detected": [],
        "recommended_claude_code_tasks": ["llm task A", "llm task B"],
        "audit_source": "llm", "model": "test",
    })
    jar.run_audit(digest, root=tmp_path)
    queue = tmp_path / "logs" / "research_engine_feedback_queue.jsonl"
    rows = [json.loads(ln) for ln in
            queue.read_text(encoding="utf-8").splitlines() if ln.strip()]
    sources = {r["audit_source"] for r in rows}
    assert sources == {"rule_based_fallback", "llm"}
    assert {r["task"] for r in rows if r["audit_source"] == "llm"} == \
        {"llm task A", "llm task B"}


def test_dashboard_payload_includes_journal_audit(tmp_path):
    jar.run_audit(make_digest(quarantined=3), root=tmp_path, use_llm=False)
    store = ArtifactStore(root=tmp_path)
    status = build_status(store)
    ja = status["journal_audit"]
    assert ja["present"] is True
    assert ja["promote_to_signal"] is False
    assert ja["one_line_summary"]
    assert ja["engine_health"] in jar.ENGINE_HEALTH
    assert ja["alpha_discovery_quality"] in jar.QUALITY_LEVELS
    assert len(ja["top_flaws"]) <= 3
    for f in ja["top_flaws"]:
        assert set(f) == {"severity", "area", "issue"}


def test_dashboard_payload_degrades_when_audit_missing(tmp_path):
    block = build_journal_audit(ArtifactStore(root=tmp_path))
    assert block == {"present": False, "promote_to_signal": False}


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    digest_file = tmp_path / "digest.md"
    digest_file.write_text(make_digest(), encoding="utf-8")
    rc = jar.main(["--digest-file", str(digest_file), "--dry-run",
                   "--skip-llm", "--root", str(tmp_path)])
    assert rc == 0
    assert not (tmp_path / "cache" / "research"
                / "journal_audit_latest.json").exists()
    assert not (tmp_path / "logs"
                / "research_engine_feedback_queue.jsonl").exists()
    out = capsys.readouterr().out
    assert "research_verdict" in out


def test_cli_reads_latest_digest_from_journal(tmp_path):
    journal = tmp_path / "data" / "research" / "journal.jsonl"
    journal.parent.mkdir(parents=True)
    entries = [
        {"source_view": "manual", "note": "not a digest"},
        {"source_view": "journal_digest_script",
         "note": make_digest(quarantined=1)},
    ]
    journal.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    rc = jar.main(["--skip-llm", "--root", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "cache" / "research"
            / "journal_audit_latest.json").exists()


def test_cli_errors_cleanly_without_digest(tmp_path):
    assert jar.main(["--skip-llm", "--root", str(tmp_path)]) == 1
