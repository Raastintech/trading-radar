"""Shared contracts for the provider-agnostic LLM layer.

Cred-free at import time.  Never logs or embeds API keys anywhere —
error strings, reprs, and artifacts must stay key-free.
"""
from __future__ import annotations

import abc
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# Model-routing roles.  Callers ask for a role, never a hard-coded model
# name; each provider maps the role to a model via its own env vars.
ROLE_CHAT = "chat"          # summaries, memos, light audits, panel notes
ROLE_REASONER = "reasoner"  # reasoning-heavy critique / structured audits

# Forced safety contract for every JSON artifact that embeds LLM output.
# These are published invariants, not model claims — apply_safety_fields
# overwrites whatever the model returned.
_SAFETY_FIELDS = {
    "research_only": True,
    "promote_to_signal": False,
    "may_change_scores": False,
    "may_change_rankings": False,
    "may_change_gates": False,
    "may_change_verdicts": False,
}


def research_safety_fields() -> Dict[str, bool]:
    """Fresh copy of the forced research-only safety fields."""
    return dict(_SAFETY_FIELDS)


def apply_safety_fields(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Force the safety fields onto ``obj`` in place (even if the LLM lied),
    and return it."""
    obj.update(_SAFETY_FIELDS)
    return obj


def resolve_secret(name: str) -> str:
    """Resolve a secret env var with the repo's canonical convention:
    the credential file at ``SNIPER_ENV_PATH`` takes precedence over an
    inherited shell export (a stale ``export X=...`` must not shadow a
    rotated key in trading.env).  ``GEM_TRADER_SKIP_DOTENV`` disables the
    file read (tests / cred-free tooling).  Never log the returned value.
    """
    if os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in ("1", "true", "yes"):
        env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
        if env_path:
            try:
                from dotenv import dotenv_values
                v = (dotenv_values(env_path).get(name) or "").strip()
                if v:
                    return v
            except Exception:
                pass
    return os.getenv(name, "").strip()


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def llm_fail_open() -> bool:
    """``LLM_FAIL_OPEN`` (default true): when true, an LLM failure must
    degrade to the caller's deterministic path so scheduled cycles keep
    running; setting it false makes callers propagate the failure instead
    (debugging / hard-verification runs only)."""
    return env_flag("LLM_FAIL_OPEN", True)


def extract_json_object(text: str) -> Dict[str, Any]:
    """Parse a JSON object from an LLM reply: tolerates ``` fences and
    leading/trailing prose (falls back to the outermost {...} span).
    Raises ``ValueError`` when no JSON object can be recovered."""
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(stripped[start:end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError("response did not contain a JSON object")


class LLMError(RuntimeError):
    """Any provider-side failure (transport, timeout, HTTP error, refusal
    surface, malformed payload).  Message must never contain the API key."""


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    # Normalised: "end" | "max_tokens" | "refusal" | "other"
    stop_reason: str = "end"
    # deepseek-reasoner chain-of-thought (informational only; never parsed
    # for verdicts and never required to be present).
    reasoning_text: str = field(default="", repr=False)
    # Provider-reported usage (None when the provider omits it).
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    latency_ms: Optional[float] = None


class LLMClient(abc.ABC):
    """Minimal provider contract: one blocking text completion."""

    provider_name: str = "unknown"

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """True when the provider has a usable API key."""

    @abc.abstractmethod
    def model_for_role(self, role: str) -> str:
        """The concrete model this provider routes ``role`` to."""

    @abc.abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        role: str = ROLE_CHAT,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        artifact: Optional[str] = None,
    ) -> LLMResponse:
        """Single completion.  Raises :class:`LLMError` on any failure —
        callers convert failures into their deterministic fallbacks.

        ``artifact`` is a short caller-supplied label (the artifact the
        output feeds, e.g. ``cache/research/journal_audit_latest.json``)
        recorded in the usage log; never part of the prompt."""

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        max_tokens: int,
        role: str = ROLE_CHAT,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        artifact: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Structured-output completion: parse the reply as a JSON object,
        force the research-only safety fields onto it, and attach a
        ``_llm`` metadata block (provider/model/usage — no secrets).

        On truncation or unparseable JSON raises :class:`LLMError` with
        ``schema_name`` and a safely truncated ``raw_excerpt`` attribute so
        callers can persist an ERROR artifact for diagnosis."""
        resp = self.complete(
            user_prompt, system=system_prompt, role=role, model=model,
            max_tokens=max_tokens, temperature=temperature, timeout=timeout,
            artifact=artifact)
        if resp.stop_reason == "max_tokens":
            err = LLMError(
                f"response truncated at max_tokens={max_tokens} "
                f"for schema '{schema_name}'")
            err.schema_name = schema_name
            err.raw_excerpt = resp.text[:2000]
            raise err
        try:
            obj = extract_json_object(resp.text)
        except Exception as exc:
            err = LLMError(
                f"invalid JSON for schema '{schema_name}': "
                f"{type(exc).__name__}")
            err.schema_name = schema_name
            err.raw_excerpt = resp.text[:2000]
            raise err from exc
        apply_safety_fields(obj)
        obj["_llm"] = {
            "provider": resp.provider,
            "model": resp.model,
            "latency_ms": resp.latency_ms,
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
        }
        return obj
