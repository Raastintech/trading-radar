"""
Shadow Rate Forecaster — regime-conditional base rates from shadow outcomes.

This module is additive. It prefers the system's own resolved shadow evidence
when building entry forecasts and falls back to the legacy closed-trade
ForecastEngine when shadow samples are thin or unavailable.
"""

from __future__ import annotations

import math
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

from forecast_engine import ForecastEngine
from vix_snapshot import get_vix_snapshot


MODEL_VERSION = "shadow_rcbr_v2"
DEFAULT_SHADOW_MIN_SAMPLES = 25
DEFAULT_HORIZON_DAYS = 5
DEFAULT_CACHE_TTL_SECONDS = 300


def _score_bucket(score: float) -> str:
    if score >= 80:
        return "HIGH"
    if score >= 65:
        return "MED_HIGH"
    if score >= 50:
        return "MED"
    return "LOW"


def _wilson_conf(wins: int, n: int, z: float = 1.645) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p_hat = wins / n
    denom = 1 + z * z / n
    centre = (p_hat + z * z / (2 * n)) / denom
    spread = z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class ShadowRateForecaster:
    """
    API-compatible replacement for ForecastEngine.entry_snapshot().

    Primary source:
      shadow outcome segment lookup keyed by
      (strategy, regime_volatility, score_bucket, horizon_days)

    Fallback hierarchy:
      1. strategy + regime_volatility + score_bucket + preferred horizon
      2. strategy + regime_volatility + preferred horizon
      3. strategy + score_bucket + preferred horizon
      4. strategy + preferred horizon
      5. same hierarchy without a horizon filter
      6. legacy ForecastEngine fallback
    """

    supports_shadow_context = True

    def __init__(
        self,
        db_path: str = "trading_performance.db",
        base_forecaster: Optional[ForecastEngine] = None,
    ):
        self.db_path = db_path
        self.base_forecaster = base_forecaster or ForecastEngine(db_path=db_path)
        self.shadow_min_samples = self._env_int("SHADOW_FORECAST_MIN_SAMPLES", DEFAULT_SHADOW_MIN_SAMPLES)
        self.preferred_horizon_days = self._env_int("SHADOW_FORECAST_HORIZON_DAYS", DEFAULT_HORIZON_DAYS)
        self.shadow_cache_ttl_seconds = self._env_int(
            "SHADOW_FORECAST_CACHE_TTL_SECONDS",
            DEFAULT_CACHE_TTL_SECONDS,
        )
        self._segment_lookup: Dict[Tuple[str, Optional[str], Optional[str], Optional[int]], Dict[str, Any]] = {}
        self._segment_lookup_loaded_at: float = 0.0

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return int(default)

    def entry_snapshot(
        self,
        strategy: str,
        pathway: str = "",
        composite_score: float = 50.0,
        min_samples: int = 10,
        rr: Optional[float] = None,
        regime_status: str = "",
        regime_volatility: str = "",
        vix_level: Optional[float] = None,
    ) -> dict:
        strategy = str(strategy or "").upper().strip()
        if not strategy:
            return self._fallback_snapshot(pathway=pathway, composite_score=composite_score, min_samples=min_samples)

        if vix_level is None:
            vix_level = _safe_float(get_vix_snapshot().get("vix_level"))
        score_bucket = _score_bucket(float(composite_score or 0.0))
        vol_bucket = self._normalize_volatility_bucket(regime_volatility, vix_level)
        shadow_min = max(int(min_samples or 0), self.shadow_min_samples)

        self._refresh_shadow_segment_lookup()
        segments = self._build_segments(
            strategy=strategy,
            score_bucket=score_bucket,
            regime_volatility=vol_bucket,
        )

        for segment in segments:
            stats = self._segment_lookup.get(self._segment_key(
                strategy=segment["strategy"],
                regime_volatility=segment.get("regime_volatility"),
                score_bucket=segment.get("score_bucket"),
                horizon_days=segment.get("horizon_days"),
            ))
            if stats and int(stats.get("n", 0) or 0) >= shadow_min:
                return self._build_snapshot_from_stats(stats)

        return self._fallback_snapshot(
            strategy=strategy,
            pathway=pathway,
            composite_score=composite_score,
            min_samples=min_samples,
        )

    def exit_error(
        self,
        forecast_expected_return: Optional[float],
        actual_return_pct: float,
    ) -> Optional[float]:
        return self.base_forecaster.exit_error(
            forecast_expected_return=forecast_expected_return,
            actual_return_pct=actual_return_pct,
        )

    def _fallback_snapshot(
        self,
        strategy: Optional[str] = None,
        pathway: str = "",
        composite_score: float = 50.0,
        min_samples: int = 10,
    ) -> dict:
        if strategy is None:
            return {
                "forecast_p_win": None,
                "forecast_expected_return": None,
                "forecast_expected_rr": None,
                "forecast_horizon_days": None,
                "forecast_conf_low": None,
                "forecast_conf_high": None,
                "forecast_model_version": MODEL_VERSION,
                "_segment": {"note": "missing_strategy"},
            }
        return self.base_forecaster.entry_snapshot(
            strategy=strategy,
            pathway=pathway,
            composite_score=composite_score,
            min_samples=min_samples,
        )

    def _build_segments(
        self,
        strategy: str,
        score_bucket: str,
        regime_volatility: str,
    ) -> List[Dict[str, Any]]:
        segments: List[Dict[str, Any]] = []
        seen = set()
        horizons = [self.preferred_horizon_days, None]

        for horizon_days in horizons:
            for use_vol, use_bucket in (
                (True, True),
                (True, False),
                (False, True),
                (False, False),
            ):
                seg = {
                    "strategy": strategy,
                    "regime_volatility": regime_volatility if use_vol else None,
                    "score_bucket": score_bucket if use_bucket else None,
                    "horizon_days": horizon_days,
                }
                key = (
                    seg["strategy"],
                    seg.get("regime_volatility"),
                    seg.get("score_bucket"),
                    seg.get("horizon_days"),
                )
                if key in seen:
                    continue
                seen.add(key)
                segments.append(seg)

        return segments

    def _refresh_shadow_segment_lookup(self, force: bool = False) -> None:
        now = time.monotonic()
        ttl = max(0, int(self.shadow_cache_ttl_seconds or 0))
        if (
            not force
            and self._segment_lookup
            and ttl > 0
            and (now - self._segment_lookup_loaded_at) < ttl
        ):
            return
        self._segment_lookup = self._load_shadow_segment_lookup()
        self._segment_lookup_loaded_at = now

    def _load_shadow_segment_lookup(self) -> Dict[Tuple[str, Optional[str], Optional[str], Optional[int]], Dict[str, Any]]:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                WITH decision_context AS (
                    SELECT
                        run_id,
                        UPPER(COALESCE(ticker, '')) AS ticker_key,
                        UPPER(COALESCE(strategy, '')) AS strategy_key,
                        MAX(COALESCE(avg_score, 0)) AS avg_score,
                        MAX(COALESCE(NULLIF(UPPER(regime_volatility), ''), 'UNKNOWN')) AS regime_volatility
                    FROM decisions
                    GROUP BY run_id, UPPER(COALESCE(ticker, '')), UPPER(COALESCE(strategy, ''))
                )
                SELECT
                    UPPER(COALESCE(sc.strategy, '')) AS strategy,
                    COALESCE(dc.regime_volatility, 'UNKNOWN') AS regime_volatility,
                    CASE
                        WHEN COALESCE(dc.avg_score, 0) >= 80 THEN 'HIGH'
                        WHEN COALESCE(dc.avg_score, 0) >= 65 THEN 'MED_HIGH'
                        WHEN COALESCE(dc.avg_score, 0) >= 50 THEN 'MED'
                        ELSE 'LOW'
                    END AS score_bucket,
                    COALESCE(so.horizon_days, sc.horizon_days, ?) AS horizon_days,
                    COALESCE(so.end_return_pct, 0.0) AS end_return_pct,
                    COALESCE(so.max_favorable_excursion, 0.0) AS max_favorable_excursion,
                    COALESCE(so.max_adverse_excursion, 0.0) AS max_adverse_excursion,
                    COALESCE(so.hit_target, 0) AS hit_target,
                    COALESCE(so.hit_stop, 0) AS hit_stop,
                    sc.entry_price AS entry_price,
                    sc.stop_price AS stop_price
                FROM shadow_outcomes so
                JOIN shadow_candidates sc
                  ON sc.id = so.candidate_id
                LEFT JOIN decision_context dc
                  ON dc.run_id = sc.run_id
                 AND dc.ticker_key = UPPER(COALESCE(sc.ticker, ''))
                 AND dc.strategy_key = UPPER(COALESCE(sc.strategy, ''))
                WHERE so.data_available = 1
                  AND UPPER(COALESCE(sc.strategy, '')) != ''
                """,
                (self.preferred_horizon_days,),
            )
            rows = cur.fetchall()
            conn.close()
            return self._build_segment_lookup(rows)
        except Exception:
            return {}

    @staticmethod
    def _segment_key(
        strategy: str,
        regime_volatility: Optional[str],
        score_bucket: Optional[str],
        horizon_days: Optional[int],
    ) -> Tuple[str, Optional[str], Optional[str], Optional[int]]:
        return (
            str(strategy or "").upper().strip(),
            str(regime_volatility or "").upper().strip() or None if regime_volatility is not None else None,
            str(score_bucket or "").upper().strip() or None if score_bucket is not None else None,
            int(horizon_days) if horizon_days is not None else None,
        )

    def _build_segment_lookup(
        self,
        rows: List[sqlite3.Row],
    ) -> Dict[Tuple[str, Optional[str], Optional[str], Optional[int]], Dict[str, Any]]:
        lookup: Dict[Tuple[str, Optional[str], Optional[str], Optional[int]], Dict[str, Any]] = {}

        for row in rows:
            strategy = str(row["strategy"] or "").upper().strip()
            if not strategy:
                continue
            regime_volatility = str(row["regime_volatility"] or "UNKNOWN").upper().strip() or "UNKNOWN"
            score_bucket = str(row["score_bucket"] or "LOW").upper().strip() or "LOW"
            horizon_days = int(row["horizon_days"] or self.preferred_horizon_days)
            ret = _safe_float(row["end_return_pct"], 0.0) or 0.0
            mfe = _safe_float(row["max_favorable_excursion"], 0.0) or 0.0
            mae = _safe_float(row["max_adverse_excursion"], 0.0) or 0.0
            hit_target = int(row["hit_target"] or 0)
            hit_stop = int(row["hit_stop"] or 0)

            realized_rr = None
            entry_price = _safe_float(row["entry_price"])
            stop_price = _safe_float(row["stop_price"])
            if entry_price is not None and stop_price is not None and entry_price > 0:
                risk_pct = abs(entry_price - stop_price) / entry_price * 100.0
                if risk_pct > 0:
                    realized_rr = ret / risk_pct

            for use_vol in (regime_volatility, None):
                for use_bucket in (score_bucket, None):
                    for use_horizon in (horizon_days, None):
                        key = self._segment_key(strategy, use_vol, use_bucket, use_horizon)
                        agg = lookup.setdefault(
                            key,
                            {
                                "strategy": strategy,
                                "regime_volatility": use_vol,
                                "score_bucket": use_bucket,
                                "horizon_days": use_horizon,
                                "n": 0,
                                "wins": 0,
                                "return_sum": 0.0,
                                "mfe_sum": 0.0,
                                "mae_sum": 0.0,
                                "target_hits": 0,
                                "stop_hits": 0,
                                "rr_sum": 0.0,
                                "rr_count": 0,
                                "horizon_sum": 0.0,
                            },
                        )
                        agg["n"] += 1
                        agg["wins"] += 1 if ret > 0 else 0
                        agg["return_sum"] += ret
                        agg["mfe_sum"] += mfe
                        agg["mae_sum"] += mae
                        agg["target_hits"] += hit_target
                        agg["stop_hits"] += hit_stop
                        agg["horizon_sum"] += horizon_days
                        if realized_rr is not None:
                            agg["rr_sum"] += realized_rr
                            agg["rr_count"] += 1

        finalized: Dict[Tuple[str, Optional[str], Optional[str], Optional[int]], Dict[str, Any]] = {}
        for key, agg in lookup.items():
            n = int(agg["n"] or 0)
            if n <= 0:
                continue
            wins = int(agg["wins"] or 0)
            conf_low, conf_high = _wilson_conf(wins, n)
            avg_horizon = round(float(agg["horizon_sum"]) / n) if n else None
            finalized[key] = {
                "strategy": agg["strategy"],
                "regime_volatility": agg["regime_volatility"],
                "score_bucket": agg["score_bucket"],
                "horizon_days": agg["horizon_days"],
                "avg_horizon_days": int(avg_horizon) if avg_horizon else None,
                "n": n,
                "win_rate": round(wins / n, 4),
                "avg_return": round(float(agg["return_sum"]) / n, 4),
                "avg_mfe": round(float(agg["mfe_sum"]) / n, 4),
                "avg_mae": round(float(agg["mae_sum"]) / n, 4),
                "avg_realized_rr": round(float(agg["rr_sum"]) / int(agg["rr_count"]), 4)
                if int(agg["rr_count"] or 0) > 0 else None,
                "target_rate": round(float(agg["target_hits"]) / n, 4),
                "stop_rate": round(float(agg["stop_hits"]) / n, 4),
                "forecast_conf_low": round(conf_low, 4),
                "forecast_conf_high": round(conf_high, 4),
                "source": "shadow_segment_lookup",
            }
        return finalized

    def _build_snapshot_from_stats(self, stats: Dict[str, Any]) -> dict:
        segment_info = {
            "strategy": stats.get("strategy"),
            "regime_volatility": stats.get("regime_volatility"),
            "score_bucket": stats.get("score_bucket"),
            "horizon_days": stats.get("horizon_days")
            if stats.get("horizon_days") is not None
            else stats.get("avg_horizon_days"),
            "n": stats.get("n"),
            "win_rate": stats.get("win_rate"),
            "avg_return": stats.get("avg_return"),
            "avg_mae": stats.get("avg_mae"),
            "avg_mfe": stats.get("avg_mfe"),
            "stop_rate": stats.get("stop_rate"),
            "target_rate": stats.get("target_rate"),
            "source": stats.get("source"),
        }
        return {
            "forecast_p_win": stats.get("win_rate"),
            "forecast_expected_return": stats.get("avg_return"),
            "forecast_expected_rr": stats.get("avg_realized_rr"),
            "forecast_horizon_days": int(stats["horizon_days"])
            if stats.get("horizon_days") is not None
            else stats.get("avg_horizon_days"),
            "forecast_conf_low": stats.get("forecast_conf_low"),
            "forecast_conf_high": stats.get("forecast_conf_high"),
            "forecast_model_version": MODEL_VERSION,
            "_segment": segment_info,
        }

    @staticmethod
    def _normalize_volatility_bucket(regime_volatility: str, vix_level: Optional[float]) -> str:
        vol = str(regime_volatility or "").upper().strip()
        if vol:
            return vol
        if vix_level is None:
            return "UNKNOWN"
        if vix_level < 20:
            return "NORMAL"
        if vix_level < 25:
            return "ELEVATED"
        if vix_level < 30:
            return "HIGH"
        return "EXTREME"
