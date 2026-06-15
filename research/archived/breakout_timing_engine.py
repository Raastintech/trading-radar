from __future__ import annotations

import argparse
from collections import defaultdict
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from alpaca_data import AlpacaDataFeed
from short_scanner_v1 import ShortScanner
from sniper_scanner_v2 import SniperScannerV2

logger = logging.getLogger(__name__)

MODEL_VERSION = "bte_advisory_v1"
SHORT_MODEL_VERSION = "bte_short_advisory_v1"
DEFAULT_DB_PATH = "trading_performance.db"
DEFAULT_HORIZON_DAYS = 10
DEFAULT_BTE_HORIZONS = (10, 15, 20)
MIN_PRE_BREAKOUT_SCORE = 55.0
MIN_BREAKOUT_VOLUME_RATIO = 1.5
MIN_BREAKOUT_CLOSE_POSITION = 0.60
MIN_PRE_BREAKDOWN_SCORE = 55.0
MAX_BREAKDOWN_CLOSE_POSITION = 0.40
DEFAULT_SNAPSHOT_LOOKBACK_BARS = 90
BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS = 5
BTE_GUIDANCE_MIN_SEGMENT_BREAKOUTS = 2
BTE_SNIPER_SCORE_THRESHOLD = 60.0
BTE_SNIPER_RR_THRESHOLD = 2.5
BTE_SHORT_SCORE_THRESHOLD = 55.0
BTE_SHORT_RR_THRESHOLD = 3.0
DEFAULT_DECISION_SEED_REASONS = (
    "risk_reward_too_low",
    "score_below_threshold",
    "bracket_order_submitted",
)
DEFAULT_SHORT_DECISION_SEED_REASONS = (
    "risk_reward_too_low",
    "score_below_threshold",
    "no_short_pathway_qualified",
    "bracket_order_submitted",
)

_OUTCOME_STATUS_PENDING = "PENDING"
_OUTCOME_STATUS_COMPUTED = "COMPUTED"
_OUTCOME_STATUS_NO_DATA = "NO_DATA"


@dataclass
class BTECandidateSnapshot:
    ticker: str
    strategy_source: str
    candidate_date: str
    snapshot_timestamp: str
    entry_price: float
    breakout_pivot: float
    consolidation_low: float
    recent_atr: float
    avg_volume_20: float
    atr_contraction_pct: float
    volume_ratio: float
    rs_acceleration: float
    close_position: float
    base_tightness: float
    higher_lows: bool
    pivot_distance_pct: float
    pre_breakout_score: float
    candidate_state: str
    model_version: str = MODEL_VERSION


@dataclass
class BTEShortCandidateSnapshot:
    ticker: str
    strategy_source: str
    candidate_date: str
    snapshot_timestamp: str
    entry_price: float
    breakdown_support: float
    consolidation_high: float
    recent_atr: float
    avg_volume_20: float
    atr_contraction_pct: float
    relative_weakness_pct: float
    distribution_days_10: int
    volume_on_decline: bool
    close_position: float
    base_tightness: float
    lower_highs: bool
    support_distance_pct: float
    pre_breakdown_score: float
    candidate_state: str
    model_version: str = SHORT_MODEL_VERSION


class BreakoutTimingFeatureExtractor:
    def __init__(self, data_feed: Any, account_equity: float = 100000.0):
        self.data_feed = data_feed
        self.sniper = SniperScannerV2(data_feed, account_equity=account_equity)

    def extract_snapshot(
        self,
        ticker: str,
        bars: Optional[List[Dict[str, Any]]] = None,
        spy_bars: Optional[List[Dict[str, Any]]] = None,
        strategy_source: str = "SNIPER",
    ) -> Optional[BTECandidateSnapshot]:
        ticker = str(ticker or "").upper().strip()
        if not ticker:
            return None

        if bars is None:
            bars = self.data_feed.get_daily_bars(ticker, days_back=90, adjustment="all")
        if not bars or len(bars) < 30:
            return None

        if spy_bars is None:
            try:
                spy_bars = self.data_feed.get_daily_bars("SPY", days_back=90, adjustment="all")
            except Exception:
                spy_bars = None

        recent_atr = float(self.sniper._calculate_atr(bars[-10:]) or 0.0)
        prior_atr = float(self.sniper._calculate_atr(bars[-30:-10]) or 0.0)
        if recent_atr <= 0 or prior_atr <= 0:
            return None

        current_bar = bars[-1]
        entry_price = float(current_bar["close"])
        if entry_price <= 0:
            return None

        avg_volume_20 = sum(float(b["volume"]) for b in bars[-21:-1]) / 20.0
        if avg_volume_20 <= 0:
            return None

        base_slice = self.sniper._prior_breakout_window(bars)
        if not base_slice or len(base_slice) < 5:
            return None

        contraction_ratio = recent_atr / prior_atr
        atr_contraction_pct = max(0.0, 1.0 - contraction_ratio)
        volume_ratio = float(current_bar["volume"]) / avg_volume_20
        rs_acceleration = (
            float(self.sniper._calculate_rs_acceleration(bars, spy_bars))
            if spy_bars and len(spy_bars) >= 30
            else 0.0
        )

        highs = [float(b["high"]) for b in base_slice]
        lows = [float(b["low"]) for b in base_slice]
        breakout_pivot = max(highs)
        consolidation_low = min(lows)
        base_range = max(highs) - min(lows)
        base_tightness = (base_range / entry_price) if entry_price > 0 else 1.0

        day_range = float(current_bar["high"]) - float(current_bar["low"])
        close_position = (
            (float(current_bar["close"]) - float(current_bar["low"])) / day_range
            if day_range > 0
            else 0.5
        )
        higher_lows = self._higher_lows(base_slice)
        pivot_distance_pct = ((breakout_pivot - entry_price) / entry_price) * 100.0

        pre_breakout_score = self._compute_pre_breakout_score(
            atr_contraction_pct=atr_contraction_pct,
            volume_ratio=volume_ratio,
            rs_acceleration=rs_acceleration,
            base_tightness=base_tightness,
            close_position=close_position,
            higher_lows=higher_lows,
        )
        candidate_state = self._candidate_state(entry_price, breakout_pivot, pivot_distance_pct)

        return BTECandidateSnapshot(
            ticker=ticker,
            strategy_source=str(strategy_source or "SNIPER").upper(),
            candidate_date=self._bar_date_iso(current_bar),
            snapshot_timestamp=self._bar_timestamp_iso(current_bar),
            entry_price=round(entry_price, 4),
            breakout_pivot=round(breakout_pivot, 4),
            consolidation_low=round(consolidation_low, 4),
            recent_atr=round(recent_atr, 4),
            avg_volume_20=round(avg_volume_20, 4),
            atr_contraction_pct=round(atr_contraction_pct, 4),
            volume_ratio=round(volume_ratio, 4),
            rs_acceleration=round(rs_acceleration, 6),
            close_position=round(close_position, 4),
            base_tightness=round(base_tightness, 4),
            higher_lows=bool(higher_lows),
            pivot_distance_pct=round(pivot_distance_pct, 4),
            pre_breakout_score=round(pre_breakout_score, 1),
            candidate_state=candidate_state,
        )

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _compute_pre_breakout_score(
        self,
        *,
        atr_contraction_pct: float,
        volume_ratio: float,
        rs_acceleration: float,
        base_tightness: float,
        close_position: float,
        higher_lows: bool,
    ) -> float:
        contraction_pts = self._clip(atr_contraction_pct / 0.35, 0.0, 1.0) * 25.0
        volume_pts = self._clip((volume_ratio - 1.0) / 1.5, 0.0, 1.0) * 20.0
        rs_pts = self._clip(rs_acceleration / 0.04, 0.0, 1.0) * 20.0
        tightness_pts = self._clip((0.16 - base_tightness) / 0.10, 0.0, 1.0) * 15.0
        close_pts = self._clip(close_position, 0.0, 1.0) * 10.0
        higher_lows_pts = 10.0 if higher_lows else 0.0
        return contraction_pts + volume_pts + rs_pts + tightness_pts + close_pts + higher_lows_pts

    @staticmethod
    def _higher_lows(base_slice: Sequence[Dict[str, Any]]) -> bool:
        if len(base_slice) < 6:
            return False
        half = len(base_slice) // 2
        early_low = min(float(bar["low"]) for bar in base_slice[:half])
        late_low = min(float(bar["low"]) for bar in base_slice[half:])
        return late_low > early_low

    @staticmethod
    def _candidate_state(entry_price: float, breakout_pivot: float, pivot_distance_pct: float) -> str:
        if entry_price > breakout_pivot:
            return "TRIGGERING"
        if pivot_distance_pct <= 1.5:
            return "IMMINENT"
        if pivot_distance_pct <= 4.0:
            return "PRE_BREAKOUT"
        return "EARLY_BASE"

    @staticmethod
    def _bar_date_iso(bar: Dict[str, Any]) -> str:
        ts = bar.get("timestamp")
        if hasattr(ts, "date"):
            return ts.date().isoformat()
        return str(ts)[:10]

    @staticmethod
    def _bar_timestamp_iso(bar: Dict[str, Any]) -> str:
        ts = bar.get("timestamp")
        if hasattr(ts, "isoformat"):
            return ts.isoformat()
        return str(ts or "")


class ShortTimingFeatureExtractor:
    def __init__(self, data_feed: Any, account_equity: float = 100000.0):
        self.data_feed = data_feed
        self.short_scanner = ShortScanner(data_feed, account_equity=account_equity)

    def extract_snapshot(
        self,
        ticker: str,
        bars: Optional[List[Dict[str, Any]]] = None,
        strategy_source: str = "SHORT",
    ) -> Optional[BTEShortCandidateSnapshot]:
        ticker = str(ticker or "").upper().strip()
        if not ticker:
            return None

        if bars is None:
            bars = self.data_feed.get_daily_bars(ticker, days_back=90, adjustment="all")
        if not bars or len(bars) < 30:
            return None

        recent_atr = float(self.short_scanner._calc_atr(bars[-10:]) or 0.0)
        prior_atr = float(self.short_scanner._calc_atr(bars[-30:-10]) or 0.0)
        if recent_atr <= 0 or prior_atr <= 0:
            return None

        current_bar = bars[-1]
        entry_price = float(current_bar["close"])
        if entry_price <= 0:
            return None

        avg_volume_20 = sum(float(b["volume"]) for b in bars[-21:-1]) / 20.0
        if avg_volume_20 <= 0:
            return None

        base_slice = bars[-21:-1]
        if not base_slice or len(base_slice) < 10:
            return None

        contraction_ratio = recent_atr / prior_atr
        atr_contraction_pct = max(0.0, 1.0 - contraction_ratio)

        highs = [float(b["high"]) for b in base_slice]
        lows = [float(b["low"]) for b in base_slice]
        breakdown_support = min(lows)
        consolidation_high = max(highs)
        base_range = consolidation_high - breakdown_support
        base_tightness = (base_range / entry_price) if entry_price > 0 else 1.0

        day_range = float(current_bar["high"]) - float(current_bar["low"])
        close_position = (
            (float(current_bar["close"]) - float(current_bar["low"])) / day_range
            if day_range > 0
            else 0.5
        )
        support_distance_pct = ((entry_price - breakdown_support) / entry_price) * 100.0
        lower_highs = self._lower_highs(base_slice)

        try:
            rs_ctx = self.short_scanner._compute_relative_weakness(
                ticker=ticker,
                bars=bars,
                benchmark_cache={},
            ) or {}
        except Exception:
            rs_ctx = {}

        relative_weakness_pct = float(rs_ctx.get("relative_strength_pct", 0.0) or 0.0)
        distribution_days_10 = int(rs_ctx.get("distribution_days_10", 0) or 0)
        volume_on_decline = bool(rs_ctx.get("volume_on_decline", False))
        institutional_distribution = bool(rs_ctx.get("institutional_distribution", False))

        pre_breakdown_score = self._compute_pre_breakdown_score(
            atr_contraction_pct=atr_contraction_pct,
            relative_weakness_pct=relative_weakness_pct,
            distribution_days_10=distribution_days_10,
            institutional_distribution=institutional_distribution,
            base_tightness=base_tightness,
            close_position=close_position,
            lower_highs=lower_highs,
            volume_on_decline=volume_on_decline,
        )
        candidate_state = self._candidate_state(entry_price, breakdown_support, support_distance_pct)

        return BTEShortCandidateSnapshot(
            ticker=ticker,
            strategy_source=str(strategy_source or "SHORT").upper(),
            candidate_date=BreakoutTimingFeatureExtractor._bar_date_iso(current_bar),
            snapshot_timestamp=BreakoutTimingFeatureExtractor._bar_timestamp_iso(current_bar),
            entry_price=round(entry_price, 4),
            breakdown_support=round(breakdown_support, 4),
            consolidation_high=round(consolidation_high, 4),
            recent_atr=round(recent_atr, 4),
            avg_volume_20=round(avg_volume_20, 4),
            atr_contraction_pct=round(atr_contraction_pct, 4),
            relative_weakness_pct=round(relative_weakness_pct, 4),
            distribution_days_10=distribution_days_10,
            volume_on_decline=bool(volume_on_decline),
            close_position=round(close_position, 4),
            base_tightness=round(base_tightness, 4),
            lower_highs=bool(lower_highs),
            support_distance_pct=round(support_distance_pct, 4),
            pre_breakdown_score=round(pre_breakdown_score, 1),
            candidate_state=candidate_state,
        )

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _compute_pre_breakdown_score(
        self,
        *,
        atr_contraction_pct: float,
        relative_weakness_pct: float,
        distribution_days_10: int,
        institutional_distribution: bool,
        base_tightness: float,
        close_position: float,
        lower_highs: bool,
        volume_on_decline: bool,
    ) -> float:
        contraction_pts = self._clip(atr_contraction_pct / 0.35, 0.0, 1.0) * 20.0
        weakness_pts = self._clip((-relative_weakness_pct) / 6.0, 0.0, 1.0) * 25.0
        distribution_pts = self._clip(distribution_days_10 / 4.0, 0.0, 1.0) * 10.0
        confirmation_pts = 10.0 if (institutional_distribution or volume_on_decline) else 0.0
        tightness_pts = self._clip((0.16 - base_tightness) / 0.10, 0.0, 1.0) * 15.0
        close_pts = self._clip(1.0 - close_position, 0.0, 1.0) * 10.0
        lower_highs_pts = 10.0 if lower_highs else 0.0
        return (
            contraction_pts
            + weakness_pts
            + distribution_pts
            + confirmation_pts
            + tightness_pts
            + close_pts
            + lower_highs_pts
        )

    @staticmethod
    def _lower_highs(base_slice: Sequence[Dict[str, Any]]) -> bool:
        if len(base_slice) < 6:
            return False
        half = len(base_slice) // 2
        early_high = max(float(bar["high"]) for bar in base_slice[:half])
        late_high = max(float(bar["high"]) for bar in base_slice[half:])
        return late_high < early_high

    @staticmethod
    def _candidate_state(entry_price: float, breakdown_support: float, support_distance_pct: float) -> str:
        if entry_price < breakdown_support:
            return "TRIGGERING"
        if support_distance_pct <= 1.5:
            return "IMMINENT"
        if support_distance_pct <= 4.0:
            return "PRE_BREAKDOWN"
        return "EARLY_TOP"


class BreakoutTimingTracker:
    supports_advisory_context = True

    def __init__(self, db_path: str = DEFAULT_DB_PATH, data_feed: Any = None):
        self.db_path = db_path
        self.data_feed = data_feed or AlpacaDataFeed()
        self.extractor = BreakoutTimingFeatureExtractor(self.data_feed)
        self.short_extractor = ShortTimingFeatureExtractor(self.data_feed)
        self._calibration_cache: Dict[tuple[int, int], Dict[str, Any]] = {}
        self._short_calibration_cache: Dict[tuple[int, int], Dict[str, Any]] = {}
        self._ensure_tables()

    def seed_from_tickers(
        self,
        tickers: Iterable[str],
        *,
        strategy_source: str = "SNIPER",
        min_score: float = MIN_PRE_BREAKOUT_SCORE,
        regime_context: Optional[Dict[str, Any]] = None,
    ) -> int:
        inserted = 0
        spy_bars = None
        try:
            spy_bars = self.data_feed.get_daily_bars("SPY", days_back=90, adjustment="all")
        except Exception:
            spy_bars = None

        for ticker in tickers or []:
            try:
                snapshot = self.extractor.extract_snapshot(
                    ticker,
                    spy_bars=spy_bars,
                    strategy_source=strategy_source,
                )
            except Exception as exc:
                logger.debug("BTE extract %s failed: %s", ticker, exc)
                continue
            if snapshot is None:
                continue
            if snapshot.pre_breakout_score < float(min_score):
                continue
            if snapshot.candidate_state == "TRIGGERING":
                continue
            if self.record_candidate(snapshot, decision_context=self._regime_decision_context(regime_context)):
                inserted += 1
        return inserted

    def seed_from_sniper_watchlist(
        self,
        *,
        tickers: Optional[Iterable[str]] = None,
        min_score: float = MIN_PRE_BREAKOUT_SCORE,
        max_tickers: Optional[int] = None,
        regime_context: Optional[Dict[str, Any]] = None,
    ) -> int:
        if tickers is None:
            from sniper_adaptive_universe import SniperAdaptiveUniverse

            builder = SniperAdaptiveUniverse(self.data_feed)
            universe = builder.build_universe() or {}
            tickers = universe.get("tickers", []) if isinstance(universe, dict) else []

        watchlist = [str(ticker or "").upper().strip() for ticker in (tickers or []) if str(ticker or "").strip()]
        if max_tickers is not None:
            watchlist = watchlist[: max(1, int(max_tickers))]
        inserted = 0
        spy_bars = None
        try:
            spy_bars = self.data_feed.get_daily_bars("SPY", days_back=90, adjustment="all")
        except Exception:
            spy_bars = None

        for ticker in watchlist:
            try:
                snapshot = self.extractor.extract_snapshot(
                    ticker,
                    spy_bars=spy_bars,
                    strategy_source="SNIPER",
                )
            except Exception as exc:
                logger.debug("BTE watchlist extract %s failed: %s", ticker, exc)
                continue
            if snapshot is None:
                continue
            if snapshot.pre_breakout_score < float(min_score):
                continue
            if snapshot.candidate_state == "TRIGGERING":
                continue
            if self.record_candidate(
                snapshot,
                seed_source="watchlist_sniper",
                decision_context=self._regime_decision_context(regime_context),
            ):
                inserted += 1
        return inserted

    def seed_from_recent_sniper_decisions(
        self,
        *,
        days: int = 30,
        include_reasons: Optional[Sequence[str]] = None,
        min_pre_breakout_score: float = MIN_PRE_BREAKOUT_SCORE,
        min_decision_score: float = 0.0,
    ) -> int:
        reasons = tuple(
            str(reason or "").strip().lower()
            for reason in (include_reasons or DEFAULT_DECISION_SEED_REASONS)
            if str(reason or "").strip()
        )
        rows = self._fetch_recent_sniper_decisions(
            days=int(days),
            include_reasons=reasons,
            min_decision_score=float(min_decision_score),
        )
        if not rows:
            return 0

        spy_cache: Dict[str, List[Dict[str, Any]]] = {}
        inserted = 0
        for row in self._dedupe_best_decision_per_ticker_day(rows):
            candidate_day = self._decision_date(row["timestamp"])
            bars = self._historical_bars_up_to(
                ticker=row["ticker"],
                end_date=candidate_day,
                lookback_bars=DEFAULT_SNAPSHOT_LOOKBACK_BARS,
            )
            if not bars or len(bars) < 30:
                continue

            spy_key = candidate_day.isoformat()
            spy_bars = spy_cache.get(spy_key)
            if spy_bars is None:
                spy_bars = self._historical_bars_up_to(
                    ticker="SPY",
                    end_date=candidate_day,
                    lookback_bars=DEFAULT_SNAPSHOT_LOOKBACK_BARS,
                )
                spy_cache[spy_key] = spy_bars
            if not spy_bars or len(spy_bars) < 30:
                continue

            snapshot = self.extractor.extract_snapshot(
                row["ticker"],
                bars=bars,
                spy_bars=spy_bars,
                strategy_source="SNIPER",
            )
            if snapshot is None:
                continue
            if snapshot.pre_breakout_score < float(min_pre_breakout_score):
                continue
            if snapshot.candidate_state == "TRIGGERING":
                continue

            if self.record_candidate(
                snapshot,
                seed_source="decisions_sniper",
                decision_context={
                    "decision_id": row["id"],
                    "run_id": row["run_id"],
                    "council_decision": row["council_decision"],
                    "decision_cohort": self._sniper_decision_cohort(
                        council_decision=row["council_decision"],
                        council_reason=row["council_reason"],
                        rr=row["rr"],
                        avg_score=row["avg_score"],
                    ),
                    "decision_reason": row["council_reason"],
                    "decision_timestamp": row["timestamp"],
                    "decision_score": row["avg_score"],
                    "scanner_rr": row["rr"],
                    "regime_status": row["regime_status"] if "regime_status" in row.keys() else None,
                    "regime_volatility": row["regime_volatility"] if "regime_volatility" in row.keys() else None,
                    "regime_vix": row["regime_vix"] if "regime_vix" in row.keys() else None,
                    "vix_level": row["vix_level"] if "vix_level" in row.keys() else None,
                },
            ):
                inserted += 1
        return inserted

    def seed_from_recent_short_decisions(
        self,
        *,
        days: int = 30,
        include_reasons: Optional[Sequence[str]] = None,
        min_pre_breakdown_score: float = MIN_PRE_BREAKDOWN_SCORE,
        min_decision_score: float = 0.0,
    ) -> int:
        reasons = tuple(
            str(reason or "").strip().lower()
            for reason in (include_reasons or DEFAULT_SHORT_DECISION_SEED_REASONS)
            if str(reason or "").strip()
        )
        rows = self._fetch_recent_short_decisions(
            days=int(days),
            include_reasons=reasons,
            min_decision_score=float(min_decision_score),
        )
        if not rows:
            return 0

        inserted = 0
        for row in self._dedupe_best_decision_per_ticker_day(rows):
            candidate_day = self._decision_date(row["timestamp"])
            bars = self._historical_bars_up_to(
                ticker=row["ticker"],
                end_date=candidate_day,
                lookback_bars=DEFAULT_SNAPSHOT_LOOKBACK_BARS,
            )
            if not bars or len(bars) < 30:
                continue

            snapshot = self.short_extractor.extract_snapshot(
                row["ticker"],
                bars=bars,
                strategy_source="SHORT",
            )
            if snapshot is None:
                continue
            if snapshot.pre_breakdown_score < float(min_pre_breakdown_score):
                continue
            if snapshot.candidate_state == "TRIGGERING":
                continue

            if self.record_short_candidate(
                snapshot,
                seed_source="decisions_short",
                decision_context={
                    "decision_id": row["id"],
                    "run_id": row["run_id"],
                    "council_decision": row["council_decision"],
                    "decision_cohort": self._short_decision_cohort(
                        council_decision=row["council_decision"],
                        council_reason=row["council_reason"],
                        rr=row["rr"],
                        avg_score=row["avg_score"],
                    ),
                    "decision_reason": row["council_reason"],
                    "decision_timestamp": row["timestamp"],
                    "decision_score": row["avg_score"],
                    "scanner_rr": row["rr"],
                    "regime_status": row["regime_status"] if "regime_status" in row.keys() else None,
                    "regime_volatility": row["regime_volatility"] if "regime_volatility" in row.keys() else None,
                    "regime_vix": row["regime_vix"] if "regime_vix" in row.keys() else None,
                    "vix_level": row["vix_level"] if "vix_level" in row.keys() else None,
                },
            ):
                inserted += 1
        return inserted

    def record_candidate(
        self,
        snapshot: BTECandidateSnapshot,
        *,
        seed_source: str = "manual",
        decision_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        row = asdict(snapshot)
        now = datetime.now(timezone.utc).isoformat()
        decision_context = dict(decision_context or {})
        features_payload = dict(row)
        if decision_context:
            features_payload["decision_context"] = decision_context
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO bte_candidates (
                created_at, seeded_at, ticker, strategy_source, candidate_date,
                snapshot_timestamp, entry_price, breakout_pivot, consolidation_low,
                recent_atr, avg_volume_20, atr_contraction_pct, volume_ratio,
                rs_acceleration, close_position, base_tightness, higher_lows,
                pivot_distance_pct, pre_breakout_score, candidate_state,
                model_version, outcome_status, seed_source, decision_id,
                run_id, council_decision, decision_cohort, decision_reason,
                decision_score, scanner_rr, regime_status, regime_volatility,
                regime_vix, vix_level, features_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                now,
                now,
                row["ticker"],
                row["strategy_source"],
                row["candidate_date"],
                row["snapshot_timestamp"],
                row["entry_price"],
                row["breakout_pivot"],
                row["consolidation_low"],
                row["recent_atr"],
                row["avg_volume_20"],
                row["atr_contraction_pct"],
                row["volume_ratio"],
                row["rs_acceleration"],
                row["close_position"],
                row["base_tightness"],
                int(row["higher_lows"]),
                row["pivot_distance_pct"],
                row["pre_breakout_score"],
                row["candidate_state"],
                row["model_version"],
                _OUTCOME_STATUS_PENDING,
                str(seed_source or "manual"),
                decision_context.get("decision_id"),
                decision_context.get("run_id"),
                decision_context.get("council_decision"),
                decision_context.get("decision_cohort"),
                decision_context.get("decision_reason"),
                decision_context.get("decision_score"),
                decision_context.get("scanner_rr"),
                self._normalize_regime_label(decision_context.get("regime_status")),
                self._normalize_regime_label(decision_context.get("regime_volatility")),
                self._normalize_regime_label(decision_context.get("regime_vix")),
                self._safe_float(decision_context.get("vix_level")),
                json.dumps(features_payload, default=str),
            ),
        )
        inserted = cur.rowcount > 0
        conn.commit()
        conn.close()
        return inserted

    def record_short_candidate(
        self,
        snapshot: BTEShortCandidateSnapshot,
        *,
        seed_source: str = "manual",
        decision_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        row = asdict(snapshot)
        now = datetime.now(timezone.utc).isoformat()
        decision_context = dict(decision_context or {})
        features_payload = dict(row)
        if decision_context:
            features_payload["decision_context"] = decision_context
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO bte_short_candidates (
                created_at, seeded_at, ticker, strategy_source, candidate_date,
                snapshot_timestamp, entry_price, breakdown_support, consolidation_high,
                recent_atr, avg_volume_20, atr_contraction_pct, relative_weakness_pct,
                distribution_days_10, volume_on_decline, close_position, base_tightness,
                lower_highs, support_distance_pct, pre_breakdown_score, candidate_state,
                model_version, outcome_status, seed_source, decision_id,
                run_id, council_decision, decision_cohort, decision_reason,
                decision_score, scanner_rr, regime_status, regime_volatility,
                regime_vix, vix_level, features_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                now,
                now,
                row["ticker"],
                row["strategy_source"],
                row["candidate_date"],
                row["snapshot_timestamp"],
                row["entry_price"],
                row["breakdown_support"],
                row["consolidation_high"],
                row["recent_atr"],
                row["avg_volume_20"],
                row["atr_contraction_pct"],
                row["relative_weakness_pct"],
                row["distribution_days_10"],
                int(row["volume_on_decline"]),
                row["close_position"],
                row["base_tightness"],
                int(row["lower_highs"]),
                row["support_distance_pct"],
                row["pre_breakdown_score"],
                row["candidate_state"],
                row["model_version"],
                _OUTCOME_STATUS_PENDING,
                str(seed_source or "manual"),
                decision_context.get("decision_id"),
                decision_context.get("run_id"),
                decision_context.get("council_decision"),
                decision_context.get("decision_cohort"),
                decision_context.get("decision_reason"),
                decision_context.get("decision_score"),
                decision_context.get("scanner_rr"),
                self._normalize_regime_label(decision_context.get("regime_status")),
                self._normalize_regime_label(decision_context.get("regime_volatility")),
                self._normalize_regime_label(decision_context.get("regime_vix")),
                self._safe_float(decision_context.get("vix_level")),
                json.dumps(features_payload, default=str),
            ),
        )
        inserted = cur.rowcount > 0
        conn.commit()
        conn.close()
        return inserted

    @staticmethod
    def _sniper_decision_cohort(
        *,
        council_decision: Any,
        council_reason: Any,
        rr: Any,
        avg_score: Any,
    ) -> str:
        decision = str(council_decision or "").upper().strip()
        reason = str(council_reason or "").lower()
        rr_value = float(rr or 0.0)
        score_value = float(avg_score or 0.0)

        if decision == "ENTRY_SUBMITTED":
            return "APPROVED"

        rr_ratio = rr_value / BTE_SNIPER_RR_THRESHOLD if BTE_SNIPER_RR_THRESHOLD > 0 else 0.0
        score_gap = BTE_SNIPER_SCORE_THRESHOLD - score_value
        if "risk_reward_too_low" in reason and rr_ratio >= 0.80:
            return "NEAR_MISS"
        if "score_below_threshold" in reason and score_gap <= 8.0:
            return "NEAR_MISS"
        if score_value >= (BTE_SNIPER_SCORE_THRESHOLD - 6.0):
            return "NEAR_MISS"
        return "REJECT"

    @staticmethod
    def _short_decision_cohort(
        *,
        council_decision: Any,
        council_reason: Any,
        rr: Any,
        avg_score: Any,
    ) -> str:
        decision = str(council_decision or "").upper().strip()
        reason = str(council_reason or "").lower()
        rr_value = float(rr or 0.0)
        score_value = float(avg_score or 0.0)

        if decision == "ENTRY_SUBMITTED":
            return "APPROVED"

        rr_ratio = rr_value / BTE_SHORT_RR_THRESHOLD if BTE_SHORT_RR_THRESHOLD > 0 else 0.0
        score_gap = BTE_SHORT_SCORE_THRESHOLD - score_value
        if "risk_reward_too_low" in reason and rr_ratio >= 0.80:
            return "NEAR_MISS"
        if "score_below_threshold" in reason and score_gap <= 8.0:
            return "NEAR_MISS"
        if score_value >= (BTE_SHORT_SCORE_THRESHOLD - 6.0):
            return "NEAR_MISS"
        return "REJECT"

    def label_outcomes(
        self,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        retry_no_data: bool = True,
    ) -> int:
        pending = self._fetch_pending_candidates(retry_no_data=retry_no_data)
        computed = 0
        for cand in pending:
            try:
                age_days = self._candidate_age_days(cand["candidate_date"])
                if age_days < int(horizon_days) + 2:
                    continue
                bars, source = self._fetch_forward_bars(
                    ticker=cand["ticker"],
                    candidate_date=cand["candidate_date"],
                    horizon_days=horizon_days,
                )
                if not bars:
                    if age_days >= int(horizon_days) + 7:
                        self._mark_no_data(int(cand["id"]))
                    continue
                if len(bars) < int(horizon_days):
                    if age_days >= int(horizon_days) + 7:
                        self._mark_no_data(int(cand["id"]))
                    continue
                outcome = self._compute_breakout_outcome(cand, bars[: int(horizon_days)])
                outcome["data_source"] = source
                self._insert_outcome(int(cand["id"]), int(horizon_days), outcome)
                self._update_candidate_status(int(cand["id"]), _OUTCOME_STATUS_COMPUTED)
                computed += 1
            except Exception as exc:
                logger.warning("BTE outcome %s failed: %s", cand.get("ticker"), exc)
        return computed

    def label_short_outcomes(
        self,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        retry_no_data: bool = True,
    ) -> int:
        pending = self._fetch_pending_short_candidates(retry_no_data=retry_no_data)
        computed = 0
        for cand in pending:
            try:
                age_days = self._candidate_age_days(cand["candidate_date"])
                if age_days < int(horizon_days) + 2:
                    continue
                bars, source = self._fetch_forward_bars(
                    ticker=cand["ticker"],
                    candidate_date=cand["candidate_date"],
                    horizon_days=horizon_days,
                )
                if not bars:
                    if age_days >= int(horizon_days) + 7:
                        self._mark_short_no_data(int(cand["id"]))
                    continue
                if len(bars) < int(horizon_days):
                    if age_days >= int(horizon_days) + 7:
                        self._mark_short_no_data(int(cand["id"]))
                    continue
                outcome = self._compute_breakdown_outcome(cand, bars[: int(horizon_days)])
                outcome["data_source"] = source
                self._insert_short_outcome(int(cand["id"]), int(horizon_days), outcome)
                self._update_short_candidate_status(int(cand["id"]), _OUTCOME_STATUS_COMPUTED)
                computed += 1
            except Exception as exc:
                logger.warning("BTE short outcome %s failed: %s", cand.get("ticker"), exc)
        return computed

    def get_summary(self, *, horizon_days: int = DEFAULT_HORIZON_DAYS) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                COUNT(*) AS n,
                SUM(CASE WHEN breakout_triggered = 1 THEN 1 ELSE 0 END) AS breakout_count,
                AVG(CASE WHEN breakout_triggered = 1 THEN trading_days_to_breakout END) AS avg_days_to_breakout,
                AVG(end_return_pct) AS avg_end_return_pct,
                AVG(max_close_pct_above_pivot) AS avg_max_close_pct_above_pivot
            FROM bte_outcomes
            WHERE horizon_days = ? AND data_available = 1
            """,
            (int(horizon_days),),
        )
        row = cur.fetchone()
        conn.close()
        n = int((row["n"] or 0) if row else 0)
        breakout_count = int((row["breakout_count"] or 0) if row else 0)
        breakout_rate = round((breakout_count / n), 4) if n > 0 else None
        return {
            "horizon_days": int(horizon_days),
            "n": n,
            "breakout_count": breakout_count,
            "breakout_rate": breakout_rate,
            "avg_days_to_breakout": round(float(row["avg_days_to_breakout"]), 2)
            if row and row["avg_days_to_breakout"] is not None else None,
            "avg_end_return_pct": round(float(row["avg_end_return_pct"]), 3)
            if row and row["avg_end_return_pct"] is not None else None,
            "avg_max_close_pct_above_pivot": round(float(row["avg_max_close_pct_above_pivot"]), 3)
            if row and row["avg_max_close_pct_above_pivot"] is not None else None,
            "model_version": MODEL_VERSION,
        }

    def get_segmented_summary(self, *, horizon_days: int = DEFAULT_HORIZON_DAYS) -> Dict[str, Any]:
        rows = self._fetch_labeled_rows(horizon_days=int(horizon_days))
        overall = self.get_summary(horizon_days=int(horizon_days))
        status_counts = self._fetch_candidate_status_counts()
        overall_breakout_count = int(overall.get("breakout_count") or 0)
        timing_days = [
            int(row["trading_days_to_breakout"])
            for row in rows
            if int(row["breakout_triggered"] or 0) == 1 and row["trading_days_to_breakout"] is not None
        ]
        return {
            "model_version": MODEL_VERSION,
            "overall": overall,
            "candidate_status": status_counts,
            "guidance_ready": overall_breakout_count >= BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS,
            "guidance_reason": (
                None if overall_breakout_count >= BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS else
                f"insufficient_total_breakouts({overall_breakout_count}<{BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS})"
            ),
            "segments": {
                "candidate_state": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["candidate_state"] or "UNKNOWN").upper(),
                    ordering=["EARLY_BASE", "PRE_BREAKOUT", "IMMINENT", "TRIGGERING", "UNKNOWN"],
                ),
                "score_bucket": self._summarize_segments(
                    rows,
                    key_fn=lambda row: self._score_bucket(row["pre_breakout_score"]),
                    ordering=["LOW", "MEDIUM", "HIGH", "ELITE"],
                ),
                "compression_bucket": self._summarize_segments(
                    rows,
                    key_fn=lambda row: self._compression_bucket(row["atr_contraction_pct"]),
                    ordering=["LOOSE", "BUILDING", "COMPRESSED", "TIGHT"],
                ),
                "decision_cohort": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["decision_cohort"] or "UNSEEDED").upper(),
                    ordering=["APPROVED", "NEAR_MISS", "REJECT", "UNSEEDED"],
                ),
                "decision_reason": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["decision_reason"] or "UNSEEDED").lower(),
                    ordering=[],
                ),
                "regime_status": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_status"] or "UNKNOWN").upper(),
                    ordering=["BULL", "SIDEWAYS", "BEAR", "CORRECTION", "UNKNOWN"],
                ),
                "regime_volatility": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_volatility"] or "UNKNOWN").upper(),
                    ordering=["CALM", "NORMAL", "ELEVATED", "HIGH", "EXTREME", "UNKNOWN"],
                ),
                "regime_vix": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_vix"] or "UNKNOWN").upper(),
                    ordering=["CALM", "NORMAL", "ELEVATED", "HIGH", "EXTREME", "UNKNOWN"],
                ),
            },
            "timing_distribution": self._timing_distribution(
                timing_days,
                overall_n=int(overall["n"] or 0),
            ),
        }

    def get_calibration_report(
        self,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        min_segment_n: int = 5,
    ) -> Dict[str, Any]:
        rows = self._fetch_labeled_rows(horizon_days=int(horizon_days))
        overall = self.get_summary(horizon_days=int(horizon_days))
        overall_breakout_count = int(overall.get("breakout_count") or 0)
        overall_breakout_rate = float(overall["breakout_rate"] or 0.0)
        min_segment_n = max(1, int(min_segment_n))
        guidance_ready = overall_breakout_count >= BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS
        return {
            "model_version": MODEL_VERSION,
            "horizon_days": int(horizon_days),
            "min_segment_n": min_segment_n,
            "guidance_ready": guidance_ready,
            "guidance_requirements": {
                "min_total_breakouts": BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS,
                "min_segment_breakouts": BTE_GUIDANCE_MIN_SEGMENT_BREAKOUTS,
                "min_segment_n": min_segment_n,
            },
            "guidance_reason": (
                None if guidance_ready else
                f"insufficient_total_breakouts({overall_breakout_count}<{BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS})"
            ),
            "overall": overall,
            "dimensions": {
                "candidate_state": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["candidate_state"] or "UNKNOWN").upper(),
                    ordering=["EARLY_BASE", "PRE_BREAKOUT", "IMMINENT", "TRIGGERING", "UNKNOWN"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "score_bucket": self._calibration_segments(
                    rows,
                    key_fn=lambda row: self._score_bucket(row["pre_breakout_score"]),
                    ordering=["LOW", "MEDIUM", "HIGH", "ELITE"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "compression_bucket": self._calibration_segments(
                    rows,
                    key_fn=lambda row: self._compression_bucket(row["atr_contraction_pct"]),
                    ordering=["LOOSE", "BUILDING", "COMPRESSED", "TIGHT"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "decision_cohort": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["decision_cohort"] or "UNSEEDED").upper(),
                    ordering=["APPROVED", "NEAR_MISS", "REJECT", "UNSEEDED"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "decision_reason": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["decision_reason"] or "UNSEEDED").lower(),
                    ordering=[],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "regime_status": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_status"] or "UNKNOWN").upper(),
                    ordering=["BULL", "SIDEWAYS", "BEAR", "CORRECTION", "UNKNOWN"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "regime_volatility": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_volatility"] or "UNKNOWN").upper(),
                    ordering=["CALM", "NORMAL", "ELEVATED", "HIGH", "EXTREME", "UNKNOWN"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "regime_vix": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_vix"] or "UNKNOWN").upper(),
                    ordering=["CALM", "NORMAL", "ELEVATED", "HIGH", "EXTREME", "UNKNOWN"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
            },
            "composite_segments": self._composite_calibration_segments(
                rows,
                overall_breakout_rate=overall_breakout_rate,
                overall_breakout_count=overall_breakout_count,
                min_segment_n=min_segment_n,
            ),
        }

    def get_score_comparison_report(
        self,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        min_segment_n: int = 5,
    ) -> Dict[str, Any]:
        rows = [
            row
            for row in self._fetch_labeled_rows(horizon_days=int(horizon_days))
            if row["decision_score"] is not None
        ]
        overall = self._overall_from_rows(rows, horizon_days=int(horizon_days))
        overall_breakout_count = int(overall.get("breakout_count") or 0)
        overall_breakout_rate = float(overall["breakout_rate"] or 0.0)
        min_segment_n = max(1, int(min_segment_n))
        guidance_ready = overall_breakout_count >= BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS

        bte_rows = self._calibration_segments(
            rows,
            key_fn=lambda row: self._score_bucket(row["pre_breakout_score"]),
            ordering=["LOW", "MEDIUM", "HIGH", "ELITE"],
            overall_breakout_rate=overall_breakout_rate,
            overall_breakout_count=overall_breakout_count,
            min_segment_n=min_segment_n,
        )
        raw_rows = self._calibration_segments(
            rows,
            key_fn=lambda row: self._score_bucket(row["decision_score"]),
            ordering=["LOW", "MEDIUM", "HIGH", "ELITE"],
            overall_breakout_rate=overall_breakout_rate,
            overall_breakout_count=overall_breakout_count,
            min_segment_n=min_segment_n,
        )

        best_bte = self._best_guidance_row(bte_rows)
        best_raw = self._best_guidance_row(raw_rows)
        winner_dimension = None
        winner_reason = None
        if not guidance_ready:
            winner_reason = f"insufficient_total_breakouts({overall_breakout_count}<{BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS})"
        elif best_bte and not best_raw:
            winner_dimension = "BTE_PRE_BREAKOUT_SCORE"
        elif best_raw and not best_bte:
            winner_dimension = "RAW_SNIPER_SCORE"
        elif best_bte and best_raw:
            bte_lift = float(best_bte.get("lift_vs_overall") or 0.0)
            raw_lift = float(best_raw.get("lift_vs_overall") or 0.0)
            if bte_lift > raw_lift:
                winner_dimension = "BTE_PRE_BREAKOUT_SCORE"
            elif raw_lift > bte_lift:
                winner_dimension = "RAW_SNIPER_SCORE"
            else:
                winner_dimension = "TIE"
        else:
            winner_reason = "no_guidance_eligible_score_bucket"

        return {
            "model_version": MODEL_VERSION,
            "horizon_days": int(horizon_days),
            "min_segment_n": min_segment_n,
            "guidance_ready": guidance_ready,
            "guidance_reason": (
                None if guidance_ready else
                f"insufficient_total_breakouts({overall_breakout_count}<{BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS})"
            ),
            "overall": overall,
            "dimensions": {
                "bte_pre_breakout_score": bte_rows,
                "raw_sniper_score": raw_rows,
            },
            "best_bte_bucket": best_bte,
            "best_raw_bucket": best_raw,
            "winner_dimension": winner_dimension,
            "winner_reason": winner_reason,
        }

    def get_short_summary(self, *, horizon_days: int = DEFAULT_HORIZON_DAYS) -> Dict[str, Any]:
        rows = self._fetch_short_labeled_rows(horizon_days=int(horizon_days))
        overall = self._overall_from_rows(rows, horizon_days=int(horizon_days))
        overall["model_version"] = SHORT_MODEL_VERSION
        return overall

    def get_short_segmented_summary(self, *, horizon_days: int = DEFAULT_HORIZON_DAYS) -> Dict[str, Any]:
        rows = self._fetch_short_labeled_rows(horizon_days=int(horizon_days))
        overall = self.get_short_summary(horizon_days=int(horizon_days))
        status_counts = self._fetch_short_candidate_status_counts()
        overall_breakout_count = int(overall.get("breakout_count") or 0)
        timing_days = [
            int(row["trading_days_to_breakout"])
            for row in rows
            if int(row["breakout_triggered"] or 0) == 1 and row["trading_days_to_breakout"] is not None
        ]
        return {
            "model_version": SHORT_MODEL_VERSION,
            "overall": overall,
            "candidate_status": status_counts,
            "guidance_ready": overall_breakout_count >= BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS,
            "guidance_reason": (
                None if overall_breakout_count >= BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS else
                f"insufficient_total_breakdowns({overall_breakout_count}<{BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS})"
            ),
            "segments": {
                "candidate_state": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["candidate_state"] or "UNKNOWN").upper(),
                    ordering=["EARLY_TOP", "PRE_BREAKDOWN", "IMMINENT", "TRIGGERING", "UNKNOWN"],
                ),
                "score_bucket": self._summarize_segments(
                    rows,
                    key_fn=lambda row: self._score_bucket(row["pre_breakout_score"]),
                    ordering=["LOW", "MEDIUM", "HIGH", "ELITE"],
                ),
                "compression_bucket": self._summarize_segments(
                    rows,
                    key_fn=lambda row: self._compression_bucket(row["atr_contraction_pct"]),
                    ordering=["LOOSE", "BUILDING", "COMPRESSED", "TIGHT"],
                ),
                "decision_cohort": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["decision_cohort"] or "UNSEEDED").upper(),
                    ordering=["APPROVED", "NEAR_MISS", "REJECT", "UNSEEDED"],
                ),
                "decision_reason": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["decision_reason"] or "UNSEEDED").lower(),
                    ordering=[],
                ),
                "regime_status": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_status"] or "UNKNOWN").upper(),
                    ordering=["BULL", "SIDEWAYS", "BEAR", "CORRECTION", "UNKNOWN"],
                ),
                "regime_volatility": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_volatility"] or "UNKNOWN").upper(),
                    ordering=["CALM", "NORMAL", "ELEVATED", "HIGH", "EXTREME", "UNKNOWN"],
                ),
                "regime_vix": self._summarize_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_vix"] or "UNKNOWN").upper(),
                    ordering=["CALM", "NORMAL", "ELEVATED", "HIGH", "EXTREME", "UNKNOWN"],
                ),
            },
            "timing_distribution": self._timing_distribution(
                timing_days,
                overall_n=int(overall["n"] or 0),
            ),
        }

    def get_short_calibration_report(
        self,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        min_segment_n: int = 5,
    ) -> Dict[str, Any]:
        rows = self._fetch_short_labeled_rows(horizon_days=int(horizon_days))
        overall = self.get_short_summary(horizon_days=int(horizon_days))
        overall_breakout_count = int(overall.get("breakout_count") or 0)
        overall_breakout_rate = float(overall["breakout_rate"] or 0.0)
        min_segment_n = max(1, int(min_segment_n))
        guidance_ready = overall_breakout_count >= BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS
        return {
            "model_version": SHORT_MODEL_VERSION,
            "horizon_days": int(horizon_days),
            "min_segment_n": min_segment_n,
            "guidance_ready": guidance_ready,
            "guidance_requirements": {
                "min_total_breakouts": BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS,
                "min_segment_breakouts": BTE_GUIDANCE_MIN_SEGMENT_BREAKOUTS,
                "min_segment_n": min_segment_n,
            },
            "guidance_reason": (
                None if guidance_ready else
                f"insufficient_total_breakdowns({overall_breakout_count}<{BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS})"
            ),
            "overall": overall,
            "dimensions": {
                "candidate_state": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["candidate_state"] or "UNKNOWN").upper(),
                    ordering=["EARLY_TOP", "PRE_BREAKDOWN", "IMMINENT", "TRIGGERING", "UNKNOWN"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "score_bucket": self._calibration_segments(
                    rows,
                    key_fn=lambda row: self._score_bucket(row["pre_breakout_score"]),
                    ordering=["LOW", "MEDIUM", "HIGH", "ELITE"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "compression_bucket": self._calibration_segments(
                    rows,
                    key_fn=lambda row: self._compression_bucket(row["atr_contraction_pct"]),
                    ordering=["LOOSE", "BUILDING", "COMPRESSED", "TIGHT"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "decision_cohort": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["decision_cohort"] or "UNSEEDED").upper(),
                    ordering=["APPROVED", "NEAR_MISS", "REJECT", "UNSEEDED"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "decision_reason": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["decision_reason"] or "UNSEEDED").lower(),
                    ordering=[],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "regime_status": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_status"] or "UNKNOWN").upper(),
                    ordering=["BULL", "SIDEWAYS", "BEAR", "CORRECTION", "UNKNOWN"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "regime_volatility": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_volatility"] or "UNKNOWN").upper(),
                    ordering=["CALM", "NORMAL", "ELEVATED", "HIGH", "EXTREME", "UNKNOWN"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
                "regime_vix": self._calibration_segments(
                    rows,
                    key_fn=lambda row: str(row["regime_vix"] or "UNKNOWN").upper(),
                    ordering=["CALM", "NORMAL", "ELEVATED", "HIGH", "EXTREME", "UNKNOWN"],
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                ),
            },
            "composite_segments": self._composite_short_calibration_segments(
                rows,
                overall_breakout_rate=overall_breakout_rate,
                overall_breakout_count=overall_breakout_count,
                min_segment_n=min_segment_n,
            ),
        }

    def get_short_score_comparison_report(
        self,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        min_segment_n: int = 5,
    ) -> Dict[str, Any]:
        rows = [
            row
            for row in self._fetch_short_labeled_rows(horizon_days=int(horizon_days))
            if row["decision_score"] is not None
        ]
        overall = self._overall_from_rows(rows, horizon_days=int(horizon_days))
        overall["model_version"] = SHORT_MODEL_VERSION
        overall_breakout_count = int(overall.get("breakout_count") or 0)
        overall_breakout_rate = float(overall["breakout_rate"] or 0.0)
        min_segment_n = max(1, int(min_segment_n))
        guidance_ready = overall_breakout_count >= BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS

        bte_rows = self._calibration_segments(
            rows,
            key_fn=lambda row: self._score_bucket(row["pre_breakout_score"]),
            ordering=["LOW", "MEDIUM", "HIGH", "ELITE"],
            overall_breakout_rate=overall_breakout_rate,
            overall_breakout_count=overall_breakout_count,
            min_segment_n=min_segment_n,
        )
        raw_rows = self._calibration_segments(
            rows,
            key_fn=lambda row: self._score_bucket(row["decision_score"]),
            ordering=["LOW", "MEDIUM", "HIGH", "ELITE"],
            overall_breakout_rate=overall_breakout_rate,
            overall_breakout_count=overall_breakout_count,
            min_segment_n=min_segment_n,
        )

        best_bte = self._best_guidance_row(bte_rows)
        best_raw = self._best_guidance_row(raw_rows)
        winner_dimension = None
        winner_reason = None
        if not guidance_ready:
            winner_reason = f"insufficient_total_breakdowns({overall_breakout_count}<{BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS})"
        elif best_bte and not best_raw:
            winner_dimension = "BTE_PRE_BREAKDOWN_SCORE"
        elif best_raw and not best_bte:
            winner_dimension = "RAW_SHORT_SCORE"
        elif best_bte and best_raw:
            bte_lift = float(best_bte.get("lift_vs_overall") or 0.0)
            raw_lift = float(best_raw.get("lift_vs_overall") or 0.0)
            if bte_lift > raw_lift:
                winner_dimension = "BTE_PRE_BREAKDOWN_SCORE"
            elif raw_lift > bte_lift:
                winner_dimension = "RAW_SHORT_SCORE"
            else:
                winner_dimension = "TIE"
        else:
            winner_reason = "no_guidance_eligible_score_bucket"

        return {
            "model_version": SHORT_MODEL_VERSION,
            "horizon_days": int(horizon_days),
            "min_segment_n": min_segment_n,
            "guidance_ready": guidance_ready,
            "guidance_reason": (
                None if guidance_ready else
                f"insufficient_total_breakdowns({overall_breakout_count}<{BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS})"
            ),
            "overall": overall,
            "dimensions": {
                "bte_pre_breakdown_score": bte_rows,
                "raw_short_score": raw_rows,
            },
            "best_bte_bucket": best_bte,
            "best_raw_bucket": best_raw,
            "winner_dimension": winner_dimension,
            "winner_reason": winner_reason,
        }

    def get_live_advisory(
        self,
        *,
        ticker: str,
        strategy_source: str = "SNIPER",
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        min_segment_n: int = 5,
        regime_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snapshot = self.extractor.extract_snapshot(
            ticker=ticker,
            strategy_source=str(strategy_source or "SNIPER").upper(),
        )
        if snapshot is None:
            return {}
        return self.get_advisory_for_snapshot(
            snapshot,
            horizon_days=int(horizon_days),
            min_segment_n=int(min_segment_n),
            regime_context=regime_snapshot,
        )

    def get_short_live_advisory(
        self,
        *,
        ticker: str,
        strategy_source: str = "SHORT",
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        min_segment_n: int = 5,
        regime_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snapshot = self.short_extractor.extract_snapshot(
            ticker=ticker,
            strategy_source=str(strategy_source or "SHORT").upper(),
        )
        if snapshot is None:
            return {}
        return self.get_short_advisory_for_snapshot(
            snapshot,
            horizon_days=int(horizon_days),
            min_segment_n=int(min_segment_n),
            regime_context=regime_snapshot,
        )

    def get_advisory_for_snapshot(
        self,
        snapshot: BTECandidateSnapshot,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        min_segment_n: int = 5,
        regime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        calibration = self._get_cached_calibration_report(
            horizon_days=int(horizon_days),
            min_segment_n=int(min_segment_n),
        )
        overall = calibration["overall"]
        regime_ctx = self._regime_decision_context(regime_context)
        regime_status = self._normalize_regime_label(regime_ctx.get("regime_status"))
        regime_volatility = self._normalize_regime_label(
            regime_ctx.get("regime_volatility") or regime_ctx.get("regime_vix")
        )
        regime_vix = self._normalize_regime_label(regime_ctx.get("regime_vix") or regime_ctx.get("regime_volatility"))
        if not bool(calibration.get("guidance_ready")):
            return {
                "bte_candidate_state": snapshot.candidate_state,
                "bte_pre_breakout_score": round(float(snapshot.pre_breakout_score), 1),
                "bte_score_bucket": self._score_bucket(snapshot.pre_breakout_score),
                "bte_compression_bucket": self._compression_bucket(snapshot.atr_contraction_pct),
                "bte_regime_status": regime_status,
                "bte_regime_volatility": regime_volatility,
                "bte_regime_vix": regime_vix,
                "bte_breakout_probability": None,
                "bte_timing_window_low_days": None,
                "bte_timing_window_high_days": None,
                "bte_median_days_to_breakout": overall.get("avg_days_to_breakout"),
                "bte_sample_size": int(overall.get("n") or 0),
                "bte_source_dimension": "overall",
                "bte_source_segment": "INSUFFICIENT_GUIDANCE_SAMPLE",
                "bte_sample_pass": False,
                "bte_horizon_days": int(horizon_days),
                "bte_model_version": MODEL_VERSION,
                "bte_advisory_label": str(calibration.get("guidance_reason") or "insufficient_breakout_sample"),
            }
        score_bucket = self._score_bucket(snapshot.pre_breakout_score)
        compression_bucket = self._compression_bucket(snapshot.atr_contraction_pct)
        composite_label = " | ".join(
            [
                snapshot.candidate_state,
                score_bucket,
                compression_bucket,
            ]
        )

        composite_row = next(
            (row for row in calibration["composite_segments"] if row["label"] == composite_label),
            None,
        )
        state_row = self._find_calibration_row(
            calibration["dimensions"]["candidate_state"],
            snapshot.candidate_state,
        )
        score_row = self._find_calibration_row(
            calibration["dimensions"]["score_bucket"],
            score_bucket,
        )
        compression_row = self._find_calibration_row(
            calibration["dimensions"]["compression_bucket"],
            compression_bucket,
        )
        regime_status_row = self._find_calibration_row(
            calibration["dimensions"]["regime_status"],
            regime_status,
        )
        regime_volatility_row = self._find_calibration_row(
            calibration["dimensions"]["regime_volatility"],
            regime_volatility,
        )
        regime_vix_row = self._find_calibration_row(
            calibration["dimensions"]["regime_vix"],
            regime_vix,
        )
        chosen_row, chosen_dimension = self._choose_advisory_row(
            composite_row=composite_row,
            state_row=state_row,
            score_row=score_row,
            compression_row=compression_row,
            regime_status_row=regime_status_row,
            regime_volatility_row=regime_volatility_row,
            regime_vix_row=regime_vix_row,
            overall=overall,
            min_segment_n=int(min_segment_n),
        )

        breakout_probability = self._safe_probability(chosen_row.get("breakout_rate"))
        low_days = chosen_row.get("timing_window_low_days")
        high_days = chosen_row.get("timing_window_high_days")
        median_days = chosen_row.get("median_days_to_breakout") or overall.get("avg_days_to_breakout")
        advisory_label = (
            f"{chosen_dimension}:{chosen_row.get('label')} "
            f"p={breakout_probability:.2f} {low_days}-{high_days}d"
            if breakout_probability is not None and low_days is not None and high_days is not None
            else f"{chosen_dimension}:{chosen_row.get('label')}"
        )

        return {
            "bte_candidate_state": snapshot.candidate_state,
            "bte_pre_breakout_score": round(float(snapshot.pre_breakout_score), 1),
            "bte_score_bucket": score_bucket,
            "bte_compression_bucket": compression_bucket,
            "bte_regime_status": regime_status,
            "bte_regime_volatility": regime_volatility,
            "bte_regime_vix": regime_vix,
            "bte_breakout_probability": breakout_probability,
            "bte_timing_window_low_days": low_days,
            "bte_timing_window_high_days": high_days,
            "bte_median_days_to_breakout": median_days,
            "bte_sample_size": int(chosen_row.get("n") or 0),
            "bte_source_dimension": chosen_dimension,
            "bte_source_segment": chosen_row.get("label"),
            "bte_sample_pass": bool(chosen_row.get("sample_pass", True)),
            "bte_horizon_days": int(horizon_days),
            "bte_model_version": MODEL_VERSION,
            "bte_advisory_label": advisory_label,
        }

    def get_short_advisory_for_snapshot(
        self,
        snapshot: BTEShortCandidateSnapshot,
        *,
        horizon_days: int = DEFAULT_HORIZON_DAYS,
        min_segment_n: int = 5,
        regime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        calibration = self._get_cached_short_calibration_report(
            horizon_days=int(horizon_days),
            min_segment_n=int(min_segment_n),
        )
        overall = calibration["overall"]
        regime_ctx = self._regime_decision_context(regime_context)
        regime_status = self._normalize_regime_label(regime_ctx.get("regime_status"))
        regime_volatility = self._normalize_regime_label(
            regime_ctx.get("regime_volatility") or regime_ctx.get("regime_vix")
        )
        regime_vix = self._normalize_regime_label(regime_ctx.get("regime_vix") or regime_ctx.get("regime_volatility"))
        if not bool(calibration.get("guidance_ready")):
            return {
                "bte_short_candidate_state": snapshot.candidate_state,
                "bte_short_pre_breakdown_score": round(float(snapshot.pre_breakdown_score), 1),
                "bte_short_score_bucket": self._score_bucket(snapshot.pre_breakdown_score),
                "bte_short_compression_bucket": self._compression_bucket(snapshot.atr_contraction_pct),
                "bte_short_regime_status": regime_status,
                "bte_short_regime_volatility": regime_volatility,
                "bte_short_regime_vix": regime_vix,
                "bte_short_breakdown_probability": None,
                "bte_short_timing_window_low_days": None,
                "bte_short_timing_window_high_days": None,
                "bte_short_median_days_to_breakdown": overall.get("avg_days_to_breakout"),
                "bte_short_sample_size": int(overall.get("n") or 0),
                "bte_short_source_dimension": "overall",
                "bte_short_source_segment": "INSUFFICIENT_GUIDANCE_SAMPLE",
                "bte_short_sample_pass": False,
                "bte_short_horizon_days": int(horizon_days),
                "bte_short_model_version": SHORT_MODEL_VERSION,
                "bte_short_advisory_label": str(calibration.get("guidance_reason") or "insufficient_breakdown_sample"),
            }
        score_bucket = self._score_bucket(snapshot.pre_breakdown_score)
        compression_bucket = self._compression_bucket(snapshot.atr_contraction_pct)
        composite_label = " | ".join(
            [
                snapshot.candidate_state,
                score_bucket,
                compression_bucket,
            ]
        )
        composite_row = next(
            (row for row in calibration["composite_segments"] if row["label"] == composite_label),
            None,
        )
        state_row = self._find_calibration_row(
            calibration["dimensions"]["candidate_state"],
            snapshot.candidate_state,
        )
        score_row = self._find_calibration_row(
            calibration["dimensions"]["score_bucket"],
            score_bucket,
        )
        compression_row = self._find_calibration_row(
            calibration["dimensions"]["compression_bucket"],
            compression_bucket,
        )
        regime_status_row = self._find_calibration_row(
            calibration["dimensions"]["regime_status"],
            regime_status,
        )
        regime_volatility_row = self._find_calibration_row(
            calibration["dimensions"]["regime_volatility"],
            regime_volatility,
        )
        regime_vix_row = self._find_calibration_row(
            calibration["dimensions"]["regime_vix"],
            regime_vix,
        )
        chosen_row, chosen_dimension = self._choose_advisory_row(
            composite_row=composite_row,
            state_row=state_row,
            score_row=score_row,
            compression_row=compression_row,
            regime_status_row=regime_status_row,
            regime_volatility_row=regime_volatility_row,
            regime_vix_row=regime_vix_row,
            overall=overall,
            min_segment_n=int(min_segment_n),
        )
        breakdown_probability = self._safe_probability(chosen_row.get("breakout_rate"))
        low_days = chosen_row.get("timing_window_low_days")
        high_days = chosen_row.get("timing_window_high_days")
        median_days = chosen_row.get("median_days_to_breakout") or overall.get("avg_days_to_breakout")
        advisory_label = (
            f"{chosen_dimension}:{chosen_row.get('label')} "
            f"p={breakdown_probability:.2f} {low_days}-{high_days}d"
            if breakdown_probability is not None and low_days is not None and high_days is not None
            else f"{chosen_dimension}:{chosen_row.get('label')}"
        )
        return {
            "bte_short_candidate_state": snapshot.candidate_state,
            "bte_short_pre_breakdown_score": round(float(snapshot.pre_breakdown_score), 1),
            "bte_short_score_bucket": score_bucket,
            "bte_short_compression_bucket": compression_bucket,
            "bte_short_regime_status": regime_status,
            "bte_short_regime_volatility": regime_volatility,
            "bte_short_regime_vix": regime_vix,
            "bte_short_breakdown_probability": breakdown_probability,
            "bte_short_timing_window_low_days": low_days,
            "bte_short_timing_window_high_days": high_days,
            "bte_short_median_days_to_breakdown": median_days,
            "bte_short_sample_size": int(chosen_row.get("n") or 0),
            "bte_short_source_dimension": chosen_dimension,
            "bte_short_source_segment": chosen_row.get("label"),
            "bte_short_sample_pass": bool(chosen_row.get("sample_pass", True)),
            "bte_short_horizon_days": int(horizon_days),
            "bte_short_model_version": SHORT_MODEL_VERSION,
            "bte_short_advisory_label": advisory_label,
        }

    def _get_cached_calibration_report(
        self,
        *,
        horizon_days: int,
        min_segment_n: int,
    ) -> Dict[str, Any]:
        key = (int(horizon_days), int(min_segment_n))
        cached = self._calibration_cache.get(key)
        if cached is not None:
            return cached
        report = self.get_calibration_report(
            horizon_days=int(horizon_days),
            min_segment_n=int(min_segment_n),
        )
        self._calibration_cache[key] = report
        return report

    def _get_cached_short_calibration_report(
        self,
        *,
        horizon_days: int,
        min_segment_n: int,
    ) -> Dict[str, Any]:
        key = (int(horizon_days), int(min_segment_n))
        cached = self._short_calibration_cache.get(key)
        if cached is not None:
            return cached
        report = self.get_short_calibration_report(
            horizon_days=int(horizon_days),
            min_segment_n=int(min_segment_n),
        )
        self._short_calibration_cache[key] = report
        return report

    @staticmethod
    def _find_calibration_row(rows: Sequence[Dict[str, Any]], label: str) -> Optional[Dict[str, Any]]:
        for row in rows:
            if str(row.get("label") or "") == str(label or ""):
                return row
        return None

    def _choose_advisory_row(
        self,
        *,
        composite_row: Optional[Dict[str, Any]],
        state_row: Optional[Dict[str, Any]],
        score_row: Optional[Dict[str, Any]],
        compression_row: Optional[Dict[str, Any]],
        regime_status_row: Optional[Dict[str, Any]],
        regime_volatility_row: Optional[Dict[str, Any]],
        regime_vix_row: Optional[Dict[str, Any]],
        overall: Dict[str, Any],
        min_segment_n: int,
    ) -> tuple[Dict[str, Any], str]:
        if composite_row and bool(composite_row.get("guidance_eligible")):
            return composite_row, "composite"
        for dimension, row in (
            ("candidate_state", state_row),
            ("regime_vix", regime_vix_row),
            ("regime_volatility", regime_volatility_row),
            ("regime_status", regime_status_row),
            ("score_bucket", score_row),
            ("compression_bucket", compression_row),
        ):
            if row and bool(row.get("guidance_eligible")):
                return row, dimension
        return {
            "label": "OVERALL",
            "n": int(overall.get("n") or 0),
            "breakout_rate": overall.get("breakout_rate"),
            "median_days_to_breakout": overall.get("avg_days_to_breakout"),
            "timing_window_low_days": None,
            "timing_window_high_days": None,
            "sample_pass": int(overall.get("n") or 0) >= int(min_segment_n),
            "guidance_eligible": False,
        }, "overall"

    @staticmethod
    def _safe_probability(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None

    def _fetch_labeled_rows(self, *, horizon_days: int) -> List[sqlite3.Row]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                c.id,
                c.ticker,
                c.candidate_date,
                c.strategy_source,
                c.pre_breakout_score,
                c.candidate_state,
                c.atr_contraction_pct,
                c.seed_source,
                c.council_decision,
                c.decision_cohort,
                c.decision_reason,
                c.decision_score,
                c.scanner_rr,
                c.regime_status,
                c.regime_volatility,
                c.regime_vix,
                c.vix_level,
                o.breakout_triggered,
                o.trading_days_to_breakout,
                o.end_return_pct,
                o.max_close_pct_above_pivot
            FROM bte_candidates c
            JOIN bte_outcomes o
              ON o.candidate_id = c.id
            WHERE o.horizon_days = ?
              AND o.data_available = 1
            ORDER BY c.candidate_date ASC, c.id ASC
            """,
            (int(horizon_days),),
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def _fetch_short_labeled_rows(self, *, horizon_days: int) -> List[sqlite3.Row]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                c.id,
                c.ticker,
                c.candidate_date,
                c.strategy_source,
                c.pre_breakdown_score AS pre_breakout_score,
                c.candidate_state,
                c.atr_contraction_pct,
                c.seed_source,
                c.council_decision,
                c.decision_cohort,
                c.decision_reason,
                c.decision_score,
                c.scanner_rr,
                c.regime_status,
                c.regime_volatility,
                c.regime_vix,
                c.vix_level,
                o.breakdown_triggered AS breakout_triggered,
                o.trading_days_to_breakdown AS trading_days_to_breakout,
                o.end_return_pct,
                o.max_close_pct_below_support AS max_close_pct_above_pivot
            FROM bte_short_candidates c
            JOIN bte_short_outcomes o
              ON o.candidate_id = c.id
            WHERE o.horizon_days = ?
              AND o.data_available = 1
            ORDER BY c.candidate_date ASC, c.id ASC
            """,
            (int(horizon_days),),
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def _overall_from_rows(self, rows: Sequence[sqlite3.Row], *, horizon_days: int) -> Dict[str, Any]:
        n = len(rows)
        breakout_count = sum(1 for row in rows if int(row["breakout_triggered"] or 0) == 1)
        trigger_days = [
            int(row["trading_days_to_breakout"])
            for row in rows
            if int(row["breakout_triggered"] or 0) == 1 and row["trading_days_to_breakout"] is not None
        ]
        end_returns = [
            float(row["end_return_pct"])
            for row in rows
            if row["end_return_pct"] is not None
        ]
        max_close_values = [
            float(row["max_close_pct_above_pivot"])
            for row in rows
            if row["max_close_pct_above_pivot"] is not None
        ]
        return {
            "horizon_days": int(horizon_days),
            "n": n,
            "breakout_count": breakout_count,
            "breakout_rate": round((breakout_count / n), 4) if n > 0 else None,
            "avg_days_to_breakout": round(sum(trigger_days) / len(trigger_days), 2) if trigger_days else None,
            "avg_end_return_pct": round(sum(end_returns) / len(end_returns), 3) if end_returns else None,
            "avg_max_close_pct_above_pivot": round(sum(max_close_values) / len(max_close_values), 3)
            if max_close_values else None,
            "model_version": MODEL_VERSION,
        }

    def _fetch_candidate_status_counts(self) -> Dict[str, int]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT outcome_status, COUNT(*) AS n
            FROM bte_candidates
            GROUP BY outcome_status
            """
        )
        rows = cur.fetchall()
        conn.close()
        counts = {
            "total_candidates": 0,
            "pending_count": 0,
            "computed_count": 0,
            "no_data_count": 0,
        }
        for row in rows:
            status = str(row["outcome_status"] or "").upper()
            n = int(row["n"] or 0)
            counts["total_candidates"] += n
            if status == _OUTCOME_STATUS_PENDING:
                counts["pending_count"] += n
            elif status == _OUTCOME_STATUS_COMPUTED:
                counts["computed_count"] += n
            elif status == _OUTCOME_STATUS_NO_DATA:
                counts["no_data_count"] += n
        return counts

    def _fetch_short_candidate_status_counts(self) -> Dict[str, int]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT outcome_status, COUNT(*) AS n
            FROM bte_short_candidates
            GROUP BY outcome_status
            """
        )
        rows = cur.fetchall()
        conn.close()
        counts = {
            "total_candidates": 0,
            "pending_count": 0,
            "computed_count": 0,
            "no_data_count": 0,
        }
        for row in rows:
            status = str(row["outcome_status"] or "").upper()
            n = int(row["n"] or 0)
            counts["total_candidates"] += n
            if status == _OUTCOME_STATUS_PENDING:
                counts["pending_count"] += n
            elif status == _OUTCOME_STATUS_COMPUTED:
                counts["computed_count"] += n
            elif status == _OUTCOME_STATUS_NO_DATA:
                counts["no_data_count"] += n
        return counts

    @staticmethod
    def _score_bucket(score: Any) -> str:
        value = float(score or 0.0)
        if value >= 80.0:
            return "ELITE"
        if value >= 70.0:
            return "HIGH"
        if value >= 60.0:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _compression_bucket(atr_contraction_pct: Any) -> str:
        value = float(atr_contraction_pct or 0.0)
        if value >= 0.30:
            return "TIGHT"
        if value >= 0.20:
            return "COMPRESSED"
        if value >= 0.10:
            return "BUILDING"
        return "LOOSE"

    def _summarize_segments(
        self,
        rows: Sequence[sqlite3.Row],
        *,
        key_fn,
        ordering: Sequence[str],
    ) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            label = str(key_fn(row) or "UNKNOWN")
            stats = grouped.setdefault(
                label,
                {
                    "label": label,
                    "n": 0,
                    "breakout_count": 0,
                    "trigger_days": [],
                    "end_return_sum": 0.0,
                    "end_return_n": 0,
                    "max_close_sum": 0.0,
                    "max_close_n": 0,
                    "score_sum": 0.0,
                    "score_n": 0,
                },
            )
            stats["n"] += 1
            stats["score_sum"] += float(row["pre_breakout_score"] or 0.0)
            stats["score_n"] += 1
            if row["end_return_pct"] is not None:
                stats["end_return_sum"] += float(row["end_return_pct"])
                stats["end_return_n"] += 1
            if row["max_close_pct_above_pivot"] is not None:
                stats["max_close_sum"] += float(row["max_close_pct_above_pivot"])
                stats["max_close_n"] += 1
            if int(row["breakout_triggered"] or 0) == 1:
                stats["breakout_count"] += 1
                if row["trading_days_to_breakout"] is not None:
                    stats["trigger_days"].append(int(row["trading_days_to_breakout"]))

        if ordering:
            ordered_labels = list(ordering) + sorted(
                label for label in grouped.keys() if label not in set(ordering)
            )
        else:
            ordered_labels = [
                label
                for label, _stats in sorted(
                    grouped.items(),
                    key=lambda item: (-int(item[1]["n"]), item[0]),
                )
            ]
        summary_rows = []
        for label in ordered_labels:
            stats = grouped.get(label)
            if not stats:
                continue
            n = int(stats["n"])
            breakout_count = int(stats["breakout_count"])
            trigger_days = list(stats["trigger_days"])
            summary_rows.append(
                {
                    "label": label,
                    "n": n,
                    "breakout_count": breakout_count,
                    "breakout_rate": round(breakout_count / n, 4) if n > 0 else None,
                    "avg_days_to_breakout": round(sum(trigger_days) / len(trigger_days), 2)
                    if trigger_days else None,
                    "avg_end_return_pct": round(stats["end_return_sum"] / stats["end_return_n"], 3)
                    if stats["end_return_n"] > 0 else None,
                    "avg_max_close_pct_above_pivot": round(stats["max_close_sum"] / stats["max_close_n"], 3)
                    if stats["max_close_n"] > 0 else None,
                    "avg_pre_breakout_score": round(stats["score_sum"] / stats["score_n"], 1)
                    if stats["score_n"] > 0 else None,
                }
            )
        return summary_rows

    @staticmethod
    def _timing_distribution(timing_days: Sequence[int], *, overall_n: int) -> Dict[str, Any]:
        buckets = defaultdict(int)
        for day in timing_days:
            if day <= 1:
                buckets["D1"] += 1
            elif day == 2:
                buckets["D2"] += 1
            elif 3 <= day <= 5:
                buckets["D3_5"] += 1
            else:
                buckets["D6_PLUS"] += 1

        ordered_labels = ["D1", "D2", "D3_5", "D6_PLUS"]
        triggered_n = len(timing_days)
        bucket_rows = []
        for label in ordered_labels:
            n = int(buckets.get(label, 0))
            bucket_rows.append(
                {
                    "label": label,
                    "n": n,
                    "rate_of_triggered": round(n / triggered_n, 4) if triggered_n > 0 else None,
                    "rate_of_total": round(n / overall_n, 4) if overall_n > 0 else None,
                }
            )
        return {
            "triggered_n": triggered_n,
            "avg_days_to_breakout": round(sum(timing_days) / triggered_n, 2) if triggered_n > 0 else None,
            "buckets": bucket_rows,
        }

    def _calibration_segments(
        self,
        rows: Sequence[sqlite3.Row],
        *,
        key_fn,
        ordering: Sequence[str],
        overall_breakout_rate: float,
        overall_breakout_count: int,
        min_segment_n: int,
    ) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            grouped[str(key_fn(row) or "UNKNOWN")].append(row)

        if ordering:
            ordered_labels = list(ordering) + sorted(
                label for label in grouped.keys() if label not in set(ordering)
            )
        else:
            ordered_labels = [
                label
                for label, group_rows in sorted(
                    grouped.items(),
                    key=lambda item: (-len(item[1]), item[0]),
                )
            ]
        output = []
        for label in ordered_labels:
            group_rows = grouped.get(label)
            if not group_rows:
                continue
            output.append(
                self._build_calibration_row(
                    label=label,
                    rows=group_rows,
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                )
            )
        return output

    def _composite_calibration_segments(
        self,
        rows: Sequence[sqlite3.Row],
        *,
        overall_breakout_rate: float,
        overall_breakout_count: int,
        min_segment_n: int,
    ) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            label = " | ".join(
                [
                    str(row["candidate_state"] or "UNKNOWN").upper(),
                    self._score_bucket(row["pre_breakout_score"]),
                    self._compression_bucket(row["atr_contraction_pct"]),
                ]
            )
            grouped[label].append(row)

        output = []
        for label, group_rows in grouped.items():
            if len(group_rows) < min_segment_n:
                continue
            output.append(
                self._build_calibration_row(
                    label=label,
                    rows=group_rows,
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                )
            )
        output.sort(
            key=lambda row: (
                -(row["breakout_rate"] if row["breakout_rate"] is not None else -1.0),
                -row["n"],
                row["label"],
            )
        )
        return output

    def _composite_short_calibration_segments(
        self,
        rows: Sequence[sqlite3.Row],
        *,
        overall_breakout_rate: float,
        overall_breakout_count: int,
        min_segment_n: int,
    ) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            label = " | ".join(
                [
                    str(row["candidate_state"] or "UNKNOWN").upper(),
                    self._score_bucket(row["pre_breakout_score"]),
                    self._compression_bucket(row["atr_contraction_pct"]),
                ]
            )
            grouped[label].append(row)

        output = []
        for label, group_rows in grouped.items():
            if len(group_rows) < min_segment_n:
                continue
            output.append(
                self._build_calibration_row(
                    label=label,
                    rows=group_rows,
                    overall_breakout_rate=overall_breakout_rate,
                    overall_breakout_count=overall_breakout_count,
                    min_segment_n=min_segment_n,
                )
            )
        output.sort(
            key=lambda row: (
                -(row["breakout_rate"] if row["breakout_rate"] is not None else -1.0),
                -row["n"],
                row["label"],
            )
        )
        return output

    def _build_calibration_row(
        self,
        *,
        label: str,
        rows: Sequence[sqlite3.Row],
        overall_breakout_rate: float,
        overall_breakout_count: int,
        min_segment_n: int,
    ) -> Dict[str, Any]:
        n = len(rows)
        breakout_count = sum(1 for row in rows if int(row["breakout_triggered"] or 0) == 1)
        breakout_rate = (breakout_count / n) if n > 0 else None
        triggered_days = [
            int(row["trading_days_to_breakout"])
            for row in rows
            if int(row["breakout_triggered"] or 0) == 1 and row["trading_days_to_breakout"] is not None
        ]
        end_returns = [
            float(row["end_return_pct"])
            for row in rows
            if row["end_return_pct"] is not None
        ]
        max_close_values = [
            float(row["max_close_pct_above_pivot"])
            for row in rows
            if row["max_close_pct_above_pivot"] is not None
        ]
        return {
            "label": label,
            "n": n,
            "breakout_count": breakout_count,
            "breakout_rate": round(breakout_rate, 4) if breakout_rate is not None else None,
            "lift_vs_overall": round(breakout_rate / overall_breakout_rate, 4)
            if breakout_rate is not None and overall_breakout_rate > 0 else None,
            "avg_end_return_pct": round(sum(end_returns) / len(end_returns), 3)
            if end_returns else None,
            "avg_max_close_pct_above_pivot": round(sum(max_close_values) / len(max_close_values), 3)
            if max_close_values else None,
            "median_days_to_breakout": self._median(triggered_days),
            "timing_window_low_days": self._percentile(triggered_days, 0.25),
            "timing_window_high_days": self._percentile(triggered_days, 0.75),
            "sample_pass": n >= min_segment_n,
            "guidance_eligible": (
                overall_breakout_count >= BTE_GUIDANCE_MIN_TOTAL_BREAKOUTS
                and n >= min_segment_n
                and breakout_count >= BTE_GUIDANCE_MIN_SEGMENT_BREAKOUTS
            ),
        }

    @staticmethod
    def _median(values: Sequence[int]) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(float(value) for value in values)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return round(ordered[mid], 2)
        return round((ordered[mid - 1] + ordered[mid]) / 2.0, 2)

    @staticmethod
    def _percentile(values: Sequence[int], quantile: float) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(float(value) for value in values)
        if len(ordered) == 1:
            return round(ordered[0], 2)
        q = max(0.0, min(1.0, float(quantile)))
        pos = (len(ordered) - 1) * q
        lower = int(pos)
        upper = min(lower + 1, len(ordered) - 1)
        if lower == upper:
            return round(ordered[lower], 2)
        weight = pos - lower
        return round((ordered[lower] * (1.0 - weight)) + (ordered[upper] * weight), 2)

    @staticmethod
    def _best_guidance_row(rows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        eligible = [row for row in rows if bool(row.get("guidance_eligible"))]
        if not eligible:
            return None
        eligible.sort(
            key=lambda row: (
                -(float(row.get("lift_vs_overall") or 0.0)),
                -(float(row.get("breakout_rate") or 0.0)),
                -int(row.get("n") or 0),
                str(row.get("label") or ""),
            )
        )
        return eligible[0]

    def _fetch_recent_sniper_decisions(
        self,
        *,
        days: int,
        include_reasons: Sequence[str],
        min_decision_score: float,
    ) -> List[sqlite3.Row]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        ).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(decisions)")
        cols = {str(row["name"]) for row in cur.fetchall()}
        clauses = [
            "timestamp >= ?",
            "UPPER(COALESCE(strategy, '')) = 'SNIPER'",
        ]
        params: List[Any] = [cutoff]
        if include_reasons:
            placeholders = ", ".join(["?"] * len(include_reasons))
            clauses.append(f"LOWER(COALESCE(council_reason, '')) IN ({placeholders})")
            params.extend(include_reasons)
        if min_decision_score > 0:
            clauses.append("COALESCE(avg_score, 0) >= ?")
            params.append(float(min_decision_score))

        select_cols = [
            "id", "run_id", "timestamp", "ticker", "council_decision",
            "council_reason", "avg_score", "rr",
        ]
        for opt in ("regime_status", "regime_volatility", "regime_vix", "regime_overall", "vix_level"):
            if opt in cols:
                select_cols.append(opt)

        cur.execute(
            f"""
            SELECT
                {", ".join(select_cols)}
            FROM decisions
            WHERE {" AND ".join(clauses)}
            ORDER BY timestamp DESC, id DESC
            """,
            params,
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    def _fetch_recent_short_decisions(
        self,
        *,
        days: int,
        include_reasons: Sequence[str],
        min_decision_score: float,
    ) -> List[sqlite3.Row]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
        ).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(decisions)")
        cols = {str(row["name"]) for row in cur.fetchall()}
        clauses = [
            "timestamp >= ?",
            "UPPER(COALESCE(strategy, '')) = 'SHORT'",
        ]
        params: List[Any] = [cutoff]
        if include_reasons:
            placeholders = ", ".join(["?"] * len(include_reasons))
            clauses.append(f"LOWER(COALESCE(council_reason, '')) IN ({placeholders})")
            params.extend(include_reasons)
        if min_decision_score > 0:
            clauses.append("COALESCE(avg_score, 0) >= ?")
            params.append(float(min_decision_score))

        select_cols = [
            "id", "run_id", "timestamp", "ticker", "council_decision",
            "council_reason", "avg_score", "rr",
        ]
        for opt in ("regime_status", "regime_volatility", "regime_vix", "regime_overall", "vix_level"):
            if opt in cols:
                select_cols.append(opt)

        cur.execute(
            f"""
            SELECT
                {", ".join(select_cols)}
            FROM decisions
            WHERE {" AND ".join(clauses)}
            ORDER BY timestamp DESC, id DESC
            """,
            params,
        )
        rows = cur.fetchall()
        conn.close()
        return rows

    @staticmethod
    def _dedupe_best_decision_per_ticker_day(rows: Sequence[sqlite3.Row]) -> List[sqlite3.Row]:
        chosen: Dict[tuple[str, str], sqlite3.Row] = {}
        for row in rows:
            ticker = str(row["ticker"] or "").upper().strip()
            ts = str(row["timestamp"] or "")
            key = (ticker, ts[:10])
            existing = chosen.get(key)
            if existing is None:
                chosen[key] = row
                continue
            current_score = float(row["avg_score"] or 0.0)
            prior_score = float(existing["avg_score"] or 0.0)
            if current_score > prior_score:
                chosen[key] = row
                continue
            if current_score == prior_score and str(row["timestamp"] or "") > str(existing["timestamp"] or ""):
                chosen[key] = row
        return list(chosen.values())

    def _historical_bars_up_to(
        self,
        *,
        ticker: str,
        end_date: date,
        lookback_bars: int,
    ) -> List[Dict[str, Any]]:
        age_days = max((datetime.now(timezone.utc).date() - end_date).days, 0)
        calendar_lookback = age_days + max(int(lookback_bars) * 3, 270)
        raw_bars = self.data_feed.get_daily_bars(
            ticker,
            days_back=calendar_lookback,
            adjustment="all",
        ) or []
        filtered = []
        for bar in raw_bars:
            bar_date = self._bar_date(bar)
            if bar_date and bar_date <= end_date:
                filtered.append(bar)
        if len(filtered) <= int(lookback_bars):
            return filtered
        return filtered[-int(lookback_bars):]

    @staticmethod
    def _decision_date(timestamp_text: str) -> date:
        text = str(timestamp_text or "")
        try:
            return datetime.fromisoformat(text.replace("Z", "")).date()
        except Exception:
            return datetime.now(timezone.utc).date()

    def _fetch_pending_candidates(self, *, retry_no_data: bool) -> List[sqlite3.Row]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if retry_no_data:
            cur.execute(
                """
                SELECT *
                FROM bte_candidates
                WHERE outcome_status IN (?, ?)
                ORDER BY candidate_date ASC, id ASC
                """,
                (_OUTCOME_STATUS_PENDING, _OUTCOME_STATUS_NO_DATA),
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM bte_candidates
                WHERE outcome_status = ?
                ORDER BY candidate_date ASC, id ASC
                """,
                (_OUTCOME_STATUS_PENDING,),
            )
        rows = cur.fetchall()
        conn.close()
        return rows

    def _fetch_pending_short_candidates(self, *, retry_no_data: bool) -> List[sqlite3.Row]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if retry_no_data:
            cur.execute(
                """
                SELECT *
                FROM bte_short_candidates
                WHERE outcome_status IN (?, ?)
                ORDER BY candidate_date ASC, id ASC
                """,
                (_OUTCOME_STATUS_PENDING, _OUTCOME_STATUS_NO_DATA),
            )
        else:
            cur.execute(
                """
                SELECT *
                FROM bte_short_candidates
                WHERE outcome_status = ?
                ORDER BY candidate_date ASC, id ASC
                """,
                (_OUTCOME_STATUS_PENDING,),
            )
        rows = cur.fetchall()
        conn.close()
        return rows

    def _fetch_forward_bars(
        self,
        *,
        ticker: str,
        candidate_date: str,
        horizon_days: int,
    ) -> tuple[List[Dict[str, Any]], str]:
        candidate_dt = datetime.fromisoformat(candidate_date).date()
        age_days = max((datetime.now(timezone.utc).date() - candidate_dt).days, 0)
        lookback = max(90, int(age_days) + int(horizon_days) + 20)
        bars = self.data_feed.get_daily_bars(ticker, days_back=lookback, adjustment="all") or []
        filtered: List[Dict[str, Any]] = []
        for bar in bars:
            bar_date = self._bar_date(bar)
            if bar_date and bar_date > candidate_dt:
                filtered.append(bar)
        return filtered, "alpaca_prices"

    def _compute_breakout_outcome(
        self,
        candidate: sqlite3.Row,
        bars: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        pivot = float(candidate["breakout_pivot"])
        entry_price = float(candidate["entry_price"])
        avg_volume_20 = float(candidate["avg_volume_20"] or 0.0)
        breakout_triggered = 0
        breakout_timestamp = None
        trading_days_to_breakout = None
        breakout_close = None
        breakout_volume_ratio = None
        breakout_close_position = None
        max_close_pct_above_pivot = -999.0

        for idx, bar in enumerate(bars, start=1):
            close = float(bar["close"])
            high = float(bar["high"])
            low = float(bar["low"])
            volume = float(bar["volume"])
            range_span = max(high - low, 1e-6)
            close_position = (close - low) / range_span
            vol_ratio = volume / max(avg_volume_20, 1.0)
            max_close_pct_above_pivot = max(max_close_pct_above_pivot, ((close - pivot) / pivot) * 100.0)
            if (
                close > pivot
                and vol_ratio >= MIN_BREAKOUT_VOLUME_RATIO
                and close_position >= MIN_BREAKOUT_CLOSE_POSITION
            ):
                breakout_triggered = 1
                breakout_timestamp = self._bar_timestamp_iso(bar)
                trading_days_to_breakout = idx
                breakout_close = round(close, 4)
                breakout_volume_ratio = round(vol_ratio, 4)
                breakout_close_position = round(close_position, 4)
                break

        end_close = float(bars[-1]["close"])
        end_return_pct = ((end_close - entry_price) / entry_price) * 100.0 if entry_price > 0 else 0.0
        return {
            "breakout_triggered": breakout_triggered,
            "breakout_timestamp": breakout_timestamp,
            "trading_days_to_breakout": trading_days_to_breakout,
            "breakout_close": breakout_close,
            "breakout_volume_ratio": breakout_volume_ratio,
            "breakout_close_position": breakout_close_position,
            "max_close_pct_above_pivot": round(max_close_pct_above_pivot, 4),
            "end_return_pct": round(end_return_pct, 4),
            "data_available": 1,
            "data_source": "alpaca_prices",
            "model_version": MODEL_VERSION,
        }

    def _compute_breakdown_outcome(
        self,
        candidate: sqlite3.Row,
        bars: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        support = float(candidate["breakdown_support"])
        entry_price = float(candidate["entry_price"])
        avg_volume_20 = float(candidate["avg_volume_20"] or 0.0)
        breakdown_triggered = 0
        breakdown_timestamp = None
        trading_days_to_breakdown = None
        breakdown_close = None
        breakdown_volume_ratio = None
        breakdown_close_position = None
        max_close_pct_below_support = -999.0

        for idx, bar in enumerate(bars, start=1):
            close = float(bar["close"])
            high = float(bar["high"])
            low = float(bar["low"])
            volume = float(bar["volume"])
            range_span = max(high - low, 1e-6)
            close_position = (close - low) / range_span
            vol_ratio = volume / max(avg_volume_20, 1.0)
            max_close_pct_below_support = max(
                max_close_pct_below_support,
                ((support - close) / support) * 100.0,
            )
            if (
                close < support
                and vol_ratio >= MIN_BREAKOUT_VOLUME_RATIO
                and close_position <= MAX_BREAKDOWN_CLOSE_POSITION
            ):
                breakdown_triggered = 1
                breakdown_timestamp = self._bar_timestamp_iso(bar)
                trading_days_to_breakdown = idx
                breakdown_close = round(close, 4)
                breakdown_volume_ratio = round(vol_ratio, 4)
                breakdown_close_position = round(close_position, 4)
                break

        end_close = float(bars[-1]["close"])
        end_return_pct = ((entry_price - end_close) / entry_price) * 100.0 if entry_price > 0 else 0.0
        return {
            "breakdown_triggered": breakdown_triggered,
            "breakdown_timestamp": breakdown_timestamp,
            "trading_days_to_breakdown": trading_days_to_breakdown,
            "breakdown_close": breakdown_close,
            "breakdown_volume_ratio": breakdown_volume_ratio,
            "breakdown_close_position": breakdown_close_position,
            "max_close_pct_below_support": round(max_close_pct_below_support, 4),
            "end_return_pct": round(end_return_pct, 4),
            "data_available": 1,
            "data_source": "alpaca_prices",
            "model_version": SHORT_MODEL_VERSION,
        }

    def _insert_outcome(self, candidate_id: int, horizon_days: int, outcome: Dict[str, Any]) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM bte_outcomes WHERE candidate_id = ? AND horizon_days = ?", (candidate_id, horizon_days))
        cur.execute(
            """
            INSERT INTO bte_outcomes (
                candidate_id, computed_at, horizon_days, breakout_triggered,
                breakout_timestamp, trading_days_to_breakout, breakout_close,
                breakout_volume_ratio, breakout_close_position,
                max_close_pct_above_pivot, end_return_pct, data_source,
                data_available, model_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(candidate_id),
                datetime.now(timezone.utc).isoformat(),
                int(horizon_days),
                int(outcome.get("breakout_triggered", 0)),
                outcome.get("breakout_timestamp"),
                outcome.get("trading_days_to_breakout"),
                outcome.get("breakout_close"),
                outcome.get("breakout_volume_ratio"),
                outcome.get("breakout_close_position"),
                outcome.get("max_close_pct_above_pivot"),
                outcome.get("end_return_pct"),
                outcome.get("data_source", "unknown"),
                int(outcome.get("data_available", 1)),
                outcome.get("model_version", MODEL_VERSION),
            ),
        )
        conn.commit()
        conn.close()
        self._calibration_cache.clear()

    def _insert_short_outcome(self, candidate_id: int, horizon_days: int, outcome: Dict[str, Any]) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM bte_short_outcomes WHERE candidate_id = ? AND horizon_days = ?", (candidate_id, horizon_days))
        cur.execute(
            """
            INSERT INTO bte_short_outcomes (
                candidate_id, computed_at, horizon_days, breakdown_triggered,
                breakdown_timestamp, trading_days_to_breakdown, breakdown_close,
                breakdown_volume_ratio, breakdown_close_position,
                max_close_pct_below_support, end_return_pct, data_source,
                data_available, model_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(candidate_id),
                datetime.now(timezone.utc).isoformat(),
                int(horizon_days),
                int(outcome.get("breakdown_triggered", 0)),
                outcome.get("breakdown_timestamp"),
                outcome.get("trading_days_to_breakdown"),
                outcome.get("breakdown_close"),
                outcome.get("breakdown_volume_ratio"),
                outcome.get("breakdown_close_position"),
                outcome.get("max_close_pct_below_support"),
                outcome.get("end_return_pct"),
                outcome.get("data_source", "unknown"),
                int(outcome.get("data_available", 1)),
                outcome.get("model_version", SHORT_MODEL_VERSION),
            ),
        )
        conn.commit()
        conn.close()
        self._short_calibration_cache.clear()

    def _mark_no_data(self, candidate_id: int) -> None:
        self._update_candidate_status(candidate_id, _OUTCOME_STATUS_NO_DATA)

    def _mark_short_no_data(self, candidate_id: int) -> None:
        self._update_short_candidate_status(candidate_id, _OUTCOME_STATUS_NO_DATA)

    def _update_candidate_status(self, candidate_id: int, status: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "UPDATE bte_candidates SET outcome_status = ? WHERE id = ?",
            (status, int(candidate_id)),
        )
        conn.commit()
        conn.close()

    def _update_short_candidate_status(self, candidate_id: int, status: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "UPDATE bte_short_candidates SET outcome_status = ? WHERE id = ?",
            (status, int(candidate_id)),
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _candidate_age_days(candidate_date: str) -> int:
        return max((datetime.now(timezone.utc).date() - datetime.fromisoformat(candidate_date).date()).days, 0)

    @staticmethod
    def _bar_date(bar: Dict[str, Any]):
        ts = bar.get("timestamp")
        if hasattr(ts, "date"):
            return ts.date()
        try:
            return datetime.fromisoformat(str(ts)).date()
        except Exception:
            return None

    @staticmethod
    def _bar_timestamp_iso(bar: Dict[str, Any]) -> str:
        ts = bar.get("timestamp")
        if hasattr(ts, "isoformat"):
            return ts.isoformat()
        return str(ts or "")

    @staticmethod
    def _normalize_regime_label(value: Any) -> str:
        text = str(value or "").strip().upper()
        return text or "UNKNOWN"

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except Exception:
            return None

    def _regime_decision_context(self, regime_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        ctx = dict(regime_context or {})
        regime_status = (
            ctx.get("regime_status")
            or ctx.get("status")
            or ctx.get("regime_overall")
            or ctx.get("regime")
        )
        regime_volatility = (
            ctx.get("regime_volatility")
            or ctx.get("volatility")
            or ctx.get("regime_vix")
            or ctx.get("vix_regime")
        )
        regime_vix = (
            ctx.get("regime_vix")
            or ctx.get("vix_regime")
            or regime_volatility
        )
        return {
            "regime_status": self._normalize_regime_label(regime_status),
            "regime_volatility": self._normalize_regime_label(regime_volatility),
            "regime_vix": self._normalize_regime_label(regime_vix),
            "vix_level": self._safe_float(ctx.get("vix_level")),
        }

    def _ensure_tables(self) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bte_candidates (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at         TEXT NOT NULL,
                seeded_at          TEXT NOT NULL,
                ticker             TEXT NOT NULL,
                strategy_source    TEXT NOT NULL,
                candidate_date     TEXT NOT NULL,
                snapshot_timestamp TEXT NOT NULL,
                entry_price        REAL,
                breakout_pivot     REAL,
                consolidation_low  REAL,
                recent_atr         REAL,
                avg_volume_20      REAL,
                atr_contraction_pct REAL,
                volume_ratio       REAL,
                rs_acceleration    REAL,
                close_position     REAL,
                base_tightness     REAL,
                higher_lows        INTEGER NOT NULL DEFAULT 0,
                pivot_distance_pct REAL,
                pre_breakout_score REAL,
                candidate_state    TEXT,
                model_version      TEXT,
                outcome_status     TEXT NOT NULL DEFAULT 'PENDING',
                seed_source        TEXT,
                decision_id        INTEGER,
                run_id             TEXT,
                council_decision   TEXT,
                decision_cohort    TEXT,
                decision_reason    TEXT,
                decision_score     REAL,
                scanner_rr         REAL,
                regime_status      TEXT,
                regime_volatility  TEXT,
                regime_vix         TEXT,
                vix_level          REAL,
                features_json      TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bte_candidates
            ON bte_candidates(ticker, strategy_source, candidate_date)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bte_outcomes (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id             INTEGER NOT NULL,
                computed_at              TEXT NOT NULL,
                horizon_days             INTEGER NOT NULL,
                breakout_triggered       INTEGER NOT NULL DEFAULT 0,
                breakout_timestamp       TEXT,
                trading_days_to_breakout INTEGER,
                breakout_close           REAL,
                breakout_volume_ratio    REAL,
                breakout_close_position  REAL,
                max_close_pct_above_pivot REAL,
                end_return_pct           REAL,
                data_source              TEXT,
                data_available           INTEGER NOT NULL DEFAULT 1,
                model_version            TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bte_outcomes
            ON bte_outcomes(candidate_id, horizon_days)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bte_short_candidates (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at           TEXT NOT NULL,
                seeded_at            TEXT NOT NULL,
                ticker               TEXT NOT NULL,
                strategy_source      TEXT NOT NULL,
                candidate_date       TEXT NOT NULL,
                snapshot_timestamp   TEXT NOT NULL,
                entry_price          REAL,
                breakdown_support    REAL,
                consolidation_high   REAL,
                recent_atr           REAL,
                avg_volume_20        REAL,
                atr_contraction_pct  REAL,
                relative_weakness_pct REAL,
                distribution_days_10 INTEGER,
                volume_on_decline    INTEGER NOT NULL DEFAULT 0,
                close_position       REAL,
                base_tightness       REAL,
                lower_highs          INTEGER NOT NULL DEFAULT 0,
                support_distance_pct REAL,
                pre_breakdown_score  REAL,
                candidate_state      TEXT,
                model_version        TEXT,
                outcome_status       TEXT NOT NULL DEFAULT 'PENDING',
                seed_source          TEXT,
                decision_id          INTEGER,
                run_id               TEXT,
                council_decision     TEXT,
                decision_cohort      TEXT,
                decision_reason      TEXT,
                decision_score       REAL,
                scanner_rr           REAL,
                regime_status        TEXT,
                regime_volatility    TEXT,
                regime_vix           TEXT,
                vix_level            REAL,
                features_json        TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bte_short_candidates
            ON bte_short_candidates(ticker, strategy_source, candidate_date)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bte_short_outcomes (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id              INTEGER NOT NULL,
                computed_at               TEXT NOT NULL,
                horizon_days              INTEGER NOT NULL,
                breakdown_triggered       INTEGER NOT NULL DEFAULT 0,
                breakdown_timestamp       TEXT,
                trading_days_to_breakdown INTEGER,
                breakdown_close           REAL,
                breakdown_volume_ratio    REAL,
                breakdown_close_position  REAL,
                max_close_pct_below_support REAL,
                end_return_pct            REAL,
                data_source               TEXT,
                data_available            INTEGER NOT NULL DEFAULT 1,
                model_version             TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_bte_short_outcomes
            ON bte_short_outcomes(candidate_id, horizon_days)
            """
        )
        cur.execute("PRAGMA table_info(bte_candidates)")
        existing_cols = {row[1] for row in cur.fetchall()}
        candidate_additions = {
            "seed_source": "TEXT",
            "decision_id": "INTEGER",
            "run_id": "TEXT",
            "council_decision": "TEXT",
            "decision_cohort": "TEXT",
            "decision_reason": "TEXT",
            "decision_score": "REAL",
            "scanner_rr": "REAL",
            "regime_status": "TEXT",
            "regime_volatility": "TEXT",
            "regime_vix": "TEXT",
            "vix_level": "REAL",
        }
        for col_name, col_type in candidate_additions.items():
            if col_name not in existing_cols:
                cur.execute(f"ALTER TABLE bte_candidates ADD COLUMN {col_name} {col_type}")
        cur.execute("PRAGMA table_info(bte_short_candidates)")
        existing_short_cols = {row[1] for row in cur.fetchall()}
        short_candidate_additions = {
            "seed_source": "TEXT",
            "decision_id": "INTEGER",
            "run_id": "TEXT",
            "council_decision": "TEXT",
            "decision_cohort": "TEXT",
            "decision_reason": "TEXT",
            "decision_score": "REAL",
            "scanner_rr": "REAL",
            "regime_status": "TEXT",
            "regime_volatility": "TEXT",
            "regime_vix": "TEXT",
            "vix_level": "REAL",
        }
        for col_name, col_type in short_candidate_additions.items():
            if col_name not in existing_short_cols:
                cur.execute(f"ALTER TABLE bte_short_candidates ADD COLUMN {col_name} {col_type}")
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
        )
        has_decisions_table = cur.fetchone() is not None
        if has_decisions_table and {"decision_id", "council_decision", "decision_cohort", "decision_reason"}.issubset(
            existing_cols | set(candidate_additions.keys())
        ):
            cur.execute("PRAGMA table_info(decisions)")
            decision_cols = {row[1] for row in cur.fetchall()}
            def _decision_select(col_name: str) -> str:
                return f"d.{col_name}" if col_name in decision_cols else f"NULL AS {col_name}"
            cur.execute(
                """
                SELECT c.id, c.decision_id, c.decision_reason, c.council_decision, c.decision_cohort,
                       c.decision_score, c.scanner_rr, c.regime_status, c.regime_volatility,
                       c.regime_vix, c.vix_level,
                       d.council_decision, d.council_reason, d.rr, d.avg_score,
                       {decision_regime_status}, {decision_regime_volatility}, {decision_regime_vix}
                FROM bte_candidates c
                JOIN decisions d
                  ON d.id = c.decision_id
                WHERE c.decision_id IS NOT NULL
                  AND (
                    c.council_decision IS NULL OR c.council_decision = ''
                    OR c.decision_cohort IS NULL OR c.decision_cohort = ''
                    OR c.decision_reason IS NULL OR c.decision_reason = ''
                    OR c.decision_score IS NULL
                    OR c.scanner_rr IS NULL
                    OR c.regime_status IS NULL OR c.regime_status = ''
                    OR c.regime_volatility IS NULL OR c.regime_volatility = ''
                    OR c.regime_vix IS NULL OR c.regime_vix = ''
                  )
                """.format(
                    decision_regime_status=_decision_select("regime_status"),
                    decision_regime_volatility=_decision_select("regime_volatility"),
                    decision_regime_vix=_decision_select("regime_vix"),
                )
            )
            for (
                candidate_id,
                _decision_id,
                existing_reason,
                existing_council_decision,
                existing_cohort,
                existing_decision_score,
                existing_scanner_rr,
                existing_regime_status,
                existing_regime_volatility,
                existing_regime_vix,
                existing_vix_level,
                council_decision,
                council_reason,
                rr,
                avg_score,
                decision_regime_status,
                decision_regime_volatility,
                decision_regime_vix,
            ) in cur.fetchall():
                resolved_reason = existing_reason or council_reason
                resolved_decision = existing_council_decision or council_decision
                resolved_cohort = existing_cohort or self._sniper_decision_cohort(
                    council_decision=resolved_decision,
                    council_reason=resolved_reason,
                    rr=rr,
                    avg_score=avg_score,
                )
                cur.execute(
                    """
                    UPDATE bte_candidates
                    SET council_decision = ?, decision_cohort = ?, decision_reason = ?,
                        decision_score = ?, scanner_rr = ?, regime_status = ?,
                        regime_volatility = ?, regime_vix = ?, vix_level = ?
                    WHERE id = ?
                    """,
                    (
                        resolved_decision,
                        resolved_cohort,
                        resolved_reason,
                        existing_decision_score if existing_decision_score is not None else avg_score,
                        existing_scanner_rr if existing_scanner_rr is not None else rr,
                        existing_regime_status or self._normalize_regime_label(decision_regime_status),
                        existing_regime_volatility or self._normalize_regime_label(decision_regime_volatility),
                        existing_regime_vix or self._normalize_regime_label(decision_regime_vix or decision_regime_volatility),
                        existing_vix_level,
                        int(candidate_id),
                    ),
                )
            cur.execute(
                """
                SELECT c.id, c.decision_id, c.decision_reason, c.council_decision, c.decision_cohort,
                       c.decision_score, c.scanner_rr, c.regime_status, c.regime_volatility,
                       c.regime_vix, c.vix_level,
                       d.council_decision, d.council_reason, d.rr, d.avg_score,
                       {decision_regime_status}, {decision_regime_volatility}, {decision_regime_vix}
                FROM bte_short_candidates c
                JOIN decisions d
                  ON d.id = c.decision_id
                WHERE c.decision_id IS NOT NULL
                  AND (
                    c.council_decision IS NULL OR c.council_decision = ''
                    OR c.decision_cohort IS NULL OR c.decision_cohort = ''
                    OR c.decision_reason IS NULL OR c.decision_reason = ''
                    OR c.decision_score IS NULL
                    OR c.scanner_rr IS NULL
                    OR c.regime_status IS NULL OR c.regime_status = ''
                    OR c.regime_volatility IS NULL OR c.regime_volatility = ''
                    OR c.regime_vix IS NULL OR c.regime_vix = ''
                  )
                """.format(
                    decision_regime_status=_decision_select("regime_status"),
                    decision_regime_volatility=_decision_select("regime_volatility"),
                    decision_regime_vix=_decision_select("regime_vix"),
                )
            )
            for (
                candidate_id,
                _decision_id,
                existing_reason,
                existing_council_decision,
                existing_cohort,
                existing_decision_score,
                existing_scanner_rr,
                existing_regime_status,
                existing_regime_volatility,
                existing_regime_vix,
                existing_vix_level,
                council_decision,
                council_reason,
                rr,
                avg_score,
                decision_regime_status,
                decision_regime_volatility,
                decision_regime_vix,
            ) in cur.fetchall():
                resolved_reason = existing_reason or council_reason
                resolved_decision = existing_council_decision or council_decision
                resolved_cohort = existing_cohort or self._short_decision_cohort(
                    council_decision=resolved_decision,
                    council_reason=resolved_reason,
                    rr=rr,
                    avg_score=avg_score,
                )
                cur.execute(
                    """
                    UPDATE bte_short_candidates
                    SET council_decision = ?, decision_cohort = ?, decision_reason = ?,
                        decision_score = ?, scanner_rr = ?, regime_status = ?,
                        regime_volatility = ?, regime_vix = ?, vix_level = ?
                    WHERE id = ?
                    """,
                    (
                        resolved_decision,
                        resolved_cohort,
                        resolved_reason,
                        existing_decision_score if existing_decision_score is not None else avg_score,
                        existing_scanner_rr if existing_scanner_rr is not None else rr,
                        existing_regime_status or self._normalize_regime_label(decision_regime_status),
                        existing_regime_volatility or self._normalize_regime_label(decision_regime_volatility),
                        existing_regime_vix or self._normalize_regime_label(decision_regime_vix or decision_regime_volatility),
                        existing_vix_level,
                        int(candidate_id),
                    ),
                )
        conn.commit()
        conn.close()


def _parse_tickers(raw: str) -> List[str]:
    return [token.strip().upper() for token in str(raw or "").split(",") if token.strip()]


def _parse_csv_tokens(raw: str, *, upper: bool = False) -> List[str]:
    values = []
    for token in str(raw or "").split(","):
        cleaned = token.strip()
        if not cleaned:
            continue
        values.append(cleaned.upper() if upper else cleaned)
    return values


def _parse_horizons(raw: str) -> List[int]:
    horizons = []
    for token in str(raw or "").split(","):
        cleaned = token.strip()
        if not cleaned:
            continue
        try:
            value = int(cleaned)
        except ValueError:
            continue
        if value > 0 and value not in horizons:
            horizons.append(value)
    return horizons or list(DEFAULT_BTE_HORIZONS)


def _format_segment_rows(rows: Sequence[Dict[str, Any]]) -> List[str]:
    lines = []
    for row in rows:
        lines.append(
            "  {label:<11} n={n:<3} breakout={breakout_count:<3} rate={breakout_rate} "
            "avg_days={avg_days} avg_ret={avg_ret}% avg_score={avg_score}".format(
                label=row["label"],
                n=row["n"],
                breakout_count=row["breakout_count"],
                breakout_rate=row["breakout_rate"] if row["breakout_rate"] is not None else "—",
                avg_days=row["avg_days_to_breakout"] if row["avg_days_to_breakout"] is not None else "—",
                avg_ret=row["avg_end_return_pct"] if row["avg_end_return_pct"] is not None else "—",
                avg_score=row["avg_pre_breakout_score"] if row["avg_pre_breakout_score"] is not None else "—",
            )
        )
    return lines


def _format_segmented_summary(summary: Dict[str, Any]) -> str:
    overall = summary["overall"]
    status = summary["candidate_status"]
    timing = summary["timing_distribution"]
    lines = [
        "BTE advisory report",
        f"  model_version: {summary['model_version']}",
        f"  horizon_days: {overall['horizon_days']}",
        f"  completed_n: {overall['n']}",
        f"  breakout_count: {overall['breakout_count']}",
        f"  breakout_rate: {overall['breakout_rate']}",
        f"  avg_days_to_breakout: {overall['avg_days_to_breakout']}",
        f"  avg_end_return_pct: {overall['avg_end_return_pct']}",
        f"  guidance_ready: {summary.get('guidance_ready')}",
        (
            "  candidates: total={total} computed={computed} pending={pending} no_data={no_data}".format(
                total=status["total_candidates"],
                computed=status["computed_count"],
                pending=status["pending_count"],
                no_data=status["no_data_count"],
            )
        ),
    ]
    if summary.get("guidance_reason"):
        lines.append(f"  guidance_reason: {summary['guidance_reason']}")
    lines.extend(["", "By candidate state"])
    lines.extend(_format_segment_rows(summary["segments"]["candidate_state"]))
    lines.append("")
    lines.append("By score bucket")
    lines.extend(_format_segment_rows(summary["segments"]["score_bucket"]))
    lines.append("")
    lines.append("By compression bucket")
    lines.extend(_format_segment_rows(summary["segments"]["compression_bucket"]))
    lines.append("")
    lines.append("By decision cohort")
    lines.extend(_format_segment_rows(summary["segments"]["decision_cohort"]))
    lines.append("")
    lines.append("By decision reason")
    lines.extend(_format_segment_rows(summary["segments"]["decision_reason"][:10]))
    lines.append("")
    lines.append("By regime status")
    lines.extend(_format_segment_rows(summary["segments"]["regime_status"]))
    lines.append("")
    lines.append("By regime volatility")
    lines.extend(_format_segment_rows(summary["segments"]["regime_volatility"]))
    lines.append("")
    lines.append("By VIX bucket")
    lines.extend(_format_segment_rows(summary["segments"]["regime_vix"]))
    lines.append("")
    lines.append("Timing distribution")
    lines.append(f"  triggered_n={timing['triggered_n']} avg_days={timing['avg_days_to_breakout']}")
    for bucket in timing["buckets"]:
        lines.append(
            "  {label:<8} n={n:<3} rate_triggered={rt} rate_total={ra}".format(
                label=bucket["label"],
                n=bucket["n"],
                rt=bucket["rate_of_triggered"] if bucket["rate_of_triggered"] is not None else "—",
                ra=bucket["rate_of_total"] if bucket["rate_of_total"] is not None else "—",
            )
        )
    return "\n".join(lines)


def _format_short_segmented_summary(summary: Dict[str, Any]) -> str:
    overall = summary["overall"]
    status = summary["candidate_status"]
    timing = summary["timing_distribution"]
    lines = [
        "BTE short advisory report",
        f"  model_version: {summary['model_version']}",
        f"  horizon_days: {overall['horizon_days']}",
        f"  completed_n: {overall['n']}",
        f"  breakdown_count: {overall['breakout_count']}",
        f"  breakdown_rate: {overall['breakout_rate']}",
        f"  avg_days_to_breakdown: {overall['avg_days_to_breakout']}",
        f"  avg_end_return_pct: {overall['avg_end_return_pct']}",
        f"  guidance_ready: {summary.get('guidance_ready')}",
        (
            "  candidates: total={total} computed={computed} pending={pending} no_data={no_data}".format(
                total=status["total_candidates"],
                computed=status["computed_count"],
                pending=status["pending_count"],
                no_data=status["no_data_count"],
            )
        ),
    ]
    if summary.get("guidance_reason"):
        lines.append(f"  guidance_reason: {summary['guidance_reason']}")
    lines.extend(["", "By candidate state"])
    lines.extend(_format_segment_rows(summary["segments"]["candidate_state"]))
    lines.append("")
    lines.append("By score bucket")
    lines.extend(_format_segment_rows(summary["segments"]["score_bucket"]))
    lines.append("")
    lines.append("By compression bucket")
    lines.extend(_format_segment_rows(summary["segments"]["compression_bucket"]))
    lines.append("")
    lines.append("By decision cohort")
    lines.extend(_format_segment_rows(summary["segments"]["decision_cohort"]))
    lines.append("")
    lines.append("By decision reason")
    lines.extend(_format_segment_rows(summary["segments"]["decision_reason"][:10]))
    lines.append("")
    lines.append("By regime status")
    lines.extend(_format_segment_rows(summary["segments"]["regime_status"]))
    lines.append("")
    lines.append("By regime volatility")
    lines.extend(_format_segment_rows(summary["segments"]["regime_volatility"]))
    lines.append("")
    lines.append("By VIX bucket")
    lines.extend(_format_segment_rows(summary["segments"]["regime_vix"]))
    lines.append("")
    lines.append("Timing distribution")
    lines.append(f"  triggered_n={timing['triggered_n']} avg_days={timing['avg_days_to_breakout']}")
    for bucket in timing["buckets"]:
        lines.append(
            "  {label:<8} n={n:<3} rate_triggered={rt} rate_total={ra}".format(
                label=bucket["label"],
                n=bucket["n"],
                rt=bucket["rate_of_triggered"] if bucket["rate_of_triggered"] is not None else "—",
                ra=bucket["rate_of_total"] if bucket["rate_of_total"] is not None else "—",
            )
        )
    return "\n".join(lines)


def _format_calibration_rows(rows: Sequence[Dict[str, Any]]) -> List[str]:
    output = []
    for row in rows:
        output.append(
            "  {label:<28} n={n:<3} rate={rate} lift={lift} median_days={median} "
            "window={low}-{high} avg_ret={avg_ret}% min_n={sample_pass}".format(
                label=row["label"],
                n=row["n"],
                rate=row["breakout_rate"] if row["breakout_rate"] is not None else "—",
                lift=row["lift_vs_overall"] if row["lift_vs_overall"] is not None else "—",
                median=row["median_days_to_breakout"] if row["median_days_to_breakout"] is not None else "—",
                low=row["timing_window_low_days"] if row["timing_window_low_days"] is not None else "—",
                high=row["timing_window_high_days"] if row["timing_window_high_days"] is not None else "—",
                avg_ret=row["avg_end_return_pct"] if row["avg_end_return_pct"] is not None else "—",
                sample_pass="Y" if row["sample_pass"] else "N",
            )
        )
    return output


def _format_calibration_report(report: Dict[str, Any]) -> str:
    overall = report["overall"]
    lines = [
        "BTE empirical calibration report",
        f"  model_version: {report['model_version']}",
        f"  horizon_days: {report['horizon_days']}",
        f"  min_segment_n: {report['min_segment_n']}",
        f"  guidance_ready: {report.get('guidance_ready')}",
        f"  overall_n: {overall['n']}",
        f"  overall_breakout_count: {overall['breakout_count']}",
        f"  overall_breakout_rate: {overall['breakout_rate']}",
        f"  overall_avg_days_to_breakout: {overall['avg_days_to_breakout']}",
        f"  overall_avg_end_return_pct: {overall['avg_end_return_pct']}",
    ]
    if report.get("guidance_reason"):
        lines.append(f"  guidance_reason: {report['guidance_reason']}")
    lines.extend(["", "Calibration by candidate state"])
    lines.extend(_format_calibration_rows(report["dimensions"]["candidate_state"]))
    lines.append("")
    lines.append("Calibration by score bucket")
    lines.extend(_format_calibration_rows(report["dimensions"]["score_bucket"]))
    lines.append("")
    lines.append("Calibration by compression bucket")
    lines.extend(_format_calibration_rows(report["dimensions"]["compression_bucket"]))
    lines.append("")
    lines.append("Calibration by decision cohort")
    lines.extend(_format_calibration_rows(report["dimensions"]["decision_cohort"]))
    lines.append("")
    lines.append("Calibration by decision reason")
    lines.extend(_format_calibration_rows(report["dimensions"]["decision_reason"][:10]))
    lines.append("")
    lines.append("Calibration by regime status")
    lines.extend(_format_calibration_rows(report["dimensions"]["regime_status"]))
    lines.append("")
    lines.append("Calibration by regime volatility")
    lines.extend(_format_calibration_rows(report["dimensions"]["regime_volatility"]))
    lines.append("")
    lines.append("Calibration by VIX bucket")
    lines.extend(_format_calibration_rows(report["dimensions"]["regime_vix"]))
    lines.append("")
    lines.append("Composite segments meeting min sample")
    composite_rows = report["composite_segments"]
    if composite_rows:
        lines.extend(_format_calibration_rows(composite_rows))
    else:
        lines.append("  none")
    return "\n".join(lines)


def _format_short_calibration_report(report: Dict[str, Any]) -> str:
    overall = report["overall"]
    lines = [
        "BTE short empirical calibration report",
        f"  model_version: {report['model_version']}",
        f"  horizon_days: {report['horizon_days']}",
        f"  min_segment_n: {report['min_segment_n']}",
        f"  guidance_ready: {report.get('guidance_ready')}",
        f"  overall_n: {overall['n']}",
        f"  overall_breakdown_count: {overall['breakout_count']}",
        f"  overall_breakdown_rate: {overall['breakout_rate']}",
        f"  overall_avg_days_to_breakdown: {overall['avg_days_to_breakout']}",
        f"  overall_avg_end_return_pct: {overall['avg_end_return_pct']}",
    ]
    if report.get("guidance_reason"):
        lines.append(f"  guidance_reason: {report['guidance_reason']}")
    lines.extend(["", "Calibration by candidate state"])
    lines.extend(_format_calibration_rows(report["dimensions"]["candidate_state"]))
    lines.append("")
    lines.append("Calibration by score bucket")
    lines.extend(_format_calibration_rows(report["dimensions"]["score_bucket"]))
    lines.append("")
    lines.append("Calibration by compression bucket")
    lines.extend(_format_calibration_rows(report["dimensions"]["compression_bucket"]))
    lines.append("")
    lines.append("Calibration by decision cohort")
    lines.extend(_format_calibration_rows(report["dimensions"]["decision_cohort"]))
    lines.append("")
    lines.append("Calibration by decision reason")
    lines.extend(_format_calibration_rows(report["dimensions"]["decision_reason"][:10]))
    lines.append("")
    lines.append("Calibration by regime status")
    lines.extend(_format_calibration_rows(report["dimensions"]["regime_status"]))
    lines.append("")
    lines.append("Calibration by regime volatility")
    lines.extend(_format_calibration_rows(report["dimensions"]["regime_volatility"]))
    lines.append("")
    lines.append("Calibration by VIX bucket")
    lines.extend(_format_calibration_rows(report["dimensions"]["regime_vix"]))
    lines.append("")
    lines.append("Composite segments meeting min sample")
    composite_rows = report["composite_segments"]
    if composite_rows:
        lines.extend(_format_calibration_rows(composite_rows))
    else:
        lines.append("  none")
    return "\n".join(lines)


def _format_score_comparison_report(report: Dict[str, Any]) -> str:
    overall = report["overall"]
    best_bte = report.get("best_bte_bucket") or {}
    best_raw = report.get("best_raw_bucket") or {}
    lines = [
        "BTE vs raw SNIPER score comparison",
        f"  model_version: {report['model_version']}",
        f"  horizon_days: {report['horizon_days']}",
        f"  min_segment_n: {report['min_segment_n']}",
        f"  guidance_ready: {report.get('guidance_ready')}",
        f"  overall_n: {overall['n']}",
        f"  overall_breakout_count: {overall['breakout_count']}",
        f"  overall_breakout_rate: {overall['breakout_rate']}",
        f"  overall_avg_days_to_breakout: {overall['avg_days_to_breakout']}",
        f"  overall_avg_end_return_pct: {overall['avg_end_return_pct']}",
    ]
    if report.get("guidance_reason"):
        lines.append(f"  guidance_reason: {report['guidance_reason']}")
    lines.extend(["", "Calibration by BTE pre-breakout score"])
    lines.extend(_format_calibration_rows(report["dimensions"]["bte_pre_breakout_score"]))
    lines.append("")
    lines.append("Calibration by raw SNIPER score")
    lines.extend(_format_calibration_rows(report["dimensions"]["raw_sniper_score"]))
    lines.append("")
    lines.append("Comparison")
    if best_bte:
        lines.append(
            "  best_bte_bucket: {label}  lift={lift}  rate={rate}  window={low}-{high}".format(
                label=best_bte.get("label"),
                lift=best_bte.get("lift_vs_overall"),
                rate=best_bte.get("breakout_rate"),
                low=best_bte.get("timing_window_low_days") if best_bte.get("timing_window_low_days") is not None else "—",
                high=best_bte.get("timing_window_high_days") if best_bte.get("timing_window_high_days") is not None else "—",
            )
        )
    else:
        lines.append("  best_bte_bucket: none")
    if best_raw:
        lines.append(
            "  best_raw_bucket: {label}  lift={lift}  rate={rate}  window={low}-{high}".format(
                label=best_raw.get("label"),
                lift=best_raw.get("lift_vs_overall"),
                rate=best_raw.get("breakout_rate"),
                low=best_raw.get("timing_window_low_days") if best_raw.get("timing_window_low_days") is not None else "—",
                high=best_raw.get("timing_window_high_days") if best_raw.get("timing_window_high_days") is not None else "—",
            )
        )
    else:
        lines.append("  best_raw_bucket: none")
    lines.append(f"  winner_dimension: {report.get('winner_dimension') or 'NONE'}")
    if report.get("winner_reason"):
        lines.append(f"  winner_reason: {report['winner_reason']}")
    return "\n".join(lines)


def _format_short_score_comparison_report(report: Dict[str, Any]) -> str:
    overall = report["overall"]
    best_bte = report.get("best_bte_bucket") or {}
    best_raw = report.get("best_raw_bucket") or {}
    lines = [
        "BTE vs raw SHORT score comparison",
        f"  model_version: {report['model_version']}",
        f"  horizon_days: {report['horizon_days']}",
        f"  min_segment_n: {report['min_segment_n']}",
        f"  guidance_ready: {report.get('guidance_ready')}",
        f"  overall_n: {overall['n']}",
        f"  overall_breakdown_count: {overall['breakout_count']}",
        f"  overall_breakdown_rate: {overall['breakout_rate']}",
        f"  overall_avg_days_to_breakdown: {overall['avg_days_to_breakout']}",
        f"  overall_avg_end_return_pct: {overall['avg_end_return_pct']}",
    ]
    if report.get("guidance_reason"):
        lines.append(f"  guidance_reason: {report['guidance_reason']}")
    lines.extend(["", "Calibration by BTE pre-breakdown score"])
    lines.extend(_format_calibration_rows(report["dimensions"]["bte_pre_breakdown_score"]))
    lines.append("")
    lines.append("Calibration by raw SHORT score")
    lines.extend(_format_calibration_rows(report["dimensions"]["raw_short_score"]))
    lines.append("")
    lines.append("Comparison")
    if best_bte:
        lines.append(
            "  best_bte_bucket: {label}  lift={lift}  rate={rate}  window={low}-{high}".format(
                label=best_bte.get("label"),
                lift=best_bte.get("lift_vs_overall"),
                rate=best_bte.get("breakout_rate"),
                low=best_bte.get("timing_window_low_days") if best_bte.get("timing_window_low_days") is not None else "—",
                high=best_bte.get("timing_window_high_days") if best_bte.get("timing_window_high_days") is not None else "—",
            )
        )
    else:
        lines.append("  best_bte_bucket: none")
    if best_raw:
        lines.append(
            "  best_raw_bucket: {label}  lift={lift}  rate={rate}  window={low}-{high}".format(
                label=best_raw.get("label"),
                lift=best_raw.get("lift_vs_overall"),
                rate=best_raw.get("breakout_rate"),
                low=best_raw.get("timing_window_low_days") if best_raw.get("timing_window_low_days") is not None else "—",
                high=best_raw.get("timing_window_high_days") if best_raw.get("timing_window_high_days") is not None else "—",
            )
        )
    else:
        lines.append("  best_raw_bucket: none")
    lines.append(f"  winner_dimension: {report.get('winner_dimension') or 'NONE'}")
    if report.get("winner_reason"):
        lines.append(f"  winner_reason: {report['winner_reason']}")
    return "\n".join(lines)


def _format_multi_horizon_reports(reports: Sequence[Dict[str, Any]], formatter) -> str:
    return "\n\n".join(formatter(report) for report in reports)


def _artifact_horizon_slug(horizons: Sequence[int]) -> str:
    unique = sorted({int(h) for h in horizons})
    if len(unique) == 1:
        return f"{unique[0]}d"
    return "multi_" + "-".join(f"{h}d" for h in unique)


def _save_report_artifacts(
    *,
    kind: str,
    payload: Any,
    rendered_text: str,
    horizons: Sequence[int],
    logs_dir: str = "logs",
) -> Dict[str, str]:
    os.makedirs(logs_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    horizon_slug = _artifact_horizon_slug(horizons)
    base = f"bte_{kind}_{stamp}_{horizon_slug}"
    json_path = os.path.join(logs_dir, f"{base}.json")
    text_path = os.path.join(logs_dir, f"{base}.txt")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with open(text_path, "w", encoding="utf-8") as handle:
        handle.write(rendered_text.rstrip() + "\n")
    return {"json_path": json_path, "text_path": text_path}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Breakout Timing Engine advisory toolkit")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="Seed BTE candidates from a ticker list")
    seed.add_argument("--tickers", required=True, help="Comma-separated ticker list")
    seed.add_argument("--strategy-source", default="SNIPER")
    seed.add_argument("--min-score", type=float, default=MIN_PRE_BREAKOUT_SCORE)

    seed_watchlist = sub.add_parser("seed-watchlist", help="Seed BTE candidates from full SNIPER watchlist snapshots")
    seed_watchlist.add_argument("--tickers", help="Optional comma-separated tickers; defaults to adaptive SNIPER watchlist")
    seed_watchlist.add_argument("--min-score", type=float, default=MIN_PRE_BREAKOUT_SCORE)
    seed_watchlist.add_argument("--max-tickers", type=int)

    seed_decisions = sub.add_parser("seed-decisions", help="Seed BTE candidates from recent SNIPER decisions")
    seed_decisions.add_argument("--days", type=int, default=30)
    seed_decisions.add_argument(
        "--reasons",
        default=",".join(DEFAULT_DECISION_SEED_REASONS),
        help="Comma-separated council_reason values to include",
    )
    seed_decisions.add_argument("--min-pre-breakout-score", type=float, default=MIN_PRE_BREAKOUT_SCORE)
    seed_decisions.add_argument("--min-decision-score", type=float, default=0.0)

    seed_short_decisions = sub.add_parser("seed-short-decisions", help="Seed short-side BTE candidates from recent SHORT decisions")
    seed_short_decisions.add_argument("--days", type=int, default=30)
    seed_short_decisions.add_argument(
        "--reasons",
        default=",".join(DEFAULT_SHORT_DECISION_SEED_REASONS),
        help="Comma-separated council_reason values to include",
    )
    seed_short_decisions.add_argument("--min-pre-breakdown-score", type=float, default=MIN_PRE_BREAKDOWN_SCORE)
    seed_short_decisions.add_argument("--min-decision-score", type=float, default=0.0)

    label = sub.add_parser("label", help="Label forward breakout outcomes")
    label.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    label.add_argument("--horizons", help="Comma-separated horizons (e.g. 10,15,20)")
    label.add_argument("--no-retry-no-data", action="store_true")

    label_short = sub.add_parser("label-short", help="Label forward short breakdown outcomes")
    label_short.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    label_short.add_argument("--horizons", help="Comma-separated horizons (e.g. 10,15,20)")
    label_short.add_argument("--no-retry-no-data", action="store_true")

    report = sub.add_parser("report", help="Print BTE summary")
    report.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    report.add_argument("--horizons", help="Comma-separated horizons (e.g. 10,15,20)")
    report.add_argument("--json", action="store_true")
    report.add_argument("--save", action="store_true", help="Write text + JSON artifacts to logs/")

    report_short = sub.add_parser("report-short", help="Print short-side BTE summary")
    report_short.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    report_short.add_argument("--horizons", help="Comma-separated horizons (e.g. 10,15,20)")
    report_short.add_argument("--json", action="store_true")
    report_short.add_argument("--save", action="store_true", help="Write text + JSON artifacts to logs/")

    calibrate = sub.add_parser("calibrate", help="Print empirical BTE calibration report")
    calibrate.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    calibrate.add_argument("--horizons", help="Comma-separated horizons (e.g. 10,15,20)")
    calibrate.add_argument("--min-segment-n", type=int, default=5)
    calibrate.add_argument("--json", action="store_true")
    calibrate.add_argument("--save", action="store_true", help="Write text + JSON artifacts to logs/")

    calibrate_short = sub.add_parser("calibrate-short", help="Print empirical short-side BTE calibration report")
    calibrate_short.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    calibrate_short.add_argument("--horizons", help="Comma-separated horizons (e.g. 10,15,20)")
    calibrate_short.add_argument("--min-segment-n", type=int, default=5)
    calibrate_short.add_argument("--json", action="store_true")
    calibrate_short.add_argument("--save", action="store_true", help="Write text + JSON artifacts to logs/")

    compare = sub.add_parser("compare", help="Compare BTE score buckets vs raw SNIPER score buckets")
    compare.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    compare.add_argument("--horizons", help="Comma-separated horizons (e.g. 10,15,20)")
    compare.add_argument("--min-segment-n", type=int, default=5)
    compare.add_argument("--json", action="store_true")
    compare.add_argument("--save", action="store_true", help="Write text + JSON artifacts to logs/")

    compare_short = sub.add_parser("compare-short", help="Compare short-side BTE score buckets vs raw SHORT score buckets")
    compare_short.add_argument("--horizon-days", type=int, default=DEFAULT_HORIZON_DAYS)
    compare_short.add_argument("--horizons", help="Comma-separated horizons (e.g. 10,15,20)")
    compare_short.add_argument("--min-segment-n", type=int, default=5)
    compare_short.add_argument("--json", action="store_true")
    compare_short.add_argument("--save", action="store_true", help="Write text + JSON artifacts to logs/")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    tracker = BreakoutTimingTracker(db_path=args.db)

    if args.command == "seed":
        inserted = tracker.seed_from_tickers(
            _parse_tickers(args.tickers),
            strategy_source=args.strategy_source,
            min_score=float(args.min_score),
        )
        print(f"BTE candidates inserted: {inserted}")
        return

    if args.command == "seed-watchlist":
        tickers = _parse_tickers(args.tickers) if getattr(args, "tickers", None) else None
        inserted = tracker.seed_from_sniper_watchlist(
            tickers=tickers,
            min_score=float(args.min_score),
            max_tickers=args.max_tickers,
        )
        print(f"BTE candidates inserted from watchlist: {inserted}")
        return

    if args.command == "seed-decisions":
        inserted = tracker.seed_from_recent_sniper_decisions(
            days=int(args.days),
            include_reasons=_parse_csv_tokens(args.reasons.lower()),
            min_pre_breakout_score=float(args.min_pre_breakout_score),
            min_decision_score=float(args.min_decision_score),
        )
        print(f"BTE candidates inserted from decisions: {inserted}")
        return

    if args.command == "seed-short-decisions":
        inserted = tracker.seed_from_recent_short_decisions(
            days=int(args.days),
            include_reasons=_parse_csv_tokens(args.reasons.lower()),
            min_pre_breakdown_score=float(args.min_pre_breakdown_score),
            min_decision_score=float(args.min_decision_score),
        )
        print(f"BTE short candidates inserted from decisions: {inserted}")
        return

    if args.command == "label":
        horizons = _parse_horizons(args.horizons) if getattr(args, "horizons", None) else [int(args.horizon_days)]
        total = 0
        for horizon in horizons:
            computed = tracker.label_outcomes(
                horizon_days=int(horizon),
                retry_no_data=not bool(args.no_retry_no_data),
            )
            total += computed
            print(f"BTE outcomes computed ({horizon}d): {computed}")
        if len(horizons) > 1:
            print(f"BTE outcomes computed total: {total}")
        return

    if args.command == "label-short":
        horizons = _parse_horizons(args.horizons) if getattr(args, "horizons", None) else [int(args.horizon_days)]
        total = 0
        for horizon in horizons:
            computed = tracker.label_short_outcomes(
                horizon_days=int(horizon),
                retry_no_data=not bool(args.no_retry_no_data),
            )
            total += computed
            print(f"BTE short outcomes computed ({horizon}d): {computed}")
        if len(horizons) > 1:
            print(f"BTE short outcomes computed total: {total}")
        return

    if args.command == "calibrate":
        horizons = _parse_horizons(args.horizons) if getattr(args, "horizons", None) else [int(args.horizon_days)]
        reports = [
            tracker.get_calibration_report(
                horizon_days=int(horizon),
                min_segment_n=int(args.min_segment_n),
            )
            for horizon in horizons
        ]
        if args.json:
            payload: Any = reports[0] if len(reports) == 1 else {"reports": reports}
            print(json.dumps(payload, indent=2, sort_keys=True))
            return
        rendered = _format_multi_horizon_reports(reports, _format_calibration_report)
        artifact_paths = None
        if getattr(args, "save", False):
            payload = reports[0] if len(reports) == 1 else {"reports": reports}
            artifact_paths = _save_report_artifacts(
                kind="calibration",
                payload=payload,
                rendered_text=rendered,
                horizons=horizons,
            )
        print(rendered)
        if artifact_paths:
            print(f"Saved: {artifact_paths['text_path']}  {artifact_paths['json_path']}")
        return

    if args.command == "calibrate-short":
        horizons = _parse_horizons(args.horizons) if getattr(args, "horizons", None) else [int(args.horizon_days)]
        reports = [
            tracker.get_short_calibration_report(
                horizon_days=int(horizon),
                min_segment_n=int(args.min_segment_n),
            )
            for horizon in horizons
        ]
        if args.json:
            payload = reports[0] if len(reports) == 1 else {"reports": reports}
            print(json.dumps(payload, indent=2, sort_keys=True))
            return
        rendered = _format_multi_horizon_reports(reports, _format_short_calibration_report)
        artifact_paths = None
        if getattr(args, "save", False):
            payload = reports[0] if len(reports) == 1 else {"reports": reports}
            artifact_paths = _save_report_artifacts(
                kind="short_calibration",
                payload=payload,
                rendered_text=rendered,
                horizons=horizons,
            )
        print(rendered)
        if artifact_paths:
            print(f"Saved: {artifact_paths['text_path']}  {artifact_paths['json_path']}")
        return

    if args.command == "compare":
        horizons = _parse_horizons(args.horizons) if getattr(args, "horizons", None) else [int(args.horizon_days)]
        reports = [
            tracker.get_score_comparison_report(
                horizon_days=int(horizon),
                min_segment_n=int(args.min_segment_n),
            )
            for horizon in horizons
        ]
        if args.json:
            payload = reports[0] if len(reports) == 1 else {"reports": reports}
            print(json.dumps(payload, indent=2, sort_keys=True))
            return
        rendered = _format_multi_horizon_reports(reports, _format_score_comparison_report)
        artifact_paths = None
        if getattr(args, "save", False):
            payload = reports[0] if len(reports) == 1 else {"reports": reports}
            artifact_paths = _save_report_artifacts(
                kind="score_comparison",
                payload=payload,
                rendered_text=rendered,
                horizons=horizons,
            )
        print(rendered)
        if artifact_paths:
            print(f"Saved: {artifact_paths['text_path']}  {artifact_paths['json_path']}")
        return

    if args.command == "compare-short":
        horizons = _parse_horizons(args.horizons) if getattr(args, "horizons", None) else [int(args.horizon_days)]
        reports = [
            tracker.get_short_score_comparison_report(
                horizon_days=int(horizon),
                min_segment_n=int(args.min_segment_n),
            )
            for horizon in horizons
        ]
        if args.json:
            payload = reports[0] if len(reports) == 1 else {"reports": reports}
            print(json.dumps(payload, indent=2, sort_keys=True))
            return
        rendered = _format_multi_horizon_reports(reports, _format_short_score_comparison_report)
        artifact_paths = None
        if getattr(args, "save", False):
            payload = reports[0] if len(reports) == 1 else {"reports": reports}
            artifact_paths = _save_report_artifacts(
                kind="short_score_comparison",
                payload=payload,
                rendered_text=rendered,
                horizons=horizons,
            )
        print(rendered)
        if artifact_paths:
            print(f"Saved: {artifact_paths['text_path']}  {artifact_paths['json_path']}")
        return

    if args.command == "report-short":
        horizons = _parse_horizons(args.horizons) if getattr(args, "horizons", None) else [int(args.horizon_days)]
        summaries = [tracker.get_short_segmented_summary(horizon_days=int(horizon)) for horizon in horizons]
        if args.json:
            payload = summaries[0] if len(summaries) == 1 else {"reports": summaries}
            print(json.dumps(payload, indent=2, sort_keys=True))
            return
        rendered = _format_multi_horizon_reports(summaries, _format_short_segmented_summary)
        artifact_paths = None
        if getattr(args, "save", False):
            payload = summaries[0] if len(summaries) == 1 else {"reports": summaries}
            artifact_paths = _save_report_artifacts(
                kind="short_report",
                payload=payload,
                rendered_text=rendered,
                horizons=horizons,
            )
        print(rendered)
        if artifact_paths:
            print(f"Saved: {artifact_paths['text_path']}  {artifact_paths['json_path']}")
        return

    horizons = _parse_horizons(args.horizons) if getattr(args, "horizons", None) else [int(args.horizon_days)]
    summaries = [tracker.get_segmented_summary(horizon_days=int(horizon)) for horizon in horizons]
    if args.json:
        payload = summaries[0] if len(summaries) == 1 else {"reports": summaries}
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    rendered = _format_multi_horizon_reports(summaries, _format_segmented_summary)
    artifact_paths = None
    if getattr(args, "save", False):
        payload = summaries[0] if len(summaries) == 1 else {"reports": summaries}
        artifact_paths = _save_report_artifacts(
            kind="report",
            payload=payload,
            rendered_text=rendered,
            horizons=horizons,
        )
    print(rendered)
    if artifact_paths:
        print(f"Saved: {artifact_paths['text_path']}  {artifact_paths['json_path']}")


if __name__ == "__main__":
    main()
