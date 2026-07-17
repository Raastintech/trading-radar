"""Provider-agnostic LLM client layer (research-annotation use only).

DeepSeek is the default lower-cost LLM provider (2026-07-17 migration),
subject to provider rate limits, account capacity, API availability, and
the configured budget controls (``core/llm_clients/usage.py``).  Anthropic
remains available only as an explicitly-enabled fallback
(``LLM_PROVIDER=anthropic`` + ``ANTHROPIC_ENABLED=true``).

Doctrine:
  - CRED-FREE at import time.  No dependency on ``core.config``; keys are
    resolved lazily per call with the trading.env-over-shell convention.
  - NON-MUTATING.  LLM output is annotation/audit text only — it never
    changes scores, rankings, gates, verdicts, or artifacts, and every JSON
    artifact that embeds LLM output must carry the forced safety fields
    from :func:`research_safety_fields`.
  - NEVER-BLOCK.  A provider failure raises ``LLMError`` (or the factory
    returns ``None`` when unconfigured); callers degrade to their
    deterministic paths — the nightly cycle must never block on an LLM.

This package lives in ``core/`` (not ``research/``) because production
modules (``core/executive_gatekeeper.py``, ``dashboards/gem_trader_hq.py``)
consume it and the hard separation rule forbids production importing from
``research/``.
"""
from core.llm_clients.base import (  # noqa: F401
    LLMClient,
    LLMError,
    LLMResponse,
    ROLE_CHAT,
    ROLE_REASONER,
    apply_safety_fields,
    extract_json_object,
    llm_fail_open,
    research_safety_fields,
    resolve_secret,
)
from core.llm_clients.provider import (  # noqa: F401
    get_llm_client,
    get_llm_status,
)
from core.llm_clients.usage import (  # noqa: F401
    check_daily_budget,
    record_usage,
    usage_today,
)
