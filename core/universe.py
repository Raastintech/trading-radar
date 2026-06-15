"""
core/universe.py — Dynamic universe discovery and strategy routing.
Pipeline version: 2.0.0

Pipeline stages (explicit):
  Stage 1: Symbol discovery      — Alpaca ~5,500 active US equities
  Stage 2: Bar fetching          — 90-day daily bars, completed bars only
  Stage 3: Feature computation   — ~18 structural features per ticker
  Stage 4: Base filtering        — price, liquidity, data-quality gate
  Stage 5: Strategy hard filters — per-strategy structural predicates
  Stage 6: Score threshold gate  — per-strategy minimum qualifying score
  Stage 7: Readiness labeling    — READY_NOW / WATCH / DEVELOPING / GATED / REJECTED
  Stage 8: Final ranking         — sort by score, cap at top N
  Stage 9: Snapshot persistence  — JSON with full provenance

Key design rules:
  - Only names scoring ABOVE the per-strategy threshold qualify for routing.
    If only 8 names qualify on a weak day, route 8. Never fill with weak names.
  - Curated WATCHLIST_TICKERS survive the base-1000 cap but still must pass
    tradability, hard liquidity, strategy structural filters, and score thresholds.
    They are never silently promoted past those gates.
  - Completed daily bars only (by default) — today's partial in-progress bar is
    stripped before feature computation to prevent intraday noise in structural scores.
  - The snapshot carries full provenance (thresholds used, warnings, stage counts)
    so the dashboard and debugging tools have a trustworthy audit trail.

Snapshot keys returned by build_snapshot():
    base_universe         — top 1,000 by base score
    sniper_universe       — qualified momentum breakout candidates (≤ 90)
    voyager_universe      — qualified uptrend / accumulation candidates (≤ 90)
    short_universe        — qualified distribution / downtrend candidates (≤ 90)
    remora_universe       — qualified mid-vol, elevated-activity candidates (≤ 60)
    contrarian_universe   — qualified washed-out oversold candidates (≤ 90)
    strategy_candidates   — per-ticker per-strategy records for dashboard
    top_symbols           — top 25 base universe symbols
    metadata              — per-ticker scores and metrics (base universe)
    summary               — full provenance, thresholds, warnings, stage counts

Cache: written to CACHE_DIR/universe/universe_snapshot_latest.json
TTL: 30 min (UNIVERSE_SNAPSHOT_TTL_SECONDS env var)
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import core.config as cfg
from core.alpaca_client import get_alpaca

logger = logging.getLogger(__name__)

# ── Pipeline version — bump when scoring or filter logic changes materially ───
PIPELINE_VERSION = "2.0.0"

# ── Tunable pool limits (all env-overridable) ─────────────────────────────────
_BASE_LIMIT       = int(os.getenv("UNIVERSE_BASE_LIMIT",       "1000"))
_VOYAGER_LIMIT    = int(os.getenv("UNIVERSE_VOYAGER_LIMIT",    "90"))
_SNIPER_LIMIT     = int(os.getenv("UNIVERSE_SNIPER_LIMIT",     "90"))
_SHORT_LIMIT      = int(os.getenv("UNIVERSE_SHORT_LIMIT",      "90"))
_REMORA_LIMIT     = int(os.getenv("UNIVERSE_REMORA_LIMIT",     "60"))
_CONTRARIAN_LIMIT = int(os.getenv("UNIVERSE_CONTRARIAN_LIMIT", "90"))
_SNAPSHOT_TTL     = int(os.getenv("UNIVERSE_SNAPSHOT_TTL_SECONDS", "1800"))
_DAYS_BACK        = int(os.getenv("UNIVERSE_DISCOVERY_DAYS_BACK",  "90"))
_CHUNK_SIZE       = int(os.getenv("UNIVERSE_BATCH_CHUNK_SIZE",     "200"))

# ── Base liquidity gate (Stage 4) ─────────────────────────────────────────────
_MIN_PRICE    = float(os.getenv("UNIVERSE_MIN_PRICE",                  "5"))
_MAX_PRICE    = float(os.getenv("UNIVERSE_MAX_PRICE",               "1000"))
_MIN_AVG_VOL  = float(os.getenv("UNIVERSE_MIN_AVG_VOLUME",        "300000"))
_MIN_AVG_DVOL = float(os.getenv("UNIVERSE_MIN_AVG_DOLLAR_VOLUME", "5000000"))

# ── Per-strategy minimum qualifying scores (Stage 6) ─────────────────────────
# Names below these thresholds are NOT routed to any strategy pool, even if they
# pass all structural filters.  Prevents mediocre names filling slots on weak days.
# Set to 0.0 to disable thresholding for a specific strategy.
_SCORE_THRESHOLDS: Dict[str, float] = {
    "voyager":    float(os.getenv("UNIVERSE_VOYAGER_MIN_SCORE",    "0.35")),
    "sniper":     float(os.getenv("UNIVERSE_SNIPER_MIN_SCORE",     "0.38")),
    "short":      float(os.getenv("UNIVERSE_SHORT_MIN_SCORE",      "0.35")),
    "remora":     float(os.getenv("UNIVERSE_REMORA_MIN_SCORE",     "0.32")),
    "contrarian": float(os.getenv("UNIVERSE_CONTRARIAN_MIN_SCORE", "0.30")),
}

# Score ratio below which a passing-filter name is labelled DEVELOPING
# (threshold * DEVELOP_RATIO ≤ score < threshold → DEVELOPING)
_DEVELOP_RATIO = float(os.getenv("UNIVERSE_DEVELOP_SCORE_RATIO", "0.75"))

# ── Completed-bars-only flag (Stage 2) ────────────────────────────────────────
# When True, today's partial/in-progress daily bar is stripped before feature
# computation.  This prevents intraday noise from contaminating structural
# scores (ATR%, MA-distance, momentum, etc.).
# Set UNIVERSE_USE_COMPLETED_BARS_ONLY=false only if you have a specific reason
# to include the live partial bar (e.g., after-hours builds when today is done).
_USE_COMPLETED_BARS_ONLY = (
    os.getenv("UNIVERSE_USE_COMPLETED_BARS_ONLY", "true").lower() != "false"
)

# Maximum calendar days since last bar before a ticker is considered stale
# (~3 trading days × 2 calendar buffer to cover weekends / holidays).
_STALE_CALENDAR_DAYS = int(os.getenv("UNIVERSE_STALE_CALENDAR_DAYS", "6"))

# ── Short-side stricter liquidity floor (Stage 5) ─────────────────────────────
# Shorting thin names risks squeeze.  Minimum dollar volume is raised above the
# base requirement.  Earnings-proximity and borrow-rate checks are NOT performed
# here — those require FMP/broker data and are delegated to the Veto Council.
_SHORT_MIN_DVOL  = float(os.getenv("UNIVERSE_SHORT_MIN_DOLLAR_VOLUME", "15000000"))
_SHORT_MIN_PRICE = float(os.getenv("UNIVERSE_SHORT_MIN_PRICE",         "10.0"))

_SNAPSHOT_DIR = cfg.CACHE_DIR / "universe"

# ── ETF / fund name blocklist ─────────────────────────────────────────────────
_ETF_TOKENS = (
    " ETF", " ETN", " TRUST", " FUND", " SHARES", " INDEX", " BOND",
    " PROSHARES", " ISHARES", " DIREXION", " SPDR", " INVESCO", " VANGUARD",
    " ARK ", " GLOBAL X", " FIRST TRUST", " WISDOMTREE", " SCHWAB",
    " VANECK", " CBOE",
)


# ══════════════════════════════════════════════════════════════════════════════
# Readiness labels (Stage 7)
# ══════════════════════════════════════════════════════════════════════════════

class Readiness:
    """
    Readiness labels assigned to every strategy candidate.

    READY_NOW   Passes strategy hard filter, score ≥ threshold, data is fresh.
                → "Top Opportunities" / "Ready Now" on the dashboard.

    WATCH       Passes strategy hard filter, score ≥ threshold, but data is
                stale (last bar > _STALE_CALENDAR_DAYS old).
                → Still actionable but treat with caution.

    DEVELOPING  Passes strategy hard filter; score is within the developing
                band [threshold × _DEVELOP_RATIO, threshold).
                → "Developing Soon" section — one more push and it qualifies.

    GATED       Passes strategy hard filter but score is below the developing
                band.  Structurally interesting but not close to qualifying.
                → Not shown on dashboard by default; useful for deep research.

    REJECTED    Fails the strategy hard structural filter.
                → Excluded from candidate list (not surfaced).
    """
    READY_NOW  = "READY_NOW"
    WATCH      = "WATCH"
    DEVELOPING = "DEVELOPING"
    GATED      = "GATED"
    REJECTED   = "REJECTED"


# ══════════════════════════════════════════════════════════════════════════════
# Math helpers
# ══════════════════════════════════════════════════════════════════════════════

def _clip01(v: float) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except Exception:
        return 0.0


def _log_norm(v: float, lo: float, hi: float) -> float:
    if v <= 0 or hi <= lo:
        return 0.0
    try:
        lv = math.log10(float(v))
    except Exception:
        return 0.0
    return _clip01((lv - lo) / (hi - lo))


def _safe_pct(current: float, prior: float) -> float:
    return 0.0 if prior == 0 else ((current - prior) / prior) * 100.0


def _is_etf_or_fund(asset) -> bool:
    name = str(getattr(asset, "name", "") or "").upper()
    return any(tok in name for tok in _ETF_TOKENS)


def _symbol_ok(sym: str) -> bool:
    return bool(sym) and sym.isalpha() and 1 <= len(sym) <= 5


# ══════════════════════════════════════════════════════════════════════════════
# Bar quality helpers (Stage 2)
# ══════════════════════════════════════════════════════════════════════════════

def _strip_partial_today_bar(bars: List[Dict]) -> List[Dict]:
    """
    Remove today's in-progress daily bar from the bar list.

    Alpaca's daily bar endpoint includes today's partial bar when called during
    market hours.  For structural features (ATR%, MA, momentum, range position)
    we want stability → only use *completed* sessions.

    This is a no-op after market close or when _USE_COMPLETED_BARS_ONLY=False.
    """
    if not _USE_COMPLETED_BARS_ONLY or not bars:
        return bars
    today_str = date.today().isoformat()  # "2026-04-16"
    if bars[-1].get("date") == today_str:
        return bars[:-1]
    return bars


def _bars_stale(bars: Sequence[Dict]) -> bool:
    """
    Return True if the most recent bar is more than _STALE_CALENDAR_DAYS old.

    Uses calendar days as a rough proxy (no trading-day calendar needed).
    The default of 6 calendar days covers a long weekend + one holiday without
    false positives.
    """
    if not bars:
        return True
    try:
        last_date = datetime.strptime(bars[-1]["date"], "%Y-%m-%d").date()
        return (date.today() - last_date).days > _STALE_CALENDAR_DAYS
    except Exception:
        return True


# ══════════════════════════════════════════════════════════════════════════════
# Feature computation (Stage 3)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_features(symbol: str, bars: Sequence[Dict]) -> Optional[Dict]:
    """
    Compute ~18 structural features from completed daily bars.

    Returns a feature row dict, or None if there is insufficient data.
    All structural inputs (closes, highs, lows) should be from completed
    daily sessions (see _strip_partial_today_bar).
    """
    if not bars or len(bars) < 20:
        return None

    closes  = [float(b["close"])  for b in bars]
    highs   = [float(b["high"])   for b in bars]
    lows    = [float(b["low"])    for b in bars]
    volumes = [float(b["volume"]) for b in bars]

    current = closes[-1]
    if current <= 0:
        return None

    lb20 = min(20, len(bars)); lb14 = min(14, len(bars))
    lb60 = min(60, len(bars)); lb10 = min(10, len(bars))
    lb5  = min(5,  len(volumes))

    avg_vol_20   = sum(volumes[-lb20:]) / lb20
    avg_dvol_20  = sum(closes[i] * volumes[i] for i in range(len(bars) - lb20, len(bars))) / lb20
    current_dvol = current * volumes[-1]
    avg_range_14 = sum(highs[i] - lows[i] for i in range(len(bars) - lb14, len(bars))) / lb14
    atr_pct_14   = (avg_range_14 / current) * 100.0 if current > 0 else 0.0
    ret_20d      = _safe_pct(current, closes[-21]) if len(closes) >= 21 else 0.0
    ret_60d      = _safe_pct(current, closes[-61]) if len(closes) >= 61 else ret_20d
    ret_5d       = _safe_pct(current, closes[-6])  if len(closes) >= 6  else 0.0
    recent_vol5  = sum(volumes[-lb5:]) / lb5
    vol_ratio_5d = recent_vol5 / avg_vol_20 if avg_vol_20 > 0 else 0.0
    ma20         = sum(closes[-lb20:]) / lb20
    ma60         = sum(closes[-lb60:]) / lb60
    vs_ma20      = _safe_pct(current, ma20)
    vs_ma60      = _safe_pct(current, ma60)

    high20 = max(highs[-lb20:]); low20  = min(lows[-lb20:])
    high60 = max(highs[-lb60:]); low60  = min(lows[-lb60:])
    high10 = max(highs[-lb10:]); low10  = min(lows[-lb10:])

    d_hi20 = ((high20 - current) / high20) * 100.0 if high20 > 0 else 0.0
    d_lo20 = ((current - low20)  / low20)  * 100.0 if low20  > 0 else 0.0
    d_hi60 = ((high60 - current) / high60) * 100.0 if high60 > 0 else 0.0
    d_lo60 = ((current - low60)  / low60)  * 100.0 if low60  > 0 else 0.0
    rng10  = ((high10 - low10) / current)  * 100.0 if current > 0 else 0.0
    cpos10 = (current - low10) / (high10 - low10)  if high10 > low10 else 0.5

    abs_mvs = [abs(closes[i] - closes[i - 1]) for i in range(max(1, len(closes) - lb20 + 1), len(closes))]
    path20  = sum(abs_mvs)
    teff20  = abs(current - closes[-lb20]) / path20 if lb20 > 1 and path20 > 0 else 0.0

    # Normalised component signals
    liquidity = _log_norm(avg_dvol_20, 6.7, 9.7)
    movement  = _clip01((atr_pct_14 - 1.0) / 7.0)
    activity  = _clip01((vol_ratio_5d - 1.0) / 1.5)
    abs_trend = _clip01(abs(ret_20d) / 20.0)
    bull20    = _clip01(ret_20d  /  20.0); bull60 = _clip01(ret_60d  /  30.0)
    bear20    = _clip01(-ret_20d /  20.0); bear60 = _clip01(-ret_60d /  30.0)
    bp20      = _clip01((5.0  - d_hi20) /  5.0);  bp60  = _clip01((8.0 - d_hi60) /  8.0)
    lp60      = _clip01((8.0  - d_lo60) /  8.0)
    above20   = _clip01(vs_ma20  /  8.0);   above60 = _clip01(vs_ma60  / 12.0)
    below20   = _clip01(-vs_ma20 /  8.0);   below60 = _clip01(-vs_ma60 / 12.0)
    rng_tight = _clip01((18.0 - rng10) / 18.0)
    top10     = _clip01(cpos10);            bot10 = _clip01(1.0 - cpos10)
    tqual     = _clip01(teff20)
    rem_liq   = 1.0 - _clip01(abs(math.log10(max(avg_dvol_20, 1.0)) - 7.4) / 1.0)

    return {
        "symbol":               symbol,
        "price":                current,
        "avg_volume_20":        avg_vol_20,
        "avg_dollar_volume_20": avg_dvol_20,
        "current_dollar_volume": current_dvol,
        "atr_pct_14":           atr_pct_14,
        "return_20d_pct":       ret_20d,
        "return_60d_pct":       ret_60d,
        "return_5d_pct":        ret_5d,
        "volume_ratio_5d":      vol_ratio_5d,
        "close_vs_ma20_pct":    vs_ma20,
        "close_vs_ma60_pct":    vs_ma60,
        "dist_to_20d_high_pct": d_hi20,
        "dist_to_20d_low_pct":  d_lo20,
        "dist_to_60d_high_pct": d_hi60,
        "dist_to_60d_low_pct":  d_lo60,
        "range_pct_10":         rng10,
        "close_position_10":    cpos10,
        "trend_efficiency_20":  teff20,
        # Composite scores (all in [0, 1])
        "base_score":      0.45*liquidity + 0.25*movement + 0.15*activity + 0.15*abs_trend,
        "voyager_score":   0.24*liquidity + 0.22*bull20  + 0.16*bull60  + 0.12*bp60
                         + 0.10*above20  + 0.08*above60 + 0.08*tqual,
        "sniper_score":    0.18*liquidity + 0.20*movement + 0.18*activity
                         + 0.15*_clip01(ret_5d/8.0) + 0.12*_clip01(ret_20d/15.0)
                         + 0.10*bp20 + 0.07*top10,
        "short_score":     0.22*liquidity + 0.20*bear20  + 0.16*bear60  + 0.14*below20
                         + 0.10*below60  + 0.10*activity + 0.08*lp60,
        "remora_score":    0.28*rem_liq   + 0.22*movement + 0.20*activity
                         + 0.15*_clip01(abs(ret_5d)/8.0) + 0.08*_clip01(atr_pct_14/6.0)
                         + 0.07*_clip01(abs(vs_ma20)/8.0),
        "contrarian_score": 0.18*liquidity + 0.22*bear20 + 0.14*bear60
                          + 0.18*activity + 0.14*movement + 0.14*lp60,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Stage 4: Base filter
# ══════════════════════════════════════════════════════════════════════════════

def _passes_filters(row: Dict) -> bool:
    """Minimum price and liquidity gate applied to ALL strategies."""
    return (
        _MIN_PRICE <= row["price"] <= _MAX_PRICE
        and row["avg_volume_20"]       >= _MIN_AVG_VOL
        and row["avg_dollar_volume_20"] >= _MIN_AVG_DVOL
    )


# ══════════════════════════════════════════════════════════════════════════════
# Stage 5: Per-strategy hard structural filters
# ══════════════════════════════════════════════════════════════════════════════

def _voyager_filter(row: Dict) -> bool:
    """Uptrend / accumulation: price held well, near 60d high, not stretched."""
    d60 = row["dist_to_60d_high_pct"]
    if row["price"] < 10:
        return False
    if not (4.0 <= d60 <= 35.0 and row["close_vs_ma60_pct"] <= 18.0 and row["close_vs_ma20_pct"] >= -4.0):
        return False
    standard = (
        row["return_20d_pct"] >= 0 and row["return_60d_pct"] >= -2
        and row["close_vs_ma20_pct"] >= -1 and row["close_vs_ma60_pct"] >= -3
        and d60 <= 20 and row["trend_efficiency_20"] >= 0.2
    )
    recovery = (
        row["return_20d_pct"] >= 8.0
        and row["return_5d_pct"] >= 3.0
        and row["close_vs_ma20_pct"] >= 0
    )
    return standard or recovery


def _sniper_filter(row: Dict) -> bool:
    """Momentum breakout: tight range near highs, strong momentum, volume."""
    return (
        row["price"] >= 10 and row["atr_pct_14"] >= 1.5
        and row["return_20d_pct"] >= 2
        and row["close_vs_ma20_pct"] >= 0
        and row["dist_to_20d_high_pct"] <= 8
        and row["close_position_10"] >= 0.65
        and row["volume_ratio_5d"] >= 0.75
        and row["range_pct_10"] <= 18
    )


def _short_filter(row: Dict) -> bool:
    """
    Distribution / downtrend.  Short side has STRICTER structural requirements
    than long strategies to reduce squeeze risk and forced-cover exposure.

    Limitations that are NOT handled here (require FMP/broker data):
      - Earnings proximity:  checked by Veto Council EarningsAgent
      - Borrow availability: not available from Alpaca; treat as unknown
      - Short interest / days-to-cover: not available; treat as unknown
    The squeeze-risk exclusion below is a partial proxy only.
    """
    # Stricter liquidity floor: short thin names risk squeezes
    if row["avg_dollar_volume_20"] < _SHORT_MIN_DVOL:
        return False
    if row["price"] < _SHORT_MIN_PRICE:
        return False

    # Structural downtrend gate
    if not (
        row["return_20d_pct"] <= -2
        and row["close_vs_ma20_pct"] <= -1
        and row["dist_to_60d_low_pct"] <= 12
        and row["close_position_10"] <= 0.35
        and row["volume_ratio_5d"] >= 0.8
        and row["atr_pct_14"] >= 1.5       # needs daily range to capture short moves
    ):
        return False

    # Squeeze-risk exclusion: volume spike near 60d lows signals potential short squeeze
    # This is a structural proxy — does NOT replace borrow/short-interest checks.
    if row["volume_ratio_5d"] >= 2.5 and row["dist_to_60d_low_pct"] <= 3.0:
        return False

    return True


def _remora_filter(row: Dict) -> bool:
    """Mid-vol, elevated-activity names: moderate size, elevated recent vol."""
    return (
        8 <= row["price"] <= 200
        and 8_000_000 <= row["avg_dollar_volume_20"] <= 200_000_000
        and row["atr_pct_14"] >= 1.8
        and abs(row["return_5d_pct"]) >= 1.5
        and row["volume_ratio_5d"] >= 1.0
    )


def _contrarian_filter(row: Dict) -> bool:
    """Washed-out oversold names showing elevated activity — mean-reversion setups."""
    return (
        row["price"] >= 5
        and (row["return_20d_pct"] <= -8 or row["dist_to_60d_low_pct"] <= 5)
        and row["volume_ratio_5d"] >= 1.0
        and row["atr_pct_14"] >= 1.8
    )


# ══════════════════════════════════════════════════════════════════════════════
# Stage 7: Readiness labeling
# ══════════════════════════════════════════════════════════════════════════════

def _assign_readiness(
    score: float,
    threshold: float,
    passes_hard: bool,
    bars_stale: bool,
) -> Tuple[str, str]:
    """
    Return (readiness_label, key_reason) for a strategy candidate.

    Hierarchy:
      REJECTED   — fails hard structural filter (no further checks)
      READY_NOW  — passes hard + score ≥ threshold + data fresh
      WATCH      — passes hard + score ≥ threshold + data stale
      DEVELOPING — passes hard + score in [threshold × DEVELOP_RATIO, threshold)
      GATED      — passes hard + score below developing band
    """
    if not passes_hard:
        return Readiness.REJECTED, "fails_structural_filter"

    develop_floor = threshold * _DEVELOP_RATIO

    if score >= threshold:
        if bars_stale:
            return Readiness.WATCH, f"score={score:.3f} ≥ threshold but bars stale"
        return Readiness.READY_NOW, f"score={score:.3f} ≥ threshold={threshold:.3f}"

    if score >= develop_floor:
        return Readiness.DEVELOPING, (
            f"score={score:.3f} approaching threshold={threshold:.3f} "
            f"(need +{threshold - score:.3f})"
        )

    return Readiness.GATED, (
        f"score={score:.3f} below developing floor={develop_floor:.3f}"
    )


def _blocker_for(readiness: str) -> Optional[str]:
    """Map readiness label to a concise blocker string for the dashboard."""
    return {
        Readiness.READY_NOW:  None,
        Readiness.WATCH:      "stale_bars",
        Readiness.DEVELOPING: "score_below_threshold",
        Readiness.GATED:      "score_too_low",
        Readiness.REJECTED:   "structural_filter_failed",
    }.get(readiness, "unknown")


# ══════════════════════════════════════════════════════════════════════════════
# Stages 5-8: Combined routing function
# ══════════════════════════════════════════════════════════════════════════════

def _route_qualified(
    base_rows: List[Dict],
    score_key: str,
    limit: int,
    predicate: Callable[[Dict], bool],
    threshold: float,
    strategy_name: str,
    direction: str,
    curated_syms: set,
    bars_staleness: Dict[str, bool],
    last_bar_date: Dict[str, Optional[str]],
) -> Tuple[List[str], List[Dict]]:
    """
    Apply stages 5-8 for one strategy and return:
      qualified_syms  — ticker list for the strategy universe (READY_NOW + WATCH only)
      candidates      — full per-ticker records for all READY_NOW/WATCH/DEVELOPING rows
                        (GATED + REJECTED are excluded to keep the dashboard list clean)

    Threshold is enforced before top-N: if only 8 names qualify, route 8.
    Curated tickers receive a [watchlist] annotation but are NOT exempt from
    threshold or hard-filter checks.
    """
    raw_candidates: List[Dict] = []

    for row in base_rows:
        sym = row["symbol"]
        passes_hard = predicate(row)
        score = float(row[score_key])
        stale = bars_staleness.get(sym, False)

        readiness, key_reason = _assign_readiness(score, threshold, passes_hard, stale)

        # Curated watchlist tickers that fail are annotated, not silently promoted
        is_curated = sym in curated_syms
        if is_curated:
            key_reason = f"[watchlist] {key_reason}"

        # Only surface READY_NOW, WATCH, DEVELOPING — skip GATED and REJECTED
        if readiness in (Readiness.GATED, Readiness.REJECTED):
            continue

        raw_candidates.append({
            "symbol":          sym,
            "strategy":        strategy_name,
            "direction":       direction,
            "raw_score":       round(score, 4),
            "final_score":     round(score, 4),
            "readiness":       readiness,
            "key_reason":      key_reason,
            "blocker":         _blocker_for(readiness),
            "freshness_ts":    last_bar_date.get(sym),
            "price":           round(row["price"], 4),
            "avg_dollar_volume_20": round(row["avg_dollar_volume_20"], 2),
            "current_dollar_volume": round(row["current_dollar_volume"], 2),
            "atr_pct_14":      round(row["atr_pct_14"], 3),
            "return_5d_pct":    round(row["return_5d_pct"], 3),
            "return_20d_pct":  round(row["return_20d_pct"], 3),
            "volume_ratio_5d": round(row["volume_ratio_5d"], 3),
            "is_curated":      is_curated,
            "base_score":      round(row["base_score"], 4),
        })

    # Stage 8: qualify = READY_NOW or WATCH, sort by score, cap at limit
    ready = [c for c in raw_candidates if c["readiness"] in (Readiness.READY_NOW, Readiness.WATCH)]
    ready.sort(key=lambda c: (-c["final_score"], -c["base_score"], c["symbol"]))
    ready = ready[:max(0, limit)]
    qualified_syms = [c["symbol"] for c in ready]

    # All displayable candidates (READY_NOW + WATCH + DEVELOPING) for dashboard
    displayable = raw_candidates  # GATED/REJECTED already filtered above

    return qualified_syms, displayable


# ══════════════════════════════════════════════════════════════════════════════
# UniverseBuilder
# ══════════════════════════════════════════════════════════════════════════════

class UniverseBuilder:
    """
    Build and cache a dynamic market snapshot with per-strategy ticker pools.

    Usage:
        snap = get_universe_builder().build_snapshot()
        sniper_tickers = snap["sniper_universe"]   # qualified momentum-ready tickers

    Curated overlay: tickers in WATCHLIST_TICKERS env var (comma-separated) survive
    the base-1000 cap.  They still pass through ALL filters and thresholds — they are
    never silently promoted past hard rules.

    Fallback: if Alpaca discovery fails completely, the snapshot is marked as fallback
    and strategy pools are returned empty (not silently filled with the curated list).
    """

    def __init__(self, curated: Optional[List[str]] = None):
        self._alpaca       = get_alpaca()
        self._last_snap:   Optional[Dict] = None
        self._last_snap_ts = 0.0

        env_watchlist = [
            t.strip().upper()
            for t in os.getenv("WATCHLIST_TICKERS", "").split(",")
            if t.strip()
        ]
        self._curated: List[str] = list({
            s for s in (env_watchlist + (curated or []))
            if _symbol_ok(s)
        })

    # ── Public API ─────────────────────────────────────────────────────────────

    def build_snapshot(self, force: bool = False) -> Dict:
        """Return a cached snapshot (rebuilt every TTL seconds)."""
        now = time.time()
        if (
            not force
            and self._last_snap is not None
            and (now - self._last_snap_ts) < _SNAPSHOT_TTL
        ):
            # Attach live cache-age field without rebuilding
            snap = dict(self._last_snap)
            snap["cache_age_seconds"] = round(now - self._last_snap_ts, 1)
            return snap

        snap               = self._build_fresh()
        self._last_snap    = snap
        self._last_snap_ts = now
        self._persist(snap)
        return snap

    # ── Internal pipeline ──────────────────────────────────────────────────────

    def _build_fresh(self) -> Dict:
        t0 = time.time()
        warnings: List[str] = []
        logger.info("Universe [v%s]: starting full discovery…", PIPELINE_VERSION)

        # ── STAGE 1: Symbol discovery ──────────────────────────────────────────
        discovered = self._discover_symbols()
        n_discovered = len(discovered)
        if not discovered:
            msg = "asset_discovery_failed — using curated watchlist as fallback"
            warnings.append(msg)
            logger.warning("Universe [S1]: %s", msg)
            return self._empty_snapshot("asset_discovery_failed", warnings=warnings)

        # Merge curated overlay (symbols not already in the discovered set)
        combined: List[str] = list(discovered)
        seen: set = set(combined)
        curated_added: List[str] = []
        for sym in self._curated:
            if sym not in seen:
                combined.append(sym)
                seen.add(sym)
                curated_added.append(sym)
        curated_syms: set = set(self._curated)

        logger.info(
            "Universe [S1]: discovered=%d  curated_merged=%d  total=%d",
            n_discovered, len(curated_added), len(combined),
        )

        # ── STAGE 2: Fetch bars + strip partial today bar ──────────────────────
        logger.info(
            "Universe [S2]: fetching %d-day bars for %d symbols (chunks=%d, completed_only=%s)…",
            _DAYS_BACK, len(combined), _CHUNK_SIZE, _USE_COMPLETED_BARS_ONLY,
        )
        raw_bars_map = self._alpaca.get_daily_bars_batch(
            combined, days=_DAYS_BACK, chunk_size=_CHUNK_SIZE
        )
        bars_map: Dict[str, List[Dict]] = {
            sym: _strip_partial_today_bar(bars)
            for sym, bars in raw_bars_map.items()
        }

        # Staleness and last-bar metadata
        bars_staleness: Dict[str, bool]          = {}
        last_bar_date:  Dict[str, Optional[str]] = {}
        for sym, bars in bars_map.items():
            bars_staleness[sym] = _bars_stale(bars)
            last_bar_date[sym]  = bars[-1]["date"] if bars else None

        n_stale = sum(1 for v in bars_staleness.values() if v)
        if n_stale > 0:
            warnings.append(f"{n_stale} tickers have stale bars (>{_STALE_CALENDAR_DAYS} calendar days)")
            logger.info("Universe [S2]: %d tickers with stale bars flagged", n_stale)

        # ── STAGE 3: Feature computation + STAGE 4: Base filtering ────────────
        metrics: List[Dict] = []
        exc_data = exc_filter = 0

        for sym in combined:
            bars = bars_map.get(sym.upper()) or []
            row  = _compute_features(sym, bars)   # Stage 3
            if row is None:
                exc_data += 1
                continue
            if not _passes_filters(row):           # Stage 4
                exc_filter += 1
                continue
            metrics.append(row)

        if not metrics:
            return self._empty_snapshot(
                "no_symbols_passed_filters",
                source_count=len(combined),
                warnings=warnings,
            )

        logger.info(
            "Universe [S3/S4]: features=%d  excl_data=%d  excl_filter=%d",
            len(metrics), exc_data, exc_filter,
        )

        # Sort by base score; curated tickers survive the base-1000 cap
        metrics.sort(key=lambda r: (-r["base_score"], r["symbol"]))
        base_set      = {r["symbol"] for r in metrics[:_BASE_LIMIT]}
        curated_extra = [r for r in metrics if r["symbol"] in curated_syms and r["symbol"] not in base_set]
        base_rows     = metrics[:_BASE_LIMIT] + curated_extra
        base_rows.sort(key=lambda r: (-r["base_score"], r["symbol"]))
        base_syms = [r["symbol"] for r in base_rows]

        logger.info(
            "Universe [S4]: base_universe=%d (top_1000=%d + curated_extra=%d)",
            len(base_syms), min(len(metrics), _BASE_LIMIT), len(curated_extra),
        )

        # ── STAGES 5-8: Per-strategy hard filter → threshold → label → rank ───
        strategy_configs: List[Tuple] = [
            ("voyager",    "voyager_score",    _VOYAGER_LIMIT,    _voyager_filter,    "LONG"),
            ("sniper",     "sniper_score",     _SNIPER_LIMIT,     _sniper_filter,     "LONG"),
            ("short",      "short_score",      _SHORT_LIMIT,      _short_filter,      "SHORT"),
            ("remora",     "remora_score",     _REMORA_LIMIT,     _remora_filter,     "LONG"),
            ("contrarian", "contrarian_score", _CONTRARIAN_LIMIT, _contrarian_filter, "LONG"),
        ]

        all_strategy_candidates: List[Dict] = []
        routed:              Dict[str, List[str]] = {}
        n_developing:        Dict[str, int]       = {}

        for strat, score_key, limit, predicate, direction in strategy_configs:
            threshold = _SCORE_THRESHOLDS[strat]
            syms, candidates = _route_qualified(
                base_rows       = base_rows,
                score_key       = score_key,
                limit           = limit,
                predicate       = predicate,
                threshold       = threshold,
                strategy_name   = strat,
                direction       = direction,
                curated_syms    = curated_syms,
                bars_staleness  = bars_staleness,
                last_bar_date   = last_bar_date,
            )
            routed[strat] = syms
            all_strategy_candidates.extend(candidates)
            n_developing[strat] = sum(1 for c in candidates if c["readiness"] == Readiness.DEVELOPING)

            if not syms:
                msg = f"{strat}: 0 names qualified above score threshold {threshold:.2f}"
                warnings.append(msg)
                logger.warning("Universe [S8]: %s", msg)
            else:
                logger.info(
                    "Universe [S8]: %-12s → %d qualified  %d developing  (threshold=%.2f)",
                    strat, len(syms), n_developing[strat], threshold,
                )

        # ── Metadata for base universe (for legacy consumers + dashboard) ──────
        metadata = {
            r["symbol"]: {
                "price":             round(r["price"], 4),
                "avg_volume_20":     int(r["avg_volume_20"]),
                "avg_dollar_vol_20": round(r["avg_dollar_volume_20"], 2),
                "atr_pct_14":        round(r["atr_pct_14"], 3),
                "return_20d_pct":    round(r["return_20d_pct"], 3),
                "return_5d_pct":     round(r["return_5d_pct"],  3),
                "volume_ratio_5d":   round(r["volume_ratio_5d"], 3),
                "bars_stale":        bars_staleness.get(r["symbol"], False),
                "last_bar_date":     last_bar_date.get(r["symbol"]),
                "scores": {
                    "base":       round(r["base_score"],       4),
                    "voyager":    round(r["voyager_score"],    4),
                    "sniper":     round(r["sniper_score"],     4),
                    "short":      round(r["short_score"],      4),
                    "remora":     round(r["remora_score"],     4),
                    "contrarian": round(r["contrarian_score"], 4),
                },
            }
            for r in base_rows
        }

        elapsed = round(time.time() - t0, 1)
        logger.info(
            "Universe built in %.1fs — base=%d  sniper=%d  voyager=%d  "
            "short=%d  remora=%d  contrarian=%d",
            elapsed, len(base_syms),
            len(routed["sniper"]), len(routed["voyager"]),
            len(routed["short"]), len(routed["remora"]),
            len(routed["contrarian"]),
        )

        # ── STAGE 9: Snapshot with full provenance ─────────────────────────────
        built_at = datetime.now(timezone.utc).isoformat()
        return {
            # ── Strategy pools (symbol lists) ──────────────────────────────────
            "base_universe":       base_syms,
            "voyager_universe":    routed["voyager"],
            "sniper_universe":     routed["sniper"],
            "short_universe":      routed["short"],
            "remora_universe":     routed["remora"],
            "contrarian_universe": routed["contrarian"],

            # ── Dashboard-ready per-candidate records ──────────────────────────
            # Each record: symbol, strategy, direction, raw_score, final_score,
            # readiness, key_reason, blocker, freshness_ts, price, atr_pct_14,
            # return_20d_pct, volume_ratio_5d, is_curated, base_score
            "strategy_candidates": all_strategy_candidates,

            # ── Convenience shortlist ──────────────────────────────────────────
            "top_symbols": base_syms[:25],

            # ── Per-ticker metrics for the base universe ───────────────────────
            "metadata": metadata,

            # ── Full provenance / explainability (STAGE 9) ────────────────────
            "summary": {
                # Identity
                "pipeline_version":  PIPELINE_VERSION,
                "built_at":          built_at,
                "source":            "alpaca_dynamic_snapshot",
                "fallback_used":     False,
                "fallback_reason":   None,
                # Configuration used for this build
                "completed_bars_only":   _USE_COMPLETED_BARS_ONLY,
                "score_thresholds":      dict(_SCORE_THRESHOLDS),
                "short_min_dollar_vol":  _SHORT_MIN_DVOL,
                "short_min_price":       _SHORT_MIN_PRICE,
                # Stage counts
                "source_assets":            n_discovered + len(curated_added),
                "curated_requested":        len(self._curated),
                "curated_merged_above_1000": len(curated_extra),
                "excluded_for_data":        exc_data,
                "excluded_for_filters":     exc_filter,
                "passed_basic_filters":     len(metrics),
                "base_universe_size":       len(base_syms),
                "stale_bars_count":         n_stale,
                # Per-strategy qualified counts
                "strategy_sizes": {
                    "voyager":    len(routed["voyager"]),
                    "sniper":     len(routed["sniper"]),
                    "short":      len(routed["short"]),
                    "remora":     len(routed["remora"]),
                    "contrarian": len(routed["contrarian"]),
                },
                # Per-strategy developing counts (near-threshold setups)
                "strategy_developing": dict(n_developing),
                # Build performance
                "build_seconds": elapsed,
                # Warnings and anomalies
                "warnings": warnings,
            },

            # ── Legacy top-level keys (kept for backward compatibility) ────────
            "generated_at":    built_at,
            "source":          "alpaca_dynamic_snapshot",
            "fallback_used":   False,
            "fallback_reason": None,
            "cache_age_seconds": 0,
        }

    def _discover_symbols(self) -> List[str]:
        """Stage 1: enumerate all active tradable US equities via Alpaca."""
        try:
            from alpaca.trading.requests import GetAssetsRequest
            from alpaca.trading.enums import AssetClass, AssetStatus
            req    = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
            assets = self._alpaca._trading.get_all_assets(filter=req)
        except Exception as exc:
            logger.warning("Universe [S1]: get_all_assets failed: %s", exc)
            return []

        syms: List[str] = []
        for asset in assets or []:
            sym = str(getattr(asset, "symbol", "") or "").upper().strip()
            if not _symbol_ok(sym):
                continue
            if not bool(getattr(asset, "tradable", False)):
                continue
            if _is_etf_or_fund(asset):
                continue
            if "OTC" in str(getattr(asset, "exchange", "") or "").upper():
                continue
            syms.append(sym)

        syms = sorted(set(syms))
        logger.info("Universe [S1]: discovered %d active tradable equities", len(syms))
        return syms

    def _empty_snapshot(
        self,
        reason: str,
        source_count: int = 0,
        warnings: Optional[List[str]] = None,
    ) -> Dict:
        """
        Return a clearly-marked fallback snapshot when discovery or filtering fails.

        IMPORTANT: strategy pools are returned EMPTY in fallback mode.
        The curated list is provided as base_universe only.
        Scanners / dashboard must check fallback_used=True before acting.
        """
        fallback_base = self._curated or []
        warnings = list(warnings or [])
        built_at = datetime.now(timezone.utc).isoformat()
        logger.warning(
            "Universe: returning FALLBACK snapshot (reason=%s, curated=%d tickers)",
            reason, len(fallback_base),
        )
        return {
            # Base universe = curated list only (no scoring/filtering applied)
            "base_universe":       fallback_base,
            # Strategy pools are EMPTY in fallback — do not trade off these
            "voyager_universe":    [],
            "sniper_universe":     [],
            "short_universe":      [],
            "remora_universe":     [],
            "contrarian_universe": [],
            "strategy_candidates": [],
            "top_symbols":         fallback_base[:25],
            "metadata":            {},
            "summary": {
                "pipeline_version":  PIPELINE_VERSION,
                "built_at":          built_at,
                "source":            "alpaca_dynamic_snapshot",
                "fallback_used":     True,
                "fallback_reason":   reason,
                "completed_bars_only":   _USE_COMPLETED_BARS_ONLY,
                "score_thresholds":      dict(_SCORE_THRESHOLDS),
                "short_min_dollar_vol":  _SHORT_MIN_DVOL,
                "short_min_price":       _SHORT_MIN_PRICE,
                "source_assets":         source_count,
                "curated_requested":     len(self._curated),
                "curated_merged_above_1000": 0,
                "excluded_for_data":     0,
                "excluded_for_filters":  0,
                "passed_basic_filters":  0,
                "base_universe_size":    len(fallback_base),
                "stale_bars_count":      0,
                "strategy_sizes":        {k: 0 for k in _SCORE_THRESHOLDS},
                "strategy_developing":   {k: 0 for k in _SCORE_THRESHOLDS},
                "build_seconds":         0,
                "warnings":              warnings,
            },
            "generated_at":    built_at,
            "source":          "alpaca_dynamic_snapshot",
            "fallback_used":   True,
            "fallback_reason": reason,
            "cache_age_seconds": 0,
        }

    def _persist(self, snap: Dict) -> None:
        """Stage 9: atomically write snapshot JSON to disk."""
        try:
            _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            latest = _SNAPSHOT_DIR / "universe_snapshot_latest.json"
            with tempfile.NamedTemporaryFile(
                "w", dir=str(_SNAPSHOT_DIR), delete=False, suffix=".json", encoding="utf-8"
            ) as tmp:
                json.dump(snap, tmp, indent=2, sort_keys=True)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp.name, str(latest))
            logger.debug("Universe snapshot persisted → %s", latest)
        except Exception as exc:
            logger.warning("Universe: could not persist snapshot: %s", exc)


# ── Module-level singleton ────────────────────────────────────────────────────

_builder: Optional[UniverseBuilder] = None


def get_universe_builder() -> UniverseBuilder:
    global _builder
    if _builder is None:
        _builder = UniverseBuilder()
    return _builder
