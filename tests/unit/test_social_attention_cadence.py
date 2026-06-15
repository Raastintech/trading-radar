"""Phase 1G.15B tests — Reddit-free cadence, safe-nightly profile, watch
universe cap, and source-health classification for the Social Attention Radar.

Pure-Python, no live network. StockTwits is exercised with a mocked HTTP layer;
Google Trends 429 is simulated by stubbing the fetcher. Reddit is verified to be
DISABLED / NOT_CONFIGURED and to NEVER fail the run.
"""
from __future__ import annotations

import argparse
import json
import types

import pytest

from research import social_attention_radar as sar


# ── Reddit never blocks the run (Task 6) ────────────────────────────────────────
def test_reddit_disabled_does_not_fail():
    stats = sar._new_stats()
    rows = sar.collect_reddit(stats, enabled=False)
    assert rows == []
    assert "disabled" in stats["source_status"]["reddit"].lower()


def test_reddit_enabled_without_creds_is_not_configured(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    stats = sar._new_stats()
    rows = sar.collect_reddit(stats, enabled=True)  # must not raise
    assert rows == []
    assert "no reddit_client" in stats["source_status"]["reddit"].lower()


def test_safe_nightly_profile_keeps_reddit_off():
    args = argparse.Namespace(profile="safe-nightly")
    out = sar.apply_profile(args)
    assert out.enable_stocktwits is True
    assert out.enable_reddit is False
    assert out.use_watch_universe is True
    assert out.google_trends_cap == 12  # low-cap
    # dry-run profile = safe-nightly + no writes
    args2 = sar.apply_profile(argparse.Namespace(profile="dry-run"))
    assert args2.dry_run is True and args2.enable_reddit is False


# ── StockTwits opt-in works with a mocked response (Task 6) ──────────────────────
def test_stocktwits_mocked_response(monkeypatch):
    import requests

    class _Resp:
        status_code = 200

        def json(self):
            return {"messages": [
                {"id": 1, "body": "$NVDA looks strong", "created_at": "2026-06-09T15:00:00Z",
                 "user": {"username": "someuser"}, "likes": {"total": 7},
                 "entities": {"sentiment": {"basic": "Bullish"}}},
            ]}

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    stats = sar._new_stats()
    rows = sar.collect_stocktwits(stats, ["NVDA"], enabled=True, limit=1)
    assert len(rows) == 1
    item = sar.normalize_items(rows)[0]
    assert item.ticker_candidates == ["NVDA"]
    # author hashed, not raw
    assert item.author_hash and "someuser" not in (item.author_hash or "")


def test_stocktwits_disabled_returns_empty():
    stats = sar._new_stats()
    assert sar.collect_stocktwits(stats, ["NVDA"], enabled=False) == []
    assert "disabled" in stats["source_status"]["stocktwits"].lower()


def test_stocktwits_http_error_degrades(monkeypatch):
    import requests

    def _boom(*a, **k):
        raise requests.exceptions.Timeout("simulated")

    monkeypatch.setattr(requests, "get", _boom)
    stats = sar._new_stats()
    rows = sar.collect_stocktwits(stats, ["NVDA", "AMD"], enabled=True, limit=2)
    assert rows == []  # graceful, no raise
    assert stats["source_errors"]  # error recorded


# ── Google Trends 429 degrades gracefully (Task 6) ───────────────────────────────
def test_google_trends_429_degrades(monkeypatch):
    import research.social_arb_radar as srr

    def _fake_fetch(symbols, stats, **kw):
        stats["source_errors"].append(
            "google_trends: rate-limited (429); stopping further chunks for this run")
        stats["source_status"]["google_trends"] = "0 spike(s)"
        return []

    monkeypatch.setattr(srr, "_fetch_google_trends", _fake_fetch)
    stats = sar._new_stats()
    rows = sar.collect_google_trends(stats, z_threshold=1.5, limit=5)
    assert rows == []  # no raise
    health = sar.compute_source_health(stats, rows, enabled={"google_trends": True})
    assert health["google_trends"]["health"] == sar.HEALTH_RATE_LIMITED


# ── manual JSONL parses safely (Task 6) ──────────────────────────────────────────
def test_manual_feed_parses_safely(tmp_path):
    feed = tmp_path / "manual.jsonl"
    feed.write_text(
        '{"ticker":"RKLB","text":"chatter rising","source_type":"manual","confidence":0.6}\n'
        "\n"  # blank line skipped
        "{not valid json}\n"  # malformed skipped
        '{"theme":"space","text":"launch buzz","source_type":"manual"}\n'
    )
    stats = sar._new_stats()
    rows = sar.collect_manual_feed(stats, path=feed)
    assert len(rows) == 2
    items = sar.normalize_items(rows)
    assert items[0].ticker_candidates == ["RKLB"]
    # theme-only row maps via sympathy or theme_only, never crashes
    assert items[1].theme_candidates


def test_manual_feed_missing_file_is_safe(tmp_path):
    stats = sar._new_stats()
    rows = sar.collect_manual_feed(stats, path=tmp_path / "nope.jsonl")
    assert rows == []


# ── watch universe capped (Task 3) ───────────────────────────────────────────────
def test_watch_universe_respects_cap():
    w = sar.build_watch_universe(cap=10)
    assert w["size"] <= 10
    assert len(w["universe"]) == w["size"]
    # mega-cap floor guarantees it is never empty
    assert w["size"] >= 1
    assert len(set(w["universe"])) == len(w["universe"])  # deduped


def test_watch_universe_hard_max_enforced():
    w = sar.build_watch_universe(cap=10_000)
    assert w["cap"] == sar.WATCH_CAP_HARD_MAX
    assert w["size"] <= sar.WATCH_CAP_HARD_MAX


# ── source health computed correctly (Task 6) ────────────────────────────────────
def test_source_health_labels(monkeypatch, tmp_path):
    # point manual feed at a missing path → NOT_CONFIGURED
    monkeypatch.setattr(sar, "MANUAL_FEED", tmp_path / "absent.jsonl")
    raw = [
        {"source_type": "stocktwits"}, {"source_type": "stocktwits"},
        {"source_type": "google_trends"},
    ]
    stats = sar._new_stats()
    stats["source_status"]["google_trends"] = "1 spike(s)"
    health = sar.compute_source_health(
        stats, raw,
        enabled={"manual": True, "stocktwits": True, "google_trends": True, "reddit": False})
    assert health["stocktwits"]["health"] == sar.HEALTH_HEALTHY
    assert health["stocktwits"]["items"] == 2
    assert health["google_trends"]["health"] == sar.HEALTH_HEALTHY
    assert health["manual"]["health"] == sar.HEALTH_NOT_CONFIGURED
    assert health["reddit"]["health"] == sar.HEALTH_DISABLED


def test_source_health_no_data_and_disabled():
    stats = sar._new_stats()
    health = sar.compute_source_health(
        stats, raw=[],
        enabled={"manual": True, "stocktwits": False, "google_trends": False, "reddit": False})
    assert health["stocktwits"]["health"] == sar.HEALTH_DISABLED
    assert health["google_trends"]["health"] == sar.HEALTH_DISABLED
    assert health["reddit"]["health"] == sar.HEALTH_DISABLED


# ── safe-nightly build is offline-safe and labels stay research-only ─────────────
def test_safe_nightly_offline_sample_build():
    # offline_sample short-circuits all providers even under safe-nightly.
    args = sar.apply_profile(argparse.Namespace(
        profile="safe-nightly", offline_sample=True, dry_run=True,
        watch_cap=75, google_trends_cap=12, stocktwits_cap=100,
        google_trends_z=1.5))
    res = sar.build(args)
    assert res["profile"] == "safe-nightly"
    assert "source_health" in res
    # offline fixtures don't build a watch universe (no provider scan)
    assert res["watch_universe"] is None
    for lead in res["leads"]:
        assert "BUY" not in lead["label"] and "EXECUTE" not in lead["label"]


# ── cadence plan + reliability artifacts are well-formed, cache-only ─────────────
def test_cadence_plan_json_valid():
    data = json.loads(sar.CADENCE_JSON.read_text())
    assert data["auto_enabled"] is False
    assert data["approval_required"] is True
    assert "social-attention" in data["manual_run_command"]


def test_source_audit_artifact_shape():
    res = {
        "generated_at": "x", "profile": "safe-nightly",
        "source_health": {"stocktwits": {"health": "HEALTHY", "items": 5}},
        "source_errors": [], "api_attempts": {}, "watch_universe": {"size": 75},
    }
    art = sar._source_audit_artifact(res)
    assert art["kind"] == "social_attention_source_audit"
    assert "DISABLED" in art["reddit_policy"]


def test_no_forbidden_imports_still_holds():
    import inspect
    src = inspect.getsource(sar)
    for bad in ("order_manager", "paper_governance", "submit_order",
                "decision_logger", "veto_council", "ALLOW_LIVE_CAPITAL"):
        assert bad not in src
