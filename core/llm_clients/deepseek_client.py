"""DeepSeek client — the default lower-cost LLM provider (OpenAI-compatible
chat API), subject to provider rate limits, account capacity, API
availability, and the configured budget controls in
``core/llm_clients/usage.py``.

Configuration (all env, resolved lazily; trading.env wins over shell):
  DEEPSEEK_API_KEY          required for calls (never logged)
  DEEPSEEK_BASE_URL         default https://api.deepseek.com
  DEEPSEEK_CHAT_MODEL       role "chat" model (falls back to DEEPSEEK_MODEL,
                            then the provider default)
  DEEPSEEK_MODEL            legacy/back-compat default chat model
  DEEPSEEK_REASONER_MODEL   role "reasoner" model (falls back to the chat
                            chain, then the provider default)
  DEEPSEEK_TEMPERATURE      default sampling temperature for chat-role calls
                            when the caller passes none (reasoner ignores it)
  DEEPSEEK_TIMEOUT_SECONDS  default 60
  DEEPSEEK_MAX_TOKENS       default 4000 (callers may pass an explicit cap)
  DEEPSEEK_ENABLED          default true

Model names are configured here and ONLY here — callers request roles
("chat" / "reasoner"), so a provider-side model rename is an env edit,
never a code edit.  The in-code defaults below are the provider's stable
compatibility aliases, kept in this single module as the last-resort
fallback when no model env var is set.

Cred-free import; ``requests`` is imported inside the call path only, so
modules with forbidden-import guards never pull it in transitively at
collection time unless they actually invoke the LLM.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.llm_clients.base import (
    LLMClient,
    LLMError,
    LLMResponse,
    ROLE_REASONER,
    resolve_secret,
)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_CHAT_MODEL = "deepseek-chat"
DEFAULT_REASONER_MODEL = "deepseek-reasoner"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_TOKENS = 4000
# App-level output-token guardrail (NOT the provider's hard ceiling —
# requests above it are clamped locally, not errored by DeepSeek). The old
# 8192 value here was copied from the legacy deepseek-reasoner alias and
# was silently truncating callers that request more (e.g. the journal
# auditor's LLM_MAX_TOKENS=8000 consistently hit this wall, and
# social_arb_radar's max_tokens=9000 was silently cut to 8192). Per current
# DeepSeek API docs (api-docs.deepseek.com/quick_start/pricing, checked
# 2026-07-21), the configured reasoner model deepseek-v4-pro supports up
# to 384K output tokens / 1M context — 65536 here is a generous
# in-repo ceiling well below that, not a provider limit we're bumping
# into again.
PROVIDER_MAX_OUTPUT_TOKENS = 65536

_FINISH_REASON_MAP = {
    "stop": "end",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


class DeepSeekClient(LLMClient):
    provider_name = "deepseek"

    def is_configured(self) -> bool:
        return bool(resolve_secret("DEEPSEEK_API_KEY"))

    def model_for_role(self, role: str) -> str:
        # Chat chain: DEEPSEEK_CHAT_MODEL > DEEPSEEK_MODEL (legacy) > default.
        # Reasoner chain: DEEPSEEK_REASONER_MODEL > chat chain > default.
        chat = (os.getenv("DEEPSEEK_CHAT_MODEL", "").strip()
                or os.getenv("DEEPSEEK_MODEL", "").strip())
        if role == ROLE_REASONER:
            return (os.getenv("DEEPSEEK_REASONER_MODEL", "").strip()
                    or chat or DEFAULT_REASONER_MODEL)
        return chat or DEFAULT_CHAT_MODEL

    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        role: str = "chat",
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        artifact: Optional[str] = None,
    ) -> LLMResponse:
        from core.llm_clients.usage import check_daily_budget, record_usage

        api_key = resolve_secret("DEEPSEEK_API_KEY")
        if not api_key:
            raise LLMError("DEEPSEEK_API_KEY not set")

        resolved_model = (model or "").strip() or self.model_for_role(role)

        budget_reason = check_daily_budget()
        if budget_reason:
            record_usage(provider=self.provider_name, model=resolved_model,
                         status="budget_blocked", artifact=artifact)
            raise LLMError(budget_reason)

        budget = max_tokens if max_tokens else _env_int("DEEPSEEK_MAX_TOKENS", DEFAULT_MAX_TOKENS)
        budget = max(1, min(int(budget), PROVIDER_MAX_OUTPUT_TOKENS))
        wait = timeout if timeout else _env_float("DEEPSEEK_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        # deepseek-reasoner ignores sampling params; only send temperature
        # for the chat family where it is meaningful.  Caller value wins;
        # DEEPSEEK_TEMPERATURE is the env default.
        if temperature is None and role != ROLE_REASONER:
            raw_t = os.getenv("DEEPSEEK_TEMPERATURE", "").strip()
            if raw_t:
                try:
                    temperature = float(raw_t)
                except ValueError:
                    temperature = None

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: Dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "max_tokens": budget,
            "stream": False,
        }
        if temperature is not None and role != ROLE_REASONER:
            body["temperature"] = temperature

        base_url = (os.getenv("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/")
        url = f"{base_url}/chat/completions"

        import requests  # lazy — keeps module import cred/network-free
        import time as _time

        def _fail(exc_cls_name: str, err: LLMError) -> LLMError:
            record_usage(provider=self.provider_name, model=resolved_model,
                         status="error", latency_ms=(_time.monotonic() - t0) * 1000,
                         artifact=artifact, error_class=exc_cls_name)
            return err

        t0 = _time.monotonic()
        try:
            resp = requests.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=wait,
            )
        except requests.exceptions.Timeout as exc:
            raise _fail("Timeout",
                        LLMError(f"DeepSeek timeout after {wait:.0f}s")) from exc
        except Exception as exc:  # transport errors — keep key out of message
            raise _fail(type(exc).__name__,
                        LLMError(f"DeepSeek transport error: {type(exc).__name__}")) from exc
        latency_ms = (_time.monotonic() - t0) * 1000

        if resp.status_code != 200:
            # Body may contain provider error detail but never our key.
            detail = ""
            try:
                detail = str((resp.json().get("error") or {}).get("message") or "")[:200]
            except Exception:
                pass
            raise _fail(f"HTTP{resp.status_code}",
                        LLMError(f"DeepSeek HTTP {resp.status_code}: {detail or 'no detail'}"))

        try:
            payload = resp.json()
            choice = payload["choices"][0]
            message = choice.get("message") or {}
            text = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            finish = str(choice.get("finish_reason") or "")
            reported_model = str(payload.get("model") or resolved_model)
            usage = payload.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
        except Exception as exc:
            raise _fail(type(exc).__name__,
                        LLMError(f"DeepSeek malformed response: {type(exc).__name__}")) from exc

        record_usage(provider=self.provider_name, model=reported_model,
                     status="ok", prompt_tokens=prompt_tokens,
                     completion_tokens=completion_tokens,
                     latency_ms=latency_ms, artifact=artifact)
        return LLMResponse(
            text=text,
            model=reported_model,
            provider=self.provider_name,
            stop_reason=_FINISH_REASON_MAP.get(finish, "other"),
            reasoning_text=reasoning,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
