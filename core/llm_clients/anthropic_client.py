"""Anthropic client — explicit fallback only, NOT the default.

Used only when BOTH are set: ``LLM_PROVIDER=anthropic`` and
``ANTHROPIC_ENABLED=true``.  ``ANTHROPIC_API_KEY`` is not required for
normal (DeepSeek) operation.

  ANTHROPIC_MODEL           role "chat" model, default claude-haiku-4-5-20251001
  ANTHROPIC_REASONER_MODEL  role "reasoner" model, default claude-opus-4-8
  ANTHROPIC_TIMEOUT_SECONDS default 60
  ANTHROPIC_MAX_TOKENS      default 4000
"""
from __future__ import annotations

import os
from typing import Optional

from core.llm_clients.base import (
    LLMClient,
    LLMError,
    LLMResponse,
    ROLE_REASONER,
    resolve_secret,
)

DEFAULT_CHAT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_REASONER_MODEL = "claude-opus-4-8"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_TOKENS = 4000


class AnthropicClient(LLMClient):
    provider_name = "anthropic"

    def is_configured(self) -> bool:
        return bool(resolve_secret("ANTHROPIC_API_KEY"))

    def model_for_role(self, role: str) -> str:
        if role == ROLE_REASONER:
            return os.getenv("ANTHROPIC_REASONER_MODEL", "").strip() or DEFAULT_REASONER_MODEL
        return os.getenv("ANTHROPIC_MODEL", "").strip() or DEFAULT_CHAT_MODEL

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

        api_key = resolve_secret("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY not set")
        try:
            import anthropic  # lazy — cred-free module load
        except Exception as exc:
            raise LLMError("anthropic package unavailable") from exc

        resolved_model = (model or "").strip() or self.model_for_role(role)

        budget_reason = check_daily_budget()
        if budget_reason:
            record_usage(provider=self.provider_name, model=resolved_model,
                         status="budget_blocked", artifact=artifact)
            raise LLMError(budget_reason)

        budget = int(max_tokens) if max_tokens else int(
            os.getenv("ANTHROPIC_MAX_TOKENS", "") or DEFAULT_MAX_TOKENS)
        wait = timeout if timeout else float(
            os.getenv("ANTHROPIC_TIMEOUT_SECONDS", "") or DEFAULT_TIMEOUT_SECONDS)

        kwargs = {}
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        import time as _time
        t0 = _time.monotonic()
        try:
            client = anthropic.Anthropic(api_key=api_key, timeout=wait, max_retries=1)
            msg = client.messages.create(
                model=resolved_model,
                max_tokens=budget,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
        except Exception as exc:
            record_usage(provider=self.provider_name, model=resolved_model,
                         status="error",
                         latency_ms=(_time.monotonic() - t0) * 1000,
                         artifact=artifact, error_class=type(exc).__name__)
            raise LLMError(f"Anthropic call failed: {type(exc).__name__}") from exc
        latency_ms = (_time.monotonic() - t0) * 1000

        text = "".join(
            getattr(b, "text", "") or ""
            for b in getattr(msg, "content", [])
        )
        stop = getattr(msg, "stop_reason", None)
        normalized = {
            "end_turn": "end",
            "max_tokens": "max_tokens",
            "refusal": "refusal",
        }.get(str(stop), "other")
        usage = getattr(msg, "usage", None)
        prompt_tokens = getattr(usage, "input_tokens", None)
        completion_tokens = getattr(usage, "output_tokens", None)
        record_usage(provider=self.provider_name, model=resolved_model,
                     status="ok", prompt_tokens=prompt_tokens,
                     completion_tokens=completion_tokens,
                     latency_ms=latency_ms, artifact=artifact)
        return LLMResponse(
            text=text,
            model=resolved_model,
            provider=self.provider_name,
            stop_reason=normalized,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
