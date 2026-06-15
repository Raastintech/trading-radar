"""
Transparent candidate ranking utilities for best-idea selection.

This layer is intentionally additive. It reads the existing decisions/trades
telemetry and produces ranked candidates without changing live execution.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from forecast_engine import ForecastEngine
from shadow_rate_forecaster import ShadowRateForecaster
from vix_snapshot import get_vix_snapshot


LIVE_RR_THRESHOLDS: Dict[str, float] = {
    "VOYAGER": 2.0,
    "SNIPER": 2.5,
    "REMORA": 2.0,
    "SHORT": 3.0,
    "CONTRARIAN": 1.5,
    "REAPER": 1.5,
}

LIVE_SCORE_THRESHOLDS: Dict[str, float] = {
    "VOYAGER": 60.0,
    "SNIPER": 60.0,
    "REMORA": 70.0,
    "SHORT": 55.0,
    "CONTRARIAN": 60.0,
    "REAPER": 60.0,
}

STATUS_SCORES: Dict[str, float] = {
    "ENTRY_SUBMITTED": 100.0,
    "RISK_OVERLAY_DENIED": 72.0,
    "REJECT": 45.0,
    "SCANNER_REJECT": 35.0,
    "DATA_ERROR": 10.0,
}

FORECAST_PRIOR_WEIGHT = 0.10
MIN_FORECAST_CLOSED_TRADES = 200


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RankedCandidate:
    decision_id: int
    timestamp: str
    ticker: str
    strategy: str
    direction: str
    council_decision: str
    council_reason: str
    rr: Optional[float]
    avg_score: Optional[float]
    revision_score: Optional[float]
    insider_cluster_score: Optional[float]
    squeeze_risk_score: Optional[float]
    options_pcr: Optional[float]
    options_gamma: Optional[str]
    regime_status: Optional[str]
    regime_volatility: Optional[str]
    is_pilot: int = 0
    pilot_policy: Optional[str] = None
    score_threshold: float = 0.0
    rr_threshold: float = 0.0
    candidate_state: str = "REJECT"
    status_score: float = 0.0
    score_quality_score: float = 0.0
    rr_quality_score: float = 0.0
    regime_fit_score: float = 0.0
    feature_score: float = 0.0
    prior_score: float = 50.0
    prior_label: str = "neutral"
    forecast_weight: float = FORECAST_PRIOR_WEIGHT
    closed_trade_count: int = 0
    bte_breakout_probability: Optional[float] = None
    bte_timing_window_low_days: Optional[float] = None
    bte_timing_window_high_days: Optional[float] = None
    bte_median_days_to_breakout: Optional[float] = None
    bte_sample_size: int = 0
    bte_source_dimension: Optional[str] = None
    bte_source_segment: Optional[str] = None
    bte_advisory_label: Optional[str] = None
    bte_short_breakdown_probability: Optional[float] = None
    bte_short_timing_window_low_days: Optional[float] = None
    bte_short_timing_window_high_days: Optional[float] = None
    bte_short_median_days_to_breakdown: Optional[float] = None
    bte_short_sample_size: int = 0
    bte_short_source_dimension: Optional[str] = None
    bte_short_source_segment: Optional[str] = None
    bte_short_advisory_label: Optional[str] = None
    overall_rank_score: float = 0.0
    why: str = ""


class CandidateRanker:
    def __init__(self, db_path: str = "trading_performance.db"):
        self.db_path = db_path
        base_forecaster = ForecastEngine(db_path=db_path)
        self.forecast_engine = ShadowRateForecaster(
            db_path=db_path,
            base_forecaster=base_forecaster,
        )
        self._shadow_priors = self._load_shadow_priors()
        self._vix_snapshot = get_vix_snapshot()
        self._closed_trade_counts = self._load_closed_trade_counts()

    def _refresh_vix_snapshot(self) -> None:
        self._vix_snapshot = get_vix_snapshot()

    def _refresh_closed_trade_counts(self) -> None:
        self._closed_trade_counts = self._load_closed_trade_counts()

    def load_recent_decisions(
        self,
        days: int = 1,
        include_rejects: bool = True,
    ) -> List[sqlite3.Row]:
        cutoff = (_utc_now() - timedelta(days=max(1, int(days)))).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        clauses = [
            "timestamp >= ?",
            "UPPER(COALESCE(strategy, '')) != ''",
        ]
        params: List[Any] = [cutoff]
        if not include_rejects:
            clauses.append("UPPER(COALESCE(council_decision, '')) = 'ENTRY_SUBMITTED'")

        sql = f"""
            SELECT
                id, run_id, timestamp, ticker, strategy, direction,
                council_decision, council_reason, rr, avg_score,
                confluence_score, revision_score, insider_cluster_score,
                squeeze_risk_score, options_pcr, options_gamma,
                regime_status, regime_volatility, is_pilot, pilot_policy
            FROM decisions
            WHERE {" AND ".join(clauses)}
            ORDER BY timestamp DESC, id DESC
        """
        cur.execute(sql, params)
        rows = cur.fetchall()
        conn.close()
        return rows

    def rank_recent_candidates(
        self,
        days: int = 1,
        limit: int = 20,
        include_rejects: bool = True,
    ) -> List[RankedCandidate]:
        self._refresh_vix_snapshot()
        self._refresh_closed_trade_counts()
        ranked: List[RankedCandidate] = []
        for row in self.load_recent_decisions(days=days, include_rejects=include_rejects):
            item = self._rank_row(row)
            if item is None:
                continue
            ranked.append(item)

        deduped: Dict[Tuple[str, str, str], RankedCandidate] = {}
        for item in ranked:
            key = (item.ticker, item.strategy, item.direction)
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = item
                continue
            if item.overall_rank_score > existing.overall_rank_score:
                deduped[key] = item
                continue
            if item.overall_rank_score == existing.overall_rank_score and item.timestamp > existing.timestamp:
                deduped[key] = item

        rows = list(deduped.values())
        rows.sort(key=lambda x: (x.overall_rank_score, x.timestamp), reverse=True)
        return rows[: max(1, int(limit))]

    def rank_live_opportunities(
        self,
        opportunities: List[Any],
        regime_snapshot: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> List[RankedCandidate]:
        self._refresh_vix_snapshot()
        self._refresh_closed_trade_counts()
        ranked: List[RankedCandidate] = []
        for idx, opp in enumerate(opportunities or []):
            item = self._rank_live_opportunity(opp, idx=idx, regime_snapshot=regime_snapshot or {})
            if item is None:
                continue
            ranked.append(item)

        ranked.sort(key=lambda x: (x.overall_rank_score, x.timestamp), reverse=True)
        if limit is None:
            return ranked
        return ranked[: max(1, int(limit))]

    def _rank_row(self, row: sqlite3.Row) -> Optional[RankedCandidate]:
        strategy = str(row["strategy"] or "").upper().strip()
        if strategy not in LIVE_RR_THRESHOLDS:
            return None

        rr_threshold = LIVE_RR_THRESHOLDS[strategy]
        score_threshold = LIVE_SCORE_THRESHOLDS.get(strategy, 60.0)
        rr = _safe_float(row["rr"])
        avg_score = _safe_float(row["avg_score"])
        council_decision = str(row["council_decision"] or "").upper().strip()
        council_reason = str(row["council_reason"] or "").strip()

        state = self._candidate_state(
            council_decision=council_decision,
            council_reason=council_reason,
            rr=rr,
            rr_threshold=rr_threshold,
            avg_score=avg_score,
            score_threshold=score_threshold,
        )
        if state == "REJECT":
            return None

        status_score = STATUS_SCORES.get(council_decision, 35.0 if state == "NEAR_MISS" else 20.0)
        score_quality = self._score_quality(avg_score, score_threshold)
        rr_quality = self._rr_quality(rr, rr_threshold)
        regime_fit = self._regime_fit(
            strategy=strategy,
            direction=str(row["direction"] or "LONG"),
            regime_status=str(row["regime_status"] or ""),
            regime_volatility=str(row["regime_volatility"] or ""),
            vix_level=_safe_float(self._vix_snapshot.get("vix_level")),
        )
        feature_score = self._feature_score(
            strategy=strategy,
            direction=str(row["direction"] or "LONG"),
            revision_score=_safe_float(row["revision_score"]),
            insider_cluster_score=_safe_float(row["insider_cluster_score"]),
            squeeze_risk_score=_safe_float(row["squeeze_risk_score"]),
            options_pcr=_safe_float(row["options_pcr"]),
            options_gamma=str(row["options_gamma"] or ""),
        )
        prior_score, prior_label, forecast_weight, closed_trade_count = self._prior_score(
            strategy=strategy,
            rr=rr,
            avg_score=avg_score,
            regime_status=str(row["regime_status"] or ""),
            regime_volatility=str(row["regime_volatility"] or ""),
            vix_level=_safe_float(self._vix_snapshot.get("vix_level")),
        )

        overall = (
            status_score * 0.15
            + score_quality * 0.25
            + rr_quality * 0.25
            + regime_fit * 0.15
            + feature_score * 0.10
            + prior_score * forecast_weight
        )

        why_parts = [
            f"state={state.lower()}",
            f"rr={rr:.2f}/{rr_threshold:.1f}" if rr is not None else "rr=na",
            f"score={avg_score:.1f}/{score_threshold:.0f}" if avg_score is not None else "score=na",
            f"regime={regime_fit:.0f}",
            f"prior={prior_label}",
        ]

        return RankedCandidate(
            decision_id=int(row["id"]),
            timestamp=str(row["timestamp"]),
            ticker=str(row["ticker"] or "").upper(),
            strategy=strategy,
            direction=str(row["direction"] or "LONG").upper().strip() or "LONG",
            council_decision=council_decision,
            council_reason=council_reason,
            rr=rr,
            avg_score=avg_score,
            revision_score=_safe_float(row["revision_score"]),
            insider_cluster_score=_safe_float(row["insider_cluster_score"]),
            squeeze_risk_score=_safe_float(row["squeeze_risk_score"]),
            options_pcr=_safe_float(row["options_pcr"]),
            options_gamma=str(row["options_gamma"] or "").upper().strip() or None,
            regime_status=str(row["regime_status"] or "").upper().strip() or None,
            regime_volatility=str(row["regime_volatility"] or "").upper().strip() or None,
            is_pilot=int(row["is_pilot"] or 0),
            pilot_policy=str(row["pilot_policy"] or "").strip() or None,
            score_threshold=score_threshold,
            rr_threshold=rr_threshold,
            candidate_state=state,
            status_score=round(status_score, 2),
            score_quality_score=round(score_quality, 2),
            rr_quality_score=round(rr_quality, 2),
            regime_fit_score=round(regime_fit, 2),
            feature_score=round(feature_score, 2),
            prior_score=round(prior_score, 2),
            prior_label=prior_label,
            forecast_weight=round(forecast_weight, 4),
            closed_trade_count=closed_trade_count,
            bte_sample_size=0,
            bte_short_sample_size=0,
            overall_rank_score=round(_clamp(overall), 2),
            why=" | ".join(why_parts),
        )

    def _rank_live_opportunity(
        self,
        opp: Any,
        idx: int,
        regime_snapshot: Dict[str, Any],
    ) -> Optional[RankedCandidate]:
        strategy = str(getattr(opp, "strategy", "") or "").upper().strip()
        if strategy not in LIVE_RR_THRESHOLDS:
            return None

        ticker = str(getattr(opp, "ticker", "") or "").upper().strip()
        if not ticker:
            return None

        phase3 = getattr(opp, "_phase3_context", None) or {}
        rr_threshold = LIVE_RR_THRESHOLDS[strategy]
        score_threshold = LIVE_SCORE_THRESHOLDS.get(strategy, 60.0)
        rr = _safe_float(getattr(opp, "risk_reward", None))
        avg_score = _safe_float(getattr(opp, "score", None))
        direction = str(getattr(opp, "direction", "LONG") or "LONG").upper().strip()
        regime_status = str(
            regime_snapshot.get("status")
            or getattr(opp, "regime_status", None)
            or ""
        ).upper().strip()
        regime_volatility = str(
            regime_snapshot.get("volatility")
            or getattr(opp, "regime_volatility", None)
            or ""
        ).upper().strip()

        status_score = 90.0
        score_quality = self._score_quality(avg_score, score_threshold)
        rr_quality = self._rr_quality(rr, rr_threshold)
        regime_fit = self._regime_fit(
            strategy=strategy,
            direction=direction,
            regime_status=regime_status,
            regime_volatility=regime_volatility,
            vix_level=_safe_float(self._vix_snapshot.get("vix_level")),
        )
        feature_score = self._feature_score(
            strategy=strategy,
            direction=direction,
            revision_score=_safe_float(phase3.get("revision_score", getattr(opp, "revision_score", None))),
            insider_cluster_score=_safe_float(phase3.get("insider_cluster_score", getattr(opp, "insider_cluster_score", None))),
            squeeze_risk_score=_safe_float(phase3.get("squeeze_risk_score", getattr(opp, "squeeze_risk_score", None))),
            options_pcr=_safe_float(getattr(opp, "options_pcr", None)),
            options_gamma=str(getattr(opp, "options_gamma", "") or ""),
        )
        prior_score, prior_label, forecast_weight, closed_trade_count = self._prior_score(
            strategy=strategy,
            rr=rr,
            avg_score=avg_score,
            regime_status=regime_status,
            regime_volatility=regime_volatility,
            vix_level=_safe_float(self._vix_snapshot.get("vix_level")),
        )
        bte_probability = _safe_float(phase3.get("bte_breakout_probability"))
        bte_window_low = _safe_float(phase3.get("bte_timing_window_low_days"))
        bte_window_high = _safe_float(phase3.get("bte_timing_window_high_days"))
        bte_median = _safe_float(phase3.get("bte_median_days_to_breakout"))
        bte_sample_size = int(_safe_float(phase3.get("bte_sample_size"), 0) or 0)
        bte_source_dimension = str(phase3.get("bte_source_dimension") or "").strip() or None
        bte_source_segment = str(phase3.get("bte_source_segment") or "").strip() or None
        bte_advisory_label = str(phase3.get("bte_advisory_label") or "").strip() or None
        bte_short_probability = _safe_float(phase3.get("bte_short_breakdown_probability"))
        bte_short_window_low = _safe_float(phase3.get("bte_short_timing_window_low_days"))
        bte_short_window_high = _safe_float(phase3.get("bte_short_timing_window_high_days"))
        bte_short_median = _safe_float(phase3.get("bte_short_median_days_to_breakdown"))
        bte_short_sample_size = int(_safe_float(phase3.get("bte_short_sample_size"), 0) or 0)
        bte_short_source_dimension = str(phase3.get("bte_short_source_dimension") or "").strip() or None
        bte_short_source_segment = str(phase3.get("bte_short_source_segment") or "").strip() or None
        bte_short_advisory_label = str(phase3.get("bte_short_advisory_label") or "").strip() or None

        overall = (
            status_score * 0.15
            + score_quality * 0.25
            + rr_quality * 0.25
            + regime_fit * 0.15
            + feature_score * 0.10
            + prior_score * forecast_weight
        )

        why_parts = [
            "state=approved",
            f"rr={rr:.2f}/{rr_threshold:.1f}" if rr is not None else "rr=na",
            f"score={avg_score:.1f}/{score_threshold:.0f}" if avg_score is not None else "score=na",
            f"regime={regime_fit:.0f}",
            f"prior={prior_label}",
        ]
        if strategy == "SNIPER":
            if (
                bte_probability is not None
                and bte_window_low is not None
                and bte_window_high is not None
                and bte_source_dimension
            ):
                why_parts.append(
                    f"bte={bte_probability:.2f}@{bte_window_low:g}-{bte_window_high:g}d"
                    f"[{bte_source_dimension}]"
                )
            else:
                why_parts.append("bte=na")
        elif strategy == "SHORT":
            if (
                bte_short_probability is not None
                and bte_short_window_low is not None
                and bte_short_window_high is not None
                and bte_short_source_dimension
            ):
                why_parts.append(
                    f"bte-short={bte_short_probability:.2f}@{bte_short_window_low:g}-{bte_short_window_high:g}d"
                    f"[{bte_short_source_dimension}]"
                )
            else:
                why_parts.append("bte-short=na")
        why = " | ".join(why_parts)

        return RankedCandidate(
            decision_id=-(idx + 1),
            timestamp=str(getattr(opp, "timestamp", None) or _utc_now().isoformat()),
            ticker=ticker,
            strategy=strategy,
            direction=direction or "LONG",
            council_decision="APPROVED_CANDIDATE",
            council_reason="live_scan_candidate",
            rr=rr,
            avg_score=avg_score,
            revision_score=_safe_float(phase3.get("revision_score", getattr(opp, "revision_score", None))),
            insider_cluster_score=_safe_float(phase3.get("insider_cluster_score", getattr(opp, "insider_cluster_score", None))),
            squeeze_risk_score=_safe_float(phase3.get("squeeze_risk_score", getattr(opp, "squeeze_risk_score", None))),
            options_pcr=_safe_float(getattr(opp, "options_pcr", None)),
            options_gamma=str(getattr(opp, "options_gamma", "") or "").upper().strip() or None,
            regime_status=regime_status or None,
            regime_volatility=regime_volatility or None,
            is_pilot=0,
            pilot_policy=None,
            score_threshold=score_threshold,
            rr_threshold=rr_threshold,
            candidate_state="APPROVED",
            status_score=round(status_score, 2),
            score_quality_score=round(score_quality, 2),
            rr_quality_score=round(rr_quality, 2),
            regime_fit_score=round(regime_fit, 2),
            feature_score=round(feature_score, 2),
            prior_score=round(prior_score, 2),
            prior_label=prior_label,
            forecast_weight=round(forecast_weight, 4),
            closed_trade_count=closed_trade_count,
            bte_breakout_probability=bte_probability,
            bte_timing_window_low_days=bte_window_low,
            bte_timing_window_high_days=bte_window_high,
            bte_median_days_to_breakout=bte_median,
            bte_sample_size=bte_sample_size,
            bte_source_dimension=bte_source_dimension,
            bte_source_segment=bte_source_segment,
            bte_advisory_label=bte_advisory_label,
            bte_short_breakdown_probability=bte_short_probability,
            bte_short_timing_window_low_days=bte_short_window_low,
            bte_short_timing_window_high_days=bte_short_window_high,
            bte_short_median_days_to_breakdown=bte_short_median,
            bte_short_sample_size=bte_short_sample_size,
            bte_short_source_dimension=bte_short_source_dimension,
            bte_short_source_segment=bte_short_source_segment,
            bte_short_advisory_label=bte_short_advisory_label,
            overall_rank_score=round(_clamp(overall), 2),
            why=why,
        )

    def _candidate_state(
        self,
        council_decision: str,
        council_reason: str,
        rr: Optional[float],
        rr_threshold: float,
        avg_score: Optional[float],
        score_threshold: float,
    ) -> str:
        if council_decision == "ENTRY_SUBMITTED":
            return "APPROVED"
        if council_decision == "RISK_OVERLAY_DENIED":
            return "RISK_BLOCKED"

        reason = council_reason.lower()
        rr_ratio = (rr / rr_threshold) if rr is not None and rr_threshold > 0 else 0.0
        score_gap = (score_threshold - avg_score) if avg_score is not None else 999.0

        if "risk_reward_too_low" in reason and rr_ratio >= 0.80:
            return "NEAR_MISS"
        if "score_below_threshold" in reason and score_gap <= 8.0:
            return "NEAR_MISS"
        if avg_score is not None and avg_score >= (score_threshold - 6.0):
            return "NEAR_MISS"
        return "REJECT"

    def _score_quality(self, avg_score: Optional[float], threshold: float) -> float:
        if avg_score is None:
            return 35.0
        gap = avg_score - threshold
        if gap >= 20:
            return 95.0
        if gap >= 10:
            return 82.0
        if gap >= 0:
            return 70.0 + min(gap, 10) * 1.2
        if gap >= -5:
            return 58.0 + (gap + 5) * 2.4
        if gap >= -10:
            return 35.0 + (gap + 10) * 4.6
        return 10.0

    def _rr_quality(self, rr: Optional[float], threshold: float) -> float:
        if rr is None or threshold <= 0:
            return 25.0
        ratio = rr / threshold
        if ratio >= 1.35:
            return 96.0
        if ratio >= 1.0:
            return 76.0 + (ratio - 1.0) * 57.0
        if ratio >= 0.8:
            return 42.0 + (ratio - 0.8) * 170.0
        return max(0.0, ratio * 40.0)

    def _regime_fit(
        self,
        strategy: str,
        direction: str,
        regime_status: str,
        regime_volatility: str,
        vix_level: Optional[float],
    ) -> float:
        status = regime_status.upper().strip()
        vol = regime_volatility.upper().strip()
        direction_u = direction.upper().strip()
        score = 55.0

        if strategy == "SHORT":
            if status == "SIDEWAYS":
                score += 10.0
            if vol in {"HIGH", "ELEVATED"}:
                score += 12.0
            if direction_u == "SHORT":
                score += 5.0
        elif strategy == "SNIPER":
            if status == "BULL":
                score += 10.0
            if vol in {"NORMAL", "LOW"}:
                score += 8.0
        elif strategy == "VOYAGER":
            if status == "BULL":
                score += 14.0
            if vol in {"NORMAL", "LOW"}:
                score += 8.0
            if status == "SIDEWAYS":
                score -= 8.0
        elif strategy == "REMORA":
            if vol in {"HIGH", "ELEVATED"}:
                score += 10.0
            if status in {"BULL", "SIDEWAYS"}:
                score += 4.0
        elif strategy in {"CONTRARIAN", "REAPER"}:
            if vix_level is not None and vix_level >= 28.0:
                score += 18.0
            if vol in {"HIGH", "ELEVATED"}:
                score += 6.0

        return _clamp(score)

    def _feature_score(
        self,
        strategy: str,
        direction: str,
        revision_score: Optional[float],
        insider_cluster_score: Optional[float],
        squeeze_risk_score: Optional[float],
        options_pcr: Optional[float],
        options_gamma: str,
    ) -> float:
        direction_u = direction.upper().strip()
        score = 50.0

        if revision_score is not None:
            if direction_u == "LONG":
                score += (revision_score - 50.0) * 0.18
            else:
                score -= (revision_score - 50.0) * 0.08

        if insider_cluster_score is not None:
            if direction_u == "LONG":
                score += insider_cluster_score * 0.25
            else:
                score -= insider_cluster_score * 0.15

        if squeeze_risk_score is not None:
            if strategy == "SHORT":
                score -= max(0.0, squeeze_risk_score - 35.0) * 0.35
            else:
                score += max(0.0, 55.0 - squeeze_risk_score) * 0.10

        gamma = options_gamma.upper().strip()
        if direction_u == "SHORT":
            if options_pcr is not None:
                score += min(options_pcr, 2.5) * 8.0
            if gamma == "NEGATIVE":
                score += 8.0
        else:
            if options_pcr is not None:
                score += max(0.0, 1.2 - options_pcr) * 10.0
            if gamma == "POSITIVE":
                score += 6.0

        return _clamp(score)

    def _load_shadow_priors(self) -> Dict[Tuple[str, str], Dict[str, float]]:
        priors: Dict[Tuple[str, str], Dict[str, float]] = {}
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                c.strategy,
                c.policy_name,
                COUNT(*) AS n,
                AVG(o.end_return_pct) AS avg_ret,
                AVG(CASE WHEN o.hit_stop = 1 THEN 1.0 ELSE 0.0 END) AS stop_rate
            FROM shadow_candidates c
            JOIN shadow_outcomes o
              ON o.candidate_id = c.id
            WHERE o.data_available = 1
            GROUP BY c.strategy, c.policy_name
            """
        )
        for strategy, policy_name, n, avg_ret, stop_rate in cur.fetchall():
            priors[(str(strategy).upper(), str(policy_name).lower())] = {
                "n": int(n or 0),
                "avg_ret": float(avg_ret or 0.0),
                "stop_rate": float(stop_rate or 0.0),
            }
        conn.close()
        return priors

    def _load_closed_trade_counts(self) -> Dict[str, int]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT UPPER(COALESCE(strategy, 'UNKNOWN')) AS strategy, COUNT(*) AS cnt
            FROM trades
            WHERE UPPER(COALESCE(status, '')) = 'CLOSED'
            GROUP BY UPPER(COALESCE(strategy, 'UNKNOWN'))
            """
        )
        counts = {str(strategy): int(cnt or 0) for strategy, cnt in cur.fetchall()}
        conn.close()
        return counts

    def _prior_score(
        self,
        strategy: str,
        rr: Optional[float],
        avg_score: Optional[float],
        regime_status: str = "",
        regime_volatility: str = "",
        vix_level: Optional[float] = None,
    ) -> Tuple[float, str, float, int]:
        closed_trade_count = int(self._closed_trade_counts.get(strategy, 0) or 0)
        if closed_trade_count < MIN_FORECAST_CLOSED_TRADES:
            suppressed_label = (
                f"forecast_prior_suppressed:insufficient_sample(n={closed_trade_count})"
            )
            return 50.0, suppressed_label, 0.0, closed_trade_count

        forecast_kwargs = {
            "strategy": strategy,
            "pathway": "",
            "composite_score": float(avg_score or 50.0),
            "min_samples": 5,
        }
        if getattr(self.forecast_engine, "supports_shadow_context", False):
            forecast_kwargs.update({
                "rr": rr,
                "regime_status": regime_status,
                "regime_volatility": regime_volatility,
                "vix_level": vix_level,
            })
        forecast_ctx = self.forecast_engine.entry_snapshot(**forecast_kwargs)

        forecast_p = _safe_float(forecast_ctx.get("forecast_p_win"))
        forecast_ret = _safe_float(forecast_ctx.get("forecast_expected_return"))
        model_version = str(forecast_ctx.get("forecast_model_version") or "")
        if forecast_p is None:
            forecast_score = 50.0
            forecast_label = "forecast:neutral"
        else:
            forecast_score = forecast_p * 100.0
            if forecast_ret is not None:
                forecast_score += max(-10.0, min(10.0, forecast_ret * 1.25))
            forecast_score = _clamp(forecast_score)
            forecast_label = f"forecast:{forecast_p:.2f}"

        if model_version.startswith("shadow_rcbr"):
            segment = forecast_ctx.get("_segment") or {}
            policy_name = str(segment.get("policy_name") or "").strip()
            if policy_name:
                forecast_label = f"{forecast_label};shadow:{policy_name}"
            return forecast_score, forecast_label, FORECAST_PRIOR_WEIGHT, closed_trade_count

        shadow_policy = self._matching_shadow_policy(strategy, rr)
        if shadow_policy is None:
            return forecast_score, forecast_label, FORECAST_PRIOR_WEIGHT, closed_trade_count

        shadow = self._shadow_priors.get((strategy, shadow_policy))
        if not shadow or shadow.get("n", 0) < 25:
            return forecast_score, forecast_label, FORECAST_PRIOR_WEIGHT, closed_trade_count

        shadow_score = 50.0
        shadow_score += shadow["avg_ret"] * 12.0
        shadow_score -= shadow["stop_rate"] * 100.0 * 0.40
        shadow_score = _clamp(shadow_score)
        combined = _clamp((forecast_score * 0.55) + (shadow_score * 0.45))
        label = (
            f"{forecast_label};shadow:{shadow_policy}"
            f"({shadow['avg_ret']:+.2f}%/{shadow['stop_rate']*100.0:.1f}% stop)"
        )
        return combined, label, FORECAST_PRIOR_WEIGHT, closed_trade_count

    @staticmethod
    def _matching_shadow_policy(strategy: str, rr: Optional[float]) -> Optional[str]:
        if strategy == "SHORT":
            if rr is not None and rr >= 3.0:
                return "short_3_0"
            if rr is not None and rr >= 2.5:
                return "short_2_5"
            return "short_2_5"
        if strategy == "SNIPER":
            if rr is not None and rr >= 2.2:
                return "sniper_2_2"
            return "sniper_2_0"
        return None
