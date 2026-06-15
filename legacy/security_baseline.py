"""
Security baseline controls for automated trading entry paths.

This module is intentionally simple and dependency-light so all daemons
can share the same guardrails.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Dict, Optional, Tuple


_TRUTHY = {"1", "true", "yes", "on"}
_REPO_ROOT = Path(__file__).resolve().parent


def _is_truthy(value: object) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def alpaca_keys_present() -> bool:
    """Return True when both Alpaca key and secret are available."""
    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    return bool(api_key and secret_key)


def env_file_security(env_path: str = ".env") -> Tuple[bool, str]:
    """
    Check whether .env is private to the owner.

    Secure mode is owner-only (600/400 style). Group/world readability is warned.
    """
    path = Path(env_path)
    if not path.is_absolute():
        path = (_REPO_ROOT / env_path).resolve()

    if not path.exists():
        return True, f"{path} not found (using shell-injected env vars)"
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError as exc:
        return False, f"{path} permission check failed: {exc}"

    secure = (mode & 0o077) == 0
    if secure:
        return True, f"{path} permissions secure ({oct(mode)})"
    return False, f"{path} too open ({oct(mode)}), recommend chmod 600 {path}"


def repo_env_policy(env_path: str = ".env") -> Tuple[bool, str]:
    """
    Warn when a dotenv file lives inside the repository tree.

    This is not an execution blocker by itself, but it is a source-control and
    local-access risk. Preferred policy is shell env vars or SNIPER_ENV_PATH
    pointing to a file outside the repo tree.
    """
    path = Path(env_path)
    if not path.is_absolute():
        path = (_REPO_ROOT / env_path).resolve()

    if not path.exists():
        return True, "no repo-local .env detected"

    if str(os.getenv("ALLOW_PROJECT_DOTENV", "")).strip().upper() == "YES":
        return False, (
            "repo-local .env override enabled; move secrets outside the repo and "
            "remove ALLOW_PROJECT_DOTENV=YES"
        )
    return False, (
        "repo-local .env detected; move secrets outside the repo and use "
        "SNIPER_ENV_PATH or shell env vars"
    )


def live_mode_armed(execution_mode: str = "PAPER") -> Tuple[bool, str]:
    """
    Require explicit arming when someone tries to run LIVE mode.

    LIVE mode is only considered armed when LIVE_TRADING_ARMED=YES.
    """
    mode = str(execution_mode or "PAPER").upper()
    if mode != "LIVE":
        return True, "not in LIVE mode"

    armed = str(os.getenv("LIVE_TRADING_ARMED", "")).strip().upper() == "YES"
    if armed:
        return True, "LIVE mode explicitly armed"
    return False, "LIVE mode blocked: set LIVE_TRADING_ARMED=YES"


def killswitch_status(killswitch_path: Optional[str] = None) -> Tuple[bool, str, str]:
    """
    Global manual kill-switch for new entries.

    Active if:
    - TRADING_KILL_SWITCH is truthy, or
    - file exists at TRADING_KILLSWITCH_FILE (default: .trading_killswitch)
    """
    path = killswitch_path or os.getenv("TRADING_KILLSWITCH_FILE", ".trading_killswitch")

    if _is_truthy(os.getenv("TRADING_KILL_SWITCH", "0")):
        return True, "TRADING_KILL_SWITCH env active", path
    if os.path.exists(path):
        return True, f"kill-switch file present: {path}", path
    return False, "kill-switch inactive", path


def is_entry_allowed(
    execution_enabled: bool = True,
    execution_mode: str = "PAPER",
    killswitch_path: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Unified pre-entry security gate.

    Returns:
        (allowed, reason)
    """
    if not execution_enabled:
        return False, "execution disabled"

    live_ok, live_reason = live_mode_armed(execution_mode=execution_mode)
    if not live_ok:
        return False, live_reason

    kill_active, kill_reason, _ = killswitch_status(killswitch_path=killswitch_path)
    if kill_active:
        return False, kill_reason

    return True, "entry allowed"


def startup_security_report(
    component: str,
    execution_mode: str = "PAPER",
    env_path: str = ".env",
    killswitch_path: Optional[str] = None,
) -> Dict[str, object]:
    """
    Build a startup security snapshot for logs/UI.
    """
    keys_present = alpaca_keys_present()
    env_secure, env_file_message = env_file_security(env_path=env_path)
    live_armed, live_mode_message = live_mode_armed(execution_mode=execution_mode)
    kill_active, kill_reason, kill_path = killswitch_status(killswitch_path=killswitch_path)
    repo_env_ok, repo_env_message = repo_env_policy(env_path=env_path)

    return {
        "component": component,
        "execution_mode": str(execution_mode or "PAPER").upper(),
        "keys_present": keys_present,
        "env_file_secure": env_secure,
        "env_file_message": env_file_message,
        "repo_env_ok": repo_env_ok,
        "repo_env_message": repo_env_message,
        "live_armed": live_armed,
        "live_mode_message": live_mode_message,
        "killswitch_active": kill_active,
        "killswitch_reason": kill_reason,
        "killswitch_path": kill_path,
        "entry_allowed_now": bool(keys_present and live_armed and not kill_active),
    }
