"""
core/signal_hygiene.py — Phase 1G.2 paper-signal hygiene helpers.

This module is pure-helper and cache-only. It does NOT:
  - call providers
  - change scanner scoring
  - change paper governance
  - change execution
  - mutate historical evidence
  - enable live trading

It provides three pre-log checks that sit between the scanner and the
existing ``core.paper_validation.log_paper_signal`` writer:

  1. ``setup_state_hash`` / ``find_recent_duplicate`` — drop redundant
     same-day re-emissions of the same (strategy × ticker × signal_version
     × setup_state) signal.
  2. ``compute_presize_verdict`` — compare proposed notional against the
     live single-name heat cap. Oversized candidates are logged as
     diagnostic rows (status ``presize_blocked``) instead of being routed
     to paper governance, which would just reject them.
  3. ``compute_short_regime_verdict`` — implement the SHORT_A doctrine
     gate (suppress structural shorts when SPY > 50d MA, VIX < 20, and
     the regime forecast favors bull continuation / bull pullback)
     unless the signal carries an explicit event-driven exception.

Counters are aggregated per-process by :class:`HygieneCounters` and
written to ``cache/research/signal_hygiene_latest.json`` for the
dashboard / MCP audit to consume. No DB writes happen here beyond the
existing ``log_paper_signal`` path the caller invokes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────────────

DEDUP_WINDOW_HOURS_DEFAULT = 24
MAX_SINGLE_NAME_PCT_DEFAULT = 0.05  # mirrors execution.portfolio_risk.MAX_SINGLE_NAME_PCT
SHORT_REGIME_VIX_CEILING = 20.0
DEFAULT_EQUITY_HINT_FALLBACK = 100_000.0  # only used when no heartbeat is on disk

# Status values written into ``paper_signals.status`` by the hygiene layer.
STATUS_PRESIZE_BLOCKED = "presize_blocked"
STATUS_REGIME_SUPPRESSED = "regime_suppressed"

# Tokens that signal a bullish bias on the regime forecast headline.
_BULL_BIAS_TOKENS = ("constructive", "bullish", "buy", "bull")

# Regime names whose presence should suppress structural SHORT_A signals.
_BULL_REGIMES = frozenset({
    "bull continuation",
    "bull pullback / buy-the-dip",
    "bull pullback / buy the dip",
})

# Regime cluster strings (from main._paper_signal_payload) that count as
# explicit event-driven exceptions and bypass the regime gate.
EVENT_DRIVEN_REGIME_CLUSTERS = frozenset({
    "earnings_event_short",
    "earnings_event_long",
    "guidance_cut_event",
})


# ── Setup-state bucketing ────────────────────────────────────────────────────


def _score_bucket(score: Optional[float]) -> str:
    if score is None:
        return "missing"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "missing"
    if s >= 95:
        return "95+"
    if s >= 90:
        return "90-94"
    if s >= 85:
        return "85-89"
    if s >= 80:
        return "80-84"
    if s >= 75:
        return "75-79"
    if s >= 70:
        return "70-74"
    if s >= 65:
        return "65-69"
    if s >= 60:
        return "60-64"
    return "<60"


def _rr_bucket(rr: Optional[float]) -> str:
    if rr is None:
        return "missing"
    try:
        r = float(rr)
    except (TypeError, ValueError):
        return "missing"
    if r >= 4.0:
        return "4+"
    if r >= 3.0:
        return "3-4"
    if r >= 2.5:
        return "2.5-3"
    if r >= 2.0:
        return "2-2.5"
    if r >= 1.5:
        return "1.5-2"
    return "<1.5"


def _entry_bucket(entry: Optional[float]) -> str:
    """Bucket entry price coarsely enough that a fresh quote of the same
    setup hashes the same way, while a meaningfully moved price emits a
    fresh signal. Buckets widen with price."""
    if entry is None:
        return "missing"
    try:
        e = float(entry)
    except (TypeError, ValueError):
        return "missing"
    if e <= 0:
        return "missing"
    if e >= 100:
        # snap to 1% of price (≥$1 step)
        step = round(e / 100.0)
        if step <= 0:
            step = 1
        return f"{int(round(e / step) * step)}"
    if e >= 10:
        return f"{round(e, 1)}"
    return f"{round(e, 2)}"


def _regime_cluster(payload: Mapping[str, Any]) -> str:
    rc = (payload.get("regime_context") or {}).get("regime_cluster")
    return str(rc or "").strip().lower()


def setup_state_dict(opp: Mapping[str, Any], payload: Mapping[str, Any]) -> Dict[str, str]:
    """Buckets used to compute the dedup hash. Returned for debugging /
    audit only — the hash is the canonical identity."""
    return {
        "score_bucket": _score_bucket(opp.get("score")),
        "side": str(opp.get("direction", "")).upper(),
        "rr_bucket": _rr_bucket(opp.get("risk_reward")),
        "entry_bucket": _entry_bucket(opp.get("entry_price")),
        "regime_cluster": _regime_cluster(payload),
    }


def setup_state_hash(opp: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """12-char stable hash of the buckets in :func:`setup_state_dict`."""
    parts = setup_state_dict(opp, payload)
    raw = "|".join(f"{k}={parts[k]}" for k in sorted(parts))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# ── Dedup query against existing paper_signals rows ──────────────────────────


@dataclass(frozen=True)
class DedupVerdict:
    suppress: bool
    reason: str = ""
    matched_id: Optional[str] = None
    matched_logged_at: Optional[str] = None


# Statuses that should NOT be considered for dedup matching. Hygiene
# diagnostic rows shouldn't suppress a fresh real-signal emission, and
# resolved/closed signals shouldn't suppress a new same-day setup that
# re-fires after exit.
_DEDUP_EXCLUDE_STATUSES = frozenset({
    "presize_blocked",
    "regime_suppressed",
    "duplicate",
    "observe_only",
})


def find_recent_duplicate(
    *,
    strategy: str,
    ticker: str,
    signal_version: str,
    setup_state_hash_value: str,
    db_path: Optional[Path] = None,
    window_hours: float = DEDUP_WINDOW_HOURS_DEFAULT,
    now_utc: Optional[datetime] = None,
) -> DedupVerdict:
    """Look for an existing paper_signals row matching the dedup key.

    A "match" means same strategy + ticker + signal_version with either
    the same ``setup_state_hash`` written into the row (preferred path)
    or, for rows logged before the migration, a recomputed hash from the
    stored bucket fields. The window is a rolling N hours, defaulting to
    24h, so a same-setup re-emission on the next UTC day is allowed to
    pass.
    """
    if not strategy or not ticker:
        return DedupVerdict(False, "missing key")
    db_path = db_path or _resolve_db_path()
    if not db_path or not db_path.exists():
        # Brand-new install / test fixture without a DB — never suppress.
        return DedupVerdict(False, "db unavailable")
    now_utc = now_utc or datetime.now(timezone.utc)
    cutoff_iso = (now_utc.replace(microsecond=0)
                  - _hours_delta(window_hours)).isoformat()
    try:
        with sqlite3.connect(str(db_path)) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_signals)")}
            has_hash_col = "setup_state_hash" in cols
            select_sql = (
                "SELECT id, logged_at, status, score, side, risk_reward, "
                "entry_price, regime_context"
                + (", setup_state_hash" if has_hash_col else "")
                + " FROM paper_signals "
                "WHERE strategy=? AND ticker=? AND signal_version=? "
                "AND logged_at >= ? ORDER BY logged_at DESC LIMIT 200"
            )
            rows = conn.execute(
                select_sql,
                (strategy.upper(), ticker.upper(), signal_version, cutoff_iso),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        logger.debug("dedup query failed: %s", exc)
        return DedupVerdict(False, "query error")
    for row in rows:
        rid, logged_at, status, score, side, rr, entry, regime_blob = row[:8]
        stored_hash = row[8] if has_hash_col and len(row) > 8 else None
        if status in _DEDUP_EXCLUDE_STATUSES:
            continue
        if stored_hash and stored_hash == setup_state_hash_value:
            return DedupVerdict(
                True, "same setup_state_hash within window",
                matched_id=rid, matched_logged_at=logged_at,
            )
        if not stored_hash:
            # Recompute hash from row fields. Legacy rows pre-migration
            # carry their bucket inputs but not the hash itself.
            try:
                regime_dict = json.loads(regime_blob or "{}")
            except json.JSONDecodeError:
                regime_dict = {}
            recomputed = setup_state_hash(
                {"score": score, "direction": side, "risk_reward": rr,
                 "entry_price": entry},
                {"regime_context": regime_dict},
            )
            if recomputed == setup_state_hash_value:
                return DedupVerdict(
                    True, "same recomputed setup_state hash within window",
                    matched_id=rid, matched_logged_at=logged_at,
                )
    return DedupVerdict(False, "")


def _hours_delta(hours: float):
    from datetime import timedelta
    return timedelta(hours=float(hours))


def _resolve_db_path() -> Optional[Path]:
    try:
        import core.config as cfg
        return Path(cfg.DB_PATH)
    except Exception:
        return None


# ── Pre-size verdict ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PresizeVerdict:
    ok: bool
    proposed_notional: Optional[float]
    cap_notional: Optional[float]
    size_ratio: Optional[float]
    equity_used: Optional[float]
    equity_source: str
    reason: str = ""


def compute_presize_verdict(
    opp: Mapping[str, Any],
    *,
    equity: Optional[float],
    equity_source: str = "unknown",
    max_single_name_pct: float = MAX_SINGLE_NAME_PCT_DEFAULT,
) -> PresizeVerdict:
    """Compare proposed notional against the single-name heat cap.

    Degrades conservatively when inputs are missing:
      - missing equity → block (we cannot prove we are inside the cap)
      - missing shares or entry → pass through (scanner may not have
        sized yet; downstream governance will still check)
    """
    shares = opp.get("shares")
    entry = opp.get("entry_price")
    try:
        shares_f = float(shares) if shares is not None else None
    except (TypeError, ValueError):
        shares_f = None
    try:
        entry_f = float(entry) if entry is not None else None
    except (TypeError, ValueError):
        entry_f = None

    if shares_f is None or entry_f is None or shares_f <= 0 or entry_f <= 0:
        # Nothing to evaluate — let governance handle it.
        return PresizeVerdict(
            True, None, None, None, equity, equity_source,
            reason="proposed notional unknown — pre-size check skipped",
        )

    proposed = shares_f * entry_f

    if equity is None or equity <= 0:
        # Conservative: block when we cannot verify the cap. Phase 1G.2
        # is hygiene; if we don't have equity we can't say the trade is
        # in-bounds and we should not let it pollute the paper ledger.
        return PresizeVerdict(
            False, proposed, None, None, equity, equity_source,
            reason="equity hint unavailable — cannot verify single-name cap",
        )

    cap = float(equity) * float(max_single_name_pct)
    ratio = proposed / cap if cap > 0 else float("inf")
    if proposed > cap:
        return PresizeVerdict(
            False, proposed, cap, ratio, equity, equity_source,
            reason=(
                f"proposed notional ${proposed:,.0f} exceeds single-name cap "
                f"${cap:,.0f} ({ratio:.1f}x of cap at "
                f"{max_single_name_pct*100:.1f}% × equity ${float(equity):,.0f})"
            ),
        )
    return PresizeVerdict(True, proposed, cap, ratio, equity, equity_source, reason="")


# ── SHORT_A regime gate ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class RegimeGateVerdict:
    ok: bool
    reason: str = ""
    spy_above_ma50: Optional[bool] = None
    vix: Optional[float] = None
    current_regime: Optional[str] = None
    bias_5d: Optional[str] = None
    event_exception: bool = False


def _bias_is_bullish(label: Optional[str]) -> bool:
    if not label:
        return False
    s = str(label).strip().lower()
    return any(t in s for t in _BULL_BIAS_TOKENS)


def _regime_is_bull(regime: Optional[str]) -> bool:
    if not regime:
        return False
    return str(regime).strip().lower() in _BULL_REGIMES


def compute_short_regime_verdict(
    *,
    strategy: str,
    payload: Mapping[str, Any],
    opp: Mapping[str, Any],
    forecast_snapshot: Optional[Mapping[str, Any]],
    vix_ceiling: float = SHORT_REGIME_VIX_CEILING,
) -> RegimeGateVerdict:
    """Return a verdict for the SHORT_A documented regime gate.

    Only applies when ``strategy`` normalizes to ``SHORT``. Other
    strategies always pass through. The gate is purely diagnostic: it
    does not change scoring math or governance. Missing forecast inputs
    suppress conservatively (per doctrine — we cannot prove the tape is
    safe for structural shorts).
    """
    if (strategy or "").upper() != "SHORT":
        return RegimeGateVerdict(True, reason="not SHORT strategy")

    # Event-driven exception: the signal carries an explicit catalyst.
    cluster = _regime_cluster(payload)
    explicit_event = bool(opp.get("event_date") or opp.get("event_type"))
    if cluster in EVENT_DRIVEN_REGIME_CLUSTERS and explicit_event:
        return RegimeGateVerdict(
            True, reason=f"event-driven exception ({cluster})",
            event_exception=True,
        )

    if not forecast_snapshot:
        return RegimeGateVerdict(
            False, reason="regime forecast unavailable — suppressing structural short conservatively",
        )

    headline = forecast_snapshot.get("headline") or {}
    market_trend = forecast_snapshot.get("market_trend") or {}
    volatility = forecast_snapshot.get("volatility") or {}
    spy = market_trend.get("SPY") or {}
    spy_above_ma50 = spy.get("above_ma50")
    try:
        vix = float(volatility.get("vix")) if volatility.get("vix") is not None else None
    except (TypeError, ValueError):
        vix = None
    current_regime = headline.get("current_regime")
    bias_5d = headline.get("bias_5d")

    # Risk-off / stress regimes: do not suppress by this rule.
    if current_regime and any(
        tok in str(current_regime).lower()
        for tok in ("risk-off", "stress", "volatility expansion", "bear")
    ):
        return RegimeGateVerdict(
            True, reason=f"defensive regime ({current_regime}) — gate does not apply",
            spy_above_ma50=spy_above_ma50, vix=vix,
            current_regime=current_regime, bias_5d=bias_5d,
        )

    # Conservative: if any required input is missing, suppress.
    if spy_above_ma50 is None or vix is None or not current_regime:
        return RegimeGateVerdict(
            False,
            reason=(
                "regime inputs incomplete "
                f"(spy_above_ma50={spy_above_ma50}, vix={vix}, regime={current_regime!r}) "
                "— suppressing structural short conservatively"
            ),
            spy_above_ma50=spy_above_ma50, vix=vix,
            current_regime=current_regime, bias_5d=bias_5d,
        )

    bull_regime = _regime_is_bull(current_regime) or _bias_is_bullish(bias_5d)
    if bool(spy_above_ma50) and vix < vix_ceiling and bull_regime:
        return RegimeGateVerdict(
            False,
            reason=(
                f"bull tape (SPY>50d_MA, VIX={vix:.1f}<{vix_ceiling:.0f}, "
                f"regime={current_regime!r}, bias_5d={bias_5d!r}) — "
                "structural SHORT_A suppressed per doctrine"
            ),
            spy_above_ma50=spy_above_ma50, vix=vix,
            current_regime=current_regime, bias_5d=bias_5d,
        )

    return RegimeGateVerdict(
        True, reason="bull-tape gate not breached",
        spy_above_ma50=spy_above_ma50, vix=vix,
        current_regime=current_regime, bias_5d=bias_5d,
    )


# ── Equity hint reader ───────────────────────────────────────────────────────


def read_equity_hint(
    *,
    heartbeat_path: Optional[Path] = None,
    broker_snapshot_path: Optional[Path] = None,
    default: Optional[float] = None,
) -> Tuple[Optional[float], str]:
    """Best-effort equity read. Order:
      1. Caller-supplied ``default`` if explicit (rare).
      2. ``logs/trader_heartbeat.json`` `equity` field.
      3. Broker snapshot ``equity`` field if present.
      4. None (caller decides how to degrade).
    Returns (equity, source). Never raises.
    """
    try:
        import core.config as cfg
        hb = heartbeat_path or (cfg.LOG_DIR / "trader_heartbeat.json")
        bs = broker_snapshot_path or Path("cache/state/broker_positions_snapshot.json")
    except Exception:
        hb = heartbeat_path
        bs = broker_snapshot_path

    if default is not None:
        return float(default), "explicit"

    if hb and Path(hb).exists():
        try:
            data = json.loads(Path(hb).read_text(encoding="utf-8"))
            eq = data.get("equity")
            if eq is not None:
                return float(eq), "heartbeat"
        except Exception as exc:
            logger.debug("heartbeat equity read failed: %s", exc)

    if bs and Path(bs).exists():
        try:
            data = json.loads(Path(bs).read_text(encoding="utf-8"))
            eq = data.get("equity")
            if eq is not None:
                return float(eq), "broker_snapshot"
        except Exception as exc:
            logger.debug("broker snapshot equity read failed: %s", exc)

    return None, "unavailable"


# ── Counters ─────────────────────────────────────────────────────────────────


@dataclass
class HygieneCounters:
    """Per-process aggregator. Reset on daemon restart by design — the
    sidecar is overwritten each scan cycle, so a fresh process simply
    starts again at zero."""
    duplicate_suppressed_count: int = 0
    duplicate_suppressed_by_ticker: Counter = field(default_factory=Counter)
    duplicate_suppressed_by_strategy: Counter = field(default_factory=Counter)
    latest_suppressed_reason: str = ""

    presize_rejected_count: int = 0
    presize_rejected_by_ticker: Counter = field(default_factory=Counter)
    presize_rejected_by_strategy: Counter = field(default_factory=Counter)
    presize_latest_size_ratio: Optional[float] = None
    presize_latest_proposed_notional: Optional[float] = None
    presize_latest_cap_notional: Optional[float] = None

    short_regime_suppressed_count: int = 0
    short_regime_suppressed_by_ticker: Counter = field(default_factory=Counter)
    short_regime_latest_reason: str = ""

    def record_dup(self, strategy: str, ticker: str, reason: str) -> None:
        self.duplicate_suppressed_count += 1
        self.duplicate_suppressed_by_strategy[(strategy or "").upper()] += 1
        self.duplicate_suppressed_by_ticker[(ticker or "").upper()] += 1
        self.latest_suppressed_reason = reason

    def record_presize(
        self, strategy: str, ticker: str, verdict: "PresizeVerdict",
    ) -> None:
        self.presize_rejected_count += 1
        self.presize_rejected_by_strategy[(strategy or "").upper()] += 1
        self.presize_rejected_by_ticker[(ticker or "").upper()] += 1
        self.presize_latest_size_ratio = verdict.size_ratio
        self.presize_latest_proposed_notional = verdict.proposed_notional
        self.presize_latest_cap_notional = verdict.cap_notional

    def record_short_regime(
        self, ticker: str, verdict: "RegimeGateVerdict",
    ) -> None:
        self.short_regime_suppressed_count += 1
        self.short_regime_suppressed_by_ticker[(ticker or "").upper()] += 1
        self.short_regime_latest_reason = verdict.reason

    def snapshot(self) -> Dict[str, Any]:
        return {
            "version": "SIGNAL_HYGIENE_V1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "duplicate_suppressed": {
                "count": self.duplicate_suppressed_count,
                "by_ticker": dict(self.duplicate_suppressed_by_ticker.most_common(50)),
                "by_strategy": dict(self.duplicate_suppressed_by_strategy),
                "latest_suppressed_reason": self.latest_suppressed_reason,
            },
            "presize_rejected": {
                "count": self.presize_rejected_count,
                "by_ticker": dict(self.presize_rejected_by_ticker.most_common(50)),
                "by_strategy": dict(self.presize_rejected_by_strategy),
                "latest_size_ratio": self.presize_latest_size_ratio,
                "latest_proposed_notional": self.presize_latest_proposed_notional,
                "latest_cap_notional": self.presize_latest_cap_notional,
            },
            "short_regime_suppressed": {
                "count": self.short_regime_suppressed_count,
                "by_ticker": dict(self.short_regime_suppressed_by_ticker.most_common(50)),
                "latest_reason": self.short_regime_latest_reason,
            },
        }

    def write_sidecar(self, path: Optional[Path] = None) -> Optional[Path]:
        """Atomically write the per-cycle snapshot to a JSON sidecar.

        Defaults to ``cache/research/signal_hygiene_latest.json``. Never
        raises; returns the path on success or None on failure.
        """
        try:
            import core.config as cfg
            target = path or (cfg.CACHE_DIR / "research" / "signal_hygiene_latest.json")
        except Exception:
            if path is None:
                return None
            target = path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = self.snapshot()
            with tempfile.NamedTemporaryFile(
                "w", dir=str(target.parent), delete=False, suffix=".tmp",
                encoding="utf-8",
            ) as tmp:
                json.dump(payload, tmp, indent=2, default=str)
                tmp.write("\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp.name, target)
            return target
        except Exception as exc:
            logger.debug("signal_hygiene sidecar write failed: %s", exc)
            return None


# Module-global counters used by main.py. Tests construct their own.
COUNTERS = HygieneCounters()


# ── Forecast snapshot reader ─────────────────────────────────────────────────


def read_forecast_snapshot(
    path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Best-effort read of the latest regime forecast JSON. Returns
    None on any error so callers can degrade conservatively."""
    try:
        import core.config as cfg
        target = path or (cfg.CACHE_DIR / "research" / "regime_forecast_latest.json")
    except Exception:
        target = path
    if target is None or not Path(target).exists():
        return None
    try:
        return json.loads(Path(target).read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("regime_forecast read failed: %s", exc)
        return None
