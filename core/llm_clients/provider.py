"""Provider factory for the LLM layer.

Selection (env-driven, resolved at call time):
  LLM_PROVIDER       "deepseek" (default) | "anthropic" | "none"/"disabled"
  DEEPSEEK_ENABLED   default true  — set false to hard-disable DeepSeek
  ANTHROPIC_ENABLED  default false — Anthropic is opt-in fallback only

``get_llm_client()`` returns ``None`` whenever no provider is enabled and
configured; callers must treat ``None`` as "run the deterministic path".
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.llm_clients.base import LLMClient, env_flag, llm_fail_open
from core.llm_clients.deepseek_client import DeepSeekClient
from core.llm_clients.anthropic_client import AnthropicClient
from core.llm_clients.usage import usage_today

DEFAULT_PROVIDER = "deepseek"


def _selected_provider() -> str:
    return (os.getenv("LLM_PROVIDER", "").strip().lower() or DEFAULT_PROVIDER)


def _build_client(provider: str) -> Optional[LLMClient]:
    if provider == "deepseek":
        if not env_flag("DEEPSEEK_ENABLED", True):
            return None
        return DeepSeekClient()
    if provider == "anthropic":
        # Anthropic must be explicitly re-enabled — it is no longer default.
        if not env_flag("ANTHROPIC_ENABLED", False):
            return None
        return AnthropicClient()
    return None


def get_llm_client(*, require_configured: bool = True) -> Optional[LLMClient]:
    """The active LLM client, or ``None`` when disabled/unconfigured.

    With ``require_configured=True`` (default) a client without a usable
    API key also resolves to ``None`` so callers degrade immediately
    instead of failing inside the call.
    """
    client = _build_client(_selected_provider())
    if client is None:
        return None
    if require_configured and not client.is_configured():
        return None
    return client


def get_llm_status() -> Dict[str, Any]:
    """Key-free status snapshot for artifacts / operator surfaces."""
    provider = _selected_provider()
    client = _build_client(provider)
    if client is None:
        enabled = False
        configured = False
        chat_model = reasoner_model = None
    else:
        enabled = True
        configured = client.is_configured()
        chat_model = client.model_for_role("chat")
        reasoner_model = client.model_for_role("reasoner")
    return {
        "provider": provider,
        "enabled": enabled,
        "configured": configured,
        "chat_model": chat_model,
        "reasoner_model": reasoner_model,
        "fail_open": llm_fail_open(),
        "usage_today": usage_today(),
    }
