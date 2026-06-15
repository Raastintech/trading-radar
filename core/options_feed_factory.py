"""
core/options_feed_factory.py — shared options feed loader.

Phase 3A (2026-06-13): Research-only mode — Tradier is the sole options
data provider. Alpaca options feed is removed from the active code path
(original preserved at archive/execution_disabled/ if needed).

Single entry point that hands every consumer (alpha discovery, social
arb radar, stock lens runner) the Tradier feed wrapped in an
``OptionsFeedChain``. Returns ``None`` when Tradier is not configured so
callers keep their existing "no options" fallback paths.

Doctrine:
  - Data-only. No execution / governance / DB imports.
  - Fail-soft. Import errors degrade to ``None``; the chain itself
    catches per-feed errors.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TRADIER_FEED_PATH = Path(__file__).resolve().parent.parent / "legacy" / "tradier_options_feed.py"


def _load_tradier_feed() -> Optional[Any]:
    """Load the Tradier options feed. Returns the instance (configured
    or not — the chain wrapper does the configuration check). Lives in
    legacy/ for historical reasons but is the sole research-only options
    provider in Phase 3A+."""
    try:
        spec = importlib.util.spec_from_file_location(
            "legacy_tradier_options_feed", _TRADIER_FEED_PATH,
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "TradierOptionsFeed"):
            return None
        feed = module.TradierOptionsFeed()
        if not hasattr(feed, "NAME"):
            try:
                setattr(feed, "NAME", "tradier")
            except Exception:
                pass
        return feed
    except Exception as exc:
        logger.info("Tradier feed import failed: %s", exc)
        return None


def load_options_feed() -> Optional[Any]:
    """Return the Tradier-only chain wrapper, or ``None`` when Tradier is
    not configured. Callers should treat ``None`` as "options unavailable"
    — preserving every existing OPTIONS_MISSING path."""
    try:
        from core.options_feed_chain import OptionsFeedChain
    except Exception as exc:
        logger.info("Options feed chain import failed: %s", exc)
        return None
    feed = _load_tradier_feed()
    chain = OptionsFeedChain([feed] if feed is not None else [])
    return chain if chain.is_configured() else None
