"""core/llm_clients — DeepSeek-default provider layer.

Covers:
  1. factory routing (deepseek default, enable flags, anthropic opt-in)
  2. DeepSeek request/response handling (mocked transport — nothing on wire)
  3. model routing (chat vs reasoner roles, explicit override)
  4. error mapping (timeout, HTTP error, malformed payload)
  5. key hygiene (key never appears in error messages or status)
  6. forced research-only safety fields
"""
from __future__ import annotations

import json

import pytest

from core.llm_clients import (
    LLMError,
    apply_safety_fields,
    get_llm_client,
    get_llm_status,
    research_safety_fields,
)
from core.llm_clients.deepseek_client import DeepSeekClient

FAKE_KEY = "sk-test-never-log-me-123"


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch, tmp_path):
    for var in ("LLM_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_ENABLED",
                "DEEPSEEK_MODEL", "DEEPSEEK_CHAT_MODEL",
                "DEEPSEEK_REASONER_MODEL", "DEEPSEEK_TEMPERATURE",
                "DEEPSEEK_BASE_URL", "DEEPSEEK_MAX_TOKENS",
                "DEEPSEEK_TIMEOUT_SECONDS", "ANTHROPIC_API_KEY",
                "ANTHROPIC_ENABLED", "LLM_DAILY_MAX_CALLS",
                "LLM_DAILY_MAX_TOKENS", "LLM_FAIL_OPEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEM_TRADER_SKIP_DOTENV", "true")
    # Usage ledger goes to tmp — tests must never write the repo log.
    monkeypatch.setenv("LLM_USAGE_LOG_PATH", str(tmp_path / "llm_usage.jsonl"))


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def _chat_payload(text="hello", finish="stop", model="deepseek-chat",
                  reasoning=None, prompt_tokens=12, completion_tokens=5):
    message = {"content": text}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "model": model,
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt_tokens,
                  "completion_tokens": completion_tokens},
    }


# ── 1. factory routing ───────────────────────────────────────────────────────


def test_default_provider_is_deepseek_and_unconfigured_returns_none():
    status = get_llm_status()
    assert status["provider"] == "deepseek"
    assert status["enabled"] is True
    assert status["configured"] is False
    assert get_llm_client() is None


def test_deepseek_configured_returns_client(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    client = get_llm_client()
    assert client is not None
    assert client.provider_name == "deepseek"
    assert client.model_for_role("chat") == "deepseek-chat"
    assert client.model_for_role("reasoner") == "deepseek-reasoner"


def test_deepseek_disabled_flag_returns_none(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DEEPSEEK_ENABLED", "false")
    assert get_llm_client() is None


def test_anthropic_not_used_without_explicit_enable(monkeypatch):
    """ANTHROPIC_API_KEY alone must never re-activate Anthropic."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stale")
    assert get_llm_status()["provider"] == "deepseek"
    assert get_llm_client() is None  # deepseek key absent, anthropic ignored

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert get_llm_client() is None  # ANTHROPIC_ENABLED still false


def test_anthropic_fallback_requires_both_flags(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stub")
    client = get_llm_client()
    assert client is not None
    assert client.provider_name == "anthropic"


def test_unknown_or_disabled_provider_returns_none(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    assert get_llm_client() is None


def test_model_env_overrides_role_routing(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat-v2")
    monkeypatch.setenv("DEEPSEEK_REASONER_MODEL", "deepseek-reasoner-v2")
    client = get_llm_client()
    assert client.model_for_role("chat") == "deepseek-chat-v2"
    assert client.model_for_role("reasoner") == "deepseek-reasoner-v2"


# ── 2/3. DeepSeek request + response handling ────────────────────────────────


def _capture_post(monkeypatch, response):
    calls = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["body"] = json
        calls["headers"] = headers
        calls["timeout"] = timeout
        return response

    monkeypatch.setattr("requests.post", fake_post)
    return calls


def test_complete_parses_text_and_maps_finish_reason(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    calls = _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    resp = DeepSeekClient().complete("hi", system="sys", temperature=0)
    assert resp.text == "hello"
    assert resp.provider == "deepseek"
    assert resp.stop_reason == "end"
    assert calls["url"] == "https://api.deepseek.com/chat/completions"
    assert calls["body"]["model"] == "deepseek-chat"
    assert calls["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert calls["body"]["temperature"] == 0


def test_complete_reasoner_role_routes_model_and_omits_temperature(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    payload = _chat_payload(model="deepseek-reasoner", reasoning="thinking…")
    calls = _capture_post(monkeypatch, _FakeResponse(payload=payload))
    resp = DeepSeekClient().complete("hi", role="reasoner", temperature=0)
    assert calls["body"]["model"] == "deepseek-reasoner"
    assert "temperature" not in calls["body"]
    assert resp.reasoning_text == "thinking…"


def test_complete_truncation_maps_to_max_tokens(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    _capture_post(monkeypatch,
                  _FakeResponse(payload=_chat_payload(finish="length")))
    resp = DeepSeekClient().complete("hi")
    assert resp.stop_reason == "max_tokens"


def test_complete_clamps_max_tokens_to_provider_ceiling(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    calls = _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    DeepSeekClient().complete("hi", max_tokens=100_000)
    assert calls["body"]["max_tokens"] == 65536


def test_complete_explicit_model_override_wins(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    calls = _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    DeepSeekClient().complete("hi", model="custom-model")
    assert calls["body"]["model"] == "custom-model"


def test_base_url_env_override(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://proxy.example.com/")
    calls = _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    DeepSeekClient().complete("hi")
    assert calls["url"] == "https://proxy.example.com/chat/completions"


# ── 4. error mapping ─────────────────────────────────────────────────────────


def test_missing_key_raises_llm_error():
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY not set"):
        DeepSeekClient().complete("hi")


def test_http_error_raises_llm_error_without_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    _capture_post(monkeypatch, _FakeResponse(
        status_code=401,
        payload={"error": {"message": "invalid auth"}}))
    with pytest.raises(LLMError) as exc_info:
        DeepSeekClient().complete("hi")
    assert "401" in str(exc_info.value)
    assert FAKE_KEY not in str(exc_info.value)


def test_timeout_raises_llm_error_without_key(monkeypatch):
    import requests as _requests
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)

    def raise_timeout(*args, **kwargs):
        raise _requests.exceptions.Timeout("boom")

    monkeypatch.setattr("requests.post", raise_timeout)
    with pytest.raises(LLMError, match="timeout"):
        DeepSeekClient().complete("hi")


def test_malformed_payload_raises_llm_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    _capture_post(monkeypatch, _FakeResponse(payload={"choices": []}))
    with pytest.raises(LLMError, match="malformed"):
        DeepSeekClient().complete("hi")


# ── 5. key hygiene ───────────────────────────────────────────────────────────


def test_status_snapshot_never_contains_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    assert FAKE_KEY not in json.dumps(get_llm_status())


def test_key_resolution_prefers_credential_file(monkeypatch, tmp_path):
    """trading.env (SNIPER_ENV_PATH) must shadow a stale shell export."""
    env_file = tmp_path / "trading.env"
    env_file.write_text("DEEPSEEK_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.delenv("GEM_TRADER_SKIP_DOTENV", raising=False)
    monkeypatch.setenv("SNIPER_ENV_PATH", str(env_file))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "stale-shell-key")
    from core.llm_clients import resolve_secret
    assert resolve_secret("DEEPSEEK_API_KEY") == "file-key"


# ── 6. forced safety fields ──────────────────────────────────────────────────


def test_safety_fields_contract():
    fields = research_safety_fields()
    assert fields == {
        "research_only": True,
        "promote_to_signal": False,
        "may_change_scores": False,
        "may_change_rankings": False,
        "may_change_gates": False,
        "may_change_verdicts": False,
    }


def test_apply_safety_fields_overrides_lying_llm_output():
    lying = {"promote_to_signal": True, "may_change_gates": True,
             "verdict": "KEEP"}
    out = apply_safety_fields(lying)
    assert out["promote_to_signal"] is False
    assert out["may_change_gates"] is False
    assert out["research_only"] is True
    assert out["verdict"] == "KEEP"  # non-safety keys untouched


# ── 7. model-name env chain (no hard-coded models in callers) ────────────────


def test_chat_model_falls_back_to_legacy_deepseek_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "legacy-model")
    client = DeepSeekClient()
    assert client.model_for_role("chat") == "legacy-model"


def test_chat_model_env_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "legacy-model")
    monkeypatch.setenv("DEEPSEEK_CHAT_MODEL", "deepseek-v4-flash")
    client = DeepSeekClient()
    assert client.model_for_role("chat") == "deepseek-v4-flash"


def test_reasoner_falls_back_to_chat_chain(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_CHAT_MODEL", "deepseek-v4-flash")
    client = DeepSeekClient()
    assert client.model_for_role("reasoner") == "deepseek-v4-flash"
    monkeypatch.setenv("DEEPSEEK_REASONER_MODEL", "deepseek-v4-pro")
    assert client.model_for_role("reasoner") == "deepseek-v4-pro"


def test_provider_defaults_when_no_model_env():
    client = DeepSeekClient()
    assert client.model_for_role("chat") == "deepseek-chat"
    assert client.model_for_role("reasoner") == "deepseek-reasoner"


# ── 8. temperature env default ───────────────────────────────────────────────


def test_temperature_env_default_applied_to_chat(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DEEPSEEK_TEMPERATURE", "0.1")
    calls = _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    DeepSeekClient().complete("hi")
    assert calls["body"]["temperature"] == 0.1


def test_temperature_caller_value_wins_over_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DEEPSEEK_TEMPERATURE", "0.9")
    calls = _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    DeepSeekClient().complete("hi", temperature=0)
    assert calls["body"]["temperature"] == 0


def test_temperature_env_never_sent_to_reasoner(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DEEPSEEK_TEMPERATURE", "0.1")
    calls = _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    DeepSeekClient().complete("hi", role="reasoner")
    assert "temperature" not in calls["body"]


# ── 9. budget controls + usage ledger ────────────────────────────────────────


def _read_usage_log():
    import os as _os
    path = _os.environ["LLM_USAGE_LOG_PATH"]
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_usage_logged_on_success_without_secrets(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    resp = DeepSeekClient().complete("hi", artifact="test_artifact.json")
    assert resp.prompt_tokens == 12 and resp.completion_tokens == 5
    assert resp.latency_ms is not None
    rows = _read_usage_log()
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "deepseek"
    assert row["status"] == "ok"
    assert row["prompt_tokens"] == 12
    assert row["completion_tokens"] == 5
    assert row["artifact"] == "test_artifact.json"
    assert "latency_ms" in row
    assert FAKE_KEY not in json.dumps(rows)


def test_usage_logged_on_error_with_error_class(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    _capture_post(monkeypatch, _FakeResponse(
        status_code=500, payload={"error": {"message": "server"}}))
    with pytest.raises(LLMError):
        DeepSeekClient().complete("hi")
    rows = _read_usage_log()
    assert rows[-1]["status"] == "error"
    assert rows[-1]["error_class"] == "HTTP500"


def test_daily_call_cap_blocks_and_logs(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LLM_DAILY_MAX_CALLS", "1")
    _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    client = DeepSeekClient()
    client.complete("hi")  # first call fits the budget
    with pytest.raises(LLMError, match="daily call cap"):
        client.complete("hi again")
    rows = _read_usage_log()
    assert [r["status"] for r in rows] == ["ok", "budget_blocked"]


def test_daily_token_cap_blocks(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LLM_DAILY_MAX_TOKENS", "10")  # 12+5 spent by call 1
    _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    client = DeepSeekClient()
    client.complete("hi")
    with pytest.raises(LLMError, match="daily token cap"):
        client.complete("hi again")


def test_caps_can_be_disabled_with_nonpositive_values(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    monkeypatch.setenv("LLM_DAILY_MAX_CALLS", "0")
    monkeypatch.setenv("LLM_DAILY_MAX_TOKENS", "-1")
    _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    client = DeepSeekClient()
    for _ in range(3):
        client.complete("hi")
    assert len(_read_usage_log()) == 3


def test_status_snapshot_includes_usage_and_fail_open(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload()))
    DeepSeekClient().complete("hi")
    status = get_llm_status()
    assert status["fail_open"] is True
    assert status["usage_today"]["calls_today"] == 1
    assert status["usage_today"]["tokens_today"] == 17
    assert FAKE_KEY not in json.dumps(status)


# ── 10. complete_json ────────────────────────────────────────────────────────


def test_complete_json_parses_fenced_json_and_stamps_safety(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    fenced = '```json\n{"reviews": [{"id": "a", "promote_to_signal": true}]}\n```'
    _capture_post(monkeypatch, _FakeResponse(payload=_chat_payload(text=fenced)))
    out = DeepSeekClient().complete_json(
        system_prompt="sys", user_prompt="user",
        schema_name="social_arb_reviews", max_tokens=1000)
    assert out["reviews"][0]["id"] == "a"
    assert out["promote_to_signal"] is False  # forced at top level
    assert out["research_only"] is True
    assert out["_llm"]["provider"] == "deepseek"
    assert out["_llm"]["prompt_tokens"] == 12


def test_complete_json_invalid_json_raises_with_truncated_raw(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    _capture_post(monkeypatch, _FakeResponse(
        payload=_chat_payload(text="not json " * 500)))
    with pytest.raises(LLMError, match="invalid JSON for schema 'memo'") as ei:
        DeepSeekClient().complete_json(
            system_prompt="s", user_prompt="u",
            schema_name="memo", max_tokens=100)
    assert ei.value.schema_name == "memo"
    assert len(ei.value.raw_excerpt) <= 2000


def test_complete_json_truncation_raises_with_raw(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_KEY)
    _capture_post(monkeypatch, _FakeResponse(
        payload=_chat_payload(text='{"partial":', finish="length")))
    with pytest.raises(LLMError, match="truncated at max_tokens"):
        DeepSeekClient().complete_json(
            system_prompt="s", user_prompt="u",
            schema_name="memo", max_tokens=100)
