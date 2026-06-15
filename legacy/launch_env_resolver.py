#!/usr/bin/env python3
"""
Resolve launch-time environment values deterministically.

This is intentionally narrower than secure_env.load_runtime_env():
- prefer the explicit external env file selected by SNIPER_ENV_PATH
- fall back to shell env only when the external file does not define the key
- otherwise use the provided default

The main use case is startup scripts that must not inherit stale shell exports
when the operator has already updated the authoritative external env file.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict

from secure_env import runtime_env_status


_DEFAULT_BEATS_SHELL_KEYS = {"SHORT_LIVE_ENABLED"}


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(value):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            if idx == 0 or value[idx - 1].isspace():
                return value[:idx].rstrip()
    return value.rstrip()


def _normalize_value(raw: str) -> str:
    value = _strip_inline_comment(str(raw or "").strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return values

    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _normalize_value(raw_value)
    return values


def resolve_runtime_env_value(name: str, default: str = "") -> Dict[str, str]:
    key = str(name or "").strip()
    if not key:
        return {"name": "", "value": str(default or ""), "source": "default", "path": ""}

    status = runtime_env_status()
    source = str(status.get("source") or "")
    env_path = str(status.get("path") or "")

    if source in {"explicit_path", "project_dotenv_override"} and env_path:
        file_values = _parse_env_file(Path(env_path))
        if key in file_values and file_values[key] != "":
            return {
                "name": key,
                "value": file_values[key],
                "source": source,
                "path": env_path,
            }
        if key in _DEFAULT_BEATS_SHELL_KEYS:
            return {
                "name": key,
                "value": str(default or ""),
                "source": "default_with_explicit_env",
                "path": env_path,
            }

    shell_value = os.getenv(key)
    if shell_value not in (None, ""):
        return {"name": key, "value": str(shell_value), "source": "shell_env", "path": ""}

    return {"name": key, "value": str(default or ""), "source": "default", "path": ""}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve a launch-time env var from runtime env policy.")
    parser.add_argument("name", help="Environment variable name")
    parser.add_argument("--default", default="", help="Fallback value when unset everywhere")
    parser.add_argument("--json", action="store_true", help="Print JSON payload instead of only the value")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    resolved = resolve_runtime_env_value(args.name, default=args.default)
    if args.json:
        print(json.dumps(resolved, sort_keys=True))
    else:
        print(resolved["value"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
