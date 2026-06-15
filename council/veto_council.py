"""
council/veto_council.py — Weighted Veto Council.

Nine voting agents. Any single agent can VETO a trade.
Agents are organized into two tiers:

Tier 1 — HARD VETO (any single agent can block):
  1. RegimeAgent       — VIX/SPY regime gate
  2. MacroAgent        — economic calendar blackout window
  3. PortfolioAgent    — daily loss cap, position limits, heat

Tier 2 — SOFT SCORE (weighted confidence):
  4. SectorAgent       — sector rotation strength/weakness
  5. FlowAgent         — institutional flow confirmation
  6. SentimentAgent    — FMP news sentiment proxy
  7. EarningsAgent     — earnings proximity blackout
  8. SpreadAgent       — bid-ask spread / liquidity gate
  9. MomentumAgent     — broader momentum confirmation

A trade is APPROVED only when:
  • All Tier 1 agents approve
  • Weighted Tier 2 score ≥ MIN_SOFT_SCORE (50 / 100)

All data calls go through FMP Gatekeeper (cached).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.alpaca_client import get_alpaca
from core.fmp_client import get_fmp

logger = logging.getLogger(__name__)

MIN_SOFT_SCORE = 50  # out of 100

# Phase 1G.1: Tier-2 agents return integer scores on [0, 100]. Before
# this fix, ``_flow_agent`` (and a few defensive branches in other
# agents) returned scores that could fall outside that range — most
# visibly when a SHORT direction met a heavy buying day, the flow
# agent's "(1 - vol_accel) * 50" term went sharply negative, producing
# Tier-2 weighted totals like -1685 / -1896 in the veto log. Those did
# not change verdicts (still vetoes) but corrupted the soft_score
# signal that downstream evidence reports rely on.
#
# Invariant: every Tier-2 agent score must be an integer in
# [SOFT_SCORE_FLOOR, SOFT_SCORE_CEILING]. ``_safe_agent_score`` clamps
# in-range values, and ``_validate_votes`` flags off-scale inputs so
# the verdict reverts to a SCORE_ANOMALY safe veto. This is a
# safety net, not a recalibration — the underlying agent math is
# fixed at the source as well.
SOFT_SCORE_FLOOR = 0
SOFT_SCORE_CEILING = 100
SCORE_ANOMALY_AGENT = "score_anomaly"

# Fix 5 (2026-05-15 audit): once a (date, ticker, strategy) gets vetoed,
# parking it for the rest of the session unless its setup price moves
# materially. Without this, CAI was vetoed 72× in one session at
# 27.9 → 28.6 → 27.9 — same setup, same answer, dozens of redundant veto
# rows. A 50 bps move is a defensible "the input changed enough to
# revisit" threshold; tighter and the cache is useless, looser and we
# miss real intraday regime shifts.
COOLDOWN_REPRICE_BPS = 50.0

# Default Tier 2 agent weights (must sum to 1.0). Used when
# COUNCIL_PROFILES_ENABLED is false OR when signal["strategy"] has no
# matching profile in _PROFILES below. Preserves historical behavior.
_WEIGHTS = {
    "sector":    0.20,
    "flow":      0.25,
    "sentiment": 0.15,
    "earnings":  0.20,
    "spread":    0.10,
    "momentum":  0.10,
}

# Per-strategy weight profiles. See docs/strategy/COUNCIL_PROFILES.md for
# the rationale per profile and the activation gate (each profile is gated
# behind a baseline-tag bump for the corresponding sleeve). All weight maps
# must sum to 1.0 and use the same agent keys as _WEIGHTS.
_PROFILES = {
    "VOYAGER": {
        "sector":    0.15,
        "flow":      0.25,
        "sentiment": 0.10,
        "earnings":  0.10,
        "spread":    0.05,
        "momentum":  0.35,
    },
    "SNIPER": {
        "sector":    0.10,
        "flow":      0.20,
        "sentiment": 0.10,
        "earnings":  0.20,
        "spread":    0.15,
        "momentum":  0.25,
    },
    "SHORT": {
        "sector":    0.05,
        "flow":      0.10,
        "sentiment": 0.10,
        "earnings":  0.20,
        "spread":    0.15,
        "momentum":  0.40,
    },
}


def _select_weights(strategy: str) -> Tuple[Dict[str, float], str]:
    """Pick the Tier-2 weight map for a given strategy.

    Returns (weights, profile_name). When COUNCIL_PROFILES_ENABLED is false,
    or strategy has no defined profile, returns the default _WEIGHTS.
    """
    import core.config as cfg
    if not getattr(cfg, "COUNCIL_PROFILES_ENABLED", False):
        return _WEIGHTS, "default"
    profile = _PROFILES.get(strategy.upper())
    if profile is None:
        return _WEIGHTS, "default"
    return profile, strategy.upper()


def _safe_agent_score(raw: Any) -> int:
    """Phase 1G.1 invariant helper.

    Convert a Tier-2 agent's reported score to an integer in
    [SOFT_SCORE_FLOOR, SOFT_SCORE_CEILING]. Non-numeric / missing
    inputs collapse to the neutral 50, matching the documented
    fallback in each agent's error branch. Out-of-range numerics are
    clamped *and* reported back so the council can decide whether to
    treat the input as an anomaly.

    The clamp is doctrine: Tier-2 weights are calibrated for the [0,100]
    range; permitting out-of-range scores would bias the soft_score in
    a way the weight schema does not anticipate. This is not a
    threshold change — verdicts still hinge on the documented
    MIN_SOFT_SCORE=50 cut.
    """
    if raw is None:
        return 50
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 50
    if val != val:  # NaN
        return 50
    if val < SOFT_SCORE_FLOOR:
        return SOFT_SCORE_FLOOR
    if val > SOFT_SCORE_CEILING:
        return SOFT_SCORE_CEILING
    return int(round(val))


def _validate_votes(votes: Dict[str, Dict]) -> Tuple[bool, List[str]]:
    """Phase 1G.1: surface any Tier-2 agent that returned an out-of-range
    score. Returns (ok, anomaly_descriptions). The caller treats a
    non-ok result as a SCORE_ANOMALY safe veto so a misbehaving agent
    can never accidentally drag the soft_score below the floor and
    fool the verdict.
    """
    anomalies: List[str] = []
    for name, vote in votes.items():
        if not isinstance(vote, dict):
            anomalies.append(f"{name}: vote is not a dict")
            continue
        if "score" not in vote:
            continue  # Tier-1 agents do not report a score
        raw = vote.get("score")
        try:
            val = float(raw)
        except (TypeError, ValueError):
            anomalies.append(f"{name}: non-numeric score {raw!r}")
            continue
        if val != val:
            anomalies.append(f"{name}: NaN score")
            continue
        if val < SOFT_SCORE_FLOOR or val > SOFT_SCORE_CEILING:
            anomalies.append(
                f"{name}: score {val:.1f} outside "
                f"[{SOFT_SCORE_FLOOR},{SOFT_SCORE_CEILING}]"
            )
    return (not anomalies), anomalies


class VetoCouncil:
    """
    Call evaluate(signal) → dict with verdict, reasons, soft_score.
    signal must have: ticker, strategy, direction, entry_price, stop_loss, target_price.
    """

    def __init__(self):
        self._alpaca = get_alpaca()
        self._fmp    = get_fmp()
        # _cooldown[(date_str, ticker, strategy)] = {
        #     entry_price, soft_score, agent, reason, ts
        # }
        self._cooldown: Dict[Tuple[str, str, str], Dict] = {}

    def _cooldown_key(self, ticker: str, strategy: str) -> Tuple[str, str, str]:
        today = datetime.now(timezone.utc).date().isoformat()
        return (today, ticker.upper(), strategy.upper())

    def _prune_cooldown(self, today: str) -> None:
        """Drop entries from prior sessions on day rollover."""
        stale = [k for k in self._cooldown if k[0] != today]
        for k in stale:
            self._cooldown.pop(k, None)

    def _check_cooldown(
        self, ticker: str, strategy: str, entry_price: float,
    ) -> Optional[Dict]:
        """Return the cached veto verdict iff the same (date, ticker,
        strategy) was already vetoed and the entry_price hasn't moved
        > COOLDOWN_REPRICE_BPS since."""
        key = self._cooldown_key(ticker, strategy)
        self._prune_cooldown(key[0])
        prior = self._cooldown.get(key)
        if prior is None:
            return None
        prior_px = prior.get("entry_price") or 0
        if not prior_px or not entry_price:
            return None
        move_bps = abs(entry_price - prior_px) / prior_px * 1e4
        if move_bps > COOLDOWN_REPRICE_BPS:
            # Setup re-priced enough to deserve a fresh look.
            self._cooldown.pop(key, None)
            return None
        return {
            "verdict":    "VETOED",
            "agent":      prior["agent"],
            "reason":     prior["reason"],
            "soft_score": prior["soft_score"],
            "votes":      {},
            "cached":     True,
            "cached_at":  prior["ts"],
        }

    def _record_cooldown(
        self, ticker: str, strategy: str, entry_price: float,
        *, agent: str, reason: str, soft_score: float,
    ) -> None:
        key = self._cooldown_key(ticker, strategy)
        self._cooldown[key] = {
            "entry_price": entry_price,
            "soft_score":  soft_score,
            "agent":       agent,
            "reason":      reason,
            "ts":          datetime.now(timezone.utc).isoformat(),
        }

    def evaluate(self, signal: Dict, portfolio_state: Dict) -> Dict:
        ticker    = signal["ticker"].upper()
        strategy  = signal["strategy"].upper()
        direction = signal["direction"].upper()
        entry_px  = float(signal.get("entry_price") or 0)

        cached = self._check_cooldown(ticker, strategy, entry_px)
        if cached is not None:
            return cached

        votes: Dict[str, Dict] = {}

        # ── Tier 1: Hard veto agents ──────────────────────────────────────────
        votes["regime"]    = self._regime_agent(ticker)
        votes["macro"]     = self._macro_agent(ticker)
        votes["portfolio"] = self._portfolio_agent(ticker, signal, portfolio_state)

        for name, vote in votes.items():
            if vote["verdict"] == "VETO":
                logger.info(
                    "VETO %s %s by %s: %s", strategy, ticker, name, vote["reason"]
                )
                self._record_cooldown(
                    ticker, strategy, entry_px,
                    agent=name, reason=vote["reason"], soft_score=0,
                )
                return {
                    "verdict":    "VETOED",
                    "agent":      name,
                    "reason":     vote["reason"],
                    "soft_score": 0,
                    "votes":      votes,
                    "cached":     False,
                }

        # ── Tier 2: Soft score agents ─────────────────────────────────────────
        votes["sector"]    = self._sector_agent(ticker, direction)
        votes["flow"]      = self._flow_agent(ticker, direction)
        votes["sentiment"] = self._sentiment_agent(ticker, direction)
        votes["earnings"]  = self._earnings_agent(ticker)
        votes["spread"]    = self._spread_agent(ticker)
        votes["momentum"]  = self._momentum_agent(ticker, direction)

        # Phase 1G.1: invariant check. Any agent returning an
        # out-of-range numeric score is a bug; convert to a safe veto
        # rather than carry a corrupt score into the weighted sum.
        ok, anomalies = _validate_votes(votes)
        if not ok:
            reason = "SCORE_ANOMALY: " + "; ".join(anomalies[:3])
            logger.warning(
                "SCORE_ANOMALY %s %s: %s", strategy, ticker, anomalies,
            )
            self._record_cooldown(
                ticker, strategy, entry_px,
                agent=SCORE_ANOMALY_AGENT, reason=reason, soft_score=0,
            )
            return {
                "verdict":    "VETOED",
                "agent":      SCORE_ANOMALY_AGENT,
                "reason":     reason,
                "soft_score": 0,
                "anomalies":  anomalies,
                "votes":      votes,
                "cached":     False,
            }

        weights, profile_name = _select_weights(strategy)
        # Use _safe_agent_score to fold any in-range float into an
        # integer in [0, 100]; combined with _validate_votes above we
        # guarantee soft_score stays in [0, 100].
        soft_score = sum(
            weights.get(name, 0) * _safe_agent_score(votes[name].get("score", 50))
            for name in weights
        )
        soft_score = round(soft_score, 1)

        if soft_score < MIN_SOFT_SCORE:
            reason = (f"Tier 2 weighted score {soft_score:.1f} < "
                      f"{MIN_SOFT_SCORE} (profile={profile_name})")
            self._record_cooldown(
                ticker, strategy, entry_px,
                agent="soft_score", reason=reason, soft_score=soft_score,
            )
            return {
                "verdict":    "VETOED",
                "agent":      "soft_score",
                "reason":     reason,
                "soft_score": soft_score,
                "profile":    profile_name,
                "votes":      votes,
                "cached":     False,
            }

        return {
            "verdict":    "APPROVED",
            "agent":      None,
            "reason":     f"All agents passed. Soft score: {soft_score:.1f} (profile={profile_name})",
            "soft_score": soft_score,
            "profile":    profile_name,
            "votes":      votes,
            "cached":     False,
        }

    # ── Tier 1 agents ─────────────────────────────────────────────────────────

    def _regime_agent(self, ticker: str) -> Dict:
        """VIX and SPY trend gate."""
        try:
            vix = self._fmp.get_vix() or 0.0
            spy = self._alpaca.get_daily_bars("SPY", days=25)
            if not spy:
                return {"verdict": "VETO", "reason": "SPY data unavailable"}

            closes = [b["close"] for b in spy]
            ma20   = sum(closes[-20:]) / 20
            spy_above_ma = closes[-1] > ma20

            # Hard block: extreme panic and not a contrarian trade
            if vix > 40:
                return {"verdict": "VETO", "reason": f"VIX={vix:.1f} extreme fear — only contrarian allowed"}

            return {
                "verdict": "APPROVE",
                "vix":     vix,
                "spy_above_ma20": spy_above_ma,
                "reason":  "regime ok",
            }
        except Exception as exc:
            logger.warning("regime_agent failed: %s", exc)
            return {"verdict": "APPROVE", "reason": "regime agent error — defaulting approve"}

    def _macro_agent(self, ticker: str) -> Dict:
        """Block if a U.S. high-impact (3-star) macro event is within 30 minutes.

        Scoped to ``country='US'`` on purpose: the FMP economic calendar carries
        every country's events, and a foreign high-impact print (e.g. BoC/ECB
        rate decisions, UK GDP, China CPI) must NOT put the U.S.-equities engine
        into defensive standby. NULL/foreign rows fail safe toward APPROVE.
        """
        try:
            import sqlite3
            from datetime import datetime, timezone, timedelta
            import core.config as cfg

            conn = sqlite3.connect(str(cfg.DB_PATH))
            now_utc = datetime.now(timezone.utc)
            # macro_events.event_time_utc is stored in FMP's space-separated UTC
            # form ("YYYY-MM-DD HH:MM:SS"). Match that exact string shape so the
            # BETWEEN comparison works — an ISO 'T'/offset form sorts wrong and
            # silently never matches same-day events.
            _FMT = "%Y-%m-%d %H:%M:%S"
            window_start = (now_utc - timedelta(minutes=15)).strftime(_FMT)
            window_end   = (now_utc + timedelta(minutes=30)).strftime(_FMT)
            rows = conn.execute(
                "SELECT event_name, impact FROM macro_events "
                "WHERE event_time_utc BETWEEN ? AND ? "
                "AND impact='High' AND country='US'",
                (window_start, window_end),
            ).fetchall()
            conn.close()

            if rows:
                names = ", ".join(r[0] for r in rows[:3])
                return {"verdict": "VETO", "reason": f"US high-impact macro event imminent: {names}"}
            return {"verdict": "APPROVE", "reason": "no macro conflict"}
        except Exception as exc:
            logger.debug("macro_agent DB error: %s", exc)
            return {"verdict": "APPROVE", "reason": "macro agent unavailable"}

    def _portfolio_agent(self, ticker: str, signal: Dict, state: Dict) -> Dict:
        """Daily loss cap, max open positions, single-name heat."""
        import core.config as cfg
        from execution.portfolio_risk import MAX_SINGLE_NAME_PCT

        daily_pnl_pct = state.get("daily_pnl_pct", 0.0)
        if daily_pnl_pct <= -cfg.MAX_DAILY_LOSS_PCT:
            return {
                "verdict": "VETO",
                "reason":  f"Daily loss cap hit: {daily_pnl_pct*100:.1f}% ≤ -{cfg.MAX_DAILY_LOSS_PCT*100:.0f}%",
            }

        open_positions = state.get("open_positions", 0)
        max_positions  = state.get("max_positions", 8)
        if open_positions >= max_positions:
            return {"verdict": "VETO", "reason": f"Position limit reached ({open_positions}/{max_positions})"}

        circuit_broken = state.get("circuit_breaker", False)
        if circuit_broken:
            return {"verdict": "VETO", "reason": "Circuit breaker active"}

        # Fix 6 (2026-05-15 audit): pre-check single-name heat at council
        # time. Without this, scanners can propose sizes that exceed the
        # portfolio book's MAX_SINGLE_NAME_PCT cap; council approves, gate
        # refuses, and the cycle burns budget approving impossible trades
        # (29 HUBS approvals → 29 submission-gate rejections at $11.6k vs
        # $4.1k cap during the 2026-05-13 session).
        shares    = float(signal.get("shares") or 0)
        entry_px  = float(signal.get("entry_price") or 0)
        trade_val = shares * entry_px
        equity    = state.get("equity") or state.get("account_equity") or 0
        if trade_val > 0 and equity and equity > 0:
            single_cap = float(equity) * MAX_SINGLE_NAME_PCT
            try:
                existing_val = sum(
                    abs(float(p.get("market_value") or 0))
                    for p in (self._alpaca.get_positions() or [])
                    if str(p.get("ticker") or "").upper() == ticker.upper()
                )
            except Exception:
                existing_val = 0.0
            projected = existing_val + trade_val
            if projected > single_cap:
                return {
                    "verdict": "VETO",
                    "reason": (
                        f"{ticker} single-name heat would reach "
                        f"${projected:,.0f} (cap: ${single_cap:,.0f}, "
                        f"{MAX_SINGLE_NAME_PCT*100:.1f}% × equity)"
                    ),
                }

        return {"verdict": "APPROVE", "reason": "portfolio gate ok"}

    # ── Tier 2 agents (scored 0–100) ──────────────────────────────────────────

    def _sector_agent(self, ticker: str, direction: str) -> Dict:
        """Basic sector PE vs market PE check via FMP."""
        try:
            sector_pes = self._fmp.get_sector_pe()
            if not sector_pes:
                return {"verdict": "APPROVE", "score": 50, "reason": "sector data unavailable"}
            # Without a sector map for the ticker, return neutral
            return {"verdict": "APPROVE", "score": 55, "reason": "sector agent neutral"}
        except Exception:
            return {"verdict": "APPROVE", "score": 50, "reason": "sector agent error"}

    def _flow_agent(self, ticker: str, direction: str) -> Dict:
        """Intraday volume trend as institutional flow proxy.

        Phase 1G.1 fix: the previous form used ``min(100, ...)`` with no
        lower bound, so a SHORT direction meeting a heavy buying spike
        (``vol_accel`` of 10×+) yielded scores like -450, which the
        soft_score sum carried into the veto_log as negative four-digit
        numbers. Tier-2 scoring is defined on [0, 100]; we now clamp at
        both ends and cap ``vol_accel`` at the documented ±100% sensitivity
        before the linear map. This preserves the intent (heavy
        volume-side flow boosts the matching direction) while keeping
        the agent's contract intact.
        """
        try:
            bars_5m = self._alpaca.get_intraday_bars(ticker, "5Min", limit=20)
            if len(bars_5m) < 5:
                return {"verdict": "APPROVE", "score": 50, "reason": "insufficient intraday data"}
            vols    = [b["volume"] for b in bars_5m]
            avg_early = sum(vols[:10]) / 10
            avg_late  = sum(vols[-5:]) / 5
            vol_accel = avg_late / avg_early if avg_early > 0 else 1.0
            # Map ``vol_accel`` to a centered delta. The linear coefficient
            # of 50 means a 1.0× ratio is neutral (50), a 2.0× ratio adds
            # 50 (LONG: caps at 100), and a 0.0× ratio subtracts 50
            # (LONG: caps at 0). The same logic mirrors for SHORT.
            if direction == "LONG":
                raw = 50 + (vol_accel - 1.0) * 50
            else:
                raw = 50 + (1.0 - vol_accel) * 50
            score = max(SOFT_SCORE_FLOOR, min(SOFT_SCORE_CEILING, int(raw)))
            return {"verdict": "APPROVE", "score": score, "vol_accel": round(vol_accel, 2)}
        except Exception:
            return {"verdict": "APPROVE", "score": 50, "reason": "flow agent error"}

    def _sentiment_agent(self, ticker: str, direction: str) -> Dict:
        """FMP news sentiment proxy."""
        try:
            sent = self._fmp.get_sentiment_score(ticker)  # 0.0–1.0
            # For LONG: high sentiment = good; for SHORT: low sentiment = good
            score = int(sent * 100) if direction == "LONG" else int((1 - sent) * 100)
            return {"verdict": "APPROVE", "score": score, "raw_sentiment": sent}
        except Exception:
            return {"verdict": "APPROVE", "score": 50, "reason": "sentiment agent error"}

    def _earnings_agent(self, ticker: str) -> Dict:
        """Block if earnings within 5 days."""
        try:
            cal = self._fmp.get_earnings_calendar(days_ahead=5)
            soon = {e.get("symbol", "").upper() for e in cal}
            if ticker in soon:
                return {"verdict": "APPROVE", "score": 20, "reason": "earnings within 5 days — low score"}
            return {"verdict": "APPROVE", "score": 80, "reason": "no earnings conflict"}
        except Exception:
            return {"verdict": "APPROVE", "score": 50}

    def _spread_agent(self, ticker: str) -> Dict:
        """Bid-ask spread gate from live Alpaca quote."""
        try:
            q = self._alpaca.get_quote(ticker)
            if not q:
                return {"verdict": "APPROVE", "score": 50, "reason": "no quote"}
            spread_pct = (q["ask"] - q["bid"]) / q["mid"] if q["mid"] > 0 else 1.0
            if spread_pct > 0.005:  # > 0.5% is poor
                return {"verdict": "APPROVE", "score": 20, "spread_pct": round(spread_pct * 100, 3)}
            score = max(30, int(100 - spread_pct * 10000))
            return {"verdict": "APPROVE", "score": score, "spread_pct": round(spread_pct * 100, 3)}
        except Exception:
            return {"verdict": "APPROVE", "score": 50}

    def _momentum_agent(self, ticker: str, direction: str) -> Dict:
        """20-day price momentum."""
        try:
            bars = self._alpaca.get_daily_bars(ticker, days=25)
            if len(bars) < 21:
                return {"verdict": "APPROVE", "score": 50}
            closes = [b["close"] for b in bars]
            mom_20 = (closes[-1] / closes[-21] - 1)
            if direction == "LONG":
                score = min(100, max(0, int(50 + mom_20 * 300)))
            else:
                score = min(100, max(0, int(50 - mom_20 * 300)))
            return {"verdict": "APPROVE", "score": score, "mom_20d_pct": round(mom_20 * 100, 2)}
        except Exception:
            return {"verdict": "APPROVE", "score": 50}
