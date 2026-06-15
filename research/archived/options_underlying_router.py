from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_ALLOWED_STRATEGIES = {"VOYAGER", "SNIPER", "SHORT", "CONTRARIAN"}
_LIVE_SCORE_THRESHOLDS = {
    "VOYAGER": 60.0,
    "SNIPER": 60.0,
    "SHORT": 55.0,
    "CONTRARIAN": 60.0,
}
_STRATEGY_PRIORITY = {
    "SNIPER": 1,
    "SHORT": 2,
    "VOYAGER": 3,
    "CONTRARIAN": 4,
}


@dataclass
class OptionUnderlying:
    ticker: str
    strategy: str
    direction: str
    source_type: str
    entry_price: float
    stop_loss: float
    target_price: float
    equity_rr: float
    score: float
    options_pcr: Optional[float] = None
    options_gamma: Optional[str] = None
    run_id: Optional[str] = None
    approved: bool = False
    rank_score: float = 0.0
    rank_reason: Optional[str] = None


class OptionsUnderlyingRouter:
    """Build an options-eligible underlying pool from current scan outputs."""

    def __init__(
        self,
        db_path: str = "trading_performance.db",
        lookback_hours: int = 48,
        allow_equity_overlap: bool = False,
        salvage_score_buffer: float = 5.0,
    ):
        self.db_path = db_path
        self.lookback_hours = int(lookback_hours)
        self.allow_equity_overlap = bool(allow_equity_overlap)
        self.salvage_score_buffer = float(salvage_score_buffer)

    def build(
        self,
        *,
        approved_opportunities: Optional[Iterable] = None,
        reject_rows: Optional[Iterable[Dict]] = None,
        equity_positions: Optional[Dict] = None,
    ) -> Dict[str, List[OptionUnderlying]]:
        grouped: Dict[str, Dict[str, OptionUnderlying]] = {s: {} for s in _ALLOWED_STRATEGIES}
        blocked = {str(k).upper() for k in (equity_positions or {}).keys()} if not self.allow_equity_overlap else set()

        for candidate in self._normalize_approved(approved_opportunities or []):
            if candidate.ticker in blocked:
                continue
            self._merge_candidate(grouped[candidate.strategy], candidate)

        for candidate in self._normalize_rejects(reject_rows or []):
            if candidate.ticker in blocked:
                continue
            self._merge_candidate(grouped[candidate.strategy], candidate)

        if not any(grouped[strat] for strat in grouped):
            for candidate in self._load_recent_db_candidates(blocked):
                self._merge_candidate(grouped[candidate.strategy], candidate)

        result: Dict[str, List[OptionUnderlying]] = {}
        for strategy, mapping in grouped.items():
            items = sorted(
                mapping.values(),
                key=lambda x: (
                    0 if x.approved else 1,
                    -float(x.rank_score or 0.0),
                    -float(x.score or 0.0),
                    -float(x.equity_rr or 0.0),
                    x.ticker,
                ),
            )
            result[strategy] = items
        return result

    def _merge_candidate(self, bucket: Dict[str, OptionUnderlying], candidate: OptionUnderlying) -> None:
        current = bucket.get(candidate.ticker)
        if current is None:
            bucket[candidate.ticker] = candidate
            return
        current_rank = (
            0 if current.approved else 1,
            -float(current.rank_score or 0.0),
            -float(current.score or 0.0),
            -float(current.equity_rr or 0.0),
        )
        candidate_rank = (
            0 if candidate.approved else 1,
            -float(candidate.rank_score or 0.0),
            -float(candidate.score or 0.0),
            -float(candidate.equity_rr or 0.0),
        )
        if candidate_rank < current_rank:
            bucket[candidate.ticker] = candidate

    def _normalize_approved(self, approved_opportunities: Iterable) -> List[OptionUnderlying]:
        rows: List[OptionUnderlying] = []
        for item in approved_opportunities:
            strategy = str(getattr(item, "strategy", None) or getattr(item, "source_strategy", None) or "").upper().strip()
            if strategy not in _ALLOWED_STRATEGIES:
                continue
            ticker = str(getattr(item, "ticker", None) or "").upper().strip()
            direction = str(getattr(item, "direction", None) or ("SHORT" if strategy == "SHORT" else "LONG")).upper().strip()
            entry = self._safe_float(getattr(item, "entry_price", None), None)
            stop = self._safe_float(getattr(item, "stop_loss", None), None)
            target = self._safe_float(getattr(item, "target_price", None), None)
            rr = self._safe_float(getattr(item, "risk_reward", None), None)
            score = self._safe_float(getattr(item, "score", None), 0.0)
            if not ticker or None in (entry, stop, target):
                continue
            rows.append(
                OptionUnderlying(
                    ticker=ticker,
                    strategy=strategy,
                    direction=direction,
                    source_type="approved",
                    entry_price=float(entry),
                    stop_loss=float(stop),
                    target_price=float(target),
                    equity_rr=float(rr or 0.0),
                    score=float(score or 0.0),
                    options_pcr=self._safe_float(getattr(item, "options_pcr", None), None),
                    options_gamma=getattr(item, "options_gamma", None),
                    approved=True,
                    rank_score=self._safe_float(getattr(item, "machine_rank_score", None), 0.0) or 0.0,
                    rank_reason=getattr(item, "machine_rank_reason", None),
                )
            )
        return rows

    def _normalize_rejects(self, reject_rows: Iterable[Dict]) -> List[OptionUnderlying]:
        rows: List[OptionUnderlying] = []
        for raw in reject_rows:
            strategy = str(raw.get("strategy") or "").upper().strip()
            if strategy not in _ALLOWED_STRATEGIES:
                continue
            reason = str(raw.get("reason") or raw.get("execution_deny_reason") or "").lower().strip()
            if reason != "risk_reward_too_low":
                continue
            gates_failed = {str(g).lower().strip() for g in (raw.get("gates_failed") or [])}
            if {"data", "prefilter", "pathway"} & gates_failed:
                continue
            ticker = str(raw.get("ticker") or "").upper().strip()
            direction = str(raw.get("direction") or ("SHORT" if strategy == "SHORT" else "LONG")).upper().strip()
            entry = self._safe_float(raw.get("entry") or raw.get("entry_price"), None)
            stop = self._safe_float(raw.get("stop") or raw.get("stop_loss"), None)
            target = self._safe_float(raw.get("target") or raw.get("target_price"), None)
            rr = self._safe_float(raw.get("rr") or raw.get("risk_reward"), None)
            score = self._safe_float(raw.get("score") or raw.get("avg_score"), 0.0)
            if not ticker or None in (entry, stop, target):
                continue
            live_threshold = _LIVE_SCORE_THRESHOLDS.get(strategy, 60.0)
            if score < (live_threshold - self.salvage_score_buffer):
                continue
            rows.append(
                OptionUnderlying(
                    ticker=ticker,
                    strategy=strategy,
                    direction=direction,
                    source_type="rr_reject",
                    entry_price=float(entry),
                    stop_loss=float(stop),
                    target_price=float(target),
                    equity_rr=float(rr or 0.0),
                    score=float(score or 0.0),
                    options_pcr=self._safe_float(raw.get("options_pcr"), None),
                    options_gamma=raw.get("options_gamma"),
                    run_id=raw.get("run_id"),
                    approved=False,
                )
            )
        return rows

    def _load_recent_db_candidates(self, blocked: set[str]) -> List[OptionUnderlying]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        query = """
            SELECT ticker, strategy, direction, entry_price, stop_loss, target_price, rr,
                   avg_score, options_pcr, options_gamma, position_opened,
                   execution_denied, execution_deny_reason, notes, run_id
            FROM decisions
            WHERE timestamp >= ?
              AND strategy IN ('VOYAGER', 'SNIPER', 'SHORT', 'CONTRARIAN')
              AND (
                    position_opened = 1
                 OR execution_denied = 1
              )
            ORDER BY timestamp DESC, id DESC
        """
        rows = conn.execute(query, (cutoff,)).fetchall()
        conn.close()

        normalized: List[OptionUnderlying] = []
        for row in rows:
            ticker = str(row["ticker"] or "").upper().strip()
            if not ticker or ticker in blocked:
                continue
            strategy = str(row["strategy"] or "").upper().strip()
            if strategy not in _ALLOWED_STRATEGIES:
                continue
            notes = self._parse_notes(row["notes"])
            gates_failed = {str(g).lower().strip() for g in (notes.get("gates_failed") or [])}
            approved = int(row["position_opened"] or 0) == 1
            if not approved:
                reason = str(row["execution_deny_reason"] or "").lower().strip()
                if reason != "risk_reward_too_low":
                    continue
                if {"data", "prefilter", "pathway"} & gates_failed:
                    continue
                live_threshold = _LIVE_SCORE_THRESHOLDS.get(strategy, 60.0)
                score_val = self._safe_float(notes.get("score") or row["avg_score"], 0.0)
                if score_val < (live_threshold - self.salvage_score_buffer):
                    continue
            entry = self._safe_float(row["entry_price"], None)
            stop = self._safe_float(row["stop_loss"], None)
            target = self._safe_float(row["target_price"], None)
            if None in (entry, stop, target):
                continue
            normalized.append(
                OptionUnderlying(
                    ticker=ticker,
                    strategy=strategy,
                    direction=str(row["direction"] or ("SHORT" if strategy == "SHORT" else "LONG")).upper().strip(),
                    source_type="approved" if approved else "rr_reject",
                    entry_price=float(entry),
                    stop_loss=float(stop),
                    target_price=float(target),
                    equity_rr=float(self._safe_float(row["rr"], 0.0) or 0.0),
                    score=float(self._safe_float(notes.get("score") or row["avg_score"], 0.0) or 0.0),
                    options_pcr=self._safe_float(row["options_pcr"], None),
                    options_gamma=row["options_gamma"],
                    run_id=row["run_id"],
                    approved=approved,
                )
            )
        return normalized

    @staticmethod
    def _parse_notes(raw) -> Dict:
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return {}

    @staticmethod
    def _safe_float(value, default: Optional[float]) -> Optional[float]:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except Exception:
            return default
