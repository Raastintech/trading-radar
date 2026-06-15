"""
core/config.py — Single source of truth for all runtime configuration.

Reads from environment (populated by .env via systemd EnvironmentFile).
No credentials ever hardcoded here.

Phase 3A (2026-06-13): System permanently converted to RESEARCH_ONLY mode.
Alpaca keys are now optional — the system starts and runs research functions
without them. FMP remains required. See core/research_mode.py for mode flags.
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root if running outside systemd.
# Set GEM_TRADER_SKIP_DOTENV=true for credential-free tooling/tests.
_ROOT = Path(__file__).resolve().parent.parent
if os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in ("1", "true", "yes"):
    load_dotenv(_ROOT / ".env", override=False)

# ── System mode (Phase 3A) ────────────────────────────────────────────────────
from core.research_mode import SYSTEM_MODE, RESEARCH_ONLY_BANNER  # noqa: E402


def _strip_comment(v: str) -> str:
    """Remove inline shell comments (e.g. 'value  # comment' → 'value')."""
    return v.split("#")[0].strip()


def _req(key: str) -> str:
    v = _strip_comment(os.getenv(key, "").strip())
    if not v:
        raise RuntimeError(f"Required env var {key!r} is not set. Check your .env file.")
    return v


def _opt(key: str, default: str = "") -> str:
    return _strip_comment(os.getenv(key, default).strip())


# ── Alpaca (Phase 3A: optional — system runs research without Alpaca keys) ────
# Keys are no longer required. If present they are read for any legacy code
# paths that still reference them; if absent the system degrades gracefully.
# Broker execution through Alpaca is permanently disabled (see research_mode.py).
ALPACA_API_KEY    = _opt("ALPACA_API_KEY",    "")
ALPACA_SECRET_KEY = _opt("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = _opt("ALPACA_BASE_URL", "https://api.alpaca.markets")
ALPACA_DATA_URL   = _opt("ALPACA_DATA_URL", "https://data.alpaca.markets")
ALPACA_PAPER      = _opt("ALPACA_PAPER", "true").lower() == "true"

# ── Financial Modeling Prep ───────────────────────────────────────────────────
FMP_API_KEY          = _req("FMP_API_KEY")
FMP_BASE_URL         = _opt("FMP_BASE_URL", "https://financialmodelingprep.com/api")

# Confirmed from plan page: 750 calls/minute.
# This is the ONLY hard enforcement gate — the token bucket in FMPClient enforces it.
FMP_CALLS_PER_MINUTE = int(_opt("FMP_CALLS_PER_MINUTE", "750"))

# Telemetry-only counters. No confirmed hard call cap on this plan (plan page shows
# 750 RPM + 50 GB bandwidth, not a monthly call count). These values are recorded
# in the DB for visibility but never block any call.
#
# TODO(fmp-premium-renewal): once the Premium plan renews and the monthly call
# cap is confirmed from the plan dashboard, set FMP_MONTHLY_BUDGET to ~80% of
# the confirmed cap and FMP_DAILY_BUDGET to roughly cap/22 so the gatekeeper
# trips before the provider does.  Do NOT change FMP_CALLS_PER_MINUTE (already
# at the Premium ceiling of 750) and do NOT alter the cache → Alpaca → FMP →
# yfinance order in the bar loaders — Alpaca-first keeps FMP usage low.
FMP_MONTHLY_BUDGET   = int(_opt("FMP_MONTHLY_BUDGET", "0"))   # 0 = no cap enforced
FMP_DAILY_BUDGET     = int(_opt("FMP_DAILY_BUDGET",   "0"))   # 0 = no cap enforced

# ── Storage ───────────────────────────────────────────────────────────────────
DB_PATH   = Path(_opt("DB_PATH",   str(_ROOT / "db" / "trading.db"))).resolve()
CACHE_DIR = Path(_opt("CACHE_DIR", str(_ROOT / "cache"))).resolve()
LOG_DIR   = Path(_opt("LOG_DIR",   str(_ROOT / "logs"))).resolve()
LOG_LEVEL = _opt("LOG_LEVEL", "INFO").upper()

# Ensure directories exist
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
(CACHE_DIR / "prices").mkdir(parents=True, exist_ok=True)
(CACHE_DIR / "fundamentals").mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Risk limits ───────────────────────────────────────────────────────────────
MAX_POSITION_PCT  = float(_opt("MAX_POSITION_PCT",  "0.02"))
MAX_DAILY_LOSS_PCT = float(_opt("MAX_DAILY_LOSS_PCT", "0.05"))
ALLOW_SHORTS      = _opt("ALLOW_SHORTS", "true").lower() == "true"
# Phase 1A: paper mode is the safe default — see ALPACA_PAPER above.
PAPER_TRADING     = _opt("PAPER_TRADING", "true").lower() == "true"

# ── Live-capital two-key gate (Phase 1A) ─────────────────────────────────────
# Live trading requires THREE independent env keys to all be set
# explicitly:
#
#   PAPER_TRADING       = false
#   ALPACA_PAPER        = false
#   ALLOW_LIVE_CAPITAL  = true
#
# AND, optionally, a manual confirmation file pointed at by
# LIVE_CONFIRM_FILE must exist on disk.  The check lives inside
# ``AlpacaClient.submit_*_order`` and ``close_position`` so any future caller
# (engine, repl, script) inherits the gate without having to remember it.
#
# This is intentionally redundant with PAPER_TRADING: a single typo
# can't put real capital at risk, and an operator must touch all three
# keys to enable live.  Until the holdout closes (2026-12-01) the
# expectation is that ALLOW_LIVE_CAPITAL stays False.
ALLOW_LIVE_CAPITAL = _opt("ALLOW_LIVE_CAPITAL", "false").lower() == "true"

# Optional path to a manual confirmation file.  When set (non-empty),
# the live-capital gate ALSO requires this file to exist on disk
# before any live order can be submitted.  Use case: an operator
# explicitly creates the file with date / sign-off content before
# enabling live, and removes it to immediately drop back to paper.
# Empty string (default) means "no file required" — the three env
# keys alone are sufficient.
LIVE_CONFIRM_FILE = _opt("LIVE_CONFIRM_FILE", "")

# ── Regime freshness SLA (Phase 1A) ──────────────────────────────────────────
# Submission-time regime favorability remains cache-only.  In paper mode a
# stale/missing forecast warns by default so evidence collection keeps running;
# operators may set REGIME_STALE_BEHAVIOR_PAPER=block to fail closed.  Any
# future live-side config must block on stale/missing/malformed regime artifacts.
REGIME_FRESHNESS_MAX_MINUTES = int(_opt("REGIME_FRESHNESS_MAX_MINUTES", "1440"))
REGIME_STALE_BEHAVIOR_PAPER = _opt("REGIME_STALE_BEHAVIOR_PAPER", "warn").lower()
REGIME_STALE_BEHAVIOR_LIVE = _opt("REGIME_STALE_BEHAVIOR_LIVE", "block").lower()

# ── Voyager paper validation ──────────────────────────────────────────────────
# Set VOYAGER_PAPER_LOG=true to auto-log every Voyager signal to voyager_paper_signals.
# This is the paper-validation cycle tracker. Does not affect order routing.
VOYAGER_PAPER_LOG = _opt("VOYAGER_PAPER_LOG", "false").lower() == "true"

# ── Council profile activation ────────────────────────────────────────────────
# When false, VetoCouncil uses a single Tier-2 weight set for all strategies
# (historical default). When true, the council selects per-strategy weights
# from the profile registry — see docs/strategy/COUNCIL_PROFILES.md for the
# activation gate and baseline-tag implications. Default false; activate per
# sleeve only after the corresponding paper sample resolves.
COUNCIL_PROFILES_ENABLED = _opt("COUNCIL_PROFILES_ENABLED", "false").lower() == "true"
