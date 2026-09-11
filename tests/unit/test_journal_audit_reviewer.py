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
- Broad-scanner candidates (discovery only): AAPL, NVDA
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
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_FAIL_OPEN", raising=False)
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


_LEGACY_RECALL_LINE = (
    "- Warning: Legacy council-funnel recall 0.0% (autopsy of the "
    "pipeline decommissioned 2026-06-13, not the live board) — main "
    "miss: FILTER_TOO_STRICT — live research-board recall accruing via "
    "scanner-recall cohorts: NEED_MORE_DATA")


def test_legacy_labeled_recall_extracted_and_flagged():
    sig = jar.extract_digest_signals(make_digest(
        recall_line=_LEGACY_RECALL_LINE))
    assert sig["legacy_recall_labeled"] is True
    # the legacy figure is excluded from scanner_recall_pct entirely (not
    # just from flaw-triggering) so it never leaks into any consumer --
    # e.g. an MCP/dashboard snapshot -- as if it were live-board data.
    assert sig["scanner_recall_pct"] is None
    assert sig["live_recall_verdict"] == "NEED_MORE_DATA"


def test_legacy_labeled_recall_creates_no_high_flaw():
    """A recall figure explicitly labeled as the decommissioned council
    funnel must never be read as a live-board blocker -- it is a
    historical autopsy, not evidence about the current scanner."""
    digest = make_digest(recall_line=_LEGACY_RECALL_LINE)
    audit = jar.audit_daily_digest(digest)
    assert not [f for f in audit["flaws_detected"]
                if f["area"] == "scanner_recall"]
    assert not [a for a in audit["next_system_actions"]
                if a["area"] == "scanner_recall"]
    assert "low recall" not in audit["one_line_summary"]


def test_non_legacy_recall_still_flagged_alongside_legacy_line():
    """If a genuinely live-board recall figure ever appears in the same
    digest as the legacy line, it must still be caught -- the guard only
    suppresses the specific legacy-labeled occurrence, not all recall
    signal."""
    digest = make_digest(
        recall_line=_LEGACY_RECALL_LINE
        + "\n- Warning: live-board recall 1.5% vs 40% baseline")
    audit = jar.audit_daily_digest(digest)
    flaws = [f for f in audit["flaws_detected"] if f["area"] == "scanner_recall"]
    assert flaws and flaws[0]["severity"] == "HIGH"


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

    # recommended_claude_code_tasks are free-text one-liners the LLM
    # rewords nightly — they never dedupe reliably, so they stay in the
    # sidecar as read-only notes only and must NOT be queued.
    assert audit["recommended_claude_code_tasks"]
    assert saved["recommended_claude_code_tasks"] == \
        audit["recommended_claude_code_tasks"]

    assert queue.exists()
    entries = [json.loads(ln) for ln in
               queue.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert entries
    assert all(e["source"] == "journal_audit_reviewer" for e in entries)
    assert all(e["kind"] == "system_repair_task" for e in entries)
    repairs = entries
    assert {(r["priority"], r["area"], r["task"]) for r in repairs} == \
        {(a["priority"], a["area"], a["task"])
         for a in audit["next_system_actions"]}
    for r in repairs:
        assert r["priority"] in jar.PRIORITIES
        assert r["area"] in jar.ACTION_AREAS
        assert r["why"] and r["success_metric"]


def test_feedback_queue_dedupes_same_digest(tmp_path):
    digest = make_digest(quarantined=5)
    jar.run_audit(digest, root=tmp_path, use_llm=False)
    jar.run_audit(digest, root=tmp_path, use_llm=False)  # re-run, same digest
    queue = tmp_path / "logs" / "research_engine_feedback_queue.jsonl"
    entries = [ln for ln in queue.read_text(encoding="utf-8").splitlines()
               if ln.strip()]
    reference = jar.run_audit(digest, root=tmp_path, use_llm=False,
                              write=False)
    n_expected = len(reference["next_system_actions"])
    assert len(entries) == n_expected  # not doubled; recommended tasks unqueued


def test_llm_reaudit_of_same_digest_still_queues(tmp_path, monkeypatch):
    """A fallback audit queues first; a later LLM audit of the SAME digest
    must still append its own next_system_actions — dedupe is per audit
    source, not per digest.  recommended_claude_code_tasks (here "llm
    task A"/"llm task B") must never appear in the queue at all, on
    either path — they are sidecar-only read notes."""
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
    assert all(r["kind"] == "system_repair_task" for r in rows)
    assert {r["task"] for r in rows}.isdisjoint({"llm task A", "llm task B"})


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


# ── 9. next_system_actions (structured repair tasks) ─────────────────────────


def test_low_recall_generates_p0_scanner_action():
    digest = make_digest(
        recall_line="- Warning: Scanner recall low at 2.3% — main miss: "
                    "FILTER_TOO_STRICT (simple-RS baseline: 34.0%)")
    audit = jar.audit_daily_digest(digest)
    acts = [a for a in audit["next_system_actions"]
            if a["area"] == "scanner_recall"]
    assert acts and acts[0]["priority"] == "P0"
    task = acts[0]["task"]
    assert "reject counts by filter" in task
    assert "top 25" in task
    assert "5d, 10d, and 20d" in task
    assert "Do not loosen any filter automatically" in task
    # digest numbers land in the why
    assert "2.3%" in acts[0]["why"]
    assert "34.0%" in acts[0]["why"]
    assert "FILTER_TOO_STRICT" in acts[0]["why"]
    assert acts[0]["success_metric"]


def test_no_forward_edge_generates_p0_forward_action():
    digest = make_digest(verdict="NO_FORWARD_EDGE")
    audit = jar.audit_daily_digest(digest)
    acts = [a for a in audit["next_system_actions"]
            if a["area"] == "forward_evidence"]
    assert acts and acts[0]["priority"] == "P0"
    task = acts[0]["task"]
    assert "hit rate vs SPY" in task
    assert "median forward return" in task
    assert "average forward return" in task
    assert "max drawdown after selection" in task
    assert "5d, 10d, and 20d" in task
    assert "high-priority names separated from watch-only names" in task
    assert "NO_FORWARD_EDGE" in acts[0]["why"]
    # negative verdict also lands as a CRITICAL flaw in the fallback
    fe = [f for f in audit["flaws_detected"]
          if f["area"] == "forward_evidence"]
    assert fe and fe[0]["severity"] == "CRITICAL"


def test_options_disabled_generates_p2_action():
    digest = make_digest(
        options_line="- Options overlay: DISABLED — insufficient coverage")
    audit = jar.audit_daily_digest(digest)
    acts = [a for a in audit["next_system_actions"]
            if a["area"] == "options_overlay"]
    assert acts and acts[0]["priority"] == "P2"
    task = acts[0]["task"]
    assert "valid options data" in task
    assert "skipped" in task
    assert "required or optional" in task


def test_quarantine_generates_p1_data_quality_action():
    audit = jar.audit_daily_digest(make_digest(quarantined=8))
    acts = [a for a in audit["next_system_actions"]
            if a["area"] == "data_quality"]
    assert acts and acts[0]["priority"] == "P1"
    assert "8 ticker(s) quarantined" in acts[0]["why"]


def test_healthy_digest_has_no_p0_actions():
    audit = jar.audit_daily_digest(
        make_digest(phase4b="UNBLOCKED", verdict="PROMISING"))
    assert not [a for a in audit["next_system_actions"]
                if a["priority"] == "P0"]


def test_actions_sorted_p0_first_and_schema_clean():
    digest = make_digest(
        verdict="NO_FORWARD_EDGE",
        recall_line="- Warning: Scanner recall low at 2.3% (simple-RS "
                    "baseline: 34.0%)",
        options_line="- Options overlay: DISABLED — insufficient coverage",
        quarantined=8)
    audit = jar.audit_daily_digest(digest)
    acts = audit["next_system_actions"]
    priorities = [a["priority"] for a in acts]
    assert priorities == sorted(priorities)
    assert priorities[0] == "P0"
    for a in acts:
        assert set(a) == {"priority", "area", "task", "why",
                          "success_metric"}
        assert a["priority"] in jar.PRIORITIES
        assert a["area"] in jar.ACTION_AREAS
    assert not FORBIDDEN.search(json.dumps(acts))


def test_llm_actions_sanitized_and_deterministic_actions_win(monkeypatch):
    monkeypatch.setattr(jar, "_llm_audit", lambda text: {
        "research_verdict": "CAUTION",
        "alpha_discovery_quality": "MIXED",
        "engine_health": "OPERATIONAL_WITH_BLOCKERS",
        "promote_to_signal": False,
        "one_line_summary": "s", "what_is_working": [],
        "flaws_detected": [], "recommended_claude_code_tasks": [],
        "next_system_actions": [
            {"priority": "P9", "area": "fundamental_overlay",
             "task": "llm fundamental task", "why": "w",
             "success_metric": "m"},
            {"priority": "P0", "area": "not_an_area",
             "task": "must be dropped", "why": "w", "success_metric": "m"},
            {"priority": "P0", "area": "forward_evidence",
             "task": "llm forward task that must lose to deterministic",
             "why": "w", "success_metric": "m"},
        ],
        "audit_source": "llm", "model": "test",
    })
    audit = jar.audit_daily_digest(make_digest(verdict="NO_FORWARD_EDGE"))
    acts = audit["next_system_actions"]
    # bad area dropped
    assert "must be dropped" not in json.dumps(acts)
    # bad priority coerced to P2
    fund = [a for a in acts if a["area"] == "fundamental_overlay"]
    assert fund and fund[0]["priority"] == "P2"
    # deterministic forward action wins over the LLM's
    fwd = [a for a in acts if a["area"] == "forward_evidence"]
    assert len(fwd) == 1 and "hit rate vs SPY" in fwd[0]["task"]


# ── 10. audit_trend (vs previous audits) ─────────────────────────────────────


def test_first_audit_trend_has_no_priors(tmp_path):
    audit = jar.run_audit(make_digest(), root=tmp_path, use_llm=False)
    trend = audit["audit_trend"]
    assert trend["n_prior_audits"] == 0
    assert all(b["direction"] == "INSUFFICIENT_HISTORY"
               for b in trend["blockers"])


def test_trend_recall_improving_and_worsening(tmp_path):
    jar.run_audit(make_digest(
        recall_line="- Warning: scanner recall 1.1% vs 18% baseline"),
        root=tmp_path, use_llm=False)
    audit = jar.run_audit(make_digest(
        recall_line="- Warning: scanner recall 2.3% vs 18% baseline"),
        root=tmp_path, use_llm=False)
    rec = [b for b in audit["audit_trend"]["blockers"]
           if b["area"] == "scanner_recall"]
    assert rec and rec[0]["direction"] == "IMPROVING"
    assert audit["audit_trend"]["n_prior_audits"] == 1

    audit = jar.run_audit(make_digest(
        recall_line="- Warning: scanner recall 0.5% vs 18% baseline"),
        root=tmp_path, use_llm=False)
    rec = [b for b in audit["audit_trend"]["blockers"]
           if b["area"] == "scanner_recall"]
    assert rec and rec[0]["direction"] == "WORSENING"


def test_trend_forward_verdict_unchanged(tmp_path):
    jar.run_audit(make_digest(verdict="NO_FORWARD_EDGE", quarantined=1),
                  root=tmp_path, use_llm=False)
    audit = jar.run_audit(
        make_digest(verdict="NO_FORWARD_EDGE", quarantined=2),
        root=tmp_path, use_llm=False)
    fwd = [b for b in audit["audit_trend"]["blockers"]
           if b["area"] == "forward_evidence"]
    assert fwd and fwd[0]["direction"] == "UNCHANGED"


def test_trend_forward_verdict_improving(tmp_path):
    jar.run_audit(make_digest(verdict="NO_FORWARD_EDGE", quarantined=1),
                  root=tmp_path, use_llm=False)
    audit = jar.run_audit(
        make_digest(verdict="NEED_MORE_DATA", quarantined=1),
        root=tmp_path, use_llm=False)
    fwd = [b for b in audit["audit_trend"]["blockers"]
           if b["area"] == "forward_evidence"]
    assert fwd and fwd[0]["direction"] == "IMPROVING"


def test_trend_resolved_blocker(tmp_path):
    jar.run_audit(make_digest(
        options_line="- Options overlay: DISABLED — insufficient coverage"),
        root=tmp_path, use_llm=False)
    audit = jar.run_audit(make_digest(quarantined=1),
                          root=tmp_path, use_llm=False)
    opt = [b for b in audit["audit_trend"]["blockers"]
           if b["area"] == "options_overlay"]
    assert opt and opt[0]["direction"] == "RESOLVED"
    new = [b for b in audit["audit_trend"]["blockers"]
           if b["area"] == "data_quality"]
    assert new and new[0]["direction"] == "NEW"


def test_trend_window_caps_at_five_priors(tmp_path):
    for q in range(1, 8):  # 7 distinct digests
        jar.run_audit(make_digest(quarantined=q), root=tmp_path,
                      use_llm=False)
    audit = jar.run_audit(make_digest(quarantined=9), root=tmp_path,
                          use_llm=False)
    assert audit["audit_trend"]["n_prior_audits"] == 5
    assert len(audit["audit_trend"]["prior_generated_at"]) == 5


def test_history_file_appended_and_deduped(tmp_path):
    digest = make_digest(quarantined=4)
    jar.run_audit(digest, root=tmp_path, use_llm=False)
    jar.run_audit(digest, root=tmp_path, use_llm=False)  # same digest
    history = tmp_path / "data" / "research" / "journal_audit_history.jsonl"
    rows = [json.loads(ln) for ln in
            history.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) == 1
    assert rows[0]["blockers"]["data_quality"]["quarantine_count"] == 4
    assert rows[0]["audit_source"] == "rule_based_fallback"


def test_dry_run_writes_no_history(tmp_path):
    audit = jar.run_audit(make_digest(), root=tmp_path, use_llm=False,
                          write=False)
    assert "audit_trend" in audit  # trend still computed read-only
    assert not (tmp_path / "data" / "research"
                / "journal_audit_history.jsonl").exists()
    assert not (tmp_path / "cache" / "research"
                / "journal_audit_latest.json").exists()


def test_sidecar_carries_actions_and_trend(tmp_path):
    jar.run_audit(make_digest(quarantined=2), root=tmp_path, use_llm=False)
    sidecar = tmp_path / "cache" / "research" / "journal_audit_latest.json"
    saved = json.loads(sidecar.read_text(encoding="utf-8"))
    assert "next_system_actions" in saved
    assert "audit_trend" in saved
    assert saved["promote_to_signal"] is False
    assert not FORBIDDEN.search(json.dumps(saved))


# ── 11. balanced analyst interpretation (candidate tiers + missing evidence) ─


def make_analyst_digest(*, verdict: str = "NO_FORWARD_EDGE",
                        phase4b: str = "BLOCKED",
                        matured_20d: str = "not tracked in current artifacts",
                        leading: str = "none",
                        weak: str = "XLU, XLB, XLK",
                        top_sectors: str = "Industrials, Technology, "
                                           "Consumer Cyclical",
                        alignment: str = "aligned") -> str:
    """Digest mirroring the real 2026-07-09 format: mixed-quality
    high-priority names, a social-attention name, and analyst context."""
    return f"""# Daily Research Digest

## 1. Data Quality
- Freshness: FRESH (scanner age 0.0h)
- Stale-price skipped: 17 | suspect-feed skipped: 7 | quarantined: 5
- Missing artifacts: 0
- Warning: Scanner recall low at 2.0% — main miss: FILTER_TOO_STRICT (simple-RS baseline: 33.7%)
- Warning: Options overlay: DISABLED — insufficient coverage
- Warning: Targeted backfill plan: 8 tickers need >=300 bars — run targeted-backfill --execute to fill

## 2. Scanner / Why Names Appeared
- Total candidates: 99
- Broad-scanner candidates (discovery only): FBIN, SDGR, AGYS, BLMN, RH, LEVI
- RH: Social attention signal (source: social_arb)

## 3. Sector / Regime
- Regime: Bull Pullback / Buy-the-Dip (confidence low; bias 5d constructive / 10d constructive / 30d mixed)
- Leading sectors: {leading} | weak sectors: {weak}
- Top-name sectors: {top_sectors} — alignment: {alignment}

## 4. Forward Evidence
- Tracked entries: 1771 | matured 5d: 763 | matured 10d: 338 | matured 20d: {matured_20d}
- Tracker verdict: {verdict} | moment-of-truth verdict: INCONCLUSIVE
- Phase 4B: {phase4b} (forward evidence insufficient)

## 5. Fundamental Overlay
- FBIN: quality PROFITABLE_CASHGEN | GM 44.01% | OM 11.0% | net debt | dilution 3q -2.1%
- SDGR: quality UNPROFITABLE_FUNDED | GM 55.33% | OM -64.65% | net cash | dilution 3q +0.8%
- AGYS: quality PROFITABLE_CASHGEN | GM 61.85% | OM 12.61% | net cash | dilution 3q +0.5%
- BLMN: quality PROFITABLE_CASHGEN | GM 54.45% | OM 1.07% | net debt | dilution 3q +0.7%
- RH: quality PROFITABLE_CASHGEN | GM 43.54% | OM 10.8% | net debt | dilution 3q -4.5%
- LEVI: quality PROFITABLE_CASHGEN | GM 61.69% | OM 10.54% | net debt | dilution 3q -1.2%

## 6. Journal Review Queue
- Review first: FBIN, SDGR, AGYS, BLMN, RH, LEVI

## 7. Final Finding
Pipeline is operational.

Research only — not a signal or recommendation.
"""


def test_mixed_fundamentals_produce_tiered_candidate_summary():
    audit = jar.audit_daily_digest(make_analyst_digest(), use_llm=False)
    cq = audit["candidate_quality_summary"]
    assert cq["overall"] == "IMPROVING_BUT_UNPROVEN"
    assert cq["higher_quality_manual_review"] == ["FBIN", "AGYS", "RH",
                                                  "LEVI"]
    # BLMN: profitable but OM 1.07% (< 3% thin-margin floor)
    assert cq["caution_manual_review"] == ["BLMN"]


def test_unprofitable_funded_negative_om_is_speculative():
    audit = jar.audit_daily_digest(make_analyst_digest(), use_llm=False)
    cq = audit["candidate_quality_summary"]
    assert cq["speculative_or_prove_it"] == ["SDGR"]
    assert "SDGR" not in cq["higher_quality_manual_review"]


def test_social_attention_name_requires_confirmation():
    audit = jar.audit_daily_digest(make_analyst_digest(), use_llm=False)
    cq = audit["candidate_quality_summary"]
    assert cq["social_signal_requires_confirmation"] == ["RH"]
    # RH is profitable so it still tiers as higher-quality — the social
    # flag is an additional confirmation requirement, not a demotion.
    assert "RH" in cq["higher_quality_manual_review"]
    assert any("confirmation" in s and "RH" in s
               for s in audit["unbiased_next_steps"])


def test_missing_20d_creates_warning_and_system_action():
    audit = jar.audit_daily_digest(make_analyst_digest(), use_llm=False)
    missing = audit["missing_evidence_or_artifacts"]
    fwd = [e for e in missing if e["area"] == "forward_evidence"]
    assert fwd and "20d" in fwd[0]["issue"]
    acts = [a for a in audit["next_system_actions"]
            if a["area"] == "forward_evidence"
            and "winsorized mean" in a["success_metric"]]
    assert acts and acts[0]["priority"] == "P0"
    # a digest that DOES track 20d raises neither
    audit2 = jar.audit_daily_digest(
        make_analyst_digest(matured_20d="120"), use_llm=False)
    assert not [e for e in audit2["missing_evidence_or_artifacts"]
                if e["area"] == "forward_evidence"]


def test_sector_alignment_review_warning():
    audit = jar.audit_daily_digest(make_analyst_digest(), use_llm=False)
    sect = [e for e in audit["missing_evidence_or_artifacts"]
            if e["area"] == "sector_alignment"]
    assert sect and "XLK" in sect[0]["issue"]
    assert any(a["area"] == "sector_alignment"
               for a in audit["next_system_actions"])
    # with actual leading sectors the warning is not raised
    audit2 = jar.audit_daily_digest(
        make_analyst_digest(leading="XLI, XLV"), use_llm=False)
    assert not [e for e in audit2["missing_evidence_or_artifacts"]
                if e["area"] == "sector_alignment"]


def test_blocked_and_no_forward_edge_stay_research_only(monkeypatch):
    """Improving candidate quality never overrides the safety verdict —
    even when a lying LLM says READY_FOR_HUMAN_REVIEW."""
    monkeypatch.setattr(jar, "_llm_audit", lambda text: {
        "research_verdict": "READY_FOR_HUMAN_REVIEW",
        "alpha_discovery_quality": "IMPROVING",
        "engine_health": "OPERATIONAL",
        "promote_to_signal": True,
        "one_line_summary": "candidate quality improved",
        "candidate_quality_summary": {
            "overall": "IMPROVING_BUT_UNPROVEN",
            "higher_quality_manual_review": ["FBIN", "AGYS", "RH", "LEVI"],
            "caution_manual_review": [], "speculative_or_prove_it": [],
            "social_signal_requires_confirmation": []},
        "what_is_working": [], "flaws_detected": [],
        "recommended_claude_code_tasks": [], "next_system_actions": [],
        "audit_source": "llm", "model": "test",
    })
    audit = jar.audit_daily_digest(make_analyst_digest(
        verdict="NO_FORWARD_EDGE", phase4b="BLOCKED"))
    assert audit["research_verdict"] == "RESEARCH_ONLY"
    assert audit["promote_to_signal"] is False
    # NO_FORWARD_EDGE alone (Phase 4B wording unblocked) also pins it
    audit2 = jar.audit_daily_digest(make_analyst_digest(
        verdict="NO_FORWARD_EDGE", phase4b="UNBLOCKED"), use_llm=False)
    assert audit2["research_verdict"] == "RESEARCH_ONLY"


def test_fallback_produces_tiers_and_unbiased_steps_without_llm():
    audit = jar.audit_daily_digest(make_analyst_digest(), use_llm=False)
    assert audit["audit_source"] == "rule_based_fallback"
    cq = audit["candidate_quality_summary"]
    assert cq["higher_quality_manual_review"] and \
        cq["caution_manual_review"] and cq["speculative_or_prove_it"]
    steps = "\n".join(audit["unbiased_next_steps"])
    assert "RESEARCH_ONLY" in steps
    assert "high-priority" in steps
    assert "fundamentals" in steps          # tier instead of equals
    assert "backfill" in steps              # 8 tickers >=300 bars
    assert "20d" in steps                   # missing horizon
    assert "shadow recall" in steps         # not loosening gates
    assert "sector-alignment" in steps      # none-vs-weak overlap
    assert audit["balanced_research_takeaway"]
    assert "manual review, not promotion" in \
        audit["balanced_research_takeaway"]
    # two-sided one-liner: improvement AND blockers
    assert "Candidate quality improved" in audit["one_line_summary"]
    assert "research-only" in audit["one_line_summary"]


def test_llm_cannot_promote_tickers_or_invent_them(monkeypatch):
    monkeypatch.setattr(jar, "_llm_audit", lambda text: {
        "research_verdict": "RESEARCH_ONLY",
        "alpha_discovery_quality": "MIXED",
        "engine_health": "OPERATIONAL_WITH_BLOCKERS",
        "promote_to_signal": True,  # must be forced false
        "one_line_summary": "s",
        "candidate_quality_summary": {
            "overall": "STRONG_BUT_BLOCKED",
            # SDGR promoted + invented ticker: both must be rejected
            "higher_quality_manual_review": ["SDGR", "ZZZZ"],
            # demotion of a deterministic higher-quality name is allowed
            "caution_manual_review": ["LEVI"],
            "speculative_or_prove_it": [],
            "social_signal_requires_confirmation": ["RH"]},
        "what_is_working": [], "flaws_detected": [],
        "recommended_claude_code_tasks": [], "next_system_actions": [],
        "audit_source": "llm", "model": "test",
    })
    audit = jar.audit_daily_digest(make_analyst_digest())
    assert audit["promote_to_signal"] is False
    cq = audit["candidate_quality_summary"]
    assert "SDGR" in cq["speculative_or_prove_it"]      # not promoted
    assert "ZZZZ" not in json.dumps(cq)                 # invented: dropped
    assert "LEVI" in cq["caution_manual_review"]        # demotion allowed
    assert "LEVI" not in cq["higher_quality_manual_review"]
    assert cq["social_signal_requires_confirmation"] == ["RH"]


def test_existing_blocker_detection_still_works():
    audit = jar.audit_daily_digest(make_analyst_digest(), use_llm=False)
    areas = {f["area"] for f in audit["flaws_detected"]}
    assert {"scanner_recall", "forward_evidence",
            "options_overlay"} <= areas
    act_areas = {a["area"] for a in audit["next_system_actions"]}
    assert {"scanner_recall", "forward_evidence", "data_quality",
            "options_overlay"} <= act_areas


def test_no_broker_or_execution_imports_in_reviewer():
    import ast as _ast
    source = Path(jar.__file__).read_text(encoding="utf-8")
    tree = _ast.parse(source)
    imported = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, _ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = ("alpaca", "tradier", "execution", "broker",
                 "core.config", "core.alpaca", "order", "strategies",
                 "council")
    for mod in imported:
        assert not any(mod.startswith(f) or f in mod for f in forbidden), \
            f"forbidden import in journal_audit_reviewer: {mod}"


# ── 12. per-program verdicts (Phase 5.1) ─────────────────────────────────────


PROGRAM_SECTION = """
## 4b. Research Programs
- TACTICAL (hold 5-15td): candidates today 31 | verdict INSUFFICIENT_MATURE_EVIDENCE | matured episodes 5d:96 10d:32
- SWING (hold 45-60td): candidates today 58 | verdict PROMISING_BUT_UNPROVEN | matured episodes 5d:99 10d:69
- LONG_TERM (hold 126-378td): candidates today 11 | verdict INSUFFICIENT_MATURE_EVIDENCE | matured episodes none
"""


def test_program_verdicts_parsed_from_digest():
    digest = make_analyst_digest() + PROGRAM_SECTION
    sig = jar.extract_digest_signals(digest)
    assert sig["digest_programs"]["TACTICAL"]["verdict"] == \
        "INSUFFICIENT_MATURE_EVIDENCE"
    assert sig["digest_programs"]["SWING"]["candidates_today"] == 58
    audit = jar.audit_daily_digest(digest, use_llm=False)
    pv = audit["program_verdicts"]
    assert pv["tactical"] == "INSUFFICIENT_MATURE_EVIDENCE"
    assert pv["swing"] == "PROMISING_BUT_UNPROVEN"
    assert pv["long_term"] == "INSUFFICIENT_MATURE_EVIDENCE"
    # safety verdict, never a blended performance conclusion
    assert pv["overall_engine"] == "RESEARCH_ONLY"


def test_program_verdicts_llm_cannot_override_digest(monkeypatch):
    monkeypatch.setattr(jar, "_llm_audit", lambda text: {
        "research_verdict": "RESEARCH_ONLY",
        "alpha_discovery_quality": "MIXED",
        "engine_health": "OPERATIONAL_WITH_BLOCKERS",
        "promote_to_signal": False,
        "one_line_summary": "s",
        "program_verdicts": {  # lying LLM promotes all programs
            "tactical": "VALIDATED_EDGE",
            "swing": "VALIDATED_EDGE",
            "long_term": "VALIDATED_EDGE",
            "overall_engine": "READY_FOR_HUMAN_REVIEW"},
        "what_is_working": [], "flaws_detected": [],
        "recommended_claude_code_tasks": [], "next_system_actions": [],
        "audit_source": "llm", "model": "test",
    })
    audit = jar.audit_daily_digest(make_analyst_digest() + PROGRAM_SECTION)
    pv = audit["program_verdicts"]
    assert pv["tactical"] == "INSUFFICIENT_MATURE_EVIDENCE"  # digest wins
    assert pv["swing"] == "PROMISING_BUT_UNPROVEN"
    assert pv["overall_engine"] == "RESEARCH_ONLY"


def test_program_verdicts_unknown_when_section_missing():
    audit = jar.audit_daily_digest(make_analyst_digest(), use_llm=False)
    pv = audit["program_verdicts"]
    assert pv["tactical"] == "UNKNOWN"
    assert pv["swing"] == "UNKNOWN"
    assert pv["long_term"] == "UNKNOWN"
    assert pv["overall_engine"] == "RESEARCH_ONLY"


def test_honest_no_leadership_label_not_flagged_as_questionable():
    """After the d29046c033c6 fix, a digest that says 'not_assessable'
    for a no-leadership tape is correct behavior — only a digest still
    CLAIMING 'aligned' in that tape is a contradiction."""
    honest = make_analyst_digest(alignment="not_assessable")
    sig = jar.extract_digest_signals(honest)
    assert sig["sector_alignment_questionable"] is False
    audit = jar.audit_daily_digest(honest, use_llm=False)
    assert not [e for e in audit["missing_evidence_or_artifacts"]
                if e["area"] == "sector_alignment"]
    # the legacy contradiction still fires
    lying = make_analyst_digest(alignment="aligned")
    assert jar.extract_digest_signals(lying)[
        "sector_alignment_questionable"] is True


# ── 13. queue quieting: declared deliverables suppress repair actions ────────


DECLARATIONS = """
- Quarantine causes: 3x YOUNG_LISTING, 2x REPAIRED — see quarantine-cause report
- Warning: Options overlay: DISABLED — insufficient coverage (known structural cause: capped snapshot-collector universe, by design — see options-coverage report)
  - Red flags in review order: SDGR (unprofitable, OM -64.7%)
"""


def _digest_without_plan_warning() -> str:
    """Analyst digest minus the actionable backfill-plan warning — a
    coherent post-backfill state where the cause report covers the rest."""
    return make_analyst_digest().replace(
        "- Warning: Targeted backfill plan: 8 tickers need >=300 bars "
        "— run targeted-backfill --execute to fill\n", "")


def test_declared_deliverables_suppress_deterministic_actions():
    quiet = jar.audit_daily_digest(_digest_without_plan_warning()
                                   + DECLARATIONS, use_llm=False)
    areas = {a["area"] for a in quiet["next_system_actions"]}
    assert "options_overlay" not in areas
    assert "data_quality" not in areas
    assert "fundamental_overlay" not in areas
    # the still-live blockers keep their actions
    assert "scanner_recall" in areas and "forward_evidence" in areas

    loud = jar.audit_daily_digest(make_analyst_digest(), use_llm=False)
    loud_areas = {a["area"] for a in loud["next_system_actions"]}
    assert "options_overlay" in loud_areas  # no declaration -> fires


def test_backfill_pending_keeps_data_quality_action():
    actionable = DECLARATIONS.replace(
        "3x YOUNG_LISTING, 2x REPAIRED",
        "2x YOUNG_LISTING, 1x BACKFILL_PENDING")
    audit = jar.audit_daily_digest(_digest_without_plan_warning()
                                   + actionable, use_llm=False)
    areas = {a["area"] for a in audit["next_system_actions"]}
    assert "data_quality" in areas  # actionable cause -> still proposed


def test_plan_warning_blocks_data_quality_suppression():
    # a live "backfill plan: N tickers need" warning is actionable even
    # when the quarantine report is referenced (non-quarantined names
    # can need depth too)
    audit = jar.audit_daily_digest(make_analyst_digest() + DECLARATIONS,
                                   use_llm=False)
    assert "data_quality" in {
        a["area"] for a in audit["next_system_actions"]}


def test_llm_cannot_reintroduce_suppressed_area(monkeypatch):
    monkeypatch.setattr(jar, "_llm_audit", lambda text: {
        "research_verdict": "RESEARCH_ONLY",
        "alpha_discovery_quality": "MIXED",
        "engine_health": "OPERATIONAL_WITH_BLOCKERS",
        "promote_to_signal": False, "one_line_summary": "s",
        "what_is_working": [], "flaws_detected": [],
        "recommended_claude_code_tasks": [],
        "next_system_actions": [
            {"priority": "P2", "area": "options_overlay",
             "task": "build an options coverage report", "why": "w",
             "success_metric": "m"}],
        "audit_source": "llm", "model": "test",
    })
    audit = jar.audit_daily_digest(_digest_without_plan_warning()
                                   + DECLARATIONS)
    assert "options_overlay" not in {
        a["area"] for a in audit["next_system_actions"]}


# ── sector-alignment reconciliation (flag vs label contract) ─────────────────


def test_reconciliation_honest_misaligned_label_is_consistent():
    """An honest 'misaligned' label with questionable=False is the intended
    behavior — the reconciliation must say so instead of leaving the LLM
    to report a contradiction."""
    sig = jar.extract_digest_signals(make_analyst_digest(
        alignment="misaligned — top names sit in weak sectors: Technology"))
    assert sig["sector_alignment_questionable"] is False
    rec = sig["sector_alignment_reconciliation"]
    assert rec["consistent"] is True
    assert rec["expected_questionable"] is False
    assert "misaligned" in rec["note"]
    assert "intentionally NOT flagged" in rec["flag_semantics"]


def test_reconciliation_dishonest_aligned_claim_is_flagged_consistent():
    """The dishonest case still fires the flag, and the reconciliation
    confirms flag and label agree with the contract."""
    sig = jar.extract_digest_signals(make_analyst_digest(alignment="aligned"))
    assert sig["sector_alignment_questionable"] is True
    rec = sig["sector_alignment_reconciliation"]
    assert rec["consistent"] is True
    assert rec["expected_questionable"] is True


def test_reconciliation_detects_contract_mismatch():
    """A drifted edit that desyncs flag and label must be reported (and
    logged) as inconsistent."""
    rec = jar.reconcile_sector_alignment(
        alignment_label="aligned",
        leading_sectors=[],
        weak_overlap=["XLK"],
        sectors_line_present=True,
        questionable=False)   # contract says this should be True
    assert rec["consistent"] is False
    assert rec["expected_questionable"] is True
    assert "contradicts" in rec["note"]


def test_llm_context_carries_alignment_reconciliation():
    sig = jar.extract_digest_signals(make_analyst_digest(
        alignment="misaligned — top names sit in weak sectors: Technology"))
    ctx = jar._build_llm_context(sig)
    rec = ctx["sector_regime"]["sector_alignment_reconciliation"]
    assert rec["consistent"] is True
    assert "flag_semantics" in rec


# ── recall-diagnostics deliverable declaration (queue-quieting) ──────────────


_RECALL_DECLARATION = (
    "- Recall diagnostics: see scanner-recall diagnostics report "
    "(as-of 2026-06-04) — strict vs simple-RS vs loose cohorts tracked "
    "at 5d/10d/20d; top over-blocking filters: no_atr_contraction "
    "(511 rejected/128 winners missed). Gates unchanged pending forward "
    "evidence.")


def test_recall_declaration_suppresses_p0_build_action():
    digest = make_digest(
        recall_line="- Warning: Scanner recall low at 0.0% — main miss: "
                    "FILTER_TOO_STRICT (simple-RS baseline: 40.5%)\n"
                    + _RECALL_DECLARATION)
    audit = jar.audit_daily_digest(digest, use_llm=False)
    assert not [a for a in audit["next_system_actions"]
                if a["area"] == "scanner_recall"]
    # low recall itself stays visible as a HIGH flaw
    assert any(f["area"] == "scanner_recall" and f["severity"] == "HIGH"
               for f in audit["flaws_detected"])


def test_recall_declaration_blocks_llm_reintroduction(monkeypatch):
    monkeypatch.setattr(jar, "_llm_audit", lambda text: {
        "research_verdict": "RESEARCH_ONLY",
        "alpha_discovery_quality": "MIXED",
        "engine_health": "OPERATIONAL_WITH_BLOCKERS",
        "promote_to_signal": False, "one_line_summary": "s",
        "what_is_working": [], "flaws_detected": [],
        "recommended_claude_code_tasks": [],
        "next_system_actions": [
            {"priority": "P0", "area": "scanner_recall",
             "task": "build a recall diagnostic", "why": "w",
             "success_metric": "m"}],
        "audit_source": "llm", "model": "test",
    })
    digest = make_digest(
        recall_line="- Warning: Scanner recall low at 0.0% — main miss: "
                    "FILTER_TOO_STRICT (simple-RS baseline: 40.5%)\n"
                    + _RECALL_DECLARATION)
    audit = jar.audit_daily_digest(digest)
    assert "scanner_recall" not in {
        a["area"] for a in audit["next_system_actions"]}


def test_missing_recall_declaration_keeps_p0_action():
    digest = make_digest(
        recall_line="- Warning: Scanner recall low at 0.0% — main miss: "
                    "FILTER_TOO_STRICT (simple-RS baseline: 40.5%)")
    audit = jar.audit_daily_digest(digest, use_llm=False)
    assert [a for a in audit["next_system_actions"]
            if a["area"] == "scanner_recall"]


def test_parse_ticker_list_strips_tier_annotations():
    """Digest name lists may carry '(QUALITY_TIER)' suffixes — the parser
    must keep the ticker instead of dropping the token."""
    raw = ("FRMM (UNPROFITABLE_FUNDED), ADP, HOOD, "
           "BAX (UNPROFITABLE_FUNDED) (+2 more)")
    assert jar._parse_ticker_list(raw) == ["FRMM", "ADP", "HOOD", "BAX"]


class _FakeLLMClient:
    """Provider-shaped stub matching core.llm_clients.LLMClient surface."""

    provider_name = "deepseek"

    def __init__(self, text: str, stop_reason: str = "end"):
        self._text = text
        self._stop_reason = stop_reason

    def is_configured(self):
        return True

    def model_for_role(self, role):
        return "deepseek-reasoner"

    def complete(self, prompt, **kwargs):
        from core.llm_clients import LLMResponse
        return LLMResponse(text=self._text, model="deepseek-reasoner",
                           provider=self.provider_name,
                           stop_reason=self._stop_reason)


def test_truncated_llm_response_raises_and_saves_raw(monkeypatch, tmp_path):
    """A max_tokens-truncated reply must fail loudly (clear reason, raw
    reply preserved) instead of dying inside json.loads."""
    monkeypatch.setattr(jar, "REPO_ROOT", tmp_path)

    truncated = '{"research_verdict": "RESEARCH_ONLY", "what_is_wor'
    monkeypatch.setattr(
        jar, "_get_llm_client",
        lambda: _FakeLLMClient(truncated, stop_reason="max_tokens"))

    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="truncated at max_tokens"):
        jar._llm_audit("digest text")
    raw = tmp_path / "logs" / "journal_audit_llm_raw_error.txt"
    assert raw.exists()
    assert "stop_reason=max_tokens" in raw.read_text()
    assert truncated in raw.read_text()


def test_unparseable_llm_response_saves_raw(monkeypatch, tmp_path):
    monkeypatch.setattr(jar, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        jar, "_get_llm_client",
        lambda: _FakeLLMClient("not json at all"))

    import pytest as _pytest
    with _pytest.raises(Exception):
        jar._llm_audit("digest text")
    raw = tmp_path / "logs" / "journal_audit_llm_raw_error.txt"
    assert raw.exists()
    assert "not json at all" in raw.read_text()


def test_llm_audit_records_provider_and_safety_fields(monkeypatch, tmp_path):
    """A successful provider reply must stamp model+provider, and the
    final artifact must carry the forced research-only safety contract
    even when the LLM tries to claim otherwise."""
    monkeypatch.setattr(jar, "REPO_ROOT", tmp_path)
    reply = ('{"research_verdict": "RESEARCH_ONLY", '
             '"promote_to_signal": true, "may_change_scores": true}')
    monkeypatch.setattr(
        jar, "_get_llm_client", lambda: _FakeLLMClient(reply))

    audit = jar._llm_audit("digest text")
    assert audit["model"] == "deepseek-reasoner"
    assert audit["llm_provider"] == "deepseek"

    monkeypatch.setattr(jar, "_llm_audit", lambda text: dict(audit))
    final = jar.audit_daily_digest("digest text")
    assert final["research_only"] is True
    assert final["promote_to_signal"] is False
    assert final["may_change_scores"] is False
    assert final["may_change_rankings"] is False
    assert final["may_change_gates"] is False
    assert final["may_change_verdicts"] is False
    assert final["llm_provider"] == "deepseek"


def test_llm_failure_fails_open_by_default(monkeypatch):
    """LLM_FAIL_OPEN default: an LLM crash degrades to the fallback audit
    so the nightly keeps running."""
    def boom(text):
        raise RuntimeError("provider down")
    monkeypatch.setattr(jar, "_llm_audit", boom)
    audit = jar.audit_daily_digest(make_digest())
    assert audit["audit_source"] == "rule_based_fallback"
    assert "provider down" in (audit["fallback_reason"] or "")


def test_llm_failure_propagates_when_fail_open_disabled(monkeypatch):
    """LLM_FAIL_OPEN=false is a debugging mode: the failure must surface
    instead of silently falling back."""
    monkeypatch.setenv("LLM_FAIL_OPEN", "false")

    def boom(text):
        raise RuntimeError("provider down")
    monkeypatch.setattr(jar, "_llm_audit", boom)
    with pytest.raises(RuntimeError, match="provider down"):
        jar.audit_daily_digest(make_digest())


# ── 2026-09-11: addressed P0 tasks must not re-queue every night ─────────────
#
# 8d01d462a3bb (forward_evidence) re-queued on 50 nights and 2e43d991d887
# (scanner_recall) on 35, both after being ADDRESSED on 2026-07-10.  The
# forward task keyed on the MIXED / Phase-4B-BLOCKED verdict (a maturity
# state); the recall task keyed on any LLM recall flaw once its digest
# declaration line was dropped.

from dashboards.research_command_center import journal_digest as jd  # noqa: E402

LIVE_RECALL_LINE = (
    "- Warning: Live research-board recall accruing via scanner-recall "
    "cohorts: LOOSE_NOT_BETTER (25/15 matured dates); scanner-watchlist "
    "recall 15.0% vs RS-baseline 5.4% at 20d")

FORWARD_TASK_PREFIX = "Improve forward-evidence tracking"
RECALL_TASK_PREFIX = "Build scanner recall diagnostics"


def _forward_inputs(drop_field: str = "") -> dict:
    row = {"n": 10, "mean_ret_pct": 1.0, "median_ret_pct": 0.5,
           "hit_rate_vs_spy": 0.5, "mean_mae_pct": -3.0}
    row.pop(drop_field, None)
    return {"forward": {"priority_split": {
        g: {"horizons": {h: dict(row) for h in ("5d", "10d", "20d")}}
        for g in ("high_priority", "watch_only")}}}


def _declared_digest(**kwargs) -> str:
    """A digest in today's live shape: MIXED + Phase 4B BLOCKED, the live
    recall-cohorts line, and the forward-stats declaration."""
    declaration = jd._forward_stats_declaration(_forward_inputs())
    return make_digest(verdict="MIXED", recall_line=LIVE_RECALL_LINE,
                       **kwargs) + declaration + "\n"


def _llm_recall_flaw(severity: str):
    return lambda text: {
        "research_verdict": "RESEARCH_ONLY",
        "alpha_discovery_quality": "MIXED",
        "engine_health": "OPERATIONAL_WITH_BLOCKERS",
        "promote_to_signal": False, "one_line_summary": "s",
        "what_is_working": [], "recommended_claude_code_tasks": [],
        "flaws_detected": [{
            "severity": severity, "area": "scanner_recall",
            "issue": "recall context", "why_it_matters": "w",
            "suggested_fix": "f"}],
        "next_system_actions": [], "audit_source": "llm", "model": "test",
    }


def _task_texts(audit) -> list:
    return [a["task"] for a in audit["next_system_actions"]]


def test_digest_forward_declaration_is_what_the_audit_reads():
    line = jd._forward_stats_declaration(_forward_inputs())
    assert line and "5d/10d/20d" in line
    assert jar.extract_digest_signals(line)["forward_stats_referenced"]
    # stats missing from the sidecar -> no declaration -> task can fire
    assert jd._forward_stats_declaration(
        _forward_inputs(drop_field="mean_mae_pct")) is None
    assert jd._forward_stats_declaration({"forward": {}}) is None


def test_forward_task_not_requeued_when_stats_exist_and_evidence_immature():
    audit = jar.audit_daily_digest(_declared_digest(), use_llm=False)
    assert not [t for t in _task_texts(audit)
                if t.startswith(FORWARD_TASK_PREFIX)]
    wait = [w for w in audit["wait_for_maturity"]
            if w["area"] == "forward_evidence"]
    assert wait and wait[0]["status"] == "WAIT_FOR_MATURITY"
    assert wait[0]["queued"] is False
    assert "MIXED" in wait[0]["condition"]
    # the blocker itself stays visible
    assert audit["research_verdict"] == "RESEARCH_ONLY"


def test_forward_task_still_fires_when_stats_are_not_declared():
    audit = jar.audit_daily_digest(
        make_digest(verdict="MIXED", recall_line=LIVE_RECALL_LINE),
        use_llm=False)
    assert [t for t in _task_texts(audit)
            if t.startswith(FORWARD_TASK_PREFIX)]


def test_live_recall_cohorts_line_declares_the_recall_deliverable(monkeypatch):
    monkeypatch.setattr(jar, "_llm_audit", _llm_recall_flaw("HIGH"))
    audit = jar.audit_daily_digest(_declared_digest())
    assert not [t for t in _task_texts(audit)
                if t.startswith(RECALL_TASK_PREFIX)]


def test_low_llm_recall_flaw_alone_does_not_raise_a_p0(monkeypatch):
    monkeypatch.setattr(jar, "_llm_audit", _llm_recall_flaw("LOW"))
    audit = jar.audit_daily_digest(make_digest(verdict="MIXED"))
    assert not [t for t in _task_texts(audit)
                if t.startswith(RECALL_TASK_PREFIX)]
    # a serious recall flaw without any declaration still raises it
    monkeypatch.setattr(jar, "_llm_audit", _llm_recall_flaw("HIGH"))
    audit = jar.audit_daily_digest(make_digest(verdict="MIXED"))
    assert [t for t in _task_texts(audit)
            if t.startswith(RECALL_TASK_PREFIX)]


def _queued_p0(root: Path) -> list:
    path = root / jar.FEEDBACK_QUEUE_REL
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines()
            if l.strip() and json.loads(l).get("priority") == "P0"]


def test_addressed_tasks_do_not_reopen_on_consecutive_nights(monkeypatch,
                                                            tmp_path):
    monkeypatch.setattr(jar, "_llm_audit", _llm_recall_flaw("LOW"))
    for night in ("2026-09-12", "2026-09-13"):
        jar.run_audit(_declared_digest() + f"- Night: {night}\n",
                      root=tmp_path)
    assert _queued_p0(tmp_path) == []

    # control: the same nights without the declarations re-queue the P0
    # forward task every night — the loop this fix closes
    control = tmp_path / "control"
    for night in ("2026-09-12", "2026-09-13"):
        jar.run_audit(make_digest(verdict="MIXED") + f"- Night: {night}\n",
                      root=control)
    forward = [e for e in _queued_p0(control)
               if e["task"].startswith(FORWARD_TASK_PREFIX)]
    assert len(forward) == 2


def test_llm_provider_doc_names_the_role_the_audit_uses(monkeypatch,
                                                        tmp_path):
    captured = {}

    class _Capture(_FakeLLMClient):
        def complete(self, prompt, **kwargs):
            captured.update(kwargs)
            return super().complete(prompt, **kwargs)

    monkeypatch.setattr(jar, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(jar, "_get_llm_client", lambda: _Capture(
        '{"research_verdict": "RESEARCH_ONLY"}'))
    jar._llm_audit("digest text")
    assert captured["role"] == jar.LLM_ROLE

    doc = (Path(jar.__file__).resolve().parents[1]
           / "docs" / "ops" / "LLM_PROVIDER.md").read_text(encoding="utf-8")
    lines = doc.splitlines()
    inventory = next(l for l in lines
                     if l.startswith("| `research/journal_audit_reviewer.py`"))
    override = next(l for l in lines
                    if l.startswith("- `JOURNAL_AUDIT_LLM_MODEL`"))
    assert f"(role: {jar.LLM_ROLE})" in inventory
    assert f"(role: {jar.LLM_ROLE})" in override


def test_loop_closing_text_has_no_trade_or_rank_language():
    lang = re.compile(r"\bbuy\b|\bsell\b|top pick|price target|"
                      r"\brank(s|ed|ing)?\b|position size|"
                      r"trade recommendation", re.IGNORECASE)
    audit = jar.audit_daily_digest(_declared_digest(), use_llm=False)
    for text in (json.dumps(audit["wait_for_maturity"]),
                 jd._forward_stats_declaration(_forward_inputs()),
                 jd.FORWARD_STATS_DECLARATION):
        assert not lang.search(text), text
