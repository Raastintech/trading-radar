"""LLM budget controls + secret-free usage logging.

DeepSeek is the default lower-cost LLM provider, subject to provider rate
limits, account capacity, API availability, and the budget controls below
— never assume unmetered usage.

Env (lazy, all optional):
  LLM_DAILY_MAX_CALLS    default 200  — provider calls per UTC day; <=0 disables the cap
  LLM_DAILY_MAX_TOKENS   default 400000 — prompt+completion tokens per UTC day; <=0 disables
  LLM_USAGE_LOG_PATH     default <repo>/logs/llm_usage.jsonl
  LLM_MONTHLY_BUDGET_USD reserved — NOT enforced (no per-token cost model
                         exists in this repo yet); documented for forward
                         compatibility only.

The usage ledger is append-only JSONL, one entry per provider call
attempt.  Logged fields: ts, provider, model, status (ok | error |
budget_blocked), prompt_tokens, completion_tokens, latency_ms, artifact
(caller label), error_class.  NEVER logged: API keys, auth headers, raw
prompts, raw replies.

Everything here is best-effort: logging failures are swallowed and a
broken ledger never blocks a call beyond the budget verdict it supports.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_REL = Path("logs") / "llm_usage.jsonl"

DEFAULT_DAILY_MAX_CALLS = 200
DEFAULT_DAILY_MAX_TOKENS = 400_000

# Statuses that count against the daily caps (budget_blocked rows are
# bookkeeping, not spend).
_COUNTED_STATUSES = ("ok", "error")


def _log_path() -> Path:
    override = os.getenv("LLM_USAGE_LOG_PATH", "").strip()
    return Path(override) if override else REPO_ROOT / DEFAULT_LOG_REL


def _cap(name: str, default: int) -> Optional[int]:
    """Env int cap; <=0 means the cap is disabled."""
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except (TypeError, ValueError):
        value = default
    return value if value > 0 else None


def _today_totals() -> Tuple[int, int]:
    """(calls, tokens) recorded today (UTC) — counted statuses only."""
    path = _log_path()
    if not path.exists():
        return 0, 0
    today = datetime.now(timezone.utc).date().isoformat()
    calls = 0
    tokens = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not str(row.get("ts", "")).startswith(today):
                    continue
                if row.get("status") not in _COUNTED_STATUSES:
                    continue
                calls += 1
                tokens += int(row.get("prompt_tokens") or 0)
                tokens += int(row.get("completion_tokens") or 0)
    except Exception:
        return 0, 0
    return calls, tokens


def check_daily_budget() -> Optional[str]:
    """None when under budget; otherwise a human-readable (key-free)
    reason string the caller should surface and treat as a skip."""
    calls_cap = _cap("LLM_DAILY_MAX_CALLS", DEFAULT_DAILY_MAX_CALLS)
    tokens_cap = _cap("LLM_DAILY_MAX_TOKENS", DEFAULT_DAILY_MAX_TOKENS)
    if calls_cap is None and tokens_cap is None:
        return None
    calls, tokens = _today_totals()
    if calls_cap is not None and calls >= calls_cap:
        return (f"LLM daily call cap reached ({calls}/{calls_cap} — "
                f"LLM_DAILY_MAX_CALLS)")
    if tokens_cap is not None and tokens >= tokens_cap:
        return (f"LLM daily token cap reached ({tokens}/{tokens_cap} — "
                f"LLM_DAILY_MAX_TOKENS)")
    return None


def record_usage(
    *,
    provider: str,
    model: str,
    status: str,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    latency_ms: Optional[float] = None,
    artifact: Optional[str] = None,
    error_class: Optional[str] = None,
) -> None:
    """Append one usage entry.  Best-effort — never raises."""
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": model,
            "status": status,
        }
        if prompt_tokens is not None:
            entry["prompt_tokens"] = int(prompt_tokens)
        if completion_tokens is not None:
            entry["completion_tokens"] = int(completion_tokens)
        if latency_ms is not None:
            entry["latency_ms"] = round(float(latency_ms), 1)
        if artifact:
            entry["artifact"] = str(artifact)
        if error_class:
            entry["error_class"] = str(error_class)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def usage_today() -> Dict[str, Any]:
    """Key-free snapshot for operator surfaces / get_llm_status."""
    calls, tokens = _today_totals()
    return {
        "calls_today": calls,
        "tokens_today": tokens,
        "daily_max_calls": _cap("LLM_DAILY_MAX_CALLS",
                                DEFAULT_DAILY_MAX_CALLS),
        "daily_max_tokens": _cap("LLM_DAILY_MAX_TOKENS",
                                 DEFAULT_DAILY_MAX_TOKENS),
    }
