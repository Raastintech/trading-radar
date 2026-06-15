"""
Explicit runtime environment loading policy for local trading tools.

Default behavior is fail-closed for repo-local secret files:
- Do not auto-load a `.env` file from inside the repository.
- Allow shell-injected environment variables.
- Allow an explicit external env file via `SNIPER_ENV_PATH`.
- Allow repo-local `.env` only when `ALLOW_PROJECT_DOTENV=YES`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

try:
    from dotenv import load_dotenv as _load_dotenv
except Exception:
    _load_dotenv = None


_TRUTHY = {"1", "true", "yes", "on"}
_REPO_ROOT = Path(__file__).resolve().parent
_PROJECT_DOTENV = _REPO_ROOT / ".env"


def _is_truthy(value: object) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def _resolve_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _is_inside_repo(path: Path) -> bool:
    try:
        path.relative_to(_REPO_ROOT)
        return True
    except ValueError:
        return False


def runtime_env_status() -> Dict[str, object]:
    explicit_path = str(os.getenv("SNIPER_ENV_PATH", "")).strip()
    allow_project_dotenv = _is_truthy(os.getenv("ALLOW_PROJECT_DOTENV"))

    if explicit_path:
        resolved = _resolve_path(explicit_path)
        if not resolved.exists():
            return {
                "loaded": False,
                "source": "missing_explicit_path",
                "path": str(resolved),
                "message": f"SNIPER_ENV_PATH does not exist: {resolved}",
            }
        if _is_inside_repo(resolved) and not allow_project_dotenv:
            return {
                "loaded": False,
                "source": "blocked_repo_path",
                "path": str(resolved),
                "message": (
                    "Refusing to auto-load an env file from inside the repo tree. "
                    "Move it outside the project or set ALLOW_PROJECT_DOTENV=YES."
                ),
            }
        return {
            "loaded": False,
            "source": "explicit_path",
            "path": str(resolved),
            "message": f"Using explicit env path: {resolved}",
        }

    if allow_project_dotenv and _PROJECT_DOTENV.exists():
        return {
            "loaded": False,
            "source": "project_dotenv_override",
            "path": str(_PROJECT_DOTENV),
            "message": (
                "Repo-local .env override enabled via ALLOW_PROJECT_DOTENV=YES. "
                "Move secrets outside the repo for stronger isolation."
            ),
        }

    if _PROJECT_DOTENV.exists():
        return {
            "loaded": False,
            "source": "repo_dotenv_disabled",
            "path": str(_PROJECT_DOTENV),
            "message": (
                "Repo-local .env detected but auto-load is disabled. "
                "Use shell env vars or SNIPER_ENV_PATH to an external file."
            ),
        }

    return {
        "loaded": False,
        "source": "shell_only",
        "path": "",
        "message": "No env file auto-loaded. Using shell-injected environment only.",
    }


def load_runtime_env(component: Optional[str] = None) -> Dict[str, object]:
    status = runtime_env_status()
    env_path = str(status.get("path") or "")
    source = str(status.get("source") or "")

    if source in {"explicit_path", "project_dotenv_override"} and env_path:
        if _load_dotenv is None:
            status["message"] = (
                f"{status['message']} python-dotenv is unavailable, so no env file was loaded."
            )
        else:
            _load_dotenv(dotenv_path=env_path, override=False)
            status["loaded"] = True

    if component:
        status["component"] = component
    return status
